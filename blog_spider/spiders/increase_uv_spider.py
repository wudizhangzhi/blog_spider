import datetime
import logging
import sys
import traceback
from hashlib import md5
from urllib import parse
from random import randrange, random, choice

import requests
from selenium.common.exceptions import NoSuchElementException, MoveTargetOutOfBoundsException

sys.path.append('../..')
import time
from importlib import import_module

from scrapy.http import HtmlResponse
from scrapy_selenium import SeleniumRequest
from scrapy.utils.request import request_fingerprint

from selenium.webdriver import DesiredCapabilities
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains
from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import Rule
from user_agent import generate_user_agent
from blog_spider import settings

logging.basicConfig(handlers=[logging.FileHandler(filename="debug.log",
                                                  encoding='utf-8', mode='w')],
                    format="%(asctime)s %(name)s:%(levelname)s:%(message)s",
                    datefmt="%F %A %T",
                    level=logging.WARNING)


class CustomSeleniumSpider(object):
    """
    访问百度，然后进入网站模拟人为模式浏览
    """
    allowed_domains = ['blog.yueyawochong.cn', 'www.itdaan.com']
    # start_urls = [
    #     'https://demo.patec.net/p#/workbench/index',
    # ]
    linkextractors = (
        LinkExtractor(
            allow=('.*',),
            deny=(
                'https://demo.patec.net/p#/backendLog/.*',
                'https://demo.patec.net/p#/sysPath/sysStopwatch'
            )
        ),
    )
    WIDTH = 1366
    HEIGHT = 942

    def __init__(self):
        self.search_list = getattr(settings, 'SEARCH_LIST', [])
        driver_name = getattr(settings, 'SELENIUM_DRIVER_NAME', None)
        driver_executable_path = getattr(settings, 'SELENIUM_DRIVER_EXECUTABLE_PATH', None)
        browser_executable_path = getattr(settings, 'SELENIUM_BROWSER_EXECUTABLE_PATH', None)
        driver_arguments = getattr(settings, 'SELENIUM_DRIVER_ARGUMENTS', None)
        self._init_driver(driver_name, driver_executable_path, driver_arguments, browser_executable_path)
        #
        self.queue = list()
        self.fingerprints = set()
        self.search_page_count = 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver:
            self.driver.quit()

    def _init_driver(self, driver_name, driver_executable_path, driver_arguments,
                     browser_executable_path):
        """Initialize the selenium webdriver

        Parameters
        ----------
        driver_name: str
            The selenium ``WebDriver`` to use
        driver_executable_path: str
            The path of the executable binary of the driver
        driver_arguments: list
            A list of arguments to initialize the driver
        browser_executable_path: str
            The path of the executable binary of the browser
        """
        webdriver_base_path = f'selenium.webdriver.{driver_name}'

        driver_klass_module = import_module(f'{webdriver_base_path}.webdriver')
        driver_klass = getattr(driver_klass_module, 'WebDriver')
        if driver_name == 'phantomjs':
            cap = DesiredCapabilities.PHANTOMJS.copy()

            # for key, value in settings.SELENIUM_DRIVER_HEADERS.items():
            #     cap['phantomjs.page.customHeaders.{}'.format(key)] = value
            service_args = ['--web-security=no', '--ssl-protocol=any', '--ignore-ssl-errors=true']
            driver_kwargs = {
                'executable_path': driver_executable_path,
                'service_args': service_args,
                'desired_capabilities': cap
            }
        else:
            driver_options_module = import_module(f'{webdriver_base_path}.options')
            driver_options_klass = getattr(driver_options_module, 'Options')
            driver_options = driver_options_klass()
            if browser_executable_path:
                driver_options.binary_location = browser_executable_path
            for argument in driver_arguments:
                driver_options.add_argument(argument)
            # 随机头
            driver_options.add_argument(
                f"user-agent={generate_user_agent(os=('win',), device_type=('desktop',), navigator=('chrome',))}")
            # 代理
            driver_options.add_argument(
                f"--proxy-server={self.get_proxy()}"
            )
            driver_kwargs = {
                'executable_path': driver_executable_path,
                f'{driver_name}_options': driver_options
            }

        self.driver = driver_klass(**driver_kwargs)
        # 隐式等待5秒，可以自己调节
        self.driver.implicitly_wait(5)
        self.driver.set_page_load_timeout(60)
        # driver.maximize_window()
        self.driver.set_window_size(self.WIDTH, self.HEIGHT)

    def reset(self):
        self.search_page_count = 0

    @staticmethod
    def random_sec(start=None, end=None):
        if not start:
            start = 0
        if not end:
            end = start + 1
        return randrange(start, end) + random()

    @staticmethod
    def random_sleep(start=None, end=None):
        time.sleep(CustomSeleniumSpider.random_sec(start, end))

    @staticmethod
    def get_proxy():
        return requests.get('http://192.168.20.27:5010', timeout=10).text.replace('http://', '')

    def fetch(self, url, meta=None):
        self.driver.get(url)
        self.random_sleep(start=1)
        meta = meta or dict()
        body = str.encode(self.driver.page_source)
        screenshot = self.driver.get_screenshot_as_base64()
        return HtmlResponse(
            url,
            body=body,
            encoding='utf-8',
            request=SeleniumRequest(
                url=url,
                meta=dict(screenshot=screenshot, **meta),
            )
        )

    def get_response(self):
        body = str.encode(self.driver.page_source)
        screenshot = self.driver.get_screenshot_as_base64()
        return HtmlResponse(
            self.driver.current_url,
            body=body,
            encoding='utf-8',
            request=SeleniumRequest(
                url=self.driver.current_url,
                meta=dict(screenshot=screenshot),
            )
        )

    def extract_links(self):
        return [i for i in self.driver.find_elements_by_tag_name('a') if i.is_displayed()]

    def add_request(self, request):
        fp = request_fingerprint(request, keep_fragments=True)
        if request.dont_filter or fp not in self.fingerprints:
            self.fingerprints.add(fp)
            # print(f'加入队列： {request.url} {request.meta.get("page")}')
            self.queue.append(request)
        # else:
        #     if request.meta.get('page'):
        #         print(f'重复: {request.url} {request.meta.get("page")}')

    def handle_request(self, request):
        meta = request.meta
        response = self.fetch(request.url, meta=meta)
        self.extract_links(response)
        return response

    def baidu_search(self, text):
        logging.debug(f'百度搜索: {text}')
        self.fetch('https://baidu.com')
        WebDriverWait(self.driver, 10, ).until(EC.presence_of_element_located((By.ID, 'kw')))
        ele_input = self.driver.find_element_by_id('kw')
        ele_input.send_keys(text)
        self.random_sleep()
        ele_submit = self.driver.find_element_by_id('su')
        self.perform_click(ele_submit)
        self.random_sleep(start=3)

    @staticmethod
    def get_baidu_true_url(link):
        parsed = parse.urlsplit(link)
        query_dict = parse.parse_qs(parsed.query)
        pared_url = f"{parsed.scheme}://{parsed.netloc}/link?url={query_dict['url'][0]}"
        return requests.head(pared_url, allow_redirects=True, timeout=10).url

    @staticmethod
    def get_domain(link):
        return parse.urlsplit(link).netloc

    def baidu_next_page(self):
        print(f'下一页: {self.search_page_count}')
        # 翻页
        ele_next = self.driver.find_element_by_xpath('//*[@id="page"]/div/a[@class="n"]')
        ele_next.click()
        self.search_page_count += 1
        self.random_sleep(start=2)

    def baidu_find_domain_result(self):
        elements = self.driver.find_elements_by_xpath('//*[@id="content_left"]/div')
        for element in elements:
            try:
                a = element.find_element_by_xpath('./h3/a')
                link = a.get_attribute('href')
                title = a.text
                link_true = self.get_baidu_true_url(link)
                print(f"匹配: {title} {link_true}")
                if self.get_domain(link_true) in self.allowed_domains:
                    text = f'| 找到: {title} {link_true} |'
                    print('-' * len(text))
                    print(text)
                    print('-' * len(text))
                    return link
            # except NoSuchElementException:
            except:
                pass
        else:
            if self.search_page_count < 10:  # 最大搜索页数
                self.baidu_next_page()
                return self.baidu_find_domain_result()

    def perform_click(self, ele):
        ActionChains(self.driver).move_to_element(ele).pause(self.random_sec(1)).click(ele).perform()

    def perform_view(self):
        # last_height = self.driver.execute_script("return document.body.scrollHeight")
        # self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        print('模拟浏览的操作')
        chains = ActionChains(self.driver)
        idx = 0
        for i in range(randrange(5, 10)):
            # 随机操作
            if random() > 0.5:
                chains = chains.send_keys(Keys.ARROW_DOWN).pause(self.random_sec(1))
                print(f'{idx} ARROW_DOWN')
                idx += 1
            if random() > 0.5:
                chains = chains.send_keys(Keys.ARROW_UP).pause(self.random_sec(1))
                print(f'{idx} ARROW_UP')
                idx += 1
            # 随机点击拖拽
            if random() > 0.8:
                # xoffset, yoffset = randrange(self.WIDTH // 2), randrange(self.HEIGHT // 2)
                # to_xoffset, to_yoffset = min(xoffset + randrange(5, 30), self.WIDTH), min(yoffset + randrange(5, 30),
                #                                                                           self.HEIGHT)
                to_xoffset, to_yoffset = randrange(5, 30), randrange(5, 30)
                idx += 1
                ele = choice([i for i in self.driver.find_elements_by_tag_name('div') if i.is_displayed()])
                print(f'{idx} drag {ele.location} -> ({to_xoffset} {to_yoffset})')
                # chains = chains.move_by_offset(xoffset, yoffset). \
                #     click_and_hold().pause(self.random_sec()). \
                #     move_by_offset(to_xoffset, to_yoffset). \
                #     pause(self.random_sec()).release()
                chains = chains.move_to_element(ele).pause(self.random_sec()). \
                    click_and_hold().pause(self.random_sec()).move_by_offset(to_xoffset, to_yoffset). \
                    pause(self.random_sec())
        try:
            chains.perform()
        except MoveTargetOutOfBoundsException as e:
            print(e)

    def simulation_human_visit(self):
        # 随便寻找链接点击
        for depth in range(randrange(1, 10)):
            print(f'浏览: {depth}')
            if self.get_domain(self.driver.current_url) not in self.allowed_domains:
                print(f'域名跳出: {self.driver.current_url}')
                break
            self.perform_view()
            ele_link = choice(self.extract_links())
            print(f"选中: {ele_link.get_attribute('href')}")
            self.perform_click(ele_link)

            self.random_sleep(5)

    def start(self):
        """
        从百度搜索进入，
        搜索关键词，
        匹配到网站地址后点击进入
        模拟浏览等操作
        :return:
        """
        for search_text in self.search_list:
            self.baidu_search(search_text)
            # 寻找自己的domain
            target = self.baidu_find_domain_result()
            self.reset()
            if not target:
                raise Exception(f'没有找到目标: {search_text}')
            print(f'点击进入网站: {target}')
            # 找到目标后进入，并模拟访问
            self.fetch(target)
            self.random_sleep(8)
            self.simulation_human_visit()

    def start_one_url(self, target):
        # 找到目标后进入，并模拟访问
        self.fetch(target)
        self.random_sleep(8)
        self.simulation_human_visit()


if __name__ == '__main__':
    for i in range(1000):
        start = datetime.datetime.now()
        try:
            with CustomSeleniumSpider() as spider:
                # spider.start()
                spider.start_one_url('http://blog.yueyawochong.cn')
        except:
            pass
        print(f'用时: {datetime.datetime.now() - start}')

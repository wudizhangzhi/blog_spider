import datetime
import logging
import sys
import traceback
from hashlib import md5
from urllib import parse

import requests
from selenium.common.exceptions import NoSuchElementException

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
    allowed_domains = ['demo.patec.net']
    start_urls = [
        'https://demo.patec.net/p#/workbench/index',
    ]
    linkextractors = (
        LinkExtractor(
            allow=('.*',),
            deny=(
                'https://demo.patec.net/p#/backendLog/.*',
                'https://demo.patec.net/p#/sysPath/sysStopwatch'
            )
        ),
    )

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
            driver_kwargs = {
                'executable_path': driver_executable_path,
                f'{driver_name}_options': driver_options
            }

        self.driver = driver_klass(**driver_kwargs)
        # 隐式等待5秒，可以自己调节
        self.driver.implicitly_wait(5)
        self.driver.set_page_load_timeout(60)
        # driver.maximize_window()
        self.driver.set_window_size(1366, 942)

    @staticmethod
    def get_proxy():
        return requests.get('http://192.168.20.27:5010', timeout=10).text.replace('http://', '')

    def fetch(self, url, meta=None):
        self.driver.get(url)
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

    def extract_links(self, response):
        # 解析url
        for extractor in self.linkextractors:
            links = extractor.extract_links(response)
            for link in links:
                request = SeleniumRequest(
                    url=link.url,
                    meta=dict(),
                )
                self.add_request(request)

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
        time.sleep(0.5)
        ele_submit = self.driver.find_element_by_id('su')
        ele_submit.click()
        time.sleep(3)

    @staticmethod
    def get_baidu_true_url(link):
        parsed = parse.urlsplit(link)
        query_dict = parse.parse_qs(parsed.query)
        pared_url = f"{parsed.scheme}://{parsed.netloc}/?url={query_dict['url'][0]}"
        return requests.head(pared_url, allow_redirects=True, timeout=10).url

    @staticmethod
    def get_domain(link):
        return parse.urlsplit(link).netloc

    def baidu_find_domain_result(self):
        elements = self.driver.find_elements_by_xpath('//*[@id="content_left"]/div')
        for element in elements:
            try:
                a = element.find_element_by_xpath('./h3/a')
                link = a.get_attribute('href')
                title = a.text
                link_true = self.get_baidu_true_url(link)
                if self.get_domain(link_true) in self.allowed_domains:
                    return element
            except NoSuchElementException:
                pass
        else:
            # 翻页
            pass

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
            self.baidu_find_domain_result()

        # for start_url in self.start_urls:
        #     response = self.fetch(start_url)
        #     # 解析url
        #     self.extract_links(response)
        #
        # # 处理任务队列
        # for request in self.queue:
        #     try:
        #         response = self.handle_request(request)
        #         # 是否有分页
        #         meta = response.meta.copy()
        #         if not meta.get('page'):
        #             # 是否含有分页, 有的话加入队列
        #             pagination = response.xpath('//div[@class="pagination-container"]')
        #             if pagination:
        #                 pagination = pagination[0]
        #                 pages = pagination.xpath('./div/ul/li/text()').extract()
        #                 last_page = pages[-1]
        #                 logging.warning(f'{request.url} 是否含有分页last_page: {pages}')
        #                 if last_page and last_page.isdigit() and int(last_page) > 1:
        #                     for page in range(2, int(last_page)):
        #                         _meta = meta.copy()
        #                         _meta.pop('screenshot', None)
        #                         _meta.update({
        #                             'page': page
        #                         })
        #                         request = SeleniumRequest(
        #                             url=response.request.url,
        #                             meta=_meta,
        #                             dont_filter=True,
        #                         )
        #                         self.add_request(request)
        #         # 保存
        #     except KeyboardInterrupt:
        #         print('手动停止')
        #         break
        #     except:
        #         logging.error(traceback.format_exc())


if __name__ == '__main__':
    start = datetime.datetime.now()
    with CustomSeleniumSpider() as spider:
        spider.start()
    print(f'用时: {datetime.datetime.now() - start}')

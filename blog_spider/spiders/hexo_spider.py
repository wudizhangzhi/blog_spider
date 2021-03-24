import scrapy


class HexoSpiderSpider(scrapy.Spider):
    name = 'hexo_spider'
    allowed_domains = ['tding.top']
    start_urls = ['http://tding.top/']



    def parse(self, response):
        pass

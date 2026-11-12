"""美股投研看板 - 配置。

对应 A 股版的 7 个模块：
  平台总览 / 公司研究看板 / 机构持仓(13F) / 行业景气度 / 海外->中文财经信息 / 重大信息跟踪 / 市场内参(盘前盘后)
"""

# ---------------------------------------------------------------------------
# 1. 覆盖公司：参考买点 / 合理价 / 参考卖点 由研究员维护（等价于 A 股版的"估值三值"）
#    buy  : 低于此价 -> 买点区
#    fair : 报告合理价
#    sell : 高于此价 -> 卖点上方
#    risk / growth / intro : 研究员手写要点
# ---------------------------------------------------------------------------
# 观察名单与研究观点属于个人研究内容：放在 watchlist_local.py（不进仓库）；没有时回退到示例。
try:
    from watchlist_local import WATCHLIST
except ImportError:
    from watchlist_example import WATCHLIST

# 行业 -> 代表 ETF（用于行业景气度：动量+相对强弱）
SECTOR_ETFS = {
    "半导体": "SMH", "软件": "IGV", "科技(XLK)": "XLK", "通信/互联网": "XLC",
    "可选消费": "XLY", "必选消费": "XLP", "金融": "XLF", "医疗": "XLV",
    "能源": "XLE", "工业": "XLI", "材料": "XLB", "公用事业": "XLU",
    "房地产": "XLRE", "生物科技": "XBI", "云计算": "SKYY", "网络安全": "CIBR",
    "清洁能源": "ICLN", "银行": "KBE", "航空航天国防": "ITA", "黄金矿业": "GDX",
}
BENCHMARK = "SPY"

# ---------------------------------------------------------------------------
# 2. 新闻源（RSS）。对应 A 股版的财联社/东财/新浪/财新/格隆汇
#    注意：个别站点会拦截脚本，抓取失败会自动跳过，不影响其他源。
# ---------------------------------------------------------------------------
NEWS_SOURCES = [
    {"name": "Yahoo Finance", "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html"},
    {"name": "CNBC Tech", "url": "https://www.cnbc.com/id/19854910/device/rss/rss.html"},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
    {"name": "WSJ Markets", "url": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"},
    {"name": "Nasdaq", "url": "https://www.nasdaq.com/feed/rssoutbound?category=Markets"},
    {"name": "Benzinga", "url": "https://www.benzinga.com/feed"},
    {"name": "PRNewswire 金融", "url": "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss"},
    {"name": "Reuters(经Google)", "url": "https://news.google.com/rss/search?q=site:reuters.com+markets+when:1d&hl=en-US&gl=US&ceid=US:en"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news.rss"},
    {"name": "SEC 8-K", "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom"},
    {"name": "Google News 美股", "url": "https://news.google.com/rss/search?q=US+stocks+when:1d&hl=en-US&gl=US&ceid=US:en"},
]
# 逐公司新闻（Google News 按代码检索，补足覆盖）
PER_TICKER_NEWS = "https://news.google.com/rss/search?q={q}+stock+when:2d&hl=en-US&gl=US&ceid=US:en"

# ---------------------------------------------------------------------------
# 3. 13F 机构（对应"国家队/险资/公募/牛散"）
#    CIK 需为 10 位；建议自行核对后增删
# ---------------------------------------------------------------------------
FUNDS_13F = [
    {"name": "Berkshire Hathaway", "type": "价值/长线", "cik": "0001067983"},
    {"name": "Bridgewater Associates", "type": "宏观对冲", "cik": "0001350694"},
    {"name": "Renaissance Technologies", "type": "量化", "cik": "0001037389"},
    {"name": "Pershing Square", "type": "激进/集中", "cik": "0001336528"},
    {"name": "Tiger Global", "type": "成长对冲", "cik": "0001167483"},
    {"name": "Appaloosa", "type": "事件驱动", "cik": "0001656456"},
    {"name": "Scion Asset Mgmt", "type": "逆向", "cik": "0001649339"},
    {"name": "Citadel Advisors", "type": "多策略", "cik": "0001423053"},
]
SEC_USER_AGENT = "us-stock-board research contact@example.com"  # SEC 要求带联系方式，请改成你自己的

# ---------------------------------------------------------------------------
# 4. 评分/评级标准（沿用 A 股版）
# ---------------------------------------------------------------------------
GRADES = [("S", 80, "特级"), ("A", 68, "重要"), ("B", 55, "关注"), ("C", 42, "一般"), ("D", 0, "次要")]

# 剔除规则：只剔除涉政治/国安敏感内容（与原版一致）
EXCLUDE_KEYWORDS = ["assassination", "coup", "impeach", "regime change", "ethnic cleansing"]

# 自动估值参数（core/valuation.py）。在 WATCHLIST 某只里加 "manual": True 可强制用手工 buy/fair/sell
VALUATION = {
    "discount": 0.09,          # DCF 折现率
    "terminal_g": 0.03,        # 永续增速
    "aaa_yield": 5.0,          # Graham 公式里的 AAA 级债券收益率(%)，请按当前市场更新
    "g_min": 0.02, "g_max": 0.20,   # 历史增速的截断范围，防止被极端值带偏
    "margin_of_safety": 0.25,  # 买点 = 合理价 × (1 - 安全边际)
    "premium": 0.20,           # 卖点 = 合理价 × (1 + 溢价)
}

# 可选：大模型研究推演（需环境变量 ANTHROPIC_API_KEY；未设置则自动跳过）
RESEARCH_LLM = {"enabled": True, "model": "claude-sonnet-5-5", "workers": 4}

# 价格区间判定：距买点/合理价的阈值
ZONE_NEAR_PCT = 0.05  # 价格在 buy 上方 5% 内仍算"买点区"边缘

# 站点密码门（留空则不加密，仅本地预览）。生产环境建议用环境变量 BOARD_PASSPHRASE
PASSPHRASE_ENV = "BOARD_PASSPHRASE"
PBKDF2_ITER = 300000

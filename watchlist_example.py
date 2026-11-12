"""示例观察名单（占位数据，不是研究结论）。
复制本文件为 watchlist_local.py 并填入你自己的标的与观点；watchlist_local.py 已被 .gitignore 忽略。
字段：t 代码 / name / sector / etf 行业代表ETF / buy,fair,sell 手工估值(自动估值失败时回退) / risk,growth 定性观点 / intro 简介。"""
WATCHLIST = [
    {"t": "AAPL", "name": "Apple", "sector": "消费电子", "etf": "XLK", "buy": 170, "fair": 235, "sell": 280,
     "risk": "（示例）填写你的风险判断", "growth": "（示例）填写你的增长逻辑", "intro": "硬件+服务生态"},
    {"t": "MSFT", "name": "Microsoft", "sector": "软件", "etf": "IGV", "buy": 360, "fair": 470, "sell": 560,
     "risk": "（示例）", "growth": "（示例）", "intro": "云、办公软件与 AI 平台"},
    {"t": "JPM", "name": "JPMorgan", "sector": "银行", "etf": "XLF", "buy": 200, "fair": 270, "sell": 320,
     "risk": "（示例）", "growth": "（示例）", "intro": "全球最大商业银行"},
    {"t": "XOM", "name": "ExxonMobil", "sector": "能源", "etf": "XLE", "buy": 95, "fair": 120, "sell": 140,
     "risk": "（示例）", "growth": "（示例）", "intro": "综合能源巨头"},
]

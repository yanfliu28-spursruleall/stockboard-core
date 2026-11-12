"""覆盖名单扩展：在你手工维护的 WATCHLIST（含研究观点/手工估值）之外，自动纳入 EXTRA_TICKERS。

新增标的没有手写的买卖点与观点，所以：
  · 名称 / 行业 / 对应行业 ETF 取自 Yahoo 公司资料；取不到时退化为代码本身与“未分类”，不影响构建；
  · 买点/合理价/卖点留空，由自动估值计算；自动估值也失败且没有分析师目标价时，页面显示“无估值”而不是编一个数；
  · 风险点/增长点完全由数据规则生成（没有“研究员观点”那一条）。"""
from __future__ import annotations

from core.narrative import INDUSTRY_CN, SECTOR_CN
from core.valuation import Consensus

SECTOR_ETF = {"Technology": "XLK", "Communication Services": "XLC", "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
              "Financial Services": "XLF", "Healthcare": "XLV", "Energy": "XLE", "Industrials": "XLI", "Basic Materials": "XLB",
              "Utilities": "XLU", "Real Estate": "XLRE"}


def expand(manual: list[dict], extra: list[str], cons: Consensus | None = None) -> list[dict]:
    have = {c["t"] for c in manual}
    todo = [t for t in dict.fromkeys(extra) if t not in have]
    out = list(manual)
    if not todo:
        return out
    cons = cons or Consensus()
    for t in todo:
        cs = cons.get(t) or {}
        pf = cs.get("profile") or {}
        sec = pf.get("sector")
        out.append({"t": t, "name": cs.get("name") or t, "sector": SECTOR_CN.get(sec, sec or "未分类"), "etf": SECTOR_ETF.get(sec, "SPY"),
                    "buy": None, "fair": None, "sell": None, "risk": "", "growth": "",
                    "intro": INDUSTRY_CN.get(pf.get("industry"), pf.get("industry") or ""), "auto_added": True})
    return out

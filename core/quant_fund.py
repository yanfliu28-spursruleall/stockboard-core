"""基本面因子（点对点 PIT）：只使用“披露日 +1 天”之后才可知的 10-K 财报，避免前视偏差。

局限（页面会写明）：
  · 只有年报(10-K)，没有季报 → 数据更新慢，最长滞后接近一年；
  · 没有分析师盈利预期修正的历史快照（免费数据没有），所以不做“预期修正”类因子；
  · 财报标签口径因公司而异，缺失的格子留空，不做插补。"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import numpy as np
import pandas as pd

from core import valuation


def prefetch(tickers: list[str], workers: int = 4) -> dict[str, dict | None]:
    """并发拉取 SEC 财报（带 7 天缓存）。首次约数分钟，之后秒级。"""
    def one(t):
        try: return t, valuation.extract(t)
        except Exception: return t, None
    with ThreadPoolExecutor(workers) as ex:
        return dict(ex.map(one, tickers))


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s) + pd.Timedelta(days=1)          # 披露日次日起可用


def _step(vals: dict[pd.Timestamp, float], idx: pd.DatetimeIndex) -> pd.Series:
    if not vals: return pd.Series(np.nan, index=idx)
    s = pd.Series(vals).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(idx.union(s.index)).ffill().reindex(idx)


def fund_factors(P: dict, data: dict[str, dict | None]) -> dict[str, pd.DataFrame]:
    idx = P["close"].index
    cols = {k: {} for k in ("roe", "net_margin", "fcf_margin", "low_lev", "rev_g", "ni_g", "ey", "bp", "fcfy")}
    for t, x in data.items():
        if not x or t not in P["close"].columns: continue
        sp = P["_splits"].get(t, [])
        g = lambda k, e: (x[k].get(e) or [None, None])
        ends = sorted(x["ni"]) if x.get("ni") else []
        m = {k: {} for k in cols}
        prev_rev = prev_ni = None
        for e in ends:
            ni, f_ni = g("ni", e); rev, f_rev = g("rev", e); eq, f_eq = g("equity", e)
            ocf, f_ocf = g("ocf", e); cap, f_cap = g("capex", e); debt, f_d = g("debt", e)
            eps, f_eps = g("eps", e); sh, f_sh = g("shares", e)
            fmax = lambda *fs: max(f for f in fs if f)
            if ni is not None and eq and eq > 0:
                m["roe"][_ts(fmax(f_ni, f_eq))] = ni / eq
            if ni is not None and rev:
                m["net_margin"][_ts(fmax(f_ni, f_rev))] = ni / rev
            if ocf is not None and rev:
                m["fcf_margin"][_ts(fmax(f_ocf, f_rev, f_cap or f_ocf))] = (ocf - (cap or 0)) / rev
            if debt is not None and eq and eq > 0:
                m["low_lev"][_ts(fmax(f_d, f_eq))] = -debt / eq
            if rev and prev_rev and prev_rev[0] > 0 and 300 < (date.fromisoformat(e) - date.fromisoformat(prev_rev[1])).days < 430:
                m["rev_g"][_ts(f_rev)] = rev / prev_rev[0] - 1
            if ni is not None and prev_ni and abs(prev_ni[0]) > 0 and 300 < (date.fromisoformat(e) - date.fromisoformat(prev_ni[1])).days < 430:
                m["ni_g"][_ts(f_ni)] = float(np.clip((ni - prev_ni[0]) / abs(prev_ni[0]), -3, 3))
            if rev: prev_rev = (rev, e)
            if ni is not None: prev_ni = (ni, e)
            # 每股数据换算到“今天的股本口径”（与 Yahoo 已按拆股复权的价格一致）
            if eps is not None: m["ey"][_ts(f_eps)] = eps / valuation._split_factor(sp, f_eps)
            if eq and sh: m["bp"][_ts(fmax(f_eq, f_sh))] = eq / (sh * valuation._split_factor(sp, f_sh))
            if ocf is not None and sh: m["fcfy"][_ts(fmax(f_ocf, f_sh))] = (ocf - (cap or 0)) / (sh * valuation._split_factor(sp, f_sh))
        for k in cols:
            cols[k][t] = _step(m[k], idx)
    out = {}
    price = P["close"]
    for k, d in cols.items():
        if not d: continue
        F = pd.DataFrame(d).reindex(columns=price.columns)
        if k in ("ey", "bp", "fcfy"):
            F = F / price                                   # 每股指标 / 价格 = 收益率型估值因子（越高越便宜）
        out[k] = F.replace([np.inf, -np.inf], np.nan)
    return out

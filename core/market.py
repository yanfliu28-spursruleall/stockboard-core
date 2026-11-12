"""行情抓取（Yahoo chart 公共接口，仅用 requests）+ 估值区间 + 行业景气度。"""
from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor
import requests

import config

UA = {"User-Agent": "Mozilla/5.0 (us-stock-board)"}
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=1y&interval=1d"


def fetch_history(ticker: str, retries: int = 2) -> dict | None:
    """返回 {'close':[...], 'ts':[...], 'last':float, 'prev':float}；失败返回 None。"""
    for i in range(retries + 1):
        try:
            r = requests.get(CHART.format(t=ticker), headers=UA, timeout=15)
            if r.status_code != 200:
                time.sleep(1 + i)
                continue
            res = r.json()["chart"]["result"][0]
            ts = res["timestamp"]
            close = res["indicators"]["quote"][0]["close"]
            pairs = [(t, c) for t, c in zip(ts, close) if c is not None]
            if len(pairs) < 2:
                return None
            ts, close = zip(*pairs)
            return {"ts": list(ts), "close": list(close), "last": close[-1], "prev": close[-2],
                    "currency": res["meta"].get("currency", "USD")}
        except Exception:
            time.sleep(1 + i)
    return None


def fetch_many(tickers: list[str], workers: int = 8) -> dict[str, dict | None]:
    with ThreadPoolExecutor(workers) as ex:
        return dict(zip(tickers, ex.map(fetch_history, tickers)))


def _ret(close: list[float], n: int) -> float | None:
    if len(close) <= n:
        return None
    return close[-1] / close[-1 - n] - 1


def zone_of(price: float, buy: float, fair: float, sell: float) -> str:
    """估值状态：买点区 / 沉默区 / 高估区 / 卖点上方（与 A 股版 4 档一致）"""
    if price <= buy * (1 + config.ZONE_NEAR_PCT):
        return "买点区"
    if price >= sell:
        return "卖点上方"
    if price >= fair:
        return "高估区"
    return "沉默区"


def _risk(h: dict, spy: dict | None) -> dict:
    """近 1 年年化波动率 / 最大回撤 / 对标普500的 Beta（按日期对齐）。"""
    c, ts = h["close"], h["ts"]
    rets = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
    mu = sum(rets) / len(rets)
    vol = (sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5 * 252 ** 0.5 if len(rets) > 2 else None
    peak, mdd = c[0], 0.0
    for x in c:
        peak = max(peak, x); mdd = min(mdd, x / peak - 1)
    beta = None
    if spy:
        sm = dict(zip(spy["ts"], spy["close"]))
        pairs = [(sm[ts[i]] / sm[ts[i - 1]] - 1, c[i] / c[i - 1] - 1) for i in range(1, len(c)) if ts[i] in sm and ts[i - 1] in sm]
        if len(pairs) > 60:
            ms, mt = sum(p[0] for p in pairs) / len(pairs), sum(p[1] for p in pairs) / len(pairs)
            var = sum((p[0] - ms) ** 2 for p in pairs)
            beta = round(sum((p[0] - ms) * (p[1] - mt) for p in pairs) / var, 2) if var else None
    return {"vol": round(vol, 3) if vol else None, "mdd": round(mdd, 3), "beta1y": beta}


def _rel(px: float, ref) -> float | None:
    return round(px / ref - 1, 4) if ref else None


def build_board(hist: dict[str, dict | None], watchlist: list[dict] | None = None) -> list[dict]:
    rows = []
    for c in (watchlist or config.WATCHLIST):
        h = hist.get(c["t"])
        row = dict(c)
        if not h:
            row.update(price=None, zone="无数据")
            rows.append(row)
            continue
        px, close = h["last"], h["close"]
        hi, lo = max(close), min(close)
        row.update(
            price=round(px, 2),
            chg=round(px / h["prev"] - 1, 4),
            r1m=_ret(close, 21), r3m=_ret(close, 63), r6m=_ret(close, 126),
            hi52=round(hi, 2), lo52=round(lo, 2),
            from_hi=round(px / hi - 1, 4),
            to_buy=_rel(px, c.get("buy")), to_fair=_rel(px, c.get("fair")), to_sell=_rel(px, c.get("sell")),
            zone=zone_of(px, c["buy"], c["fair"], c["sell"]) if all(c.get(k) for k in ("buy", "fair", "sell")) else "无估值",
            spark=[round(x, 2) for x in close[-60:]],
            **_risk(h, hist.get(config.BENCHMARK)),
        )
        rows.append(row)
    return rows


def sector_prosperity(hist: dict[str, dict | None]) -> list[dict]:
    """行业景气分(0-100)：动量 + 相对强弱 + 趋势位置 + 广度。
    对应 A 股版的景气度排行（那边用财报同比，这里用价格/资金面代理，口径写在页面上）。"""
    spy = hist.get(config.BENCHMARK)
    spy_close = spy["close"] if spy else None
    out = []
    for name, etf in config.SECTOR_ETFS.items():
        h = hist.get(etf)
        if not h:
            continue
        c = h["close"]
        r1, r3, r6 = _ret(c, 21), _ret(c, 63), _ret(c, 126)
        if r1 is None or r3 is None:
            continue
        rs3 = r3 - (_ret(spy_close, 63) or 0) if spy_close else 0
        ma50 = sum(c[-50:]) / min(50, len(c))
        ma200 = sum(c[-200:]) / min(200, len(c))
        trend = (1 if c[-1] > ma50 else 0) + (1 if c[-1] > ma200 else 0) + (1 if ma50 > ma200 else 0)
        pos = (c[-1] - min(c)) / (max(c) - min(c) + 1e-9)

        def sc(x, lo, hi):  # 线性映射到 0-100
            return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))

        score = (0.25 * sc(r1, -0.08, 0.10) + 0.25 * sc(r3, -0.15, 0.25) +
                 0.20 * sc(rs3, -0.10, 0.12) + 0.15 * (trend / 3 * 100) + 0.15 * pos * 100)
        members = [w["t"] for w in config.WATCHLIST if w["etf"] == etf]
        out.append(dict(sector=name, etf=etf, score=round(score, 1), r1m=r1, r3m=r3, r6m=r6,
                        rs3m=rs3, trend=trend, pos52=round(pos, 3), members=members))
    out.sort(key=lambda x: -x["score"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out

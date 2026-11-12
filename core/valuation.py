"""估值 v2：相对估值(自身历史前瞻PE分位) × 分析师一致预期EPS，再与 DCF(一致预期增速) 交叉。

口径（关键，必须一致）：
  历史前瞻PE_t = 当时股价 / 其后“当前财年”实际EPS      (用 SEC 10-K 的已实现 EPS，按拆股复权)
  当前前瞻PE   = 现价 / 一致预期“当前财年”EPS (Yahoo earningsTrend 0y)
  两者都是“价格 ÷ 当前财年EPS”，因此可以直接比较，也可以把历史分位倍数乘到一致预期EPS 上。

  相对估值合理价 = 历史前瞻PE 中位数 × 一致预期EPS
  DCF 合理价     = 每股自由现金流，前5年按一致预期增速、后5年线性衰减到永续增速，再折现
  最终合理价     = 两者均值（只有一个可用时取该个）
  买点/卖点      = 合理价 × (历史PE P25/P50) 与 × (历史PE P75/P50)，比值限制在 [0.6,0.95] / [1.05,1.6]
                   —— 带宽由该股自己的估值波动决定，而不是拍一个固定安全边际。

局限：一致预期来自 Yahoo 非官方接口(可能失效，失效时自动降级)；历史 PE 区间只覆盖“有已实现EPS”的时段；
增长股的历史 PE 区间本身很宽，带宽也会宽；金融/周期股 EPS 波动大，置信度会降低。
"""
from __future__ import annotations
import json, statistics, time
from bisect import bisect_right
from datetime import date, datetime, timezone
from pathlib import Path
import requests

import config

CACHE_ROOT = Path(__file__).resolve().parent.parent / "cache"
FACTS = CACHE_ROOT / "facts_v5"
H = {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
FACT_TTL, CONS_TTL = 7 * 86400, 12 * 3600

TAGS = {
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
    "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "ni": ["NetIncomeLoss", "ProfitLoss"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "debt": ["LongTermDebt", "LongTermDebtNoncurrent", "DebtLongtermAndShorttermCombinedAmount"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
}
INSTANT = {"equity", "debt", "cash"}  # 资产负债表项目：没有 start 字段


# ------------------------------------------------------------------ SEC 财报
def _sec_get(url: str, tries: int = 4):
    for i in range(tries):
        time.sleep(0.2)
        r = requests.get(url, headers=H, timeout=40)
        if r.status_code in (403, 429, 503):
            time.sleep(2 * (i + 1)); continue
        r.raise_for_status(); return r
    r.raise_for_status()


CIK_OVERRIDE = {"XOM": "0000034088"}  # Exxon 重组为控股公司后 ticker 映射到新 CIK，历史财报仍在旧 CIK 下


def _cik_map() -> dict[str, str]:
    p = CACHE_ROOT / "cik_map.json"
    if p.exists() and time.time() - p.stat().st_mtime < 30 * 86400:
        return json.loads(p.read_text("utf-8"))
    j = _sec_get("https://www.sec.gov/files/company_tickers.json").json()
    m = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in j.values()}
    p.write_text(json.dumps(m), "utf-8")
    return m


def _annual(facts: dict, tags: list[str], unit: str, instant: bool = False) -> dict[str, list]:
    """{财年结束日: [值, 申报日]}，同一结束日取最新申报；多个标签时取最新财年最近的那个。"""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    cands = []
    for tag in tags:
        units = gaap.get(tag, {}).get("units", {})
        rows = units.get(unit) or next((v for k, v in units.items() if k.replace(" ", "") == unit), None)
        if not rows:
            continue
        best: dict[str, list] = {}
        for r in rows:
            if r.get("form") not in ("10-K", "10-K/A") or r.get("fp") != "FY":
                continue
            if "start" in r:
                d = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
                if not 340 <= d <= 380:
                    continue
            elif not instant:
                continue
            cur = best.get(r["end"])
            if cur is None or r["filed"] > cur[1]:
                best[r["end"]] = [float(r["val"]), r["filed"]]
        if best:
            cands.append(dict(sorted(best.items())))
    return max(cands, key=lambda c: (list(c)[-1], len(c))) if cands else {}


def _fix_share_units(x: dict | None) -> dict | None:
    """个别公司（如 MCD）在 XBRL 里把股数直接以“百万股/千股”写出，不带换算 —— 按“净利润 ÷ EPS ≈ 股数”校验并换算。
    只在数量级明显对不上时才改（相差约 1e3 或 1e6 倍），其余数据原样保留。读缓存时执行，所以不需要重新下载财报。"""
    if not x or not x.get("shares") or not x.get("ni") or not x.get("eps"):
        return x
    for e in reversed(list(x["shares"])):
        ni, eps, sh = (x["ni"].get(e) or [None])[0], (x["eps"].get(e) or [None])[0], x["shares"][e][0]
        if ni and eps and eps > 0 and ni > 0 and sh > 0:
            r = (ni / eps) / sh
            scale = 1e6 if 0.5e6 < r < 2e6 else 1e3 if 0.5e3 < r < 2e3 else 1
            if scale != 1:
                x["shares"] = {k: [v[0] * scale, v[1]] for k, v in x["shares"].items()}
            break
    return x


def extract(ticker: str) -> dict | None:
    FACTS.mkdir(parents=True, exist_ok=True)
    p = FACTS / f"{ticker}.json"
    if p.exists() and time.time() - p.stat().st_mtime < FACT_TTL:
        return _fix_share_units(json.loads(p.read_text("utf-8")) or None)
    cik = CIK_OVERRIDE.get(ticker) or _cik_map().get(ticker)
    if not cik:
        return None
    try:
        facts = _sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json").json()
    except Exception:
        return None
    if "us-gaap" not in facts.get("facts", {}):
        p.write_text("{}", "utf-8"); return None
    out = {k: _annual(facts, tags, "USD/shares" if k == "eps" else "shares" if k == "shares" else "USD", k in INSTANT) for k, tags in TAGS.items()}
    p.write_text(json.dumps(out), "utf-8")
    return _fix_share_units(out)


def fundamentals(x: dict) -> dict:
    """上一完整财年的财务摘要（来自 10-K）。"""
    last = lambda d: (list(d.items())[-1] if d else (None, None))
    prev = lambda d: (list(d.items())[-2] if d and len(d) > 1 else (None, None))
    out = {}
    try:
        e, (rv, _) = last(x["rev"]); out["fy_end"] = e; out["rev"] = rv
        pe_, (pr, _) = prev(x["rev"])
        if pr: out["rev_g"] = rv / pr - 1
        ni = x["ni"].get(e, [None])[0]
        if ni is not None and rv: out["net_margin"] = ni / rv
        ocf, cap = x["ocf"].get(e, [None])[0], x["capex"].get(e, [0])[0]
        if ocf is not None and rv: out["fcf_margin"] = (ocf - cap) / rv
        eq = x["equity"].get(e, [None])[0]
        if ni is not None and eq and eq > 0: out["roe"] = ni / eq
        debt, cash = x["debt"].get(e, [None])[0], x["cash"].get(e, [None])[0]
        if debt is not None and eq and eq > 0: out["debt_equity"] = debt / eq
        if debt is not None and cash is not None: out["net_debt"] = debt - cash
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ 价格历史 + 拆股
def history_5y(ticker: str) -> tuple[list[int], list[float], list[tuple[int, float]]] | None:
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                         params={"range": "10y", "interval": "1wk", "events": "split"}, headers=BROWSER, timeout=20)
        if r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        pairs = [(t, c) for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c]
        splits = [(int(v["date"]), v["numerator"] / v["denominator"]) for v in (res.get("events", {}).get("splits", {}) or {}).values()]
        return [p[0] for p in pairs], [p[1] for p in pairs], sorted(splits)
    except Exception:
        return None


def _split_factor(splits, filed: str) -> float:
    ft = datetime.fromisoformat(filed).replace(tzinfo=timezone.utc).timestamp()
    f = 1.0
    for ts, ratio in splits:
        if ts > ft:
            f *= ratio
    return f


# ------------------------------------------------------------------ 分析师一致预期
class Consensus:
    def __init__(self):
        self.path = CACHE_ROOT / "consensus.json"
        self.store = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
        self.s = None

    def _session(self):
        if self.s:
            return self.s
        s = requests.Session(); s.headers.update(BROWSER)
        try:
            s.get("https://fc.yahoo.com", timeout=15)
            c = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15)
            if c.status_code != 200 or "<" in c.text:
                return None
            s.crumb = c.text
        except Exception:
            return None
        self.s = s
        return s

    @staticmethod
    def _extra(j: dict, tr: dict, fd: dict, pr: dict) -> dict:
        """关键指标 / 预期修正 / 卖方评级 / 财报日 / 超预期记录 / 内部人交易。全部来自 Yahoo quoteSummary，缺失即 None。"""
        R = lambda d, k: ((d or {}).get(k) or {}).get("raw") if isinstance((d or {}).get(k), dict) else None
        dk, sd, mh = j.get("defaultKeyStatistics") or {}, j.get("summaryDetail") or {}, j.get("majorHoldersBreakdown") or {}
        ce, ns = j.get("calendarEvents") or {}, j.get("netSharePurchaseActivity") or {}
        rt = ((j.get("recommendationTrend") or {}).get("trend") or [{}])[0]
        eh = (j.get("earningsHistory") or {}).get("history") or []
        e0, q0 = tr.get("0y", {}) or {}, tr.get("0q", {}) or {}
        et, er = e0.get("epsTrend") or {}, e0.get("epsRevisions") or {}
        return {
            "k": {"mcap": R(sd, "marketCap") or R(pr, "marketCap"), "fpe": R(dk, "forwardPE"), "peg": R(dk, "pegRatio"), "pb": R(dk, "priceToBook"),
                  "ev_ebitda": R(dk, "enterpriseToEbitda"), "beta": R(dk, "beta"), "div": R(sd, "dividendYield"), "payout": R(sd, "payoutRatio"),
                  "short": R(dk, "shortPercentOfFloat"), "inst": R(mh, "institutionsPercentHeld"), "insider": R(mh, "insidersPercentHeld"),
                  "roe": R(fd, "returnOnEquity"), "pm": R(fd, "profitMargins"), "om": R(fd, "operatingMargins"), "gm": R(fd, "grossMargins"),
                  "fcf": R(fd, "freeCashflow"), "ebitda": R(fd, "ebitda"), "debt": R(fd, "totalDebt"), "cash": R(fd, "totalCash"),
                  "rev_g": R(fd, "revenueGrowth")},
            "rv": {"cur": R(et, "current"), "d7": R(et, "7daysAgo"), "d30": R(et, "30daysAgo"), "d90": R(et, "90daysAgo"),
                   "up7": R(er, "upLast7days"), "dn7": R(er, "downLast7Days"), "up30": R(er, "upLast30days"), "dn30": R(er, "downLast30days")},
            "rt": {"sb": rt.get("strongBuy"), "b": rt.get("buy"), "h": rt.get("hold"), "s": rt.get("sell"), "ss": rt.get("strongSell"),
                   "mean": R(fd, "recommendationMean"), "n": R(fd, "numberOfAnalystOpinions"), "tlo": R(fd, "targetLowPrice"),
                   "thi": R(fd, "targetHighPrice"), "tmed": R(fd, "targetMedianPrice"), "tmean": R(fd, "targetMeanPrice")},
            "ev": {"earn": [x.get("raw") for x in ((ce.get("earnings") or {}).get("earningsDate") or []) if isinstance(x, dict)],
                   "exdiv": R(ce, "exDividendDate"), "q_eps": ((q0.get("earningsEstimate") or {}).get("avg") or {}).get("raw"),
                   "q_rev": ((q0.get("revenueEstimate") or {}).get("avg") or {}).get("raw"), "q_end": q0.get("endDate")},
            "bt": [{"q": (h.get("quarter") or {}).get("fmt"), "act": R(h, "epsActual"), "est": R(h, "epsEstimate"), "sp": R(h, "surprisePercent")} for h in eh[-4:]],
            "ins": {"buy": R(ns, "buyInfoCount"), "sell": R(ns, "sellInfoCount"), "net": R(ns, "netPercentInsiderShares")},
        }

    def get(self, t: str) -> dict | None:
        c = self.store.get(t)
        if c and c.get("_v") == 5 and time.time() - c["_ts"] < CONS_TTL:
            return c
        s = self._session()
        if not s:
            return c  # 失效时用旧缓存，没有则 None
        try:
            r = s.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{t}",
                      params={"modules": "earningsTrend,financialData,assetProfile,price,defaultKeyStatistics,summaryDetail,recommendationTrend,calendarEvents,earningsHistory,netSharePurchaseActivity,majorHoldersBreakdown", "crumb": s.crumb}, timeout=20)
            if r.status_code != 200:
                return c
            j = r.json()["quoteSummary"]["result"][0]
            tr = {e["period"]: e for e in j["earningsTrend"]["trend"]}
            g = lambda p, k: ((tr.get(p, {}).get("earningsEstimate", {}) or {}).get(k, {}) or {}).get("raw")
            gr = lambda p: (tr.get(p, {}).get("growth", {}) or {}).get("raw")
            fd = j.get("financialData", {})
            rv = lambda p, k="avg": ((tr.get(p, {}).get("revenueEstimate", {}) or {}).get(k, {}) or {}).get("raw")
            fy = lambda p: (tr.get(p, {}).get("endDate") or "")[:4]
            ap, pr = j.get("assetProfile", {}) or {}, j.get("price", {}) or {}
            out = {"_ts": time.time(), "_v": 5, "name": pr.get("shortName") or pr.get("longName"), "ccy": fd.get("financialCurrency"), "eps0": g("0y", "avg"), "eps1": g("+1y", "avg"), "n0": g("0y", "numberOfAnalysts"),
                   "eps0_lo": g("0y", "low"), "eps0_hi": g("0y", "high"),
                   "rev0": rv("0y"), "rev1": rv("+1y"), "revg0": rv("0y", "growth"), "revg1": rv("+1y", "growth"), "fy0": fy("0y"), "fy1": fy("+1y"),
                   "profile": {"sector": ap.get("sector"), "industry": ap.get("industry"), "city": ap.get("city"), "state": ap.get("state"),
                               "country": ap.get("country"), "employees": ap.get("fullTimeEmployees"), "website": ap.get("website"),
                               "summary": (ap.get("longBusinessSummary") or "")[:900], "mcap": (pr.get("marketCap") or {}).get("raw")},
                   "g0": gr("0y"), "g1": gr("+1y"),
                   "target": (fd.get("targetMeanPrice") or {}).get("raw"), "n_target": (fd.get("numberOfAnalystOpinions") or {}).get("raw"),
                   **self._extra(j, tr, fd, pr)}
            self.store[t] = out
            self.path.write_text(json.dumps(self.store), "utf-8")
            return out
        except Exception:
            return c


# ------------------------------------------------------------------ 计算
def _pct(vals: list[float], q: float) -> float:
    v = sorted(vals); k = (len(v) - 1) * q; lo = int(k)
    return v[lo] + (v[min(lo + 1, len(v) - 1)] - v[lo]) * (k - lo)


def fwd_pe_history(x: dict, hist) -> list[float]:
    """历史前瞻PE：当时价格 / 其后“当前财年”(= 首个结束日晚于当时的财年)的已实现EPS，按拆股复权。"""
    ts, close, splits = hist
    fy = []
    for end, (eps, filed) in x["eps"].items():
        fy.append((datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp(), eps / _split_factor(splits, filed)))
    fy.sort()
    ends = [e for e, _ in fy]
    out = []
    for t, px in zip(ts, close):
        i = bisect_right(ends, t + 30 * 86400)  # 首个结束日 > 当时+30天
        if i >= len(fy):
            break  # 之后的财年 EPS 还没发生
        eps = fy[i][1]
        if eps > 0 and 0 < px / eps < 150:
            out.append(px / eps)
    return out


def dcf(fcfps: float, g: float, r: float, tg: float) -> float:
    cf, pv = fcfps, 0.0
    for yr in range(1, 11):
        gy = g if yr <= 5 else g + (tg - g) * (yr - 5) / 5
        cf *= 1 + gy
        pv += cf / (1 + r) ** yr
    return pv + cf * (1 + tg) / (r - tg) / (1 + r) ** 10


def _target_only(base: dict, why: str) -> dict:
    """无 SEC 美国会计准则财报时(外国发行人/按股份类别披露等)：以分析师一致目标价为中枢，置信恒为低。"""
    P = config.VALUATION
    tgt = base["methods"].get("分析师目标价(参考)")
    if not tgt:
        base["src"] = f"手工({why}，且无分析师目标价)"
        return base
    base.update(fair=round(tgt, 2), buy=round(tgt * (1 - P["margin_of_safety"]), 2), sell=round(tgt * (1 + P["premium"]), 2),
                src=f"仅分析师目标价({why})", conf="低")
    return base


def implied_growth(fcfps: float, px: float, P: dict) -> float | None:
    """反向 DCF：当前价格隐含的未来 5 年每股自由现金流年增速（后 5 年同样线性衰减到永续增速）。二分法求解。"""
    f = lambda g: dcf(fcfps, g, P["discount"], P["terminal_g"]) - px
    lo, hi = -0.30, 0.80
    if f(lo) > 0: return lo
    if f(hi) < 0: return None
    for _ in range(40):
        mid = (lo + hi) / 2
        if f(mid) > 0: hi = mid
        else: lo = mid
    return round((lo + hi) / 2, 4)


def sensitivity(fcfps: float, g: float, P: dict) -> dict:
    """DCF 敏感性：增速 ±5pp × 折现率 ±1pp。单点估值没有说服力，专业人士看的是区间。"""
    gs = [max(-0.2, g - 0.05), g, min(0.4, g + 0.05)]
    rs = [P["discount"] - 0.01, P["discount"], P["discount"] + 0.01]
    return {"g": [round(x, 3) for x in gs], "r": [round(x, 3) for x in rs],
            "v": [[round(dcf(fcfps, gg, rr, P["terminal_g"]), 2) for gg in gs] for rr in rs]}


def value_one(c: dict, px: float | None, cons: Consensus) -> dict:
    P = config.VALUATION
    base = {"buy": c["buy"], "fair": c["fair"], "sell": c["sell"], "src": "手工(示例)", "conf": "—", "methods": {}, "g": None,
            "pe_now": None, "pe_pct": None, "pe_band": None, "n_analysts": None, "cs": None, "fund": {},
            "g_implied": None, "sens": None, "policy": None, "sector_en": None}
    if c.get("manual") or px is None:
        return base
    cs = cons.get(c["t"])
    base["cs"] = cs
    if cs and cs.get("target"):
        base["methods"]["分析师目标价(参考)"] = round(cs["target"], 2)
        base["n_analysts"] = cs.get("n_target")
    x = extract(c["t"])
    if x:
        base["fund"] = fundamentals(x)
    if not x or not x["eps"] or not x["shares"]:
        return _target_only(base, "无SEC美国会计准则财报")

    parts, notes = {}, []
    # —— 相对估值：历史前瞻PE分位 × 当年EPS
    #    当年EPS 不直接用 Yahoo 的绝对值(多为 non-GAAP，与 10-K 的 GAAP 口径不一致)，
    #    而是 GAAP 基期EPS × (1+一致预期增速)；增速是相对去年同口径算的，与口径无关。
    hist = history_5y(c["t"])
    rel = ratio_lo = ratio_hi = None
    eps0 = None
    try:
        last_end = list(x["eps"])[-1]
        eps_base = x["eps"][last_end][0] / _split_factor(hist[2] if hist else [], x["eps"][last_end][1])
        g0 = (cs or {}).get("g0")
        if eps_base > 0 and g0 is not None and -0.5 < g0 < 3:
            eps0 = eps_base * (1 + g0)
    except Exception:
        eps0 = None
    if hist and eps0 and eps0 > 0:
        pes = fwd_pe_history(x, hist)
        if len(pes) >= 52:  # 至少约1年周线
            p25, p50, p75 = _pct(pes, .25), _pct(pes, .5), _pct(pes, .75)
            rel = p50 * eps0
            ratio_lo, ratio_hi = max(0.6, min(0.95, p25 / p50)), max(1.05, min(1.6, p75 / p50))
            pe_now = px / eps0
            base["pe_now"], base["pe_pct"] = round(pe_now, 1), round(sum(p <= pe_now for p in pes) / len(pes), 2)
            base["pe_band"] = [round(p25, 1), round(p50, 1), round(p75, 1)]
            parts["相对估值(历史前瞻PE中位×GAAP预期EPS)"] = rel
    # —— DCF：用一致预期增速，现金流用上一财年每股FCF(按拆股复权)
    try:
        ocf, capex, sh = x["ocf"], x["capex"], x["shares"]
        last_end = list(ocf)[-1]
        fcf = ocf[last_end][0] - (capex.get(last_end, [0])[0])
        shares = sh[last_end][0] * _split_factor(hist[2] if hist else [], sh[last_end][1]) if last_end in sh else None
        fcfps = fcf / shares if shares else None
    except Exception:
        fcfps = None
    gs = [g for g in ((cs or {}).get("g0"), (cs or {}).get("g1")) if g is not None]
    g = max(P["g_min"], min(0.25, statistics.median(gs))) if gs else None
    sec = ((cs or {}).get("profile") or {}).get("sector")
    base["sector_en"] = sec
    skip_dcf = sec == "Financial Services"        # 银行/保险：存款与保费现金流不是自由现金流，DCF 没有意义
    if fcfps and fcfps > 0 and g is not None and not skip_dcf:
        parts["DCF(一致预期增速)"] = dcf(fcfps, g, P["discount"], P["terminal_g"])
        base["g"] = g
        base["g_implied"] = implied_growth(fcfps, px, P)
        base["sens"] = sensitivity(fcfps, g, P)

    if not parts:
        return _target_only(base, "缺一致预期增速或现金流为负，模型不适用")
    fair = statistics.mean(parts.values())
    if px and not 0.1 <= fair / px <= 10:        # 结果保护：偏离现价超过 10 倍几乎一定是数据口径问题，不展示荒谬的数字
        base["pe_now"] = base["pe_pct"] = base["pe_band"] = base["g"] = None
        return _target_only(base, "模型结果与现价偏离>10倍，疑似财报口径问题")
    if ratio_lo:
        buy, sell = fair * ratio_lo, fair * ratio_hi
    else:
        buy, sell = fair * (1 - P["margin_of_safety"]), fair * (1 + P["premium"])
    # 置信度
    agree = max(parts.values()) / min(parts.values()) if len(parts) == 2 else None
    n = (cs or {}).get("n0") or 0
    tgt = (cs or {}).get("target")
    off_target = bool(tgt and not 0.6 <= fair / tgt <= 1.5)
    conf = "高" if agree and agree < 1.6 and n >= 15 else "中" if (agree and agree < 2.5) or (len(parts) == 1 and n >= 15) else "低"
    policy = None
    if sec == "Financial Services":
        policy = "金融股：自由现金流不适用，只用相对估值；应结合 P/TBV、ROTCE、净息差、信贷成本与资本充足率判断"
    elif sec in ("Energy", "Basic Materials"):
        policy = "周期行业：盈利处于景气高点时低 PE 往往是陷阱，宜用周期正常化盈利与 EV/EBITDA 交叉验证，不宜只看 PE 分位"
    elif sec in ("Real Estate", "Utilities"):
        policy = "利率敏感的资产型行业：EPS 不能反映真实现金回报（地产看 FFO/AFFO），估值应结合股息率与利率走势"
    if policy and conf == "高":
        conf = "中"                                 # 方法不完全适配的行业，置信度封顶“中”
    base["policy"] = policy
    if off_target:
        conf = "低"
        base["methods"]["⚠与分析师目标价偏离过大(合理价/目标价)"] = round(fair / tgt, 2)
    base.update(buy=round(buy, 2), fair=round(fair, 2), sell=round(sell, 2), src="自动(相对估值+一致预期)" if rel else "自动(DCF·一致预期)",
                conf=conf, n_analysts=n or base["n_analysts"])
    base["methods"].update({k: round(v, 2) for k, v in parts.items()})
    return base


def apply(watchlist: list[dict], hist: dict) -> list[dict]:
    cons = Consensus()
    out = []
    for c in watchlist:
        h = hist.get(c["t"])
        v = value_one(c, h["last"] if h else None, cons)
        out.append({**c, "buy": v["buy"], "fair": v["fair"], "sell": v["sell"], "val_src": v["src"], "val_conf": v["conf"],
                    "val_methods": v["methods"], "val_g": v["g"], "pe_now": v["pe_now"], "pe_pct": v["pe_pct"],
                    "pe_band": v["pe_band"], "n_analysts": v["n_analysts"], "cs": v["cs"], "fund": v["fund"],
                    "g_implied": v["g_implied"], "sens": v["sens"], "policy": v["policy"], "sector_en": v["sector_en"]})
    return out

"""公司研究看板的“核心内容”：把估值 / 一致预期 / 财务 / 情报 / 机构持仓 组装成结构化中文要点。
全部是规则生成（数据驱动），研究员手写的定性观点(config 里的 risk/growth)作为“研究员观点”追加在最后。"""
from __future__ import annotations
import json
from datetime import date, datetime
from pathlib import Path

import config
from core import research_llm

CACHE = Path(__file__).resolve().parent.parent / "cache"

SECTOR_CN = {"Technology": "信息技术", "Communication Services": "通信服务", "Consumer Cyclical": "可选消费", "Consumer Defensive": "必选消费",
             "Financial Services": "金融", "Healthcare": "医疗保健", "Energy": "能源", "Industrials": "工业", "Basic Materials": "材料",
             "Utilities": "公用事业", "Real Estate": "房地产"}
INDUSTRY_CN = {"Semiconductors": "半导体", "Semiconductor Equipment & Materials": "半导体设备", "Software - Infrastructure": "基础设施软件",
               "Software - Application": "应用软件", "Internet Content & Information": "互联网内容", "Internet Retail": "互联网零售",
               "Consumer Electronics": "消费电子", "Auto Manufacturers": "整车", "Entertainment": "娱乐/流媒体", "Banks - Diversified": "综合银行",
               "Credit Services": "信用/支付服务", "Drug Manufacturers - General": "大型制药", "Healthcare Plans": "医疗保险",
               "Oil & Gas Integrated": "综合油气", "Farm & Heavy Construction Machinery": "工程机械", "Specialty Industrial Machinery": "专用工业机械",
               "Discount Stores": "折扣零售", "Capital Markets": "资本市场", "Information Technology Services": "IT 服务",
               "Electronic Components": "电子元件", "Communication Equipment": "通信设备", "Diagnostics & Research": "诊断与研究"}
HOLD_KEY = {"NVDA": "NVIDIA", "AMD": "ADVANCED MICRO", "AVGO": "BROADCOM", "TSM": "TAIWAN SEMI", "ASML": "ASML", "MSFT": "MICROSOFT",
            "GOOGL": "ALPHABET", "META": "META PLATFORMS", "AMZN": "AMAZON", "AAPL": "APPLE", "TSLA": "TESLA", "NFLX": "NETFLIX",
            "CRM": "SALESFORCE", "PLTR": "PALANTIR", "ORCL": "ORACLE", "JPM": "JPMORGAN", "V": "VISA", "LLY": "ELI LILLY", "UNH": "UNITEDHEALTH",
            "XOM": "EXXON", "CAT": "CATERPILLAR", "GEV": "GE VERNOVA", "COST": "COSTCO", "COIN": "COINBASE"}


CCY_SYM = {"USD": "$", "TWD": "NT$", "EUR": "€", "JPY": "¥", "CNY": "¥", "GBP": "£"}


def _b(v, ccy="USD"):  # 金额 -> 带币种与单位（外国发行人的财报币种不一定是美元）
    if v is None: return "—"
    sym = CCY_SYM.get(ccy or "USD", (ccy or "") + " ")
    a = abs(v)
    return f"{sym}{v/1e12:.2f}T" if a >= 1e12 else f"{sym}{v/1e9:.1f}B" if a >= 1e9 else f"{sym}{v/1e6:.0f}M"


def _p(v, d=0, sign=True):
    return "—" if v is None else f"{v*100:+.{d}f}%" if sign else f"{v*100:.{d}f}%"


def analysis_snapshot(board: list[dict]) -> None:
    """“分析时价(报告日)”：估值每 7 天重算一次，重算时记下当时的价格与日期，之后用于看“自分析以来涨跌”。"""
    p = CACHE / "analysis.json"
    snap = json.loads(p.read_text("utf-8")) if p.exists() else {}
    today = date.today()
    for r in board:
        if r.get("price") is None:
            continue
        s = snap.get(r["t"])
        if not s or (today - date.fromisoformat(s["date"])).days >= 7:
            s = snap[r["t"]] = {"px": r["price"], "date": today.isoformat(), "fair": r["fair"]}
        r["analysis_px"], r["analysis_date"], r["analysis_fair"] = s["px"], s["date"], s["fair"]
        r["since_analysis"] = round(r["price"] / s["px"] - 1, 4)
    p.write_text(json.dumps(snap, ensure_ascii=False), "utf-8")


def holders_of(t: str, funds: list[dict]) -> list[dict]:
    key = HOLD_KEY.get(t)
    out = []
    for f in funds:
        for row in f["top"] + f.get("moves", []):
            if key and row["issuer"].upper().startswith(key):
                out.append({"fund": f["fund"], "type": f["type"], "pct": row["pct"], "value": row["value"], "act": row["act"], "chg": row["chg"], "period": f["period"]})
                break
    return out


def _risks(r: dict, news: list[dict], holders: list[dict]) -> list[dict]:
    out, cs, fd = [], r.get("cs") or {}, r.get("fund") or {}
    pe, pct, band = r.get("pe_now"), r.get("pe_pct"), r.get("pe_band")
    if pe and pct is not None and band:
        if pct >= 0.75:
            out.append(("估值风险", f"前瞻PE {pe:.1f}x，处于自身历史 {pct*100:.0f}% 分位（P25/P50/P75 = {band[0]:.0f}/{band[1]:.0f}/{band[2]:.0f}x），"
                                    f"价格已隐含较高预期；业绩或指引不及预期时存在估值与盈利双杀。"))
        elif pct <= 0.15:
            out.append(("价值陷阱核查", f"前瞻PE {pe:.1f}x 仅处于历史 {pct*100:.0f}% 分位，低估值可能来自预期被下修或周期见顶，需核对近期盈利预期变化与行业景气方向，而非只看倍数。"))
    g0 = cs.get("g0")
    if g0 is not None and g0 > 0.30:
        out.append(("高预期风险", f"一致预期本财年 EPS 同比 {g0*100:+.0f}%（{cs.get('fy0','')}E），增长已被充分定价，任一季度低于预期的指引都会放大回撤。"))
    elif g0 is not None and g0 < 0:
        out.append(("盈利下滑", f"一致预期本财年 EPS 同比 {g0*100:+.0f}%，盈利处于下行通道，估值支撑依赖于拐点出现的时点。"))
    if r.get("r3m") is not None and r["r3m"] < -0.15:
        out.append(("趋势风险", f"近 3 月 {r['r3m']*100:+.1f}%，距 52 周高点 {r['from_hi']*100:+.1f}%，趋势仍在走弱，左侧买入需要分批并设置失效条件。"))
    elif r.get("r3m") is not None and r["r3m"] > 0.35:
        out.append(("回撤风险", f"近 3 月已上涨 {r['r3m']*100:+.1f}%，短期涨幅较大，追高的盈亏比下降。"))
    if fd.get("debt_equity") is not None and fd["debt_equity"] > 1.5:
        out.append(("杠杆风险", f"长期债务/股东权益 {fd['debt_equity']:.1f}x，利率高位时利息负担与再融资压力上升。"))
    if fd.get("fcf_margin") is not None and fd["fcf_margin"] < 0:
        out.append(("现金流风险", f"最近财年自由现金流为负（FCF 利润率 {fd['fcf_margin']*100:.1f}%），增长依赖外部融资或资本开支回报兑现。"))
    tgt = (r.get("val_methods") or {}).get("分析师目标价(参考)")
    if tgt and r.get("fair") and not 0.6 <= r["fair"] / tgt <= 1.5:
        out.append(("模型分歧", f"本看板合理价 ${r['fair']:.0f} 与分析师一致目标价 ${tgt:.0f} 偏离 {abs(r['fair']/tgt-1)*100:.0f}%，估值结论置信度低，应以基本面跟踪为主。"))
    if cs.get("n0") is not None and cs["n0"] < 8:
        out.append(("覆盖不足", f"仅 {cs['n0']} 位分析师覆盖，一致预期的统计意义较弱。"))
    if cs.get("eps0_lo") and cs.get("eps0_hi") and cs.get("eps0") and cs["eps0"] > 0 and (cs["eps0_hi"] - cs["eps0_lo"]) / cs["eps0"] > 0.35:
        out.append(("预期分歧", f"分析师对本财年 EPS 的区间 {cs['eps0_lo']:.2f}–{cs['eps0_hi']:.2f}（中枢 {cs['eps0']:.2f}），分歧较大。"))
    bad = [n for n in news if n["sentiment"] == "利空" and n["grade"] in ("S", "A", "B")][:2]
    for n in bad:
        out.append(("近期利空", f"[{n['grade']}级] {n['title'][:110]}（{'/'.join(n['sources'][:2])}）"))
    tr = [h for h in holders if h["act"] in ("减仓", "清仓")]
    if tr:
        out.append(("机构减持", "；".join(f"{h['fund']} {h['act']}" + (f" {h['chg']*100:+.0f}%" if h["chg"] not in (None,) else "") for h in tr[:3]) + "（13F，披露滞后约 45 天）"))
    if r.get("risk"):
        out.append(("研究员观点", r["risk"]))
    return [{"k": k, "t": t} for k, t in out]


def _growth(r: dict, news: list[dict], holders: list[dict]) -> list[dict]:
    out, cs, fd = [], r.get("cs") or {}, r.get("fund") or {}
    if cs.get("eps0") and cs.get("g0") is not None:
        s = f"一致预期 {cs.get('fy0','')}E EPS {cs['eps0']:.2f}（{cs['g0']*100:+.0f}%）"
        if cs.get("eps1") and cs.get("g1") is not None:
            s += f"，{cs.get('fy1','')}E {cs['eps1']:.2f}（{cs['g1']*100:+.0f}%）"
        s += f"；{cs.get('n0') or '—'} 位分析师覆盖，口径为 non-GAAP。"
        out.append(("盈利预期", s))
    if cs.get("rev0"):
        s = f"营收一致预期 {cs.get('fy0','')}E {_b(cs['rev0'], cs.get('ccy'))}" + (f"（{cs['revg0']*100:+.0f}%）" if cs.get("revg0") is not None else "")
        if cs.get("rev1"):
            s += f"，{cs.get('fy1','')}E {_b(cs['rev1'], cs.get('ccy'))}" + (f"（{cs['revg1']*100:+.0f}%）" if cs.get("revg1") is not None else "")
        out.append(("营收预期", s + "。"))
    tgt = (r.get("val_methods") or {}).get("分析师目标价(参考)")
    if tgt and r.get("price"):
        out.append(("目标价空间", f"分析师一致目标价 ${tgt:.0f}，较现价 ${r['price']:.1f} 有 {(tgt/r['price']-1)*100:+.0f}% 空间。"))
    if r.get("fair") and r.get("price") and r.get("val_src", "").startswith("自动"):
        s = f"本看板合理价 ${r['fair']:.0f}（买点 ${r['buy']:.0f} / 卖点 ${r['sell']:.0f}），现价距合理价 {(r['price']/r['fair']-1)*100:+.0f}%"
        if r.get("pe_now") and r.get("pe_band"):
            s += f"；前瞻PE {r['pe_now']:.1f}x 对比自身历史中位 {r['pe_band'][1]:.0f}x"
        out.append(("估值修复", s + "。"))
    q = []
    if fd.get("rev_g") is not None: q.append(f"营收同比 {fd['rev_g']*100:+.0f}%")
    if fd.get("net_margin") is not None: q.append(f"净利率 {fd['net_margin']*100:.1f}%")
    if fd.get("fcf_margin") is not None: q.append(f"FCF 利润率 {fd['fcf_margin']*100:.1f}%")
    if fd.get("roe") is not None: q.append(f"ROE {fd['roe']*100:.0f}%")
    if q:
        out.append(("经营质量", f"{fd.get('fy_end','')[:4]} 财年：" + "，".join(q) + "（10-K）。"))
    good = [n for n in news if n["sentiment"] == "利好" and n["grade"] in ("S", "A", "B")][:2]
    for n in good:
        out.append(("近期催化", f"[{n['grade']}级] {n['title'][:110]}（{'/'.join(n['sources'][:2])}）"))
    add = [h for h in holders if h["act"] in ("加仓", "新进")]
    if add:
        out.append(("机构加仓", "；".join(f"{h['fund']} {h['act']}" + (f" {h['chg']*100:+.0f}%" if h["chg"] is not None else "") for h in add[:3]) + "（13F，披露滞后约 45 天）"))
    if r.get("growth"):
        out.append(("研究员观点", r["growth"]))
    return [{"k": k, "t": t} for k, t in out]


def enrich(board: list[dict], news: list[dict], funds: list[dict], alerts: list[dict]) -> None:
    analysis_snapshot(board)
    by_t: dict[str, list[dict]] = {}
    for n in news:
        if n["excluded"]:
            continue
        for t in n["tickers"]:
            by_t.setdefault(t, []).append(n)
    for r in board:
        cs = r.get("cs") or {}
        pf = cs.get("profile") or {}
        r["gics_sector"] = SECTOR_CN.get(pf.get("sector"), pf.get("sector") or "—")
        r["gics_industry"] = INDUSTRY_CN.get(pf.get("industry"), pf.get("industry") or r["sector"])
        r["hq"] = ", ".join(x for x in (pf.get("city"), pf.get("state"), pf.get("country")) if x) or "—"
        r["employees"], r["mcap"], r["website"], r["summary_en"] = pf.get("employees"), pf.get("mcap"), pf.get("website"), pf.get("summary")
        r["news"] = [{k: n[k] for k in ("title", "url", "grade", "sentiment", "imp", "sources", "ts")} for n in by_t.get(r["t"], [])[:8]]
        r["holders"] = holders_of(r["t"], funds)
        r["risks"] = _risks(r, by_t.get(r["t"], []), r["holders"])
        r["growths"] = _growth(r, by_t.get(r["t"], []), r["holders"])
        r["est"] = {"fy0": cs.get("fy0"), "fy1": cs.get("fy1"), "rev0": cs.get("rev0"), "rev1": cs.get("rev1"),
                    "eps0": cs.get("eps0"), "eps1": cs.get("eps1"), "n": cs.get("n0"), "ccy": cs.get("ccy") or "USD"}
        r["alerts"] = [a for a in alerts if a["t"] == r["t"]]
        r.pop("cs", None)  # 原始预期字段已展开，避免重复体积
    research_llm.generate_all(board, by_t, {r["t"]: r["holders"] for r in board})

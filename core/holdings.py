"""SEC 13F 机构持仓：最近两期对比（新进/加仓/减仓/清仓），并汇总"多家共同持有"。
SEC 限速 10 次/秒，且要求 User-Agent 带联系方式（见 config.SEC_USER_AGENT）。"""
from __future__ import annotations
import re, time
import xml.etree.ElementTree as ET
import requests

import config

H = {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def _get(url: str, tries: int = 4):
    """SEC 限速(403/429)时指数退避重试。"""
    for i in range(tries):
        time.sleep(0.2)
        r = requests.get(url, headers=H, timeout=40)
        if r.status_code in (403, 429, 503):
            time.sleep(2 * (i + 1))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def latest_13f_filings(cik: str, n: int = 2) -> list[dict]:
    j = _get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
    rec = j["filings"]["recent"]
    out = []
    for form, acc, date, rep in zip(rec["form"], rec["accessionNumber"], rec["filingDate"], rec["reportDate"]):
        if form == "13F-HR":
            out.append({"acc": acc, "filed": date, "period": rep})
        if len(out) >= n:
            break
    return out


def parse_infotable(cik: str, acc: str) -> dict[str, dict]:
    nod = acc.replace("-", "")
    idx = _get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nod}/index.json").json()
    names = [f["name"] for f in idx["directory"]["item"] if f["name"].lower().endswith(".xml")]
    # 信息表一般是非 primary_doc 的那个 xml
    cand = [n for n in names if "primary" not in n.lower()] or names
    pos: dict[str, dict] = {}
    for nm in cand:
        try:
            root = ET.fromstring(_get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nod}/{nm}").content)
        except Exception:
            continue
        for e in root.iter():
            if not e.tag.endswith("infoTable"):
                continue
            def f(tag):
                for c in e.iter():
                    if c.tag.endswith(tag):
                        return (c.text or "").strip()
                return ""
            issuer, cusip = f("nameOfIssuer"), f("cusip")
            val, sh = f("value"), f("sshPrnamt")
            if not issuer:
                continue
            d = pos.setdefault(cusip or issuer, {"issuer": issuer, "value": 0.0, "shares": 0.0})
            d["value"] += float(val or 0)
            d["shares"] += float(sh or 0)
        if pos:
            break
    return pos


def fund_report(fund: dict) -> dict | None:
    try:
        fl = latest_13f_filings(fund["cik"])
        if not fl:
            return None
        cur = parse_infotable(fund["cik"], fl[0]["acc"])
        prev = parse_infotable(fund["cik"], fl[1]["acc"]) if len(fl) > 1 else {}
    except Exception as e:
        print(f"      13F 失败 {fund['name']}: {type(e).__name__} {str(e)[:80]}")
        return None
    # 2023 年起 value 单位为美元；更早为千美元。这里按美元处理，页面上标注口径
    total = sum(p["value"] for p in cur.values()) or 1
    rows = []
    for k, p in cur.items():
        q = prev.get(k)
        if q is None: act, chg = "新进", None
        elif p["shares"] > q["shares"] * 1.02: act, chg = "加仓", p["shares"] / q["shares"] - 1
        elif p["shares"] < q["shares"] * 0.98: act, chg = "减仓", p["shares"] / q["shares"] - 1
        else: act, chg = "持平", 0.0
        rows.append({"issuer": p["issuer"], "value": p["value"], "pct": p["value"] / total, "shares": p["shares"], "act": act, "chg": chg})
    for k, q in prev.items():
        if k not in cur:
            rows.append({"issuer": q["issuer"], "value": 0, "pct": 0, "shares": 0, "act": "清仓", "chg": -1.0})
    rows.sort(key=lambda r: -r["value"])
    return {"fund": fund["name"], "type": fund["type"], "period": fl[0]["period"], "filed": fl[0]["filed"],
            "total": total, "n": len(cur), "top": rows[:25],
            "moves": [r for r in rows if r["act"] in ("新进", "加仓", "减仓", "清仓")][:30]}


# ETF 家族的统称（如 "ISHARES TR" 下面是几十只不同的 ETF），13F 里同名但不是同一个标的，不能当作“共同持有”
_FAMILY = re.compile(r"^(ISHARES (TR|INC|U S ETF TR)|VANGUARD (INDEX|SCOTTSDALE|WORLD|MALVERN) FDS|SELECT SECTOR SPDR TR|SPDR SER TR|"
                     r"PROSHARES TR|INVESCO EXCHANGE TRADED FD TR|DIREXION SHS ETF TR|VANECK ETF TR(UST)?|ETF SER SOLUTIONS|ALPS ETF TR|GLOBAL X FDS)$")


def overlap(reports: list[dict]) -> list[dict]:
    """多家机构共同持有（对应 A 股版"三类资金共同重仓"）。
    按“机构”去重计数：同一家机构持有同一公司的多个股票类别（如 Alphabet A/C 类）只算 1 家，
    否则会出现“11 家持有”而总共只跟踪 8 家机构的错误。"""
    m: dict[str, dict[str, list]] = {}
    for r in reports:
        for t in r["top"][:25]:
            name = re.sub(r"\s+", " ", t["issuer"].upper().strip())
            if _FAMILY.match(name):
                continue
            key = re.sub(r"[^A-Z0-9]", "", name)[:14]
            m.setdefault(key, {}).setdefault(r["fund"], []).append(t)
    out = []
    for by_fund in m.values():
        if len(by_fund) >= 2:
            rows = [t for ts in by_fund.values() for t in ts]
            out.append({"issuer": rows[0]["issuer"], "funds": list(by_fund), "n": len(by_fund),
                        "value": sum(t["value"] for t in rows),
                        "adds": sum(1 for ts in by_fund.values() if any(t["act"] in ("新进", "加仓") for t in ts))})
    out.sort(key=lambda x: (-x["n"], -x["value"]))
    return out[:30]


def build_holdings() -> dict:
    reps = []
    for f in config.FUNDS_13F:
        r = fund_report(f)
        if r:
            reps.append(r)
            print(f"      13F 成功 {f['name']} {r['period']} {r['n']} 只")
    return {"funds": reps, "overlap": overlap(reps),
            "note": "数据来自 SEC 13F-HR，披露滞后季末约 45 天；仅含美股多头与部分期权，不含空头与非美资产。"}

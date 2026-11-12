"""探测 Yahoo quoteSummary 各模块在免费接口下是否可用、字段长什么样（只读）。"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from core.valuation import Consensus

MODS = ["earningsTrend", "financialData", "defaultKeyStatistics", "summaryDetail", "recommendationTrend",
        "calendarEvents", "earningsHistory", "netSharePurchaseActivity", "majorHoldersBreakdown", "upgradeDowngradeHistory"]
s = Consensus()._session()
if not s:
    sys.exit("取 crumb 失败")
raw = lambda d, k: (d.get(k) or {}).get("raw") if isinstance(d.get(k), dict) else d.get(k)
for t in sys.argv[1:] or ["NVDA", "JPM"]:
    print(f"\n===== {t} =====")
    for m in MODS:
        r = s.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{t}", params={"modules": m, "crumb": s.crumb}, timeout=20)
        if r.status_code != 200:
            print(f"{m:26} HTTP {r.status_code}"); continue
        try:
            j = r.json()["quoteSummary"]["result"][0].get(m)
        except Exception as e:
            print(f"{m:26} 解析失败 {e}"); continue
        if j is None:
            print(f"{m:26} 空"); continue
        if m == "earningsTrend":
            tr = {e["period"]: e for e in j["trend"]}
            e0 = tr.get("0y", {})
            print(f"{m:26} 0y.epsTrend={ {k: raw(e0.get('epsTrend',{}),k) for k in ('current','30daysAgo','90daysAgo')} } epsRevisions={ {k: raw(e0.get('epsRevisions',{}),k) for k in ('upLast30days','downLast30days')} }")
        elif m == "financialData":
            print(f"{m:26}", {k: raw(j, k) for k in ("targetHighPrice", "targetLowPrice", "targetMedianPrice", "recommendationMean", "numberOfAnalystOpinions", "freeCashflow", "ebitda", "totalDebt", "totalCash", "returnOnEquity", "profitMargins", "operatingMargins")})
        elif m == "defaultKeyStatistics":
            print(f"{m:26}", {k: raw(j, k) for k in ("forwardPE", "pegRatio", "priceToBook", "enterpriseToEbitda", "beta", "shortPercentOfFloat", "heldPercentInsiders", "heldPercentInstitutions")})
        elif m == "summaryDetail":
            print(f"{m:26}", {k: raw(j, k) for k in ("dividendYield", "payoutRatio", "marketCap", "trailingPE", "fiftyTwoWeekHigh")})
        elif m == "recommendationTrend":
            print(f"{m:26}", j["trend"][0] if j.get("trend") else None)
        elif m == "calendarEvents":
            ed = [(x.get("raw")) for x in (j.get("earnings", {}).get("earningsDate") or [])]
            print(f"{m:26} earningsDate={ed} exDiv={raw(j,'exDividendDate')}")
        elif m == "earningsHistory":
            print(f"{m:26}", [(h.get("quarter", {}).get("fmt"), raw(h, "epsActual"), raw(h, "epsEstimate"), raw(h, "surprisePercent")) for h in j.get("history", [])])
        elif m == "netSharePurchaseActivity":
            print(f"{m:26}", {k: raw(j, k) for k in ("period", "buyInfoCount", "sellInfoCount", "netInfoCount", "netPercentInsiderShares")})
        elif m == "majorHoldersBreakdown":
            print(f"{m:26}", {k: raw(j, k) for k in ("insidersPercentHeld", "institutionsPercentHeld", "institutionsCount")})
        else:
            print(f"{m:26} keys={list(j)[:6]}")

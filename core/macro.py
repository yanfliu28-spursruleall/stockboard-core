"""市场环境面板：指数 / 波动率 / 利率 / 美元 / 信用 / 大宗，以及基于股票池的市场宽度，并给出一个可解释的“风险偏好”判断。
没有这些参照，单只股票的“便宜/贵”没有意义（利率与风险偏好决定估值中枢）。全部来自 Yahoo 公共行情 + 本地股票池面板。"""
from __future__ import annotations
import math
from datetime import date
from pathlib import Path

from core import market

SYMS = [("^GSPC", "标普500"), ("^IXIC", "纳指"), ("^RUT", "罗素2000"), ("RSP", "等权标普"), ("^VIX", "VIX"),
        ("^TNX", "10年期收益率"), ("DX-Y.NYB", "美元指数"), ("HYG", "高收益债"), ("LQD", "投资级债"),
        ("GLD", "黄金"), ("CL=F", "WTI 原油")]
PANEL = Path(__file__).resolve().parent.parent / "cache" / "panel2.pkl"


def _ret(c, n):
    return c[-1] / c[-1 - n] - 1 if len(c) > n else None


def _item(sym, name, h):
    c = h["close"]
    ma = lambda n: sum(c[-n:]) / min(n, len(c))
    lo, hi = min(c), max(c)
    d = {"sym": sym, "name": name, "last": round(c[-1], 2), "d1": _ret(c, 1), "w1": _ret(c, 5), "m1": _ret(c, 21), "m3": _ret(c, 63),
         "vs50": c[-1] / ma(50) - 1, "vs200": c[-1] / ma(200) - 1, "pos52": (c[-1] - lo) / (hi - lo + 1e-9)}
    if sym == "^TNX":
        d["bp1m"] = round((c[-1] - c[-22]) * 100) if len(c) > 22 else None     # 收益率 1 个月变动（基点）
    if sym == "^VIX":
        d["pctile"] = sum(1 for x in c if x <= c[-1]) / len(c)                   # 当前 VIX 在近 1 年中的分位
    ys = [x for t, x in zip(h["ts"], c) if date.fromtimestamp(t).year == date.today().year]
    d["ytd"] = c[-1] / ys[0] - 1 if ys else None
    return d


def _breadth():
    try:
        import pandas as pd
        P = pd.read_pickle(PANEL)
        adj = P["adj"].drop(columns=["SPY"], errors="ignore")
        last = adj.iloc[-1]
        ok = last.notna()
        a200 = (adj.iloc[-1] > adj.rolling(200).mean().iloc[-1])[ok].mean()
        a50 = (adj.iloc[-1] > adj.rolling(50).mean().iloc[-1])[ok].mean()
        hi = (adj.iloc[-1] >= adj.rolling(252).max().iloc[-1] * 0.999)[ok].sum()
        lo = (adj.iloc[-1] <= adj.rolling(252).min().iloc[-1] * 1.001)[ok].sum()
        return {"n": int(ok.sum()), "above200": round(float(a200), 3), "above50": round(float(a50), 3), "new_high": int(hi), "new_low": int(lo),
                "asof": str(adj.index[-1].date())}
    except Exception:
        return None


def build() -> dict | None:
    hist = market.fetch_many([s for s, _ in SYMS] + ["SPY"])      # SPY 仅用于计算等权/市值加权的相对强弱，不展示
    items = [_item(s, n, hist[s]) for s, n in SYMS if hist.get(s)]
    if not items:
        return None
    by = {i["sym"]: i for i in items}
    br = _breadth()
    pts, why = 0, []
    spx, vix = by.get("^GSPC"), by.get("^VIX")
    if spx:
        pts += (spx["vs200"] > 0) + (spx["vs50"] > 0)
        why.append(f"标普500 {'高于' if spx['vs200'] > 0 else '低于'}200日线 {abs(spx['vs200'])*100:.1f}%，{'高于' if spx['vs50'] > 0 else '低于'}50日线 {abs(spx['vs50'])*100:.1f}%")
    if vix:
        pts += 1 if vix["last"] < 20 else (-1 if vix["last"] > 30 else 0)
        why.append(f"VIX {vix['last']}（近1年 {vix['pctile']*100:.0f}% 分位）")
    if br:
        pts += 1 if br["above200"] > 0.6 else (-1 if br["above200"] < 0.4 else 0)
        why.append(f"{br['above200']*100:.0f}% 的股票在200日线上方（{br['n']} 只样本）")
    hyg, lqd = hist.get("HYG"), hist.get("LQD")
    credit = None
    if hyg and lqd and len(hyg["close"]) == len(lqd["close"]):
        r = [a / b for a, b in zip(hyg["close"], lqd["close"])]
        credit = r[-1] / (sum(r[-50:]) / 50) - 1
        pts += 1 if credit > 0 else -1
        why.append(f"高收益/投资级债比价 {'高于' if credit > 0 else '低于'}50日均值 {abs(credit)*100:.2f}%（{'信用偏稳' if credit > 0 else '信用走弱'}）")
    label = "风险偏好偏强" if pts >= 4 else "风险偏好偏弱" if pts <= 0 else "中性偏谨慎" if pts <= 2 else "中性偏强"
    rsp, spy = by.get("RSP"), hist.get("SPY")
    return {"items": items, "breadth": br, "credit": credit, "regime": {"label": label, "score": int(pts), "why": why},
            "rsp_vs_spx_3m": (rsp["m3"] - (_ret(spy["close"], 63) or 0)) if rsp and spy else None,
            "asof": date.today().isoformat()}

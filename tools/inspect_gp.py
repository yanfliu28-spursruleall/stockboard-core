"""检查遗传因子到底在“选”什么：与已知特征（规模/波动/动量/流动性/行业）的横截面秩相关。"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.stdout.reconfigure(encoding="utf-8")
from core import quant_dsl as D, quant_eval as E

P = pd.read_pickle(root / "cache" / "panel2.pkl")
q = json.loads((root / "cache" / "quant2.json").read_text("utf-8"))
ctx, X = E.Ctx(P), D.make_fields(P)
adj, close, vol = P["adj"], P["close"], P["vol"]
ret = X["ret"]
known = {
    "美元成交额(规模/流动性代理)": np.log1p((vol * close).rolling(60).mean()),
    "股价水平": np.log(close),
    "波动率60日": ret.rolling(60).std(),
    "12-1动量": adj.shift(21) / adj.shift(252) - 1,
    "近1月涨幅": adj / adj.shift(21) - 1,
    "成交量水平(股数)": np.log1p(vol.rolling(60).mean()),
    "成交量趋势(20/120)": vol.rolling(20).mean() / vol.rolling(120).mean(),
}
rb = ctx.rb[ctx.rb >= adj.index[300]]


def xcorr(A, B):
    c = []
    for d in rb[::2]:
        a, b = A.loc[d, ctx.tickers], B.loc[d, ctx.tickers]
        m = a.notna() & b.notna()
        if m.sum() > 60:
            c.append(a[m].rank().corr(b[m].rank()))
    return float(np.nanmean(c))


print(f"{'遗传因子':8}{'公式':52}" + "".join(f"{k[:9]:>11}" for k in known))
for r in q["factors"]:
    if r["src"] != "遗传规划":
        continue
    F = D.evaluate(D.parse(r["expr"]), X)
    print(f"{r['id']:8}{r['expr'][:50]:52}" + "".join(f"{xcorr(F, K):11.2f}" for K in known.values()))

# 行业暴露：因子在各行业的平均分位
sec = pd.Series(json.loads((root / "cache" / "sectors.json").read_text("utf-8"))["map"])
print("\n各行业平均分位（0=最低分, 1=最高分），最近一期：")
for r in q["factors"]:
    if r["src"] != "遗传规划":
        continue
    F = D.evaluate(D.parse(r["expr"]), X)
    last = F.loc[F.index[-1], ctx.tickers].dropna().rank(pct=True)
    g = last.groupby(sec.reindex(last.index).fillna("未知")).mean().sort_values()
    print(r["id"], " | ".join(f"{k[:6]} {v:.2f}" for k, v in list(g.items())[:2] + list(g.items())[-2:]))

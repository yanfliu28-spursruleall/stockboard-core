"""因子评估引擎：月度调仓 · 秩 IC · 五分位组合 · 多空/纯多头 · 成本 · 样本内外 · 分行情。

无前视约定：t 日（月末）的因子值只使用 ≤t 的数据；收益是 (t, 下一个月末] 的复权收盘收益。
样本内(IS)/样本外(OOS)按“时间顺序前 60% / 后 40%”切分，因子的筛选、选入、定向只允许看 IS。"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

IS_FRAC = 0.6
MIN_N = 30          # 每期有效股票数下限
COST_BPS = 10       # 单边成本 (bps)，含冲击与佣金的粗略假设
NQ = 5


def rebalance_dates(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(idx, index=idx)
    return pd.DatetimeIndex(s.groupby([idx.year, idx.month]).last().values)


class Ctx:
    """一次研究的共享上下文：月末日期、下期收益、基准、行情状态。"""

    def __init__(self, P: dict):
        adj = P["adj"]
        self.tickers = [c for c in adj.columns if c != "SPY"]
        self.adj = adj[self.tickers]
        self.rb = rebalance_dates(adj.index)
        px = adj.loc[self.rb]
        fwd_all = px.shift(-1) / px - 1                     # 每个月末 → 下个月末
        self.fwd = fwd_all[self.tickers]
        self.spy_fwd = fwd_all["SPY"]
        self.ew_fwd = self.fwd.mean(axis=1)                  # 等权全市场基准
        spy = adj["SPY"]
        self.bull = (spy > spy.rolling(200).mean()).loc[self.rb]  # t 月末 SPY 在 200 日线上方 = 牛市状态
        self.dates = [d for d in self.rb if not np.isnan(self.ew_fwd.get(d, np.nan))]
        # 所有因子共用同一个样本内/外切分日（按日历），避免各因子边界不一致造成隐性泄漏。
        # 起点跳过前 260 个交易日：最长窗口的因子（12-1 动量）需要这么多历史才有值。
        warm = adj.index[min(260, len(adj) - 1)]
        vd = [d for d in self.dates if d >= warm]
        self.cutoff = vd[int(len(vd) * IS_FRAC)]


def _rank(a: np.ndarray) -> np.ndarray:
    return np.argsort(np.argsort(a)).astype(float)


def ic_series(F: pd.DataFrame, ctx: Ctx) -> pd.Series:
    """每个月末：因子与下期收益的秩相关系数（Spearman IC）。"""
    out = {}
    for d in ctx.dates:
        if d not in F.index: continue
        f, r = F.loc[d, ctx.tickers].values.astype(float), ctx.fwd.loc[d].values.astype(float)
        m = ~(np.isnan(f) | np.isnan(r))
        if m.sum() < MIN_N: continue
        out[d] = np.corrcoef(_rank(f[m]), _rank(r[m]))[0, 1]
    return pd.Series(out, dtype=float)


def ic_stats(ic: pd.Series) -> dict:
    n = len(ic)
    if n < 6: return {"n": n, "ic": None, "icir": None, "t": None, "hit": None}
    m, s = ic.mean(), ic.std(ddof=1)
    return {"n": n, "ic": round(float(m), 4), "icir": round(float(m / s), 3) if s > 0 else None,
            "t": round(float(m / s * math.sqrt(n)), 2) if s > 0 else None, "hit": round(float((ic > 0).mean()), 3)}


def is_oos(ic: pd.Series, ctx: Ctx):
    return ic[ic.index < ctx.cutoff], ic[ic.index >= ctx.cutoff]


# ---------------------------------------------------------------- 组合
def _groups(f: np.ndarray, m: np.ndarray) -> np.ndarray:
    g = np.full(len(f), -1)
    r = _rank(f[m])
    g[m] = np.minimum((r / m.sum() * NQ).astype(int), NQ - 1)
    return g


def portfolios(F: pd.DataFrame, ctx: Ctx) -> dict:
    """五分位等权组合月度收益 + 最高/最低分位换手。"""
    dates, qret, turn_top, turn_bot, prev_top, prev_bot = [], [], [], [], None, None
    for d in ctx.dates:
        if d not in F.index: continue
        f, r = F.loc[d, ctx.tickers].values.astype(float), ctx.fwd.loc[d].values.astype(float)
        m = ~(np.isnan(f) | np.isnan(r))
        if m.sum() < MIN_N: continue
        g = _groups(f, m)
        qret.append([float(np.mean(r[g == q])) for q in range(NQ)])
        top, bot = set(np.where(g == NQ - 1)[0]), set(np.where(g == 0)[0])
        turn_top.append(1 - len(top & prev_top) / len(top) if prev_top else 1.0)
        turn_bot.append(1 - len(bot & prev_bot) / len(bot) if prev_bot else 1.0)
        prev_top, prev_bot = top, bot
        dates.append(d)
    return {"dates": dates, "q": np.array(qret), "turn_top": np.array(turn_top), "turn_bot": np.array(turn_bot)}


def _curve(r: np.ndarray) -> np.ndarray:
    return np.cumprod(1 + r)


def _perf(r: np.ndarray) -> dict:
    n = len(r)
    if n < 6: return {"ann": None, "vol": None, "sharpe": None, "mdd": None}
    c = _curve(r)
    ann = c[-1] ** (12 / n) - 1
    vol = r.std(ddof=1) * math.sqrt(12)
    peak = np.maximum.accumulate(np.concatenate([[1.0], c]))[1:]
    return {"ann": round(float(ann), 4), "vol": round(float(vol), 4), "sharpe": round(float(r.mean() * 12 / vol), 2) if vol > 0 else None,
            "mdd": round(float((c / peak - 1).min()), 4)}


def backtest(F: pd.DataFrame, ctx: Ctx) -> dict:
    pf = portfolios(F, ctx)
    if len(pf["dates"]) < 12: return {}
    q, ds = pf["q"], pf["dates"]
    ew = ctx.ew_fwd.loc[ds].values
    spy = ctx.spy_fwd.loc[ds].values
    c = COST_BPS / 1e4
    top_g, ls_g = q[:, -1], q[:, -1] - q[:, 0]
    top_n = top_g - 2 * c * pf["turn_top"]                       # 卖出旧仓+买入新仓
    ls_n = ls_g - 2 * c * (pf["turn_top"] + pf["turn_bot"])      # 多空两条腿都要交易
    k = sum(1 for d in ds if d < ctx.cutoff)
    return {
        "dates": [d.strftime("%Y-%m") for d in ds], "split": k,
        "q_curves": [_curve(q[:, i]).round(4).tolist() for i in range(NQ)],
        "top_net": _curve(top_n).round(4).tolist(), "ls_net": _curve(ls_n).round(4).tolist(), "ls_gross": _curve(ls_g).round(4).tolist(),
        "ew": _curve(ew).round(4).tolist(), "spy": _curve(spy).round(4).tolist(),
        "q_ann": [round(float(_curve(q[:, i])[-1] ** (12 / len(ds)) - 1), 4) for i in range(NQ)],
        "perf": {"ls_net": _perf(ls_n), "ls_gross": _perf(ls_g), "top_net": _perf(top_n), "ew": _perf(ew), "spy": _perf(spy),
                 "ls_net_is": _perf(ls_n[:k]), "ls_net_oos": _perf(ls_n[k:]), "top_excess_oos": round(float((top_n[k:] - ew[k:]).mean() * 12), 4)},
        "turnover_top": round(float(pf["turn_top"][1:].mean()), 3) if len(ds) > 1 else None,
        "monotonic": round(float(np.corrcoef(range(NQ), [q[:, i].mean() for i in range(NQ)])[0, 1]), 2),  # 分位收益单调性
    }


def regime_ic(ic: pd.Series, ctx: Ctx) -> dict:
    b = ctx.bull.reindex(ic.index).fillna(False).astype(bool)
    f = lambda s: round(float(s.mean()), 4) if len(s) >= 6 else None
    return {"bull": f(ic[b]), "bear": f(ic[~b]), "n_bull": int(b.sum()), "n_bear": int((~b).sum())}


def verdict(st: dict) -> tuple[str, str]:
    """把 样本内/样本外/全样本 统计压成一个可解释的标签（规则写死，避免事后挑口径）。"""
    a, i, o = st["all"], st["is"], st["oos"]
    if a["t"] is None or abs(a["t"]) < 1.5:
        return "无显著性", "全样本 IC 的 t 值 <1.5，与随机无法区分"
    if i["icir"] is None or o["icir"] is None:
        return "样本不足", "样本内或样本外月份太少"
    if i["icir"] > 0.3 and (o["icir"] < 0 or o["icir"] < 0.4 * i["icir"]):
        return "过拟合嫌疑", f"样本内 ICIR {i['icir']} → 样本外 {o['icir']}，大幅衰减甚至反向"
    if o["icir"] >= 0.6 * i["icir"] and o["t"] is not None and o["t"] > 1:
        return "稳健", f"样本外 ICIR {o['icir']} 保持样本内的 {o['icir'] / i['icir'] * 100:.0f}%"
    return "衰减", f"样本外 ICIR {o['icir']} 弱于样本内 {i['icir']}"


def full_report(F: pd.DataFrame, ctx: Ctx) -> dict | None:
    ic = ic_series(F, ctx)
    if len(ic) < 24: return None
    bt = backtest(F, ctx)
    if not bt: return None
    a, (ic_is, ic_oos) = ic, is_oos(ic, ctx)
    st = {"all": ic_stats(a), "is": ic_stats(ic_is), "oos": ic_stats(ic_oos)}
    tag, why = verdict(st)
    last = F.loc[[d for d in F.index if d <= ctx.rb[-1]][-1], ctx.tickers].dropna().sort_values(ascending=False)
    pct = last.rank(pct=True)
    return {"stats": st, "regime": regime_ic(ic, ctx), "bt": bt, "tag": tag, "why": why,
            "ic_dates": [d.strftime("%Y-%m") for d in ic.index], "ic": ic.round(4).tolist(), "ic_split": int((ic.index < ctx.cutoff).sum()), "cutoff": ctx.cutoff.strftime("%Y-%m"),
            "top": [[t, round(float(pct[t]), 2)] for t in last.index[:8]], "bottom": [[t, round(float(pct[t]), 2)] for t in last.index[-8:]]}

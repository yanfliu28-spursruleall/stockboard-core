"""因子表达式语言（DSL）：手工因子、遗传规划、大模型提出的公式共用同一套表达式 —— 这样才能用同一把尺子评估。

表达式例： neg(ts_std(ret,20))   cs_rank(ts_corr(ret,lvol,20))   div(ts_delta(lvol,5),ts_mean(rng,20))
  字段   ret(日收益)  lvol(对数成交量)  rng((最高-最低)/收盘)
  一元   neg abs cs_rank                         (cs_rank = 当日横截面分位)
  带窗口 ts_mean ts_std ts_sum ts_max ts_min ts_delta ts_zscore ts_corr(x,y,w)
  二元   add sub mul div
窗口取值固定在 WINS，防止模型/进化产生任意窗口导致无意义的“调参”。"""
from __future__ import annotations
import random, re
import numpy as np
import pandas as pd

WINS = [5, 10, 20, 40, 60, 120]
FIELDS = ["ret", "lvol", "rng"]
UN = ["neg", "abs", "cs_rank"]
UNW = ["ts_mean", "ts_std", "ts_sum", "ts_max", "ts_min", "ts_delta", "ts_zscore"]
BIN = ["add", "sub", "mul", "div"]
BINW = ["ts_corr"]
MAX_NODES = 14


# ---------------------------------------------------------------- 字符串 <-> 树
def to_str(t) -> str:
    k = t[0]
    if k == "f": return t[1]
    if k in UN: return f"{k}({to_str(t[1])})"
    if k in UNW: return f"{k}({to_str(t[1])},{t[2]})"
    if k in BIN: return f"{k}({to_str(t[1])},{to_str(t[2])})"
    return f"{k}({to_str(t[1])},{to_str(t[2])},{t[3]})"


def parse(s: str):
    toks = re.findall(r"[A-Za-z_]+|\d+|[(),]", s.replace(" ", ""))
    pos = [0]

    def nxt():
        pos[0] += 1; return toks[pos[0] - 1]

    def expr():
        name = nxt()
        if name in FIELDS:
            return ("f", name)
        if name not in UN + UNW + BIN + BINW:
            raise ValueError(f"未知算子/字段: {name}")
        assert nxt() == "("
        a = expr()
        if name in UN:
            assert nxt() == ")"; return (name, a)
        assert nxt() == ","
        if name in UNW:
            w = int(nxt()); assert w in WINS, f"窗口 {w} 不在允许范围 {WINS}"; assert nxt() == ")"; return (name, a, w)
        b = expr()
        if name in BIN:
            assert nxt() == ")"; return (name, a, b)
        assert nxt() == ","
        w = int(nxt()); assert w in WINS, f"窗口 {w} 不在允许范围 {WINS}"; assert nxt() == ")"
        return (name, a, b, w)

    t = expr()
    if pos[0] != len(toks):
        raise ValueError("表达式末尾有多余内容")
    if size(t) > MAX_NODES:
        raise ValueError("表达式过长")
    return t


def size(t) -> int:
    return 1 + sum(size(c) for c in t[1:] if isinstance(c, tuple))


# ---------------------------------------------------------------- 求值
def make_fields(P: dict) -> dict[str, pd.DataFrame]:
    adj = P["adj"]
    return {"ret": adj.pct_change(), "lvol": np.log1p(P["vol"]), "rng": (P["high"] - P["low"]) / adj}


def ev(t, X):
    k = t[0]
    if k == "f": return X[t[1]]
    if k == "neg": return -ev(t[1], X)
    if k == "abs": return ev(t[1], X).abs()
    if k == "cs_rank": return ev(t[1], X).rank(axis=1, pct=True)
    if k in UNW:
        x, w = ev(t[1], X), t[2]
        if k == "ts_mean": return x.rolling(w, min_periods=w).mean()
        if k == "ts_std": return x.rolling(w, min_periods=w).std()
        if k == "ts_sum": return x.rolling(w, min_periods=w).sum()
        if k == "ts_max": return x.rolling(w, min_periods=w).max()
        if k == "ts_min": return x.rolling(w, min_periods=w).min()
        if k == "ts_delta": return x - x.shift(w)
        return (x - x.rolling(w, min_periods=w).mean()) / (x.rolling(w, min_periods=w).std() + 1e-9)
    a, b = ev(t[1], X), ev(t[2], X)
    if k == "add": return a + b
    if k == "sub": return a - b
    if k == "mul": return a * b
    if k == "div": return a / b.where(b.abs() > 1e-9)
    return a.rolling(t[3], min_periods=t[3]).corr(b)


def evaluate(t, X) -> pd.DataFrame:
    return ev(t, X).replace([np.inf, -np.inf], np.nan)


# ---------------------------------------------------------------- 遗传规划的树操作
def rand_tree(rnd: random.Random, depth: int):
    if depth <= 0 or rnd.random() < 0.25:
        return ("f", rnd.choice(FIELDS))
    g = rnd.random()
    if g < 0.15: return (rnd.choice(UN), rand_tree(rnd, depth - 1))
    if g < 0.55: return (rnd.choice(UNW), rand_tree(rnd, depth - 1), rnd.choice(WINS))
    if g < 0.85: return (rnd.choice(BIN), rand_tree(rnd, depth - 1), rand_tree(rnd, depth - 1))
    return ("ts_corr", rand_tree(rnd, depth - 1), rand_tree(rnd, depth - 1), rnd.choice(WINS))


def _paths(t, p=()):
    yield p
    for i, c in enumerate(t[1:], 1):
        if isinstance(c, tuple):
            yield from _paths(c, p + (i,))


def _get(t, p):
    for i in p: t = t[i]
    return t


def _set(t, p, new):
    if not p: return new
    l = list(t); l[p[0]] = _set(t[p[0]], p[1:], new); return tuple(l)


def crossover(rnd, a, b):
    pa, pb = rnd.choice(list(_paths(a))), rnd.choice(list(_paths(b)))
    return _set(a, pa, _get(b, pb))


def mutate(rnd, t):
    p = rnd.choice(list(_paths(t)))
    sub = _get(t, p)
    r = rnd.random()
    if r < 0.5 or sub[0] == "f":
        return _set(t, p, rand_tree(rnd, 2))
    if sub[0] in UNW:  # 只换窗口
        return _set(t, p, (sub[0], sub[1], rnd.choice(WINS)))
    return _set(t, p, rand_tree(rnd, 2))

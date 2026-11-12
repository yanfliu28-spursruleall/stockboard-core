"""量化因子与回测：三代因子挖掘放在同一把尺子下评估。

  第一代 手工因子     —— 价量因子 + 基本面因子（点对点 PIT），每个都写明经济学机制（HAND / FUND）
  第二代 遗传规划     —— 在表达式空间随机搜索 + 进化；只用样本内打分选择，样本外验证（gp_mine）
  第三代 大模型提出假设 —— 让模型“从行为金融/风险溢价机制出发”写公式，仍用同一套评估（llm_propose，需 key，可选）
  强化学习            —— 未实现。页面如实标注，不假装有。
每个因子同时给出：原始评估 + 行业中性评估 + 给前端交互回测用的“月末排名矩阵”。
"""
from __future__ import annotations
import base64, copy, json, math, os, random, re, time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

from core import quant_data, quant_dsl as D, quant_eval as E, quant_fund

CACHE = Path(__file__).resolve().parent.parent / "cache" / "quant2.json"

FAMILY_ORDER = ["动量", "反转", "风险", "流动性", "价值", "质量", "成长", "遗传", "LLM", "合成"]


# ---------------------------------------------------------------- 第一代：手工因子（高 = 预期更好）
def _hand(P, X):
    adj, ret, vol = P["adj"], X["ret"], P["vol"]
    spy = ret["SPY"]
    return {
        "mom_12_1": (adj.shift(21) / adj.shift(252) - 1, "12-1 月动量", "动量", "投资者对信息反应不足，趋势延续；跳过最近 1 个月以避开短期反转"),
        "mom_6_1": (adj.shift(21) / adj.shift(126) - 1, "6-1 月动量", "动量", "同上，窗口更短，对风格切换更敏感"),
        "high_52w": (adj / adj.rolling(252).max(), "接近 52 周高点", "动量", "锚定效应：投资者不愿在接近高点时继续买入，利好消息被低估"),
        "trend_200": (adj / adj.rolling(200).mean() - 1, "200 日趋势", "动量", "价格在长期均线上方 = 中期趋势向上"),
        "rev_1m": (-(adj / adj.shift(21) - 1), "1 月反转", "反转", "短期过度反应与流动性冲击，随后回归"),
        "low_vol_60": (-ret.rolling(60).std(), "低波动 (60日)", "风险", "杠杆约束与彩票偏好使高波动股被高估，低波动股风险调整后收益更好"),
        "low_beta_120": (-(ret.rolling(120).cov(spy).div(spy.rolling(120).var(), axis=0)), "低贝塔 (120日)", "风险", "不能加杠杆的投资者偏好高贝塔股，推高其价格、压低其未来收益"),
        "lottery_max": (-ret.rolling(21).max(), "彩票效应 (近月最大单日涨幅)", "风险", "偏好彩票型股票的投资者推高了近期出现极端上涨的股票"),
        "skew_60": (-ret.rolling(60).skew(), "低偏度 (60日)", "风险", "正偏度（偶发暴涨）被追捧而高估，未来收益偏低"),
        "rng_20": (-((P["high"] - P["low"]) / adj).rolling(20).mean(), "低日内振幅 (20日)", "风险", "日内波动大 = 分歧与不确定性高，风险溢价难以兑现"),
        "vol_trend": (-(vol.rolling(20).mean() / vol.rolling(120).mean() - 1), "缩量 (20/120日成交量)", "流动性", "成交量骤增常伴随关注度过热，随后收益偏低"),
    }


FUND = {
    "ey": ("盈利收益率 E/P", "价值", "市盈率的倒数：越便宜，未来回报补偿越高（价值溢价）；EPS 为披露日已知的上一财年"),
    "bp": ("账面市值比 B/P", "价值", "净资产相对价格越高越便宜；对轻资产/无形资产多的公司不公平，需结合行业中性看"),
    "fcfy": ("自由现金流收益率", "价值", "自由现金流相对价格，比会计利润更难被操纵"),
    "roe": ("净资产收益率 ROE", "质量", "资本回报高的公司竞争优势更强；但高 ROE 可能已被定价"),
    "net_margin": ("净利率", "质量", "盈利能力强，对成本冲击有缓冲"),
    "fcf_margin": ("自由现金流利润率", "质量", "盈利转化为现金的能力，应计项目低 → 盈利质量高"),
    "low_lev": ("低杠杆 (-债务/权益)", "质量", "低杠杆公司在融资收紧时更稳健"),
    "rev_g": ("营收同比增速", "成长", "增长延续；但高增长常伴随高估值，需与价值对照"),
    "ni_g": ("净利润同比增速", "成长", "盈利改善的延续性（基数为负时按绝对值处理并截断 ±300%）"),
}


# ---------------------------------------------------------------- 第二代：遗传规划
def gp_mine(ctx: E.Ctx, X: dict, hand_panels: dict, seed: int = 7, pop: int = 48, gens: int = 7) -> tuple[list[dict], int]:
    rnd = random.Random(seed)
    ctx_is = copy.copy(ctx)
    ctx_is.dates = [d for d in ctx.dates if d < ctx.cutoff]       # 适应度只看样本内 —— 样本外对搜索过程不可见
    seen: dict[str, tuple] = {}

    def fit(t):
        s = D.to_str(t)
        if s in seen: return seen[s]
        try:
            ic = E.ic_series(D.evaluate(t, X), ctx_is)
            v = (-9.0, 0.0, 0) if len(ic) < 18 or ic.std() == 0 else (abs(ic.mean() / ic.std()) - 0.012 * D.size(t), float(ic.mean() / ic.std()), len(ic))
        except Exception:
            v = (-9.0, 0.0, 0)
        seen[s] = v
        return v

    popu = [D.rand_tree(rnd, 3) for _ in range(pop)]
    for g in range(gens):
        scored = sorted(popu, key=lambda t: -fit(t)[0])
        nxt = scored[:6]
        tour = lambda: max(rnd.sample(scored, 4), key=lambda t: fit(t)[0])
        while len(nxt) < pop:
            r = rnd.random()
            c = D.crossover(rnd, tour(), tour()) if r < 0.6 else D.mutate(rnd, tour()) if r < 0.9 else D.rand_tree(rnd, 3)
            if D.size(c) <= D.MAX_NODES and c[0] != "f":
                nxt.append(c)
        popu = nxt
    for t in popu: fit(t)

    def panel_rank(F):
        return F.loc[ctx.rb].rank(axis=1, pct=True)

    def corr(a, b):
        return float(np.nanmean([a.loc[d].corr(b.loc[d]) for d in a.index[::3] if d in b.index]))

    kept, kept_panels = [], [panel_rank(v[0]) for v in hand_panels.values()]
    ranked = sorted(((s, v) for s, v in seen.items() if v[0] > -9), key=lambda kv: -kv[1][0])
    for s, v in ranked:
        t = D.parse(s)
        F = D.evaluate(t, X)
        if v[1] < 0:                                                  # 按样本内 IC 方向定向（仍只用了样本内信息）
            t, F = ("neg", t), -F
        pr = panel_rank(F)
        if any(abs(corr(pr, o)) > 0.85 for o in kept_panels):
            continue
        kept.append({"expr": D.to_str(t), "F": F})
        kept_panels.append(pr)
        if len(kept) >= 6: break
    return kept, len([1 for v in seen.values() if v[0] > -9])


# ---------------------------------------------------------------- 第三代：大模型提出“有机制的”公式
LLM_SYSTEM = """你是量化研究员。请基于【行为金融 / 风险溢价 / 流动性 / 信息扩散】中的明确经济学机制，提出可检验的横截面选股因子，用给定的表达式语言写成公式。
表达式语言：字段 ret(日收益) lvol(对数成交量) rng((最高-最低)/收盘)；一元 neg abs cs_rank；带窗口 ts_mean ts_std ts_sum ts_max ts_min ts_delta ts_zscore（第二个参数是窗口）；
ts_corr(x,y,w)；二元 add sub mul div。窗口只能取 5,10,20,40,60,120。表达式最多 14 个节点。
要求：每个因子必须写清机制（为什么该因子“值越大，未来收益应越高”）；不要提出与“简单动量/反转/波动率”几乎相同的因子；不要给出历史业绩，你没有数据。
只输出 JSON 数组：[{"name":"...","mechanism":"...","expr":"..."}]，8 个。"""


def llm_propose(n: int = 8) -> list[dict]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return []
    base = os.environ.get("RESEARCH_API_BASE", "https://api.anthropic.com").rstrip("/")
    body = {"model": os.environ.get("RESEARCH_MODEL", "claude-sonnet-5-5"), "max_tokens": 2000, "temperature": 0.7,
            "system": LLM_SYSTEM, "messages": [{"role": "user", "content": f"请提出 {n} 个互不相似的因子。"}]}
    try:
        r = requests.post(f"{base}/v1/messages", json=body, timeout=90, headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
        if r.status_code != 200: return []
        txt = re.sub(r"^```(?:json)?|```$", "", "".join(b.get("text", "") for b in r.json()["content"]).strip(), flags=re.M).strip()
        out = []
        for it in json.loads(txt):
            try:
                t = D.parse(str(it["expr"]))
                out.append({"name": str(it["name"])[:30], "mech": str(it["mechanism"])[:200], "expr": D.to_str(t), "tree": t})
            except Exception:
                continue                                              # 语法/窗口不合法的直接丢弃，不修补
        return out
    except Exception:
        return []


# ---------------------------------------------------------------- 打包
def _b64(a: np.ndarray) -> str:
    return base64.b64encode(a.tobytes()).decode()


def rank_matrix(F: pd.DataFrame, ctx: E.Ctx) -> str:
    """月末横截面百分位排名 0~99（uint8），缺失=255；给前端交互回测用。"""
    R = F.loc[ctx.rb, ctx.tickers].rank(axis=1, pct=True)
    a = np.where(R.isna().values, 255, np.minimum((R.values * 100).astype(float), 99)).astype(np.uint8)
    return _b64(a)


def neutral_stats(F: pd.DataFrame, ctx: E.Ctx, sec: pd.Series) -> dict:
    Fr = F.loc[ctx.rb, ctx.tickers]
    Fn = Fr - Fr.T.groupby(sec.reindex(ctx.tickers).values).transform("mean").T
    ic = E.ic_series(Fn, ctx)
    a, (i, o) = E.ic_stats(ic), E.is_oos(ic, ctx)
    ii, oo = E.ic_stats(i), E.ic_stats(o)
    return {"ic": a["ic"], "t": a["t"], "is_icir": ii["icir"], "oos_icir": oo["icir"], "oos_t": oo["t"]}


def _row(fid, name, src, fam, mech, expr, rep, F, ctx, sec):
    rep["neutral"] = neutral_stats(F, ctx, sec)
    return {"id": fid, "name": name, "src": src, "family": fam, "mech": mech, "expr": expr, "ranks": rank_matrix(F, ctx), **rep}


def run(refresh: bool = False, max_age_h: float = 24, log=print) -> dict:
    if CACHE.exists() and not refresh and time.time() - CACHE.stat().st_mtime < max_age_h * 3600:
        return json.loads(CACHE.read_text("utf-8"))
    t0 = time.time()
    log("      [量化] 行情面板 ..."); P = quant_data.load_panel()
    ctx, X = E.Ctx(P), D.make_fields(P)
    log(f"      [量化] 行业分类 ({len(ctx.tickers)} 只) ..."); sec = pd.Series(quant_data.sectors(ctx.tickers))
    rows, panels = [], {}

    def add(fid, name, src, fam, mech, expr, F):
        rep = E.full_report(F, ctx)
        if rep:
            rows.append(_row(fid, name, src, fam, mech, expr, rep, F, ctx, sec)); panels[fid] = F

    hand = _hand(P, X)
    for fid, (F, name, fam, mech) in hand.items():
        add(fid, name, "手工", fam, mech, None, F)

    log("      [量化] 基本面财报 (SEC，首次较慢) ...")
    fd = quant_fund.prefetch(ctx.tickers)
    log(f"      [量化] 财报可用 {sum(1 for v in fd.values() if v)}/{len(fd)} 只")
    for fid, F in quant_fund.fund_factors(P, fd).items():
        name, fam, mech = FUND[fid]
        add(fid, name, "手工", fam, mech, None, F)

    log("      [量化] 遗传规划搜索 ...")
    price_only = {k: v for k, v in hand.items()}
    kept, n_tried = gp_mine(ctx, X, price_only)
    for i, k in enumerate(kept, 1):
        add(f"gp{i}", f"遗传因子 G{i}", "遗传规划", "遗传", "无先验机制：由搜索发现，解释性待定", k["expr"], k["F"])

    for i, c in enumerate(llm_propose(), 1):
        add(f"llm{i}", c["name"], "LLM假设", "LLM", c["mech"], c["expr"], D.evaluate(c["tree"], X))

    pick = [r["id"] for r in rows if (r["stats"]["is"]["icir"] or 0) > 0.15]
    comp = None
    if len(pick) >= 2:
        Fc = sum(panels[i].rank(axis=1, pct=True) for i in pick) / len(pick)
        rep = E.full_report(Fc, ctx)
        if rep:
            comp = _row("composite", f"多因子合成（样本内选入 {len(pick)} 个）", "合成", "合成",
                        "等权横截面分位平均；入选规则只看样本内 ICIR>0.15，不看样本外", None, rep, Fc, ctx, sec)
            comp["members"] = pick

    n_is = int(sum(1 for d in ctx.dates if ctx.cutoff > d >= P["adj"].index[260]))
    null_max = round(math.sqrt(2 * math.log(max(n_tried, 2))) / math.sqrt(max(n_is, 1)), 3)
    cut_i = next(i for i, d in enumerate(ctx.rb) if d >= ctx.cutoff)
    fwd = ctx.fwd.loc[ctx.rb].values
    fwd16 = np.where(np.isnan(fwd), -32768, np.clip(np.round(fwd * 1e4), -32000, 32000)).astype("<i2")
    cnt = sec.reindex(ctx.tickers).value_counts().to_dict()
    out = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M"), "universe": len(ctx.tickers), "missing": P.get("_missing", []),
                    "start": str(P["adj"].index[0].date()), "end": str(P["adj"].index[-1].date()), "cutoff": ctx.cutoff.strftime("%Y-%m"),
                    "months": len(ctx.dates), "cost_bps": E.COST_BPS, "n_tried": n_tried, "null_max_icir": null_max, "n_is": n_is,
                    "llm": bool(os.environ.get("ANTHROPIC_API_KEY")), "elapsed_s": round(time.time() - t0, 1),
                    "fund_covered": sum(1 for v in fd.values() if v), "sectors": cnt, "families": FAMILY_ORDER},
           "pack": {"dates": [d.strftime("%Y-%m") for d in ctx.rb], "tickers": ctx.tickers, "cut": cut_i, "n": len(ctx.tickers),
                    "fwd": _b64(fwd16), "spy": [None if np.isnan(x) else round(float(x), 4) for x in ctx.spy_fwd.loc[ctx.rb].values],
                    "bull": [int(bool(x)) for x in ctx.bull.reindex(ctx.rb).fillna(False).values], "min_n": E.MIN_N},
           "factors": rows, "composite": comp}
    CACHE.write_text(json.dumps(out, ensure_ascii=False), "utf-8")
    return out

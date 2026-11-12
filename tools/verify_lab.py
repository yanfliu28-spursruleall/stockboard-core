"""交叉验证“回测实验室”：把前端 labRun 的算法逐行移植到 Python，直接吃打包后的 base64 数据（即页面里的数据），
与 quant_eval.backtest（pandas 实现，用原始浮点因子值和 qcut 分组）的结果对比。
这能验证：打包/解码是否正确、实验室算法是否与主评估一致。局限：不能执行 JS 本身（本机无 Node），JS 与此移植需人工对读。"""
import base64, json, math, sys
from pathlib import Path
import numpy as np

root = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")
q = json.loads((root / "cache" / "quant2.json").read_text("utf-8"))
pk = q["pack"]
N, M = pk["n"], len(pk["dates"])
fwd = np.frombuffer(base64.b64decode(pk["fwd"]), dtype="<i2").reshape(M, N)


def lab_run(ranks_b64, nq=5, hold=1, bps=10, mode="ls"):
    rk = np.frombuffer(base64.b64decode(ranks_b64), dtype=np.uint8).reshape(M, N)
    c = bps / 1e4
    cur, prevT, prevB, k = None, None, None, 0
    ls, topn, ew, split = [], [], [], 0
    for m in range(M):
        if pk["spy"][m] is None: continue
        valid = rk[m] != 255
        if valid.sum() < pk["min_n"]: continue
        reb = (k % hold == 0) or cur is None
        tT = tB = 0.0
        if reb:
            cur = np.full(N, -1)
            cur[valid] = np.minimum(nq - 1, (rk[m][valid].astype(int) * nq) // 100)
            T, B = set(np.where(cur == nq - 1)[0]), set(np.where(cur == 0)[0])
            ov = lambda A, Z: len(A & Z) / len(A) if Z else 0
            tT = 1 - ov(T, prevT) if prevT else 1.0
            tB = 1 - ov(B, prevB) if prevB else 1.0
            prevT, prevB = T, B
        f = fwd[m]
        ok = valid & (f != -32768)
        if ok.sum() < pk["min_n"]: continue
        x = f / 1e4
        qr = [x[ok & (cur == g)].mean() if (ok & (cur == g)).any() else 0.0 for g in range(nq)]
        costT, costL = (2 * c * tT, 2 * c * (tT + tB)) if reb else (0, 0)
        ew.append(x[ok].mean()); topn.append(qr[-1] - costT); ls.append(qr[-1] - qr[0] - costL)
        if m < pk["cut"]: split += 1
        k += 1
    return np.array(ls if mode == "ls" else topn), np.array(ew), split


def ann(r):
    return float(np.prod(1 + r) ** (12 / len(r)) - 1)


print(f"{'因子':24}{'实验室 多空净年化':>16}{'pandas 多空净年化':>18}{'差':>8}{'实验室 月数':>10}{'pandas 月数':>12}")
worst = 0
for r in q["factors"][:: max(1, len(q['factors']) // 10)] + [q["composite"]]:
    if not r: continue
    ls, ew, sp = lab_run(r["ranks"])
    ref = r["bt"]["perf"]["ls_net"]["ann"]
    d = ann(ls) - ref
    worst = max(worst, abs(d))
    print(f"{r['name'][:22]:24}{ann(ls)*100:15.2f}%{ref*100:17.2f}%{d*100:7.2f}pp{len(ls):10}{len(r['bt']['dates']):12}")
print(f"\n最大偏差 {worst*100:.2f} 个百分点。偏差来源：前端用 0~99 的百分位整数做分组（分位边界有约 ±1% 的量化），pandas 用精确 qcut；")
print("若偏差在 1~2pp 内且月数一致，说明打包与算法一致；若大于此或月数不同，需要排查。")

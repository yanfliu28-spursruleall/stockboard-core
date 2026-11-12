"""单独运行量化研究并打印摘要（不经过 build.py），用于调试/验收。  python tools/run_quant.py [--refresh]"""
import sys, time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.stdout.reconfigure(encoding="utf-8")
from core import quant

t0 = time.time()
q = quant.run(refresh="--refresh" in sys.argv)
m = q["meta"]
print(f"用时 {time.time()-t0:.0f}s | 股票池 {m['universe']} 只(缺 {len(m['missing'])}: {m['missing'][:6]}) | {m['start']}~{m['end']} | 月数 {m['months']} | 切分日 {m['cutoff']}")
print(f"GP 评估过的有效候选: {m['n_tried']} | 纯噪声下最优 ICIR 期望≈{m['null_max_icir']} | LLM: {m['llm']}")
print(f"{'因子':26}{'类别':6}{'IC(全)':>8}{'t':>6}{'ICIR_IS':>9}{'ICIR_OOS':>9}{'多空净年化':>10}{'Sharpe':>7}{'MDD':>7}{'换手':>6}  判定")
for r in q["factors"] + ([q["composite"]] if q["composite"] else []):
    s, b = r["stats"], r["bt"]
    p = b["perf"]["ls_net"]
    print(f"{r['name'][:24]:26}{r['family']:6}{s['all']['ic'] or 0:8.3f}{s['all']['t'] or 0:6.1f}{s['is']['icir'] or 0:9.2f}{s['oos']['icir'] or 0:9.2f}"
          f"{(p['ann'] or 0)*100:9.1f}%{p['sharpe'] or 0:7.2f}{(p['mdd'] or 0)*100:6.0f}%{b['turnover_top'] or 0:6.2f}  {r['tag']} | 行业中性OOS {r['neutral']['oos_icir']}")
for r in q["factors"]:
    if r["expr"]: print("  ", r["id"], r["expr"])

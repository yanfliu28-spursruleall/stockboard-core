"""一键构建：抓取 -> 打分 -> 预警/内参 -> 渲染 -> (可选)加密 -> dist/

用法:
  python build.py --demo                 # 离线演示数据，验证页面
  python build.py                        # 实时抓取，输出明文 dist/index.html
  set BOARD_PASSPHRASE=xxx && python build.py   # 同上，但输出口令门 + 加密 app.bin
  python build.py --skip-13f             # 跳过 13F（较慢，一般每天跑一次即可）
  python build.py --auto --out dist_new --require-encrypt   # 定时任务用：13F 超过 7 天才重算；输出到临时目录；没口令就拒绝构建
"""
import argparse, json, os, sys, time
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except Exception: pass
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import config
from core import market, news as newsmod, alerts_brief, holdings as hmod, crypto_gate, demo, valuation, narrative, quant

CACHE, DIST = ROOT / "cache", ROOT / "dist"
CACHE.mkdir(exist_ok=True); DIST.mkdir(exist_ok=True)


def _load(name, default):
    p = CACHE / name
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--skip-13f", action="store_true")
    ap.add_argument("--skip-quant", action="store_true", help="跳过量化因子研究（读缓存或不展示）")
    ap.add_argument("--refresh-quant", action="store_true", help="强制重算量化研究（约 1 分钟，默认缓存 24 小时）")
    ap.add_argument("--auto", action="store_true", help="定时模式：13F 缓存超过 7 天才重算，否则复用")
    ap.add_argument("--out", default="", help="输出目录（默认 dist/）；定时任务先输出到临时目录，校验通过后再替换 dist/")
    ap.add_argument("--require-encrypt", action="store_true", help="没有设置 BOARD_PASSPHRASE 时直接失败，绝不输出明文页面")
    a = ap.parse_args()
    t0 = time.time()
    out_dir = Path(a.out) if a.out else DIST
    if a.require_encrypt and not os.environ.get(config.PASSPHRASE_ENV, ""):
        sys.exit("拒绝构建：--require-encrypt 要求设置环境变量 BOARD_PASSPHRASE（避免误输出明文页面）。")
    if a.auto and not a.skip_13f:
        hp = CACHE / "holdings.json"
        a.skip_13f = hp.exists() and time.time() - hp.stat().st_mtime < 7 * 86400
        print(f"      13F 缓存{'新鲜，复用' if a.skip_13f else '已过期或不存在，重新抓取（约 6 分钟）'}")

    if a.demo:
        hist, raw, stat, hold = demo.demo_hist(), demo.demo_news(), {"演示": 15}, demo.demo_holdings()
    else:
        tick = [c["t"] for c in config.WATCHLIST] + list(set(config.SECTOR_ETFS.values())) + [config.BENCHMARK]
        print(f"[1/4] 行情 {len(tick)} 个标的 ..."); hist = market.fetch_many(tick)
        print("[2/4] 新闻 ..."); raw, stat = newsmod.fetch_all_news()
        if a.skip_13f:
            hold = _load("holdings.json", {"funds": [], "overlap": [], "note": "13F 本次未更新"})
        else:
            print("[3/4] 13F ..."); hold = hmod.build_holdings()
            (CACHE / "holdings.json").write_text(json.dumps(hold, ensure_ascii=False), "utf-8")
        ok = sum(1 for v in hist.values() if v)
        print(f"      行情成功 {ok}/{len(hist)}，新闻原始 {len(raw)} 条，13F 机构 {len(hold['funds'])} 家")
        if ok == 0 and not raw:
            sys.exit("所有数据源均失败：请检查网络；可先用 --demo 验证页面。")

    if a.demo:
        wl = config.WATCHLIST
    else:
        print("      估值：SEC 财报 -> Graham/DCF/Lynch ..."); wl = valuation.apply(config.WATCHLIST, hist)
        print("      自动估值", sum(1 for w in wl if w["val_src"].startswith("自动")), "家，回退目标价/手工", sum(1 for w in wl if not w["val_src"].startswith("自动")), "家")
    board = market.build_board(hist, wl)
    sectors = market.sector_prosperity(hist)
    news = newsmod.process(raw)
    prev = _load("zones.json", {})
    alerts = alerts_brief.build_alerts(board, news, prev)
    (CACHE / "zones.json").write_text(json.dumps({r["t"]: r["zone"] for r in board}, ensure_ascii=False), "utf-8")
    prev_alerts = set(_load("alert_ids.json", []))
    cur_ids = [f'{x["t"]}|{x["type"]}|{x["msg"][:40]}' for x in alerts]
    new_alerts = sum(1 for i in cur_ids if i not in prev_alerts)
    (CACHE / "alert_ids.json").write_text(json.dumps(cur_ids, ensure_ascii=False), "utf-8")
    narrative.enrich(board, news, hold["funds"], alerts)
    briefs = [alerts_brief.build_brief(k, board, sectors, news, alerts) for k in ("pre", "post")]

    data = {"meta": {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "demo": a.demo, "news_stat": stat, "new_alerts": new_alerts,
                                                "health": {"auto": sum(1 for r in board if r.get("val_src","").startswith("自动")), "low_conf": sum(1 for r in board if r.get("val_conf") == "低"), "total": len(board)}},
            "board": board, "sectors": sectors, "news": news[:400], "alerts": alerts, "holdings": hold, "briefs": briefs}
    if not a.demo and not a.skip_quant:
        print("      量化因子研究 ..."); data["quant"] = quant.run(refresh=a.refresh_quant)
        print(f"      量化：{len(data['quant']['factors'])} 个因子，GP 评估 {data['quant']['meta']['n_tried']} 个候选")
    elif (CACHE / "quant2.json").exists():
        data["quant"] = json.loads((CACHE / "quant2.json").read_text("utf-8"))
    payload = json.dumps(data, ensure_ascii=False, default=lambda o: sorted(o) if isinstance(o, set) else str(o)).replace("</", "<\\/")
    page = (ROOT / "templates" / "app.html").read_text("utf-8").replace("__DATA__", payload)

    pw = os.environ.get(config.PASSPHRASE_ENV, "")
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("*"):
        if f.is_file():
            f.unlink()
    if pw:
        salt = crypto_gate.stable_salt(ROOT / "secrets" / "salt.bin")   # 固定盐：已保存的登录状态才不会在每次定时刷新后失效
        (out_dir / "app.bin").write_bytes(crypto_gate.encrypt(page, pw, config.PBKDF2_ITER, salt))
        (out_dir / "index.html").write_text(crypto_gate.gate_html(salt, config.PBKDF2_ITER), "utf-8")
        mode = "加密(口令门)"
    else:
        (out_dir / "index.html").write_text(page, "utf-8")
        mode = "明文(未设置 BOARD_PASSPHRASE，仅限本地预览，请勿公网发布)"
    print(f"[4/4] 完成 {time.time()-t0:.1f}s · 公司 {len(board)} · 行业 {len(sectors)} · 情报 {len(news)} · 预警 {len(alerts)} · 输出 {mode}\n      -> {out_dir/'index.html'}")


if __name__ == "__main__":
    main()

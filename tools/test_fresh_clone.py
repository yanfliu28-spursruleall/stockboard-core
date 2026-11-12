"""模拟“陌生人克隆仓库”：在临时目录克隆本地提交，不带 secrets/缓存/观察名单，检查能否构建与运行。"""
import os, py_compile, re, shutil, subprocess, sys, tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SRC = sys.argv[1]
tmp = Path(tempfile.mkdtemp(prefix="usboard_clone_"))
repo = tmp / "clone"
try:
    subprocess.run(["git", "clone", "-q", SRC, str(repo)], check=True)
    files = sorted(p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts)
    print("克隆得到文件:", len(files))
    print("不应存在 secrets/cache/dist/watchlist_local:", [n for n in ("secrets", "cache", "dist", "watchlist_local.py") if (repo / n).exists()] or "均不存在 ✓")
    bad = []
    for f in repo.rglob("*.py"):
        try: py_compile.compile(str(f), doraise=True)
        except Exception as e: bad.append((f.name, str(e)[:80]))
    print("全部 .py 语法编译:", "通过 ✓" if not bad else bad)
    r = subprocess.run([sys.executable, "build.py", "--demo"], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={k: v for k, v in os.environ.items() if k not in ("BOARD_PASSPHRASE", "ANTHROPIC_API_KEY")})
    print("演示构建退出码:", r.returncode)
    print("\n".join((r.stdout or "").strip().splitlines()[-2:]))
    if r.returncode:
        print((r.stderr or "")[-600:])
    for tname in ("test_junk.py", "test_broker.py"):                      # 仓库自带的单测，在干净克隆里也应通过
        if (repo / "tools" / tname).exists():
            tr = subprocess.run([sys.executable, str(repo / "tools" / tname)], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
            last = ((tr.stdout or "").strip().splitlines() or ["(无输出)"])[-1]
            print(f"单测 {tname}: 退出码 {tr.returncode} | {last}")
    out = repo / "dist" / "index.html"
    if out.exists():
        h = out.read_text("utf-8")
        tick = re.findall(r'"t": ?"([A-Z.\-]+)", ?"name"', h)
        print("演示页面生成:", f"{len(h)//1024} KB", "| 观察名单(应为示例4只):", tick[:6])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

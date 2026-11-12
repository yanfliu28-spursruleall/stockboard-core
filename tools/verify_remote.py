"""从线上真实克隆公开仓库，核对：文件清单、有无敏感文件、有无口令/盐真实值。用法: python tools/verify_remote.py <仓库URL>"""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
url = sys.argv[1]
ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="usboard_verify_"))
try:
    r = subprocess.run(["git", "clone", "-q", url, str(tmp / "c")], capture_output=True, text=True, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    print("clone 退出码", r.returncode)
    if r.returncode:
        sys.exit("克隆失败：" + r.stderr[-300:])
    c = tmp / "c"
    files = sorted(p.relative_to(c).as_posix() for p in c.rglob("*") if p.is_file() and ".git" not in p.parts)
    print("线上克隆文件数:", len(files))
    bad = [p for p in files if p.startswith(("secrets/", "cache/", "dist", "logs/", "status/")) or p in ("watchlist_local.py", "publish_hook.cmd")]
    print("不应出现的文件:", bad or "无 ✓")
    pw = (ROOT / "secrets" / "passphrase.txt").read_text().strip()
    salt = (ROOT / "secrets" / "salt.bin").read_bytes().hex()
    leak = [p for p in files if pw in (c / p).read_text("utf-8", "replace") or salt in (c / p).read_text("utf-8", "replace")]
    print("线上文件含口令/盐真实值:", leak or "无 ✓")
    print("关键文件在线:", [p for p in ("README.md", "config.py", "watchlist_example.py", "requirements.txt", "core/quant.py", "templates/app.html") if p in files])
    print("线上最新提交:", subprocess.run(["git", "-C", str(c), "log", "-1", "--format=%h %an <%ae>"], capture_output=True, text=True).stdout.strip())
finally:
    shutil.rmtree(tmp, ignore_errors=True)

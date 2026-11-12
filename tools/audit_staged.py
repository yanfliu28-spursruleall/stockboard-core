"""推送前审计：检查将提交的全部文件里是否有 口令/盐真实值、本机路径、疑似密钥、控制字符、不该出现的目录。
用法:  python tools/audit_staged.py
  · 若 git 目录不在项目内（--separate-git-dir），设置环境变量 USBOARD_GITDIR 指向它；否则用当前目录的仓库。
  · 个人相关的“禁止出现”正则（用户名、无关仓库名等）写在本地文件 secrets/audit_deny.txt（每行一个正则，已被 .gitignore 忽略）。"""
import os, re, subprocess, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W = Path(__file__).resolve().parent.parent
GD = os.environ.get("USBOARD_GITDIR")


def git(*a):
    base = ["git", f"--git-dir={GD}", f"--work-tree={W}"] if GD else ["git", "-C", str(W)]
    return subprocess.run(base + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


files = [f for f in git("ls-files").split("\n") if f]
staged = [l for l in git("diff", "--cached", "--name-status").split("\n") if l]
print(f"将提交的文件总数 {len(files)} | 本次暂存改动 {len(staged)} 项")
for l in staged:
    print("  ", l)

secrets = []
for name in ("secrets/passphrase.txt",):
    p = W / name
    if p.exists():
        secrets.append(p.read_text().strip())
sp = W / "secrets/salt.bin"
if sp.exists():
    secrets.append(sp.read_bytes().hex())

PATS = {"本机路径": r"[A-Za-z]:[\\/](Users|html|home)\b", "疑似密钥": r"(sk-[A-Za-z0-9]{16,}|api[_-]?key\s*=\s*[\"'][^\"']{8,}|token\s*=\s*[\"'][^\"']{8,})"}
deny = W / "secrets/audit_deny.txt"
if deny.exists():
    for i, line in enumerate(deny.read_text(encoding="utf-8").splitlines()):
        if line.strip():
            PATS[f"个人禁止项#{i + 1}"] = line.strip()

bad = []
for f in files:
    p = W / f
    if not p.exists() or p.suffix in (".png", ".ico"):
        continue
    t = p.read_text(encoding="utf-8", errors="replace")
    if any(s and s in t for s in secrets):
        bad.append(("!!口令/盐真实值", f))
    for name, pat in PATS.items():
        if re.search(pat, t):
            bad.append((name, f))
    if any(ord(c) < 32 and c not in "\n\r\t" for c in t):
        bad.append(("控制字符", f))
forbidden = [f for f in files if f.startswith(("secrets/", "cache/", "dist", "logs/", "status/")) or f in ("watchlist_local.py", "publish_hook.cmd")]
print("\n不该出现的文件:", forbidden or "无 ✓")
print("审计问题:", bad or "无 ✓", f"（检查项 {len(PATS)} 个 + 口令/盐 + 控制字符）")
sys.exit(1 if (bad or forbidden) else 0)

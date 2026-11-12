"""扫描源码里的不可见控制字符（如被转义误写进去的退格符 0x08）。用法: python tools/scan_ctrl.py"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
root = Path(__file__).resolve().parent.parent
bad = 0
for f in sorted(list(root.glob("*.py")) + list(root.glob("core/*.py")) + list(root.glob("tools/*.py")) + [root / "templates" / "app.html"]):
    s = f.read_text(encoding="utf-8")
    ctrl = [(i, hex(ord(c))) for i, c in enumerate(s) if ord(c) < 32 and c not in "\n\r\t"]
    if ctrl:
        bad += 1
        line = s[:ctrl[0][0]].count("\n") + 1
        print(f"{f.relative_to(root)}: {len(ctrl)} 个控制字符 {set(c for _, c in ctrl)}，首个在第 {line} 行")
print("扫描完成：", "发现问题文件 %d 个" % bad if bad else "未发现控制字符 ✓")

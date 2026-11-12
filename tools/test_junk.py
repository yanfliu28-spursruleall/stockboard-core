"""垃圾稿件识别的单测：自动生成的机构持仓流水账应被降权，真正的事件不能被误伤。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from core import news


def t(title):
    it = {"title": title, "summary": "", "url": "u", "source": "x", "ts": time.time(), "dup": 1, "sources": ["x"]}
    r = news.score(it)
    return r["junk"], r["grade"], r["imp"]


cases = [
    ("Advanced Micro Devices, Inc. $AMD Stock Acquired by Artemis Wealth Advisors LLC - MarketBeat", True),
    ("NVIDIA shares sold by Smith Capital Management LLC", True),
    ("Wells Fargo & Company MN Buys New Stake in Micron Technology, Inc. $MU", True),
    ("Micron Stock Surges 13% as CoreWeave Raises 2026 Revenue Outlook", False),
    ("Oracle raises guidance after record cloud bookings", False),
    ("Apple files 8-K: completes acquisition of AI startup for $1.2 billion", False),
    ("Nvidia to acquire stake in Intel for $5 billion", False),
    ("Berkshire cuts Apple position by 15% in latest 13F", False),
]
ok = True
for title, want in cases:
    j, g, i = t(title)
    good = j == want
    ok &= good
    print(("OK " if good else "BAD"), f"junk={j!s:5} expect={want!s:5} grade={g} imp={i:5}  {title[:66]}")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)

"""券商作为“信息来源”出现的新闻不应算作该券商股票的新闻；作为主角的应保留。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import config
from core import news

news.set_universe(list(config.WATCHLIST) + [{"t": "GS", "name": "Goldman Sachs Group, Inc."}, {"t": "MS", "name": "Morgan Stanley"}])

cases = [  # (标题, 是否应归属 JPM)
    ("Tata Power Share Price: Citi sees 45% upside, but why are JP Morgan & CLSA cautious after Q2 results?", False),
    ("REG - iShares II JPM$ EM $ - Dividend Declaration", False),
    ("JPMorgan Names Dividend Giant Its Top Stock Pick for a Rocky Market", False),
    ("RIOT Stock Gains After Jefferies Upgrade, JP Morgan Price Target Hike", False),
    ("JPMorgan Names Top 3 Picks Among Auto Parts Stocks", False),
    ("JPMorgan Chase (JPM) Takes Lead Role In A Major AI IPO", True),
    ("JPMorgan to cut 1,000 jobs in investment banking restructuring", True),
    ("JPMorgan reports record Q3 earnings, raises net interest income outlook", True),
    ("JPMorgan Chase fined $200 million over reporting failures", True),
    ("SEC charges JPMorgan Chase subsidiary", True),
]
ok = True
for title, want in cases:
    it = {"title": title, "summary": "", "url": "u", "source": "x", "ts": time.time(), "dup": 1, "sources": ["x"]}
    got = "JPM" in news.score(it)["tickers"]
    ok &= got == want
    print("OK " if got == want else "BAD", f"JPM归属={got!s:5} 期望={want!s:5}  {title[:72]}")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)

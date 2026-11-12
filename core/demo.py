"""离线演示数据（--demo）：随机游走行情 + 示例新闻 + 示例 13F，用于验证页面与全部逻辑，无需联网。"""
import random, time
import config


def _walk(start, n=252, vol=0.02, seed=0):
    rnd = random.Random(seed)
    p, out = start, []
    for _ in range(n):
        p *= 1 + rnd.gauss(0.0004, vol)
        out.append(p)
    return out


def demo_hist() -> dict:
    now = int(time.time())
    hist = {}
    tickers = [(c["t"], c["fair"]) for c in config.WATCHLIST] + [(e, 100) for e in set(config.SECTOR_ETFS.values())] + [(config.BENCHMARK, 500)]
    for i, (t, ref) in enumerate(tickers):
        close = _walk(ref * random.Random(i).uniform(0.7, 1.2), seed=i, vol=0.018 if t in {c["t"] for c in config.WATCHLIST} else 0.01)
        ts = [now - (len(close) - k) * 86400 for k in range(len(close))]
        hist[t] = {"ts": ts, "close": close, "last": close[-1], "prev": close[-2], "currency": "USD"}
    return hist


def demo_news() -> list[dict]:
    now = int(time.time())
    t = [
        ("NVIDIA reports record data center revenue, raises guidance on Blackwell demand", "CNBC Markets"),
        ("Fed officials signal rate cut possible in December as inflation cools", "MarketWatch"),
        ("Broadcom announces $10 billion buyback and custom AI chip order from hyperscaler", "Yahoo Finance"),
        ("Tesla deliveries miss estimates as price war weighs; stock slumps", "Investing.com"),
        ("Apple files 8-K: completes acquisition of AI startup for $1.2 billion", "SEC 8-K"),
        ("Why Palantir is surging: 5 stocks to buy now, says Motley Fool", "Seeking Alpha"),
        ("Reportedly, Microsoft may consider new data center deal with OpenAI?", "Yahoo Finance"),
        ("Eli Lilly receives FDA approval for oral obesity drug", "CNBC Markets"),
        ("Stocks CRASH in bloodbath as panic grips Wall Street", "Investing.com"),
        ("JPMorgan beats earnings estimates, raises net interest income outlook", "MarketWatch"),
        ("US announces new export controls on advanced chips to China; ASML and TSMC fall", "CNBC Tech"),
        ("Coinbase launches stablecoin payments with Visa partnership", "Yahoo Finance"),
        ("Meta plans layoffs in Reality Labs, restructuring continues", "Google News 美股"),
        ("Oil climbs as OPEC extends production cuts, Exxon gains", "Google News 美股"),
        ("Amazon AWS wins $4 billion contract; downgrade from analyst on valuation", "CNBC Tech"),
    ]
    return [{"title": s, "summary": "", "url": "https://example.com/" + str(i), "source": src,
             "ts": now - i * 2700} for i, (s, src) in enumerate(t)]


def demo_holdings() -> dict:
    def fund(name, typ, rows):
        total = sum(r[1] for r in rows)
        top = [{"issuer": n, "value": v, "pct": v / total, "shares": v / 100, "act": a, "chg": c} for n, v, a, c in rows]
        return {"fund": name, "type": typ, "period": "2026-09-30", "filed": "2026-11-10", "total": total, "n": len(rows),
                "top": top, "moves": [r for r in top if r["act"] != "持平"]}
    f = [fund("Berkshire Hathaway", "价值/长线", [("APPLE INC", 6e10, "减仓", -0.15), ("BANK AMER CORP", 3e10, "持平", 0), ("CHEVRON CORP", 1.6e10, "加仓", 0.1)]),
         fund("Pershing Square", "激进/集中", [("ALPHABET INC", 2e9, "加仓", 0.2), ("UBER TECHNOLOGIES", 1.5e9, "新进", None), ("APPLE INC", 1e9, "新进", None)]),
         fund("Citadel Advisors", "多策略", [("NVIDIA CORP", 4e9, "加仓", 0.3), ("APPLE INC", 3e9, "减仓", -0.1), ("ALPHABET INC", 2e9, "持平", 0)])]
    from core.holdings import overlap
    return {"funds": f, "overlap": overlap(f), "note": "【演示数据】真实数据来自 SEC 13F-HR。"}

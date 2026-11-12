"""预警日报 + 盘前/盘后内参（对应 A 股版的预警信号与 早参/晚参）。"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _why(news: list[dict], t: str):
    """该标的 48 小时内重要性最高的一条情报，用来解释“为什么异动”。"""
    best = None
    for n in news:
        if t in n["tickers"] and not n["excluded"] and n["age_h"] <= 48 and not n.get("junk") and (best is None or n["imp"] > best["imp"]):
            best = n
    return best


def build_alerts(board: list[dict], news: list[dict], prev_zones: dict[str, str] | None = None) -> list[dict]:
    """预警类型：区间切换 / 大幅异动 / 逼近52周高低 / S-A 级新闻命中自选"""
    prev_zones = prev_zones or {}
    out = []
    for r in board:
        if r.get("price") is None:
            continue
        t = r["t"]
        if prev_zones.get(t) and prev_zones[t] != r["zone"] and "存疑" not in (prev_zones[t], r["zone"]):
            out.append({"t": t, "type": "区间切换", "level": "高", "msg": f'{prev_zones[t]} → {r["zone"]}'})
        if r["zone"] == "买点区":
            out.append({"t": t, "type": "进入买点区", "level": "中", "msg": f'现价 {r["price"]} ≤ 买点 {r["buy"]}×1.05'})
        if abs(r["chg"]) >= 0.05:
            w = _why(news, t)
            out.append({"t": t, "type": "大幅异动", "level": "高" if abs(r["chg"]) >= 0.08 else "中",
                        "msg": f'单日 {r["chg"]*100:+.1f}%' + (f' · 相关情报：{w["title"][:90]}' if w else ' · 48h 内无匹配情报（可能是板块/宏观驱动）'),
                        "url": w["url"] if w else None})
        if r["from_hi"] >= -0.02:
            out.append({"t": t, "type": "逼近52周高点", "level": "低", "msg": f'距高点 {r["from_hi"]*100:.1f}%'})
        if r["price"] <= r["lo52"] * 1.03:
            out.append({"t": t, "type": "逼近52周低点", "level": "中", "msg": f'低点 {r["lo52"]}'})
    for n in news:
        if n["grade"] in ("S", "A") and n["tickers"] and not n["excluded"]:
            for t in n["tickers"][:2]:
                out.append({"t": t, "type": f'{n["grade"]}级情报', "level": "高" if n["grade"] == "S" else "中",
                            "msg": n["title"][:90], "url": n["url"]})
    rank = {"高": 0, "中": 1, "低": 2}
    out.sort(key=lambda x: rank[x["level"]])
    return out


def window(kind: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """盘前: 上一交易日 16:00 ET → 当日 09:00 ET；盘后: 当日 09:00 → 16:30 ET。"""
    now = (now or datetime.now(ET)).astimezone(ET)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "pre":
        prev = day - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev.replace(hour=16), day.replace(hour=9)
    return day.replace(hour=9), day.replace(hour=16, minute=30)


def build_brief(kind: str, board: list[dict], sectors: list[dict], news: list[dict], alerts: list[dict]) -> dict:
    a, b = window(kind)
    ts_a, ts_b = a.timestamp(), b.timestamp()
    # 窗口内情报；窗口内不足则放宽到近 24h，避免空报告
    win = [n for n in news if ts_a <= n["ts"] <= ts_b and not n["excluded"]] or \
          [n for n in news if n["age_h"] <= 24 and not n["excluded"]]
    key = [n for n in win if n["grade"] in ("S", "A")][:12]
    movers = sorted([r for r in board if r.get("price")], key=lambda r: -abs(r["chg"]))[:6]
    topics: dict[str, int] = {}
    for n in win:
        for t in n["topics"]:
            topics[t] = topics.get(t, 0) + 1
    return {
        "kind": "盘前简报" if kind == "pre" else "盘后简报",
        "window": f'{a:%m-%d %H:%M} → {b:%m-%d %H:%M} ET',
        "generated": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "headline_stats": {"情报总数": len(win), "重点(S/A)": len(key),
                           "买点区": sum(r["zone"] == "买点区" for r in board),
                           "高预警": sum(x["level"] == "高" for x in alerts)},
        "key_news": key,
        "movers": [{"t": r["t"], "name": r["name"], "chg": r["chg"], "price": r["price"], "zone": r["zone"]} for r in movers],
        "top_sectors": sectors[:3], "bottom_sectors": sectors[-3:] if len(sectors) > 3 else [],
        "topics": sorted(topics.items(), key=lambda x: -x[1])[:6],
    }

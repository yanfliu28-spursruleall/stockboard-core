"""新闻抓取 + 三维打分(强度/确定性/新颖度) + S/A/B/C/D 分级 + 个股关联 + 去重。"""
from __future__ import annotations
import hashlib, re, time, html
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import requests

import config

UA = {"User-Agent": "Mozilla/5.0 (us-stock-board)"}

# ---- 关键词词表（可持续迭代；这就是"打分模型"，属于核心知识产权，建议只留后端） ----
STRONG_EVENTS = {  # 强度：事件级别 -> 分值
    r"\b(merger|acquir\w+|buyout|takeover|tender offer)\b": 30,
    r"\b(bankruptcy|chapter 11|delist\w*|fraud|sec charges?|doj|investigation|subpoena)\b": 30,
    r"\b(guidance|outlook|forecast)\b.*\b(raise[sd]?|cut|lower\w*|slash\w*|withdraw\w*)\b": 28,
    r"\b(raise[sd]?|cut[s]?|lower\w*|slash\w*|withdraw\w*)\b.*\b(guidance|outlook|forecast)\b": 28,
    r"\b(earnings|eps|revenue|quarterly results|q[1-4])\b": 22,
    r"\b(fda|approval|approves?|phase [123]|clinical)\b": 24,
    r"\b(tariff|sanction|export (control|ban|restriction)|ban)\b": 24,
    r"\b(fed|fomc|rate (cut|hike)|cpi|inflation|jobs report|payrolls)\b": 22,
    r"\b(buyback|repurchase|dividend|stock split)\b": 18,
    r"\b(upgrade[sd]?|downgrade[sd]?|price target|initiates?)\b": 14,
    r"\b(ipo|offering|secondary|convertible)\b": 18,
    r"\b(layoffs?|job cuts|restructur\w+|recall)\b": 18,
    r"\b(contract|partnership|deal|order)\b": 12,
}
CERTAIN_POS = [r"\b(announce[sd]?|reports?|files?|completes?|signed|confirms?|8-k|press release)\b",
               r"\$\s?\d[\d,.]*\s?(million|billion|bn|m|b)\b", r"\b\d+(\.\d+)?%"]
CERTAIN_NEG = [r"\b(rumou?rs?|reportedly|sources? say|could|may|might|speculat\w+|plans? to consider|eyes?)\b", r"\?"]
NOVEL_NEG = [r"\b(what to know|here'?s why|should you|is it too late|3 stocks|5 stocks|best stocks|why .* (is|are) (up|down))\b",
             r"\b(motley fool|zacks)\b"]
EMOTION = [r"\b(plunge[sd]?|soar(s|ed)?|crash\w*|skyrocket\w*|bloodbath|panic|surge[sd]?|meltdown)\b"]

BULL = r"\b(beat[s]?|raise[sd]?|surge[sd]?|rall(y|ies|ied)|upgrade[sd]?|record|approval|wins?|jumps?|gains?|soars?|buyback)\b"
BEAR = r"\b(miss(es|ed)?|cut[s]?|plunge[sd]?|falls?|fell|drops?|dropped|down|lower|retreat\w*|slides?|slid|tumbl\w+|sinks?|sank|scandal|hike[sd]?|downgrade[sd]?|probe|lawsuit|recall|layoffs?|slump\w*|warns?|ban|curbs?)\b"

TOPICS = {
    "AI算力": r"\b(ai|artificial intelligence|gpu|data ?center|nvidia|hbm|llm|openai|inference)\b",
    "半导体": r"\b(chip|semiconductor|foundry|tsmc|asml|wafer|euv)\b",
    "宏观/美联储": r"\b(fed|fomc|powell|inflation|cpi|treasury|yield|rate (cut|hike)|jobs)\b",
    "财报业绩": r"\b(earnings|eps|revenue|quarter|guidance|results)\b",
    "资本运作": r"\b(merger|acquisition|ipo|buyback|offering|spin-?off|dividend)\b",
    "监管/政策": r"\b(sec|doj|ftc|antitrust|tariff|sanction|regulat\w+|export control)\b",
    "医药": r"\b(fda|drug|trial|biotech|glp-1|obesity)\b",
    "能源": r"\b(oil|crude|opec|natural gas|lng)\b",
    "加密": r"\b(bitcoin|crypto|ethereum|stablecoin|coinbase)\b",
}

_TICKER_RE = {c["t"]: re.compile(r"(?<![A-Za-z])(\$?%s)(?![A-Za-z])" % re.escape(c["t"])) for c in config.WATCHLIST}
_ALIAS = {"LLY": "Lilly", "XOM": "Exxon", "GOOGL": "Google|Alphabet", "META": "Meta Platforms|Facebook", "V": "Visa",
          "TSM": "TSMC|Taiwan Semiconductor", "CRM": "Salesforce", "UNH": "UnitedHealth", "JPM": "JPMorgan|JP Morgan"}
_NAME_RE = {c["t"]: re.compile(r"\b(%s)\b" % _ALIAS.get(c["t"], re.escape(c["name"].split()[0])), re.I)
            for c in config.WATCHLIST if c["t"] in _ALIAS or len(c["name"].split()[0]) > 3}


def _any(pats, text): return any(re.search(p, text, re.I) for p in pats)


def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(xml_bytes: bytes, source: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall(".//item") or root.findall(".//a:entry", ns)
    for e in entries:
        def g(tag, atom_tag=None):
            x = e.find(tag)
            if x is None and atom_tag:
                x = e.find(atom_tag, ns)
            return x
        t = g("title", "a:title"); l = g("link", "a:link"); d = g("description", "a:summary"); p = g("pubDate", "a:updated")
        link = (l.get("href") if l is not None and l.get("href") else (l.text if l is not None else "")) or ""
        ts = None
        if p is not None and p.text:
            try:
                ts = parsedate_to_datetime(p.text)
            except Exception:
                try: ts = datetime.fromisoformat(p.text.replace("Z", "+00:00"))
                except Exception: ts = None
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        title = clean(t.text if t is not None else "")
        if not title:
            continue
        items.append({"title": title, "summary": clean(d.text if d is not None else "")[:400],
                      "url": link.strip(), "source": source,
                      "ts": int((ts or datetime.now(timezone.utc)).timestamp())})
    return items


def fetch_feed(src: dict) -> list[dict]:
    try:
        headers = dict(UA)
        if "sec.gov" in src["url"]:
            headers["User-Agent"] = config.SEC_USER_AGENT
        r = requests.get(src["url"], headers=headers, timeout=15)
        if r.status_code != 200:
            return []
        return parse_feed(r.content, src["name"])
    except Exception:
        return []


def fetch_all_news(workers: int = 8) -> tuple[list[dict], dict]:
    jobs = list(config.NEWS_SOURCES) + [
        {"name": "Google News · " + c["t"], "url": config.PER_TICKER_NEWS.format(q=c["t"])} for c in config.WATCHLIST]
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(fetch_feed, jobs))
    items, stat = [], {}
    for job, r in zip(jobs, res):
        key = job["name"].split(" · ")[0]
        stat[key] = stat.get(key, 0) + len(r)
        items += r
    return items, stat


def dedup(items: list[dict]) -> list[dict]:
    """标题归一化后去重；相似标题保留来源最多的一条并记录 sources 数（新颖度反向因子）。"""
    seen: dict[str, dict] = {}
    for it in sorted(items, key=lambda x: -x["ts"]):
        key = re.sub(r"[^a-z0-9]+", " ", it["title"].lower().split(" - ")[0]).strip()
        key = hashlib.md5(" ".join(key.split()[:9]).encode()).hexdigest()
        if key in seen:
            seen[key]["dup"] += 1
            seen[key]["sources"].add(it["source"].split(" · ")[0])
        else:
            it["dup"] = 1
            it["sources"] = {it["source"].split(" · ")[0]}
            seen[key] = it
    out = list(seen.values())
    for o in out:
        o["sources"] = sorted(o["sources"])
    return out


def score(it: dict, now: float | None = None) -> dict:
    now = now or time.time()
    text = f'{it["title"]}. {it["summary"]}'
    # 强度 0-100
    strength = 20 + max([v for p, v in STRONG_EVENTS.items() if re.search(p, text, re.I)] + [0]) * 2
    if re.search(r"\$\s?\d[\d,.]*\s?(billion|bn|b)\b", text, re.I): strength += 8
    # 确定性 0-100
    certain = 55 + 12 * sum(bool(re.search(p, text, re.I)) for p in CERTAIN_POS) - 18 * sum(bool(re.search(p, text, re.I)) for p in CERTAIN_NEG)
    if "sec.gov" in it["url"] or it["source"].startswith("SEC"): certain += 20
    # 新颖度 0-100：时效衰减 + 被多源转载=已被消化 + 水文降分
    age_h = max(0, (now - it["ts"]) / 3600)
    novel = 85 - min(60, age_h * 2.5) - 6 * (it.get("dup", 1) - 1)
    if _any(NOVEL_NEG, text): novel -= 25
    strength, certain, novel = [max(0, min(100, x)) for x in (strength, certain, novel)]
    imp = 0.45 * strength + 0.30 * certain + 0.25 * novel
    emotional = _any(EMOTION, it["title"]) and not re.search(r"\$\s?\d|%|\b(announce|report|file)", text, re.I)
    if emotional: imp -= 10  # 情绪化无事实依据 -> 压分打灰
    bull, bear = len(re.findall(BULL, text, re.I)), len(re.findall(BEAR, text, re.I))
    sentiment = "利好" if bull > bear else "利空" if bear > bull else "中性"
    tickers = [t for t, rx in _TICKER_RE.items() if rx.search(it["title"]) or (t in _NAME_RE and _NAME_RE[t].search(it["title"]))]
    topics = [k for k, p in TOPICS.items() if re.search(p, text, re.I)] or ["其他"]
    imp = round(max(0, min(100, imp)), 1)
    grade = next(g for g, th, _ in config.GRADES if imp >= th)
    excluded = _any([rf"\b{k}\b" for k in config.EXCLUDE_KEYWORDS], text)
    return {**it, "strength": int(strength), "certain": int(certain), "novel": int(novel), "imp": imp,
            "grade": grade, "sentiment": sentiment, "tickers": tickers, "topics": topics,
            "emotional": bool(emotional), "excluded": bool(excluded), "age_h": round(age_h, 1)}


def process(items: list[dict]) -> list[dict]:
    scored = [score(i) for i in dedup(items)]
    scored.sort(key=lambda x: (-x["imp"], -x["ts"]))
    return scored

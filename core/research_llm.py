"""可选：用大模型把“结构化事实包”写成研究员风格的风险/增长推演（论点 → 反驳/证据 → 跟踪信号）。

启用：设置环境变量 ANTHROPIC_API_KEY（专用 key）。未设置则整体跳过，看板保持规则版，不报错。
可选环境变量：RESEARCH_API_BASE（默认 https://api.anthropic.com）、RESEARCH_MODEL（默认见 config.RESEARCH_LLM）。

防编造措施：
  1. 提示词只提供事实包，明确禁止引入包外数据/事件；不确定处必须写“需跟踪”；
  2. 输出后核对：文中出现的数字必须能在事实包里找到，找不到的计入 unverified，超过阈值整段打警告；
  3. 页面把“AI 推演”与“数据要点”分开展示并标注，且缓存按事实包哈希，事实不变不重复调用。
"""
from __future__ import annotations
import hashlib, json, os, re, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

import config

CACHE = Path(__file__).resolve().parent.parent / "cache" / "llm"

SYSTEM = """你是一名严谨的美股研究员，为内部投研看板撰写单只股票的“风险点”和“增长点”。
硬性规则：
1. 只能使用【事实包】里出现的数据与事件。禁止引入包外的数字、日期、事件、人名、产品名；包里没有的信息一律写“需跟踪”，不要推测成事实。
2. 每条结构：先给一句【论点】，再写【证据/反驳】（必须引用事实包里的具体数字或情报标题），最后写【跟踪信号】——必须是可观测的指标或事件（如下季度指引、毛利率、订单、监管进展、分析师预期变化），并说明“出现什么则论点强化/证伪”。
3. 风险 4~6 条，增长 3~5 条，每条不超过 190 字，中文，语气克制，不使用“强烈推荐/必涨”等措辞，不给买卖指令。
4. 风险里必须包含至少一条“反方视角”：假设当前低估/高估的结论是错的，最可能错在哪里。
5. one_liner 是不超过 60 字的一句话结论，概括估值位置与最主要的矛盾。
只输出 JSON，不要任何解释，格式：
{"one_liner":"...","risks":[{"k":"标签","t":"正文"}],"growths":[{"k":"标签","t":"正文"}]}"""


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and config.RESEARCH_LLM.get("enabled", True)


def facts_packet(r: dict, news: list[dict], holders: list[dict]) -> dict:
    """喂给模型的事实包：全部来自本看板已算出的数据，不含任何外部检索。"""
    f = {
        "标的": f'{r["name"]} ({r["t"]})', "行业": f'{r.get("gics_sector","")} / {r.get("gics_industry","")}',
        "现价": r.get("price"), "日涨跌": r.get("chg"), "近3月": r.get("r3m"), "距52周高点": r.get("from_hi"),
        "估值": {"参考买点": r.get("buy"), "合理价": r.get("fair"), "参考卖点": r.get("sell"), "状态": r.get("zone"),
                 "来源": r.get("val_src"), "置信": r.get("val_conf"), "各方法": r.get("val_methods"),
                 "前瞻PE": r.get("pe_now"), "前瞻PE历史分位": r.get("pe_pct"), "历史PE_P25_P50_P75": r.get("pe_band")},
        "一致预期": r.get("est"), "上一财年财务(10-K)": r.get("fund"),
        "近期情报": [{"级别": n["grade"], "倾向": n["sentiment"], "标题": n["title"][:140], "来源": "/".join(n["sources"][:2])} for n in news[:6]],
        "机构13F": [{"机构": h["fund"], "动作": h["act"], "占其组合": h["pct"]} for h in holders[:5]],
        "研究员既有观点": {"风险": r.get("risk"), "增长": r.get("growth")},
    }
    return json.loads(json.dumps(f, ensure_ascii=False, default=str))


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def verify(out: dict, packet: dict) -> int:
    """输出里的数字（排除 1~2 位整数和年份）必须出现在事实包；返回无法核对的个数。"""
    pool = _numbers(json.dumps(packet, ensure_ascii=False))
    # 百分比以小数存储，补充 ×100 的写法
    for m in re.findall(r"-?\d+\.\d+", json.dumps(packet)):
        v = float(m) * 100
        pool |= {f"{v:.0f}", f"{v:.1f}", f"{abs(v):.0f}", f"{abs(v):.1f}"}
    bad = 0
    body = out.get("one_liner", "") + " ".join(i["t"] for i in out.get("risks", []) + out.get("growths", []))
    for n in _numbers(body):
        if re.fullmatch(r"\d{1,2}", n) or re.fullmatch(r"20\d\d", n):
            continue
        if n not in pool and n.rstrip("0").rstrip(".") not in pool:
            bad += 1
    return bad


def _call(packet: dict) -> dict | None:
    base = os.environ.get("RESEARCH_API_BASE", "https://api.anthropic.com").rstrip("/")
    body = {"model": os.environ.get("RESEARCH_MODEL", config.RESEARCH_LLM["model"]), "max_tokens": 2200, "temperature": 0.3,
            "system": SYSTEM, "messages": [{"role": "user", "content": "【事实包】\n" + json.dumps(packet, ensure_ascii=False)}]}
    for i in range(3):
        try:
            r = requests.post(f"{base}/v1/messages", json=body, timeout=90,
                              headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
            if r.status_code in (429, 500, 502, 503, 529):
                time.sleep(3 * (i + 1)); continue
            if r.status_code != 200:
                return None
            txt = "".join(b.get("text", "") for b in r.json().get("content", []))
            txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
            out = json.loads(txt)
            ok = lambda L: [{"k": str(i["k"])[:12], "t": str(i["t"])[:420]} for i in L if isinstance(i, dict) and i.get("k") and i.get("t")]
            return {"one_liner": str(out.get("one_liner", ""))[:120], "risks": ok(out.get("risks", [])), "growths": ok(out.get("growths", []))}
        except Exception:
            time.sleep(2)
    return None


def generate(r: dict, news: list[dict], holders: list[dict]) -> dict | None:
    packet = facts_packet(r, news, holders)
    key = hashlib.md5(json.dumps(packet, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f'{r["t"]}.json'
    if p.exists():
        c = json.loads(p.read_text("utf-8"))
        if c.get("key") == key and time.time() - c["ts"] < 3 * 86400:
            return c["out"]
    out = _call(packet)
    if not out or not (out["risks"] or out["growths"]):
        return json.loads(p.read_text("utf-8"))["out"] if p.exists() else None  # 失败时沿用旧稿
    out["unverified"] = verify(out, packet)
    out["model"] = os.environ.get("RESEARCH_MODEL", config.RESEARCH_LLM["model"])
    out["generated"] = time.strftime("%Y-%m-%d %H:%M")
    p.write_text(json.dumps({"key": key, "ts": time.time(), "out": out}, ensure_ascii=False), "utf-8")
    return out


def generate_all(rows: list[dict], news_by_t: dict, holders_by_t: dict) -> None:
    if not enabled():
        print("      AI 推演：未设置 ANTHROPIC_API_KEY，跳过（保持规则版）")
        return
    def one(r):
        return r["t"], generate(r, news_by_t.get(r["t"], []), holders_by_t.get(r["t"], []))
    with ThreadPoolExecutor(config.RESEARCH_LLM.get("workers", 4)) as ex:
        res = dict(ex.map(one, rows))
    n = 0
    for r in rows:
        o = res.get(r["t"])
        if o:
            r["ai"] = o; n += 1
    print(f"      AI 推演：{n}/{len(rows)} 家成功，数字核对警告 {sum(1 for r in rows if r.get('ai', {}).get('unverified', 0) > 2)} 家")

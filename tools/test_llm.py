"""离线测试 research_llm：不联网，用模拟响应验证 解析 / 数字核对 / 缓存 / 失败降级。"""
import json, os, sys, tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.stdout.reconfigure(encoding="utf-8")
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"

from core import research_llm as R

R.CACHE = Path(tempfile.mkdtemp())  # 不污染真实缓存

row = {"t": "TEST", "name": "Test Co", "gics_sector": "信息技术", "gics_industry": "半导体", "price": 168.9, "chg": 0.01, "r3m": -0.08,
       "from_hi": -0.22, "buy": 256.7, "fair": 293.5, "sell": 369.6, "zone": "买点区", "val_src": "自动", "val_conf": "高", "val_methods": {"DCF": 227.0},
       "pe_now": 17.6, "pe_pct": 0.01, "pe_band": [33.0, 38.0, 47.0], "est": {"eps0": 9.32}, "fund": {"rev_g": 0.35}, "risk": "r", "growth": "g"}
news = [{"grade": "A", "sentiment": "利空", "title": "Export curbs widen", "sources": ["CNBC"]}]

calls = {"n": 0}


class Resp:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self): return self._b


def fake_post_good(url, **kw):
    calls["n"] += 1
    out = {"one_liner": "前瞻PE 17.6 倍仅处历史 1% 分位，但需核对预期。",
           "risks": [{"k": "估值陷阱", "t": "前瞻PE 17.6x 处于历史1%分位（P25=33），可能反映预期下修；跟踪：下季指引。"},
                     {"k": "编造测试", "t": "公司在 2031 年之前将获得 987654 份订单，毛利率 71.3%，合同金额 55555 万。"}],
           "growths": [{"k": "预期", "t": "本财年 EPS 一致预期 9.32。"}]}
    return Resp(200, {"content": [{"type": "text", "text": "```json\n" + json.dumps(out, ensure_ascii=False) + "\n```"}]})


R.requests.post = fake_post_good
o = R.generate(row, news, [])
print("解析成功:", bool(o), "| 风险条数:", len(o["risks"]), "| 未能核对的数字个数:", o["unverified"])
assert o["unverified"] >= 3, "编造的数字应被抓出"
o2 = R.generate(row, news, [])
print("相同事实包第二次调用是否走缓存:", calls["n"] == 1)
assert calls["n"] == 1

row2 = dict(row, price=170.0)  # 事实变化 -> 重新生成
R.generate(row2, news, [])
print("事实变化后重新调用:", calls["n"] == 2)


def fake_post_down(url, **kw):
    return Resp(500, {})


R.requests.post = fake_post_down
R.time.sleep = lambda s: None
old = R.generate(dict(row, price=999.0), news, [])
print("接口失败但有旧稿 -> 沿用旧稿:", old is not None and old["risks"][0]["k"] == "估值陷阱")
assert old is not None
fresh = R.generate(dict(row, t="NEWCO", price=999.0), news, [])
print("接口失败且无旧稿 -> 返回 None（页面回退规则版）:", fresh is None)
assert fresh is None
os.environ.pop("ANTHROPIC_API_KEY")
print("无 key 时 enabled():", R.enabled())
print("全部通过")

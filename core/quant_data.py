"""量化研究的数据面板：股票池日线（收盘 / 复权收盘 / 最高 / 最低 / 成交量 / 拆股事件）+ 行业分类。
数据源 Yahoo chart 公共接口；行情缓存 12 小时，行业缓存 30 天。

重要局限（页面上必须展示）：股票池是“今天的大中型股名单”，不含历史上已退市/被剔除的公司 ——
这是幸存者偏差，会系统性高估多头因子的回测收益，免费数据无法消除，只能披露。

注意：Yahoo 的 close 已按拆股复权（但不含分红），adj 同时含分红。每股财务数据必须按“披露日之后发生的拆股”换算到同一口径。"""
from __future__ import annotations
import json, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd
import requests

CACHE = Path(__file__).resolve().parent.parent / "cache"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
YEARS = 10

_LARGE = """AAPL MSFT NVDA GOOGL AMZN META AVGO TSLA ORCL NFLX AMD CRM ADBE CSCO INTC QCOM TXN IBM AMAT MU LRCX KLAC NOW INTU PANW SNPS CDNS ANET UBER PLTR
JPM BAC WFC GS MS C BLK SCHW AXP V MA PYPL SPGI MCO CB PGR BRK-B COF USB
LLY JNJ UNH ABBV MRK PFE TMO ABT DHR AMGN GILD ISRG VRTX BMY MDT CVS REGN
WMT COST HD MCD NKE SBUX TGT LOW PG KO PEP PM MO MDLZ BKNG
CAT DE HON GE RTX LMT BA UPS UNP ETN MMM
XOM CVX COP SLB EOG NEE DUK SO LIN SHW FCX PLD AMT DIS CMCSA T VZ TMUS"""
_MID = """ADSK WDAY FTNT DDOG CRWD MRVL ON MCHP NXPI ADI SWKS GLW KEYS TER MPWR HPQ STX WDC NTAP AKAM TYL PTC FICO CDW EPAM FFIV
AIG MET PRU ALL TRV AFL HIG CINF TROW BK STT NTRS FITB HBAN RF KEY CFG MTB ICE CME NDAQ MSCI FIS GPN SYF AMP RJF
ZTS BDX SYK BSX EW IDXX IQV A MTD WAT RMD HCA ELV CI HUM MCK COR BIIB ALGN DXCM HOLX
F GM MAR HLT ORLY AZO ROST TJX YUM CMG DRI LULU EBAY RCL CCL DHI LEN PHM NVR
KMB CL GIS HSY SYY KR ADM STZ KDP MNST EL CLX
EMR ITW PH ROK FDX CSX NSC DAL UAL LUV WM RSG CTAS PAYX ADP GD NOC TDG HWM CARR OTIS PCAR CMI FAST GWW URI
PSX MPC VLO OXY KMI WMB OKE HAL BKR DVN FANG
APD ECL NEM DOW NUE MLM VMC D AEP EXC SRE XEL PEG WEC ED EIX CCI EQIX PSA O SPG WELL DLR
CHTR EA TTWO WBD OMC""".split()
UNIVERSE = list(dict.fromkeys(_LARGE.split() + _MID))
BENCH = "SPY"


def _one(t: str):
    for i in range(3):
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}",
                             params={"range": f"{YEARS}y", "interval": "1d", "events": "split"}, headers=UA, timeout=30)
            if r.status_code != 200:
                time.sleep(1 + i); continue
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose") or q["close"]
            idx = pd.to_datetime(res["timestamp"], unit="s").normalize()
            df = pd.DataFrame({"adj": adj, "high": q["high"], "low": q["low"], "vol": q["volume"], "close": q["close"]}, index=idx)
            sp = [(int(v["date"]), v["numerator"] / v["denominator"]) for v in (res.get("events", {}).get("splits", {}) or {}).values()]
            return t, (df[~df.index.duplicated(keep="last")].dropna(subset=["adj"]), sorted(sp))
        except Exception:
            time.sleep(1 + i)
    return t, None


def load_panel(universe: list[str] | None = None, max_age_h: float = 12) -> dict:
    """返回 {'adj','close','high','low','vol'}: 日期×股票 DataFrame；'_splits': {ticker:[(ts,ratio)]}; '_missing': [...]"""
    p = CACHE / "panel2.pkl"
    if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
        return pd.read_pickle(p)
    tickers = list(dict.fromkeys((universe or UNIVERSE) + [BENCH]))
    with ThreadPoolExecutor(8) as ex:
        got = dict(ex.map(_one, tickers))
    ok = {t: v for t, v in got.items() if v is not None and len(v[0]) > 500}
    if BENCH not in ok:
        raise RuntimeError("基准 SPY 行情获取失败")
    panel = {k: pd.DataFrame({t: v[0][k] for t, v in ok.items()}).sort_index() for k in ("adj", "close", "high", "low", "vol")}
    panel["_splits"] = {t: v[1] for t, v in ok.items()}
    panel["_missing"] = sorted(set(tickers) - set(ok))
    CACHE.mkdir(exist_ok=True)
    pd.to_pickle(panel, p)
    return panel


def sectors(tickers: list[str]) -> dict[str, str]:
    """GICS 风格行业（Yahoo assetProfile.sector）。缓存 30 天；取不到的归入“未知”。"""
    p = CACHE / "sectors.json"
    have = json.loads(p.read_text("utf-8")) if p.exists() else {}
    fresh = have.get("_ts", 0) > time.time() - 30 * 86400
    todo = [t for t in tickers if t not in have.get("map", {})] if fresh else tickers
    m = have.get("map", {}) if fresh else {}
    if todo:
        from core.valuation import Consensus
        s = Consensus()._session()
        if s:
            def one(t):
                try:
                    r = s.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{t}",
                              params={"modules": "assetProfile", "crumb": s.crumb}, timeout=20)
                    if r.status_code == 200:
                        return t, r.json()["quoteSummary"]["result"][0]["assetProfile"].get("sector")
                except Exception:
                    pass
                return t, None
            with ThreadPoolExecutor(6) as ex:
                for t, sec in ex.map(one, todo):
                    if sec: m[t] = sec
        p.write_text(json.dumps({"_ts": time.time(), "map": m}, ensure_ascii=False), "utf-8")
    return {t: m.get(t, "未知") for t in tickers}

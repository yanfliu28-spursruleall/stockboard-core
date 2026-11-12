"""定时刷新入口（给 Windows 任务计划用）：  pythonw refresh.py [--force] [--dry]

安全设计：
  1. 口令从 secrets/passphrase.txt 读取，只通过子进程的环境变量传递，不出现在命令行/日志里；没有口令就拒绝运行（绝不输出明文页面）。
  2. 锁文件防止上一次还没跑完（例如 13F 要 6 分钟）又启动一次；锁超过 45 分钟视为残留。
  3. 先构建到 dist_new/，校验：文件齐全 + 用口令真能解密 + 解密后内容含关键数据，通过才替换 dist/；失败则保留旧 dist/，页面不会坏。
  4. 按美股时段限频：盘中 ≥50 分钟、工作日盘外 ≥3 小时、周末 ≥8 小时；任务计划可以每小时触发，多余的触发会自己跳过。
  5. 发布是“留空接口”：只有项目根目录存在 publish_hook.cmd 才会调用（参数=dist 路径）；默认什么都不对外推送。
  6. 连续失败 ≥3 次写 logs/ALERT.txt。
"""
import argparse, base64, gzip, hashlib, json, os, re, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# pythonw.exe（计划任务用，无控制台）下 sys.stdout/stderr 为 None，print 会崩；统一改成安全的 utf-8 输出
for _n in ("stdout", "stderr"):
    _s = getattr(sys, _n)
    if _s is None:
        setattr(sys, _n, open(os.devnull, "w", encoding="utf-8"))
    else:
        try: _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

ROOT = Path(__file__).resolve().parent
LOGS, STATUS = ROOT / "logs", ROOT / "status"
DIST, NEW, PREV = ROOT / "dist", ROOT / "dist_new", ROOT / "dist_prev"
LOCK, PASS_FILE = ROOT / "refresh.lock", ROOT / "secrets" / "passphrase.txt"
HOOK = ROOT / "publish_hook.cmd"
ET = ZoneInfo("America/New_York")
PBKDF2_ITER = 300000


def log(msg: str):
    LOGS.mkdir(exist_ok=True)
    p = LOGS / "refresh.log"
    if p.exists() and p.stat().st_size > 1_000_000:
        p.replace(LOGS / "refresh.log.1")
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def min_interval_minutes(now: datetime | None = None) -> int:
    """美股时段限频：盘中 50 分钟；工作日盘外 3 小时；周末 8 小时。"""
    t = (now or datetime.now(ET)).astimezone(ET)
    if t.weekday() >= 5:
        return 8 * 60
    mins = t.hour * 60 + t.minute
    return 50 if 9 * 60 + 30 <= mins <= 16 * 60 + 30 else 3 * 60


def read_status() -> dict:
    p = STATUS / "last_refresh.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def write_status(d: dict):
    STATUS.mkdir(exist_ok=True)
    (STATUS / "last_refresh.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")


def acquire_lock() -> bool:
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime > 45 * 60:
        log("发现残留锁(>45分钟)，清除"); LOCK.unlink()
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {time.time()}".encode()); os.close(fd)
        return True
    except FileExistsError:
        return False


def verify(out: Path, pw: str) -> str | None:
    """返回 None 表示通过，否则返回失败原因。"""
    idx, binf = out / "index.html", out / "app.bin"
    if not idx.exists() or not binf.exists():
        return "缺少 index.html 或 app.bin"
    gate = idx.read_text("utf-8")
    if "NVDA" in gate or "const D =" in gate:
        return "index.html 里出现了明文数据 —— 加密失败，拒绝发布"
    m = re.search(r"atob\('([^']+)'\)", gate)
    if not m:
        return "口令门页面异常"
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(m.group(1)), PBKDF2_ITER, 32)
    b = binf.read_bytes()
    try:
        html = gzip.decompress(AESGCM(key).decrypt(b[:12], b[12:], None)).decode("utf-8")
    except Exception:
        return "用当前口令无法解密 app.bin"
    for need in ('"board"', '"quant"', "美股投研平台"):
        if need not in html:
            return f"解密后的页面缺少关键内容 {need}"
    if len(html) < 500_000:
        return f"页面体积异常偏小 ({len(html)} 字节)"
    return None


def swap():
    if PREV.exists(): shutil.rmtree(PREV)
    if DIST.exists(): DIST.rename(PREV)
    NEW.rename(DIST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略限频，立即刷新")
    ap.add_argument("--dry", action="store_true", help="只检查前置条件并打印，不构建")
    a = ap.parse_args()
    st = read_status()

    if not PASS_FILE.exists() and not os.environ.get("BOARD_PASSPHRASE"):
        log("失败：没有口令（secrets/passphrase.txt 不存在，也未设置 BOARD_PASSPHRASE），拒绝运行"); return 3
    pw = os.environ.get("BOARD_PASSPHRASE") or PASS_FILE.read_text("utf-8").strip()

    need = min_interval_minutes()
    age = (time.time() - st.get("finished_ts", 0)) / 60 if st.get("ok") else 1e9
    if not a.force and age < need:
        log(f"跳过：距上次成功 {age:.0f} 分钟 < 当前时段最小间隔 {need} 分钟"); return 0
    if a.dry:
        print(f"将刷新：距上次成功 {age:.0f} 分钟，最小间隔 {need} 分钟，口令已就绪（长度 {len(pw)}）"); return 0
    if not acquire_lock():
        log("跳过：上一次刷新还在运行（锁存在）"); return 0

    t0 = time.time()
    started = datetime.now().isoformat(timespec="seconds")
    try:
        if NEW.exists(): shutil.rmtree(NEW)
        env = dict(os.environ, BOARD_PASSPHRASE=pw, PYTHONIOENCODING="utf-8")
        cmd = [sys.executable, str(ROOT / "build.py"), "--auto", "--out", str(NEW), "--require-encrypt"]
        log(f"开始构建（最小间隔 {need} 分钟）")
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40 * 60)
        tail = "\n".join((r.stdout or "").strip().splitlines()[-6:])
        if r.returncode != 0:
            raise RuntimeError(f"build.py 退出码 {r.returncode}: {((r.stderr or '').strip().splitlines() or [''])[-1][:300]}")
        why = verify(NEW, pw)
        if why:
            raise RuntimeError("校验未通过：" + why)
        swap()
        sec = time.time() - t0
        log(f"成功 {sec:.0f}s，已替换 dist/\n{tail}")
        write_status({"ok": True, "started": started, "finished": datetime.now().isoformat(timespec="seconds"), "finished_ts": time.time(),
                      "seconds": round(sec), "consecutive_failures": 0})
        if (LOGS / "ALERT.txt").exists(): (LOGS / "ALERT.txt").unlink()
        if HOOK.exists():
            h = subprocess.run(["cmd", "/c", str(HOOK), str(DIST)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
            log(f"发布钩子 publish_hook.cmd 退出码 {h.returncode}: {(h.stdout or h.stderr or '').strip()[-200:]}")
        return 0
    except Exception as e:
        n = st.get("consecutive_failures", 0) + 1
        log(f"失败（连续第 {n} 次）：{e}；保留旧 dist/")
        write_status({**{k: v for k, v in st.items() if k in ("finished", "finished_ts", "ok")}, "ok": st.get("ok", False), "last_error": str(e)[:500],
                      "last_failed": datetime.now().isoformat(timespec="seconds"), "consecutive_failures": n})
        if n >= 3:
            (LOGS / "ALERT.txt").write_text(f"{datetime.now():%Y-%m-%d %H:%M} 刷新已连续失败 {n} 次：{e}\n详见 logs/refresh.log", "utf-8")
        return 1
    finally:
        if LOCK.exists(): LOCK.unlink()


if __name__ == "__main__":
    sys.exit(main())

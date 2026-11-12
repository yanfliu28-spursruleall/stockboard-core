"""本地预览加密页面：  python tools/serve.py [--port 8765]   然后浏览器打开 http://127.0.0.1:8765/
为什么不能双击 dist/index.html：口令门要用 fetch('app.bin') 读密文，浏览器禁止 file:// 页面发起 fetch。
只绑定 127.0.0.1（本机），不会暴露到局域网。"""
import argparse, functools, http.server, sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):                     # Windows 控制台默认 cp1252，打印中文会崩
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8765)
a = ap.parse_args()
root = Path(__file__).resolve().parent.parent / "dist"


class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")   # 刷新后总能拿到最新密文
        super().end_headers()

    def log_message(self, *args):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), functools.partial(H, directory=str(root)))
print(f"预览地址 http://127.0.0.1:{a.port}/  （Ctrl+C 停止；口令在 secrets/passphrase.txt）", flush=True)
try:
    srv.serve_forever()
except KeyboardInterrupt:
    pass

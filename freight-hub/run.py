"""货代询价助手启动入口。

优先用 pywebview（Windows 上是 Edge WebView2）打开无边框独立窗口（自绘标题栏）；
不可用时退回 Edge/Chrome 的 --app 窗口；都没有则用默认浏览器。
支持 pythonw / 无控制台打包：标准输出会重定向到 data/freight-hub.log。
"""
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser

ROOT = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
if not getattr(sys, "frozen", False):
    sys.path.insert(0, ROOT)

def _parse_cli(argv):
    """命令行：--server [--host 0.0.0.0] [--port 8765] [--cert 证书.pem --key 私钥.pem] [--https]。
    服务器模式要在导入 app.main 之前确定（通过环境变量传给它）。"""
    import argparse
    ap = argparse.ArgumentParser(add_help=True, description="货代询价助手 FreightHub")
    ap.add_argument("--server", action="store_true", help="网页/服务器模式：浏览器访问，需要登录")
    ap.add_argument("--host"), ap.add_argument("--port", type=int)
    ap.add_argument("--cert"), ap.add_argument("--key")
    ap.add_argument("--trust-proxy", action="store_true", help="位于 Nginx 等反向代理之后（读取 X-Forwarded-*）")
    a, _ = ap.parse_known_args(argv)
    if a.server:
        os.environ["FREIGHT_HUB_MODE"] = "server"
    for k, v in (("FREIGHT_HUB_HOST", a.host), ("FREIGHT_HUB_PORT", a.port), ("FREIGHT_HUB_CERT", a.cert), ("FREIGHT_HUB_KEY", a.key)):
        if v:
            os.environ[k] = str(v)
    if a.trust_proxy:
        os.environ["FREIGHT_HUB_TRUST_PROXY"] = "1"


_parse_cli(sys.argv[1:])
SERVER_MODE = os.environ.get("FREIGHT_HUB_MODE", "").lower() == "server"
HOST = os.environ.get("FREIGHT_HUB_HOST") or ("0.0.0.0" if SERVER_MODE else "127.0.0.1")
PORT = int(os.environ.get("FREIGHT_HUB_PORT", "8765"))
CERT, KEY = os.environ.get("FREIGHT_HUB_CERT"), os.environ.get("FREIGHT_HUB_KEY")
_SCHEME = "https" if CERT else "http"
URL = f"{_SCHEME}://{'127.0.0.1' if HOST in ('0.0.0.0', '::') else HOST}:{PORT}/"
TITLE = "货代询价助手 FreightHub"
from app.paths import data_path  # noqa: E402
DATA_DIR = str(data_path())
WEBVIEW_DIR = os.path.join(DATA_DIR, "webview")


def setup_stdio():
    """pythonw / --noconsole 下 stdout 为 None，uvicorn 会崩；改写到日志文件。"""
    for st in (sys.stdout, sys.stderr):      # 控制台 / 重定向到文件时可能是 cp936 / cp1252：中文提示不能让程序崩
        try:
            st and st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.stdout is not None and sys.stderr is not None:
        return
    from app.paths import data_path
    d = str(data_path())
    os.makedirs(d, exist_ok=True)
    f = open(os.path.join(d, "freight-hub.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


def fatal(msg):
    print(msg, file=sys.stderr)
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg[:1500], TITLE, 0x10)
        except Exception:
            pass


def port_free(p=PORT):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST, p)) != 0


def find_browser():
    from app.cdp import find_browser as fb
    return fb()


class DeskApi:
    """暴露给页面 JS 的窗口控制接口（无边框窗口用）。

    重要：pywebview 会递归遍历 js_api 对象上所有「不以下划线开头」的公开属性并逐个反射；
    如果这里存 self.window（公开属性），就会遍历到 window.native（.NET WinForms 对象树），
    在非 UI 线程访问 COM 对象会刷出大量错误并无限递归，造成窗口卡死、空白（1.2.2 的问题）。
    所以所有非纯数据的状态一律用下划线私有属性。"""

    def __init__(self):
        self._window = None
        self._maxed = False
        self._shell = None

    def minimize(self):
        self._window.minimize()

    def toggle_maximize(self):
        if self._maxed:
            self._window.restore()
        else:
            self._window.maximize()
        self._maxed = not self._maxed
        return self._maxed

    def close(self):
        sh = self._shell
        if sh is not None and sh.close_to_tray():
            self._window.hide()          # 最小化到托盘：快捷键 / 托盘继续可用
        else:
            self._window.destroy()

    def quick_popup(self, text=""):
        """主窗口工具栏输入框 / 菜单：在鼠标处弹出快速询价浮窗。"""
        if self._shell is None:
            raise RuntimeError("外壳未启动")
        threading.Thread(target=self._shell.show_popup, args=(str(text)[:120],), daemon=True).start()
        return True

    def get_rect(self):
        w = self._window
        return [w.x, w.y, w.width, w.height]

    def set_rect(self, x, y, w, h):
        self._window.resize(int(w), int(h))
        self._window.move(int(x), int(y))
        return True

    def save_file(self, name, text):
        import webview
        dlg = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or webview.SAVE_DIALOG
        path = self._window.create_file_dialog(dlg, save_filename=name, file_types=("CSV 文件 (*.csv)", "所有文件 (*.*)"))
        if not path:
            return ""
        path = path if isinstance(path, str) else path[0]
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return path

    def save_bytes(self, name, b64):
        """保存二进制文件（xlsx 等）；b64 为 base64 文本。返回保存路径，取消则返回空串。"""
        import base64
        import webview
        dlg = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or webview.SAVE_DIALOG
        types = ("Excel 文件 (*.xlsx)", "所有文件 (*.*)") if name.lower().endswith(".xlsx") else ("CSV 文件 (*.csv)", "所有文件 (*.*)")
        path = self._window.create_file_dialog(dlg, save_filename=name, file_types=types)
        if not path:
            return ""
        path = path if isinstance(path, str) else path[0]
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))
        return path


LOADING_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#f3f3f3;font:14px "Segoe UI","Microsoft YaHei",sans-serif;color:#444;overflow:hidden}
.c{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center}
.sp{width:38px;height:38px;border:4px solid #d6e4f5;border-top-color:#0f6cbd;border-radius:50%;animation:s .8s linear infinite;margin-bottom:16px}
@keyframes s{to{transform:rotate(360deg)}} .t2{margin-top:6px;font-size:12px;color:#888;max-width:80%;text-align:center;white-space:pre-wrap}
#x{position:fixed;top:0;right:0;width:46px;height:32px;line-height:32px;text-align:center;color:#555;cursor:default}#x:hover{background:#e81123;color:#fff}
</style></head><body><div id="x" onclick="window.pywebview&&pywebview.api.close()">&#x2715;</div>
<div class="c"><div class="sp" id="sp"></div><div id="m">正在启动…</div><div class="t2" id="s">货代询价助手 FreightHub</div></div>
<script>function setErr(m){document.getElementById('sp').style.display='none';document.getElementById('m').textContent='启动失败';document.getElementById('s').textContent=m}</script>
</body></html>"""

_server_lock = threading.Lock()
_server = None


def ensure_server():
    """幂等地启动本地服务（首次调用才导入 fastapi / 适配器，加快窗口出现的速度）。"""
    global _server
    with _server_lock:
        if _server is None and port_free():
            _server = start_server()
        return _server


def start_server():
    import uvicorn
    from app.main import app
    kw = {}
    if CERT:
        kw.update(ssl_certfile=CERT, ssl_keyfile=KEY or None)
    if os.environ.get("FREIGHT_HUB_TRUST_PROXY"):
        kw.update(proxy_headers=True, forwarded_allow_ips="*")
    cfg = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", log_config=None, **kw)
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(300):
        if not port_free():
            return server
        time.sleep(0.1)
    raise RuntimeError("本地服务启动失败")


def start_shell(win, api, holder):
    """全局热键 + 托盘 + 鼠标处快速询价浮窗（仅 Windows）。失败只记日志，不影响主窗口。"""
    if not sys.platform.startswith("win") or os.environ.get("FREIGHT_HUB_NO_SHELL"):
        return
    try:
        import webview
        from app import main as appmain
        from app.paths import app_root
        from app.shell import Shell
        sh = Shell(webview, win, TITLE, URL, icon_path=str(app_root() / "FreightHub.ico"))
        ok = sh.start(exit_cb=lambda: win.destroy())
        holder["shell"] = sh
        api._shell = sh
        appmain.shell_hooks.update(status=sh.status, settings=sh.apply_settings,
                                   open_popup=lambda t: threading.Thread(target=sh.show_popup, args=(t,), daemon=True).start())
        print("快速询价外壳：" + sh.status().get("message", ""), flush=True)
    except Exception:
        print("快速询价外壳启动失败：\n" + traceback.format_exc(), file=sys.stderr)


def run_pywebview():
    """先立刻显示「正在启动…」窗口，后台线程启动本地服务，就绪后跳转主界面。
    成功打开并在窗口关闭后返回 True；pywebview 不可用返回 False。"""
    try:
        import webview
    except Exception:
        return False
    api = DeskApi()
    native = bool(os.environ.get("FREIGHT_HUB_NATIVE_FRAME"))  # 用系统自带标题栏（调试 / 兼容用）
    kw = dict(js_api=api, width=1360, height=860, min_size=(900, 560), background_color="#f3f3f3", html=LOADING_HTML)
    if not native:
        kw.update(frameless=True, easy_drag=False)
    try:
        win = webview.create_window(TITLE, shadow=not native, **kw)
    except TypeError:
        kw.pop("shadow", None)
        win = webview.create_window(TITLE, **kw)
    api._window = win
    target = URL + ("" if native else "?frame=1")
    if sys.platform.startswith("win") and not os.environ.get("FREIGHT_HUB_NO_SHELL"):
        try:
            from app import winshell
            winshell.set_dpi_aware()      # 每显示器 DPI 感知：浮窗按鼠标所在显示器的缩放摆放
        except Exception:
            traceback.print_exc()
    holder = {}

    def on_closed():
        sh = holder.get("shell")
        if sh:
            sh.stop()
    win.events.closed += on_closed

    def boot():
        try:
            ensure_server()
            print(f"货代询价助手已启动：{URL}", flush=True)
            win.load_url(target)
            start_shell(win, api, holder)
        except Exception:
            err = traceback.format_exc()
            print("本地服务启动失败：\n" + err, file=sys.stderr)
            try:
                import json
                win.evaluate_js("setErr(%s)" % json.dumps(err[-600:], ensure_ascii=False))
            except Exception:
                pass

    gui = "edgechromium" if sys.platform.startswith("win") else None
    os.makedirs(WEBVIEW_DIR, exist_ok=True)
    webview.start(boot, gui=gui, private_mode=False, storage_path=WEBVIEW_DIR)
    return True


def open_edge_app():
    b = find_browser()
    if b:
        subprocess.Popen([b, f"--app={URL}", "--window-size=1360,860", f"--user-data-dir={os.path.join(DATA_DIR, 'edge-profile')}",
                          "--no-first-run", "--no-default-browser-check"])
        return True
    webbrowser.open(URL)
    return False


def watchdog():
    """浏览器窗口模式下：页面心跳停止（窗口被关闭）后自动退出。"""
    from app.main import ping_state
    start = time.time()
    while True:
        time.sleep(2)
        last = ping_state["last"]
        if last and time.time() - last > 25:
            return
        if not last and time.time() - start > 120:
            return


def run_server_mode():
    """网页/服务器模式：不开窗口，前台运行直到被终止（Ctrl+C / systemd / docker stop）。"""
    if CERT and (not os.path.exists(CERT) or (KEY and not os.path.exists(KEY))):
        fatal(f"找不到 HTTPS 证书文件：{CERT} / {KEY}")
        sys.exit(2)
    if not port_free():
        fatal(f"端口 {PORT} 已被占用，请用 --port 换一个。")
        sys.exit(2)
    ensure_server()
    print(f"货代询价助手（网页模式）已启动：{_SCHEME}://{HOST}:{PORT}/", flush=True)
    if not CERT and HOST not in ("127.0.0.1", "localhost"):
        print("提示：当前是 HTTP 明文。跨公网访问请配置 HTTPS（--cert/--key）或放在 Nginx/Caddy 反向代理之后，详见 部署说明.md。", flush=True)
    while True:
        time.sleep(3600)


def main():
    setup_stdio()
    if SERVER_MODE:
        return run_server_mode()
    no_ui = bool(os.environ.get("FREIGHT_HUB_NO_UI"))
    if no_ui:
        ensure_server()
        print(f"货代询价助手已启动：{URL}", flush=True)
        while True:
            time.sleep(3600)
    try:
        if run_pywebview():       # 已有实例在运行时 ensure_server 什么也不做，窗口直接连上去
            return
    except Exception:
        print("pywebview 启动失败，改用浏览器应用窗口：\n" + traceback.format_exc(), file=sys.stderr)
    ensure_server()
    print(f"货代询价助手已启动：{URL}", flush=True)
    open_edge_app()
    if _server is not None:
        watchdog()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        fatal("货代询价助手启动失败：\n" + traceback.format_exc())
    finally:
        os._exit(0)

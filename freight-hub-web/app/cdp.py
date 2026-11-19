"""极简 Chrome DevTools Protocol 客户端：用本机 Edge/Chrome 完成网页登录并取回 Cookie。
不依赖 Playwright（体积小）；只用 websocket-client。"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket


def find_browser():
    cands = []
    if sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LocalAppData")):
            if base:
                cands += [os.path.join(base, r"Microsoft\Edge\Application\msedge.exe"),
                          os.path.join(base, r"Google\Chrome\Application\chrome.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        cands += [shutil.which(x) or "" for x in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser")]
        cands += glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


class Browser:
    def __init__(self, headless=True, width=1280, height=800):
        exe = find_browser()
        if not exe:
            raise RuntimeError("找不到可用的浏览器（需要 Edge 或 Chrome）")
        self.dir = tempfile.mkdtemp(prefix="fh_cdp_")
        args = [exe, "--remote-debugging-port=0", f"--user-data-dir={self.dir}", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
                f"--window-size={width},{height}", "--lang=zh-CN", "--no-sandbox" if sys.platform.startswith("linux") else "--disable-gpu-sandbox"]
        if headless:
            args.append("--headless=new")
        args.append("about:blank")
        flags = 0x08000000 if sys.platform.startswith("win") else 0  # CREATE_NO_WINDOW（仅无界面模式需要）
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=flags if headless else 0)
        port_file = os.path.join(self.dir, "DevToolsActivePort")
        deadline = time.time() + 20
        while time.time() < deadline and not os.path.exists(port_file):
            if self.proc.poll() is not None:
                raise RuntimeError("浏览器启动后立即退出")
            time.sleep(0.2)
        if not os.path.exists(port_file):
            self.close()
            raise RuntimeError("浏览器调试端口未就绪")
        time.sleep(0.2)
        self.port = int(open(port_file).read().split()[0])
        self._id = 0
        ws_url = None
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=3))
                pages = [t for t in tabs if t.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if not ws_url:
            self.close()
            raise RuntimeError("无法连接浏览器页面")
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)

    def send(self, method, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def eval(self, js):
        r = self.send("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def goto(self, url, timeout=60):
        self.send("Page.navigate", url=url)
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.5)
            try:
                if self.eval("document.readyState") == "complete":
                    return
            except Exception:
                pass
        raise RuntimeError("页面加载超时")

    def wait_for(self, js, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            try:
                v = self.eval(js)
                if v:
                    return v
            except Exception:
                pass
            time.sleep(0.4)
        raise RuntimeError("等待页面元素超时")

    def type_into(self, selector, text):
        ok = self.eval(f"""(()=>{{const e=document.querySelector({json.dumps(selector)});if(!e)return false;e.focus();e.select&&e.select();return true}})()""")
        if not ok:
            raise RuntimeError("找不到输入框 " + selector)
        self.send("Input.dispatchKeyEvent", type="keyDown", key="a", modifiers=2, commands=["selectAll"])
        self.send("Input.dispatchKeyEvent", type="keyUp", key="a", modifiers=2)
        self.send("Input.insertText", text=text)

    def click_xy(self, x, y):
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left" if t != "mouseMoved" else "none", clickCount=1)
            time.sleep(0.05)

    def click_selector(self, selector, text=None):
        js = f"""(()=>{{const els=[...document.querySelectorAll({json.dumps(selector)})].filter(e=>e.offsetParent!==null&&(!{json.dumps(text)}||e.innerText.includes({json.dumps(text)})));
        if(!els.length)return null;const r=els[0].getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]}})()"""
        p = self.eval(js)
        if not p:
            raise RuntimeError("找不到按钮 " + (text or selector))
        self.click_xy(p[0], p[1])

    def cookies(self):
        return self.send("Network.getAllCookies").get("cookies", [])

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        shutil.rmtree(self.dir, ignore_errors=True)

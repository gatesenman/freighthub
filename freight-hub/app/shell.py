"""桌面外壳：全局热键 + 托盘 + 鼠标处弹出的快速询价浮窗。只在 Windows + pywebview 下启用。
窗口摆放用 Win32（物理像素），摆放数学在 app/quick.py（纯函数，有单元测试）。"""
import json
import os
import sys
import threading
import time
import traceback

from . import quick, settings, winshell

POPUP_TITLE = "FreightHub-QuickQuote"
POPUP_DIP = (460, 540)          # 浮窗逻辑尺寸；按所在显示器 DPI 换算成物理像素


class PopupApi:
    """暴露给浮窗页面的接口。非纯数据一律下划线私有（见 DeskApi 的说明）。"""

    def __init__(self, shell):
        self._shell = shell

    def close_popup(self):
        self._shell.hide_popup()
        return True

    def open_main(self, history_id):
        self._shell.open_main(int(history_id))
        return True


class Shell:
    def __init__(self, webview, main_window, main_title, url, icon_path=None):
        self._wv, self._main, self._main_title, self._url = webview, main_window, main_title, url
        self._icon = icon_path
        self._popup = None
        self._popup_lock = threading.Lock()
        self._visible = False
        self._loop = None
        self._exit_cb = None
        self.error = ""
        self._set = settings.load()

    # ---------------------------------------------------------------- 启动 / 状态
    def start(self, exit_cb=None):
        self._exit_cb = exit_cb
        if not winshell.IS_WIN:
            self.error = "仅 Windows 桌面版支持全局快捷键和托盘"
            return False
        self._ensure_popup()
        self._loop = winshell.ShellLoop(self._on_hotkey, self._on_command, icon_path=self._icon, menu_labels=self._labels())
        if not self._loop.start():
            self.error = "无法创建外壳消息窗口"
            return False
        self.apply_settings(self._set)
        return True

    def _labels(self):
        hk = self._set.get("hotkey", quick.DEFAULT_HOTKEY)
        return {winshell.CMD_QUICK: f"快速询价（{hk}）", winshell.CMD_MAIN: "打开主窗口", winshell.CMD_SETTINGS: "快捷键与浮窗设置…", winshell.CMD_EXIT: "退出"}

    def apply_settings(self, cur):
        self._set = cur
        l = self._loop
        if not l:
            return self.status()
        l.update_labels(self._labels())
        l.set_tray(cur.get("quick_enabled", True))
        if cur.get("quick_enabled", True):
            try:
                l.set_hotkey(quick.parse_hotkey(cur["hotkey"]))
            except ValueError as e:
                l.hotkey_ok, l.hotkey_err = False, str(e)
        else:
            l.set_hotkey(None)
        return self.status()

    def status(self):
        l = self._loop
        if not l:
            return {"active": False, "hotkey": self._set.get("hotkey"), "hotkey_ok": False, "tray_ok": False, "message": self.error or "外壳未启动"}
        on = self._set.get("quick_enabled", True)
        if not on:
            msg = "全局快捷键与托盘已关闭"
        elif l.hotkey_ok:
            msg = f"全局快捷键 {self._set['hotkey']} 已生效（主窗口未聚焦、最小化时也能用）" + ("" if l.tray_ok else "；托盘图标创建失败")
        else:
            msg = f"快捷键 {self._set['hotkey']} 未生效：{l.hotkey_err or '未注册'}"
        return {"active": True, "hotkey": self._set.get("hotkey"), "hotkey_ok": bool(l.hotkey_ok and on), "tray_ok": bool(l.tray_ok),
                "message": msg, "popup_ready": self._popup is not None, "fired": l.fired}

    def close_to_tray(self):
        return bool(self._set.get("close_to_tray") and self._set.get("quick_enabled", True) and self._loop and self._loop.tray_ok)

    # ---------------------------------------------------------------- 回调（消息线程里：立刻丢线程）
    def _on_hotkey(self):
        threading.Thread(target=self.toggle_popup, daemon=True).start()

    def _on_command(self, c):
        def run():
            try:
                if c == winshell.CMD_QUICK:
                    self.show_popup()
                elif c == winshell.CMD_MAIN:
                    self.show_main()
                elif c == winshell.CMD_SETTINGS:
                    self.show_main()
                    self._main.evaluate_js("window.__hub&&__hub.cmd.shellSettings()")
                elif c == winshell.CMD_EXIT:
                    self.quit()
            except Exception:
                traceback.print_exc()
        threading.Thread(target=run, daemon=True).start()

    # ---------------------------------------------------------------- 浮窗
    def _ensure_popup(self):
        with self._popup_lock:
            if self._popup is None:
                self._popup = self._wv.create_window(
                    POPUP_TITLE, self._url + "quick", js_api=PopupApi(self), width=POPUP_DIP[0], height=POPUP_DIP[1], hidden=True,
                    frameless=True, easy_drag=False, on_top=True, resizable=False, shadow=True, background_color="#f3f3f3", focus=True)
            return self._popup

    def geometry(self):
        """鼠标处的浮窗几何（物理像素）。"""
        cur = winshell.cursor_pos()
        mons = winshell.monitors()
        wa = [m["work"] for m in mons]
        by = {m["work"]: m["hmon"] for m in mons}
        return quick.popup_geometry(cur, POPUP_DIP, wa, scale_of=lambda r: winshell.dpi_scale(by.get(tuple(r), 0))) | {"cursor": list(cur)}

    def show_popup(self, text=""):
        win = self._ensure_popup()
        g = self.geometry()
        win.show()
        hwnd = winshell.find_window(POPUP_TITLE)
        if hwnd:
            winshell.place_window(hwnd, g["x"], g["y"], g["w"], g["h"], activate=True)
            winshell.force_foreground(hwnd)
        self._visible = True
        win.evaluate_js("window.__quickShow&&window.__quickShow(%s)" % json.dumps({"text": text}, ensure_ascii=False))
        return g

    def hide_popup(self):
        if self._popup is not None:
            hwnd = winshell.find_window(POPUP_TITLE)
            if hwnd:
                winshell.hide_window(hwnd)
            else:
                self._popup.hide()
        self._visible = False

    def toggle_popup(self):
        hwnd = winshell.find_window(POPUP_TITLE) if self._popup else 0
        if hwnd and winshell.window_visible(hwnd):
            self.hide_popup()
        else:
            self.show_popup()

    # ---------------------------------------------------------------- 主窗口
    def show_main(self):
        m = self._main
        try:
            m.show()
            m.restore()
        except Exception:
            pass
        hwnd = winshell.find_window(self._main_title)
        if hwnd:
            winshell.force_foreground(hwnd)

    def open_main(self, history_id):
        self.hide_popup()
        self.show_main()
        self._main.evaluate_js("window.__hub&&__hub.openHistory(%d)" % int(history_id))

    def quit(self):
        try:
            if self._loop:
                self._loop.stop()
        finally:
            if self._exit_cb:
                self._exit_cb()

    def stop(self):
        """主窗口真正关闭时：撤销热键 / 托盘，销毁隐藏的浮窗（否则 webview.start 不会返回）。"""
        if self._loop:
            self._loop.stop()
        p, self._popup = self._popup, None
        if p is not None:
            try:
                p.destroy()
            except Exception:
                pass

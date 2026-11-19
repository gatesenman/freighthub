"""Windows 桌面外壳原语（纯 ctypes，不加任何第三方依赖）：
  - DPI 感知、鼠标位置、显示器工作区 / DPI 缩放
  - 窗口置顶 / 定位 / 抢前台
  - 一个隐藏窗口 + 消息循环线程：全局热键（RegisterHotKey）+ 托盘图标（Shell_NotifyIconW）+ 右键菜单
非 Windows 平台导入安全，所有入口返回「不可用」。"""
import sys
import threading

IS_WIN = sys.platform.startswith("win")

WM_DESTROY, WM_CLOSE, WM_NULL, WM_HOTKEY = 0x0002, 0x0010, 0x0000, 0x0312
WM_APP = 0x8000
WM_TRAY, WM_REHOTKEY, WM_RETRAY = WM_APP + 1, WM_APP + 2, WM_APP + 3
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
HOTKEY_ID = 0xA11C
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
HWND_TOPMOST = -1
SW_HIDE, SW_RESTORE, SW_SHOW = 0, 9, 5
CMD_QUICK, CMD_MAIN, CMD_SETTINGS, CMD_EXIT = 1, 2, 3, 4

if IS_WIN:
    import ctypes
    from ctypes import wintypes as wt

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HANDLE), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HANDLE),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
                    ("hIcon", wt.HANDLE), ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                    ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                    ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HANDLE)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM), ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", wt.POINT)]

    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wt.ATOM
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.CreateWindowExW.restype = wt.HWND
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
    user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
    user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
    user32.FindWindowW.restype = wt.HWND
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetForegroundWindow.restype = wt.HWND
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.BringWindowToTop.argtypes = [wt.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsIconic.argtypes = [wt.HWND]
    user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID]
    user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
    user32.CreatePopupMenu.restype = wt.HMENU
    user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.LoadImageW.restype = wt.HANDLE
    user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
    user32.LoadIconW.restype = wt.HANDLE
    user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(MONITORINFO)]
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.DestroyMenu.argtypes = [wt.HMENU]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HMODULE


# ------------------------------------------------------------------ 基础原语
def set_dpi_aware():
    """进程设为「每显示器 DPI 感知 v2」（必须在创建任何窗口前调用）。后面 pywebview 再调 SetProcessDPIAware 会被系统忽略。"""
    if not IS_WIN:
        return False
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:
        pass
    try:
        return ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0
    except Exception:
        pass
    try:
        return bool(user32.SetProcessDPIAware())
    except Exception:
        return False


def cursor_pos():
    pt = wt.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        raise OSError("GetCursorPos 失败")
    return pt.x, pt.y


def monitors():
    """所有显示器的工作区（不含任务栏）：[{'work': (l,t,r,b), 'full': (...), 'hmon': int}]（物理像素，副屏可为负）。"""
    out = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HANDLE, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(hmon, hdc, rc, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            w, f = mi.rcWork, mi.rcMonitor
            out.append({"work": (w.left, w.top, w.right, w.bottom), "full": (f.left, f.top, f.right, f.bottom), "hmon": int(hmon or 0)})
        return True
    user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, PROC, wt.LPARAM]
    user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
    return out


def dpi_scale(hmon):
    """显示器缩放系数（96 DPI=1.0，144=1.5…）。取不到按 1.0。"""
    try:
        x, y = wt.UINT(), wt.UINT()
        if ctypes.windll.shcore.GetDpiForMonitor(wt.HANDLE(hmon), 0, ctypes.byref(x), ctypes.byref(y)) == 0 and x.value:
            return x.value / 96.0
    except Exception:
        pass
    try:
        return user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def find_window(title):
    return user32.FindWindowW(None, title) or 0


def place_window(hwnd, x, y, w, h, activate=True):
    """物理像素：置顶 + 移动 + 缩放 + 显示。"""
    flags = SWP_SHOWWINDOW | (0 if activate else SWP_NOACTIVATE)
    return bool(user32.SetWindowPos(wt.HWND(hwnd), wt.HWND(HWND_TOPMOST), int(x), int(y), int(w), int(h), flags))


def force_foreground(hwnd):
    """全局热键触发时进程刚收到过输入，通常能直接成为前台；不行就借前台线程的输入队列。"""
    h = wt.HWND(hwnd)
    if user32.IsIconic(h):
        user32.ShowWindow(h, SW_RESTORE)
    if user32.SetForegroundWindow(h):
        return True
    fg = user32.GetForegroundWindow()
    cur = kernel32.GetCurrentThreadId()
    ftid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    try:
        if ftid and ftid != cur:
            user32.AttachThreadInput(cur, ftid, True)
        user32.BringWindowToTop(h)
        return bool(user32.SetForegroundWindow(h))
    finally:
        if ftid and ftid != cur:
            user32.AttachThreadInput(cur, ftid, False)


def hide_window(hwnd):
    user32.ShowWindow(wt.HWND(hwnd), SW_HIDE)


def window_visible(hwnd):
    return bool(hwnd) and bool(user32.IsWindowVisible(wt.HWND(hwnd)))


# ------------------------------------------------------------------ 热键 + 托盘 消息循环线程
class ShellLoop:
    """在独立线程里创建隐藏窗口并跑消息循环（RegisterHotKey 必须和接收消息的窗口在同一线程）。
    on_hotkey()：热键触发；on_command(cmd_id)：托盘菜单 / 点击。回调在消息线程里调用，应当立刻返回（耗时事务丢线程）。"""

    def __init__(self, on_hotkey, on_command, tip="货代询价助手 FreightHub", icon_path=None, menu_labels=None):
        self.on_hotkey, self.on_command, self.tip, self.icon_path = on_hotkey, on_command, tip, icon_path
        self.labels = menu_labels or {}
        self.hwnd = 0
        self.hotkey = None               # (mods, vk)
        self.hotkey_ok, self.hotkey_err = False, ""
        self.tray_ok = False
        self._ready = threading.Event()
        self._applied = threading.Event()
        self._thread = None
        self._proc = None
        self._nid = None
        self._icon = None
        self._taskbar_msg = 0
        self.fired = 0                   # 收到的热键次数（诊断用）

    # -- 对外
    def start(self, timeout=5.0):
        if not IS_WIN:
            return False
        self._thread = threading.Thread(target=self._run, name="fh-shell-loop", daemon=True)
        self._thread.start()
        return self._ready.wait(timeout) and bool(self.hwnd)

    def set_hotkey(self, mods_vk, timeout=3.0):
        """(mods, vk) 或 None（取消）。返回 (ok, err)。"""
        self.hotkey = mods_vk
        self._applied.clear()
        if not self.hwnd:
            return False, "外壳未启动"
        user32.PostMessageW(wt.HWND(self.hwnd), WM_REHOTKEY, 0, 0)
        if not self._applied.wait(timeout):
            return False, "注册快捷键超时"
        return self.hotkey_ok, self.hotkey_err

    def set_tray(self, on):
        self._want_tray = bool(on)
        if self.hwnd:
            user32.PostMessageW(wt.HWND(self.hwnd), WM_RETRAY, 0, 0)

    def update_labels(self, labels):
        self.labels = labels

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(wt.HWND(self.hwnd), WM_CLOSE, 0, 0)

    def inject_hotkey(self):
        """测试用：像按下热键一样给隐藏窗口投一条 WM_HOTKEY。"""
        user32.PostMessageW(wt.HWND(self.hwnd), WM_HOTKEY, HOTKEY_ID, 0)

    # -- 消息线程内
    _want_tray = True

    def _run(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            self._proc = WNDPROC(self._wndproc)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.lpszClassName = "FreightHubShellWnd"
            user32.RegisterClassW(ctypes.byref(wc))
            self.hwnd = int(user32.CreateWindowExW(0, "FreightHubShellWnd", "FreightHubShell", 0, 0, 0, 0, 0, None, None, hinst, None) or 0)
            self._taskbar_msg = user32.RegisterWindowMessageW("TaskbarCreated")
            self._load_icon()
            self._apply_tray()
        finally:
            self._ready.set()
        if not self.hwnd:
            return
        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _load_icon(self):
        h = None
        if self.icon_path:
            try:
                sm = user32.GetSystemMetrics(49)       # SM_CXSMICON
                h = user32.LoadImageW(None, self.icon_path, 1, sm, sm, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
            except Exception:
                h = None
        self._icon = h or user32.LoadIconW(None, ctypes.cast(ctypes.c_void_p(32512), wt.LPCWSTR))   # IDI_APPLICATION

    def _apply_tray(self):
        want = self._want_tray
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = wt.HWND(self.hwnd)
        nid.uID = 1
        if want:
            nid.uFlags = 0x1 | 0x2 | 0x4            # NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = self._icon
            nid.szTip = self.tip[:127]
            ok = shell32.Shell_NotifyIconW(0, ctypes.byref(nid))     # NIM_ADD
            if not ok:
                ok = shell32.Shell_NotifyIconW(1, ctypes.byref(nid))  # 已存在 → NIM_MODIFY
            self.tray_ok = bool(ok)
        else:
            shell32.Shell_NotifyIconW(2, ctypes.byref(nid))           # NIM_DELETE
            self.tray_ok = False

    def _apply_hotkey(self):
        user32.UnregisterHotKey(wt.HWND(self.hwnd), HOTKEY_ID)
        self.hotkey_ok, self.hotkey_err = False, ""
        if self.hotkey:
            mods, vk = self.hotkey
            if user32.RegisterHotKey(wt.HWND(self.hwnd), HOTKEY_ID, mods | MOD_NOREPEAT, vk):
                self.hotkey_ok = True
            else:
                err = ctypes.get_last_error()
                self.hotkey_err = ("这个快捷键已被其他程序占用，请换一个组合键" if err == ERROR_HOTKEY_ALREADY_REGISTERED
                                   else f"注册快捷键失败（错误码 {err}）")
        self._applied.set()

    def _menu(self):
        m = user32.CreatePopupMenu()
        L = self.labels
        user32.AppendMenuW(m, 0, CMD_QUICK, L.get(CMD_QUICK, "快速询价"))
        user32.AppendMenuW(m, 0, CMD_MAIN, L.get(CMD_MAIN, "打开主窗口"))
        user32.AppendMenuW(m, 0, CMD_SETTINGS, L.get(CMD_SETTINGS, "快捷键与浮窗设置…"))
        user32.AppendMenuW(m, 0x800, 0, None)       # MF_SEPARATOR
        user32.AppendMenuW(m, 0, CMD_EXIT, L.get(CMD_EXIT, "退出"))
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(wt.HWND(self.hwnd))     # 否则点别处菜单不会消失
        cmd = user32.TrackPopupMenu(m, 0x0100 | 0x0002, pt.x, pt.y, 0, wt.HWND(self.hwnd), None)   # TPM_RETURNCMD | TPM_RIGHTBUTTON
        user32.PostMessageW(wt.HWND(self.hwnd), WM_NULL, 0, 0)
        user32.DestroyMenu(m)
        return int(cmd or 0)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_HOTKEY and wparam == HOTKEY_ID:
                self.fired += 1
                self.on_hotkey()
                return 0
            if msg == WM_TRAY:
                ev = lparam & 0xFFFF
                if ev == WM_LBUTTONUP:
                    self.on_command(CMD_QUICK)
                elif ev == WM_LBUTTONDBLCLK:
                    self.on_command(CMD_MAIN)
                elif ev == WM_RBUTTONUP:
                    c = self._menu()
                    if c:
                        self.on_command(c)
                return 0
            if msg == WM_REHOTKEY:
                self._apply_hotkey()
                return 0
            if msg == WM_RETRAY:
                self._apply_tray()
                return 0
            if self._taskbar_msg and msg == self._taskbar_msg:      # 资源管理器重启后重新放回托盘图标
                self._apply_tray()
                return 0
            if msg == WM_CLOSE:
                user32.UnregisterHotKey(wt.HWND(hwnd), HOTKEY_ID)
                self._want_tray = False
                self._apply_tray()
                user32.DestroyWindow(wt.HWND(hwnd))
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            import traceback
            traceback.print_exc()
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

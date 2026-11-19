"""在 Wine（或真 Windows）下冒烟 app/winshell.py 的 Win32 原语：DPI / 鼠标位置 / 显示器 / 置顶定位 / 热键 / 托盘。
用法（Wine）：wine64 python\\python.exe scripts\\wine_smoke_shell.py   —— 全部打印 OK/FAIL，末行 RESULT。
注意：Wine 的合成环境不等于真实 Windows 10/11：热键的「真实按键」、托盘的真实显示、WebView2 窗口摆放都无法在这里确认。"""
import ctypes
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import quick, winshell as ws  # noqa: E402

res = {}


def check(name, ok, info=""):
    res[name] = bool(ok)
    print(("OK   " if ok else "FAIL ") + name + (("  " + str(info)) if info else ""), flush=True)


check("is_windows", ws.IS_WIN, sys.platform)
check("set_dpi_aware", ws.set_dpi_aware() in (True, False))      # Wine 里可能返回 False，只要不崩
x, y = ws.cursor_pos()
check("cursor_pos", isinstance(x, int) and isinstance(y, int), (x, y))
mons = ws.monitors()
check("monitors", len(mons) >= 1 and mons[0]["work"][2] > mons[0]["work"][0], [m["work"] for m in mons])
sc = ws.dpi_scale(mons[0]["hmon"])
check("dpi_scale", 0.5 <= sc <= 4, sc)
g = quick.popup_geometry((x, y), (460, 540), [m["work"] for m in mons], scale_of=lambda r: sc)
w0 = mons[0]["work"]
check("popup_geometry_inside_work_area", w0[0] <= g["x"] and g["x"] + g["w"] <= w0[2] and w0[1] <= g["y"] and g["y"] + g["h"] <= w0[3], g)

# 原生窗口：置顶 + 定位 + 找窗口 + 隐藏
u = ws.user32
hw = u.CreateWindowExW(0, "STATIC", "FHSmokeWin", 0x10000000 | 0x00C00000, 100, 100, 300, 200, None, None, ws.kernel32.GetModuleHandleW(None), None)
check("create_native_window", bool(hw), hw)
check("find_window_by_title", ws.find_window("FHSmokeWin") == hw)
check("place_window", ws.place_window(hw, 220, 140, 460, 540, activate=False))
rc = ws.wt.RECT()
u.GetWindowRect(ws.wt.HWND(hw), ctypes.byref(rc))
check("window_rect_matches", (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top) == (220, 140, 460, 540), (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top))
ex = u.GetWindowLongW(ws.wt.HWND(hw), -20)
check("window_is_topmost", bool(ex & 0x8), hex(ex))
check("window_visible", ws.window_visible(hw))
ws.hide_window(hw)
check("hide_window", not ws.window_visible(hw))
u.DestroyWindow(ws.wt.HWND(hw))

# 热键 + 托盘消息线程
hits, cmds = [], []
loop = ws.ShellLoop(lambda: hits.append(time.time()), lambda c: cmds.append(c), icon_path=None)
check("shellloop_start", loop.start(), loop.hwnd)
ok, err = loop.set_hotkey(quick.parse_hotkey("Ctrl+Alt+Q"))
check("register_hotkey_ctrl_alt_q", ok, err)
loop.inject_hotkey()
for _ in range(50):
    if hits:
        break
    time.sleep(0.1)
check("wm_hotkey_reaches_callback", len(hits) == 1 and loop.fired == 1, loop.fired)
ok2, err2 = loop.set_hotkey(quick.parse_hotkey("Ctrl+Shift+K"))
check("rebind_hotkey", ok2, err2)
ok3, err3 = loop.set_hotkey(None)
check("unregister_hotkey", ok3 is False and err3 == "")
loop.set_tray(True)
time.sleep(0.5)
check("tray_icon_added", loop.tray_ok)
loop.set_tray(False)
time.sleep(0.3)
check("tray_icon_removed", not loop.tray_ok)
# 托盘左键单击 / 双击消息 → 命令
u.PostMessageW(ws.wt.HWND(loop.hwnd), ws.WM_TRAY, 1, ws.WM_LBUTTONUP)
u.PostMessageW(ws.wt.HWND(loop.hwnd), ws.WM_TRAY, 1, ws.WM_LBUTTONDBLCLK)
time.sleep(0.5)
check("tray_clicks_map_to_commands", cmds == [ws.CMD_QUICK, ws.CMD_MAIN], cmds)
loop.stop()
time.sleep(0.5)
check("shellloop_stop_destroys_window", not u.IsWindow(ws.wt.HWND(loop.hwnd)))
print("RESULT " + json.dumps({"passed": sum(res.values()), "total": len(res), "failed": [k for k, v in res.items() if not v]}, ensure_ascii=False))
sys.exit(0 if all(res.values()) else 1)

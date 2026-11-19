"""端到端测试：启动真实服务 → 用真实平台接口查询 → 在 UI 中验证并截图。
运行：python tests/e2e_test.py   （需要 data/config.json 里已配置云途、中航账号）"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "screenshots"
SHOTS.mkdir(exist_ok=True)
PORT = 8766
# 隔离数据目录：只复制云途/中航账号及环洋 API 授权（若开发配置里有）用于真实联调，其余平台（含雨桐/顺丰 API 密钥）不进入 e2e，也不会被测试改动
import json as _j, tempfile as _t  # noqa: E402
DATA = Path(_t.mkdtemp(prefix="fh_e2e_"))
(DATA / "sessions").mkdir()
try:
    _src = _j.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))
    (DATA / "config.json").write_text(_j.dumps({"accounts": {k: v for k, v in _src.get("accounts", {}).items() if k in ("yuntu", "zhonghang") or (k == "huanyang" and v.get("app_key"))}}, ensure_ascii=False), encoding="utf-8")
except Exception:
    pass
BASE = f"http://127.0.0.1:{PORT}"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def wait_up():
    for _ in range(40):
        try:
            requests.get(BASE + "/api/meta", timeout=1)
            return
        except Exception:
            time.sleep(0.5)
    raise SystemExit("服务未启动")


def quote(country, weight, **kw):
    t = time.time()
    r = requests.post(BASE + "/api/quote", json={"country": country, "weight": weight, **kw}, timeout=300)
    r.raise_for_status()
    d = r.json()
    d["_secs"] = time.time() - t
    return d


def main():
    env = dict(os.environ, FREIGHT_HUB_NO_UI="1", FREIGHT_HUB_PORT=str(PORT), FREIGHT_HUB_DATA=str(DATA))
    srv = subprocess.Popen([sys.executable, "run.py"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        wait_up()
        # ---- 回归（1.2.3）：js_api 不能有公开的非函数属性，否则 pywebview 会递归遍历 window.native 导致卡死空白 ----
        sys.path.insert(0, str(ROOT))
        import importlib.util
        spec = importlib.util.spec_from_file_location("fh_run", ROOT / "run.py")
        runmod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runmod)
        api = runmod.DeskApi()
        api._window = object()
        pub = [n for n in dir(api) if not n.startswith("_")]
        check("js_api 公开成员全是方法（window 等对象为下划线私有）", pub and all(callable(getattr(api, n)) for n in pub) and not hasattr(api, "window"), ",".join(pub))
        check("启动先显示 loading 页（含错误回显函数）", "正在启动" in runmod.LOADING_HTML and "setErr" in runmod.LOADING_HTML)
        # ---- API：平台状态 ----
        pl = {p["id"]: p for p in requests.get(BASE + "/api/platforms").json()}
        check("7 个平台都已注册", len(pl) == 7, ",".join(pl))
        check("顺丰/雨桐为待验证占位", pl["shunfeng"]["state"] == "pending" and pl["yutong"]["state"] == "pending")
        HY = bool(json.loads((DATA / "config.json").read_text(encoding="utf-8")).get("accounts", {}).get("huanyang"))
        check("环洋：配置了 API 授权则为 API 模式，否则待配置", (pl["huanyang"]["auth_mode"] == "api") if HY else pl["huanyang"]["state"] in ("no_account", "idle", "ready", "error"),
              pl["huanyang"]["state"])
        check("密码不会通过接口回传", all(not f["value"] for p in pl.values() for f in p["fields"] if f["secret"]))

        # ---- API：真实查询 美国 1kg（先删会话，验证自动重登）----
        for pid in ("yuntu", "zhonghang"):
            f = DATA / "sessions" / f"{pid}.json"
            if f.exists():
                f.unlink()
        us = quote("US", 1)
        st = {p["id"]: p for p in us["platforms"]}
        for pid, minimum in (("yuntu", 20), ("zhonghang", 20), ("xinhang", 20)):
            check(f"美国 1kg · {st[pid]['name']} 返回渠道", st[pid]["state"] == "ok" and st[pid]["count"] >= minimum,
                  f"{st[pid]['count']} 条 {st[pid]['elapsed_ms']}ms {st[pid]['message']}")
        if HY:
            check("美国 1kg · 环洋(API 模式)返回渠道", st["huanyang"]["state"] == "ok" and st["huanyang"]["count"] >= 10,
                  f"{st['huanyang']['count']} 条 {st['huanyang']['elapsed_ms']}ms {st['huanyang']['message']}")
        check("美国 1kg · 汇总结果按价格升序", all(a["total"] <= b["total"] for a, b in zip(us["rows"], us["rows"][1:])), f"共 {len(us['rows'])} 条")
        check("美国 1kg · 并发总耗时小于各平台耗时之和", us["_secs"] * 1000 < sum(p["elapsed_ms"] for p in us["platforms"]) + 500, f"{us['_secs']:.1f}s")

        # ---- 英国 0.5kg ----
        gb = quote("GB", 0.5)
        st = {p["id"]: p for p in gb["platforms"]}
        for pid in ("yuntu", "zhonghang", "xinhang"):
            check(f"英国 0.5kg · {st[pid]['name']} 返回渠道", st[pid]["state"] == "ok" and st[pid]["count"] > 0,
                  f"{st[pid]['count']} 条 {st[pid]['message']}")
        if HY:
            check("英国 0.5kg · 环洋(API 模式)返回渠道", st["huanyang"]["state"] == "ok" and st["huanyang"]["count"] >= 10,
                  f"{st['huanyang']['count']} 条 {st['huanyang']['message']}")
        # ---- 德国 2kg 带邮编 ----
        de = quote("DE", 2, postcode="10115")
        st = {p["id"]: p for p in de["platforms"]}
        check("德国 2kg 带邮编 · 至少两家成功", sum(1 for p in st.values() if p["state"] == "ok") >= 2,
              ",".join(f"{p['name']}:{p['count']}" for p in st.values() if p["state"] == "ok"))

        # ---- 会话过期自动重登（把中航 token 改坏）----
        f = DATA / "sessions" / "zhonghang.json"
        s = json.loads(f.read_text())
        s["token"] = "broken-token"
        f.write_text(json.dumps(s))
        again = quote("US", 1, platforms=["zhonghang"])
        check("中航 token 损坏后自动重登并成功", again["platforms"][0]["state"] == "ok" and again["platforms"][0]["count"] > 0)

        # ---- 历史与导出 ----
        hist = requests.get(BASE + "/api/history").json()
        check("查询历史已记录", len(hist) >= 4, f"{len(hist)} 条")
        csv = requests.get(f"{BASE}/api/history/{us['id']}/csv")
        check("CSV 导出", csv.status_code == 200 and "平台" in csv.text and csv.text.count("\n") > 50)

        # ---- 输入校验 ----
        r = requests.post(BASE + "/api/quote", json={"country": "", "weight": 1})
        check("空国家被拒绝", r.status_code == 400)

        # ---- UI（桌面软件风格）----
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            ctx = b.new_context(viewport={"width": 1360, "height": 860}, device_scale_factor=1.5, locale="zh-CN", accept_downloads=True)
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            pg.goto(BASE + "/?frame=1", wait_until="networkidle")
            pg.wait_for_selector(".panel.active .platlist input")
            pg.wait_for_function("!document.querySelector('#boot')", timeout=5000)
            check("UI 启动 loading 遮罩在加载完成后移除", pg.locator("#boot").count() == 0)
            pg.screenshot(path=str(SHOTS / "01-主界面-初始.png"))
            check("UI 标题栏与窗口按钮(最小化/最大化/关闭)", pg.locator("#winCtl button").count() == 3 and pg.locator("#winCtl").is_visible())
            check("UI 菜单栏 5 项", pg.locator(".mb-item").all_inner_texts() == ["文件", "查询", "平台", "工具", "帮助"])
            check("UI 图标工具栏", pg.locator("#toolbar .tb").count() >= 9 and pg.locator("#toolbar .tb svg").count() >= 9)
            check("UI 左侧树形导航", pg.locator("#tree .tn").count() >= 12 and pg.locator('#tree .tn.grp').count() == 4)
            check("UI 状态栏显示 7 个平台指示灯", pg.locator("#sbPlats .dot").count() == 7)
            check("UI 无浏览器地址栏元素/页面为无边框布局", pg.locator("#titlebar").is_visible() and pg.locator("#menubar").is_visible())

            pg.click('.mb-item:has-text("文件")')
            check("UI 文件菜单弹出", pg.locator("#ctxmenu .mi").count() >= 5)
            pg.screenshot(path=str(SHOTS / "02-菜单栏-文件菜单.png"))
            pg.keyboard.press("Escape")
            pg.click('.mb-item:has-text("平台")')
            check("UI 平台菜单列出 7 个平台状态", pg.locator("#ctxmenu .mi .mdot").count() == 7)
            pg.keyboard.press("Escape")

            pg.fill(".panel.active .inCountry", "美国")
            pg.fill(".panel.active .inWeight", "1")
            pg.keyboard.press("F5")
            pg.wait_for_selector(".panel.active tbody tr", timeout=240000)
            time.sleep(0.6)
            rows = pg.locator(".panel.active tbody tr").count()
            check("UI 美国 1kg 数据网格显示结果", rows >= 100, f"{rows} 行")
            plats_in_table = set(pg.locator(".panel.active tbody .pb").all_inner_texts())
            check("UI 结果同时包含云途/中航/欣航" + ("/环洋" if HY else ""), ({"云途", "中航", "欣航"} | ({"环洋"} if HY else set())) <= plats_in_table, ",".join(sorted(plats_in_table)))
            check("UI 标签页标题更新为查询条件", "美国 1kg" in pg.locator(".tab.active .tt").inner_text())
            check("UI 状态栏记录数/耗时", f"记录：{121 + (20 if HY else 0)}" in pg.locator("#sbCount").inner_text() and "耗时：" in pg.locator("#sbTime").inner_text(),
                  pg.locator("#sbCount").inner_text() + " " + pg.locator("#sbTime").inner_text())
            pg.locator(".panel.active tbody tr").nth(2).click()
            check("UI 行选中高亮 + 明细面板", pg.locator(".panel.active tbody tr.sel").count() == 1 and "总价" in pg.locator(".panel.active .detail").inner_text())
            pg.screenshot(path=str(SHOTS / "03-询价结果-美国1kg.png"))

            # 排序
            pg.click('.panel.active th:has-text("总价")')  # 已是升序 -> 降序
            desc = float(pg.locator(".panel.active tbody tr td.price").first.inner_text().replace(",", ""))
            pg.click('.panel.active th:has-text("总价")')
            asc = float(pg.locator(".panel.active tbody tr td.price").first.inner_text().replace(",", ""))
            check("UI 列头点击排序(升/降)", desc > asc, f"{desc} > {asc}")
            pg.click('.panel.active th:has-text("时效")')
            check("UI 时效列可排序", pg.locator(".panel.active th:has-text('时效') .ar").count() == 1)
            # 列宽拖拽
            th = pg.locator('.panel.active th:has-text("渠道")')
            w0 = th.bounding_box()["width"]
            rz = th.locator(".rz").bounding_box()
            pg.mouse.move(rz["x"] + 3, rz["y"] + 8)
            pg.mouse.down()
            pg.mouse.move(rz["x"] + 83, rz["y"] + 8, steps=5)
            pg.mouse.up()
            w1 = th.bounding_box()["width"]
            check("UI 列宽可拖拽调整", w1 > w0 + 50, f"{w0:.0f} -> {w1:.0f}")
            # 多选 + 右键菜单 + 复制 + 导出
            pg.click('.panel.active th:has-text("总价")')
            pg.locator(".panel.active tbody tr").nth(1).click()
            pg.locator(".panel.active tbody tr").nth(4).click(modifiers=["Shift"])
            check("UI Shift 连选 4 行", pg.locator(".panel.active tbody tr.sel").count() == 4)
            pg.locator(".panel.active tbody tr").nth(3).click(button="right")
            items = pg.locator("#ctxmenu .mi").all_inner_texts()
            check("UI 右键菜单含复制/导出", any("复制选中行" in i for i in items) and any("导出" in i for i in items), " | ".join(i.replace("\n", " ") for i in items)[:120])
            pg.screenshot(path=str(SHOTS / "04-右键菜单-多选行.png"))
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
            pg.locator("#ctxmenu .mi:has-text('复制选中行')").click()
            clip = pg.evaluate("navigator.clipboard.readText()")
            check("UI 复制选中行到剪贴板", clip.count("\n") == 3 and "\t" in clip, clip.split("\n")[0][:60])
            with pg.expect_download() as dl:
                pg.click('#toolbar .tb[data-cmd="export"]')
            path = dl.value.path()
            txt = open(path, encoding="utf-8-sig").read()
            check("UI 导出 CSV(工具栏)", txt.startswith("平台,渠道") and txt.count("\n") >= 100, f"{dl.value.suggested_filename} {txt.count(chr(10))} 行")
            # 筛选
            pg.fill(".panel.active .fKey", "FEDEX")
            n_f = pg.locator(".panel.active tbody tr").count()
            check("UI 关键词筛选", 0 < n_f < rows, f"{n_f} 行")
            pg.fill(".panel.active .fKey", "")
            pg.fill(".panel.active .fMaxP", "200")
            maxp = max(float(x.replace(",", "")) for x in pg.locator(".panel.active tbody td.price").all_inner_texts())
            check("UI 最高价筛选", maxp <= 200, f"最高 {maxp}")
            pg.fill(".panel.active .fMaxP", "")

            # 多标签页
            pg.keyboard.press("Control+t")
            check("UI Ctrl+T 新建第二个查询标签", pg.locator(".tab").count() == 2)
            pg.fill(".panel.active .inCountry", "GB")
            pg.fill(".panel.active .inWeight", "0.5")
            pg.click(".panel.active .bRun")
            pg.wait_for_selector(".panel.active tbody tr", timeout=240000)
            time.sleep(0.6)
            gb_plats = set(pg.locator(".panel.active tbody .pb").all_inner_texts())
            check("UI 英国 0.5kg 各家都有结果(第二个标签)", ({"云途", "中航", "欣航"} | ({"环洋"} if HY else set())) <= gb_plats, ",".join(sorted(gb_plats)))
            pg.screenshot(path=str(SHOTS / "05-多标签-英国0.5kg.png"))
            pg.locator(".tab").first.click()
            check("UI 切回第一个标签结果仍在", pg.locator(".panel.active tbody tr").count() == rows)

            # 账号页
            pg.click('#toolbar .tb[data-cmd="accounts"]')
            pg.wait_for_selector(".panel.active .acc-form")
            check("UI 账号页平台网格 7 行", pg.locator(".panel.active tbody tr").count() == 7)
            pw = pg.locator('.panel.active input[type="password"]')
            check("UI 账号页密码框不回显明文", all(pw.nth(i).input_value() == "" for i in range(pw.count())))
            pg.evaluate("document.querySelectorAll('input[data-k=username]').forEach(i=>{i.value=i.value?i.value.slice(0,2)+'*'.repeat(6)+i.value.slice(-2):''})")
            pg.screenshot(path=str(SHOTS / "06-平台账号页.png"))
            pg.locator('.panel.active tbody tr:has-text("顺丰")').click()
            pg.click(".panel.active .aTest")
            pg.wait_for_function("document.querySelector('.panel.active .log').value.includes('顺丰')", timeout=15000)
            time.sleep(0.5)
            check("UI 顺丰登录模式测试连接提示暂未实现并引导切换 API", "暂未实现" in pg.locator(".panel.active .log").input_value())
            pg.screenshot(path=str(SHOTS / "07-占位平台状态.png"))

            # ---- 两种授权模式 UI（用顺丰/雨桐演示，测完切回登录模式，不污染真实账号配置）----
            check("UI 账号页有授权模式切换（登录账号授权 / 开放平台API授权）",
                  pg.locator(".panel.active .aMode").count() == 2 and "登录账号授权" in pg.locator(".panel.active .modesw").inner_text()
                  and "开放平台API授权" in pg.locator(".panel.active .modesw").inner_text())
            check("UI 账号页网格有授权模式列", pg.locator(".panel.active th:has-text('授权模式')").count() == 1)
            pg.locator('.panel.active .aMode[value="api"]').check()
            pg.wait_for_selector('.panel.active input[data-k="aes_key"]')
            keys = pg.locator(".panel.active .acc-form [data-k]").evaluate_all("els=>els.map(e=>e.dataset.k)")
            check("UI 顺丰 API 模式显示 sf.global 字段（APP Key/Secret/AES/customerCode）与丰桥备选字段",
                  {"app_key", "app_secret", "aes_key", "customer_code", "env", "api_kind", "partner_id", "check_word", "auth"} <= set(keys), ",".join(keys))
            check("UI 顺丰 API 模式字段含下拉选择框", pg.locator(".panel.active .acc-form select").count() == 3)
            check("UI API 模式显示文档状态徽标与文档链接", "有公开文档" in pg.locator(".panel.active .docbadge").inner_text() and pg.locator(".panel.active .docbadge a").count() == 1)
            check("UI API 模式 secret 字段为密码框", pg.locator('.panel.active input[data-k="aes_key"]').get_attribute("type") == "password")
            pg.screenshot(path=str(SHOTS / "10-开放平台API授权模式.png"))
            pg.click(".panel.active .aTest")
            pg.wait_for_function("document.querySelector('.panel.active .log').value.includes('开放平台API')", timeout=15000)
            time.sleep(0.8)
            check("UI 开放平台API模式测试连接：未填凭据给出明确提示", "尚未填写" in pg.locator(".panel.active .log").input_value(), pg.locator(".panel.active .log").input_value().strip().split("\n")[-1][:60])
            pg.locator(".panel.active tbody tr", has_text="雨桐").click()
            pg.locator('.panel.active .aMode[value="api"]').check()
            pg.wait_for_selector('.panel.active input[data-k="app_key"]')
            check("UI 雨桐 API 模式：有公开文档 + 运价试算可用（无“仅连接测试”提示）", "有公开文档" in pg.locator(".panel.active .docbadge").inner_text() and "仅连接测试" not in pg.locator(".panel.active .docbadge").inner_text())
            pg.screenshot(path=str(SHOTS / "11-API模式-需申请文档.png"))
            pg.locator(".panel.active tbody tr", has_text="欣航").click()
            time.sleep(0.4)
            check("UI 欣航：无授权模式切换与账号字段，显示「公开网站，无需授权」",
                  not pg.locator(".panel.active .aMode").first.is_visible() and "公开网站，无需授权" in pg.locator(".panel.active .fixedmode").inner_text()
                  and pg.locator(".panel.active .acc-form [data-k]").count() == 0)
            pg.screenshot(path=str(SHOTS / "12-欣航-公开网站无需授权.png"))
            pg.click(".panel.active .aTest")
            pg.wait_for_function("/欣航：(成功|未通过|失败)/.test(document.querySelector('.panel.active .log').value)", timeout=30000)
            time.sleep(0.8)
            check("UI 欣航：测试连接成功（无需登录）", "无需登录" in pg.locator(".panel.active .log").input_value(), pg.locator(".panel.active .log").input_value().strip().split("\n")[-1][:60])
            for nm in ("雨桐", "顺丰"):  # 切回登录模式并保存，恢复默认
                pg.locator(".panel.active tbody tr", has_text=nm).click()
                pg.locator('.panel.active .aMode[value="login"]').check()
                pg.click(".panel.active .aSave")
                time.sleep(0.5)
            modes = {p_["id"]: p_["auth_mode"] for p_ in requests.get(BASE + "/api/platforms").json()}
            check("UI 测完已恢复登录模式", modes["shunfeng"] == "login" and modes["yutong"] == "login", str(modes))

            pg.click('#toolbar .tb[data-cmd="history"]')
            pg.wait_for_selector(".panel.active tbody tr")
            check("UI 历史页有记录", pg.locator(".panel.active tbody tr").count() >= 4, f"{pg.locator('.panel.active tbody tr').count()} 条")
            pg.screenshot(path=str(SHOTS / "08-查询历史页.png"))
            pg.locator(".panel.active tbody tr").nth(1).dblclick()
            pg.wait_for_selector(".panel.active .pstrip .pcell")
            check("UI 历史记录双击回看结果", pg.locator(".panel.active tbody tr").count() > 0)

            pg.click('#toolbar .tb[data-cmd="about"]')
            pg.screenshot(path=str(SHOTS / "09-帮助与关于.png"))
            pg.keyboard.press("Control+w")
            check("UI Ctrl+W 关闭标签页", pg.locator(".tab").count() >= 4)
            check("页面无 JS 报错", not errs, "; ".join(errs)[:200])
            b.close()
    finally:
        srv.terminate()
        try:
            srv.wait(5)
        except Exception:
            srv.kill()

    # ---- 单平台异常不影响其他平台（进程内模拟）----
    os.environ["FREIGHT_HUB_DATA"] = str(DATA)
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient
    from app import main as m

    def boom(q):
        raise RuntimeError("模拟平台崩溃")

    m.registry["zhonghang"].quote = boom
    c = TestClient(m.app)
    r = c.post("/api/quote", json={"country": "GB", "weight": 0.5, "platforms": ["zhonghang", "xinhang"]}).json()
    stt = {p["id"]: p for p in r["platforms"]}
    check("单平台异常不影响其他平台", stt["zhonghang"]["state"] == "error" and stt["xinhang"]["state"] == "ok" and len(r["rows"]) > 0,
          f"中航={stt['zhonghang']['message']}; 欣航={stt['xinhang']['count']}条")

    # ---- 开放平台 API 模式测试（独立数据目录子进程）----
    pr = subprocess.run([sys.executable, str(ROOT / "tests" / "api_mode_test.py")], cwd=ROOT, capture_output=True, text=True, timeout=300)
    sub = json.loads((ROOT / "tests" / "api_mode_result.json").read_text(encoding="utf-8"))
    for r_ in sub:
        check("[API模式] " + r_["name"], r_["pass"], r_["detail"])

    # ---- 账号批量导入/导出测试（独立数据目录子进程，含 UI 流程）----
    pr = subprocess.run([sys.executable, str(ROOT / "tests" / "import_test.py")], cwd=ROOT, capture_output=True, text=True, timeout=300)
    sub = json.loads((ROOT / "tests" / "import_result.json").read_text(encoding="utf-8"))
    for r_ in sub:
        check("[账号导入] " + r_["name"], r_["pass"], r_["detail"])

    ok = sum(1 for _, c_, _ in results if c_)
    print(f"\n结果：{ok}/{len(results)} 通过")
    out = ROOT / "tests" / "last_result.json"
    out.write_text(json.dumps([{"name": n, "pass": c_, "detail": d} for n, c_, d in results], ensure_ascii=False, indent=1), encoding="utf-8")
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()

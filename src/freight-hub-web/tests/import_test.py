"""账号批量导入/导出测试（独立临时数据目录；全部使用明显的假数据，不碰真实账号，不访问任何平台）。
覆盖：xlsx 读写、模板（xlsx/csv）、预览/应用、旧表格式、无表头、各平台两种授权方式的字段映射、
欣航跳过、打码/空白不覆盖、错误行、重复行、密钥不回显、导出（不导出/打码）、批次过期、UI 流程（Playwright）。
输出 PASS/FAIL 行并写入 tests/import_result.json。"""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = tempfile.mkdtemp(prefix="fh_imp_")
os.environ["FREIGHT_HUB_DATA"] = tmp
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import importer, main as m  # noqa: E402
from app.xlsx import read_xlsx, write_xlsx  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "pass": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def b64(b):
    return base64.b64encode(b).decode()


# 假数据（任何地方都不是真实凭据）
S1, S2, S3, S4, S5 = "FAKE-SECRET-ALPHA-1", "FAKE-SECRET-BETA-22", "FAKE-AES-GAMMA-333", "FAKE-TOKEN-DELTA-4444", "FAKE-PWD-EPSILON-5"
ALLFAKE = [S1, S2, S3, S4, S5, "FAKE-PW-LOGIN-A", "FAKE-PW-LOGIN-B", "FAKE-APPSEC-YT"]

c = TestClient(m.app)


def xl(rows, sheets=None):
    return write_xlsx(sheets or [{"name": "Sheet1", "rows": rows}])


def prev(rows=None, raw=None, name="t.xlsx"):
    data = raw if raw is not None else xl(rows)
    r = c.post("/api/accounts/import/preview", json={"filename": name, "data": b64(data)})
    return r.status_code, (r.json() if r.status_code == 200 else r.json())


def apply(batch, rows=None):
    r = c.post("/api/accounts/import/apply", json={"batch": batch, "rows": rows})
    return r.status_code, r.json()


def acct(pid):
    return m.store.get_account(pid)


def run():
    # ---- xlsx 读写 ----
    rows = [["平台", "说明"], ["云途 & <b>", "第一行\n第二行 \"引号\""], ["中文", "123"]]
    back = dict(read_xlsx(xl(rows)))
    check("xlsx 读写往返（中文/特殊字符/换行）", back.get("Sheet1") == rows, str(back)[:80])
    # ---- 模板 ----
    r = c.get("/api/accounts/template")
    sheets = dict(read_xlsx(r.content))
    check("模板 xlsx 可下载且为 3 个工作表", r.status_code == 200 and list(sheets) == ["账号配置", "字段说明", "示例（假数据）"], str(list(sheets)))
    check("模板表头含 平台/授权方式/账号/密码/appKey/appSecret/AES秘钥/客户编码/对接URL/备注",
          all(h in sheets["账号配置"][0] for h in ["平台", "授权方式", "账号", "密码", "appKey", "appSecret", "AES秘钥", "客户编码", "对接URL", "备注"]), str(sheets["账号配置"][0]))
    check("模板文件头是 zip(xlsx) 且文件名为 账号导入模板.xlsx", r.content[:2] == b"PK" and "%E8%B4%A6%E5%8F%B7%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.xlsx" in r.headers["content-disposition"])
    desc = "\n".join(" ".join(x) for x in sheets["字段说明"])
    check("字段说明页覆盖 6 个平台和两种授权方式", all(n in desc for n in ["云途", "中航", "环洋", "燕文", "顺丰", "雨桐"]) and "API" in desc and "登录" in desc)
    check("模板数据区无真实账号（账号配置页仅表头）", len(sheets["账号配置"]) <= 1 or all(not any(x) for x in sheets["账号配置"][1:]))
    rc = c.get("/api/accounts/template?fmt=csv")
    txt = importer.decode_csv(rc.content)
    check("模板 CSV 可下载并能被解码（UTF-8 BOM）", rc.status_code == 200 and rc.content[:3] == b"\xef\xbb\xbf" and "平台" in txt[0] if isinstance(txt, list) else "平台" in txt)
    code, pv = prev(raw=r.content)
    check("空模板导入 → 400 提示没有数据", code == 400, str(pv)[:60])
    code, pv = prev(raw=b"")
    check("空文件导入 → 400", code == 400)

    # ---- 旧表格式 平台名/账号/密码/网址 ----
    old = [["平台名", "账号", "密码", "网址"],
           ["云途", "user-yt@example.invalid", "FAKE-PW-LOGIN-A", "https://example.invalid/yt"],
           ["中航", "user-zh@example.invalid", "FAKE-PW-LOGIN-B", "https://example.invalid/zh"],
           ["欣航", "", "", "https://example.invalid/xh"],
           ["不存在的平台", "x", "y", ""]]
    code, pv = prev(old)
    acts = [x["action"] for x in pv["rows"]]
    check("旧表格式：云途/中航 → 新增，欣航 → 跳过，未知平台 → 错误", acts == ["add", "add", "skip", "error"], str(acts))
    check("旧表格式默认按登录模式", pv["rows"][0]["mode"] == "login" and pv["rows"][1]["mode"] == "login")
    pj = json.dumps(pv, ensure_ascii=False)
    check("预览响应不含任何密码", not any(s in pj for s in ALLFAKE))
    check("预览阶段不写入配置（确认前不落盘）", acct("yuntu") == {} and acct("zhonghang") == {})
    code, ap = apply(pv["batch"])
    check("确认后写入 云途/中航 登录账号", code == 200 and acct("yuntu").get("username") == "user-yt@example.invalid" and acct("zhonghang").get("password") == "FAKE-PW-LOGIN-B", str([x["pid"] for x in ap["results"]]))
    check("登录模式下 网址 列不写入配置（不覆盖固定系统地址）", "api_base" not in acct("zhonghang"))
    check("应用响应/平台状态不含密码", not any(s in json.dumps(ap, ensure_ascii=False) for s in ALLFAKE))
    check("应用结果标出要自动测试的平台（每平台一次）", sorted(x["pid"] for x in ap["results"] if x["testable"]) == ["yuntu", "zhonghang"])
    code, ap2 = apply(pv["batch"])
    check("批次只能使用一次/过期后提示重新选择", code == 400 and "过期" in json.dumps(ap2, ensure_ascii=False))

    # ---- 再次导入：覆盖 / 无变化 / 打码与空白不覆盖 ----
    again = [importer.COLUMNS,
             ["云途", "登录", "user-yt2@example.invalid", "********", "", "", "", "", "", "", "", ""],
             ["中航", "登录", "user-zh@example.invalid", "", "", "", "", "", "", "", "", ""]]
    code, pv = prev(again)
    acts = [x["action"] for x in pv["rows"]]
    check("再次导入：账号变了 → 覆盖；完全相同 → 无变化", acts == ["overwrite", "same"], str(acts))
    apply(pv["batch"], [0, 1])
    check("打码(****)密码不会覆盖原密码，账号被更新", acct("yuntu").get("username") == "user-yt2@example.invalid" and acct("yuntu").get("password") == "FAKE-PW-LOGIN-A")
    check("空白密码不会清空原密码", acct("zhonghang").get("password") == "FAKE-PW-LOGIN-B")

    # ---- 勾选部分行 ----
    two = [importer.COLUMNS, ["云途", "登录", "only-a@example.invalid", "FAKE-PW-LOGIN-A", "", "", "", "", "", "", "", ""],
           ["中航", "登录", "only-b@example.invalid", "FAKE-PW-LOGIN-B", "", "", "", "", "", "", "", ""]]
    code, pv = prev(two)
    apply(pv["batch"], [1])
    check("只写入勾选的行", acct("zhonghang").get("username") == "only-b@example.invalid" and acct("yuntu").get("username") == "user-yt2@example.invalid")

    # ---- API 模式：各平台字段映射 ----
    api = [importer.COLUMNS,
           ["云途", "API", "", "", "FAKE-APPID-YT", "FAKE-APPSEC-YT", "", "", "", "env=sandbox", "云途备注", ""],
           ["环洋", "开放平台API", "", "", "FAKE-APPKEY-HY", S1, "", "", "https://example.invalid/pos-web", "", "", ""],
           ["燕文", "API", "", "", "FAKE-UID-YW", S4, "", "", "", "city_id=FAKECITY", "", ""],
           ["顺丰国际", "API", "", "", "FAKE-APPKEY-SF", S2, S3, "FAKE-CUST-SF", "", "", "", ""],
           ["雨桐", "API", "", "", "FAKE-APPKEY-YU", S5, "", "", "https://example.invalid/ois", "", "", ""],
           ["中航", "API", "", "", "FAKE-APPKEY-ZH", "FAKE-APPSEC-ZH", "", "", "https://example.invalid/zh-ois", "", "", "否"]]
    code, pv = prev(api)
    acts = [x["action"] for x in pv["rows"]]
    check("API 模式 6 行均可识别为新增/覆盖", all(a in ("add", "overwrite") for a in acts), str([(x["name"], x["action"], x["message"]) for x in pv["rows"]]))
    check("API 模式预览不含密钥明文", not any(s in json.dumps(pv, ensure_ascii=False) for s in ALLFAKE))
    code, ap = apply(pv["batch"])
    yt, hy, yw, sf, yu, zh = (acct(p) for p in ("yuntu", "huanyang", "yanwen", "shunfeng", "yutong", "zhonghang"))
    check("云途 API：appKey→app_id、appSecret→app_secret、其他参数 env", yt.get("app_id") == "FAKE-APPID-YT" and yt.get("app_secret") == "FAKE-APPSEC-YT" and yt.get("env") == "sandbox", str({k: v for k, v in yt.items() if "secret" not in k}))
    check("环洋 API：appKey/appSecret/对接URL→api_base", hy.get("app_key") == "FAKE-APPKEY-HY" and hy.get("app_secret") == S1 and hy.get("api_base") == "https://example.invalid/pos-web")
    check("燕文 API：appKey→user_id、appSecret→api_token、city_id", yw.get("user_id") == "FAKE-UID-YW" and yw.get("api_token") == S4 and yw.get("city_id") == "FAKECITY")
    check("顺丰 API：appKey/appSecret/AES秘钥/客户编码", sf.get("app_key") == "FAKE-APPKEY-SF" and sf.get("app_secret") == S2 and sf.get("aes_key") == S3 and sf.get("customer_code") == "FAKE-CUST-SF", str(sorted(sf)))
    check("雨桐 API：appKey/appSecret/对接URL", yu.get("app_key") == "FAKE-APPKEY-YU" and yu.get("app_secret") == S5 and yu.get("api_base") == "https://example.invalid/ois")
    check("中航 API 写入且「启用=否」生效；原登录账号保留", zh.get("app_key") == "FAKE-APPKEY-ZH" and zh.get("enabled") is False and zh.get("username") == "only-b@example.invalid", str(zh.get("enabled")))
    check("API 行写入后该平台授权方式切换为 API", all(acct(p).get("auth_mode") == "api" for p in ("yuntu", "huanyang", "yanwen", "shunfeng", "yutong")))
    check("备注列被保存", acct("yuntu").get("remark") == "云途备注")
    st = c.get("/api/platforms").text
    check("平台状态接口不回显任何密钥/密码", not any(s in st for s in ALLFAKE))
    test_pids = sorted(x["pid"] for x in ap["results"] if x["testable"])
    check("自动测试：每个平台只测一次", len(test_pids) == len(set(test_pids)))

    # ---- 错误/重复/授权方式 ----
    bad = [importer.COLUMNS,
           ["云途", "魔法", "a", "b", "", "", "", "", "", "", "", ""],
           ["云途", "登录", "dup1@example.invalid", "FAKE-PW-LOGIN-A", "", "", "", "", "", "", "", ""],
           ["云途", "登录", "dup2@example.invalid", "FAKE-PW-LOGIN-A", "", "", "", "", "", "", "", ""],
           ["云途", "API", "", "", "k", "s", "", "", "", "不存在参数=1", "", ""],
           ["云途", "API", "", "", "k", "s", "", "", "", "env=不存在", "", ""]]
    code, pv = prev(bad)
    acts = [x["action"] for x in pv["rows"]]
    check("错误行：授权方式无法识别/未知参数/参数值不在可选项；同模式重复行标记 dup（后者生效）", acts == ["error", "dup", "overwrite", "error", "error"], f"{acts} {[x['message'] for x in pv['rows']]}")
    check("错误行不可被应用（即使勾选）", apply(pv["batch"], [0, 3, 4])[1]["results"] == [])

    class EmptyStore:
        def get_account(self, pid):
            return {}
    pj_ = importer.preview("x.xlsx", b64(xl([importer.COLUMNS, ["雨桐", "API", "", "", "FAKE-K", "", "", "", "", "", "", ""], ["云途", "登录", "only-user@example.invalid", "", "", "", "", "", "", "", "", ""]])), m.registry, EmptyStore())
    check("没有旧配置时缺必填（appSecret/密码）→ 错误并指出缺哪项", [x["action"] for x in pj_["rows"]] == ["error", "error"] and "appSecret" in pj_["rows"][0]["message"] and "密码" in pj_["rows"][1]["message"], str([x["message"] for x in pj_["rows"]]))

    # ---- 无表头 / CSV(GB18030, tab) ----
    code, pv = prev(raw="云途,hl@example.invalid,FAKE-PW-LOGIN-A,https://example.invalid\n中航\thz@example.invalid\tFAKE-PW-LOGIN-B\t".encode("gb18030"), name="a.csv")
    check("无表头 CSV（GB18030）：按 平台/账号/密码/网址 读取", code == 200 and [x["action"] for x in pv["rows"]][0] in ("overwrite", "same", "add"), str(pv)[:100])
    code, pv = prev(raw="平台\t账号\t密码\n云途\ttab@example.invalid\tFAKE-PW-LOGIN-A\n".encode("utf-8-sig"), name="a.txt")
    check("制表符分隔文本（从 Excel 复制）可读取", code == 200 and pv["rows"][0]["pid"] == "yuntu")
    # 列顺序打乱 + 英文别名
    code, pv = prev([["password", "Platform", "Username"], ["FAKE-PW-LOGIN-A", "yuntu", "alias@example.invalid"]])
    check("表头别名/乱序（英文列名）可识别", code == 200 and pv["rows"][0]["pid"] == "yuntu" and pv["rows"][0]["action"] in ("overwrite", "add"), str(pv)[:100])

    # ---- 导出 ----
    # 导出时 yuntu 已有 FAKE 密码
    for sec in ("none", "mask"):
        for fmt in ("xlsx", "csv"):
            r = c.get(f"/api/accounts/export?fmt={fmt}&secrets={sec}")
            body = r.content.decode("utf-8", "ignore") if fmt == "csv" else json.dumps(read_xlsx(r.content), ensure_ascii=False)
            leak = [s for s in ALLFAKE if s in body]
            check(f"导出 {fmt}/密钥{'不导出' if sec == 'none' else '打码'}：不含任何密钥明文", r.status_code == 200 and not leak and "yuntu" not in body.lower().replace("yuntu", "") and len(body) > 100, f"leak={leak}")
    r = c.get("/api/accounts/export?fmt=xlsx&secrets=mask")
    ex = dict(read_xlsx(r.content))
    first = ex["账号配置"]
    check("打码导出的行里能看到打码形式（含 ****）", any("*" in cell for row in first[1:] for cell in row), "")
    check("导出不提供明文选项", c.get("/api/accounts/export?secrets=plain").status_code == 400)
    # 导出再导入（往返）：无变化或仅覆盖，不报错，且不破坏原密钥
    code, pv = prev(raw=c.get("/api/accounts/export?fmt=xlsx&secrets=mask").content)
    apply(pv["batch"])
    check("导出(打码)再导入：不会破坏已有密钥", acct("huanyang").get("app_secret") == S1 and acct("yuntu").get("password") == "FAKE-PW-LOGIN-A")
    code, pv = prev(raw=c.get("/api/accounts/export?fmt=xlsx&secrets=none").content)
    apply(pv["batch"])
    check("导出(不含密钥)再导入：不会清空已有密钥", acct("shunfeng").get("aes_key") == S3 and acct("yanwen").get("api_token") == S4)
    check("配置文件权限仍为 600", (os.stat(Path(tmp) / "config.json").st_mode & 0o777) == 0o600 if os.name == "posix" else True)


def ui_test():
    port = 8771
    data = tempfile.mkdtemp(prefix="fh_impui_")
    env = dict(os.environ, FREIGHT_HUB_NO_UI="1", FREIGHT_HUB_PORT=str(port), FREIGHT_HUB_DATA=data)
    srv = subprocess.Popen([sys.executable, "run.py"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        import requests
        for _ in range(60):
            try:
                requests.get(f"http://127.0.0.1:{port}/api/meta", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        from playwright.sync_api import sync_playwright
        xlsx = Path(data) / "导入测试.xlsx"
        xlsx.write_bytes(xl([importer.COLUMNS,
                             ["云途", "登录", "ui-yt@example.invalid", "FAKE-PW-LOGIN-A", "", "", "", "", "", "", "", ""],
                             ["环洋", "API", "", "", "FAKE-APPKEY-HY", S1, "", "", "", "", "", ""],
                             ["欣航", "", "", "", "", "", "", "", "", "", "", ""],
                             ["???", "", "", "", "", "", "", "", "", "", "", ""]]))
        shots = ROOT / "screenshots"
        shots.mkdir(exist_ok=True)
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            ctx = b.new_context(viewport={"width": 1360, "height": 860}, locale="zh-CN", accept_downloads=True)
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            tested = []

            def fake_test(route):
                tested.append(route.request.url.split("/")[-2])
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "message": "模拟：连接正常", "elapsed_ms": 1200}))
            pg.route("**/api/platforms/*/test", fake_test)
            pg.goto(f"http://127.0.0.1:{port}/?frame=1", wait_until="networkidle")
            pg.wait_for_selector(".panel.active .platlist input")
            pg.click(".mb-item:has-text('文件')")
            items = pg.locator(".pop .pi, .popmenu .pi, .menu-pop .mi").all_inner_texts() if False else pg.locator("body").inner_text()
            check("UI 文件菜单含 导入账号/下载模板/导出", all(t in items for t in ["导入账号", "下载导入模板（Excel）", "导出当前账号配置"]))
            pg.keyboard.press("Escape")
            pg.click("#tree >> text=平台账号")
            pg.wait_for_selector(".accbar .bImp")
            check("UI 平台账号页有 批量导入/下载模板/导出 按钮", pg.locator(".accbar button").count() == 4)
            with pg.expect_file_chooser() as fc:
                pg.click(".accbar .bImp")
            fc.value.set_files(str(xlsx))
            pg.wait_for_selector(".imptbl .impSel")
            txt = pg.locator("#modal").inner_text()
            check("UI 预览表：4 行，含 新增/跳过/错误 标签，不显示密码", pg.locator(".imptbl tbody tr").count() == 4 and "新增" in txt and "跳过" in txt and "错误" in txt and "FAKE-PW" not in txt and S1 not in txt and "已填写" in txt)
            check("UI 预览：可导入行默认勾选，错误/跳过行禁用", pg.locator(".impSel:checked").count() == 2 and pg.locator(".impSel:disabled").count() == 2)
            pg.screenshot(path=str(shots / "20-导入账号-预览.png"))
            pg.click("#impOk")
            pg.wait_for_selector("#impClose:not([disabled])", timeout=15000)
            txt = pg.locator("#modal").inner_text()
            check("UI 导入完成：逐个自动测试并显示结果（成功 2）", "测试完成：成功 2" in txt and txt.count("模拟：连接正常") == 2 and sorted(tested) == ["huanyang", "yuntu"], f"tested={tested}")
            pg.screenshot(path=str(shots / "21-导入账号-结果.png"))
            pg.click("#impClose")
            # 导出对话框
            pg.click(".accbar .bExp")
            pg.wait_for_selector("#exOk")
            with pg.expect_download() as dl:
                pg.click("#exOk")
            path = dl.value.path()
            blob = Path(path).read_bytes()
            check("UI 导出（默认不导出密钥）得到 xlsx 且不含密钥", blob[:2] == b"PK" and S1.encode() not in blob and b"FAKE-PW" not in blob and "账号配置" in json.dumps(read_xlsx(blob), ensure_ascii=False), dl.value.suggested_filename)
            with pg.expect_download() as dl:
                pg.click(".accbar .bTpl")
            check("UI 下载模板（Excel）", dl.value.suggested_filename == "账号导入模板.xlsx")
            check("UI 无 JS 报错", not errs, str(errs)[:120])
            b.close()
    finally:
        srv.terminate()


if __name__ == "__main__":
    run()
    try:
        ui_test()
    except Exception as e:  # noqa
        import traceback
        traceback.print_exc()
        check("UI 流程测试执行", False, f"{type(e).__name__}: {e}"[:150])
    ok = sum(1 for r in results if r["pass"])
    print(f"\n账号导入测试：{ok}/{len(results)} 通过")
    (ROOT / "tests" / "import_result.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.exit(0 if ok == len(results) else 1)

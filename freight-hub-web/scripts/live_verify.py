"""线上验收（Playwright）。管理员口令从 ADMIN_PASSWORD.txt 读取，不打印。用法：python scripts/live_verify.py https://xxx.vercel.app"""
import json, re, sys, time, pathlib
from playwright.sync_api import sync_playwright

URL = sys.argv[1].rstrip("/")
ROOT = pathlib.Path(__file__).resolve().parent.parent
SH = ROOT / "screenshots"; SH.mkdir(exist_ok=True)
txt = (ROOT / "ADMIN_PASSWORD.txt").read_text(encoding="utf-8")
PW = re.search(r"密码[:：]\s*(\S+)", txt).group(1)
res = []
def ok(name, cond, extra=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)

def jf(page, method, path, body=None):
    return page.evaluate("""async ([m,p,b]) => { const r = await fetch(p, {method:m, headers:{'Content-Type':'application/json','X-CSRF-Token':window.__csrf||''}, body: b?JSON.stringify(b):undefined}); let j=null; try{j=await r.json()}catch(e){}; return {s:r.status, j}; }""", [method, path, body])

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=True, bypass_csp=True)
    pg = ctx.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    r = pg.goto(URL + "/")
    ok("未登录访问首页 → 跳转登录页", pg.url.endswith("/login"), pg.url)
    h = r.headers if r else {}
    ok("HTTPS + HSTS", URL.startswith("https://") and "max-age" in (pg.request.get(URL + "/login").headers.get("strict-transport-security", "")))
    ok("CSP/XFO 安全头", "default-src" in pg.request.get(URL + "/login").headers.get("content-security-policy", ""))
    ok("登录页含『30 天内保持登录』", "30 天" in pg.inner_text("body"))
    pg.screenshot(path=str(SH / "live-login-1280x800.png"))
    pg.goto(URL + "/setup"); ok("已有管理员 → /setup 跳回登录", pg.url.endswith("/login"))
    # 错误口令
    pg.fill("#u", "admin"); pg.fill("#p", "wrong-pass-xx"); pg.click("#ok")
    pg.wait_for_function("document.getElementById('err').textContent.length>0")
    ok("错误口令提示且不跳转", "用户名或密码错误" in pg.inner_text("#err") and pg.url.endswith("/login"))
    # 未登录 API
    ok("未登录 /api/meta = 401", pg.request.get(URL + "/api/meta").status == 401)
    ok("未登录 /api/users = 401", pg.request.get(URL + "/api/users").status == 401)
    # 登录
    pg.fill("#u", "admin"); pg.fill("#p", PW); pg.click("#ok")
    pg.wait_for_url(URL + "/", timeout=30000); pg.wait_for_selector("#boot", state="detached", timeout=30000)
    ck = {c["name"]: c for c in ctx.cookies()}.get("fh_session") or {}
    days = (ck.get("expires", 0) - time.time()) / 86400
    ok("Cookie HttpOnly+Secure+SameSite", ck.get("httpOnly") and ck.get("secure") and ck.get("sameSite") in ("Lax", "Strict"), f"sameSite={ck.get('sameSite')}")
    ok("记住我：Cookie 有效期≈30 天", 29 <= days <= 30.1, f"{days:.1f} 天")
    ok("用户条显示 admin / 管理员", "admin" in pg.inner_text("#userChip"))
    ok("菜单含「管理」", "管理" in pg.inner_text("#menubar"))
    pg.wait_for_timeout(800)
    pg.screenshot(path=str(SH / "live-main-1280x800.png"))
    # 平台列表：密钥脱敏、云途登录仅桌面版
    pl = pg.evaluate("fetch('/api/platforms').then(r=>r.json())")
    blob = json.dumps(pl, ensure_ascii=False)
    ok("/api/platforms 不含密钥明文（字段值均已脱敏）", all(not (isinstance(v, str) and len(v) > 6 and "*" not in v and "•" not in v and "…" not in v and k.lower() in ("password", "app_secret", "appsecret", "secret", "aes_key", "aeskey", "token")) for x in pl for k, v in (x.get("account") or {}).items()) if any("account" in x for x in pl) else True)
    yt = next(x for x in pl if x["id"] == "yuntu")
    ok("云途「登录」模式：仅桌面版", yt["label"] == "仅桌面版" and yt["modes"]["login"]["desktop_only"])
    cfg = {x["id"]: x for x in pl}
    ok("已预置凭据的平台状态（雨桐/环洋/顺丰/中航/云途）", all(cfg[i].get("configured", cfg[i]["state"] != "no_account") for i in ("yutong", "huanyang", "shunfeng", "zhonghang", "yuntu")),
       {i: cfg[i]["state"] for i in ("yutong", "huanyang", "shunfeng", "zhonghang", "yuntu", "yanwen", "xinhang")})
    jf(pg, "DELETE", "/api/users/qa_tmp_user"); jf(pg, "DELETE", "/api/roles/qa_tmp")      # 清理上次中断残留
    # 用户管理
    pg.click('#menubar .mb-item[data-m="管理"]'); pg.click('#ctxmenu .mi:has-text("用户管理")')
    pg.wait_for_function("document.querySelector('.ucount') && document.querySelector('.ucount').textContent.includes('共')")
    ok("用户管理页：显示 admin", "admin" in pg.locator(".panel:visible .gridhost").inner_text())
    pg.screenshot(path=str(SH / "live-users-1280x800.png"))
    # 角色与权限矩阵
    pg.click('#menubar .mb-item[data-m="管理"]'); pg.click('#ctxmenu .mi:has-text("角色与权限")')
    pg.wait_for_selector(".rmtbl")
    ok("权限矩阵：6 个内置角色（含试用 demo） × 17 项权限", pg.locator(".rmtbl thead tr:first-child th").count() == 7 and pg.locator('.rmtbl input[type=checkbox][data-r="admin"]').count() == 17)
    ok("admin 列锁定", pg.locator('.rmtbl input[data-r="admin"]:not([disabled])').count() == 0)
    pg.screenshot(path=str(SH / "live-roles-matrix-1280x800.png"))
    # 新建角色 + 勾选权限（UI）
    pg.click(".bNew"); pg.wait_for_selector("#modal .dlg")
    pg.fill("#nrN", "qa_tmp"); pg.fill("#nrL", "QA 临时角色")
    pg.click("#nrO"); pg.wait_for_selector('.rmtbl input[data-r="qa_tmp"][data-k="query.run"]')
    pg.check('.rmtbl input[data-r="qa_tmp"][data-k="query.run"]')
    pg.check('.rmtbl input[data-r="qa_tmp"][data-k="history.view"]')
    ok("勾选依赖自动补全（query.run → platforms.view）", pg.is_checked('.rmtbl input[data-r="qa_tmp"][data-k="platforms.view"]'))
    pg.click(".bSave"); pg.wait_for_timeout(1200)
    rl = jf(pg, "GET", "/api/roles")["j"]["roles"]
    q = next(x for x in rl if x["name"] == "qa_tmp")
    ok("保存权限矩阵 → 后端已生效", set(q["perms"]) == {"query.run", "platforms.view", "history.view"}, q["perms"])
    # 建临时用户并用该角色登录
    r1 = jf(pg, "POST", "/api/users", {"username": "qa_tmp_user", "password": "QaTmp-" + str(int(time.time())) + "-Zx", "role": "qa_tmp"})
    ok("创建用户（API+CSRF）", r1["s"] == 200, r1["s"])
    pwq = None
    # 读取刚设的密码不可得 → 重置为已知值
    NEWPW = "QaTmp-Pass-" + str(int(time.time()))
    ok("重置用户密码", jf(pg, "PUT", "/api/users/qa_tmp_user", {"password": NEWPW})["s"] == 200)
    c2 = b.new_context(viewport={"width": 1280, "height": 800}, bypass_csp=True); p2 = c2.new_page()
    p2.goto(URL + "/login"); p2.fill("#u", "qa_tmp_user"); p2.fill("#p", NEWPW); p2.click("#ok")
    p2.wait_for_url(URL + "/", timeout=30000); p2.wait_for_selector("#boot", state="detached", timeout=30000)
    ok("临时用户：菜单无「管理」（无后台权限）", "管理" not in p2.inner_text("#menubar"))
    ok("临时用户：/api/users = 403", p2.request.get(URL + "/api/users").status == 403)
    ok("临时用户：/api/audit = 403", p2.request.get(URL + "/api/audit").status == 403)
    p2.screenshot(path=str(SH / "live-restricted-user-1280x800.png"))
    # 去掉 query.run → 立刻生效
    rr = jf(pg, "PUT", "/api/roles/qa_tmp", {"perms": ["platforms.view", "history.view"]})
    csrf2 = p2.evaluate("window.__csrf")
    st = p2.evaluate("""async (c) => (await fetch('/api/quote',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':c},body:JSON.stringify({country:'US',weight:1,platforms:['xinhang']})})).status""", csrf2)
    ok("撤销 query.run 后：同一会话再查询 = 403（立即生效）", st == 403, st)
    c2.close()
    # 审计页
    pg.click('#menubar .mb-item[data-m="管理"]'); pg.click('#ctxmenu .mi:has-text("审计日志")')
    pg.wait_for_selector(".auditbar"); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(SH / "live-audit-1280x800.png"))
    au = jf(pg, "GET", "/api/audit?size=200")["j"]
    acts = {x["action"] for x in au["items"]}
    ok("审计含 登录/失败登录/建角色/改角色/建用户/越权拒绝", {"auth.login", "auth.login_failed", "role.create", "role.update", "user.create", "access.denied"} <= acts, sorted(acts))
    ip = next(x["ip"] for x in au["items"] if x["action"] == "auth.login")
    ok("审计记录来源 IP（非空且非内网占位）", bool(ip) and not ip.startswith(("10.", "127.", "172.", "192.168.")), ip)
    ok("审计过滤：action=role.update", all(x["action"] == "role.update" for x in jf(pg, "GET", "/api/audit?action=role.update")["j"]["items"]))
    with pg.expect_download() as dl:
        pg.click(".panel:visible .bExp")
    f = pathlib.Path(dl.value.path()); data = f.read_bytes()
    ok("审计导出 CSV（含 BOM、有表头与数据）", data.startswith(b"\xef\xbb\xbf") and data.count(b"\n") >= 5, len(data))
    # 欣航公开查询（只选欣航）
    pg.click('#toolbar .tb[data-cmd="new"]') if pg.locator('#toolbar .tb[data-cmd="new"]').count() else None
    pg.wait_for_selector(".panel:visible .platlist")
    boxes = pg.locator(".panel:visible .platlist input")
    for i in range(boxes.count()):
        bx = boxes.nth(i); want = bx.get_attribute("data-id") == "xinhang"
        if bx.is_enabled() and bx.is_checked() != want: bx.click()
    pg.fill(".panel:visible .inCountry", "美国 US"); pg.fill(".panel:visible .inWeight", "1")
    pg.keyboard.press("F5")
    try:
        pg.wait_for_selector(".panel:visible .gridscroll tbody tr", timeout=60000)
        n = pg.locator(".panel:visible .gridscroll tbody tr").count()
        txt = pg.inner_text(".panel:visible .gridscroll tbody")
        ok("欣航公开价查询（无需凭据）返回结果", n > 0, f"{n} 行")
    except Exception as e:
        ok("欣航公开价查询（无需凭据）返回结果", False, str(e)[:120])
    pg.wait_for_timeout(500)
    pg.screenshot(path=str(SH / "live-query-xinhang-1280x800.png"))
    # 历史
    hist = jf(pg, "GET", "/api/history")["j"]
    ok("查询已写入历史（数据库）", isinstance(hist, list) and len(hist) >= 1)
    # 清理临时账号/角色
    ok("清理：删除临时用户", jf(pg, "DELETE", "/api/users/qa_tmp_user")["s"] == 200)
    ok("清理：删除临时角色", jf(pg, "DELETE", "/api/roles/qa_tmp")["s"] == 200)
    # 大屏截图
    pg.set_viewport_size({"width": 1920, "height": 1080})
    pg.screenshot(path=str(SH / "live-main-1920x1080.png"))
    # 退出登录
    pg.evaluate("document.getElementById('uLogout').click()"); pg.wait_for_url("**/login", timeout=15000)
    ok("退出登录后 /api/meta = 401", pg.request.get(URL + "/api/meta").status == 401)
    ok("页面无 JS 错误", not errs, errs[:2])
    b.close()
bad = [n for n, c in res if not c]
print(f"\n{len(res)-len(bad)}/{len(res)} 通过", "失败：" + "; ".join(bad) if bad else "")
sys.exit(1 if bad else 0)

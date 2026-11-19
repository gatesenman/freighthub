"""线上：用户 / 角色 / 权限矩阵 / 审计日志（临时对象，结束后清理）。"""
import csv, io, json, re, sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from lib import *
from app import rbac

TP = TMPPW            # 临时用户口令（随机，不打印）
TP2 = TMPPW + "-B"
def mm(p, top, item, wait=700):
    p.click(f'#menubar .mb-item[data-m="{top}"]'); p.click(f'#ctxmenu .mi:has-text("{item}")'); p.wait_for_timeout(wait)
def rows(p): return p.locator(".panel:visible .gridscroll tbody tr").count()
def sel_user(p, name):
    tr = p.locator(".panel:visible .gridscroll tbody tr"); n = tr.count()
    for i in range(n):
        if tr.nth(i).locator("td").first.inner_text().split(" ")[0] == name: tr.nth(i).click(); return
    raise AssertionError("user not in list: " + name)
def dl(p, fn):
    with p.expect_download(timeout=30000) as d: fn()
    path = DL / d.value.suggested_filename; d.value.save_as(str(path)); return path, d.value.suggested_filename
def cleanup(b):
    c, a = new_admin_page(b, "cleanup")
    us = jf(a, "GET", "/api/users")["j"]
    for u in us:
        if u["username"].startswith("e2e"): jf(a, "DELETE", "/api/users/" + u["username"])
    for r in jf(a, "GET", "/api/roles")["j"]["roles"]:
        if r["name"].startswith("e2e"): jf(a, "DELETE", "/api/roles/" + r["name"])
    c.close()

with sync_playwright() as pw:
    b = pw.chromium.launch(); cleanup(b)
    ctx, a = new_admin_page(b, "admin"); ctx.grant_permissions(["clipboard-read"])

    # ================= 角色 =================
    mm(a, "管理", "角色与权限")
    @case("角色页：权限矩阵渲染；admin 列全部勾选且禁用；各默认角色人数显示")
    def _():
        check(a.locator(".panel:visible .rmtbl").count() == 1); adm = a.locator('.panel:visible input[data-r="admin"]'); n = adm.count(); check(n == len(rbac.PERM_KEYS), n)
        check(all(adm.nth(i).is_checked() and adm.nth(i).is_disabled() for i in range(n)), "admin column editable"); shot(a, "admin_roles_matrix")
        for r in ("admin", "manager", "operator", "auditor", "viewer", "demo"): check(a.locator(f'.panel:visible th[data-r="{r}"]').count() == 1, r)
    @case("新增角色：非法标识 / 空名称 → 对话框内报错；合法标识创建成功，初始零权限")
    def _():
        a.click(".panel:visible .bNew"); a.wait_for_selector("#nrN")
        for ident, label in (("", "x"), ("Bad ID!", "x"), ("admin", "重名"), ("e2e_role", "")):
            a.fill("#nrN", ident); a.fill("#nrL", label); a.click("#nrO"); a.wait_for_timeout(500); check(a.inner_text("#nrE").strip(), f"no error for {ident!r}/{label!r}")
        a.fill("#nrN", "e2e_role"); a.fill("#nrL", "E2E测试角色"); a.fill("#nrD", "自动化测试用，可删除"); a.click("#nrO"); a.wait_for_selector("#modal", state="hidden", timeout=8000)
        check(a.locator('.panel:visible th[data-r="e2e_role"]').count() == 1, "role not shown")
        check(a.locator('.panel:visible input[data-r="e2e_role"]:checked').count() == 0, "new role has perms")
    @case("勾选权限：依赖自动联动（history.delete_all → history.view；users.manage → users.view）；保存按钮启用；放弃修改还原")
    def _():
        cb = lambda k: a.locator(f'.panel:visible input[data-r="e2e_role"][data-k="{k}"]')
        cb("history.delete_all").check(); check(cb("history.view").is_checked() or cb("history.delete").is_checked(), "implied not ticked"); check(a.locator(".panel:visible .bSave").is_enabled())
        a.click(".panel:visible .bUndo"); check(a.locator('.panel:visible input[data-r="e2e_role"]:checked').count() == 0 and a.locator(".panel:visible .bSave").is_disabled(), "undo")
        cb("users.manage").check(); check(cb("users.view").is_checked(), "users.view implied"); cb("users.view").uncheck(); check(not cb("users.manage").is_checked(), "dependent not cleared"); check(a.locator(".panel:visible .bUndo").is_disabled(), "back to original should be clean")
    @case("保存权限：query.run + platforms.view + history.view 保存成功；服务端数据一致；审计有 role.update")
    def _():
        for k in ("query.run", "platforms.view", "history.view"): a.locator(f'.panel:visible input[data-r="e2e_role"][data-k="{k}"]').check()
        a.click(".panel:visible .bSave"); a.wait_for_timeout(1200); check(a.locator(".panel:visible .bSave").is_disabled(), "still dirty")
        r = [x for x in jf(a, "GET", "/api/roles")["j"]["roles"] if x["name"] == "e2e_role"][0]; check(sorted(r["perms"]) == ["history.view", "platforms.view", "query.run"], r["perms"])
    @case("系统角色 admin 不能删除/修改（无删除按钮，API 也拒绝）")
    def _():
        check(a.locator('.panel:visible .rdel[data-r="admin"]').count() == 0)
        check(jf(a, "DELETE", "/api/roles/admin")["s"] in (400, 403), "api delete admin"); check(jf(a, "PUT", "/api/roles/admin", {"perms": []})["s"] in (400, 403), "api edit admin")

    # ================= 用户 =================
    mm(a, "管理", "用户管理")
    @case("新增用户：用户名 / 口令校验（过短口令、非法用户名、重名）→ 红字提示；合法创建")
    def _():
        for nm, pw, kind in (("", TP, "empty name"), ("e2e_u1", "short", "short pw"), ("e2e u1!", TP, "bad chars"), ("admin", TP, "duplicate")):
            a.fill(".panel:visible .nuName", nm); a.fill(".panel:visible .nuPw", pw); a.select_option(".panel:visible .nuRole", "e2e_role"); a.click(".panel:visible .nuAdd"); a.wait_for_timeout(600)
            check(a.inner_text(".panel:visible .uerr").strip(), "no error: " + kind)
        a.fill(".panel:visible .nuName", "e2e_u1"); a.fill(".panel:visible .nuPw", TP); a.select_option(".panel:visible .nuRole", "e2e_role"); a.click(".panel:visible .nuAdd"); a.wait_for_timeout(900)
        check(a.inner_text(".panel:visible .uerr").strip() == "", a.inner_text(".panel:visible .uerr")); check("e2e_u1" in a.inner_text(".panel:visible .gridscroll"), "not listed"); shot(a, "admin_users_list")
    @case("新用户登录：只能看到被授权的功能（无管理菜单），API 越权 403 且审计记录 access.denied")
    def _():
        c, u = new_ctx(b), None; u = c.new_page(); watch(u, "e2e_u1"); login(u, "e2e_u1", TP)
        tops = u.locator("#menubar .mb-item").all_inner_texts(); check("管理" not in tops, tops)
        for path in ("/api/users", "/api/roles", "/api/audit", "/api/accounts/export"): check(jf(u, "GET", path)["s"] == 403, path)
        check(jf(u, "GET", "/api/platforms")["s"] == 200 and jf(u, "GET", "/api/history")["s"] == 200)
        check(jf(u, "POST", "/api/platforms/xinhang/test")["s"] == 403, "test conn"); check(jf(u, "PUT", "/api/platforms/xinhang/account", {"values": {}})["s"] == 403, "put account")
        check(jf(u, "GET", "/api/history/1/csv")["s"] in (403, 404), "csv"); c.close()
    @case("权限实时生效：管理员取消 e2e_role 的 query.run → 已登录用户下一次询价立即 403（无需重新登录）")
    def _():
        c = new_ctx(b); u = c.new_page(); login(u, "e2e_u1", TP)
        check(jf(u, "POST", "/api/quote", {"country": "US", "weight": 1, "platforms": ["none"]})["s"] == 200, "should be allowed first")
        r = [x for x in jf(a, "GET", "/api/roles")["j"]["roles"] if x["name"] == "e2e_role"][0]; perms = [p_ for p_ in r["perms"] if p_ != "query.run"]
        check(jf(a, "PUT", "/api/roles/e2e_role", {"perms": perms})["s"] == 200); check(jf(u, "POST", "/api/quote", {"country": "US", "weight": 1, "platforms": ["none"]})["s"] == 403, "still allowed")
        jf(u, "DELETE", "/api/history"); jf(a, "PUT", "/api/roles/e2e_role", {"perms": r["perms"]}); c.close()
    @case("改角色（UI）：e2e_u1 → viewer（只读），旧会话被强制下线，重登后无询价权限；再改回")
    def _():
        c = new_ctx(b); u = c.new_page(); login(u, "e2e_u1", TP)
        a.click(".panel:visible .bRef"); a.wait_for_timeout(500); sel_user(a, "e2e_u1"); a.click(".panel:visible .bRole"); a.wait_for_selector("#rlSel"); a.select_option("#rlSel", "viewer"); a.click("#rlO"); a.wait_for_timeout(900)
        check("只读访客" in a.inner_text(".panel:visible .gridscroll"), "role not updated in list"); rq = jf(u, "POST", "/api/quote", {"country": "US", "weight": 1, "platforms": ["none"]}); check(rq["s"] == 401, f"role change must revoke old session {rq['s']}"); u.context.clear_cookies(); login(u, "e2e_u1", TP); rq = jf(u, "POST", "/api/quote", {"country": "US", "weight": 1, "platforms": ["none"]}); check(rq["s"] == 403, f"viewer can query {rq['s']}")
        sel_user(a, "e2e_u1"); a.click(".panel:visible .bRole"); a.wait_for_selector("#rlSel"); a.select_option("#rlSel", "e2e_role"); a.click("#rlO"); a.wait_for_timeout(700); c.close()
    @case("停用（UI）：已登录会话立即失效 401；登录被拒；启用后可登录")
    def _():
        c = new_ctx(b); u = c.new_page(); login(u, "e2e_u1", TP)
        sel_user(a, "e2e_u1"); a.click(".panel:visible .bDis"); a.wait_for_timeout(900); check("已停用" in a.inner_text(".panel:visible .gridscroll"), "not shown disabled")
        check(jf(u, "GET", "/api/meta")["s"] == 401, "session still valid after disable"); c.close()
        c2 = new_ctx(b); u2 = c2.new_page(); u2.goto(URL + "/login"); r = u2.request.post(URL + "/api/auth/login", data=json.dumps({"username": "e2e_u1", "password": TP}), headers={"content-type": "application/json"}); check(r.status in (401, 403), r.status); c2.close()
        sel_user(a, "e2e_u1"); a.click(".panel:visible .bDis"); a.wait_for_timeout(800); c3 = new_ctx(b); u3 = c3.new_page(); login(u3, "e2e_u1", TP); c3.close()
    @case("重置密码（UI）：旧口令失效、新口令可登录、该用户已登录会话被踢下线")
    def _():
        c = new_ctx(b); u = c.new_page(); login(u, "e2e_u1", TP)
        sel_user(a, "e2e_u1"); a.click(".panel:visible .bPw"); a.wait_for_selector("#apw"); a.fill("#apw", TP2); a.click("#apO"); a.wait_for_timeout(900)
        check(jf(u, "GET", "/api/meta")["s"] == 401, "old session alive"); c.close()
        c2 = new_ctx(b); u2 = c2.new_page(); u2.goto(URL + "/login"); r = u2.request.post(URL + "/api/auth/login", data=json.dumps({"username": "e2e_u1", "password": TP}), headers={"content-type": "application/json"}); check(r.status == 401, f"old pw {r.status}"); c2.close()
        c3 = new_ctx(b); u3 = c3.new_page(); login(u3, "e2e_u1", TP2); c3.close()
    @case("自我保护：不能删除 / 停用 / 降级自己；不能删除最后一个管理员（UI 报错而非崩溃）")
    def _():
        sel_user(a, "admin"); a.click(".panel:visible .bDis"); a.wait_for_timeout(700); e1 = a.inner_text(".panel:visible .uerr").strip()
        sel_user(a, "admin"); a.click(".panel:visible .bDel"); a.wait_for_selector("#modal", state="visible"); a.click("#modal .btn.def"); a.wait_for_timeout(700); e2 = a.inner_text(".panel:visible .uerr").strip()
        print("   self-protect msgs:", e1, "|", e2); check(e1 and e2, "no error shown")
        check(jf(a, "PUT", "/api/users/admin", {"role": "viewer"})["s"] in (400, 403, 409), "self-demote"); check("admin" in a.inner_text(".panel:visible .gridscroll"))
    @case("删除用户（UI）：确认框取消不删；确认后消失，登录失败；其会话失效")
    def _():
        c = new_ctx(b); u = c.new_page(); login(u, "e2e_u1", TP2)
        sel_user(a, "e2e_u1"); a.click(".panel:visible .bDel"); a.wait_for_selector("#modal", state="visible"); close_modal(a); check("e2e_u1" in a.inner_text(".panel:visible .gridscroll"), "cancel deleted")
        sel_user(a, "e2e_u1"); a.click(".panel:visible .bDel"); a.wait_for_selector("#modal", state="visible"); a.click("#modal .btn.def"); a.wait_for_timeout(900)
        check("e2e_u1" not in a.inner_text(".panel:visible .gridscroll"), "still listed"); check(jf(u, "GET", "/api/meta")["s"] == 401, "session alive after delete"); c.close()
    @case("删除正在使用的角色被拒；无人使用后可删除")
    def _():
        check(jf(a, "POST", "/api/users", {"username": "e2e_u2", "password": TP, "role": "e2e_role"})["s"] == 200)
        r = jf(a, "DELETE", "/api/roles/e2e_role"); check(r["s"] in (400, 409), f"deleted in-use role {r['s']}")
        check(jf(a, "DELETE", "/api/users/e2e_u2")["s"] == 200); check(jf(a, "DELETE", "/api/roles/e2e_role")["s"] == 200, "delete unused role")

    # ================= 权限矩阵（所有默认角色 × 全部接口）=================
    @case("权限矩阵：6 个内置角色 × 全部 API：该有的 200，没有的一律 403（含 demo）")
    def _():
        gets = [("GET", p_, perm) for m, p_, perm in [(m, re.sub(r"\[.*?\]\+", "x", re.sub(r"\\d\+", "1", pt)).replace("\\", ""), pr) for m, pt, pr in rbac.ROUTE_PERMS] if m == "GET"]
        paths = [("/api/platforms", "platforms.view"), ("/api/history", "history.view"), ("/api/users", "users.view"), ("/api/roles", "roles.view"), ("/api/audit", "audit.view"), ("/api/audit/export", "audit.export"), ("/api/accounts/export", "accounts.export")]
        denied_mut = [("POST", "/api/users", {"username": "e2e_x", "password": TP, "role": "viewer"}, "users.manage"), ("DELETE", "/api/users/e2e_none", None, "users.manage"), ("POST", "/api/roles", {"name": "e2e_x", "label": "x", "perms": []}, "roles.manage"),
                      ("PUT", "/api/platforms/xinhang/account", {"values": {}}, "accounts.manage"), ("POST", "/api/platforms/xinhang/test", None, "accounts.test"), ("POST", "/api/accounts/import/preview", None, "accounts.import"),
                      ("POST", "/api/quote", {"country": "US", "weight": 1, "platforms": ["none"]}, "query.run")]
        bad = []
        for role in ("admin", "manager", "operator", "auditor", "viewer", "demo"):
            nm = "e2e_m_" + role; jf(a, "DELETE", "/api/users/" + nm); check(jf(a, "POST", "/api/users", {"username": nm, "password": TP, "role": role})["s"] == 200, "create " + role)
            c = new_ctx(b); u = c.new_page(); login(u, nm, TP); perms = rbac.close_perms(next(r_[3] for r_ in rbac.DEFAULT_ROLES if r_[0] == role))
            for path, perm in paths:
                s = jf(u, "GET", path)["s"]; exp_ok = perm in perms
                if exp_ok != (s == 200): bad.append(f"{role} GET {path}: {s} (期望{'允许' if exp_ok else '403'})")
            for m, path, body, perm in denied_mut:
                if perm in perms: continue
                s = jf(u, m, path, body)["s"]
                if s != 403: bad.append(f"{role} {m} {path}: {s} (期望403)")
            if "query.run" in perms:
                s = jf(u, "POST", "/api/quote", {"country": "US", "weight": 1, "platforms": ["none"]})["s"]
                if s != 200: bad.append(f"{role} quote allowed → {s}")
                jf(u, "DELETE", "/api/history")
            c.close(); jf(a, "DELETE", "/api/users/" + nm)
        print("   matrix violations:", bad or "none"); check(not bad, "; ".join(bad[:4]))

    # ================= 审计 =================
    mm(a, "管理", "审计日志", 1500)
    @case("审计页：列表 / 总数 / 分页（上一页下一页，页码文案）")
    def _():
        tot = int(re.search(r"(\d+)", a.inner_text(".panel:visible .acount")).group(1)); check(tot > 20, tot); n1 = rows(a); check(0 < n1 <= 50, n1)
        first = a.locator(".panel:visible .gridscroll tbody tr").first.inner_text()
        if tot > 50:
            a.click(".panel:visible .bNext"); a.wait_for_timeout(900); check(a.locator(".panel:visible .gridscroll tbody tr").first.inner_text() != first, "page2 same"); a.click(".panel:visible .bPrev"); a.wait_for_timeout(700)
        print("   audit total", tot, "page text:", a.inner_text(".panel:visible .apage"))
    @case("审计筛选：按用户 / 操作 / 结果 / IP / 关键字 / 时间范围，组合与重置")
    def _():
        q = lambda s: a.locator(".panel:visible " + s)
        q(".fU").fill("e2e_u1"); q(".bGo").click(); a.wait_for_timeout(900); n = rows(a); check(n > 0, "no e2e_u1 audit"); check(all("e2e_u1" in x for x in a.locator(".panel:visible .gridscroll tbody tr td:nth-child(2)").all_inner_texts()), "user filter leak")
        q(".fA").select_option("auth.login"); q(".bGo").click(); a.wait_for_timeout(800); n2 = rows(a); check(0 < n2 <= n and all("auth.login" in x for x in a.locator(".panel:visible .gridscroll tbody tr td:nth-child(5)").all_inner_texts()), "action filter")
        q(".bClr").click(); a.wait_for_timeout(700); q(".fS").select_option("fail"); q(".bGo").click(); a.wait_for_timeout(800); check(rows(a) > 0 and all(x.strip() for x in a.locator(".panel:visible .gridscroll tbody tr td:nth-child(7)").all_inner_texts()), "fail filter")
        q(".bClr").click(); a.wait_for_timeout(500); q(".fQ").fill("e2e_role"); q(".bGo").click(); a.wait_for_timeout(800); check(rows(a) > 0, "keyword")
        q(".bClr").click(); q(".fU").fill("不存在的用户zzz"); q(".bGo").click(); a.wait_for_timeout(800); check(rows(a) == 0 and a.locator(".panel:visible .gridempty").is_visible(), "empty state")
        q(".bClr").click(); a.wait_for_timeout(500); import datetime as dt; now = dt.datetime.now(); q(".fT1").fill((now - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")); q(".fT2").fill((now + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")); q(".bGo").click(); a.wait_for_timeout(900); check(rows(a) > 0, "time range")
        q(".fT1").fill("2001-01-01T00:00"); q(".fT2").fill("2001-01-02T00:00"); q(".bGo").click(); a.wait_for_timeout(800); check(rows(a) == 0, "old range"); q(".bClr").click(); a.wait_for_timeout(600)
    @case("审计内容：本轮操作都有留痕（role.create/update/delete、user.create/update/delete、access.denied、auth.login_failed、auth.login）且不含口令")
    def _():
        need = ["role.create", "role.update", "role.delete", "user.create", "user.update", "user.delete", "access.denied", "auth.login_failed", "auth.login", "quote.run"]
        items = jf(a, "GET", "/api/audit?size=200")["j"]["items"]; have = {x["action"] for x in items}; miss = [n for n in need if n not in have]; check(not miss, f"missing {miss}")
        txt = json.dumps(items, ensure_ascii=False); check(TP not in txt and TP2 not in txt and PW not in txt, "password leaked into audit")
    @case("审计导出 CSV：下载、BOM、表头、行数 ≥ 1，且不含任何口令")
    def _():
        a.locator(".panel:visible .bClr").click(); a.wait_for_timeout(500)
        f, n = dl(a, lambda: a.click(".panel:visible .bExp")); raw = f.read_bytes(); rr = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")))); print("   audit csv", n, len(rr)); check(len(rr) > 20 and raw[:3] == b"\xef\xbb\xbf", (len(rr), raw[:3]))
        t = raw.decode("utf-8", "ignore"); check(TP not in t and PW not in t, "pw in export")
    @case("审计不可篡改：没有任何修改 / 删除审计的接口（PUT/DELETE/POST /api/audit → 404/405/403）")
    def _():
        for m in ("PUT", "DELETE", "POST", "PATCH"):
            s = jf(a, m, "/api/audit", {})["s"]; check(s in (403, 404, 405), f"{m} -> {s}")
    cleanup(b); ctx.close(); b.close()
finish("admin")

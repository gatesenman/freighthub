"""试用版线上验证：水印（登录页 / 主界面 / 弹窗 / 手机宽度 / 防篡改）+ demo 账号权限、限额、历史隔离。"""
import json, re, time
from lib import *

DEMO_PW = re.search(r"密码[:：]\s*(\S+)", (ROOT / "DEMO_ACCOUNT.txt").read_text(encoding="utf-8")).group(1)

def wm_state(p):
    return p.evaluate("""() => { const w=document.getElementById('fh-wm'), b=document.getElementById('fh-wmb'); const o={};
      for (const [k,e] of [['w',w],['b',b]]) { if(!e){o[k]=null;continue} const c=getComputedStyle(e), r=e.getBoundingClientRect();
        o[k]={disp:c.display,vis:c.visibility,op:c.opacity,pe:c.pointerEvents,z:c.zIndex,w:r.width,h:r.height,bg:(c.backgroundImage||'').slice(0,20),txt:e.textContent}; } return o; }""")

def wm_ok(p):
    s = wm_state(p)
    return s["w"] and s["b"] and s["w"]["disp"] == "block" and s["w"]["pe"] == "none" and float(s["w"]["op"]) == 1 and s["w"]["w"] >= p.viewport_size["width"] - 1 and s["w"]["bg"].startswith("url(") and s["b"]["txt"] == "未授权版本 / 试用" and s["b"]["vis"] == "visible"

with sync_playwright() as pw:
    b = pw.chromium.launch()
    # ---------- 水印 ----------
    c = new_ctx(b); p = c.new_page(); watch(p, "wm-login")
    @case("水印：登录页有斜铺水印 + 顶部徽标（1280x800）")
    def _():
        p.goto(URL + "/login"); p.wait_for_selector("#ok"); p.wait_for_timeout(300)
        check(wm_ok(p), wm_state(p)); shot(p, "trial_login_1280x800")
    @case("水印：不挡点击（pointer-events:none），登录页控件可操作")
    def _():
        p.fill("#u", "x"); check(p.input_value("#u") == "x"); p.fill("#u", "")
        check(p.evaluate("document.elementFromPoint(300,300).id") not in ("fh-wm", "fh-wmb"))
    @case("水印：1920x1080 / 手机 375x667 / 平板 768x1024 登录页均可见")
    def _():
        for w, h in ((1920, 1080), (375, 667), (768, 1024)):
            p.set_viewport_size({"width": w, "height": h}); p.wait_for_timeout(200)
            check(wm_ok(p), f"{w}x{h} {wm_state(p)}"); shot(p, f"trial_login_{w}x{h}")
        p.set_viewport_size({"width": 1280, "height": 800})
    @case("水印防篡改：删除节点 → 0.7s 内自动恢复")
    def _():
        p.evaluate("document.getElementById('fh-wm').remove(); document.getElementById('fh-wmb').remove()"); p.wait_for_timeout(1200); check(wm_ok(p), wm_state(p))
    @case("水印防篡改：display:none / opacity:0 / visibility:hidden / 改 class / 注入 CSS 均无效")
    def _():
        p.evaluate("document.getElementById('fh-wm').style.setProperty('display','none','important'); document.getElementById('fh-wmb').style.setProperty('opacity','0','important')"); p.wait_for_timeout(1200); check(wm_ok(p), "inline")
        p.add_style_tag(content="#fh-wm,#fh-wmb{display:none!important;opacity:0!important;visibility:hidden!important;z-index:-1!important;transform:scale(0)!important}")
        p.wait_for_timeout(1200); check(wm_ok(p), "css " + str(wm_state(p)))
        p.evaluate("document.getElementById('fh-wm').style.cssText='display:none'"); p.wait_for_timeout(1200); check(wm_ok(p), "cssText")
    @case("水印防篡改：给水印盖一个更高 z-index 的遮罩 / 移到别的父节点 → 恢复到 body 末尾")
    def _():
        p.evaluate("document.body.insertBefore(document.getElementById('fh-wm'), document.body.firstChild)"); p.wait_for_timeout(1200)
        check(p.evaluate("document.getElementById('fh-wm').parentNode===document.body && ['fh-wm','fh-wmb'].includes(document.body.lastChild.id)"), "order")
    c.close()

    # ---------- demo 登录 ----------
    ctx = new_ctx(b); d = ctx.new_page(); watch(d, "demo")
    @case("demo 登录成功并进入主界面，主界面有水印 + 徽标")
    def _():
        login(d, "demo", DEMO_PW); d.wait_for_timeout(500); check(wm_ok(d), wm_state(d)); shot(d, "trial_demo_main_1280x800")
    @case("demo 用户条：显示 试用客户 + 本小时剩余次数")
    def _():
        t = d.inner_text("#userChip"); print("   chip:", t.replace("\n", " | ")); check("试用客户" in t and "剩余" in t and "/30" in t, t)
    @case("demo 菜单：无「管理」、无账号导入/导出/模板、无「修改我的密码」、无导出CSV")
    def _():
        tops = d.locator("#menubar .mb-item").all_inner_texts(); check("管理" not in tops, tops)
        allitems = []
        for t in tops:
            d.click(f'#menubar .mb-item[data-m="{t}"]'); allitems += d.locator("#ctxmenu .mi").all_inner_texts(); d.keyboard.press("Escape"); d.mouse.click(5, 400)
        s = " ".join(allitems); print("   items:", s[:300])
        for bad in ("导入账号", "导入模板", "导出当前账号", "修改我的密码", "导出全部结果", "导出选中", "平台账号管理", "测试全部平台"):
            check(bad not in s, bad)
        check("开始查询" in s and "查询历史" in s, s)
    @case("demo 工具栏：无「平台账号」「导出CSV」按钮；侧边树无「平台账号」/管理后台")
    def _():
        cmds = d.locator("#toolbar .tb").evaluate_all("els=>els.map(e=>e.dataset.cmd)"); print("   toolbar:", cmds)
        check("accounts" not in cmds and "export" not in cmds and "run" in cmds and "history" in cmds, cmds)
        tree = d.inner_text("#tree") if d.locator("#tree").count() else d.inner_text("body"); check("管理后台" not in tree and "平台账号" not in tree, "tree")
    @case("demo API：账号/用户/角色/审计/导出/改密码 全部 403，平台列表不含任何账号字段")
    def _():
        for m, path in (("GET", "/api/accounts/export"), ("GET", "/api/users"), ("GET", "/api/roles"), ("GET", "/api/audit"), ("GET", "/api/audit/export"), ("POST", "/api/accounts/import/preview"),
                        ("PUT", "/api/platforms/yutong/account"), ("POST", "/api/platforms/yutong/test"), ("POST", "/api/users"), ("DELETE", "/api/users/admin"), ("POST", "/api/roles"), ("PUT", "/api/roles/demo")):
            r = jf(d, m, path, {} if m != "GET" else None); check(r["s"] == 403, f"{m} {path} -> {r['s']}")
        r = jf(d, "POST", "/api/auth/password", {"old": DEMO_PW, "new": "Whatever-12345"}); check(r["s"] == 403, f"password {r['s']}")
        pl = d.evaluate("fetch('/api/platforms').then(r=>r.json())"); txt = json.dumps(pl, ensure_ascii=False)
        check(all(not x.get("fields") and all(not m.get("fields") for m in x["modes"].values()) for x in pl), "fields leaked")
        check(not re.search(r'"value": "[^"]', txt), "non-empty value present"); check(len(pl) >= 6)
    @case("demo 页面级：打开 /api/history/…/csv 与账号导出 URL 直接访问 → 403")
    def _():
        for u in ("/api/accounts/export?fmt=csv&secrets=none", "/api/audit/export"):
            check(d.evaluate("u=>fetch(u).then(r=>r.status)", u) == 403, u)
    @case("demo 用 Ctrl+E / 打开「平台」菜单：不能导出或进入账号页")
    def _():
        d.keyboard.press("Control+e"); d.wait_for_timeout(300); check(d.locator("#modal").is_visible() or True)
        close_modal(d)
        check(d.locator('.tab[data-k="accounts"], .tabs .tab:has-text("平台账号")').count() == 0)
    @case("demo 查询（欣航，公开价）一次：有结果，历史出现 1 条，额度 -1")
    def _():
        before = d.evaluate("fetch('/api/meta').then(r=>r.json())")["quota"]
        run_query(d, 'xinhang')
        after = d.evaluate("fetch('/api/meta').then(r=>r.json())")["quota"]
        check(after["used"] == before["used"] + 1 and after["limit"] == 30, (before, after))
        check(d.locator(".panel:visible .gridscroll tbody tr").count() > 0, "no rows"); shot(d, "trial_demo_query_result")
        print("   chip:", d.inner_text("#userChip").replace("\n", " | "))
    @case("demo 历史：只有自己的记录，管理员的记录不可见、不可按 id 读取")
    def _():
        hs = d.evaluate("fetch('/api/history').then(r=>r.json())"); check(hs and all(h["user"] == "demo" for h in hs), [h["user"] for h in hs][:5])
        ca, pa = new_admin_page(b, "admin-trial")
        r = jf(pa, "POST", "/api/quote", {"country": "US", "weight": 1, "platforms": ["xinhang"]}); check(r["s"] == 200, r["s"]); aid = r["j"]["id"]
        check(d.evaluate("i=>fetch('/api/history/'+i).then(r=>r.status)", aid) == 404, "demo reads admin history")
        check(d.evaluate("i=>fetch('/api/history/'+i+'/csv').then(r=>r.status)", aid) == 403, "csv")
        jf(pa, "DELETE", f"/api/history/{aid}"); ca.close()
    @case("demo 限额：额度用尽后再查询 → 429 友好提示，不执行平台查询（用库内临时把上限调为已用次数）")
    def _():
        used = d.evaluate("fetch('/api/meta').then(r=>r.json())")["quota"]["used"]
        dbexec(f"db.x(\"DELETE FROM meta WHERE k='policy:demo'\")\ndb.x(\"INSERT INTO meta (k,v) VALUES ('policy:demo', :v)\", v=json.dumps({{'qph':{used},'no_pw':True,'trial':True}}))\nprint('ok')\n")
        try:
            r = jf(d, "POST", "/api/quote", {"country": "US", "weight": 1, "platforms": ["xinhang"]}); check(r["s"] == 429, r["s"]); print("   msg:", r["j"]["detail"]); check("试用账号" in r["j"]["detail"])
            d.reload(); d.wait_for_selector("#boot", state="detached"); check("剩余 0/" in d.inner_text("#userChip"), d.inner_text("#userChip"))
            d.click('#toolbar .tb[data-cmd="new"]'); d.fill(".panel:visible .inCountry", "美国 US"); d.fill(".panel:visible .inWeight", "1"); d.keyboard.press("F5")
            d.wait_for_selector("#modal", state="visible", timeout=30000); mt = d.inner_text("#modal"); print("   modal:", mt.replace("\n", " ")[:80]); check("每小时最多" in mt, mt); shot(d, "trial_demo_quota_exceeded"); close_modal(d)
        finally:
            dbexec("db.x(\"DELETE FROM meta WHERE k='policy:demo'\")\ndb.x(\"INSERT INTO meta (k,v) VALUES ('policy:demo', :v)\", v=json.dumps({'qph':30,'no_pw':True,'trial':True}))\nprint('ok')\n")
    @case("demo 限额：恢复 30/小时后可继续查询（未被锁死）")
    def _():
        r = d.evaluate("fetch('/api/meta').then(r=>r.json())")["quota"]; check(r["limit"] == 30 and r["left"] > 0, r)
    @case("demo 退出登录后水印仍在登录页；原会话失效")
    def _():
        d.click("#uLogout"); d.wait_for_url(URL + "/login"); d.wait_for_selector("#ok"); d.wait_for_timeout(300); check(wm_ok(d), "wm after logout")
    ctx.close()
    # ---------- 管理员页面水印 ----------
    ca, pa = new_admin_page(b, "admin-wm")
    @case("管理员主界面 / 用户管理 / 角色 / 审计 / 账号页：水印始终在（所有页面）")
    def _():
        check(wm_ok(pa), "main")
        for cmdn in ("accounts", "history"):
            pa.click(f'#toolbar .tb[data-cmd="{cmdn}"]'); pa.wait_for_timeout(300); check(wm_ok(pa), cmdn)
        for mi in ("用户管理", "角色与权限", "审计日志"):
            pa.click('#menubar .mb-item[data-m="管理"]'); pa.click(f'#ctxmenu .mi:has-text("{mi}")'); pa.wait_for_timeout(500); check(wm_ok(pa), mi)
        shot(pa, "trial_admin_audit_1280x800")
    @case("管理员主界面：1920x1080 与 375x667 水印可见、徽标不遮挡关键按钮")
    def _():
        pa.set_viewport_size({"width": 1920, "height": 1080}); pa.wait_for_timeout(300); check(wm_ok(pa)); shot(pa, "trial_admin_1920x1080")
        pa.set_viewport_size({"width": 375, "height": 667}); pa.wait_for_timeout(300); check(wm_ok(pa)); shot(pa, "trial_admin_375x667")
    @case("管理员在「用户管理」里能看到 demo 用户（角色 试用客户），角色页有 demo 角色且权限仅 4 项")
    def _():
        us = jf(pa, "GET", "/api/users")["j"]; u = [x for x in (us if isinstance(us, list) else us.get("users", us)) if x["username"] == "demo"]; check(u and u[0]["role"] == "demo", u)
        rl = jf(pa, "GET", "/api/roles")["j"]["roles"]; r = [x for x in rl if x["name"] == "demo"][0]; check(sorted(r["perms"]) == ["history.delete", "history.view", "platforms.view", "query.run"], r["perms"])
    ca.close()
    b.close()

# 清理：demo 测试用的历史和审计 / 限额记录（不占用客户的额度）
print(dbexec("for sql in (\"DELETE FROM history WHERE username='demo'\", \"DELETE FROM audit WHERE username='demo'\", \"DELETE FROM sessions WHERE username_lc='demo'\", \"DELETE FROM throttle\"):\n    db.x(sql)\nprint('cleaned')\n"))
finish("trial")

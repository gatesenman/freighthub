"""云途 OMS2。

登录：passport.yunexpress.cn（密码 RSA 加密 + 阿里云验证码），用本机 Edge/Chrome（CDP 协议）完成一次登录并保存 Cookie；
询价：POST ucv2.yunexpress.cn/api/Home/FreightTrial（Cookie 认证，纯接口）。
会话过期（约 1~2 小时）后自动重新登录。
"""
import time

import requests

from . import openapi as oa
from .base import API_DOC_PUBLIC, ENV_FIELD, Adapter, AdapterError, Query, make_row, to_float

LOGIN_URL = "https://passport.yunexpress.cn/login/#/?returnUrl=https%3A%2F%2Foms2.yunexpress.cn&CustomerBusinessType=B2C"
API = "https://ucv2.yunexpress.cn/api/Home/FreightTrial"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


class YuntuAdapter(Adapter):
    id = "yuntu"
    desktop_only_login = True      # 登录模式要真实浏览器（CDP + 验证码），Serverless 网页版不可用
    mode_short_login = "OMS2 登录"
    mode_short_api = "云途开放API"
    name = "云途"
    system = "云途 OMS2（Vue + 多微服务）"
    mode = "浏览器登录一次取 Cookie，之后纯接口询价；过期自动重登"
    login_note = "登录需要本机 Edge / Chrome；出现验证码会弹出窗口，请手动完成。"
    envs = {"prod": "正式环境", "sandbox": "测试环境"}
    env_urls = {"prod": oa.YT_PROD, "sandbox": "https://openapi-sbx.yunexpress.cn"}
    api_doc_status = API_DOC_PUBLIC
    api_doc_url = "https://open.yunexpress.cn/"
    api_doc_note = ("云途开放平台 open.yunexpress.cn 申请应用，取得 appId / appSecret（及 sourceKey）；"
                    "OAuth2 client_credentials 换 token（POST /openapi/oauth2/token），请求头 token+date+sign（HMAC-SHA256）；"
                    "运价试算 GET /v1/price-trial/get。正式 openapi.yunexpress.cn，沙箱 openapi-sbx.yunexpress.cn（同样用 appId/appSecret 换 token）。")
    api_fields = [
        {"key": "app_id", "label": "appId", "secret": False},
        {"key": "app_secret", "label": "appSecret（apiKey）", "secret": True},
        {"key": "source_key", "label": "sourceKey", "secret": False, "optional": True},
        {"key": "access_token", "label": "手动 accessToken", "secret": True, "optional": True,
         "hint": "一般留空，自动用 appId/appSecret 换取（沙箱和正式环境都支持）；仅账号特殊时才手动填"},
        ENV_FIELD,
    ]
    note = "登录需要本机有 Edge / Chrome（Windows 自带 Edge 即可）。若出现验证码会弹出窗口，请手动完成。官方开放 API 开通后可改走官方接口。"

    # ---------- 登录 ----------
    def _browser_login(self, headless=True, wait=40):
        from .base import is_cloud
        if is_cloud():
            raise AdapterError("云途「登录账号」模式需要本机浏览器（含验证码），仅桌面版可用；网页版请改用「开放平台 API」授权", "manual")
        a = self.account()
        if not (a.get("username") and a.get("password")):
            raise AdapterError("尚未配置账号密码", "no_account")
        from .. import cdp
        try:
            br = cdp.Browser(headless=headless)
        except Exception as e:
            raise AdapterError(f"无法启动浏览器（需要 Edge 或 Chrome）：{str(e)[:120]}")
        try:
            br.send("Network.enable")
            br.send("Network.setUserAgentOverride", userAgent=UA, acceptLanguage="zh-CN")
            br.send("Page.enable")
            try:
                br.goto(LOGIN_URL, timeout=60)
                br.wait_for("!!document.querySelector('input[type=password]')", 30)
                br.type_into("input[type=text]", a["username"])
                br.type_into("input[type=password]", a["password"])
                time.sleep(0.5)
                br.click_selector("button", "登录")
            except RuntimeError as e:
                raise AdapterError(f"打开云途登录页失败：{e}")
            deadline = time.time() + wait
            ok = False
            while time.time() < deadline:
                time.sleep(1)
                try:
                    url = br.eval("location.href") or ""
                    names = {c["name"] for c in br.cookies()}
                    if "YUN_EXPRESS_AUTH" in names and "oms2.yunexpress.cn" in url:
                        ok = True
                        break
                    txt = (br.eval("document.body.innerText.slice(0,2000)") or "")
                except Exception:
                    continue
                if any(k in txt for k in ("密码错误", "账号或密码", "用户名或密码", "账户不存在")):
                    raise AdapterError("登录失败：账号或密码错误", "login_failed")
            if not ok:
                if headless:
                    return None  # 交给上层以有界面方式重试（可能需要人工验证码）
                raise AdapterError("登录超时：可能需要人工完成验证码", "manual")
            time.sleep(1.5)
            cookies = br.cookies()
            self.store.save_session(self.id, {"cookies": cookies, "ts": time.time()})
            return cookies
        finally:
            br.close()

    def _login(self):
        c = self._browser_login(headless=True, wait=40)
        if c is None:
            c = self._browser_login(headless=False, wait=150)
        return c

    def _jar(self, cookies):
        s = requests.Session()
        for c in cookies:
            s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
        s.headers.update({"User-Agent": UA, "Origin": "https://oms2.yunexpress.cn", "Referer": "https://oms2.yunexpress.cn/",
                          "Content-Type": "application/json"})
        return s

    def _call(self, cookies, body):
        s = self._jar(cookies)
        try:
            r = s.post(API, json=body, timeout=40)
        except requests.RequestException as e:
            raise AdapterError(f"网络请求失败：{e}")
        try:
            data = r.json()
        except ValueError:
            return None
        if r.status_code != 200 or not isinstance(data, list):
            return None
        return data

    # ---------- 对外 ----------
    def login_test(self):
        self.store.clear_session(self.id)
        self._login()
        return "登录成功，会话已保存（只读，未做任何修改）。"

    def login_quote(self, q: Query):
        body = {
            "CountryCode": q.country, "Weight": f"{q.weight:g}", "UnitWeight": "kg", "PackageType": "0",
            "PostCode": q.postcode or None,
            "Length": f"{q.length:g}" if q.dims() else "", "Width": f"{q.width:g}" if q.dims() else "",
            "Height": f"{q.height:g}" if q.dims() else "", "UnitLength": "cm", "ProductCategory": None,
            "Origin": "YT-SZ", "OriginName": "深圳市", "Origins": "YT-SZ---深圳市", "IsSignInfo": "",
            "CustomerstatusCode": None,
        }
        sess = self.store.load_session(self.id)
        data = self._call(sess["cookies"], body) if sess and sess.get("cookies") else None
        if data is None:  # 无会话或已过期 -> 自动重新登录
            cookies = self._login()
            data = self._call(cookies, body)
            if data is None:
                raise AdapterError("登录成功但询价接口仍未返回报价")
        rows = []
        for it in data:
            total = to_float(it.get("RmbTotalFee")) or to_float(it.get("TotalFee"))
            if total is None:
                continue
            fees = []
            for k, lab in (("ShippingFee", "运费"), ("RegistrationFee", "挂号费"), ("FuelFee", "燃油费"), ("SundryFee", "杂费")):
                v = to_float(it.get(k))
                if v:
                    fees.append({"label": lab, "value": f"{v:g}"})
            remark = "；".join(x for x in [it.get("pro_advantages") or "", it.get("Remark") or ""] if x)
            rows.append(make_row(
                self.id, self.name, it.get("CName") or it.get("Name") or it.get("Code"), total,
                group=it.get("product_groupname"), currency="CNY", days=it.get("DeliveryDays") or "",
                track=(it.get("Track") == "Y") if it.get("Track") else None, fees=fees, remark=remark,
            ))
        return rows

    # ---------- 开放平台 API ----------
    def _api_base(self):
        return "https://openapi-sbx.yunexpress.cn" if self.account().get("env") == "sandbox" else oa.YT_PROD

    def _api_token(self, force=False):
        a = self.account()
        if a.get("access_token"):
            return a["access_token"]
        sess = None if force else self.store.load_session(self.id + "_api")
        if sess and sess.get("base") == self._api_base() and sess.get("app_id") == a.get("app_id") \
                and sess.get("exp", 0) > time.time() + 60:
            return sess["token"]
        tok, ttl = oa.yt_token(self._api_base(), a["app_id"], a["app_secret"], a.get("source_key", ""))
        self.store.save_session(self.id + "_api", {"token": tok, "exp": time.time() + ttl, "base": self._api_base(),
                                                   "app_id": a["app_id"]})
        return tok

    def _api_get(self, path, params):
        a = self.account()
        # 401/403 时依次：① 刷新 token 重试一次（token 过期）② 换"签名 uri 带查询串"的写法重试一次（文档两种示例并存）
        steps = [(False, False), (True, False), (True, True)] if not a.get("access_token") else [(False, False), (False, True)]
        code, data = 0, None
        for force, qs_in_uri in steps:
            code, data = oa.yt_get(self._api_base(), self._api_token(force=force), a["app_secret"], path, params, with_query_in_uri=qs_in_uri)
            if code not in (401, 403):
                break
        if code in (401, 403):
            raise AdapterError(f"云途 API 鉴权失败（HTTP {code}）：token 或签名无效，请核对 appId / appSecret", "login_failed")
        if code == 429:
            raise AdapterError("云途 API 触发限流，请稍后再试")
        if not isinstance(data, dict) or data.get("success") is False or code >= 400:
            msg = (data.get("msg") or data.get("message")) if isinstance(data, dict) else ""
            raise AdapterError(f"云途 API 错误（HTTP {code}）：{msg or str(data)[:100]}")
        return data

    def api_test(self):
        if not self.has_account():
            raise AdapterError("尚未填写 appId 和 appSecret", "no_account")
        data = self._api_get("/v1/basic-data/countries/getlist", {})
        n = len(data.get("result") or [])
        return f"开放平台 API 鉴权成功（国家列表 {n} 项）。"

    def api_quote(self, q: Query):
        if not self.has_account():
            raise AdapterError("待配置：需要云途 appId 和 appSecret", "no_account")
        params = {"country_code": q.country, "weight": f"{q.weight:g}", "weight_unit": "KG",
                  "postal_code": q.postcode or None}
        if q.dims():
            params.update({"length": f"{q.length:g}", "width": f"{q.width:g}", "height": f"{q.height:g}", "size_unit": "CM"})
        data = self._api_get("/v1/price-trial/get", params)
        rows = []
        for it in data.get("result") or []:
            total = to_float(it.get("convert_amount")) if str(it.get("convert_currency") or "").upper() in ("RMB", "CNY") \
                else None
            if total is None:
                total = to_float(it.get("calculate_amount"))
            if total is None:
                continue
            rows.append(make_row(self.id, self.name, it.get("product_name") or it.get("product_code"), total,
                                 currency="CNY", days=it.get("interval_day") or "",
                                 remark="；".join(x for x in [it.get("price_name") or "", it.get("fee_name") or ""] if x)))
        return rows

"""跨境 V5 / tms-saas 系统（中航、环洋共用）。

请求头鉴权：version / sign / token。算法来自该系统前端 JS：
  sign  = MD5( Base64(排序后的 k=v&k=v) + 固定盐 ).upper()
  token = 字符替换( Base64( {"timestamp","nonce","token"} 的 JSON ) )
"""
import base64
import hashlib
import json
import random
import string
import time

import requests

from . import net as _net

from . import openapi as oa
from .base import API_DOC_PUBLIC, ENV_FIELD, Adapter, AdapterError, Query, make_row, to_float

SALT = "I1j0l9n4DF3Aac|spP03DGqd4msbb"


def _b64(s):
    return base64.b64encode(s.encode()).decode()


def _mix(s):
    return s.replace("a", "-").replace("c", "#").replace("x", "^").replace("M", "$")


def _rnd(n=8):
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


class OISMixin:
    """哲盟 / Intelink OIS 对外标准接口（V5、Q5 系统通用）。公开文档 help.intelink.net.cn（POS.OIS.C）：
    getAuth 取 token（3 小时）→ 头 token/sign/version（sign = MD5(Base64(排序参数)+appSecret) 大写）。
    本适配器用到：getProductList（路线/销售产品列表）、queryQuoteList（运价试算，快递小包）。
    正式域名 https://openapi.intelink.net.cn；中航/环洋（V5）与雨桐（Q5）同一套接口，各自用自己系统里的 appKey/appSecret。"""
    api_impl = True
    api_quote_ok = True
    api_doc_url = "https://help.intelink.net.cn/#/help?systemCode=POS.OIS.C"
    api_doc_status = API_DOC_PUBLIC
    api_fields = [
        {"key": "app_key", "label": "appKey", "secret": False},
        {"key": "app_secret", "label": "appSecret", "secret": True},
        {"key": "api_base", "label": "OIS 接口域名", "secret": False, "optional": True,
         "hint": "默认 https://openapi.intelink.net.cn（正式环境）；一般不用改"},
        {"key": "products", "label": "只查指定产品（可选）", "secret": False, "optional": True, "common": True,
         "hint": "销售产品/路线编码，英文逗号分隔；留空 = 查全部可用产品"},
        ENV_FIELD,
    ]
    # 哲盟 OIS 没有公开的测试域名：测试环境需要服务商给的「测试环境接口域名」+ 对应的 appKey/appSecret，分开保存
    envs = {"prod": "正式环境", "sandbox": "测试环境"}
    sandbox_needs_base = True
    env_urls = {"prod": "https://openapi.intelink.net.cn"}

    def _ois_doc(self):
        return ("哲盟 OIS 对外标准接口（help.intelink.net.cn，POS.OIS.C）公开文档：授权 getAuth、渠道列表 getProductList、"
                "运价试算 queryQuoteList、轨迹 queryTraceoutList、下单等。appKey/appSecret 在系统后台「API 授权」获取"
                "（只读查价不会下单；请勿点「重置」，会让旧密钥失效）。")

    def _ois(self):
        a = self.account()
        base = (a.get("api_base") or "").strip()
        if not base and self.is_test_env:      # 测试环境绝不回退到正式域名（否则测试密钥会打到正式环境）
            raise AdapterError("测试环境需要填写服务商提供的「测试环境接口域名」", "no_account")
        return base or getattr(self, "ois_default", None), a["app_key"], a["app_secret"]

    def _ois_token(self, force=False):
        base, key, sec = self._ois()
        sid = self.id + "_api"
        sess = self.store.load_session(sid)
        if (not force and sess and sess.get("kind") == "ois" and sess.get("key") == key
                and sess.get("base") == oa.ois_base(base) and sess.get("exp", 0) > time.time() + 300):
            return sess["token"]
        body = oa.ois_auth(base, key, sec)
        self.store.save_session(sid, {"kind": "ois", "key": key, "base": oa.ois_base(base), "token": body["token"],
                                      "clno": body.get("clno"), "coid": body.get("coid"), "exp": time.time() + 3 * 3600 - 600})
        return body["token"]

    def _ois_call(self, path, form=None):
        base, _, sec = self._ois()
        try:
            return oa.ois_call(base, sec, self._ois_token(), path, form)
        except AdapterError as e:
            if e.kind != "login_failed" or "过期" not in str(e):
                raise
            return oa.ois_call(base, sec, self._ois_token(force=True), path, form)

    def _ois_products(self):
        res = self._ois_call("/ois/order/getProductList", {})
        return res.get("body") or []

    def api_test(self):
        if not self.has_account():
            raise AdapterError("尚未填写 appKey 和 appSecret", "no_account")
        base, key, sec = self._ois()
        body = oa.ois_auth(base, key, sec)
        self.store.save_session(self.id + "_api", {"kind": "ois", "key": key, "base": oa.ois_base(base), "token": body["token"],
                                                   "clno": body.get("clno"), "coid": body.get("coid"), "exp": time.time() + 3 * 3600 - 600})
        prods = self._ois_products()
        return f"OIS 授权成功（客户 {body.get('clno') or '-'} / 公司 {body.get('coid') or '-'}），渠道列表 {len(prods)} 个产品，运价试算接口可用。"

    def api_quote(self, q: Query):
        if not self.has_account():
            raise AdapterError("待配置：需要 appKey 和 appSecret", "no_account")
        a = self.account()
        req = {"weig": q.weight, "destNo": q.country, "packing": "WPX"}
        if q.postcode:
            req["reZip"] = q.postcode
        if q.dims():
            req["pieces"] = [{"actual": q.weight, "length": q.length, "width": q.width, "height": q.height}]
        codes = [c.strip() for c in (a.get("products") or "").replace("，", ",").split(",") if c.strip()]
        if codes:
            req["hubinCodes"] = codes
        res = self._ois_call("/tms/expose/queryQuoteList", {"body1": json.dumps(req, separators=(",", ":"))})
        groups = {}
        try:
            groups = {p.get("code"): p.get("hubType") or "" for p in self._ois_products()}
        except AdapterError:
            pass
        rows = []
        for it in res.get("body") or []:
            total = to_float(it.get("standardFeeSum"))
            if not total or total <= 0 or total >= 1_000_000:   # 1000000 是系统的「无报价」占位值
                continue
            fees = []
            for k, lab in (("standardCharge", "运费"), ("fuelCharge", "燃油"), ("otherStandardFeeSum", "附加费")):
                v = to_float(it.get(k))
                if v:
                    fees.append({"label": lab, "value": f"{v:g}"})
            for f in it.get("otherStandardFeeList") or []:
                v = to_float(f.get("chargestandard"))
                if v:
                    fees.append({"label": f.get("ardedName") or f.get("ardedCode") or "附加费", "value": f"{v:g}"})
            if it.get("divsize"):
                fees.append({"label": "材积除数", "value": f"{it['divsize']:g}" if isinstance(it["divsize"], (int, float)) else str(it["divsize"])})
            if total < 10:
                fees.append({"label": "提示", "value": "价格极低，疑似境内/中港段而非国际运价"})
            remark = " ".join(x for x in [it.get("zoneName") or "", it.get("formulaView") or "", (it.get("remark") or "").strip()] if x)
            rows.append(make_row(self.id, self.name, it.get("name") or it.get("code"), total,
                                 currency=it.get("standardCur") or "CNY", days=it.get("aging") or "",
                                 group=groups.get(it.get("code")) or "", fees=fees, remark=remark[:300]))
        return rows


class V5Adapter(Adapter):
    mode_short_login = "tms-saas 登录"
    mode_short_api = "OIS 开放API"
    api = ""
    referer = ""
    login_fields = [
        {"key": "username", "label": "账号", "secret": False},
        {"key": "password", "label": "密码", "secret": True},
        {"key": "company", "label": "公司编号（可选）", "secret": False, "optional": True},
    ]
    system = "跨境 V5 / tms-saas（Vue）"
    mode = "纯接口：复现请求头 sign/token 算法，登录后调用报价接口，无需浏览器"

    # ---- 底层 ----
    def _headers(self, token, params=None):
        r = {"timestamp": int(time.time() * 1000), "nonce": _rnd(), "version": "1.0", "token": token}
        r.update(params or {})
        joined = "&".join(f"{k}={r[k]}" for k in sorted(r) if (r[k] or r[k] == 0))
        sign = hashlib.md5((_b64(joined) + SALT).encode()).hexdigest().upper() if joined else ""
        tok = _mix(_b64(json.dumps({"timestamp": r["timestamp"], "nonce": r["nonce"], "token": r["token"]}, separators=(",", ":"))))
        return {"version": "1.0", "sign": sign, "token": tok, "locale": "zhCN", "Referer": self.referer}

    def _post(self, path, data, token):
        try:
            r = _net.post(self.api + path, data=data, headers=self._headers(token, data), timeout=40)
            return r.json()
        except requests.RequestException as e:
            raise AdapterError(f"网络请求失败：{e}")
        except ValueError:
            raise AdapterError("服务端返回了非 JSON 内容，可能被网关拦截")

    def _login(self):
        a = self.account()
        if not (a.get("username") and a.get("password")):
            raise AdapterError("尚未配置账号密码", "no_account")
        data = {"userNo": a["username"], "password": _mix(_b64(a["password"])), "companyNo": a.get("company", ""), "domain": ""}
        res = self._post("user/oms/login", data, None)
        if res.get("result_code") != 0:
            msg = res.get("message") or res.get("solution") or "登录失败"
            self.store.clear_session(self.id)
            raise AdapterError(f"登录失败：{msg}", "login_failed")
        token = res["body"]["token"]
        self.store.save_session(self.id, {"token": token, "ts": time.time()})
        return token

    def _token(self, force=False):
        if not force:
            s = self.store.load_session(self.id)
            if s and s.get("token"):
                return s["token"]
        return self._login()

    # ---- 对外 ----
    def login_test(self):
        self._token(force=True)
        return "登录成功，会话已保存（只读，未做任何修改）。"

    def login_quote(self, q: Query):
        data = {
            "statTimeItem": time.strftime("%a %b %d %Y %H:%M:%S GMT+0800"),
            "destNo": q.country, "weig": f"{q.weight:g}", "packing": "WPX",
            "length": f"{q.length:g}" if q.dims() else "", "width": f"{q.width:g}" if q.dims() else "",
            "highth": f"{q.height:g}" if q.dims() else "", "includeNotUsing": "0",
            "decValue": "0", "decValueCur": "USD", "reZip": q.postcode or "",
            "statTime": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        token = self._token()
        res = self._post("oms/bms/quoteObj/list", data, token)
        if res.get("result_code") in (400, 401, 404):  # 会话过期 -> 自动重新登录一次
            token = self._token(force=True)
            res = self._post("oms/bms/quoteObj/list", data, token)
        if res.get("result_code") != 0:
            raise AdapterError(res.get("solution") or res.get("message") or f"报价接口错误 {res.get('result_code')}")
        items = (res.get("body") or {}).get("list") or []
        rows = []
        for it in items:
            total = to_float(it.get("standardFeeSum"))
            if total is None:
                total = to_float(it.get("quoteFee"))
            if total is None:
                continue
            fees = []
            if it.get("unitPrice") not in (None, ""):
                fees.append({"label": "单价", "value": f"{it.get('unitPrice')} {it.get('currency') or ''}".strip()})
            if it.get("fuelRate"):
                fees.append({"label": "燃油费率", "value": str(it.get("fuelRate"))})
            if it.get("formulaView"):
                fees.append({"label": "计费公式", "value": str(it.get("formulaView"))[:200]})
            rows.append(make_row(
                self.id, self.name, it.get("name") or it.get("quoteName") or "", total,
                group=it.get("quoteName"), currency=it.get("standardCur") or "CNY",
                days=it.get("aging") or "", fees=fees,
                remark=(f"有效期至 {str(it.get('endDate'))[:10]}" if it.get("endDate") else ""),
            ))
        return rows


class ZhonghangAdapter(OISMixin, V5Adapter):
    id = "zhonghang"
    name = "中航"
    api = "http://39.108.221.228:21000/tms-saas-all/"
    referer = "http://39.108.221.228:21000/viplogin"
    note = "登录模式：无验证码，服务器为裸 IP + HTTP。API 模式：哲盟 OIS appKey/appSecret（getProductList + queryQuoteList 运价试算）；需在 V5 后台取得 appKey/appSecret。"
    api_doc_note = property(lambda self: self._ois_doc())


class HuanyangAdapter(OISMixin, V5Adapter):
    id = "huanyang"
    name = "环洋"
    api = "https://vip.hyeline.net/tms-saas-oms/"
    referer = "https://oms.hyexp.net"
    api_doc_note = property(lambda self: self._ois_doc())
    ois_default = "https://ekttech.net/pos-web"
    env_urls = {"prod": "https://ekttech.net/pos-web（环洋对接环境，目前作正式环境使用）"}
    api_doc_url = "https://gitee.com/xis_co/print"
    api_fields = [dict(f, hint="默认 https://ekttech.net/pos-web（环洋对接环境）；一般不用改") if f["key"] == "api_base" else f
                  for f in OISMixin.api_fields]
    note = ("API 模式：易快通 OIS（gitee.com/xis_co/print），与哲盟 OIS 同一套签名/接口，对接环境 URL 默认 https://ekttech.net/pos-web，"
            "appKey/appSecret 在后台「API 授权」获取（只读查价，切勿点「重新生成密钥」）。登录模式：复用 V5 登录，须账号密码与 oms.hyexp.net 一致。")

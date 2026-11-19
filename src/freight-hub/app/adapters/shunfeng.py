"""顺丰国际：两套开放平台。
1) 顺丰国际开放平台 openapi.sf.global（默认）：appKey/appSecret 取 token（2 小时），业务报文用 AES 秘钥加密、
   SHA256 签名，统一入口 /openapi/api/dispatch，用 msgType 分发。
   本适配器用到：IPE_QUERY_PRODUCT_LIST（国际产品查询）、IUOP_QUERY_FREIGHT（预估运费）、GTS_QUERY_TRACK（轨迹，仅文档）。
   应用需在控制台「关联 API」并有 IUOP 客户编码，否则返回 1004「没权限」。
2) 丰桥 open.sf-express.com（备选）：OAuth2 / msgDigest + COM_RECE_IUOP_QUERY_FREIGHT。
登录模式：国际统一订单平台有图标点选验证码，暂未实现。"""
import time
from concurrent.futures import ThreadPoolExecutor

from . import openapi as oa
from .base import API_DOC_PUBLIC, ENV_FIELD, Adapter, AdapterError, Query, make_row, to_float

KIND_OPTS = [{"value": "sfglobal", "label": "顺丰国际开放平台 openapi.sf.global（AES+签名）"},
             {"value": "fengqiao", "label": "丰桥开放平台 open.sf-express.com（备选）"}]
MAX_PRODUCTS = 40


class ShunfengAdapter(Adapter):
    id = "shunfeng"
    mode_short_login = "sf.global 登录"
    mode_short_api = "sf.global API"
    default_mode = "api"     # 登录模式未实现，默认走开放平台 API
    name = "顺丰国际"
    system = "顺丰国际开放平台 openapi.sf.global"
    mode = "API 模式：AES 加密 + SHA256 签名，IUOP_QUERY_FREIGHT 预估运费（逐产品试算）"
    login_impl = False
    login_note = "登录有图标点选验证码，登录模式暂未实现（保存账号备用）。建议使用开放平台 API 模式。"
    legacy_sandbox_prefix = "sb_"     # 旧版把沙箱密钥存成 sb_app_key 等，读取时并入测试环境
    envs = {"prod": "正式环境", "sandbox": "测试环境"}
    env_urls = {"prod": "https://api-ifsp.sf.global（丰桥 https://sfapi.sf-express.com）",
                "sandbox": "https://api-ifsp-sit.sf.global（丰桥 https://sfapi-sbox.sf-express.com）"}
    api_doc_status = API_DOC_PUBLIC
    api_doc_url = "https://openapi-portal.sf.global/document?firstMenu=DOC&secondMenu=75"
    api_doc_note = ("控制台 openapi.sf.global →「API授权」新建应用，取得 APP Key / APP Secret / AES 秘钥，并在应用里关联所需接口"
                    "（国际产品查询、预估运费等）；客户编码 customerCode 在 IUOP「管理中心-基本信息」。"
                    "沙箱 api-ifsp-sit.sf.global 用控制台「前往沙盒」里的沙箱密钥。丰桥（open.sf-express.com）作为备选。")
    api_fields = [
        {"key": "api_kind", "common": True, "label": "接口平台", "secret": False, "optional": True, "type": "select",
         "options": KIND_OPTS, "default": "sfglobal"},
        {"key": "app_key", "label": "[顺丰国际] APP Key", "secret": False, "optional": True},
        {"key": "app_secret", "label": "[顺丰国际] APP Secret", "secret": True, "optional": True},
        {"key": "aes_key", "label": "[顺丰国际] AES 秘钥", "secret": True, "optional": True,
         "hint": "43 位 Base64 字符串，控制台应用详情页"},
        {"key": "customer_code", "label": "客户编码 customerCode", "secret": False, "optional": True,
         "hint": "IUOP 管理中心-基本信息-客户编号；预估运费必填"},
        {"key": "pay_month_card", "label": "月结账号（可选）", "secret": False, "optional": True,
         "hint": "有白名单限制时必填"},
        {"key": "products", "common": True, "label": "国际产品码（可选）", "secret": False, "optional": True,
         "hint": "留空 = 自动取「国际产品查询」列表全部试算（最多 40 个）；或填 INT0007,INT0008"},
        {"key": "sender_post", "common": True, "label": "寄件邮编", "secret": False, "optional": True, "default": "518052"},
        ENV_FIELD,
        {"key": "partner_id", "label": "[丰桥] 顾客编码 partnerID", "secret": False, "optional": True},
        {"key": "check_word", "label": "[丰桥] 校验码 checkWord", "secret": True, "optional": True},
        {"key": "auth", "common": True, "label": "[丰桥] 鉴权方式", "secret": False, "optional": True, "type": "select",
         "default": "oauth2",
         "options": [{"value": "oauth2", "label": "OAuth2（推荐）"}, {"value": "digest", "label": "数字签名 msgDigest"}]},
    ]
    note = "登录模式暂未实现（点选验证码）；API 模式默认走顺丰国际开放平台（AES+签名），丰桥为备选。"

    # ---------- 通用 ----------
    def _kind(self):
        return "fengqiao" if self.account().get("api_kind") == "fengqiao" else "sfglobal"

    def _env(self):
        return "sandbox" if self.account().get("env") == "sandbox" else "prod"

    def has_account(self):
        a = self.account()
        if self.auth_mode() != "api":
            return False
        keys = ("partner_id", "check_word") if self._kind() == "fengqiao" else ("app_key", "app_secret", "aes_key")
        return all(a.get(k) for k in keys)

    def _products(self):
        return [p.strip() for p in (self.account().get("products") or "").replace("，", ",").split(",") if p.strip()]

    def login_quote(self, q: Query):
        raise AdapterError("顺丰国际登录模式暂未实现（点选验证码），请切换到开放平台 API 模式", "pending")

    def login_test(self):
        raise AdapterError("顺丰国际登录模式暂未实现，请切换到开放平台 API 模式", "pending")

    def api_test(self):
        if not self.has_account():
            need = "partnerID 和 checkWord" if self._kind() == "fengqiao" else "APP Key、APP Secret 和 AES 秘钥"
            raise AdapterError(f"尚未填写 {need}", "no_account")
        return self._test_fq() if self._kind() == "fengqiao" else self._test_sfg()

    def api_quote(self, q: Query):
        if not self.has_account():
            raise AdapterError("待配置：请先填写顺丰国际开放平台凭据", "no_account")
        return self._quote_fq(q) if self._kind() == "fengqiao" else self._quote_sfg(q)

    # ---------- 顺丰国际开放平台 openapi.sf.global ----------
    def _sfg_token(self, force=False):
        a, env = self.account(), self._env()
        sess = self.store.load_session(self.id + "_api")
        if (not force and sess and sess.get("kind") == "sfglobal" and sess.get("env") == env
                and sess.get("ak") == a["app_key"] and sess.get("exp", 0) > time.time() + 120):
            return sess["token"]
        try:
            tok, ttl = oa.sfg_token(env, a["app_key"], a["app_secret"])
        except AdapterError as e:
            if env == "sandbox" and "1005" in str(e):
                raise AdapterError("顺丰国际沙箱授权失败：1005 App is not exist。沙箱是独立环境，"
                                   "需要使用控制台「前往沙盒」里的沙箱 APP Key / 密钥 / AES 秘钥（正式密钥在沙箱不存在）；"
                                   "沙箱地址 https://api-ifsp-sit.sf.global（http 会返回 500）。", "login_failed")
            raise
        self.store.save_session(self.id + "_api", {"kind": "sfglobal", "token": tok, "exp": time.time() + ttl,
                                                    "env": env, "ak": a["app_key"]})
        return tok

    def _sfg_call(self, msg_type, payload):
        a, env = self.account(), self._env()
        try:
            return oa.sfg_call(env, a["app_key"], a["aes_key"], self._sfg_token(), msg_type, payload)
        except AdapterError as e:
            if "500" in str(e) and "token" in str(e).lower():  # token 失效，重新取一次
                return oa.sfg_call(env, a["app_key"], a["aes_key"], self._sfg_token(force=True), msg_type, payload)
            raise

    @staticmethod
    def _no_perm(e):
        return "1004" in str(e) and "没权限" in str(e)

    def _no_perm_msg(self, what):
        return (f"授权成功（已取得 token），但该应用没有「{what}」接口权限（1004 没权限）。请到 openapi.sf.global 控制台 →"
                "「API授权」→ 应用详情 →「关联 API」勾选 国际产品查询(IPE_QUERY_PRODUCT_LIST)、预估运费(IUOP_QUERY_FREIGHT) 等接口，"
                "并确认 IUOP 账号已开通；沙箱请改用控制台「前往沙盒」里的沙箱密钥并在环境里选「沙箱」。")

    def _sfg_product_list(self):
        res = self._sfg_call("IPE_QUERY_PRODUCT_LIST", {})
        if res.get("success") is False:
            raise AdapterError(f"国际产品查询失败：{res.get('code')} {res.get('msg') or ''}")
        return res.get("data") or []

    def _test_sfg(self):
        self._sfg_token(force=True)
        env = "测试" if self._env() == "sandbox" else "正式"
        try:
            prods = self._sfg_product_list()
        except AdapterError as e:
            if self._no_perm(e):
                raise AdapterError(self._no_perm_msg("国际产品查询"), "manual")
            raise
        return f"顺丰国际开放平台授权成功（{env}环境），AES 报文加解密正常，国际产品查询返回 {len(prods)} 个产品。"

    def _quote_sfg(self, q: Query):
        a = self.account()
        if not a.get("customer_code"):
            raise AdapterError("预估运费需要客户编码 customerCode（IUOP 管理中心-基本信息-客户编号），请在账号页补填", "no_account")
        names, codes = {}, self._products()
        try:
            plist = self._sfg_product_list()
        except AdapterError as e:
            plist = []
            if not codes:
                if self._no_perm(e):
                    raise AdapterError(self._no_perm_msg("国际产品查询"), "manual")
                raise
        for p in plist:
            if p.get("interProductCode"):
                names[p["interProductCode"]] = p.get("displayProductName") or p["interProductCode"]
        if not codes:
            codes = list(names)[:MAX_PRODUCTS]
        if not codes:
            raise AdapterError("没有可试算的国际产品（国际产品查询为空，或未填写产品码）")

        def one(code):
            msg = {"customerCode": a["customer_code"], "interProductCode": code, "senderCountry": "CN",
                   "receiverCountry": q.country, "senderPostCode": a.get("sender_post") or "518052",
                   "receiverPostCode": q.postcode or "", "weight": f"{q.weight:g}", "currency": "CNY"}
            # 文档形态的嵌套寄/收件信息与重量字段一并带上（多余字段服务端忽略）
            msg["senderInfo"] = {"country": "CN", "postCode": msg["senderPostCode"]}
            msg["receiverInfo"] = {"country": q.country, "postCode": q.postcode or ""}
            msg["parcelQuantity"] = 1
            msg["parcelTotalWeight"] = round(q.weight, 3)
            if a.get("pay_month_card"):
                msg["payMonthCard"] = a["pay_month_card"]
            try:
                res = self._sfg_call("IUOP_QUERY_FREIGHT", msg)
            except AdapterError as e:
                return code, None, str(e)
            if res.get("success") is False:
                return code, None, f"{res.get('code')} {res.get('msg') or ''}".strip()
            return code, res.get("data") or {}, ""

        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(one, codes))
        rows, errs = [], []
        for code, d, err in results:
            if d is None:
                errs.append(f"{code}: {err}")
                continue
            total = to_float(d.get("discountedRate")) or to_float(d.get("totalFreight"))
            if not total:
                continue
            fees = [{"label": "结算重(kg)", "value": str(d.get("weight"))}] if d.get("weight") else []
            rows.append(make_row(self.id, self.name, f"{names.get(code) or code}（{code}）", total,
                                 currency=d.get("currency") or "CNY", fees=fees,
                                 remark="预估运费，未含其他附加费" + ("；测试环境模拟数据" if self._env() == "sandbox" else "")))
        if not rows and errs:
            if all(self._no_perm(AdapterError(x)) for x in errs):
                raise AdapterError(self._no_perm_msg("预估运费"), "manual")
            raise AdapterError("；".join(errs[:3])[:240])
        return rows

    # ---------- 丰桥 open.sf-express.com（备选） ----------
    def _fq_call(self, code, msg):
        a, env = self.account(), self._env()
        if a.get("auth") == "digest":
            return oa.sf_service(env, a["partner_id"], code, msg, check_word=a["check_word"])
        tok = self._fq_token(env)
        try:
            return oa.sf_service(env, a["partner_id"], code, msg, access_token=tok)
        except AdapterError as e:
            if e.kind != "login_failed":
                raise
            self.store.clear_session(self.id + "_api")
            return oa.sf_service(env, a["partner_id"], code, msg, access_token=self._fq_token(env))

    def _fq_token(self, env):
        a = self.account()
        sess = self.store.load_session(self.id + "_api")
        if sess and sess.get("kind") == "fengqiao" and sess.get("env") == env and sess.get("pid") == a["partner_id"] \
                and sess.get("exp", 0) > time.time() + 60:
            return sess["token"]
        tok, ttl = oa.sf_token(env, a["partner_id"], a["check_word"])
        self.store.save_session(self.id + "_api", {"kind": "fengqiao", "token": tok, "exp": time.time() + ttl,
                                                    "env": env, "pid": a["partner_id"]})
        return tok

    def _test_fq(self):
        if self.account().get("auth") == "digest":
            raise AdapterError("数字签名方式没有独立的鉴权接口；请切换为 OAuth2 测试，或直接用「查价」验证", "manual")
        self._fq_token(self._env())
        return "丰桥 OAuth2 授权成功，已取得 accessToken。完整链路需 customerCode 与产品码，请用查价验证。"

    def _quote_fq(self, q: Query):
        a = self.account()
        if not a.get("customer_code") or not self._products():
            raise AdapterError("丰桥预估运费需要 customerCode 与国际产品码（interProductCode）", "no_account")
        rows, errs = [], []
        for prod in self._products():
            msg = {"customerCode": a["customer_code"], "interProductCode": prod, "senderCountry": "CN",
                   "receiverCountry": q.country, "senderPostCode": a.get("sender_post") or "518052",
                   "receiverPostCode": q.postcode or "", "weight": f"{q.weight:g}", "currency": "CNY"}
            try:
                res = self._fq_call("COM_RECE_IUOP_QUERY_FREIGHT", msg)
            except AdapterError as e:
                if e.kind == "login_failed":
                    raise
                errs.append(f"{prod}: {e}")
                continue
            d = res.get("msgData") or {}
            total = to_float(d.get("discountedRate")) or to_float(d.get("totalFreight"))
            if total is None:
                continue
            rows.append(make_row(self.id, self.name, f"顺丰国际 {prod}", total, currency=d.get("currency") or "CNY",
                                 fees=[{"label": "结算重(kg)", "value": str(d.get("weight") or "")}] if d.get("weight") else [],
                                 remark="丰桥预估运费，未含其他附加费"))
        if not rows and errs:
            raise AdapterError("；".join(errs)[:200])
        return rows

"""燕文：官方开放平台 API（open.yw56.com.cn）。运价试算 calc.list、国家列表 common.country.getlist。
MD5 签名已用燕文官方文档示例逐字节验证（sign=bc5f1792…），沙箱 user_id=100000 可直接联调。
文档（opendocs.yw56.com.cn）：成功 code="0"；calc.list 实测成功返回 code=200；countryId 可传二字码或 common.country.getlist 的数字 id；部分国家（如英国）需 postCode。
网页登录有滑块验证码，登录模式暂不实现。"""
from . import openapi as oa
from .base import API_DOC_PUBLIC, ENV_FIELD, Adapter, AdapterError, Query, make_row, to_float

SANDBOX_USER = "100000"
SANDBOX_TOKEN = "D6140AA383FD8515B09028C586493DDB"


class YanwenAdapter(Adapter):
    id = "yanwen"
    mode_short_login = "燕文登录"
    mode_short_api = "燕文开放API"
    default_mode = "api"     # 登录模式未实现，默认走开放平台 API
    name = "燕文"
    system = "燕文开放平台（官方 API）"
    mode = "官方 API 运价试算 calc.list，MD5 签名；需燕文销售开通权限"
    login_impl = False
    login_note = "燕文网页登录带滑块验证码，登录模式暂未实现（保存账号备用）。建议使用开放平台 API 模式。"
    envs = {"prod": "正式环境", "sandbox": "测试环境"}
    env_urls = {"prod": oa.YW_PROD, "sandbox": oa.YW_SANDBOX}
    api_doc_status = API_DOC_PUBLIC
    api_doc_url = "https://opendocs.yw56.com.cn/webfile/7417494175517511680/"
    api_doc_note = "向燕文销售申请开放平台权限，取得客户号 user_id 与制单账号密钥 apitoken；MD5 签名；测试环境 open-fat.yw56.com.cn 用公开测试账号（user_id=100000），正式环境用自己的客户号和 apitoken（客户中心系统-账号管理-制单账号管理）。"
    api_fields = [
        {"key": "user_id", "label": "客户号 user_id", "secret": False},
        {"key": "api_token", "label": "密钥 apitoken", "secret": True},
        {"key": "city_id", "label": "出发仓 cityId", "secret": False, "optional": True, "default": "01", "hint": "默认 01", "common": True},
        ENV_FIELD,
    ]
    note = "网页登录有滑块验证码，推荐使用开放平台 API 模式：向燕文销售申请“运价试算”权限后填入客户号和密钥。"

    def _base(self):
        return oa.YW_SANDBOX if self.account().get("env") == "sandbox" else oa.YW_PROD

    def _call(self, method, data):
        a = self.account()
        return oa.yw_call(self._base(), a["user_id"], a["api_token"], method, data)

    def login_quote(self, q: Query):
        raise AdapterError("燕文登录模式暂未实现（网页有滑块验证码），请切换到开放平台 API 模式", "pending")

    def login_test(self):
        raise AdapterError("燕文登录模式暂未实现，请切换到开放平台 API 模式", "pending")

    def api_test(self):
        if not self.has_account():
            raise AdapterError("尚未填写客户号 user_id 和密钥 apitoken", "no_account")
        res = self._call("common.country.getlist", {})
        if not res.get("success") or str(res.get("code", "0")) not in ("0", "200"):
            raise AdapterError(f"鉴权失败：{res.get('message')}", "login_failed")
        env = "测试" if self.account().get("env") == "sandbox" else "正式"
        return f"开放平台 API 鉴权成功（{env}环境，国家列表 {len(res.get('data') or [])} 项）。"

    def api_quote(self, q: Query):
        if not self.has_account():
            raise AdapterError("待配置：需要燕文客户号和 apitoken", "no_account")
        a = self.account()
        data = {"cityId": a.get("city_id") or "01", "countryId": q.country, "weight": int(round(q.weight * 1000)),
                "productAttributes": "1"}
        if q.dims():
            data.update({"length": q.length, "width": q.width, "high": q.height})
        if q.postcode:
            data["postCode"] = q.postcode
        res = self._call("calc.list", data)
        if str(res.get("code")) not in ("0", "200") or not res.get("success", True):   # 文档：code 0 成功；calc.list 实测返回 200
            raise AdapterError(res.get("message") or f"燕文接口错误 {res.get('code')}")
        rows = []
        for it in res.get("data") or []:
            total = to_float(it.get("rmbTotalMoney"))
            if total is None:
                continue
            fees = [{"label": lab, "value": f"{to_float(it.get(k)):g}"} for k, lab in
                    (("basicFreight", "基础运费"), ("trunkFreight", "干线费"), ("fuelCost", "燃油费"), ("additionalCharge", "附加费"))
                    if to_float(it.get(k))]
            rows.append(make_row(self.id, self.name, it.get("productName") or it.get("productNumber"), total,
                                 days=it.get("referAging") or "", fees=fees,
                                 remark=""))
        return rows

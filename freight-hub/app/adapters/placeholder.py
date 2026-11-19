from . import openapi as oa  # noqa: F401
from .base import Adapter, AdapterError, Query
from .v5 import OISMixin


class YutongAdapter(OISMixin, Adapter):
    id = "yutong"
    mode_short_login = "Q5 登录"
    mode_short_api = "OIS 开放API"
    name = "雨桐"
    system = "跨境 Q5（Intelink）"
    mode = "API 模式：Intelink OIS appKey/appSecret；登录模式待验证（拼图验证码）"
    login_impl = False
    default_mode = "api"     # 登录模式未实现，默认走开放平台 API
    login_note = "登录页有拼图验证码，登录模式暂未实现（保存账号备用）。"
    note = "登录有拼图验证码；开放平台 API 模式使用 Intelink OIS，运价试算走 OIS queryQuoteList。"
    api_doc_note = property(lambda self: self._ois_doc())

    def login_quote(self, q: Query):
        raise AdapterError("雨桐登录模式暂未实现（拼图验证码）", "pending")

    def login_test(self):
        raise AdapterError("雨桐登录模式暂未实现（拼图验证码）", "manual")

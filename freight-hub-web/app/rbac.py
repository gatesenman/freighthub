"""RBAC：权限目录、默认角色、接口 → 权限映射。

- 权限（permission）是固定目录（代码里定义，对应具体接口 / 菜单）；
- 角色（role）= 一组权限，存数据库，管理员可以在「角色与权限」矩阵里随意勾选；
- 用户只挂一个角色；用户的有效权限 = 其角色当前的权限（改了立刻生效，不用重新登录）；
- admin 是系统角色：永远拥有全部权限，不可改、不可删。
- 接口默认拒绝：只有出现在 ROUTE_PERMS 里的 /api/* 才可能被放行。
"""
import re

# (key, 中文名, 分组, 说明)
PERMISSIONS = [
    ("query.run",         "执行询价",           "询价", "发起报价查询（只读，不会下单）"),
    ("platforms.view",    "查看平台状态",       "平台", "查看各货代平台连接状态与账号字段（密钥始终不返回）"),
    ("accounts.manage",   "编辑平台账号",       "平台", "保存 / 修改 / 启停平台账号与授权模式"),
    ("accounts.test",     "测试平台连接",       "平台", "对已保存的账号做一次连接测试（每个平台只登录一次）"),
    ("accounts.import",   "导入平台账号",       "平台", "从 Excel / CSV 批量导入平台账号"),
    ("accounts.export",   "导出平台账号配置",   "平台", "导出账号配置（密钥只能不导出或打码）"),
    ("history.view",      "查看查询历史（自己的）", "历史", "查看自己的查询历史"),
    ("history.view_all",  "查看所有人的历史",   "历史", "查看全部用户的查询历史"),
    ("history.export",    "导出历史为 CSV",     "历史", "下载某条历史记录的 CSV"),
    ("history.delete",    "删除历史（自己的）", "历史", "删除 / 清空自己的查询历史"),
    ("history.delete_all", "删除所有人的历史",  "历史", "删除 / 清空任意用户的查询历史"),
    ("users.view",        "查看用户列表",       "管理", "查看系统用户及其角色"),
    ("users.manage",      "管理用户",           "管理", "新增 / 停用 / 删除用户、重置密码、分配角色"),
    ("roles.view",        "查看角色与权限",     "管理", "查看角色权限矩阵"),
    ("roles.manage",      "管理角色与权限",     "管理", "新增 / 修改 / 删除角色，调整权限矩阵"),
    ("audit.view",        "查看审计日志",       "审计", "按用户 / 操作 / 时间 / IP 查看审计日志"),
    ("audit.export",      "导出审计日志",       "审计", "把（筛选后的）审计日志导出为 CSV"),
]
PERM_KEYS = [p[0] for p in PERMISSIONS]
ALL = frozenset(PERM_KEYS)

_OPERATOR = ["query.run", "platforms.view", "history.view", "history.export", "history.delete"]
DEFAULT_ROLES = [
    # name, 中文名, 说明, 权限, system
    ("admin",    "系统管理员", "拥有全部权限，不可修改 / 删除（系统内置）", PERM_KEYS, 1),
    ("manager",  "业务主管",   "询价 + 管理平台账号 + 查看所有人历史 + 查看用户", _OPERATOR + ["accounts.manage", "accounts.test", "accounts.import", "accounts.export", "history.view_all", "history.delete_all", "users.view"], 0),
    ("operator", "业务员",     "只能询价、看 / 导出 / 删除自己的历史", _OPERATOR, 0),
    ("auditor",  "审计员",     "只读：审计日志、用户与角色、所有人的历史", ["platforms.view", "history.view", "history.view_all", "history.export", "users.view", "roles.view", "audit.view", "audit.export"], 0),
    ("demo",     "试用客户",   "试用演示：只能询价 + 看 / 删自己的历史；看不到任何账号凭据，不能导出，不能管理用户 / 角色 / 审计", ["query.run", "platforms.view", "history.view", "history.delete"], 0),
    ("viewer",   "只读访客",   "只能查看平台状态和自己的历史，不能询价", ["platforms.view", "history.view"], 0),
]

# (方法, 路径正则, 权限)；权限为 None 表示「登录即可」；公开接口在 PUBLIC 里。
ROUTE_PERMS = [
    ("GET", r"/api/meta", None), ("GET", r"/api/auth/me", None), ("POST", r"/api/auth/logout", None), ("POST", r"/api/auth/password", None),
    ("GET", r"/api/accounts/template", None),         # 空模板，不含凭据
    ("GET", r"/api/countries", None),
    ("GET", r"/api/platforms", "platforms.view"),
    ("PUT", r"/api/platforms/[a-z0-9_]+/account", "accounts.manage"),
    ("POST", r"/api/platforms/[a-z0-9_]+/test", "accounts.test"),
    ("POST", r"/api/accounts/import/preview", "accounts.import"), ("POST", r"/api/accounts/import/apply", "accounts.import"),
    ("GET", r"/api/accounts/export", "accounts.export"),
    ("POST", r"/api/quote", "query.run"),
    ("GET", r"/api/diag/net", "users.manage"), ("POST", r"/api/diag/recycle", "users.manage"),    # 管理员：出站网络自检（TCP 探测各平台域名）
    ("GET", r"/api/history", "history.view"), ("GET", r"/api/history/\d+", "history.view"),
    ("GET", r"/api/history/\d+/csv", "history.export"),
    ("DELETE", r"/api/history", "history.delete"), ("DELETE", r"/api/history/\d+", "history.delete"),
    ("GET", r"/api/users", "users.view"), ("POST", r"/api/users", "users.manage"),
    ("PUT", r"/api/users/[^/]+", "users.manage"), ("DELETE", r"/api/users/[^/]+", "users.manage"),
    ("GET", r"/api/roles", "roles.view"), ("POST", r"/api/roles", "roles.manage"),
    ("PUT", r"/api/roles/[^/]+", "roles.manage"), ("DELETE", r"/api/roles/[^/]+", "roles.manage"),
    ("GET", r"/api/audit", "audit.view"), ("GET", r"/api/audit/export", "audit.export"),
]
PUBLIC = [("GET", r"/api/ping"), ("POST", r"/api/auth/login"), ("GET", r"/api/setup/status"), ("POST", r"/api/setup")]

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{1,23}$")


def match_route(method, path):
    """返回 (known, perm)。known=False 表示接口不在表里（默认拒绝）。"""
    if method == "HEAD":
        method = "GET"
    for m, p, perm in ROUTE_PERMS:
        if m == method and re.fullmatch(p, path):
            return True, perm
    return False, None


def is_public(method, path):
    if method == "HEAD":
        method = "GET"
    return any(m == method and re.fullmatch(p, path) for m, p in PUBLIC)


def clean_perms(perms):
    """去重 / 去掉未知权限，保持目录顺序。"""
    s = {p for p in (perms or []) if isinstance(p, str)}
    return [k for k in PERM_KEYS if k in s]


def valid_role_name(n):
    return bool(_ROLE_RE.match(n or ""))


# 依赖：有「管理」权限必须同时有对应「查看」权限（否则界面进不去）
IMPLIES = {"users.manage": ["users.view"], "roles.manage": ["roles.view"], "audit.export": ["audit.view"],
           "history.view_all": ["history.view"], "history.delete_all": ["history.delete", "history.view_all", "history.view"],
           "history.delete": ["history.view"], "history.export": ["history.view"],
           "accounts.manage": ["platforms.view"], "accounts.test": ["platforms.view"], "accounts.import": ["platforms.view"],
           "accounts.export": ["platforms.view"], "query.run": ["platforms.view"]}


def close_perms(perms):
    s = set(clean_perms(perms))
    changed = True
    while changed:
        changed = False
        for p in list(s):
            for q in IMPLIES.get(p, []):
                if q not in s:
                    s.add(q); changed = True
    return [k for k in PERM_KEYS if k in s]

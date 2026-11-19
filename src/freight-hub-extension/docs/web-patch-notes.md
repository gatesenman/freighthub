# 网页版（freight-hub-web）与扩展的对接说明 / 补丁说明

> 本目录**没有修改** `/workspace/freight-hub-web`。需要的服务端改动都写成补丁文件 `web-allowed-origin.patch`（只改文档 `部署说明.md`，用 `patch -p1 < web-allowed-origin.patch` 应用），由网页版负责人决定是否合入。

## 结论：网页版**不改代码**扩展也能用

| 点 | 实测 / 代码依据 | 结论 |
|---|---|---|
| CORS | 站点不返回任何 CORS 头；扩展声明了 `host_permissions`，扩展页面的 `fetch` 不受 CORS 限制 | 不需要加 CORS |
| Cookie | `fh_session`：HttpOnly + Secure + SameSite=Lax。扩展请求带 `credentials: "include"`，Chrome 会把站点 Cookie 随扩展的请求发出（有 host_permissions）。线上实测登录后 Cookie 属性为 `(fh_session, httpOnly=True, secure=True, Lax)` | 可用；扩展读不到 Cookie 内容，也不保存密码 |
| CSRF | 登录响应和 `/api/meta` 都返回 `csrf`，写请求带 `X-CSRF-Token`。令牌只放内存，不落盘 | 可用（测试覆盖：缺令牌 → 403 CSRF） |
| Origin 校验 | `origin_ok()`：写请求的 `Origin` 必须等于站点 Host，或在 `FREIGHT_HUB_ALLOWED_ORIGINS` 里。扩展发出的 Origin 是 `chrome-extension://<扩展ID>`，直接发会 **403 来源校验失败（Origin）** | 见下面两种解法 |

## Origin 的两种解法（任选其一，也可以同时开）
1. **扩展内置（默认，已启用）**：`declarativeNetRequest` 动态规则，只对「本扩展 → 所选后端」的 POST/PUT/DELETE/PATCH，把 `Origin` 改写成后端自己的 origin（等同同站请求）。服务端零改动。
   - 测试：`test_web_post_without_origin_rule_is_403_and_message_names_extension_id`（删规则 → 403；恢复规则 → 正常）。
2. **服务端白名单（推荐，更干净）**：Vercel production 环境变量 `FREIGHT_HUB_ALLOWED_ORIGINS=jepddgoigapaeamalcmjembhbpllalog` 后重新部署。
   - 注意值是**扩展 ID**（`origin_ok` 比较的是 `urlparse(origin).netloc`），不要写 `chrome-extension://` 前缀；不要写 `*`。
   - 扩展 ID 固定（清单里有固定公钥 `key`），所以 ID 不会随安装位置变化。
   - 测试：`test_web_server_allows_extension_id_without_rewrite`（删规则后仍能登录）。

## 线上实测（https://freight.gaosichun.dev，试用账号 demo，登录 1 次 + 查询 1 次）
- 扩展登录 → 成功；`/api/meta` 版本 1.4.0；配额显示 `29/30`。
- 查询请求 `POST /api/quote` 返回 200，但 **4 个平台全部是「网络请求失败 / 连接超时」**（中航、欣航、环洋、雨桐），所以没有价格。这是**部署环境到各平台的网络问题**（Vercel 出口 IP / 平台白名单或境外访问受限），不是扩展问题；同样的查询在桌面版（国内网络）可用。需要网页版负责人处理（例如固定出口 IP 并让平台加白名单，或把查询放到国内可达的服务器上）。
- 该次查询没有出现在「历史」列表里（失败的查询没有任何结果行，网页版可能不保存）。

## 扩展对服务端的调用清单（全部是网页版现有接口，无新增）
`GET /api/ping`（选项页检测）、`GET /api/meta`、`GET /api/platforms`、`POST /api/auth/login`、`POST /api/auth/logout`、`POST /api/quote`、`GET /api/history`、`GET /api/history/{id}`。
只读：不调用任何账号管理 / 下单 / 重置密钥接口。

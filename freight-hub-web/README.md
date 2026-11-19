# 货代询价助手 FreightHub v1.4

把多个货代平台的报价查询放到一个桌面软件里：输入「目的国 + 重量（+邮编/尺寸）」，并发查询所有已启用平台，在一张表里按价格/时效排序、筛选、对比、导出 CSV。**只读查询，不下单、不改平台资料。**

## 1.4.0 网页 / 服务器模式 + 1.3.1 界面修复

- **网页模式**：`python run.py --server`（Windows 双击 `start-server.bat`，Linux `./run-server.sh`，或 Docker），同样的软件式界面在浏览器里用；可配置监听地址 / 端口（默认 `0.0.0.0:8765`），可选 HTTPS。需要登录：用户系统（管理员 / 普通用户，PBKDF2 口令哈希，HttpOnly 会话 Cookie，CSRF 令牌，登录限流）。普通用户只能询价和看自己的历史；平台账号、导入导出、连接测试、用户管理仅管理员。详见 `部署说明.md`。桌面版行为不变、不需要登录。
- **1.3.1 界面**：账号页「对接方式」列改为短文案（如 `OIS 开放API`、`tms-saas 登录`，完整说明在悬停提示和平台说明里）；「操作日志」撑满剩余高度并随窗口缩放。
- **测试**：`tests/suite/` 全量测试套件（`pytest`），含 Playwright UI 端到端，见 `reports/测试报告.pdf`。可选在线冒烟：`FH_LIVE=1 pytest -m live`（只读、每平台最多登录一次、失败即停）。

## 1.3.0 批量导入账号

不用一个平台一个平台地配置：**文件 → 导入账号…**（Ctrl+I）或「平台账号」页的「批量导入账号…」，选择 Excel(.xlsx)/CSV 即可，登录账号与开放平台 API 两种方式都支持。

1. **下载模板**：文件菜单或账号页按钮，得到 `账号导入模板.xlsx`（也有 CSV）。第一页「账号配置」一行一个平台配置，列：平台、授权方式（登录/API）、账号、密码、appKey、appSecret、AES秘钥、客户编码、对接URL、其他参数、备注、启用；第二页「字段说明」逐平台说明每列填什么；第三页是假数据示例。
2. **兼容旧表**：只有「平台名/账号/密码/网址」四列（甚至没有表头）也能直接导入，按登录模式处理；表头支持常见别名和乱序，也可直接粘贴 Excel 复制出来的制表符文本。
3. **先预览再写入**：弹出预览表，列出每行将「新增 / 覆盖 / 无变化 / 跳过 / 重复 / 错误」，可逐行勾选；确认后才写入。密码和密钥在预览里只显示「已填写」，不回显。留空或打码（`****`）的单元格不会覆盖已有值；欣航是免授权公开网站，自动跳过。
4. **自动测试**：导入后可勾选「自动逐个测试连接」，每个平台只测一次（当前使用的授权方式），结果逐行显示，避免多次登录触发风控。
5. **导出当前配置**：同一格式的表格，密钥/密码只能选择「不导出」或「打码」（不提供明文导出），可再次导入。
6. 其他参数列用 `键=值; 键=值` 写（如 `env=sandbox; city_id=01`），可用键见「字段说明」。

## 1.2.3 修复

- 修复 Windows 上窗口空白/卡死/弹出空白 Python 小窗：js_api 不再暴露 window 对象（pywebview 会递归遍历公开属性）；启动先显示「正在启动…」页，后台启动服务后再跳转。

## 两种授权模式（v1.2 新增）

每个平台在【平台账号】页都可以切换授权模式，两套凭据分开保存、互不覆盖，**测试连接**按钮按当前模式验证：

- **登录账号授权**：用平台网页/后台的账号密码（登录后调用平台自己的接口或网页），沿用 v1.0 的全部功能。
- **开放平台API授权**：用平台官方开放平台的凭据（appKey / secret / token / 签名），走官方 API。账号页会标注「有公开文档」或「需向服务商申请文档」。欣航只有公开网站，不分授权模式。

## 开放平台对接状态

| 平台 | 开放平台 / 文档 | 鉴权 | 运价试算 | 沙箱 | 软件内状态 |
|---|---|---|---|---|---|
| 云途 | ✅ open.yunexpress.cn（官网为单页应用，接口规范以其 SDK/OpenAPI 为准） | OAuth2 client_credentials（appId/appSecret[/sourceKey]）+ 请求头 token/date/sign(HMAC-SHA256) | ✅ `/v1/price-trial/get` | openapi-sbx.yunexpress.cn（需手动 token） | API 已实现；**待你申请 appId/appSecret 后验证** |
| 燕文 | ✅ opendocs.yw56.com.cn（已按 2025-04 国家列表文档核对） | user_id + apitoken，MD5 小写签名 `apitoken+user_id+data+format+method+timestamp+version+apitoken`（与文档示例 `bc5f1792…` 逐字节一致） | ✅ `calc.list`（countryId 可传二字码或国家列表数字 id；成功码文档为 `0`、calc.list 实测 `200`，均接受；英国等需 postCode） | ✅ open-fat.yw56.com.cn / user_id=100000 | API 已实现，**已在官方沙箱真实验证**（美国 29 个渠道） |
| 顺丰国际 | ✅ **sf.global 开放平台**（openapi.sf.global；丰桥 open.sf-express.com 保留为备选） | `GET /openapi/api/token`（appKey+appSecret）→ `POST /openapi/api/dispatch`：请求体 AES-256-CBC 加密（Base64 秘钥，IV=秘钥前 16 字节，明文=16 位随机串+4 字节长度+报文+appKey，PKCS7 块 32）+ 头 signature=SHA256(字典序拼接 token/timestamp/nonce/密文)，msgType 路由接口 | ✅ `IUOP_QUERY_FREIGHT`（需 customerCode）+ `IPE_QUERY_PRODUCT_LIST` 产品列表 | ✅ api-ifsp-sit.sf.global（需控制台「前往沙盒」的沙箱密钥） | API 已实现；**已用文档公开的共享测试凭据在沙箱真实验证**（token、AES 往返、产品查询 5 个产品）；正式密钥可取得 token，但应用须先在控制台关联 API（否则全部接口 1004 没权限）并补填 customerCode |
| 欣航 | ➖ 只有公开报价网站 http://xinh.kingtrans.net/price.jsp，无开放平台 | **无需授权**（界面显示「公开网站，无需授权」，没有登录/API 切换和账号字段） | ✅ 公开价页面（POST 表单 + 解析 HTML） | - | 已实现并实测（美国 1kg 36 个渠道）；得到的是公开价而不是协议价 |
| 雨桐 / 中航 / 环洋 | ✅ 哲盟 Intelink **OIS 对外标准接口**（help.intelink.net.cn，POS.OIS.C；V5/Q5 通用）：授权 / 渠道列表 / **运价试算** / 下单 / 轨迹均公开 | appKey + appSecret → `POST /ois/order/getAuth` 取 token（3 小时）；业务请求头 token/sign/version，sign=MD5(Base64(排序参数)+appSecret) 大写 | ✅ `/tms/expose/queryQuoteList`（body1：weig/destNo/packing/reZip/pieces…）+ `/ois/order/getProductList` | 正式域名 **https://openapi.intelink.net.cn**（注意：系统登录地址如 ytgj56.intelink.net.cn 不是接口域名） | **雨桐已用真实密钥只读验证**：渠道列表 116 个产品，美国 1kg 报价 60 个渠道（最低有效价 143 CNY）；中航同一套接口，需在 V5 后台取得 appKey/appSecret 后即可使用；**环洋**对接环境为易快通 OIS（文档 gitee.com/xis_co/print，URL `https://ekttech.net/pos-web`，签名/接口与哲盟 OIS 相同），已只读验证：渠道列表 940 个产品，美国 1kg 20 个渠道（最低 250.81 CNY）、英国 0.5kg 20 个渠道（最低 191.86 CNY） |

> 未用真实凭据验证的接口，均按官方文档/SDK 实现，并有「签名算法单元测试 + 模拟 HTTP + 假凭据在线探测」覆盖（见测试一节）。

## 登录模式对接状态

| 平台 | 状态 | 方式 |
|---|---|---|
| 云途 | ✅ 可用 | 本机 Edge/Chrome 自动登录一次取 Cookie（约 1.5 小时有效），之后纯接口查价；过期自动重登 |
| 中航 | ✅ 可用 | 纯接口（V5/tms-saas 签名算法），无需浏览器，过期自动重登 |
| 欣航 | ✅ 可用 | 公开网站，无需授权：公开报价页（得到的是公开价，不是协议价）。只有这一种模式 |
| 环洋 | ✅ API 模式（易快通 OIS，只读验证通过） | API 模式：appKey/appSecret + 对接环境 URL（默认 https://ekttech.net/pos-web）→ getAuth + queryQuoteList；登录模式复用 V5 登录（新账号登录返回「账号错误」，建议用 API 模式） |
| 燕文 | 🚧 登录模式未实现 | 网页登录有滑块验证码；请使用开放平台API模式 |
| 顺丰国际 | 🚧 登录模式未实现 | 登录需点选验证码；请使用开放平台API模式 |
| 雨桐 | 🚧 登录模式未实现 | 登录需滑块拼图验证码；请使用开放平台API模式（OIS，运价试算已实现并实测） |

## 运行

### Windows 安装版（推荐）
运行 `FreightHub-Setup.exe`：中文安装向导，默认装到 `%LOCALAPPDATA%\Programs\FreightHub`（免管理员，可改路径），创建桌面和开始菜单快捷方式，并在「设置 → 应用」里注册卸载项。
安装版的账号、会话、历史保存在 `%LOCALAPPDATA%\FreightHub\data`（升级覆盖安装不会丢；卸载时会询问是否一并删除）。安装包不含任何账号。
构建安装包（在 Linux 或 Windows 上，需要 NSIS）：`makensis installer/FreightHub.nsi`。

### Windows 便携版（免安装 zip）
1. 解压 `FreightHub-win-x64.zip`（20 MB）到任意目录（路径尽量不要含特殊字符）。
2. 双击 **`FreightHub.vbs`**（无黑色控制台窗口；`start.bat` 效果相同）。出现独立的桌面软件窗口：自绘标题栏、菜单栏、工具栏、左侧树、多标签页、状态栏。
3. 首次使用：菜单【平台】→【平台账号管理】，填账号密码，点「测试登录」。
4. 关闭窗口即退出软件。启动失败会弹出错误框，详细日志在 `data\freight-hub.log`；也可双击 `start-debug.bat` 在控制台看输出。

要求：Windows 10/11，已装 Edge WebView2 运行时（Win11 和更新过的 Win10 自带），云途登录还需要 Edge 或 Chrome。压缩包内已含 Python 运行环境和全部依赖。
窗口实现：优先 pywebview（Edge WebView2）无边框窗口；若 pywebview 无法启动，自动退回 Edge/Chrome 的 `--app` 独立窗口（无地址栏，关闭窗口后自动退出）。设置环境变量 `FREIGHT_HUB_NATIVE_FRAME=1` 可改用系统自带标题栏。

如需单个 exe：在有 Python 的 Windows 上双击 `build_exe.bat`（PyInstaller，无控制台），产物在 `dist\FreightHub\`。

### 从源码运行（Windows / macOS / Linux）
```
pip install -r requirements.txt
python run.py          # Windows 无黑窗：pythonw run.py
```
环境变量：`FREIGHT_HUB_PORT` 改端口，`FREIGHT_HUB_NO_UI=1` 只起服务（用浏览器打开 http://127.0.0.1:8765/ ），`FREIGHT_HUB_DATA` 改数据目录。

## 界面与使用
- **菜单栏**：文件 / 查询 / 平台 / 工具 / 帮助。**工具栏**：新建查询、查询、重置、导出 CSV、复制行、平台账号、查询历史、刷新状态、帮助。**左侧树**：询价中心（含最近查询）、货代平台（状态灯）、系统。**状态栏**：操作提示、各平台连接状态灯、本次查询耗时、记录数、版本。
- **多标签页**：每个查询一个标签（Ctrl+T 新建，Ctrl+W 关闭），可并排对比不同国家/重量；账号、历史、帮助各一个标签。
- **询价**：「查询条件」「查询平台」分组框。目的国可输中文或二字码（US / 美国）；邮编、尺寸可选。F5 或 Enter 查询；各平台并发，单个平台失败（登录失败、超时、需验证码）只在其状态条里提示，不影响其他平台。
- **数据网格**：点列头排序，拖列头右边缘调整列宽（双击恢复），斑马纹，单击/Ctrl/Shift 选行，↑↓ 移动；右键菜单可复制选中行（制表符，可直接粘贴到 Excel）、复制渠道名/价格、导出选中行或全部为 CSV、只看该平台。上方可按关键词、最高价、最长时效、是否有轨迹、平台筛选；下方「选中行明细」显示运费/挂号费/燃油费等。「全场最低」「最快」自动标记。
- **平台账号**：平台状态表 + 账号设置分组框（保存、启用、测试登录）和操作日志。密码只写不读（界面不回显）。
- **查询历史**：自动保存；双击回看，可重新查询、导出、删除、清空。

## 数据与安全
- 全部数据在 `data/` 目录：`config.json`（账号密码，明文存储在本机，权限 600，**不要分享/提交**）、`sessions/`（登录会话）、`history.db`（历史）。
- 前端页面和接口从不返回密码；服务只监听 127.0.0.1。
- 想换电脑：整个目录拷走即可（含 data）；想分发给别人：先删掉 `data/`。
- 注意：中航系统本身是 `http://IP:端口` 明文协议（对方平台所限），环洋/云途为 https。

## 常见问题
- **云途失败 / 弹出验证窗口**：登录用本机 Edge/Chrome（先无界面尝试，需要验证码时才弹窗）；在弹出的窗口里完成验证即可（最长等待 150 秒）；会话约 1.5 小时过期，之后自动重登。
- **欣航/中航结果为空**：该国家/重量无报价或平台暂时不可用，看状态卡片的说明。
- **改了账号还是旧的**：保存账号会自动清除旧会话。

## 目录
```
run.py            启动入口(pywebview窗口)  FreightHub.vbs / start.bat / build_exe.bat   Windows 启动 / 打包
app/main.py       FastAPI 接口    app/adapters/   每个平台一个适配器（openapi.py = 各开放平台的签名/鉴权协议实现）
app/store.py      配置/会话/历史  app/cdp.py  Edge/Chrome 登录(CDP)  app/static/  界面（离线，无 CDN）
tests/e2e_test.py 端到端测试      tests/api_mode_test.py 开放平台API模式测试  tests/import_test.py 账号导入导出测试
app/importer.py 批量导入/导出/模板  app/xlsx.py 纯标准库 xlsx 读写（安装包不依赖 openpyxl）      screenshots/    界面截图
```
新增平台：在 `app/adapters/` 写一个继承 `Adapter` 的类（实现 `login_quote`/`login_test` 与 `api_quote`/`api_test`，声明 `login_fields`/`api_fields`），加入 `__init__.py` 的 `ADAPTER_CLASSES` 即可。

## 测试
`python tests/e2e_test.py`：启动真实服务，用真实账号查询美国 1kg、英国 0.5kg、德国 2kg，校验云途/中航/欣航结果、排序、筛选、历史、CSV、会话损坏自动重登、单平台异常隔离，并含授权模式切换的界面测试、开放平台 API 模式测试（`tests/api_mode_test.py`：燕文官方沙箱在线验证、签名单元测试、各平台 mock HTTP 适配器测试、假凭据在线探测），账号批量导入/导出测试（`tests/import_test.py`：模板、旧表格式、预览/覆盖/跳过、密钥不回显、导出打码、界面流程），并用 Playwright 驱动界面做完整交互测试、截图到 `screenshots/`（仅测试需要：`pip install -r requirements-dev.txt && playwright install chromium`）。

# FreightHub Chrome 扩展（MV3）v1.0.1 · 未授权版本 / 试用

右侧 Side Panel 版货代询价。后端可选：网页版（默认）/ 本机桌面版。

- 源码：`manifest.json` `background.js` `sidepanel.html` `options.html` `js/` `css/` `icons/`
- 测试：`tests/`（pytest + Playwright 加载未打包扩展；后端用桌面版仓库的桩服务，没有真实外网请求）
  - 运行：`cd /workspace/freight-hub-extension && /workspace/fh/.venv/bin/python -m pytest tests -q`
  - `tests/live_web_smoke.py`：对线上站点登录 + 查询各一次的手动冒烟（读 `FH_DEMO_FILE`，不在默认测试里）
- 打包：`scripts/build_zip.sh` → `dist/FreightHub-Chrome-1.0.1.zip`（含 `安装说明.md`）
- 网页版对接 / 补丁：`docs/web-patch-notes.md`、`docs/web-allowed-origin.patch`
- 固定扩展 ID：`jepddgoigapaeamalcmjembhbpllalog`（清单里的 `key`；私钥在 `/workspace/freight-hub-extension-key/`，不入包）
- 水印：v1.0.1 常开（`js/trial.js`），没有关闭开关。

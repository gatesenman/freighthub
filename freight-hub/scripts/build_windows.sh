#!/usr/bin/env bash
# 在 Linux 上构建 Windows 安装包（NSIS）+ 便携 zip。
#   scripts/build_windows.sh            -> dist/FreightHub-<ver>-Setup.exe（正式版，无水印）
#   TRIAL=1 scripts/build_windows.sh    -> dist/FreightHub-<ver>-Trial-Setup.exe（试用版：整页水印 + 标题栏角标）
# 环境变量：
#   BASE_STAGE  已有的 Windows 运行时目录（含 python/ 嵌入式 Python 与 site-packages/，默认上一版的 stage）
#   MAKENSIS    makensis 路径（默认 PATH 中的 makensis）
# 绝不打包 data/（账号凭据、历史记录），也不打包 tests/ dist/ screenshots/ reports/。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
VER="$(python3 -c 'import re;print(re.search(r"__version__ = \"([^\"]+)\"",open("app/__init__.py").read()).group(1))')"
BASE_STAGE="${BASE_STAGE:-/workspace/winbuild/stage-1.4.0/FreightHub}"
MAKENSIS="${MAKENSIS:-$(command -v makensis || echo /workspace/nsis/root/usr/bin/makensis)}"
export NSISDIR="${NSISDIR:-/workspace/nsis/root/usr/share/nsis}"
TRIAL="${TRIAL:-0}"
SUF=""; [ "$TRIAL" = "1" ] && SUF="-Trial"
WORK="${WORK:-/workspace/winbuild/stage-$VER$SUF}"
OUT="$ROOT/dist"; mkdir -p "$OUT"

[ -d "$BASE_STAGE/python" ] && [ -d "$BASE_STAGE/site-packages" ] || { echo "BASE_STAGE 缺少 python/ 或 site-packages/：$BASE_STAGE" >&2; exit 1; }
rm -rf "$WORK"; mkdir -p "$WORK/FreightHub"
S="$WORK/FreightHub"
cp -a "$BASE_STAGE/python" "$BASE_STAGE/site-packages" "$S/"
cp -a app "$S/app"
find "$S/app" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -f "$S/app/edition.json"
for f in run.py FreightHub.ico FreightHub.vbs start.bat start-debug.bat start-server.bat build_exe.bat run-server.sh Dockerfile docker-compose.yml requirements.txt requirements-dev.txt README.md 部署说明.md; do
  [ -f "$f" ] && cp -a "$f" "$S/$f"
done
if [ "$TRIAL" = "1" ]; then
  printf '{"trial": true, "edition": "trial"}\n' > "$S/app/edition.json"
  echo "== 试用版：已写入 app/edition.json"
fi
# 安全检查：不得出现运行数据 / 凭据文件
if find "$S" -path "$S/python" -prune -o -path "$S/site-packages" -prune -o \( -name config.json -o -name users.json -o -name history.db -o -name INITIAL_ADMIN_PASSWORD.txt \) -print | grep -q .; then
  echo "暂存目录里发现运行数据文件，中止" >&2; exit 1
fi

SETUP="$OUT/FreightHub-$VER${SUF}-Setup.exe"
ZIP="$OUT/FreightHub-$VER${SUF}-win-x64.zip"
NSIS_EXTRA=(); [ "$TRIAL" = "1" ] && NSIS_EXTRA+=(-DTRIAL=1)
"$MAKENSIS" -V2 "${NSIS_EXTRA[@]}" -DSRC="$S" -DOUT="$SETUP" -DVERSION="$VER" installer/FreightHub.nsi
python3 - "$WORK" "$ZIP" <<'PY'
import sys, zipfile, pathlib
work, out = pathlib.Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(work.rglob("*")):
        if p.is_file():
            z.write(p, p.relative_to(work))
PY
( cd "$OUT" && sha256sum "$(basename "$SETUP")" "$(basename "$ZIP")" >> "SHA256SUMS-$VER.txt.new" && \
  { [ -f "SHA256SUMS-$VER.txt" ] && grep -v -F -e "$(basename "$SETUP")" -e "$(basename "$ZIP")" "SHA256SUMS-$VER.txt" || true; cat "SHA256SUMS-$VER.txt.new"; } > "SHA256SUMS-$VER.txt.tmp" && \
  mv "SHA256SUMS-$VER.txt.tmp" "SHA256SUMS-$VER.txt" && rm -f "SHA256SUMS-$VER.txt.new" )
ls -la "$SETUP" "$ZIP"

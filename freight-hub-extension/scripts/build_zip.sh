#!/usr/bin/env bash
# 打包：dist/FreightHub-Chrome-1.0.1.zip（顶层目录 FreightHub-Chrome-1.0.1/，含安装说明）
set -euo pipefail
cd "$(dirname "$0")/.."
V=$(python3 -c "import json;print(json.load(open('manifest.json'))['version'])")
N="FreightHub-Chrome-$V"
rm -rf "dist/$N" "dist/$N.zip"; mkdir -p "dist/$N"
cp -r manifest.json background.js sidepanel.html options.html js css icons "dist/$N/"
cp 安装说明.md "dist/$N/"
(cd dist && python3 - "$N" <<'PY'
import sys, zipfile, os
n = sys.argv[1]
with zipfile.ZipFile(n + ".zip", "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(n):
        for f in sorted(files):
            p = os.path.join(root, f)
            zi = zipfile.ZipInfo.from_file(p, p); zi.compress_type = zipfile.ZIP_DEFLATED; zi.date_time = (2026, 10, 31, 0, 0, 0)
            z.writestr(zi, open(p, "rb").read())
PY
)
rm -rf "dist/$N"
sha256sum "dist/$N.zip" | tee "dist/SHA256SUMS-Chrome-$V.txt"

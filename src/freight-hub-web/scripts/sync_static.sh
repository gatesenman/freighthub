#!/bin/sh
# 把静态资源同步到 public/static，由 Vercel CDN 直接发（不占函数调用）；入口页（index/login/setup）只走带鉴权的函数。
cd "$(dirname "$0")/.." && rm -rf public/static && mkdir -p public/static && \
for f in app/static/*; do case "$(basename $f)" in index.html|login.html|setup.html) ;; *) cp "$f" public/static/;; esac; done

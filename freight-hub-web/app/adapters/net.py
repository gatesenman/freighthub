"""统一的出站 HTTP：短连接超时 + 连接级失败自动换新连接重试。

背景：网页版跑在 Vercel（日本东京）的无服务器函数里，函数闲置一段时间会被回收/冻结，
下一次请求是"冷启动"，此时对国内平台的第一次 TCP 连接偶尔会直接超时（本次故障里 4 个平台同一秒全部 connect timeout）。
旧代码连接超时 = 40 秒、不重试，单个平台等满 40 秒才报错。
这里：连接超时只给 6 秒，连接级错误（超时/拒绝/被重置）最多重试 3 次，每次都是全新连接（不复用可能已失效的连接）。
只用于只读查询；ReadTimeout（请求已发出、对方没回）不重试，避免重复提交。
"""
import os
import time

import requests

CONNECT_TIMEOUT = float(os.environ.get("FH_CONNECT_TIMEOUT", "6"))
ATTEMPTS = int(os.environ.get("FH_NET_ATTEMPTS", "3"))
BACKOFF = (0.3, 0.8)


RETRY_STATUS = (429, 502, 503, 504)   # 限流 / 网关错误：只读查询，退避后再试一次


def request(method, url, **kw):
    read = kw.pop("timeout", 40)
    if isinstance(read, tuple):
        read = read[1]
    last = None
    status_retried = False
    for i in range(max(1, ATTEMPTS)):
        try:
            r = requests.request(method, url, timeout=(CONNECT_TIMEOUT, read), **kw)
            if r.status_code in RETRY_STATUS and not status_retried and i < ATTEMPTS - 1:
                status_retried = True
                try:
                    wait = min(3.0, float(r.headers.get("Retry-After", "")))
                except ValueError:
                    wait = 1.0
                time.sleep(wait)
                continue
            return r
        except (requests.ConnectTimeout, requests.exceptions.ConnectionError) as e:   # 连接级失败：请求没发出去，可安全重试
            last = e
            if i < ATTEMPTS - 1:
                time.sleep(BACKOFF[min(i, len(BACKOFF) - 1)])
    raise last


def get(url, **kw):
    return request("GET", url, **kw)


def post(url, **kw):
    return request("POST", url, **kw)

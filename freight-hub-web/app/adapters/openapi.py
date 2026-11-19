"""各平台开放平台（官方 API）的底层协议实现：签名、取 token、请求封装。
这里只放协议细节，业务适配（字段映射）在各平台适配器里。"""
import base64
import hashlib
import hmac
import json
import time
import os
import struct
import uuid
from urllib.parse import quote_plus

import requests

from . import aes as _aes
from . import net as _net
from .base import AdapterError

UA = "FreightHub/1.0"


def _json(r, what):
    try:
        return r.json()
    except ValueError:
        raise AdapterError(f"{what}返回了非 JSON 内容（HTTP {r.status_code}）")


def _req(method, url, what, **kw):
    kw.setdefault("timeout", 40)
    try:
        return _net.request(method, url, **kw)
    except requests.RequestException as e:
        from ..redact import redact
        extra = [str(v) for v in (kw.get("params") or {}).values()] if isinstance(kw.get("params"), dict) else []
        raise AdapterError(redact(f"{what}网络请求失败：{e}", extra))


# ------------------------------------------------------------------ 云途 YunExpress OpenAPI
YT_PROD = "https://openapi.yunexpress.cn"


def yt_sign(secret, date, method, path, body_text=""):
    """HMAC-SHA256(secret, [body=<body>&]date=<ms>&method=<GET|POST>&uri=<path>) → Base64。
    参考云途 OpenAPI SDK（官方文档站为 SPA，此处按 SDK 实现）。"""
    parts = ([f"body={body_text}"] if body_text else []) + [f"date={date}", f"method={method}", f"uri={path}"]
    return base64.b64encode(hmac.new(secret.encode(), "&".join(parts).encode(), hashlib.sha256).digest()).decode()


def yt_token(base, app_id, app_secret, source_key=""):
    body = {"grantType": "client_credentials", "appId": app_id, "appSecret": app_secret}
    if source_key:
        body["sourceKey"] = source_key
    r = _req("POST", base.rstrip("/") + "/openapi/oauth2/token", "云途授权", json=body, headers={"User-Agent": UA})
    data = _json(r, "云途授权")
    if r.status_code != 200 or not isinstance(data, dict) or not data.get("accessToken"):
        msg = (data.get("message") or data.get("msg")) if isinstance(data, dict) else str(data)[:80]
        raise AdapterError(f"云途授权失败（HTTP {r.status_code}）：{msg or 'appId / appSecret 不正确'}",
                           "login_failed" if r.status_code in (400, 401, 403, 412) else "error")   # 5xx/限流是平台问题，不算账号错，不触发锁定
    return data["accessToken"], int(data.get("expiresIn") or 7200)


def yt_get(base, token, secret, path, params, with_query_in_uri=False):
    date = str(int(time.time() * 1000))
    qs = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items() if v not in (None, ""))
    uri = path + ("?" + qs if (with_query_in_uri and qs) else "")
    headers = {"token": token, "date": date, "sign": yt_sign(secret, date, "GET", uri), "Accept-Language": "zh-CN",
               "User-Agent": UA}
    r = _req("GET", base.rstrip("/") + path + ("?" + qs if qs else ""), "云途接口", headers=headers)
    return r.status_code, _json(r, "云途接口")


# ------------------------------------------------------------------ 燕文 Yanwen OpenAPI
YW_PROD = "https://open.yw56.com.cn/api/order"
YW_SANDBOX = "https://open-fat.yw56.com.cn/api/order"


def yw_sign(token, user_id, data, method, ts, fmt="json", version="V1.0"):
    return hashlib.md5((token + user_id + data + fmt + method + ts + version + token).encode()).hexdigest()


def yw_call(base, user_id, token, method, data):
    d = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    ts = str(int(time.time() * 1000))
    sign = yw_sign(token, user_id, d, method, ts)
    url = f"{base}?user_id={user_id}&method={method}&format=json&timestamp={ts}&sign={sign}&version=V1.0"
    r = _req("POST", url, "燕文接口", data=d.encode(), headers={"Content-Type": "application/json"})
    return _json(r, "燕文接口")


# ------------------------------------------------------------------ 顺丰国际（丰桥开放平台 open.sf-express.com）
SF_HOSTS = {"prod": "https://sfapi.sf-express.com", "sandbox": "https://sfapi-sbox.sf-express.com"}


def _java_urlencode(text):
    out = []
    for b in text.encode("utf-8"):
        c = chr(b)
        if c.isascii() and (c.isalnum() or c in "*-_."):
            out.append(c)
        elif c == " ":
            out.append("+")
        else:
            out.append("%%%02X" % b)
    return "".join(out)


def sf_digest(msg_data, timestamp, check_word):
    """msgDigest = Base64(MD5(URLEncode_UTF8(msgData + timestamp + checkWord)))，URLEncode 同 Java URLEncoder。"""
    raw = _java_urlencode(f"{msg_data}{timestamp}{check_word}")
    return base64.b64encode(hashlib.md5(raw.encode("utf-8")).digest()).decode()


def _sf_result(r, what):
    data = _json(r, what)
    code = str(data.get("apiResultCode", ""))
    if code != "A1000":
        raise AdapterError(f"{what}失败：{code} {data.get('apiErrorMsg') or ''}".strip(),
                           "login_failed" if code in ("A1001", "A1002", "A1003", "A1004", "A1005", "A1006", "A1011", "A1012") else "error")
    res = data.get("apiResultData")
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except ValueError:
            raise AdapterError(f"{what}返回数据无法解析")
    res = res or {}
    if isinstance(res, dict) and str(res.get("success", "true")).lower() == "false":
        raise AdapterError(f"{what}业务失败：{res.get('errorCode')} {res.get('errorMsg') or ''}".strip())
    return res


def sf_token(env, partner_id, check_word):
    host = SF_HOSTS.get(env, SF_HOSTS["prod"])
    r = _req("POST", host + "/oauth2/accessToken", "顺丰授权",
             data={"partnerID": partner_id, "secret": check_word, "grantType": "password"})
    res = _sf_result(r, "顺丰授权")
    if not res.get("accessToken"):
        raise AdapterError(f"顺丰授权失败：{res.get('errorMsg') or '未返回 accessToken'}", "login_failed")
    return res["accessToken"], int(res.get("expiresIn") or 7200)


def sf_service(env, partner_id, service_code, msg_data, access_token=None, check_word=None):
    host = SF_HOSTS.get(env, SF_HOSTS["prod"])
    ts = str(int(time.time() * 1000))
    md = json.dumps(msg_data, ensure_ascii=False, separators=(",", ":"))
    form = {"partnerID": partner_id, "requestID": str(uuid.uuid4()), "serviceCode": service_code, "timestamp": ts, "msgData": md}
    if access_token:
        form["accessToken"] = access_token
    else:
        form["msgDigest"] = sf_digest(md, ts, check_word)
    r = _req("POST", host + "/std/service", "顺丰接口", data=form)
    return _sf_result(r, "顺丰接口")


# ------------------------------------------------------------------ Intelink / 哲盟 OIS 对外标准接口（V5、Q5 系统通用）
# 文档：https://help.intelink.net.cn/#/help?systemCode=POS.OIS.C（SPA，后端文档接口 v5p.intelink.net.cn/cfg/helpCatalogue/guest/{id}）
OIS_DEFAULT = "https://openapi.intelink.net.cn"      # 正式环境（新）；ois.intelink.net.cn 仅存量，预计 2027-01-01 下线
OIS_LEGACY = "https://ois.intelink.net.cn"


def ois_base(base):
    base = ((base or "").strip() or OIS_DEFAULT).rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    return base


def ois_auth(base, app_key, app_secret):
    """POST /ois/order/getAuth → body{ack, clno, coid, hash, token}；token 3 小时有效。"""
    r = _req("POST", ois_base(base) + "/ois/order/getAuth", "OIS 授权", data={"appKey": app_key, "appSecret": app_secret})
    try:
        res = r.json()
    except ValueError:
        raise AdapterError(f"OIS 授权返回了非 JSON 内容（HTTP {r.status_code}）：{ois_base(base)} 不是 OIS 接口域名，"
                           f"请把「OIS 接口域名」留空以使用默认的 {OIS_DEFAULT}（系统登录地址不是接口域名）", "error")
    body = res.get("body") or {}
    if not isinstance(body, dict) or str(body.get("ack")) != "true":
        raise AdapterError(f"OIS 授权失败：{res.get('message') or res.get('solution') or '账号或密钥错误'}", "login_failed")
    return body


def _ois_fake_token(token, ts, nonce="slnkda"):
    raw = json.dumps({"nonce": nonce, "timestamp": int(ts), "token": token}, separators=(",", ":"))
    s = base64.b64encode(raw.encode()).decode()
    return s.replace("a", "-").replace("c", "#").replace("x", "^").replace("M", "$")


def ois_sign(app_secret, params):
    """参数（表单 + nonce/timestamp/token/version）按键名排序 a=1&b=2 → Base64 → +appSecret → MD5 大写；空值不参与。"""
    pin = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] not in ("", None))
    return hashlib.md5((base64.b64encode(pin.encode()).decode() + app_secret).encode()).hexdigest().upper()


def ois_call(base, app_secret, token, path, form=None):
    """业务接口：头 token（伪装）/sign/version；表单 application/x-www-form-urlencoded。"""
    form = dict(form or {})
    ts, nonce, ver = str(int(time.time() * 1000)), "slnkda", "1.0"
    pm = {"nonce": nonce, "timestamp": ts, "token": token, "version": ver}
    pm.update(form)
    headers = {"token": _ois_fake_token(token, ts, nonce), "sign": ois_sign(app_secret, pm), "version": ver}
    r = _req("POST", ois_base(base) + path, "OIS 接口", data=form, headers=headers)
    res = _json(r, "OIS 接口")
    code = res.get("result_code", res.get("code"))
    if str(code) in ("1004", "1005", "401"):
        raise AdapterError("OIS token 已过期", "login_failed")
    if str(code) not in ("0", "None") and not res.get("body"):
        raise AdapterError(f"OIS 接口失败：{code} {res.get('message') or res.get('solution') or ''}".strip())
    return res


# ------------------------------------------------------------------ 顺丰国际 OpenAPI（openapi.sf.global，AES 加密 + SHA256 签名）
SFG_HOSTS = {"prod": "https://api-ifsp.sf.global", "sandbox": "https://api-ifsp-sit.sf.global"}


def _sfg_pad(data: bytes, block=32):
    n = block - len(data) % block
    return data + bytes([n]) * n


def sfg_key(aes_key):
    try:
        key = base64.b64decode(aes_key.strip() + "=")
    except Exception:
        key = b""
    if len(key) != 32:
        raise AdapterError("AES 秘钥格式不正确（应为 43 位 Base64 字符串）", "no_account")
    return key


def sfg_encrypt(aes_key, app_key, plain: str, rand=None) -> str:
    """明文 = 16 位随机串 + 4 字节大端长度 + 报文 + appKey；PKCS7(块 32) 填充；AES-256-CBC（IV=key[:16]）；Base64。"""
    key = sfg_key(aes_key)
    msg = plain.encode("utf-8")
    rnd = (rand or base64.b64encode(os.urandom(12)).decode().replace("+", "a").replace("/", "b")[:16]).encode()
    raw = _sfg_pad(rnd + struct.pack(">I", len(msg)) + msg + app_key.encode())
    return base64.b64encode(_aes.cbc_encrypt(key, key[:16], raw)).decode()


def sfg_decrypt(aes_key, app_key, cipher_b64: str) -> str:
    key = sfg_key(aes_key)
    raw = _aes.cbc_decrypt(key, key[:16], base64.b64decode(cipher_b64))
    pad = raw[-1]
    if 1 <= pad <= 32:
        raw = raw[:-pad]
    n = struct.unpack(">I", raw[16:20])[0]
    if raw[20 + n:].decode("utf-8", "ignore") != app_key:
        raise AdapterError("顺丰国际响应解密校验失败（AES 秘钥或 appKey 不匹配）", "login_failed")
    return raw[20:20 + n].decode("utf-8")


def sfg_signature(token, timestamp, nonce, encrypt):
    return hashlib.sha256("".join(sorted([token, timestamp, nonce, encrypt])).encode()).hexdigest()


def sfg_token(env, app_key, app_secret):
    host = SFG_HOSTS.get(env, SFG_HOSTS["prod"])
    r = _req("GET", host + "/openapi/api/token", "顺丰国际授权", params={"appKey": app_key, "appSecret": app_secret})
    res = _json(r, "顺丰国际授权")
    if str(res.get("apiResultCode")) != "0" or not (res.get("apiResultData") or {}).get("accessToken"):
        raise AdapterError(f"顺丰国际授权失败：{res.get('apiResultCode')} {res.get('apiErrorMsg') or 'appKey / appSecret 不正确'}",
                           "login_failed")
    d = res["apiResultData"]
    return d["accessToken"], int(d.get("expireIn") or 7200)


def sfg_call(env, app_key, aes_key, token, msg_type, payload, lang="zh-CN"):
    host = SFG_HOSTS.get(env, SFG_HOSTS["prod"])
    plain = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    enc = sfg_encrypt(aes_key, app_key, plain)
    ts, nonce = str(int(time.time() * 1000)), uuid.uuid4().hex
    headers = {"appKey": app_key, "token": token, "timestamp": ts, "nonce": nonce,
               "signature": sfg_signature(token, ts, nonce, enc), "msgType": msg_type, "lang": lang,
               "Content-Type": "application/json"}
    r = _req("POST", host + "/openapi/api/dispatch", "顺丰国际接口", data=enc.encode(), headers=headers)
    res = _json(r, "顺丰国际接口")
    code = str(res.get("apiResultCode"))
    if code != "0":
        kind = "login_failed" if code in ("1001", "1002", "1003", "1004", "1005", "1006", "401", "403") else "error"
        raise AdapterError(f"顺丰国际接口 {msg_type}：{code} {res.get('apiErrorMsg') or ''}".strip(), kind)
    data = res.get("apiResultData")
    if isinstance(data, str) and data:
        try:
            data = json.loads(sfg_decrypt(aes_key, app_key, data))
        except AdapterError:
            raise
        except Exception:
            raise AdapterError("顺丰国际响应解密失败（AES 秘钥可能不正确）", "login_failed")
    return data or {}

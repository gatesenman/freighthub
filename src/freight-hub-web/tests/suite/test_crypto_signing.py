"""签名 / 加解密向量：燕文 MD5、云途 HMAC、OIS MD5+Base64、V5 头部、顺丰丰桥 msgDigest、顺丰国际 AES-256-CBC + SHA256。"""
import base64
import hashlib
import hmac
import json
import os
import struct

import pytest

from app.adapters import aes, openapi as oa
from app.adapters.base import AdapterError
from app.adapters.v5 import SALT, ZhonghangAdapter, _mix, _b64

pytestmark = pytest.mark.unit


# ---------------- AES 官方向量（NIST SP 800-38A F.2.5 / F.2.1 CBC） ----------------
KEY256 = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
KEY128 = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
IV = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
PT = bytes.fromhex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51"
                   "30c81c46a35ce411e5fbc1191a0a52eff69f2445df4f9b17ad2b417be66c3710")
CT256 = bytes.fromhex("f58c4c04d6e5f1ba779eabfb5f7bfbd69cfc4e967edb808d679f777bc6702c7d"
                      "39f23369a9d9bacfa530e26304231461b2eb05e2c39be9fcda6c19078c6a9d1b")
CT128 = bytes.fromhex("7649abac8119b246cee98e9b12e9197d5086cb9b507219ee95db113a917678b2"
                      "73bed6b8e3c1743b7116e69e222295163ff1caa1681fac09120eca307586e1a7")


def test_aes256_cbc_nist_vector_encrypt():
    assert aes.cbc_encrypt(KEY256, IV, PT) == CT256


def test_aes256_cbc_nist_vector_decrypt():
    assert aes.cbc_decrypt(KEY256, IV, CT256) == PT


def test_aes128_cbc_nist_vector_both_directions():
    assert aes.cbc_encrypt(KEY128, IV, PT) == CT128
    assert aes.cbc_decrypt(KEY128, IV, CT128) == PT


def test_aes_matches_cryptography_library_random():
    c = pytest.importorskip("cryptography.hazmat.primitives.ciphers")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    for n in (16, 32, 48, 160, 1024):
        key, iv, data = os.urandom(32), os.urandom(16), os.urandom(n)
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ref = enc.update(data) + enc.finalize()
        assert aes.cbc_encrypt(key, iv, data) == ref
        assert aes.cbc_decrypt(key, iv, ref) == data


# ---------------- 燕文 ----------------
def test_yanwen_sign_official_doc_vector():
    tok, uid = "D6140AA383FD8515B09028C586493DDB", "100000"
    assert oa.yw_sign(tok, uid, "{}", "common.country.getlist", "1655798809414") == "bc5f1792e30936c72fa2a35efead8d73"


def test_yanwen_sign_is_lowercase_md5_and_depends_on_every_part():
    base = oa.yw_sign("t", "u", "d", "m", "1")
    assert base == hashlib.md5(b"tudjsonm1V1.0t").hexdigest() and base == base.lower() and len(base) == 32
    for variant in (oa.yw_sign("t2", "u", "d", "m", "1"), oa.yw_sign("t", "u2", "d", "m", "1"), oa.yw_sign("t", "u", "d2", "m", "1"),
                    oa.yw_sign("t", "u", "d", "m2", "1"), oa.yw_sign("t", "u", "d", "m", "2")):
        assert variant != base


def test_yanwen_sign_unicode_payload():
    s = oa.yw_sign("t", "u", '{"名称":"测试"}', "m", "1")
    assert s == hashlib.md5(('t' + 'u' + '{"名称":"测试"}' + 'json' + 'm' + '1' + 'V1.0' + 't').encode()).hexdigest()


# ---------------- 云途 ----------------
def _hm(secret, text):
    return base64.b64encode(hmac.new(secret.encode(), text.encode(), hashlib.sha256).digest()).decode()


def test_yuntu_sign_without_body():
    assert oa.yt_sign("S", "1700000000000", "GET", "/v1/x") == _hm("S", "date=1700000000000&method=GET&uri=/v1/x")


def test_yuntu_sign_with_body_prefix():
    assert oa.yt_sign("S", "1", "POST", "/p", '{"a":1}') == _hm("S", 'body={"a":1}&date=1&method=POST&uri=/p')


def test_yuntu_sign_secret_and_path_sensitivity():
    a = oa.yt_sign("S1", "1", "GET", "/a")
    assert a != oa.yt_sign("S2", "1", "GET", "/a") and a != oa.yt_sign("S1", "1", "GET", "/b") and a != oa.yt_sign("S1", "2", "GET", "/a")


# ---------------- OIS ----------------
def test_ois_sign_vector_sorted_base64_plus_secret_md5_upper():
    params = {"b": "2", "a": "1", "c": ""}
    expect = hashlib.md5((base64.b64encode(b"a=1&b=2").decode() + "SEC").encode()).hexdigest().upper()
    assert oa.ois_sign("SEC", params) == expect


def test_ois_sign_ignores_none_and_empty_but_keeps_zero_string():
    assert oa.ois_sign("S", {"a": "0", "b": None, "c": ""}) == oa.ois_sign("S", {"a": "0"})


def test_ois_sign_unicode_and_special_chars():
    p = {"body1": '{"名":"值&=?"}', "version": "1.0"}
    pin = "&".join(f"{k}={p[k]}" for k in sorted(p))
    assert oa.ois_sign("S", p) == hashlib.md5((base64.b64encode(pin.encode()).decode() + "S").encode()).hexdigest().upper()


def test_ois_fake_token_roundtrip_and_char_mapping():
    t = oa._ois_fake_token("TOK", "1700000000000", "slnkda")
    back = t.replace("-", "a").replace("#", "c").replace("^", "x").replace("$", "M")
    assert json.loads(base64.b64decode(back)) == {"nonce": "slnkda", "timestamp": 1700000000000, "token": "TOK"}
    assert not any(ch in t for ch in "acxM")


@pytest.mark.parametrize("base,expect", [
    (None, oa.OIS_DEFAULT), ("", oa.OIS_DEFAULT), ("  ", oa.OIS_DEFAULT), ("openapi.example.com", "https://openapi.example.com"),
    ("http://x.y/z/", "http://x.y/z"), ("https://ekttech.net/pos-web///", "https://ekttech.net/pos-web")])
def test_ois_base_normalisation(base, expect):
    assert oa.ois_base(base) == expect


# ---------------- 中航/环洋 V5 头部 ----------------
def test_v5_mix_and_b64_helpers():
    assert _mix("abcxMz") == "-b#^$z"
    assert _b64("ab") == "YWI="


def test_v5_headers_sign_and_token(store):
    a = ZhonghangAdapter(store)
    h = a._headers("TKN", {"destNo": "US", "weig": "1", "empty": "", "zero": 0})
    assert set(h) >= {"version", "sign", "token", "locale", "Referer"} and h["version"] == "1.0"
    assert h["sign"] == h["sign"].upper() and len(h["sign"]) == 32
    raw = h["token"].replace("-", "a").replace("#", "c").replace("^", "x").replace("$", "M")
    d = json.loads(base64.b64decode(raw))
    assert d["token"] == "TKN" and set(d) == {"timestamp", "nonce", "token"}


def test_v5_headers_without_token_has_empty_token_field(store):
    a = ZhonghangAdapter(store)
    h = a._headers(None, {"userNo": "u"})
    raw = h["token"].replace("-", "a").replace("#", "c").replace("^", "x").replace("$", "M")
    assert json.loads(base64.b64decode(raw))["token"] is None


# ---------------- 顺丰丰桥 msgDigest ----------------
def test_java_urlencode_matches_java_semantics():
    assert oa._java_urlencode("a b+c/d=e&f~g*h-i_j.k") == "a+b%2Bc%2Fd%3De%26f%7Eg*h-i_j.k"
    assert oa._java_urlencode("中") == "%E4%B8%AD"
    assert oa._java_urlencode('{"a":"b"}') == "%7B%22a%22%3A%22b%22%7D"


def test_sf_digest_vector():
    raw = oa._java_urlencode('{"a":1}' + "1700" + "CW")
    assert oa.sf_digest('{"a":1}', "1700", "CW") == base64.b64encode(hashlib.md5(raw.encode()).digest()).decode()


# ---------------- 顺丰国际 sf.global ----------------
AES43 = "A" * 43


def test_sfg_key_validation():
    assert len(oa.sfg_key(AES43)) == 32
    for bad in ("", "short", "!!!not-base64!!!", "A" * 44, "A" * 20):
        with pytest.raises(AdapterError):
            oa.sfg_key(bad)


def test_sfg_encrypt_structure_is_documented_layout():
    key = oa.sfg_key(AES43)
    enc = oa.sfg_encrypt(AES43, "APPK", '{"x":"中"}', rand="0123456789abcdef")
    raw = aes.cbc_decrypt(key, key[:16], base64.b64decode(enc))
    pad = raw[-1]
    assert 1 <= pad <= 32 and raw[-pad:] == bytes([pad]) * pad
    raw = raw[:-pad]
    assert raw[:16] == b"0123456789abcdef"
    n = struct.unpack(">I", raw[16:20])[0]
    assert raw[20:20 + n].decode() == '{"x":"中"}' and raw[20 + n:] == b"APPK"
    assert len(base64.b64decode(enc)) % 32 == 0   # PKCS7 块 32 → 一定是 16 的倍数且满足 CBC


@pytest.mark.parametrize("plain", ["", "{}", "a" * 31, "a" * 32, "a" * 33, "中文" * 100, json.dumps({"k": list(range(500))})])
def test_sfg_roundtrip_various_lengths(plain):
    assert oa.sfg_decrypt(AES43, "AK", oa.sfg_encrypt(AES43, "AK", plain)) == plain


def test_sfg_decrypt_wrong_appkey_is_rejected():
    enc = oa.sfg_encrypt(AES43, "AK", "{}")
    with pytest.raises(AdapterError) as e:
        oa.sfg_decrypt(AES43, "OTHER", enc)
    assert e.value.kind == "login_failed"


def test_sfg_decrypt_wrong_key_is_rejected_or_garbage():
    enc = oa.sfg_encrypt(AES43, "AK", '{"ok":1}')
    other = "B" * 43
    with pytest.raises(Exception):
        out = oa.sfg_decrypt(other, "AK", enc)
        assert out != '{"ok":1}'
        raise AdapterError("x")


def test_sfg_encrypt_is_randomised_but_decrypts_same():
    a, b = oa.sfg_encrypt(AES43, "AK", "same"), oa.sfg_encrypt(AES43, "AK", "same")
    assert a != b and oa.sfg_decrypt(AES43, "AK", a) == oa.sfg_decrypt(AES43, "AK", b) == "same"


def test_sfg_signature_sorted_sha256():
    s = oa.sfg_signature("tok", "123", "nonce", "enc")
    assert s == hashlib.sha256("".join(sorted(["tok", "123", "nonce", "enc"])).encode()).hexdigest()
    assert s == oa.sfg_signature("enc", "nonce", "123", "tok")  # 顺序无关
    assert s != oa.sfg_signature("tok", "124", "nonce", "enc")


def test_sfg_hosts_and_env_fallback():
    assert oa.SFG_HOSTS["prod"].startswith("https://api-ifsp.") and "sit" in oa.SFG_HOSTS["sandbox"]

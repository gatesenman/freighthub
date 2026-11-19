"""静态加密（at rest）：AES-256-GCM。密钥来自环境变量 FH_DATA_KEY（base64，32 字节），绝不进代码仓库。

密文格式：``fh1.<base64url(nonce || ciphertext+tag)>``；AAD 绑定用途（如 ``account:yuntu``），
防止把 A 处的密文挪到 B 处解密。丢了密钥 = 平台账号密文无法恢复（需重新导入账号）。
"""
import base64
import json
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "fh1."


class SecretBoxError(RuntimeError):
    pass


def generate_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _load_key(raw: str) -> bytes:
    raw = (raw or "").strip()
    try:
        k = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception:
        k = b""
    if len(k) != 32:
        raise SecretBoxError("FH_DATA_KEY 必须是 base64 编码的 32 字节随机密钥（可用 python -c \"import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\" 生成）")
    return k


class SecretBox:
    def __init__(self, key: str | None = None):
        self.aes = AESGCM(_load_key(key if key is not None else os.environ.get("FH_DATA_KEY", "")))

    def seal(self, obj, aad: str) -> str:
        nonce = secrets.token_bytes(12)
        ct = self.aes.encrypt(nonce, json.dumps(obj, ensure_ascii=False).encode("utf-8"), aad.encode())
        return PREFIX + base64.urlsafe_b64encode(nonce + ct).decode()

    def open(self, blob: str, aad: str):
        if not isinstance(blob, str) or not blob.startswith(PREFIX):
            raise SecretBoxError("不是受支持的密文格式")
        try:
            raw = base64.urlsafe_b64decode(blob[len(PREFIX):] + "=" * (-len(blob[len(PREFIX):]) % 4))
            return json.loads(self.aes.decrypt(raw[:12], raw[12:], aad.encode()).decode("utf-8"))
        except InvalidTag:
            raise SecretBoxError("解密失败：密钥不匹配或数据被篡改")

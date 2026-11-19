"""日志 / 错误信息脱敏：URL 查询串里的密钥、以及已保存的密钥原文，一律替换成 ***。"""
import re

_PARAM = re.compile(r"(?i)\b(app[_-]?secret|secret|password|passwd|pwd|token|access[_-]?token|sign|signature|msgdigest|checkword|key)=([^&\s'\")]+)")


def redact(text, secrets=()):
    s = str(text)
    s = _PARAM.sub(lambda m: f"{m.group(1)}=***", s)
    for v in secrets:
        if v and isinstance(v, str) and len(v) >= 4:
            s = s.replace(v, "***")
    return s

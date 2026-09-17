# -*- coding: utf-8 -*-
"""
鉴权：签名 Token（结构类似 JWT，但不引入额外依赖）。

真实项目请用 JWT（PyJWT）+ 密码哈希（bcrypt/argon2）。
这里是教学用最小实现，重点演示这类缺陷：
- 越权访问（authz）：能通过鉴权，但没校验资源归属
- 无 Token / 错 Token 应被拒
"""
import base64
import hashlib
import hmac
import json
import os
import time

SECRET = os.getenv("LAB_SECRET", "lab-secret-do-not-use-in-production").encode()
TOKEN_TTL = 3600  # 1 小时


class AuthError(Exception):
    """鉴权失败。"""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload_b64: str) -> str:
    mac = hmac.new(SECRET, payload_b64.encode(), hashlib.sha256).digest()
    return _b64e(mac)


def create_token(user_id: int, username: str) -> str:
    payload = {
        "uid": user_id,
        "sub": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_TTL,
    }
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_token(token: str) -> dict:
    """校验签名与有效期，返回 payload。"""
    if not token or "." not in token:
        raise AuthError("Token 格式错误")
    payload_b64, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload_b64)):
        raise AuthError("Token 签名校验失败")
    try:
        payload = json.loads(_b64d(payload_b64))
    except Exception:
        raise AuthError("Token 解析失败")
    if payload.get("exp", 0) < int(time.time()):
        raise AuthError("Token 已过期")
    return payload

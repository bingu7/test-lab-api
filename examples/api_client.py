# -*- coding: utf-8 -*-
"""
统一请求封装。

对应框架设计教程里的「基础层」：把日志、鉴权、断言、地址拼接
等横切关注点收敛到一处，用例层不必重复处理。
"""
import logging

import requests

logger = logging.getLogger(__name__)


class ApiClient:
    def __init__(self, base_url: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self._token = None

    # -------------------------------------------------- 鉴权

    def set_token(self, token: str) -> None:
        self._token = token

    def clear_token(self) -> None:
        self._token = None

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if extra:
            headers.update(extra)
        return headers

    # -------------------------------------------------- 请求

    def request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        kwargs.setdefault("timeout", self.timeout)
        kwargs["headers"] = self._headers(kwargs.pop("headers", None))
        logger.info("→ %s %s body=%s", method.upper(), url, kwargs.get("json"))
        resp = self.session.request(method, url, **kwargs)
        logger.info("← %s [%s] %s", url, resp.status_code, resp.text[:300])
        return self._parse(resp)

    @staticmethod
    def _parse(resp: requests.Response) -> dict:
        """统一解析：返回 {status, code, message, data}，避免用例层到处 .json()。"""
        try:
            body = resp.json()
        except ValueError:
            body = {}
        return {
            "status": resp.status_code,          # HTTP 状态码
            "code": body.get("code"),            # 业务码
            "message": body.get("message"),
            "data": body.get("data"),
            "raw": body,
        }

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    # -------------------------------------------------- 断言

    @staticmethod
    def assert_success(resp: dict, msg: str = "") -> None:
        """断言业务成功。附带完整上下文，失败时不需要看日志就能定位。"""
        assert resp["code"] == 0, (
            f"{msg} 业务码非 0：code={resp['code']} "
            f"message={resp['message']} | HTTP={resp['status']} | 原文={resp['raw']}"
        )

    @staticmethod
    def assert_code(resp: dict, expected: int, msg: str = "") -> None:
        assert resp["code"] == expected, (
            f"{msg} 业务码不符：期望 {expected}，实际 {resp['code']} "
            f"(message={resp['message']}) | 原文={resp['raw']}"
        )

    @staticmethod
    def assert_http_status(resp: dict, expected: int, msg: str = "") -> None:
        assert resp["status"] == expected, (
            f"{msg} HTTP 状态码不符：期望 {expected}，实际 {resp['status']}"
        )

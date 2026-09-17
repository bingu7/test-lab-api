# -*- coding: utf-8 -*-
"""
Pytest 全局 fixture。

对应框架设计教程里的关键实践：
- 会话级 Token 复用（整个测试只登录一次）
- 用例级数据重置（保证用例独立、可重复运行）
"""
import os

import pytest

from api_client import ApiClient

BASE_URL = os.getenv("LAB_BASE_URL", "http://127.0.0.1:8099")

USER = {"username": "alice", "password": "alice123"}   # 会员
USER_B = {"username": "bob", "password": "bob123"}     # 普通用户


@pytest.fixture(scope="session")
def client() -> ApiClient:
    """全局唯一的客户端，整个测试会话复用（复用连接、共享日志）。"""
    return ApiClient(BASE_URL)


@pytest.fixture(scope="session", autouse=True)
def _check_env(client):
    """环境自检：环境不可达时快速失败，而不是让每条用例都报错。"""
    resp = client.get("/health")
    assert resp["code"] == 0, f"服务不可达：{BASE_URL}，请先启动 Mock 服务"
    return resp


@pytest.fixture
def reset_data(client):
    """
    用例级数据重置。

    这是「用例之间互相独立」的关键：每条用例开始前把数据恢复初始状态，
    于是用例可以单独运行、可以并行、失败一定是真问题。
    """
    client.post("/lab/reset")
    return True


@pytest.fixture
def auth_client(client):
    """已登录的客户端（alice，会员）。"""
    resp = client.post("/api/login", json=USER)
    ApiClient.assert_success(resp, "登录失败")
    client.set_token(resp["data"]["token"])
    yield client
    client.clear_token()


@pytest.fixture
def auth_client_b(client):
    """已登录的客户端（bob，普通用户）——用于越权测试。"""
    resp = client.post("/api/login", json=USER_B)
    ApiClient.assert_success(resp, "登录失败")
    token_b = resp["data"]["token"]
    yield {"client": client, "token": token_b}
    client.clear_token()

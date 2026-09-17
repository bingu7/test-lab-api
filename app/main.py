# -*- coding: utf-8 -*-
"""
HTTP 接口层。

统一响应结构（模仿真实企业项目）：
    成功: {"code": 0, "message": "ok", "data": {...}}
    失败: {"code": 1002, "message": "用户名或密码错误", "data": null}

注意：业务失败仍返回 HTTP 200，靠 `code` 区分。
这是国内很常见的约定，也是接口测试必须注意的点——
**只断言 HTTP 200 是不够的，必须同时断言业务码。**
"""
from typing import Optional

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse

from . import config, services
from .auth import AuthError, create_token, verify_token
from .db import init_db

app = FastAPI(
    title="测试练习用 Mock 服务",
    description="极简电商业务接口，支持植入可控缺陷，用于接口/自动化测试练习。",
    version="1.0.0",
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------------------------------------------------------------- 通用

def ok(data=None, message: str = "ok"):
    return {"code": 0, "message": message, "data": data}


@app.exception_handler(services.BizError)
async def biz_error_handler(request: Request, exc: services.BizError):
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


def current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """从 Authorization: Bearer <token> 解析当前用户。"""
    if not authorization:
        raise services.BizError(1401, "未提供 Token", http_status=401)
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    try:
        payload = verify_token(token)
    except AuthError as e:
        raise services.BizError(1401, str(e), http_status=401)
    return {"user_id": payload["uid"], "username": payload["sub"]}


# ---------------------------------------------------------------- 基础

@app.get("/health", summary="健康检查")
def health():
    return ok({"status": "up"})


@app.get("/lab/info", summary="查看当前缺陷开关状态（练习辅助）")
def lab_info():
    """返回当前启用了哪些缺陷。练习找缺陷时可先不看，直接测。"""
    return ok({
        "active_flaws": sorted(config.active_flaws()),
        "all_flaws": sorted(config.ALL_FLAWS),
        "clean_mode": len(config.active_flaws()) == 0,
    })


@app.post("/lab/reset", summary="重置测试数据（练习辅助）")
def lab_reset():
    return ok(services.test_reset())


@app.get("/lab/state", summary="查看当前数据状态（练习辅助）")
def lab_state():
    return ok(services.test_state())


# ---------------------------------------------------------------- 认证

@app.post("/api/login", summary="登录")
def login(body: dict):
    user = services.login(body.get("username", ""), body.get("password", ""))
    token = create_token(user["id"], user["username"])
    return ok({**user, "token": token})


@app.get("/api/me", summary="查询当前登录用户")
def me(user: dict = Depends(current_user)):
    return ok(services.get_balance(user["user_id"]))


# ---------------------------------------------------------------- 商品

@app.get("/api/products", summary="商品列表（支持分页与关键词）")
def products(keyword: str = "", page: int = 1, size: int = 10):
    return ok(services.list_products(keyword, page, size))


@app.get("/api/products/{product_id}", summary="商品详情")
def product_detail(product_id: int):
    return ok(services.get_product(product_id))


# ---------------------------------------------------------------- 购物车

@app.post("/api/cart", summary="加入购物车")
def cart_add(body: dict, user: dict = Depends(current_user)):
    return ok(services.add_to_cart(
        user["user_id"], int(body.get("product_id", 0)), int(body.get("quantity", 0))
    ))


@app.get("/api/cart", summary="查看购物车")
def cart_get(user: dict = Depends(current_user)):
    return ok(services.get_cart(user["user_id"]))


# ---------------------------------------------------------------- 订单

@app.post("/api/orders", summary="创建订单（扣库存）")
def order_create(body: dict, user: dict = Depends(current_user)):
    return ok(services.create_order(
        user["user_id"], int(body.get("product_id", 0)), int(body.get("quantity", 0))
    ))


@app.get("/api/orders", summary="我的订单列表")
def order_list(user: dict = Depends(current_user)):
    return ok(services.list_orders(user["user_id"]))


@app.get("/api/orders/{order_id}", summary="订单详情")
def order_detail(order_id: int, user: dict = Depends(current_user)):
    return ok(services.get_order(user["user_id"], order_id))


# ---------------------------------------------------------------- 支付

@app.post("/api/pay/callback", summary="支付回调（会重复推送）")
def pay_callback(body: dict, user: dict = Depends(current_user)):
    """
    真实场景由支付平台调用，这里为方便练习需要带 Token。
    重复调用同一个 trade_no 就是「重复通知」场景。
    """
    return ok(services.pay_order(
        user["user_id"],
        int(body.get("order_id", 0)),
        str(body.get("trade_no", "")),
        int(body.get("amount", 0)),
    ))

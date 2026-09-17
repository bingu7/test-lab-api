# -*- coding: utf-8 -*-
"""
下单支付链路的自动化用例。

演示框架设计教程里的分层做法：
- 用例层只表达业务意图，不出现 URL 拼接、headers、requests 调用
- 断言带完整上下文
- 用例之间独立（依赖 reset_data fixture）
"""
import pytest

from api_client import ApiClient

# 商品 ID（对应种子数据）
KEYBOARD = 1      # 机械键盘 399.99 元，库存 100
CABLE = 4         # USB 数据线 18.99 元，库存 500
MONITOR = 3       # 显示器 1299.99 元，库存 30


# ============================================================ 登录与鉴权

class TestAuth:

    def test_login_success(self, client, reset_data):
        resp = client.post("/api/login", json={"username": "alice", "password": "alice123"})
        ApiClient.assert_success(resp, "正常登录")
        assert "token" in resp["data"]
        assert resp["data"]["is_member"] is True

    def test_login_wrong_password(self, client, reset_data):
        resp = client.post("/api/login", json={"username": "alice", "password": "wrong"})
        ApiClient.assert_code(resp, 1002, "密码错误")

    def test_login_empty_username(self, client, reset_data):
        resp = client.post("/api/login", json={"username": "", "password": "alice123"})
        ApiClient.assert_code(resp, 1002, "用户名为空")

    def test_me_without_token(self, client, reset_data):
        client.clear_token()
        resp = client.get("/api/me")
        ApiClient.assert_http_status(resp, 401, "未带 Token")

    def test_me_with_bad_token(self, client, reset_data):
        client.set_token("bad.token")
        resp = client.get("/api/me")
        ApiClient.assert_http_status(resp, 401, "伪造 Token")
        client.clear_token()


# ============================================================ 商品与分页

class TestProduct:

    def test_pagination(self, client, reset_data):
        resp = client.get("/api/products", params={"page": 1, "size": 2})
        ApiClient.assert_success(resp)
        assert len(resp["data"]["items"]) == 2
        assert resp["data"]["total"] == 5

    def test_last_page_partial(self, client, reset_data):
        """5 条数据、每页 2 条，第 3 页应只有 1 条。"""
        resp = client.get("/api/products", params={"page": 3, "size": 2})
        ApiClient.assert_success(resp)
        assert len(resp["data"]["items"]) == 1

    def test_search_by_keyword(self, client, reset_data):
        resp = client.get("/api/products", params={"keyword": "键盘"})
        ApiClient.assert_success(resp)
        assert len(resp["data"]["items"]) == 1
        assert "键盘" in resp["data"]["items"][0]["name"]

    def test_product_not_found(self, client, reset_data):
        resp = client.get("/api/products/999999")
        ApiClient.assert_http_status(resp, 404)


# ============================================================ 金额计算

class TestAmount:

    @pytest.mark.parametrize("product_id, quantity, expect_discount", [
        # 会员 85 折，折扣 = 小计 - 小计*85//100（整数运算）
        (KEYBOARD, 1, 6000),     # 小计 39999 → 折扣 6000
        (KEYBOARD, 2, 12000),    # 小计 79998 → 折扣 12000
        (CABLE, 1, 285),         # 小计  1899 → 折扣  285
    ], ids=["键盘x1", "键盘x2", "数据线x1"])
    def test_member_discount(self, auth_client, reset_data,
                             product_id, quantity, expect_discount):
        resp = auth_client.post("/api/orders",
                                json={"product_id": product_id, "quantity": quantity})
        ApiClient.assert_success(resp)
        order = resp["data"]

        assert order["discount"] == expect_discount, (
            f"折扣错误：期望 {expect_discount}，实际 {order['discount']} "
            f"（金额单位：分）"
        )

        # 应付金额 = 小计 - 折扣 (+ 运费，未满 9900 分时)
        subtotal = order["items"][0]["price"] * quantity
        payable = subtotal - expect_discount
        shipping = 0 if payable >= 9900 else 800
        assert order["total_amount"] == payable + shipping, (
            f"应付错误：期望 {payable + shipping}，实际 {order['total_amount']}"
        )

    def test_rounding_is_exact(self, auth_client, reset_data):
        """
        精度校验：折扣必须用整数运算，不能出现浮点误差。

        39999 分用浮点算是 int(39999*0.15)=5999，会少 1 分。
        这类 1 分钱误差在真实系统里是明确的缺陷（对账不平）。
        """
        resp = auth_client.post("/api/orders",
                                json={"product_id": KEYBOARD, "quantity": 1})
        ApiClient.assert_success(resp)
        order = resp["data"]
        subtotal = order["items"][0]["price"] * 1
        exact = subtotal - (subtotal * 85) // 100
        floating = int(subtotal * (1 - 0.85))

        assert exact == floating or order["discount"] == exact, (
            f"折扣出现浮点误差：整数运算应得 {exact}，浮点算得 {floating}，"
            f"接口实际返回 {order['discount']}"
        )


# ============================================================ 库存

class TestStock:

    def test_stock_deducted(self, auth_client, reset_data):
        before = auth_client.get(f"/api/products/{MONITOR}")["data"]["stock"]
        resp = auth_client.post("/api/orders",
                                json={"product_id": MONITOR, "quantity": 2})
        ApiClient.assert_success(resp)
        after = auth_client.get(f"/api/products/{MONITOR}")["data"]["stock"]
        assert after == before - 2, f"库存应从 {before} 扣 2，实际 {after}"

    def test_insufficient_stock(self, auth_client, reset_data):
        resp = auth_client.post("/api/orders",
                                json={"product_id": MONITOR, "quantity": 99999})
        ApiClient.assert_code(resp, 3001, "库存不足")

    def test_concurrent_no_oversell(self, auth_client, reset_data):
        """
        并发下单不能超卖。

        这类缺陷只在并发下出现：单线程反复调用一定测不出来。
        需要用真实的并发请求去触发「检查库存」与「扣减库存」之间的竞态窗口。

        注：这个用例在「干净模式」下通过，在「缺陷模式」下应该失败。
        """
        import concurrent.futures

        stock_before = auth_client.get(f"/api/products/{MONITOR}")["data"]["stock"]
        qty = 3
        concurrency = 20          # 20 × 3 = 60 件 >> 库存 30 件

        def buy(_):
            # 每个请求都用同一个已登录客户端（Session 复用连接）
            return auth_client.post("/api/orders",
                                    json={"product_id": MONITOR, "quantity": qty})

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
            results = list(ex.map(buy, range(concurrency)))

        success = sum(1 for r in results if r["code"] == 0)
        stock_after = auth_client.get(f"/api/products/{MONITOR}")["data"]["stock"]
        sold = stock_before - stock_after

        assert stock_after >= 0, (
            f"超卖缺陷：初始库存 {stock_before}，成功下单 {success} 笔（每笔 {qty} 件），"
            f"剩余库存 {stock_after}（负数即为超卖）"
        )
        assert sold <= stock_before, (
            f"超卖缺陷：共售出 {sold} 件，超过初始库存 {stock_before} 件"
        )
        assert success * qty <= stock_before, (
            f"超卖缺陷：成功下单 {success} 笔 × {qty} 件 = {success * qty} 件，"
            f"超过库存 {stock_before} 件"
        )


# ============================================================ 支付与幂等

class TestPayment:

    def _create_order(self, client) -> dict:
        resp = client.post("/api/orders", json={"product_id": KEYBOARD, "quantity": 1})
        ApiClient.assert_success(resp, "下单")
        return resp["data"]

    def test_pay_success(self, auth_client, reset_data):
        order = self._create_order(auth_client)
        resp = auth_client.post("/api/pay/callback", json={
            "order_id": order["id"],
            "trade_no": f"TRADE-{order['id']}",
            "amount": order["total_amount"],
        })
        ApiClient.assert_success(resp, "支付")
        assert resp["data"]["status"] == "paid"

    def test_pay_amount_mismatch(self, auth_client, reset_data):
        """篡改金额应被服务端拒绝（以订单金额为准）。"""
        order = self._create_order(auth_client)
        resp = auth_client.post("/api/pay/callback", json={
            "order_id": order["id"],
            "trade_no": f"TRADE-{order['id']}",
            "amount": 1,                      # 故意篡改成 1 分
        })
        ApiClient.assert_code(resp, 4002, "金额篡改")

    def test_callback_idempotent(self, auth_client, reset_data):
        """
        幂等核心用例：同一个 trade_no 重复回调，只能扣一次款。

        这是最容易被忽略、后果也最严重的一类缺陷（重复扣款 = 资损）。
        """
        order = self._create_order(auth_client)
        balance_before = auth_client.get("/api/me")["data"]["balance"]

        payload = {
            "order_id": order["id"],
            "trade_no": f"TRADE-{order['id']}",
            "amount": order["total_amount"],
        }
        first = auth_client.post("/api/pay/callback", json=payload)
        second = auth_client.post("/api/pay/callback", json=payload)
        third = auth_client.post("/api/pay/callback", json=payload)

        ApiClient.assert_success(first, "首次回调")

        balance_after = auth_client.get("/api/me")["data"]["balance"]
        deducted = balance_before - balance_after

        assert deducted == order["total_amount"], (
            f"重复回调导致重复扣款：共扣 {deducted} 分，"
            f"应只扣 {order['total_amount']} 分（3 次回调）"
        )

        # 支付记录也应只有一条
        state = auth_client.get("/lab/state")["data"]
        pay_count = len([p for p in state["payments"]
                         if p["trade_no"] == payload["trade_no"]])
        assert pay_count == 1, f"同一 trade_no 应只有 1 条支付记录，实际 {pay_count} 条"


# ============================================================ 越权

class TestAuthorization:

    def test_cannot_view_others_order(self, auth_client, auth_client_b):
        """
        水平越权：bob 不能查看 alice 的订单。

        这类缺陷不会在功能测试中暴露——功能都正常，只是权限没校验。
        """
        # alice 下单
        resp = auth_client.post("/api/orders", json={"product_id": KEYBOARD, "quantity": 1})
        ApiClient.assert_success(resp, "alice 下单")
        order_id = resp["data"]["id"]

        # bob 用自己合法的 token 去查 alice 的订单
        client = auth_client_b["client"]
        client.set_token(auth_client_b["token"])
        resp = client.get(f"/api/orders/{order_id}")

        assert resp["status"] == 404, (
            f"越权缺陷：bob 不应能查看 alice 的订单 {order_id}，"
            f"实际 HTTP={resp['status']} code={resp['code']} data={resp['data']}"
        )
        client.clear_token()


# ============================================================ 购物车参数校验

class TestCartValidation:

    @pytest.mark.parametrize("quantity", [0, -1, -100], ids=["零", "负一", "负一百"])
    def test_invalid_quantity_rejected(self, auth_client, reset_data, quantity):
        resp = auth_client.post("/api/cart",
                                json={"product_id": KEYBOARD, "quantity": quantity})
        ApiClient.assert_code(resp, 2002, f"数量 {quantity} 应被拒绝")

    def test_too_large_quantity_rejected(self, auth_client, reset_data):
        resp = auth_client.post("/api/cart",
                                json={"product_id": KEYBOARD, "quantity": 1000})
        ApiClient.assert_code(resp, 2003, "数量超上限")

    def test_valid_quantity_accepted(self, auth_client, reset_data):
        resp = auth_client.post("/api/cart",
                                json={"product_id": KEYBOARD, "quantity": 2})
        ApiClient.assert_success(resp, "正常加入购物车")

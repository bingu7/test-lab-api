# -*- coding: utf-8 -*-
"""
业务逻辑层。

这一层承载全部业务规则，也是缺陷植入的位置。
每个缺陷都用 `if config.has("xxx")` 显式包裹，方便：
1. 干净模式下行为完全正确
2. 对照阅读时一眼看出「缺陷是怎么引入的」
"""
import time
from datetime import datetime, timezone

from . import config
from .db import tx


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BizError(Exception):
    """业务异常，带业务码。"""

    def __init__(self, code: int, message: str, http_status: int = 200):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


# ---------------------------------------------------------------- 登录

def login(username: str, password: str) -> dict:
    conn = tx_conn = None
    with tx() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
    if row is None or row["password"] != password:
        # 注意：不区分「用户不存在」和「密码错误」，避免账号枚举。
        # 这是安全测试的一个检查点。
        raise BizError(1002, "用户名或密码错误")
    return {
        "id": row["id"],
        "username": row["username"],
        "nickname": row["nickname"],
        "is_member": bool(row["is_member"]),
    }


# ---------------------------------------------------------------- 商品

def list_products(keyword: str = "", page: int = 1, size: int = 10) -> dict:
    offset = (page - 1) * size
    with tx() as conn:
        if keyword:
            total = conn.execute(
                "SELECT COUNT(*) c FROM products WHERE status='on' AND name LIKE ?",
                (f"%{keyword}%",),
            ).fetchone()["c"]
            rows = conn.execute(
                "SELECT * FROM products WHERE status='on' AND name LIKE ? "
                "ORDER BY id LIMIT ? OFFSET ?",
                (f"%{keyword}%", size, offset),
            ).fetchall()
        else:
            total = conn.execute(
                "SELECT COUNT(*) c FROM products WHERE status='on'"
            ).fetchone()["c"]
            rows = conn.execute(
                "SELECT * FROM products WHERE status='on' "
                "ORDER BY id LIMIT ? OFFSET ?",
                (size, offset),
            ).fetchall()
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [dict(r) for r in rows],
    }


def get_product(product_id: int) -> dict:
    with tx() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id=?", (product_id,)
        ).fetchone()
    if row is None:
        raise BizError(2001, "商品不存在", http_status=404)
    return dict(row)


# ---------------------------------------------------------------- 购物车

def add_to_cart(user_id: int, product_id: int, quantity: int) -> dict:
    # 【缺陷 cartqty】购物车不做数量校验：0、负数都能加进去，
    # 下游下单时才会出现金额为 0 或负数、库存反向增加的怪现象。
    if not config.has("cartqty"):
        if quantity <= 0:
            raise BizError(2002, "购买数量必须大于 0")
        if quantity > 999:
            raise BizError(2003, "单次购买数量不能超过 999")

    with tx() as conn:
        product = conn.execute(
            "SELECT * FROM products WHERE id=?", (product_id,)
        ).fetchone()
        if product is None:
            raise BizError(2001, "商品不存在", http_status=404)
        if product["status"] != "on":
            raise BizError(2004, "商品已下架")

        existing = conn.execute(
            "SELECT * FROM carts WHERE user_id=? AND product_id=?",
            (user_id, product_id),
        ).fetchone()
        if existing:
            new_qty = existing["quantity"] + quantity
            conn.execute(
                "UPDATE carts SET quantity=? WHERE id=?",
                (new_qty, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO carts(user_id,product_id,quantity) VALUES(?,?,?)",
                (user_id, product_id, quantity),
            )
    return get_cart(user_id)


def get_cart(user_id: int) -> dict:
    with tx() as conn:
        rows = conn.execute(
            """SELECT c.id, c.product_id, c.quantity, p.name, p.price, p.stock
               FROM carts c JOIN products p ON p.id = c.product_id
               WHERE c.user_id=? ORDER BY c.id""",
            (user_id,),
        ).fetchall()
    items = [dict(r) for r in rows]
    # 注意：购物车这里只算金额，不扣库存
    amount = sum(i["price"] * i["quantity"] for i in items)
    return {"user_id": user_id, "items": items, "amount": amount}


# ---------------------------------------------------------------- 金额计算

def calc_amount(items: list, is_member: bool) -> dict:
    """
    计算应付金额。单位：分。

    规则：
    - 商品小计 = Σ(单价 × 数量)
    - 会员享受 85 折
    - 折后满 99 元包邮，否则加 8 元运费
    """
    subtotal = sum(i["price"] * i["quantity"] for i in items)

    if is_member:
        # 【缺陷 rounding】用浮点计算折扣，再取整。
        # 0.85 无法用二进制精确表示，某些金额会出现 1 分误差。
        # 正确做法：整数运算 (subtotal * 85) // 100
        if config.has("rounding"):
            discount = int(subtotal * (1 - config.DISCOUNT_RATE))
        else:
            discount = subtotal - (subtotal * 85) // 100
    else:
        discount = 0

    payable = subtotal - discount
    shipping = 0 if payable >= config.FREE_SHIPPING_THRESHOLD else config.SHIPPING_FEE

    return {
        "subtotal": subtotal,
        "discount": discount,
        "shipping": shipping,
        "payable": payable + shipping,
    }


# ---------------------------------------------------------------- 下单

def create_order(user_id: int, product_id: int, quantity: int) -> dict:
    """
    下单：扣库存 + 建订单。

    【缺陷 oversell】分两步走：
        第 1 步（独立事务）：读库存并校验
        第 2 步（另一个事务）：扣减
    两步之间存在真实竞态窗口——多个请求可能都读到「库存充足」，
    然后各自扣减，导致超卖。

    注意为什么这样写才真实：
    如果把「读 + 校验 + 扣减」放进同一个 BEGIN IMMEDIATE 事务，
    SQLite 的写锁会把并发请求串行化，反而不会超卖。
    真实系统的超卖几乎都来自「校验与写入不在同一个原子操作里」
    （比如：先查缓存/先查从库/先校验后写），这里就是这种情况。

    正确做法：`UPDATE ... WHERE stock >= ?` 靠数据库条件更新保证原子性
    （见下方 else 分支），或用 SELECT ... FOR UPDATE / 乐观锁版本号。
    """
    order_no = f"SO{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

    if config.has("oversell"):
        # 第 1 步：只读事务，校验库存（此时不持有写锁）
        with tx() as conn:
            product = conn.execute(
                "SELECT * FROM products WHERE id=?", (product_id,)
            ).fetchone()
            if product is None:
                raise BizError(2001, "商品不存在", http_status=404)
            if product["status"] != "on":
                raise BizError(2004, "商品已下架")
            if product["stock"] < quantity:
                raise BizError(3001, "库存不足")
            price_snapshot = product["price"]

        # 模拟真实系统在两步之间的处理耗时（查询、风控、组包等）。
        # 这个延迟正是竞态窗口被放大的原因。
        time.sleep(0.05)

        # 第 2 步：另开事务扣减（不再校验，直接减）
        with tx() as conn:
            conn.execute(
                "UPDATE products SET stock = stock - ? WHERE id=?",
                (quantity, product_id),
            )
            user = conn.execute(
                "SELECT * FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if user is None:
                raise BizError(1002, "用户不存在")
            amount = calc_amount(
                [{"price": price_snapshot, "quantity": quantity}],
                bool(user["is_member"]),
            )
            cur = conn.execute(
                """INSERT INTO orders(order_no,user_id,total_amount,discount,status,created_at)
                   VALUES(?,?,?,?, 'pending', ?)""",
                (order_no, user_id, amount["payable"], amount["discount"], now()),
            )
            order_id = cur.lastrowid
            conn.execute(
                """INSERT INTO order_items(order_id,product_id,quantity,price)
                   VALUES(?,?,?,?)""",
                (order_id, product_id, quantity, price_snapshot),
            )
            conn.execute(
                "INSERT INTO order_logs(order_id,action,detail,created_at) VALUES(?,?,?,?)",
                (order_id, "create", f"下单 {quantity} 件", now()),
            )
        return get_order(user_id, order_id)

    # -------- 正确实现：单事务 + 条件更新，原子扣减 --------
    with tx() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if user is None:
            raise BizError(1002, "用户不存在")
        product = conn.execute(
            "SELECT * FROM products WHERE id=?", (product_id,)
        ).fetchone()
        if product is None:
            raise BizError(2001, "商品不存在", http_status=404)
        if product["status"] != "on":
            raise BizError(2004, "商品已下架")

        # 条件更新：库存不足时 rowcount=0，由数据库保证原子性
        cur = conn.execute(
            "UPDATE products SET stock = stock - ? WHERE id=? AND stock >= ?",
            (quantity, product_id, quantity),
        )
        if cur.rowcount == 0:
            raise BizError(3001, "库存不足")

        amount = calc_amount(
            [{"price": product["price"], "quantity": quantity}],
            bool(user["is_member"]),
        )
        cur = conn.execute(
            """INSERT INTO orders(order_no,user_id,total_amount,discount,status,created_at)
               VALUES(?,?,?,?, 'pending', ?)""",
            (order_no, user_id, amount["payable"], amount["discount"], now()),
        )
        order_id = cur.lastrowid
        conn.execute(
            """INSERT INTO order_items(order_id,product_id,quantity,price)
               VALUES(?,?,?,?)""",
            (order_id, product_id, quantity, product["price"]),
        )
        conn.execute(
            "INSERT INTO order_logs(order_id,action,detail,created_at) VALUES(?,?,?,?)",
            (order_id, "create", f"下单 {quantity} 件", now()),
        )

    return get_order(user_id, order_id)


def get_order(user_id: int, order_id: int) -> dict:
    """
    【缺陷 authz】不校验订单归属，任何登录用户都能查别人的订单。
    正确做法：WHERE id=? AND user_id=?
    """
    with tx() as conn:
        if config.has("authz"):
            row = conn.execute(
                "SELECT * FROM orders WHERE id=?", (order_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM orders WHERE id=? AND user_id=?",
                (order_id, user_id),
            ).fetchone()
        if row is None:
            raise BizError(4004, "订单不存在", http_status=404)
        items = conn.execute(
            """SELECT oi.*, p.name FROM order_items oi
               JOIN products p ON p.id = oi.product_id
               WHERE oi.order_id=?""",
            (order_id,),
        ).fetchall()
    data = dict(row)
    data["items"] = [dict(i) for i in items]
    return data


def list_orders(user_id: int) -> dict:
    with tx() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user_id,)
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


# ---------------------------------------------------------------- 支付

def pay_order(user_id: int, order_id: int, trade_no: str, amount: int) -> dict:
    """
    支付回调。

    真实场景由支付平台异步通知，会重复推送，因此必须幂等。

    【缺陷 idempotent】不做幂等判断：
    - 同一个 trade_no 重复通知，会被当成新支付再次处理
    - 余额被重复扣减，产生重复的 payments 记录
    正确做法：先按 trade_no 查是否已处理，已处理则直接返回成功（幂等返回）。
    """
    with tx() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()
        if order is None:
            raise BizError(4004, "订单不存在", http_status=404)

        if not config.has("idempotent"):
            # 正确版：幂等校验
            done = conn.execute(
                "SELECT * FROM payments WHERE trade_no=?", (trade_no,)
            ).fetchone()
            if done is not None:
                return {"order_id": order_id, "status": order["status"],
                        "idempotent": True, "message": "该支付已处理，直接返回"}

        if order["status"] == "paid" and not config.has("idempotent"):
            return {"order_id": order_id, "status": "paid",
                    "idempotent": True, "message": "订单已支付"}

        # 校验金额（防篡改）：以服务端订单金额为准
        if amount != order["total_amount"]:
            raise BizError(4002, f"支付金额与订单不符：应付 {order['total_amount']} 分")

        user = conn.execute(
            "SELECT * FROM users WHERE id=?", (order["user_id"],)
        ).fetchone()
        if user["balance"] < amount:
            raise BizError(4001, "余额不足")

        # 扣余额（缺陷模式下会因为重复回调被重复扣）
        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE id=?",
            (amount, order["user_id"]),
        )
        conn.execute(
            """INSERT INTO payments(order_id,trade_no,amount,status,created_at)
               VALUES(?,?,?,'success',?)""",
            (order_id, trade_no, amount, now()),
        )
        conn.execute(
            "UPDATE orders SET status='paid' WHERE id=?", (order_id,)
        )
        conn.execute(
            "INSERT INTO order_logs(order_id,action,detail,created_at) VALUES(?,?,?,?)",
            (order_id, "pay", f"支付 {amount} 分 trade_no={trade_no}", now()),
        )

    return {"order_id": order_id, "status": "paid", "idempotent": False}


def get_balance(user_id: int) -> dict:
    with tx() as conn:
        row = conn.execute(
            "SELECT id, username, nickname, balance FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
    if row is None:
        raise BizError(1002, "用户不存在")
    return dict(row)


# ---------------------------------------------------------------- 测试辅助

def test_reset() -> dict:
    """把数据库恢复成初始状态。练习时反复调用，保证用例可重复执行。"""
    from .db import init_db, DB_PATH
    import os

    if DB_PATH.exists():
        # 关闭 WAL 相关文件后删除
        for suffix in ("", "-wal", "-shm"):
            p = DB_PATH.with_name(DB_PATH.name + suffix)
            if p.exists():
                try:
                    os.remove(p)
                except PermissionError:
                    pass
    init_db()
    return {"ok": True, "message": "数据已重置为初始状态"}


def test_state() -> dict:
    """查看当前数据状态，方便断言和排查。"""
    with tx() as conn:
        users = [dict(r) for r in conn.execute(
            "SELECT id,username,nickname,is_member,balance FROM users").fetchall()]
        products = [dict(r) for r in conn.execute(
            "SELECT id,name,price,stock,status FROM products").fetchall()]
        orders = [dict(r) for r in conn.execute(
            "SELECT id,order_no,user_id,total_amount,status FROM orders").fetchall()]
        payments = [dict(r) for r in conn.execute(
            "SELECT id,order_id,trade_no,amount,status FROM payments").fetchall()]
    return {"users": users, "products": products, "orders": orders,
            "payments": payments}

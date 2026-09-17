# -*- coding: utf-8 -*-
"""
数据库层：SQLite + 显式事务。

为什么用 SQLite 而不是 MySQL/Redis：
- 零依赖，一个文件就是数据库，`rm lab.db` 即可重置
- 单文件可随仓库分发，clone 下来即可运行
- 支持事务，能做「库存扣减」「并发」这类真实业务校验

金额一律用「分」为单位的整数存储，避免浮点误差。
（但缺陷模式里的 rounding 会故意引入浮点误差，用于练习发现精度缺陷）
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.getenv("LAB_DB", Path(__file__).parent.parent / "lab.db"))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL 模式：允许读写并发，更接近真实数据库行为，
    # 也让「并发下单」这类场景能被真实复现
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx():
    """显式事务。正常结束提交，异常回滚。"""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password      TEXT NOT NULL,
    nickname      TEXT NOT NULL,
    is_member     INTEGER NOT NULL DEFAULT 0,
    balance       INTEGER NOT NULL DEFAULT 0      -- 余额，单位分
);

CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    price         INTEGER NOT NULL,               -- 单价，单位分
    stock         INTEGER NOT NULL DEFAULT 0,     -- 库存
    status        TEXT NOT NULL DEFAULT 'on'      -- on / off
);

CREATE TABLE IF NOT EXISTS carts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    product_id    INTEGER NOT NULL REFERENCES products(id),
    quantity      INTEGER NOT NULL,
    UNIQUE(user_id, product_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no      TEXT UNIQUE NOT NULL,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    total_amount  INTEGER NOT NULL,               -- 应付总额，单位分
    discount      INTEGER NOT NULL DEFAULT 0,     -- 优惠金额，单位分
    status        TEXT NOT NULL DEFAULT 'pending',-- pending/paid/cancelled
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL REFERENCES orders(id),
    product_id    INTEGER NOT NULL REFERENCES products(id),
    quantity      INTEGER NOT NULL,
    price         INTEGER NOT NULL                -- 下单时单价快照
);

CREATE TABLE IF NOT EXISTS payments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL REFERENCES orders(id),
    trade_no      TEXT NOT NULL,                  -- 支付流水号（业务幂等键）
    amount        INTEGER NOT NULL,
    status        TEXT NOT NULL,                  -- success
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL,
    action        TEXT NOT NULL,
    detail        TEXT,
    created_at    TEXT NOT NULL
);
"""


def init_db(seed: bool = True) -> None:
    """建表 + 灌入种子数据。已存在则跳过（要重置就删掉 lab.db）。"""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        cur = conn.execute("SELECT COUNT(*) AS c FROM users")
        if cur.fetchone()["c"] == 0 and seed:
            seed_data(conn)
    finally:
        conn.close()


def seed_data(conn: sqlite3.Connection) -> None:
    """种子数据：2 个用户、5 个商品。密码明文存储仅为演示，真实项目必须哈希。"""
    from datetime import datetime, timezone

    users = [
        # username, password, nickname, is_member, balance(分)
        # 余额故意给足（5000 元 / 2000 元）：
        # 否则「重复回调重复扣款」这个缺陷会因为余额不足而变成「扣款失败」，
        # 掩盖掉真正的症状（重复扣款）。测试数据设计会影响缺陷能否被观察到。
        ("alice", "alice123", "爱丽丝", 1, 500000),   # 会员，5000 元
        ("bob", "bob123", "鲍勃", 0, 200000),         # 普通用户，2000 元
    ]
    conn.executemany(
        "INSERT INTO users(username,password,nickname,is_member,balance) VALUES(?,?,?,?,?)",
        users,
    )

    products = [
        # name, price(分), stock
        # 注意：价格故意不是整百分（末两位非 00）。
        # 真实商品定价就是这样（39.99 / 12.99 / 129.99…），
        # 这也让 rounding 缺陷（浮点折扣误差）能被真实复现。
        ("机械键盘", 39999, 100),
        ("无线鼠标", 12999, 200),
        ("显示器", 129999, 30),
        ("USB 数据线", 1899, 500),
        ("笔记本支架", 8999, 50),
    ]
    conn.executemany(
        "INSERT INTO products(name,price,stock) VALUES(?,?,?)", products
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO order_logs(order_id,action,detail,created_at) VALUES(0,'seed','初始化种子数据',?)",
        (now,),
    )


if __name__ == "__main__":
    init_db()
    print(f"数据库已初始化：{DB_PATH}")

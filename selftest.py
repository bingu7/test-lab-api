# -*- coding: utf-8 -*-
"""
Mock 服务自检脚本：验证干净模式行为正确、缺陷模式能复现缺陷。

用法：
    python selftest.py              # 自检干净模式
    python selftest.py --flaws      # 自检缺陷模式（需另起一个带 LAB_FLAWS=all 的实例）
"""
import sys
import threading

import requests

BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8099"
passed = 0
failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {extra}")


def api(method, path, token=None, **kw):
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.request(method, BASE + path, headers=headers, timeout=30, **kw)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:200]}


def dget(j, *keys):
    """安全地从响应里取 data 下的字段，任意一层缺失都返回 None。"""
    cur = j
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def login(u="alice", p="alice123"):
    st, j = api("POST", "/api/login", json={"username": u, "password": p})
    return j["data"]["token"] if j.get("code") == 0 else None


def reset():
    api("POST", "/lab/reset")


print(f"=== 自检目标: {BASE} ===")
st, j = api("GET", "/lab/info")
flaws = j["data"]["active_flaws"]
mode = "缺陷模式" if flaws else "干净模式"
print(f"当前模式: {mode}  启用缺陷={flaws}\n")

# ---------------------------------------------------------------- 基础接口

print("[1] 健康检查与登录")
st, j = api("GET", "/health")
check("健康检查返回 code=0", j.get("code") == 0)

st, j = api("POST", "/api/login", json={"username": "alice", "password": "wrong"})
check("密码错误返回业务码 1002", j.get("code") == 1002, f"实际 {j.get('code')}")

st, j = api("POST", "/api/login", json={"username": "alice", "password": "alice123"})
check("正确密码登录成功并返回 token", j.get("code") == 0 and "token" in j.get("data", {}))

TOKEN = login()
BOB = login("bob", "bob123")

st, j = api("GET", "/api/me")
check("无 Token 访问受保护接口返回 401", st == 401, f"实际 {st}")

st, j = api("GET", "/api/me", token="bad.token.here")
check("伪造 Token 被拒绝", st == 401, f"实际 {st}")

# ---------------------------------------------------------------- 商品与分页

print("\n[2] 商品列表与分页")
st, j = api("GET", "/api/products?page=1&size=2")
d = j["data"]
check("分页返回 size 条", len(d["items"]) == 2, f"实际 {len(d['items'])}")
check("total 正确（5 个商品）", d["total"] == 5, f"实际 {d['total']}")

st, j = api("GET", "/api/products?page=3&size=2")
check("第 3 页只返回 1 条（5 条数据）", len(j["data"]["items"]) == 1,
      f"实际 {len(j['data']['items'])}")

st, j = api("GET", "/api/products?keyword=键盘")
check("关键词搜索命中 1 条", len(j["data"]["items"]) == 1,
      f"实际 {len(j['data']['items'])}")

st, j = api("GET", "/api/products/99999")
check("不存在的商品返回 404", st == 404, f"实际 {st}")

# ---------------------------------------------------------------- 会员折扣与运费

print("\n[3] 金额计算（会员折扣 + 运费）")
reset()
TOKEN = login()  # alice 是会员
# 机械键盘 39999 分。会员 85 折：39999*85//100 = 33999，折扣 6000
st, j = api("POST", "/api/orders", token=TOKEN, json={"product_id": 1, "quantity": 1})
d = j.get("data", {})
if d:
    subtotal = 39999
    expect_discount = subtotal - (subtotal * 85) // 100   # 6000
    expect_payable = subtotal - expect_discount           # 33999
    if flaws:
        flaw_discount = int(subtotal * (1 - 0.85))        # 5999
        check(f"【缺陷 rounding】折扣少算 1 分（缺陷={flaw_discount} 正确={expect_discount}）",
              d["discount"] == flaw_discount,
              f"实际 {d['discount']}")
        check(f"【缺陷 rounding】应付金额多算 1 分（应为 {subtotal - flaw_discount}）",
              d["total_amount"] == subtotal - flaw_discount,
              f"实际 {d['total_amount']}")
    else:
        check(f"会员折扣正确（应为 {expect_discount} 分）",
              d["discount"] == expect_discount, f"实际 {d.get('discount')}")
        check(f"应付金额正确（应为 {expect_payable} 分，含免运费）",
              d["total_amount"] == expect_payable, f"实际 {d.get('total_amount')}")

# 小额订单验证运费（USB 数据线 1899 分，非会员）
st, j = api("POST", "/api/orders", token=BOB, json={"product_id": 4, "quantity": 1})
d = j.get("data", {})
if d:
    expect = 1899 + 800  # 未满 99 元，加 8 元运费
    check(f"未满包邮门槛加 8 元运费（应为 {expect} 分）",
          d["total_amount"] == expect, f"实际 {d.get('total_amount')}")

# ---------------------------------------------------------------- 库存

print("\n[4] 库存扣减与超卖")
reset()
TOKEN = login()
st, j = api("GET", "/api/products/3")
before = j["data"]["stock"]
st, j = api("POST", "/api/orders", token=TOKEN, json={"product_id": 3, "quantity": 2})
check("下单成功", j.get("code") == 0, f"code={j.get('code')}")
st, j = api("GET", "/api/products/3")
after = j["data"]["stock"]
check(f"库存从 {before} 扣减 2 到 {before-2}", after == before - 2, f"实际 {after}")

st, j = api("POST", "/api/orders", token=TOKEN, json={"product_id": 3, "quantity": 99999})
check("库存不足被拒绝（业务码 3001）", j.get("code") == 3001, f"code={j.get('code')}")

# 超卖验证：并发下单
print("\n[5] 并发下单（超卖检测）")
reset()
import concurrent.futures

st, j = api("GET", "/api/products/3")
stock0 = j["data"]["stock"]  # 30
qty = 3
concurrency = 20  # 20 * 3 = 60 > 30，若无并发控制会超卖

tokens = [login() for _ in range(concurrency)]


def buy(idx):
    return api("POST", "/api/orders", token=tokens[idx % len(tokens)],
               json={"product_id": 3, "quantity": qty})


with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
    results = list(ex.map(buy, range(concurrency)))

ok_count = sum(1 for _, j in results if j.get("code") == 0)
st, j = api("GET", "/api/products/3")
final_stock = j["data"]["stock"]
sold = stock0 - final_stock

print(f"       初始库存={stock0} 成功下单={ok_count} 件数={ok_count*qty} "
      f"剩余库存={final_stock} 实际售出={sold}")

if flaws:
    # 缺陷模式：竞态窗口让多个请求都通过了库存校验
    check("【缺陷 oversell】库存出现负数（超卖）", final_stock < 0,
          f"剩余={final_stock}")
    check("【缺陷 oversell】售出件数超过初始库存", sold > stock0,
          f"售出 {sold} > 库存 {stock0}")
    check("【缺陷 oversell】成功下单数 * 件数 > 初始库存",
          ok_count * qty > stock0, f"{ok_count}*{qty}={ok_count*qty} vs {stock0}")
else:
    check("干净模式无超卖（剩余库存 >= 0）", final_stock >= 0, f"剩余={final_stock}")
    check("售出数量不超过初始库存", sold <= stock0, f"售出 {sold} / 库存 {stock0}")
    check("成功下单数 * 件数 <= 初始库存",
          ok_count * qty <= stock0, f"{ok_count}*{qty}={ok_count*qty} vs {stock0}")

# ---------------------------------------------------------------- 幂等

print("\n[6] 支付回调幂等性")
reset()
TOKEN = login()
st, j = api("POST", "/api/orders", token=TOKEN, json={"product_id": 1, "quantity": 1})
order_id = j["data"]["id"]
total = j["data"]["total_amount"]
st, j = api("GET", "/api/me", token=TOKEN)
balance_before = j["data"]["balance"]

trade_no = "TRADE-FIXED-0001"
payload = {"order_id": order_id, "trade_no": trade_no, "amount": total}

st1, j1 = api("POST", "/api/pay/callback", token=TOKEN, json=payload)
st2, j2 = api("POST", "/api/pay/callback", token=TOKEN, json=payload)
st3, j3 = api("POST", "/api/pay/callback", token=TOKEN, json=payload)

st, j = api("GET", "/api/me", token=TOKEN)
balance_after = j["data"]["balance"]
deducted = balance_before - balance_after

print(f"       首次回调 code={j1.get('code')} idempotent={dget(j1,'data','idempotent')}")
print(f"       二次回调 code={j2.get('code')} idempotent={dget(j2,'data','idempotent')}")
print(f"       三次回调 code={j3.get('code')} idempotent={dget(j3,'data','idempotent')}")
print(f"       余额 {balance_before} -> {balance_after}，共扣 {deducted} 分（应付 {total}）")

st, j = api("GET", "/lab/state")
pay_count = len([p for p in j["data"]["payments"] if p["trade_no"] == trade_no])
print(f"       payments 表中该 trade_no 记录数={pay_count}")

if flaws:
    check("【缺陷 idempotent】重复回调重复扣款", deducted == total * 3,
          f"扣款 {deducted}，期望 {total*3}")
    check("【缺陷 idempotent】产生多条支付记录", pay_count == 3, f"实际 {pay_count} 条")
else:
    check("幂等：只扣款一次", deducted == total, f"扣款 {deducted}，期望 {total}")
    check("幂等：只有 1 条支付记录", pay_count == 1, f"实际 {pay_count} 条")
    check("二次回调被识别为幂等", dget(j2, "data", "idempotent") is True)

# ---------------------------------------------------------------- 越权

print("\n[7] 越权访问")
reset()
TOKEN_A = login("alice", "alice123")
TOKEN_B = login("bob", "bob123")
st, j = api("POST", "/api/orders", token=TOKEN_A, json={"product_id": 1, "quantity": 1})
order_a = j["data"]["id"]

st, j = api("GET", f"/api/orders/{order_a}", token=TOKEN_B)
if flaws:
    check("【缺陷 authz】bob 能查到 alice 的订单", st == 200 and j.get("code") == 0,
          f"status={st} code={j.get('code')}")
else:
    check("bob 查 alice 的订单被拒绝（404）", st == 404, f"status={st}")

# ---------------------------------------------------------------- 购物车数量

print("\n[8] 购物车数量校验")
reset()
TOKEN = login()
st, j = api("POST", "/api/cart", token=TOKEN, json={"product_id": 1, "quantity": 0})
if flaws:
    check("【缺陷 cartqty】数量 0 被接受", j.get("code") == 0, f"code={j.get('code')}")
else:
    check("数量 0 被拒绝（业务码 2002）", j.get("code") == 2002, f"code={j.get('code')}")

st, j = api("POST", "/api/cart", token=TOKEN, json={"product_id": 1, "quantity": -5})
if flaws:
    check("【缺陷 cartqty】负数被接受", j.get("code") == 0, f"code={j.get('code')}")
else:
    check("负数被拒绝", j.get("code") == 2002, f"code={j.get('code')}")

# ---------------------------------------------------------------- 汇总

print(f"\n{'='*50}")
print(f"通过 {passed} / 失败 {failed}   （模式：{mode}）")
sys.exit(1 if failed else 0)

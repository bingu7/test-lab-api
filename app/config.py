# -*- coding: utf-8 -*-
"""
缺陷开关配置。

设计意图：同一个服务，通过环境变量在「干净模式」和「缺陷模式」之间切换。

- 干净模式（默认）：接口行为正确，适合练自动化框架、数据驱动、CI 集成。
- 缺陷模式（LAB_FLAWS=all）：故意植入 5 个真实世界常见缺陷，
  适合练「缺陷发现」——这才是测试的核心能力。

用法：
    LAB_FLAWS=all uvicorn app.main:app          # 全部缺陷打开
    LAB_FLAWS=oversell,idempotent ...           # 只打开指定缺陷
    （不设 LAB_FLAWS）                           # 干净模式
"""
import os

# 所有可用的缺陷名
ALL_FLAWS = {
    "oversell",     # 并发下单导致库存超卖（检查后再扣减，中间有竞态窗口）
    "idempotent",   # 支付回调不幂等（重复通知导致重复扣款/状态错乱）
    "authz",        # 越权访问（订单详情不校验归属，可看别人的订单）
    "rounding",     # 折扣金额计算精度错误（float 计算导致的 1 分误差）
    "cartqty",      # 购物车数量校验缺失（可传 0 或负数）
}


def active_flaws() -> set:
    """读取当前启用的缺陷集合。每次请求时调用，支持运行中改环境变量。"""
    raw = (os.getenv("LAB_FLAWS") or "").strip().lower()
    if not raw:
        return set()
    if raw == "all":
        return set(ALL_FLAWS)
    return {f.strip() for f in raw.split(",") if f.strip() in ALL_FLAWS}


def has(flaw: str) -> bool:
    """判断某个缺陷是否启用。"""
    return flaw in active_flaws()


# 业务参数（故意不做成配置，方便阅读）
DISCOUNT_RATE = 0.85          # 会员 85 折
FREE_SHIPPING_THRESHOLD = 9900  # 满 99 元包邮（单位：分）
SHIPPING_FEE = 800            # 运费 8 元（单位：分）

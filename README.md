# test-lab-api

一个**零依赖**的极简电商业务 API，专为软件测试练习设计。

零依赖是指：不需要 MySQL、Redis、消息队列、注册中心。一个 Python 进程 + 一个 SQLite 文件就够。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 为什么需要它

公开靶场（如 restful-booker、petstore）能练接口基础，但它们有个共同硬伤：**没有业务状态机**。你没法在它们上面测这些：

- 下单 → 扣库存 → 支付 → 回调 → 幂等校验
- 优惠券叠加 → 订单金额 → 对账
- 并发下单导致库存超卖
- 数据库中的最终一致性

这个服务补齐的就是这一块。

---

## 核心特性：可控缺陷

同一个服务，通过环境变量在两种模式间切换：

| 模式 | 启动方式 | 用途 |
|------|----------|------|
| **干净模式** | 不设 `LAB_FLAWS` | 接口行为完全正确。适合练自动化框架、数据驱动、CI 集成 |
| **缺陷模式** | `LAB_FLAWS=all` | 故意植入 5 个真实世界缺陷。适合练**缺陷发现**——这才是测试的核心能力 |

缺陷模式下，接口依然按正常流程返回成功，**缺陷藏在业务逻辑里**。你需要靠测试设计把它们找出来，而不是看代码。

仓库自带的 `examples/` 用例套件就是最好的演示：

```text
干净模式：25 passed
缺陷模式：11 failed / 14 passed   ← 同一套用例，抓出 5 类缺陷
```

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动（干净模式，自动创建 SQLite 数据库）
python -m uvicorn app.main:app --port 8099
```

打开交互式文档：**http://127.0.0.1:8099/docs**

```bash
# 3. 跑自检，确认服务正常
python selftest.py

# 4. 跑示例自动化用例
pip install pytest
pytest examples/ -v
```

切换缺陷模式：

```bash
# Linux / macOS
LAB_FLAWS=all python -m uvicorn app.main:app --port 8099

# Windows PowerShell
$env:LAB_FLAWS="all"; python -m uvicorn app.main:app --port 8099

# 也可以只打开指定缺陷
LAB_FLAWS=oversell,idempotent python -m uvicorn app.main:app --port 8099
```

---

## 五个可练习的缺陷

**先不要看下面这张表。** 建议流程是：用缺陷模式启动服务，自己设计用例去测，记录可疑现象，然后再对照此表。

| 缺陷名 | 症状 | 需要用到的测试技能 |
|--------|------|--------------------|
| `oversell` | 并发下单时库存超卖，库存变负数 | 并发测试、数据一致性校验 |
| `idempotent` | 支付回调重复推送时重复扣款 | 幂等测试、重复请求构造 |
| `authz` | 能查看其他用户的订单 | 越权测试（水平越权） |
| `rounding` | 会员折扣金额少算 1 分，应付金额多算 1 分 | 边界值、精度校验、金额对账 |
| `cartqty` | 购物车能加入数量 0 或负数 | 参数校验、边界值 |

### 缺陷位置与根因

> **想自己找就先别往下读。** 建议用缺陷模式启动服务，自己设计用例测一遍，记录可疑现象，再回来对照。

<details>
<summary><b>点击展开：5 个缺陷的位置与根因（找到后再看）</b></summary>

<br>

**`oversell`** —— `app/services.py` 的 `create_order()`

把「读库存并校验」和「扣减库存」拆成了两个独立事务，中间还有 50ms 的业务处理延迟（模拟真实的查询、风控、组包耗时）。多个并发请求会同时读到"库存充足"，然后各自扣减。

*为什么这样写才真实*：如果把「读+校验+扣减」放进同一个 `BEGIN IMMEDIATE` 事务，SQLite 的写锁会把并发串行化，反而不会超卖。真实系统的超卖几乎都来自"校验与写入不在同一个原子操作里"（先查缓存、先查从库、先校验后写）。

修复方向：`UPDATE ... WHERE stock >= ?` 用条件更新交给数据库保证原子性。

**`idempotent`** —— `app/services.py` 的 `pay_order()`

不检查 `trade_no` 是否已处理，重复回调会被当成新支付再次处理。

*为什么能复现*：`payments.trade_no` 上原本有 `UNIQUE` 约束，这个约束本身就是正确的防护——所以实现缺陷时特意去掉了它，否则第二次回调会撞唯一索引报错，症状就变成"500"而不是"重复扣款"了。

修复方向：以 `trade_no` 为幂等键，已处理则直接返回成功。

**`authz`** —— `app/services.py` 的 `get_order()`

查询订单时只按 `id` 过滤，没有校验 `user_id` 归属。

修复方向：`WHERE id=? AND user_id=?`。

**`rounding`** —— `app/services.py` 的 `calc_amount()`

折扣用浮点计算：`int(subtotal * (1 - 0.85))`。`0.85` 无法用二进制精确表示，导致部分金额少算 1 分。

*触发条件*：只要商品单价不是整百分（末两位非 `00`）就会触发。种子商品价格特意设成 `39.99`、`12.99`、`129.99` 这类真实定价。

修复方向：整数运算 `subtotal - (subtotal * 85) // 100`。

**`cartqty`** —— `app/services.py` 的 `add_to_cart()`

没有做数量校验，`0` 和负数都能加入购物车。

修复方向：校验 `quantity > 0`（并按业务规则设上限）。

</details>

---

## 接口清单

统一响应结构（模仿真实企业项目）：

```json
{"code": 0, "message": "ok", "data": {...}}
```

> **注意**：业务失败也返回 HTTP 200，靠 `code` 区分。这是国内常见约定，也是接口测试必须注意的点——只断言 HTTP 200 是不够的。

| 方法 | 路径 | 说明 | 需鉴权 |
|------|------|------|:------:|
| GET | `/health` | 健康检查 | |
| POST | `/api/login` | 登录，返回 Token | |
| GET | `/api/me` | 当前用户信息（含余额） | ✅ |
| GET | `/api/products` | 商品列表（`keyword` / `page` / `size`） | |
| GET | `/api/products/{id}` | 商品详情 | |
| POST | `/api/cart` | 加入购物车 | ✅ |
| GET | `/api/cart` | 查看购物车 | ✅ |
| POST | `/api/orders` | 创建订单（扣库存） | ✅ |
| GET | `/api/orders` | 我的订单列表 | ✅ |
| GET | `/api/orders/{id}` | 订单详情 | ✅ |
| POST | `/api/pay/callback` | 支付回调（可重复调用） | ✅ |

### 测试辅助接口（练习用）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/lab/reset` | 重置数据到初始状态（**用例前后调用，保证可重复运行**） |
| GET | `/lab/state` | 查看当前所有数据（用户/商品/订单/支付） |
| GET | `/lab/info` | 查看当前启用了哪些缺陷 |

### 测试账号

| 用户名 | 密码 | 身份 | 初始余额 |
|--------|------|------|----------|
| `alice` | `alice123` | 会员（85 折） | 5000 元 |
| `bob` | `bob123` | 普通用户 | 2000 元 |

---

## 业务规则

```text
金额单位：分（整数存储，避免浮点误差）

商品小计 = Σ(单价 × 数量)
会员折扣 = 小计 × 15%（85 折）
应付金额 = 小计 - 折扣 + 运费
运费     = 折后满 99 元免运费，否则 8 元

下单：校验库存 → 扣减库存 → 创建订单（状态 pending）
支付：校验金额 → 扣余额 → 记 payments → 订单状态改 paid
```

---

## 用干净模式练自动化

`examples/` 下有一个可直接运行的 Pytest 示例，演示了分层框架的做法：

| 文件 | 作用 |
|------|------|
| `examples/api_client.py` | 统一请求封装（日志、断言、鉴权注入）——**基础层** |
| `examples/conftest.py` | 会话级 Token 复用、用例级数据重置 fixture |
| `examples/test_order_flow.py` | 下单支付完整链路的自动化用例——**用例层** |

它包含 7 组用例：

```text
TestAuth             登录与鉴权（5 条）
TestProduct          分页与搜索（4 条）
TestAmount           金额计算与精度（4 条）
TestStock            库存扣减、库存不足、并发不超卖（3 条）
TestPayment          支付、金额篡改、回调幂等（3 条）
TestAuthorization    水平越权（1 条）
TestCartValidation   购物车参数校验（5 条）
```

这个示例刻意写得「像真实项目」，可以直接作为你练习框架设计的起点。

---

## 目录结构

```text
test-lab-api/
├── app/
│   ├── config.py       # 缺陷开关
│   ├── db.py           # SQLite + 事务 + 种子数据
│   ├── auth.py         # Token 签发与校验
│   ├── services.py     # 业务逻辑（缺陷藏在这里）
│   └── main.py         # HTTP 接口层
├── examples/           # Pytest 自动化示例
├── selftest.py         # 服务自检（验证两种模式）
├── requirements.txt
├── LICENSE
└── README.md
```

---

## 练完之后

这个服务的价值不只是练手——**它可以变成你的面试作品**。

在公司做的东西涉及保密，你没法展示代码和报告。但基于这个服务写的自动化框架，你可以：

- 推到自己的 GitHub 仓库（就是你正在看的这个）
- 把 Allure 报告截图放进 README
- 面试时说："我用自己的练习项目搭过一套接口自动化框架，被测对象是我自己写的 Mock 服务，代码在 GitHub 上，您可以直接看"

面试官对「自学项目」是认可的，对「编造的公司项目」是零容忍的。明确说清这是你自己的练习，然后展示技术细节——这比含糊地说"我在公司做过"要可信得多。

---

## 配套学习资料

本服务是下面这套教程的配套练习环境（在线站点）：

- [练习靶场与工具清单](https://bingu7.github.io/test-tutorials/项目实战/练习靶场与工具清单/) — 公开靶场对比与选择
- [接口测试方法论](https://bingu7.github.io/test-tutorials/专项测试/接口测试完整教程-软件测试版/) — 用例设计与断言策略
- [测试框架设计与设计模式](https://bingu7.github.io/test-tutorials/自动化测试/测试框架设计与设计模式教程/) — 分层架构，`examples/` 就是它的实现
- [简历撰写与项目包装](https://bingu7.github.io/test-tutorials/面试专题/简历撰写与项目包装/) — 把练习项目写成面试素材

---

## 说明

- **这是教学演示项目**，不是生产代码。密码明文存储、Token 用简化签名，真实项目必须用密码哈希（bcrypt/argon2）和标准 JWT。
- `lab.db` 已被 `.gitignore` 忽略。想重置数据库，直接删掉这个文件即可（或调用 `/lab/reset`）。
- 服务只监听本地端口即可，**不要部署到公网**。

## License

[MIT](LICENSE)

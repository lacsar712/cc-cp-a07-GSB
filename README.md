# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

**条数配额**：每个自然日（按 Asia/Shanghai 切日）可交条数有配额。记录员在顶栏「条数配额」落地页设置本日可交条数；每笔成功入队扣一额，超额拒交并写明今日已交几条、还剩几条。改配额只影响之后提交；切日自动清零并记流水；入队与扣额在同一事务内完成，多人抢最后一额只有一人成功。观察账号可翻已交与流水，不能改配额。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Preact + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3197 |
| 接口 | http://localhost:8197 |
| PostgreSQL | localhost:54397（库名 `coldchain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| logger | log123456 | 记录员，可提交读数、改配额 |
| logger2 | log123456 | 记录员，可提交读数、改配额 |
| watcher | watch123456 | 值班员，只读列表/配额/流水 |

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 条数配额接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/quota` | 查本日配额、今日已交、还剩（登录即可） |
| PUT | `/api/quota` | 设本日可交条数 `{"daily_quota": N}`（仅记录员） |
| GET | `/api/quota/ledger` | 清零与改配额流水（登录即可） |
| POST | `/api/readings` | 入队与扣额同事务；超额返回 **409** 并写明今日已交/还剩 |

- 配额状态为单行 `quota_state`，事务内 `FOR UPDATE` 行锁保证并发扣额只成一单。
- 切日采用懒清零：任意配额相关请求发现日期变了，就清零已交、推进日期并写一条 `reset` 流水。
- 默认配额 10 条，可用环境变量 `DEFAULT_DAILY_QUOTA`、`QUOTA_TZ` 调整。

## 验收流程

```bash
# 登录记录员，配额设为 1
TOKEN=$(curl -s -X POST localhost:8197/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"logger","password":"log123456"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -X PUT localhost:8197/api/quota -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"daily_quota":1}'

# 第一笔：201 入队候审（status=pending）
curl -X POST localhost:8197/api/readings -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"probe_id":"探头C03","temp_c":5.5}'

# 第二笔：409 被挡 → {"detail":"超出本日条数配额：今日已交 1 条，还剩 0 条",...}
curl -X POST localhost:8197/api/readings -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"probe_id":"探头D04","temp_c":6.0}'
```

## 种子数据

| 探头 | 温度 | 结论 |
|------|------|------|
| 探头A01 | 4.2℃ | 合格 |
| 探头B02 | 12.5℃ | 超温 |

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。

规则自测（无需数据库）：`cd backend && python3 test_rules.py`

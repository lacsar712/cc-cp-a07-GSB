# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

每日上报有**条数配额**：记录员在「条数配额」落地页设置本日可交条数，每笔成功入队在同一事务内扣一额；超额拒交并写明今日已交/还剩条数。配额调整只影响之后的提交，次日（UTC+8）零点首次活动时自动清零、承接上日配额并记一条清零流水。

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
| logger | log123456 | 记录员，可提交读数、可改配额 |
| logger2 | log2123456 | 记录员（第二名写权限人员，用于并发抢额） |
| watcher | watch123456 | 值班员，只读列表/配额/流水，不能改配额 |

## 条数配额

- `GET /api/quota`：本日配额、今日已交、剩余（登录可读；未设额时不限）。
- `PUT /api/quota` `{"quota_limit": 1}`：设置本日可交条数，仅记录员；不改动已交条数，只影响之后提交。
- `GET /api/quota/ledger`：切日清零流水（登录可读）。
- `POST /api/readings`：入队与扣额在同一数据库事务内完成，当日配额行先 `pg_advisory_xact_lock` 再 `FOR UPDATE`，两名写权限人员抢最后一额只有一笔成功；超额返回 **400**，`detail` 形如「今日条数配额已用尽，拒交：今日已交 1 条，配额 1 条，还剩 0 条」。
- 切日（UTC+8）后首次提交或设额时惰性建立新日行：已交清零、承接上日配额，并向 `quota_ledger` 写一条 `reset` 流水。

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

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

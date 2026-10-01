import json
import os
from datetime import date, datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import (
    DEFAULT_DAILY_QUOTA,
    QUOTA_SEED_SQL,
    QUOTA_TZ,
    create_pool,
    ensure_schema_async,
    seed_if_empty,
)
from rules import judge_temp, quota_decision

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "logger2": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"detail": "未登录"}, ensure_ascii=False), content_type="application/json")
    return user


def require_writer(request: web.Request, detail: str = "仅记录员可提交读数") -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": detail}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


async def _lock_quota_state(conn) -> tuple[dict, date]:
    """在事务内锁定配额单行（FOR UPDATE），切日时自动清零并记流水。

    入队扣额、查配额、改配额都先经过这里，行锁保证两名记录员
    抢最后一额时只有一人的事务能先完成扣额。
    """
    row = await conn.fetchrow(
        "SELECT id, day, daily_quota, used FROM quota_state WHERE id = 1 FOR UPDATE"
    )
    if row is None:
        await conn.execute(QUOTA_SEED_SQL, QUOTA_TZ, DEFAULT_DAILY_QUOTA)
        row = await conn.fetchrow(
            "SELECT id, day, daily_quota, used FROM quota_state WHERE id = 1 FOR UPDATE"
        )
    today = await conn.fetchval("SELECT (now() AT TIME ZONE $1)::date", QUOTA_TZ)
    if row["day"] < today:
        detail = f"{row['day'].isoformat()} 已交 {row['used']} 条，切日自动清零"
        await conn.execute(
            "UPDATE quota_state SET day = $1, used = 0, updated_at = now() WHERE id = 1",
            today,
        )
        await conn.execute(
            "INSERT INTO quota_ledger (event, day, detail, actor) VALUES ('reset', $1, $2, $3)",
            today,
            detail,
            "system",
        )
        row = await conn.fetchrow(
            "SELECT id, day, daily_quota, used FROM quota_state WHERE id = 1"
        )
    return row, today


def _quota_payload(row: dict, today) -> dict:
    return {
        "day": today.isoformat(),
        "daily_quota": row["daily_quota"],
        "used": row["used"],
        "remaining": max(0, row["daily_quota"] - row["used"]),
        "tz": QUOTA_TZ,
    }


async def get_quota(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row, today = await _lock_quota_state(conn)
    return web.json_response(_quota_payload(row, today))


async def put_quota(request: web.Request) -> web.Response:
    user = require_writer(request, "观察账号不能改配额，仅记录员可设置")
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    raw = body.get("daily_quota")
    if isinstance(raw, bool):
        quota = None
    elif isinstance(raw, int):
        quota = raw
    elif isinstance(raw, str) and raw.strip().lstrip("+").isdigit():
        quota = int(raw.strip())
    else:
        quota = None
    if quota is None or not 0 <= quota <= 100000:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "配额必须是 0~100000 的整数"}, ensure_ascii=False),
            content_type="application/json",
        )

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row, today = await _lock_quota_state(conn)
            old = row["daily_quota"]
            await conn.execute(
                "UPDATE quota_state SET daily_quota = $1, updated_by = $2, updated_at = now() WHERE id = 1",
                quota,
                user["username"],
            )
            await conn.execute(
                "INSERT INTO quota_ledger (event, day, detail, actor) VALUES ('set_quota', $1, $2, $3)",
                today,
                f"本日可交条数由 {old} 改为 {quota}（只影响之后提交）",
                user["username"],
            )
            row = await conn.fetchrow(
                "SELECT id, day, daily_quota, used FROM quota_state WHERE id = 1"
            )
    return web.json_response(_quota_payload(row, today))


async def get_quota_ledger(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, event, day, detail, actor, created_at
        FROM quota_ledger
        ORDER BY id DESC
        LIMIT 100
        """
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "event": r["event"],
                "day": r["day"].isoformat() if r["day"] else None,
                "detail": r["detail"],
                "actor": r["actor"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
        )
    return web.json_response(out)


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, probe_id, temp_c, verdict, reason, status, created_by, created_at, processed_at
        FROM probe_readings
        ORDER BY id DESC
        """
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "probe_id": r["probe_id"],
                "temp_c": r["temp_c"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
            }
        )
    return web.json_response(out)


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "探头编号不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    pool: asyncpg.Pool = request.app["pool"]
    rejection = None
    row = None
    remaining_after = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            state, _today = await _lock_quota_state(conn)
            allowed, remaining, reason = quota_decision(state["daily_quota"], state["used"])
            if not allowed:
                # 事务照常提交（落切日清零流水），但本条不入队、不扣额。
                rejection = {
                    "detail": reason,
                    "used": state["used"],
                    "remaining": remaining,
                }
            else:
                await conn.execute(
                    "UPDATE quota_state SET used = used + 1, updated_at = now() WHERE id = 1"
                )
                row = await conn.fetchrow(
                    """
                    INSERT INTO probe_readings (probe_id, temp_c, status, created_by, created_at)
                    VALUES ($1, $2, 'pending', $3, now())
                    RETURNING id, probe_id, temp_c, verdict, reason, status, created_by, created_at, processed_at
                    """,
                    probe_id,
                    temp_c,
                    user["username"],
                )
                remaining_after = remaining - 1
    if rejection is not None:
        raise web.HTTPConflict(
            text=json.dumps(rejection, ensure_ascii=False),
            content_type="application/json",
        )
    return web.json_response(
        {
            "id": row["id"],
            "probe_id": row["probe_id"],
            "temp_c": row["temp_c"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "processed_at": None,
            "message": f"已入队候审，扣除 1 条配额，今日还剩 {remaining_after} 条",
        },
        status=201,
    )


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_get("/api/quota", get_quota)
    app.router.add_put("/api/quota", put_quota)
    app.router.add_get("/api/quota/ledger", get_quota_ledger)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)

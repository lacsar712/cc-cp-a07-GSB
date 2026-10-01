import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import judge_temp
from quota import (
    QuotaExceeded,
    ensure_today_row,
    get_effective_quota,
    quota_payload,
    today_cn,
)

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "logger2": {"role": "writer", "password_hash": pwd.hash("log2123456")},
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


def require_writer(request: web.Request, message: str = "仅记录员可提交读数") -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": message}, ensure_ascii=False),
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
    day = today_cn()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # 入队与扣额同一事务：当日行加行锁（并以咨询锁串行化建日），抢最后一额只许一成
                limit, used = await ensure_today_row(conn, day)
                if limit is not None and used >= limit:
                    raise QuotaExceeded(used=used, limit=limit)
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
                used_after = await conn.fetchval(
                    "UPDATE daily_quota SET used_count = used_count + 1, updated_at = now() WHERE day = $1 RETURNING used_count",
                    day,
                )
    except QuotaExceeded as exc:
        raise web.HTTPBadRequest(
            text=json.dumps(
                {
                    "detail": (
                        f"今日条数配额已用尽，拒交：今日已交 {exc.used} 条，"
                        f"配额 {exc.limit} 条，还剩 {exc.remaining} 条"
                    ),
                    "used_count": exc.used,
                    "quota_limit": exc.limit,
                    "remaining": exc.remaining,
                },
                ensure_ascii=False,
            ),
            content_type="application/json",
        ) from exc
    remaining = None if limit is None else max(0, limit - used_after)
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
            "message": "已入队，后台工人将认领并判定",
            "quota": quota_payload(day, limit, used_after),
            "remaining": remaining,
        },
        status=201,
    )


async def get_quota(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    day = today_cn()
    async with pool.acquire() as conn:
        limit, used = await get_effective_quota(conn, day)
    return web.json_response(quota_payload(day, limit, used))


async def set_quota(request: web.Request) -> web.Response:
    require_writer(request, "仅记录员可修改条数配额")
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    value = body.get("quota_limit")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "本日可交条数必须是不小于 0 的整数"}, ensure_ascii=False),
            content_type="application/json",
        )
    pool: asyncpg.Pool = request.app["pool"]
    day = today_cn()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 与提交共用同一把日锁：配额变更只影响之后提交，不改动已交条数
            await ensure_today_row(conn, day)
            row = await conn.fetchrow(
                """
                UPDATE daily_quota
                SET quota_limit = $2, updated_at = now()
                WHERE day = $1
                RETURNING quota_limit, used_count
                """,
                day,
                value,
            )
    return web.json_response(quota_payload(day, row["quota_limit"], row["used_count"]))


async def list_quota_ledger(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, kind, day, prev_day, prev_used, carried_limit, created_at
        FROM quota_ledger
        ORDER BY id DESC
        LIMIT 100
        """
    )
    return web.json_response(
        [
            {
                "id": r["id"],
                "kind": r["kind"],
                "day": r["day"].isoformat(),
                "prev_day": r["prev_day"].isoformat() if r["prev_day"] else None,
                "prev_used": r["prev_used"],
                "carried_limit": r["carried_limit"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
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
    app.router.add_put("/api/quota", set_quota)
    app.router.add_get("/api/quota/ledger", list_quota_ledger)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)

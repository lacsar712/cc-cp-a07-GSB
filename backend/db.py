import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

# 自然日按此时区切分；切日时已交条数自动清零并记流水。
QUOTA_TZ = os.environ.get("QUOTA_TZ", "Asia/Shanghai")
DEFAULT_DAILY_QUOTA = int(os.environ.get("DEFAULT_DAILY_QUOTA", "10"))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);

CREATE TABLE IF NOT EXISTS quota_state (
    id smallint PRIMARY KEY,
    day date NOT NULL,
    daily_quota integer NOT NULL,
    used integer NOT NULL DEFAULT 0,
    updated_by text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS quota_ledger (
    id serial PRIMARY KEY,
    event text NOT NULL,
    day date NOT NULL,
    detail text NOT NULL,
    actor text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_quota_ledger_id_desc ON quota_ledger (id DESC);
"""

# 单行配额状态（id=1），不存在时播种；已存在则保留现值。
QUOTA_SEED_SQL = """
INSERT INTO quota_state (id, day, daily_quota, used, updated_by)
VALUES (1, (now() AT TIME ZONE $1)::date, $2, 0, 'system')
ON CONFLICT (id) DO NOTHING
"""
QUOTA_SEED_SQL_SYNC = QUOTA_SEED_SQL.replace("$1", "%s").replace("$2", "%s")


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)
    conn.execute(QUOTA_SEED_SQL_SYNC, (QUOTA_TZ, DEFAULT_DAILY_QUOTA))


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
        await conn.execute(QUOTA_SEED_SQL, QUOTA_TZ, DEFAULT_DAILY_QUOTA)


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        samples = [
            ("探头A01", 4.2),
            ("探头B02", 12.5),
        ]
        for probe_id, temp_c in samples:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, $3, $4, 'done', 'logger', now())
                """,
                probe_id,
                temp_c,
                verdict,
                reason,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("探头A01", 4.2),
        ("探头B02", 12.5),
    ]
    for probe_id, temp_c in samples:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, 'done', 'logger', now())
            """,
            (probe_id, temp_c, verdict, reason),
        )
    conn.commit()

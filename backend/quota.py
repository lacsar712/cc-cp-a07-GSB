"""每日条数配额：按 UTC+8 自然日计额，入队与扣额同事务，切日惰性清零并记流水。"""

from datetime import date, datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))

# pg_advisory_xact_lock 的大整数键基址，加日期序号保证每日一把全局事务锁
_LOCK_BASE = 7_300_000


class QuotaExceeded(Exception):
    def __init__(self, used: int, limit: int):
        self.used = used
        self.limit = limit
        self.remaining = max(0, limit - used)
        super().__init__(
            f"今日条数配额已用尽：今日已交 {used} 条，配额 {limit} 条，还剩 {self.remaining} 条"
        )


def today_cn() -> date:
    return datetime.now(CN_TZ).date()


def _lock_key(day: date) -> int:
    return _LOCK_BASE + day.toordinal()


def quota_payload(day: date, limit: int | None, used: int) -> dict:
    remaining = None if limit is None else max(0, limit - used)
    return {
        "day": day.isoformat(),
        "quota_limit": limit,
        "used_count": used,
        "remaining": remaining,
    }


async def ensure_today_row(conn, day: date) -> tuple[int | None, int]:
    """在事务内调用：锁定当日配额行；若新一天尚无行则承接上日配额、清零并写 reset 流水。

    返回 (quota_limit, used_count)。
    """
    await conn.execute("SELECT pg_advisory_xact_lock($1)", _lock_key(day))
    row = await conn.fetchrow(
        "SELECT quota_limit, used_count FROM daily_quota WHERE day = $1 FOR UPDATE",
        day,
    )
    if row is not None:
        return row["quota_limit"], row["used_count"]

    # 当日行尚不存在：找上一日（或更早最近一日）承接配额
    prev = await conn.fetchrow(
        """
        SELECT day, quota_limit, used_count
        FROM daily_quota
        WHERE day < $1
        ORDER BY day DESC
        LIMIT 1
        """,
        day,
    )
    carried = prev["quota_limit"] if prev else None
    await conn.execute(
        "INSERT INTO daily_quota (day, quota_limit, used_count) VALUES ($1, $2, 0)",
        day,
        carried,
    )
    if prev is not None:
        await conn.execute(
            """
            INSERT INTO quota_ledger (kind, day, prev_day, prev_used, carried_limit)
            VALUES ('reset', $1, $2, $3, $4)
            """,
            day,
            prev["day"],
            prev["used_count"],
            carried,
        )
    return carried, 0


async def get_effective_quota(conn, day: date) -> tuple[int | None, int]:
    """只读视角：今日无行时展示将承接的配额，已交为 0。"""
    row = await conn.fetchrow(
        "SELECT quota_limit, used_count FROM daily_quota WHERE day = $1",
        day,
    )
    if row is not None:
        return row["quota_limit"], row["used_count"]
    prev = await conn.fetchrow(
        """
        SELECT quota_limit
        FROM daily_quota
        WHERE day < $1
        ORDER BY day DESC
        LIMIT 1
        """,
        day,
    )
    return (prev["quota_limit"] if prev else None), 0

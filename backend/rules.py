"""冷链探头读数判定：摄氏温度不超过 8 为合格，否则超温。"""


def judge_temp(temp_c: float) -> tuple[str, str]:
    if temp_c <= 8:
        return "合格", "探头温度未超过 8℃ 上限"
    return "超温", "探头温度超过 8℃ 冷链上限"


def quota_decision(daily_quota: int, used: int) -> tuple[bool, int, str]:
    """本日条数配额判定。

    返回 (是否允许入队, 提交前剩余条数, 拒绝原因)。
    超额拒交时，拒绝原因写明今日已交几条、还剩几条。
    """
    remaining = max(0, daily_quota - used)
    if used >= daily_quota:
        return False, remaining, f"超出本日条数配额：今日已交 {used} 条，还剩 {remaining} 条"
    return True, remaining, ""


def verdict_for_display(verdict: str | None, status: str) -> str:
    if verdict:
        return verdict
    if status == "pending":
        return "待处理"
    if status == "processing":
        return "处理中"
    return "—"

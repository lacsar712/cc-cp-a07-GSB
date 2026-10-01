"""配额与判定规则的纯函数自测：python3 test_rules.py"""

from rules import judge_temp, quota_decision


def test_judge_temp() -> None:
    assert judge_temp(8.0)[0] == "合格"
    assert judge_temp(4.2)[0] == "合格"
    assert judge_temp(8.1)[0] == "超温"
    assert judge_temp(12.5)[0] == "超温"


def test_quota_allows_when_remaining() -> None:
    allowed, remaining, reason = quota_decision(10, 3)
    assert allowed is True
    assert remaining == 7
    assert reason == ""


def test_quota_allows_last_slot() -> None:
    allowed, remaining, _ = quota_decision(1, 0)
    assert allowed is True
    assert remaining == 1


def test_quota_rejects_when_full_and_names_counts() -> None:
    # 配额 1 已交 1：第二笔应被挡回，并写明今日已交几条、还剩几条
    allowed, remaining, reason = quota_decision(1, 1)
    assert allowed is False
    assert remaining == 0
    assert "今日已交 1 条" in reason
    assert "还剩 0 条" in reason


def test_quota_rejects_when_used_exceeds_lowered_quota() -> None:
    # 改配额只影响之后提交：已交 3 条后配额改为 2，再交即被挡
    allowed, remaining, reason = quota_decision(2, 3)
    assert allowed is False
    assert remaining == 0
    assert "今日已交 3 条" in reason


def test_quota_zero_blocks_everything() -> None:
    allowed, remaining, _ = quota_decision(0, 0)
    assert allowed is False
    assert remaining == 0


if __name__ == "__main__":
    test_judge_temp()
    test_quota_allows_when_remaining()
    test_quota_allows_last_slot()
    test_quota_rejects_when_full_and_names_counts()
    test_quota_rejects_when_used_exceeds_lowered_quota()
    test_quota_zero_blocks_everything()
    print("rules tests OK")

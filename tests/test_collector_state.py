"""collector_state.py 동작 확인용 간단 테스트. pytest 없이
python -m tests.test_collector_state 로도 실행 가능."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import collector_state  # noqa: E402


def test_unseen_policy_is_new():
    state = {}
    assert collector_state.is_new(state, "온통청년", "P1")


def test_recorded_policy_is_not_new_next_time():
    state = {}
    collector_state.record(state, "온통청년", "P1", "조건텍스트", "eligible", "이유")
    assert not collector_state.is_new(state, "온통청년", "P1")


def test_cache_hit_when_condition_text_unchanged():
    state = {}
    collector_state.record(state, "온통청년", "P1", "같은 텍스트", "eligible", "이유")
    cached = collector_state.get_cached_verdict(state, "온통청년", "P1", "같은 텍스트")
    assert cached == ("eligible", "이유")


def test_cache_miss_when_condition_text_changed():
    state = {}
    collector_state.record(state, "온통청년", "P1", "옛날 텍스트", "eligible", "이유")
    cached = collector_state.get_cached_verdict(state, "온통청년", "P1", "새 텍스트")
    assert cached is None


def test_sources_are_independent():
    state = {}
    collector_state.record(state, "온통청년", "P1", "", "eligible", "")
    assert collector_state.is_new(state, "청년몽땅정보통", "P1")


def test_should_show_true_for_never_seen_policy():
    state = {}
    assert collector_state.should_show(state, "온통청년", "P1", "신청가능")


def test_should_show_false_when_status_unchanged():
    state = {}
    collector_state.record(state, "온통청년", "P1", "", "eligible", "", status="신청가능")
    assert not collector_state.should_show(state, "온통청년", "P1", "신청가능")


def test_should_show_true_when_status_improves():
    state = {}
    collector_state.record(state, "온통청년", "P1", "", "eligible", "", status="신청예정")
    assert collector_state.should_show(state, "온통청년", "P1", "신청가능")


def test_should_show_false_when_status_regresses():
    state = {}
    collector_state.record(state, "온통청년", "P1", "", "eligible", "", status="신청가능")
    assert not collector_state.should_show(state, "온통청년", "P1", "마감")


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"OK: {fn.__name__}")
    print(f"{len(fns)}개 테스트 통과")

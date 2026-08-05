"""dedup.py 동작 확인용 간단 테스트. pytest 없이
python -m tests.test_dedup 로도 실행 가능."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dedup import find_cross_source_duplicates, normalize_title  # noqa: E402


def test_normalize_title_strips_institution_prefix_brackets_and_date_suffix():
    assert normalize_title("광진구청<청년 월세 지원사업 신청안내>(~8/31)") == normalize_title("청년 월세 지원사업")


def test_normalize_title_strips_trailing_noise_words():
    assert normalize_title("청년 취업 장려금 신청안내") == normalize_title("청년 취업 장려금")


def test_normalize_title_handles_empty():
    assert normalize_title("") == ""
    assert normalize_title(None) == ""


def test_find_duplicates_matches_same_policy_different_formatting():
    primary = [{"plcyNm": "청년 월세 지원사업"}]
    secondary = [{"plcyNm": "광진구청<청년 월세 지원사업 신청안내>(~8/31)"}]
    assert find_cross_source_duplicates(primary, secondary) == {0}


def test_find_duplicates_does_not_match_unrelated_policies():
    primary = [{"plcyNm": "청년 취업 장려금"}]
    secondary = [{"plcyNm": "광진구청<임산부 친환경농산물 지원사업 안내>(~7/28)"}]
    assert find_cross_source_duplicates(primary, secondary) == set()


def test_find_duplicates_ignores_empty_titles():
    primary = [{"plcyNm": ""}]
    secondary = [{"plcyNm": ""}]
    assert find_cross_source_duplicates(primary, secondary) == set()


def test_find_duplicates_returns_correct_index_among_multiple():
    primary = [{"plcyNm": "청년 취업 장려금"}, {"plcyNm": "청년 월세 지원사업"}]
    secondary = [
        {"plcyNm": "광진구청<완전히 다른 정책 안내>(~9/1)"},
        {"plcyNm": "광진구청<청년 월세 지원사업 신청안내>(~8/31)"},
    ]
    assert find_cross_source_duplicates(primary, secondary) == {1}


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"OK: {fn.__name__}")
    print(f"{len(fns)}개 테스트 통과")

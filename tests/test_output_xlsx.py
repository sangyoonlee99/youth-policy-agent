"""output_xlsx.py 동작 확인용 간단 테스트."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.output_xlsx import _parse_deadline, build_rows  # noqa: E402


def test_parse_deadline_range_format():
    assert _parse_deadline("20260101 ~ 20261231") == date(2026, 12, 31)


def test_parse_deadline_single_date():
    assert _parse_deadline("2026-06-30") == date(2026, 6, 30)


def test_parse_deadline_missing_returns_none():
    assert _parse_deadline("") is None
    assert _parse_deadline(None) is None
    assert _parse_deadline("상시모집") is None


def _fake_result(name, verdict, aply_ymd, url):
    return {
        "policy": {
            "plcyNm": name,
            "lclsfNm": "일자리",
            "plcyExplnCn": "설명",
            "aplyYmd": aply_ymd,
            "aplyUrlAddr": url,
        },
        "verdict": verdict,
        "verdict_reason": "",
    }


def test_build_rows_splits_ready_vs_needs_review():
    results = [
        _fake_result("A", "eligible", "20260101 ~ 20261231", "http://a"),
        _fake_result("B", "eligible", "상시모집", "http://b"),   # 마감일 파싱 실패
        _fake_result("C", "eligible", "20260101 ~ 20260601", ""),  # 링크 없음
        _fake_result("D", "excluded", "20260101 ~ 20260601", "http://d"),  # 제외됨
    ]
    ready, needs_review = build_rows(results)
    ready_names = {r["정책명"] for r in ready}
    review_names = {r["정책명"] for r in needs_review}
    assert ready_names == {"A"}
    assert review_names == {"B", "C"}
    assert "D" not in ready_names and "D" not in review_names


def test_build_rows_skips_suppressed_repeats_entirely():
    results = [
        _fake_result("A", "eligible", "20260101 ~ 20261231", "http://a"),
        _fake_result("E", "eligible", "상시모집", "http://e"),
    ]
    results[1]["_suppress"] = True  # 상태 변화 없이 지난 회차에 이미 보여준 것으로 가정
    ready, needs_review = build_rows(results)
    all_names = {r["정책명"] for r in ready} | {r["정책명"] for r in needs_review}
    assert "A" in all_names
    assert "E" not in all_names


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"OK: {fn.__name__}")
    print(f"{len(fns)}개 테스트 통과")

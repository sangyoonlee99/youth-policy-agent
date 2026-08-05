"""matcher.py 동작 확인용 간단 테스트. pytest 없이 python -m tests.test_matcher 로도 실행 가능."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.matcher import Profile, is_eligible, match  # noqa: E402


def sample_policy(**overrides):
    base = {
        "plcyNo": "T1",
        "plcyNm": "테스트 정책",
        "lclsfNm": "일자리",
        "sprtTrgtMinAge": 19,
        "sprtTrgtMaxAge": 34,
        "zipCd": "11",
        "earnMaxAmt": 50000000,
    }
    base.update(overrides)
    return base


def base_profile(**overrides):
    defaults = dict(
        birth_year=2000,
        region_code="11",
        marital_status="무관",
        annual_income_10k=None,
        interests=[],
    )
    defaults.update(overrides)
    return Profile(**defaults)


def test_age_filter_excludes_too_young():
    profile = base_profile(birth_year=2015)
    ok, reasons = is_eligible(sample_policy(), profile)
    assert not ok
    assert any("나이 미달" in r for r in reasons)


def test_age_filter_excludes_too_old():
    profile = base_profile(birth_year=1980)
    ok, reasons = is_eligible(sample_policy(), profile)
    assert not ok
    assert any("나이 초과" in r for r in reasons)


def test_region_mismatch():
    profile = base_profile(region_code="26")
    ok, reasons = is_eligible(sample_policy(zipCd="11"), profile)
    assert not ok
    assert any("거주지역" in r for r in reasons)


def test_eligible_and_scored_higher_with_matching_interest():
    profile = base_profile(interests=["일자리"])
    results = match([sample_policy()], profile)
    assert results[0]["eligible"]
    assert results[0]["score"] > 0


def test_multi_value_job_code_matches_if_any_value_matches_profile():
    """실측 확인된 케이스: jobCd가 "0013003,0013006"처럼 콤마로 여러 값이 올 수
    있음. 그중 하나라도 프로필과 일치하면 통과해야 한다."""
    profile = base_profile(employment_status="(예비)창업자")
    ok, reasons = is_eligible(sample_policy(jobCd="0013003,0013006"), profile)
    assert ok
    assert reasons == []


def test_multi_value_job_code_excludes_if_none_match_profile():
    profile = base_profile(employment_status="재직자")
    ok, reasons = is_eligible(sample_policy(jobCd="0013003,0013006"), profile)
    assert not ok
    assert any("employment_status" in r for r in reasons)


def test_profile_summary_includes_extra_fields():
    profile = base_profile(
        education_status="대학 졸업",
        employment_status="취업준비생",
        household_type="일반",
    )
    s = profile.summary()
    assert "대학 졸업" in s
    assert "취업준비생" in s


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"OK: {fn.__name__}")
    print(f"{len(fns)}개 테스트 통과")

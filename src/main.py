"""청년정책 매칭 추천 실행 스크립트. 결과는 엑셀 파일로 저장된다.

사용법:
  python -m src.main --mock          # 실제 API 키 없이 샘플 데이터로 동작 확인
  python -m src.main --inspect       # API 원시 응답을 찍어보고 종료 (키 발급 후 최초 1회 필수)
  python -m src.main                 # config.yaml 기준 실제 실행, output/matched_policies.xlsx 생성
  python -m src.main --no-llm        # 소프트 필터(자유 텍스트 LLM 판정) 생략, 하드 필터 결과만

프로필은 config.yaml의 profile.file 에 지정한 엑셀(profile_template.xlsx 형식)에서
읽는다. file 키가 없으면 config.yaml의 profile 하위 인라인 값을 대신 쓴다(빠른 테스트용).

소프트 필터(자유 텍스트 자격조건 LLM 판정)를 쓰려면 ANTHROPIC_API_KEY 환경변수가
있어야 함. 없으면 자동으로 건너뛰고 "검토 필요"로 표시됨 — 에러 나지 않음.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

from . import collector_state
from . import dedup
from .api_client import YouthCenterClient
from .llm_check import check_free_text_eligibility, is_available
from .matcher import FIELD_MAP, Profile, match
from .output_xlsx import compute_deadline_and_status, save_xlsx
from .profile_io import gu_name_from_region_code, load_profile_from_excel
from . import seoul_youth_client

ROOT = Path(__file__).resolve().parent.parent
SOURCE_YOUTHCENTER = "온통청년"
SOURCE_SEOUL_YOUTH = "청년몽땅정보통"


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _mock_profile() -> Profile:
    """--mock 전용 기본 프로필. profile.xlsx가 아직 없을 때 data/sample_policies.json과
    짝을 이뤄 파이프라인 동작만 확인하기 위한 값 — 실제 신청 자격 판단에 쓰면 안 됨."""
    return Profile(
        birth_year=2000,
        region_code="11",
        marital_status="무관",
        annual_income_10k=3000,
        interests=["일자리", "주거"],
        education_status="대학 졸업",
        employment_status="재직자",
        employment_detail="중소기업 재직 2년차",
        specialization="공학계열",
        specialization_detail="컴퓨터공학",
        household_type="제한없음",
        military_status="군필",
        notes="",
    )


def build_profile(cfg: dict, mock: bool = False) -> Profile:
    """profile.file 이 지정돼 있으면 그 엑셀에서 읽고, 없으면 인라인 값을 쓴다.
    --mock 실행 중 profile.file이 지정돼 있지만 실제 파일이 없으면(아직 안 채웠을
    뿐이므로 에러가 아니라 정상 상황) 목 프로필로 폴백해서 파이프라인 동작 확인은
    막히지 않게 한다."""
    p = cfg["profile"]
    if p.get("file"):
        path = Path(p["file"])
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            if mock:
                print(f"[안내] --mock 실행인데 {path} 가 없어서 목 프로필로 대체합니다.")
                return _mock_profile()
            raise FileNotFoundError(
                f"{path} 가 없습니다. profile_template.xlsx를 채워서 이 경로에 저장하세요."
            )
        return load_profile_from_excel(str(path))

    return Profile(
        birth_year=p["birth_year"],
        region_code=str(p["region_code"]),
        marital_status=p.get("marital_status", "무관"),
        annual_income_10k=p.get("annual_income_10k"),
        interests=p.get("interests", []),
        education_status=p.get("education_status", ""),
        employment_status=p.get("employment_status", ""),
        employment_detail=p.get("employment_detail", ""),
        specialization=p.get("specialization", ""),
        specialization_detail=p.get("specialization_detail", ""),
        household_type=p.get("household_type", ""),
        military_status=p.get("military_status", ""),
        notes=p.get("notes", ""),
    )


def fetch_all_sources(cfg: dict, profile: Profile, mock: bool):
    """소스별로 정책을 모아서 (합쳐진 정책 리스트, 소스별 현황 dict)를 반환한다.
    소스 하나가 실패해도 예외를 밖으로 던지지 않고 그 소스만 오류로 기록하고
    나머지는 그대로 진행한다 — 한 소스의 장애가 전체 파이프라인을 막으면 안 됨."""
    source_counts = {}

    if mock:
        with open(ROOT / "data" / "sample_policies.json", "r", encoding="utf-8") as f:
            mock_policies = json.load(f)
        for p in mock_policies:
            p.setdefault("_source", SOURCE_YOUTHCENTER)
        source_counts[SOURCE_YOUTHCENTER] = {"count": len(mock_policies)}
        return mock_policies, source_counts

    yc_policies = []
    try:
        client = YouthCenterClient(
            api_key=cfg["api"]["api_key"],
            base_url=cfg["api"].get("base_url"),
        )
        yc_policies = client.fetch_all(display=cfg["api"].get("page_size", 100))
        for p in yc_policies:
            p.setdefault("_source", SOURCE_YOUTHCENTER)
        source_counts[SOURCE_YOUTHCENTER] = {"count": len(yc_policies)}
    except Exception as e:
        source_counts[SOURCE_YOUTHCENTER] = {"error": str(e)}
        print(f"[오류] {SOURCE_YOUTHCENTER} 수집 실패: {e}")

    sy_policies = []
    seoul_cfg = (cfg.get("sources") or {}).get("seoul_youth", {})
    if seoul_cfg.get("enabled", True):
        gu = gu_name_from_region_code(profile.region_code)
        if not gu:
            print(f"[안내] {SOURCE_SEOUL_YOUTH}: 프로필 거주지가 서울 자치구로 특정되지 않아 이번엔 건너뜁니다.")
        else:
            try:
                sy_policies = seoul_youth_client.fetch_and_normalize(
                    gu, fetch_details=seoul_cfg.get("fetch_details", True),
                )
                source_counts[SOURCE_SEOUL_YOUTH] = {"count": len(sy_policies)}
            except Exception as e:
                source_counts[SOURCE_SEOUL_YOUTH] = {"error": str(e)}
                print(f"[오류] {SOURCE_SEOUL_YOUTH} 수집 실패: {e}")

    # 온통청년이 이번 회차에 정상 수집됐을 때만 교차 중복 비교를 한다 — 비교 대상
    # 자체가 없거나(실패) 못 믿을 상태면 잘못 지울 위험이 있으니 그냥 둘 다 남긴다.
    if yc_policies and sy_policies:
        dup_indices = dedup.find_cross_source_duplicates(yc_policies, sy_policies)
        if dup_indices:
            sy_policies = [p for i, p in enumerate(sy_policies) if i not in dup_indices]
            source_counts[SOURCE_SEOUL_YOUTH]["deduped"] = len(dup_indices)
            source_counts[SOURCE_SEOUL_YOUTH]["count"] = len(sy_policies)

    return yc_policies + sy_policies, source_counts


def apply_soft_filter(results: list, profile: Profile, use_llm: bool, state: dict = None,
                       hide_repeats: bool = True):
    """하드 필터를 통과한 항목에 한해 자유 텍스트 자격조건을 LLM으로 판정하고
    verdict(eligible/excluded/not_sure)를 붙인다.

    `state`(증분수집 상태, collector_state.load()의 결과)가 주어지면:
    - 지난 회차와 조건 텍스트가 동일한 정책은 LLM을 다시 부르지 않고 캐시된
      verdict를 재사용한다(LLM 호출 비용 절감), 이번 회차에 처음 나타난 정책인지도
      표시한다.
    - `hide_repeats=True`(기본값)면, 상태가 지난 회차보다 더 액션 가능한 쪽으로
      바뀌지 않은 이미 본 정책은 `r["_suppress"]=True`로 표시한다(output_xlsx가
      결과 시트에서 아예 뺌). 상태는 보여주든 말든 매번 최신값으로 기록해둔다."""
    summary = profile.summary()
    for r in results:
        if not r["eligible"]:
            r["verdict"] = "excluded"
            continue
        pol = r["policy"]
        source = pol.get("_source", SOURCE_YOUTHCENTER)
        policy_id = str(pol.get(FIELD_MAP["id"], ""))
        extra1 = pol.get(FIELD_MAP["extra_qualify"], "")
        extra2 = pol.get(FIELD_MAP["ptcp_target"], "")
        condition_text = "\n".join([t for t in (extra1, extra2) if t])
        _, status = compute_deadline_and_status(pol)

        if state is not None and policy_id:
            r["is_new"] = collector_state.is_new(state, source, policy_id)
            if hide_repeats:
                r["_suppress"] = not collector_state.should_show(state, source, policy_id, status)
            cached = collector_state.get_cached_verdict(state, source, policy_id, condition_text)
            if cached:
                r["verdict"], r["verdict_reason"] = cached
                collector_state.record(state, source, policy_id, condition_text, r["verdict"], r["verdict_reason"],
                                        status=status)
                continue

        if not use_llm:
            r["verdict"] = "not_sure" if condition_text else "eligible"
            r["verdict_reason"] = "LLM 생략(--no-llm 또는 키 없음) — 직접 확인 필요" if condition_text else "추가 조건 텍스트 없음"
        else:
            verdict = check_free_text_eligibility(summary, condition_text)
            r["verdict"] = verdict["verdict"]
            r["verdict_reason"] = verdict.get("reason", "")

        if state is not None and policy_id:
            collector_state.record(state, source, policy_id, condition_text, r["verdict"], r["verdict_reason"],
                                    status=status)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--inspect", action="store_true", help="API 원시 응답 구조만 확인하고 종료")
    parser.add_argument("--mock", action="store_true", help="샘플 데이터로 실행 (API 키 불필요)")
    parser.add_argument("--no-llm", action="store_true", help="소프트 필터(자유 텍스트 LLM 판정) 생략")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        if args.mock:
            cfg = load_config(ROOT / "config.example.yaml")
        else:
            print(f"{cfg_path} 가 없습니다. config.example.yaml을 복사해서 값을 채워주세요.")
            sys.exit(1)
    else:
        cfg = load_config(cfg_path)

    if args.inspect and not args.mock:
        client = YouthCenterClient(
            api_key=cfg["api"]["api_key"],
            base_url=cfg["api"].get("base_url"),
        )
        resp = client.fetch_policies(page_index=1, display=5)
        print("Status:", resp.status_code)
        # 응답에 한글이 섞여 있으면 Windows 콘솔(cp949)에 바로 print하다가
        # UnicodeEncodeError로 죽을 수 있어서, 파일로 저장하고 경로만 안내한다.
        inspect_path = ROOT / "output" / "inspect_response.json"
        inspect_path.parent.mkdir(parents=True, exist_ok=True)
        with open(inspect_path, "w", encoding="utf-8") as f:
            f.write(resp.text)
        print(f"원시 응답을 저장했습니다: {inspect_path}  (에디터로 열어서 확인하세요)")
        return

    profile = build_profile(cfg, mock=args.mock)
    policies, source_counts = fetch_all_sources(cfg, profile, mock=args.mock)
    for name, info in source_counts.items():
        if info.get("error"):
            continue
        note = f" ({info['note']})" if info.get("note") else ""
        print(f"[수집] {name}: {info.get('count', 0)}건{note}")

    results = match(policies, profile)

    use_llm = (not args.no_llm) and is_available()
    if (not args.no_llm) and not is_available():
        print("[안내] ANTHROPIC_API_KEY가 없어서 자유 텍스트 조건 LLM 판정을 건너뜁니다. "
              "해당 정책들은 '검토 필요'로 표시됩니다.")

    out_cfg = cfg.get("output", {})
    hide_repeats = out_cfg.get("hide_repeats", True)

    state = None if args.mock else collector_state.load()
    results = apply_soft_filter(results, profile, use_llm, state=state, hide_repeats=hide_repeats)
    if state is not None:
        collector_state.save(state)

    suppressed_count = sum(1 for r in results if r.get("_suppress"))

    out_path = ROOT / out_cfg.get("path", "output/matched_policies.xlsx")
    if out_path.suffix.lower() != ".xlsx":
        out_path = out_path.with_suffix(".xlsx")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ready, needs_review = save_xlsx(
        results, str(out_path), generated_at=datetime.now(), source_counts=source_counts,
        suppressed_count=suppressed_count,
    )
    print(f"저장됨: {out_path}  (매칭결과 {len(ready)}건 / 확인필요 {len(needs_review)}건"
          f"{f' / 반복노출생략 {suppressed_count}건' if suppressed_count else ''})")


if __name__ == "__main__":
    main()

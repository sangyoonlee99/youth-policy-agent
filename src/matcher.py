"""프로필 기반 청년정책 매칭 / 스코어링.

두 단계로 나눠서 필터링한다.

1) 하드 필터 (이 파일, `is_eligible`): API가 코드/숫자로 구조화해서 주는 조건만
   본다 — 나이, 지역, 소득상한, 혼인여부. 규칙(if문)으로 정확히 판단 가능한 것만.
2) 소프트 필터 (`llm_check.py`): API가 자유 텍스트로만 주는 조건
   (`addAplyQlfcCndCn`, `ptcpPrpTrgtCn` 같은 "추가 자격조건" 서술형 문장)은
   규칙으로 파싱이 어려워서, 내 프로필 요약과 함께 LLM에게 판단시킨다.
   → 하드 필터만 통과하면 "자동 필터링 불가"로 방치하지 않고, 최대한 자동 판정까지
     간다. 그래도 확신이 안 서면 LLM이 not_sure로 표시하고, 그건 사람이 본다.

FIELD_MAP: 2026-08-03 실측(`python -m src.main --inspect`, 실제 API 응답
2,693건 중 표본 확인)으로 검증 완료됐다. 실제 응답 필드명은 여기 있는 것과 거의
다 일치했고, 딱 하나(`bizPrdSecd` → 실제는 `bizPrdSeCd`, C가 대문자)만 고쳤다.

CODE_MAPS: `docs/API코드정보.xlsx`(온통청년 공식 코드정의서, `코드정보` 시트)를
그대로 옮긴 코드값 → 한글 라벨 매핑. **다중값 주의**: 실측 결과 `jobCd`,
`plcyMajorCd`는 정책 하나에 콤마로 구분된 여러 코드가 올 수 있다(예:
"0013003,0013006") — `is_eligible`은 이걸 감안해서 콤마로 나눠 OR로 비교한다.
`schoolCd`/`sbizCd`/`mrgSttsCd`/`earnCndSeCd`/`aplyPrdSeCd`/`bizPrdSeCd`는
표본에서는 전부 단일값이었지만, 혹시 모를 다중값도 같은 방식으로 안전하게
처리되게 해뒀다.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

FIELD_MAP = {
    "id": "plcyNo",
    "name": "plcyNm",
    "desc": "plcyExplnCn",
    "category_large": "lclsfNm",
    "category_mid": "mclsfNm",
    "min_age": "sprtTrgtMinAge",
    "max_age": "sprtTrgtMaxAge",
    "marital": "mrgSttsCd",
    "income_type": "earnCndSeCd",
    "income_min": "earnMinAmt",
    "income_max": "earnMaxAmt",
    "region_codes": "zipCd",
    "apply_url": "aplyUrlAddr",
    "apply_period": "aplyYmd",
    "apply_method": "plcyAplyMthdCn",
    "extra_qualify": "addAplyQlfcCndCn",   # 자유 텍스트 — LLM 소프트 필터 대상
    "ptcp_target": "ptcpPrpTrgtCn",         # 자유 텍스트 — LLM 소프트 필터 대상
    "school": "schoolCd",                   # 정책학력요건코드
    "job": "jobCd",                         # 정책취업요건코드
    "special": "sbizCd",                    # 정책특화요건코드
    "major": "plcyMajorCd",                 # 정책전공요건코드
    "apply_period_type": "aplyPrdSeCd",     # 신청기간구분코드: 특정기간/상시/마감
    "biz_period_type": "bizPrdSeCd",        # 사업기간구분코드
}

# docs/API코드정보.xlsx > 코드정보 시트 그대로. 코드값 -> 한글 라벨.
CODE_MAPS = {
    "mrgSttsCd": {"0055001": "기혼", "0055002": "미혼", "0055003": "제한없음"},
    "earnCndSeCd": {"0043001": "무관", "0043002": "연소득", "0043003": "기타"},
    "aplyPrdSeCd": {"0057001": "특정기간", "0057002": "상시", "0057003": "마감"},
    "bizPrdSeCd": {"0056001": "특정기간", "0056002": "기타"},
    "plcyMajorCd": {
        "0011001": "인문계열", "0011002": "사회계열", "0011003": "상경계열",
        "0011004": "이학계열", "0011005": "공학계열", "0011006": "예체능계열",
        "0011007": "농산업계열", "0011008": "기타", "0011009": "제한없음",
    },
    "jobCd": {
        "0013001": "재직자", "0013002": "자영업자", "0013003": "미취업자",
        "0013004": "프리랜서", "0013005": "일용근로자", "0013006": "(예비)창업자",
        "0013007": "단기근로자", "0013008": "영농종사자", "0013009": "기타",
        "0013010": "제한없음",
    },
    "schoolCd": {
        "0049001": "고졸 미만", "0049002": "고교 재학", "0049003": "고졸 예정",
        "0049004": "고교 졸업", "0049005": "대학 재학", "0049006": "대졸 예정",
        "0049007": "대학 졸업", "0049008": "석·박사", "0049009": "기타",
        "0049010": "제한없음",
    },
    "sbizCd": {
        "0014001": "중소기업", "0014002": "여성", "0014003": "기초생활수급자",
        "0014004": "한부모가정", "0014005": "장애인", "0014006": "농업인",
        "0014007": "군인", "0014008": "지역인재", "0014009": "기타",
        "0014010": "제한없음",
    },
}

# 하드필터로 승격된 코드 카테고리와, 그에 대응하는 Profile 필드명.
_CODE_FILTER_FIELDS = {
    "school": ("schoolCd", "education_status"),
    "job": ("jobCd", "employment_status"),
    "special": ("sbizCd", "household_type"),
    "major": ("plcyMajorCd", "specialization"),
}

# "제한없음"과 동등하게 취급해서 필터링하지 않는 라벨들.
_NO_RESTRICTION_LABELS = {"제한없음", "무관", "", None}


def code_label(category: str, code) -> str:
    """코드값을 한글 라벨로 변환. 매핑에 없는 값은 코드 그대로 반환(모르는 값이라고
    해서 무조건 배제 처리하지 않기 위한 안전한 기본값)."""
    if code is None:
        return ""
    return CODE_MAPS.get(category, {}).get(str(code), str(code))


def code_labels(category: str, raw) -> list:
    """콤마로 구분된 다중 코드값(예: "0013003,0013006")을 한글 라벨 리스트로
    변환한다. jobCd/plcyMajorCd 등 정책 하나에 여러 값이 허용되는 필드용
    (2026-08-03 실측으로 다중값 존재 확인됨) — 단일값이어도 그냥 원소 1개짜리
    리스트가 된다."""
    if raw in (None, ""):
        return []
    return [code_label(category, c.strip()) for c in str(raw).split(",") if c.strip()]


@dataclass
class Profile:
    birth_year: int
    region_code: str
    marital_status: str                       # 미혼 / 기혼 / 무관
    annual_income_10k: Optional[int] = None
    interests: list = field(default_factory=list)

    # 아래 4개는 온통청년 코드정의서(schoolCd/jobCd/sbizCd/plcyMajorCd) 라벨과
    # 정확히 일치시켜서 하드 필터에도 쓴다(profile_template.xlsx 드롭다운이 이
    # 라벨들로 고정돼 있음). CODE_MAPS의 라벨 표기와 다르면 하드 필터가 항상
    # 불일치로 처리되니 임의로 변형하지 말 것.
    education_status: str = ""     # schoolCd 라벨. 예: "대학 재학", "대학 졸업"
    employment_status: str = ""    # jobCd 라벨. 예: "재직자", "미취업자", "(예비)창업자"
    specialization: str = ""       # plcyMajorCd 라벨(계열). 예: "공학계열"
    household_type: str = ""       # sbizCd 라벨. 예: "기초생활수급자", "한부모가정", "제한없음"

    # 아래는 여전히 자유 텍스트 — 하드 필터에는 안 쓰고 LLM 소프트 필터에
    # 프로필 요약으로 같이 넘겨서 자유 텍스트 자격조건과 대조시킨다.
    employment_detail: str = ""    # 예: "중소기업 재직 2년차"
    specialization_detail: str = ""  # 전공 세부 분야. 예: "컴퓨터공학"
    military_status: str = ""      # 예: "군필", "미필", "면제", "해당없음"
    notes: str = ""                # 그 외 자유롭게 적는 자기소개 (LLM이 참고용으로 씀)

    @property
    def age(self) -> int:
        return date.today().year - self.birth_year + 1  # 한국식 나이

    def summary(self) -> str:
        """LLM 소프트 필터에 넘길 프로필 요약 텍스트."""
        lines = [
            f"나이(한국식): {self.age}세 (출생연도 {self.birth_year})",
            f"거주지역 코드: {self.region_code}",
            f"혼인상태: {self.marital_status}",
        ]
        if self.annual_income_10k:
            lines.append(f"연소득: 약 {self.annual_income_10k}만원")
        if self.education_status:
            lines.append(f"학적상태: {self.education_status}")
        if self.employment_status:
            emp = self.employment_status
            if self.employment_detail:
                emp += f" ({self.employment_detail})"
            lines.append(f"취업/창업 상태: {emp}")
        if self.specialization:
            major = self.specialization
            if self.specialization_detail:
                major += f" ({self.specialization_detail})"
            lines.append(f"전공/분야: {major}")
        if self.household_type:
            lines.append(f"가구유형: {self.household_type}")
        if self.military_status:
            lines.append(f"병역상태: {self.military_status}")
        if self.interests:
            lines.append(f"관심분야: {', '.join(self.interests)}")
        if self.notes:
            lines.append(f"기타: {self.notes}")
        return "\n".join(lines)


def _get(policy: dict, key: str):
    return policy.get(FIELD_MAP.get(key, key))


def is_eligible(policy: dict, profile: Profile):
    """하드 필터: API가 코드/숫자로 구조화한 조건만 정확히 판단한다.
    애매하거나 자유 텍스트인 조건은 여기서 다루지 않고 llm_check로 넘긴다."""
    reasons = []

    min_age = _get(policy, "min_age")
    max_age = _get(policy, "max_age")
    if min_age not in (None, "", 0) and profile.age < int(min_age):
        reasons.append(f"나이 미달 (최소 {min_age}세)")
    if max_age not in (None, "", 0) and profile.age > int(max_age):
        reasons.append(f"나이 초과 (최대 {max_age}세)")

    region_codes = _get(policy, "region_codes")
    if region_codes:
        codes = [c.strip() for c in str(region_codes).split(",") if c.strip()]
        if codes and not any(
            profile.region_code.startswith(c) or c.startswith(profile.region_code)
            for c in codes
        ):
            reasons.append("거주지역 조건 불일치")

    # earnCndSeCd(소득조건구분)가 "무관"이면 income_max에 값이 있어도 무시.
    # "연소득"일 때만 실제 상한 비교를 한다. "기타"/알 수 없는 값은 하드필터로
    # 확신 있게 판단할 수 없으니 배제하지 않고 넘어간다(확신 없으면 필터링 안 함 원칙).
    income_type_label = code_label("earnCndSeCd", _get(policy, "income_type"))
    income_max = _get(policy, "income_max")
    if income_type_label == "연소득" and profile.annual_income_10k and income_max not in (None, "", 0):
        try:
            if profile.annual_income_10k * 10000 > float(income_max):
                reasons.append("소득 조건 초과")
        except (ValueError, TypeError):
            pass

    marital_label = code_label("mrgSttsCd", _get(policy, "marital"))
    if marital_label and marital_label not in _NO_RESTRICTION_LABELS:
        if profile.marital_status and profile.marital_status not in _NO_RESTRICTION_LABELS:
            if marital_label != profile.marital_status:
                reasons.append(f"혼인상태 조건 불일치 (정책 조건: {marital_label})")

    # 학력/취업/특화대상/전공 — 코드값 -> 라벨로 변환해서 프로필의 대응 필드와 비교한다.
    # 정책 쪽에 콤마로 여러 값이 올 수 있어서(실측 확인됨, 예: jobCd="0013003,0013006")
    # 그중 하나라도 프로필과 일치하면 통과시킨다(OR). 라벨 중 하나라도 "제한없음"이면
    # 그 정책은 이 조건 자체가 없는 것이므로 무조건 통과. 프로필 쪽이 비어있으면(아직
    # 안 입력했거나 해당 없음) 하드필터로 배제하지 않고 통과시킨다(오배제보다 낫음).
    for internal_key, (category, profile_field) in _CODE_FILTER_FIELDS.items():
        labels = code_labels(category, _get(policy, internal_key))
        if not labels or any(label in _NO_RESTRICTION_LABELS for label in labels):
            continue
        profile_value = getattr(profile, profile_field, "")
        if not profile_value:
            continue
        if profile_value not in labels:
            reasons.append(f"{profile_field} 조건 불일치 (정책 조건: {', '.join(labels)})")

    return (len(reasons) == 0, reasons)


def score(policy: dict, profile: Profile) -> int:
    """관심분야 일치 등으로 대략적인 우선순위 점수를 매긴다. 정교한 랭킹보다는
    '위에 뜨는 것부터 보면 됨' 수준의 1차 정렬용."""
    s = 0
    cat = _get(policy, "category_large") or ""
    if any(interest in cat for interest in profile.interests):
        s += 10
    if _get(policy, "apply_url"):
        s += 1
    return s


def match(policies: list, profile: Profile):
    """하드 필터만 적용한 1차 결과. LLM 소프트 필터는 main.py에서
    이 결과 중 eligible=True 항목에 대해 별도로 돌린다 (API 호출 비용 때문에
    분리)."""
    results = []
    for p in policies:
        eligible, reasons = is_eligible(p, profile)
        results.append(
            {
                "policy": p,
                "eligible": eligible,
                "reasons": reasons,
                "score": score(p, profile) if eligible else -1,
            }
        )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results

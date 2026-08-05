"""자유 텍스트 자격조건에 대한 LLM 보조 판정 (소프트 필터).

온통청년 API의 `addAplyQlfcCndCn`(추가 자격조건), `ptcpPrpTrgtCn`(참여제한대상)
같은 필드는 코드가 아니라 자연어 문장이라서 규칙 기반으로 정확히 걸러내기 어렵다.
그렇다고 "자동화 불가"로 두지 않고, 프로필 요약 + 해당 문장을 Claude에게 같이
주고 판정을 받는다.

ANTHROPIC_API_KEY 환경변수가 없으면 이 단계는 건너뛰고 not_sure로 표시한다
(하드 필터 결과 자체는 그대로 유효하니 전체 파이프라인이 죽지는 않는다).
"""

import json
import os

try:
    import anthropic
except ImportError:  # anthropic 패키지 미설치 시에도 나머지 기능은 동작해야 함
    anthropic = None

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """너는 한국 청년정책 신청 자격을 판단하는 보조 도구다.
아래 [신청자 프로필]과 [정책 추가 자격조건] 문장을 비교해서 신청자가
그 정책의 추가 조건을 만족하는지 판단하고, 반드시 아래 JSON 형식으로만 답해라.
다른 텍스트는 절대 붙이지 마라.

{"verdict": "eligible", "reason": "한 줄 이유"}

verdict는 다음 셋 중 하나:
- "eligible": 조건을 명백히 만족함
- "excluded": 조건에 명백히 배제된다고 쓰여있음
- "not_sure": 텍스트가 모호하거나 정보가 부족해서 판단 불가 (확신 없으면 무조건 이걸 써라)
"""


def is_available() -> bool:
    return anthropic is not None and bool(os.environ.get("ANTHROPIC_API_KEY"))


def check_free_text_eligibility(profile_summary: str, condition_text: str, model: str = DEFAULT_MODEL) -> dict:
    """condition_text가 비어있으면 바로 eligible 처리, LLM 미설정이면 not_sure."""
    if not condition_text or not str(condition_text).strip():
        return {"verdict": "eligible", "reason": "추가 자격조건 텍스트 없음"}

    if not is_available():
        return {"verdict": "not_sure", "reason": "ANTHROPIC_API_KEY 미설정 — 직접 확인 필요"}

    client = anthropic.Anthropic()
    user_msg = f"[신청자 프로필]\n{profile_summary}\n\n[정책 추가 자격조건]\n{condition_text}"
    resp = client.messages.create(
        model=model,
        max_tokens=200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text.strip()
    try:
        parsed = json.loads(raw)
        if parsed.get("verdict") not in ("eligible", "excluded", "not_sure"):
            raise ValueError("unexpected verdict")
        return parsed
    except (json.JSONDecodeError, ValueError):
        return {"verdict": "not_sure", "reason": f"LLM 응답 파싱 실패: {raw[:120]}"}

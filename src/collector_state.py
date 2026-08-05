"""증분 수집 상태 파일(data/collector_state.json) 읽기/쓰기.

정책을 한 번 긁어온 뒤 다음 수집 때 처음부터 전체를 다시 판정하지 않기 위한
로컬 상태. 온통청년이 등록일/수정일 기준 조회 파라미터를 지원하는지는
`--inspect`로 실제 요청 파라미터를 확인하기 전까지는 알 수 없어서(CLAUDE.md
참고), 지금은 로컬 dedup 방식으로 구현한다: 매 회차마다 받은 전체 목록을
이전에 본 것과 비교해서 신규만 표시하고, LLM 소프트필터 verdict는 캐싱해서
이미 판정된 정책을 매번 재호출하지 않는다(LLM 호출은 정책당 비용이 들기
때문 — 재판정 낭비를 막는 게 목적).

소스별로 독립된 하위 딕셔너리를 쓴다 — 한 소스의 상태가 깨져도 다른 소스에
영향을 안 주기 위함.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "collector_state.json"

# 상태가 "더 액션 가능한 방향"으로 바뀌었는지 비교하기 위한 순위. 마감/불명(빈 문자열)은
# 둘 다 "액션 불가"로 취급해서 같은 순위(0)로 둔다 — 마감<->불명 전환은 새 정보가 아님.
STATUS_RANK = {"": 0, "마감": 0, "신청예정": 1, "신청가능": 2}


def load(path: Path = DEFAULT_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        # 상태 파일이 깨졌어도 파이프라인은 계속 돌아야 한다 — 이번 회차는 그냥
        # 처음부터 다시 판정하는 정도로 성능만 손해보고, 에러로 죽지는 않는다.
        print(f"[안내] {path} 를 읽지 못해서({e}) 증분수집 상태 없이 처음부터 판정합니다.")
        return {}


def save(state: dict, path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def is_new(state: dict, source: str, policy_id: str) -> bool:
    """이번 회차 전에는 한 번도 안 보였던 정책인지."""
    return policy_id not in state.get(source, {}).get("seen", {})


def should_show(state: dict, source: str, policy_id: str, current_status: str) -> bool:
    """이번 회차 결과에 이 정책을 보여줄지 결정한다.

    처음 보는 정책이면 무조건 보여준다. 이미 본 적 있으면, 지난번에 기록해둔
    `last_status`보다 이번 상태가 더 액션 가능한 방향(마감/불명 < 신청예정 <
    신청가능)으로 개선됐을 때만 다시 보여준다 — 상태 변화가 없으면(같은 상태를
    반복 관찰) 이미 보여준 정보이므로 생략, 오히려 더 나빠졌으면(예: 신청가능 →
    마감) 그것도 새로 알려줄 액션이 없으므로 생략한다."""
    entry = state.get(source, {}).get("seen", {}).get(policy_id)
    if not entry:
        return True
    last_status = entry.get("last_status", "")
    return STATUS_RANK.get(current_status, 0) > STATUS_RANK.get(last_status, 0)


def get_cached_verdict(state: dict, source: str, policy_id: str, condition_text: str):
    """condition_text가 지난번과 동일할 때만 캐시를 신뢰한다(문구가 바뀌면 재판정
    해야 하니까). 캐시가 있으면 (verdict, reason) 튜플, 없으면 None."""
    entry = state.get(source, {}).get("seen", {}).get(policy_id)
    if not entry or entry.get("condition_hash") != _hash(condition_text):
        return None
    return entry.get("verdict"), entry.get("verdict_reason", "")


def record(state: dict, source: str, policy_id: str, condition_text: str,
           verdict: str, reason: str, status: str = None, now: datetime = None) -> None:
    now = now or datetime.now()
    src_state = state.setdefault(source, {})
    src_state["last_run_at"] = now.isoformat(timespec="seconds")
    seen = src_state.setdefault("seen", {})
    entry = seen.setdefault(policy_id, {"first_seen": now.isoformat(timespec="seconds")})
    entry["last_seen"] = now.isoformat(timespec="seconds")
    entry["condition_hash"] = _hash(condition_text)
    entry["verdict"] = verdict
    entry["verdict_reason"] = reason
    if status is not None:
        # 보여주든 안 보여주든 항상 최신 상태로 갱신해야 다음 회차 비교가 정확하다.
        entry["last_status"] = status

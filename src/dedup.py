"""소스 간(온통청년 × 청년몽땅정보통) 같은 정책 중복 탐지.

두 소스는 완전히 독립적으로 수집돼서, 같은 실제 정책이 양쪽에 다 올라와 있어도
겹치는지 전혀 확인이 안 됐다. 정책번호 같은 공통 키가 없어서 제목 유사도로
비교한다 — 온통청년/청년몽땅정보통은 제목 표기 관례가 달라서(예: 청년몽땅정보통은
"광진구청<제목>(~8/31)"처럼 기관명·괄호·날짜가 섞여 나옴) 정규화 후 비교해야 한다.

오탐(서로 다른 정책을 같은 걸로 착각해서 하나를 지워버리는 것)이 미탐보다 훨씬
위험하므로 임계값은 보수적으로 높게 잡는다 — 애매하면 중복 아님으로 취급하고
그냥 둘 다 남긴다.
"""

import re
from difflib import SequenceMatcher

DEFAULT_THRESHOLD = 0.82

_INSTITUTION_PREFIX_RE = re.compile(r"^[^<]{0,20}<")  # "광진구청<" 같은 기관명 접두사
_BRACKET_RE = re.compile(r"[<>]")
_DATE_SUFFIX_RE = re.compile(r"\(~?\d{1,2}[/.]\d{1,2}\)|\(상시\)|\(선착순\)")
_NON_ALNUM_RE = re.compile(r"[^0-9a-zA-Z가-힣]")

# 청년몽땅정보통 쪽 제목엔 이런 말이 흔히 덧붙어서(예: "...신청안내", "...참여자
# 모집") 실제로 같은 정책이어도 유사도가 떨어진다 — 끝에 붙어있으면 잘라낸다.
# 긴 것부터 검사해야 "참여자모집안내"가 "모집"만 잘리고 남는 일이 없다.
_TRAILING_NOISE = ["참여자모집안내", "참여자모집", "신청안내", "모집안내", "모집공고", "안내", "공고", "모집"]


def normalize_title(title: str) -> str:
    """제목을 비교 가능한 형태로 정규화한다: 기관명 접두사/꺾쇠, 날짜/모집방식
    접미사, 공백·구두점을 없애고 소문자화한 뒤 흔한 꼬리말도 제거."""
    if not title:
        return ""
    t = _INSTITUTION_PREFIX_RE.sub("", title)
    t = _BRACKET_RE.sub("", t)
    t = _DATE_SUFFIX_RE.sub("", t)
    t = _NON_ALNUM_RE.sub("", t)
    t = t.lower().strip()
    for suffix in _TRAILING_NOISE:
        if t.endswith(suffix) and len(t) > len(suffix):
            t = t[: -len(suffix)]
            break
    return t


def find_cross_source_duplicates(primary: list, secondary: list, threshold: float = DEFAULT_THRESHOLD,
                                  name_field: str = "plcyNm") -> set:
    """`secondary`의 각 항목이 `primary`의 어떤 항목과 제목이 임계값 이상 비슷하면
    그 인덱스를 모아서 반환한다(호출부에서 `secondary`쪽을 제거하는 용도).
    빈 정규화 제목끼리는(둘 다 정보가 없는 것) 비교하지 않는다 — 빈 문자열은
    항상 100% 일치로 오판되기 때문."""
    primary_norms = [normalize_title(p.get(name_field, "")) for p in primary]
    primary_norms = [n for n in primary_norms if n]

    dup_indices = set()
    for i, item in enumerate(secondary):
        norm = normalize_title(item.get(name_field, ""))
        if not norm:
            continue
        for p_norm in primary_norms:
            if SequenceMatcher(None, norm, p_norm).ratio() >= threshold:
                dup_indices.add(i)
                break
    return dup_indices

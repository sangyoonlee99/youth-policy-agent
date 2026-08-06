"""청년몽땅정보통(youth.seoul.go.kr) '청년지원정보' 게시판 스크래퍼.

공식 Open API가 없는 소스라서(개발자 문서를 못 찾음 — CLAUDE.md 리서치 기록 참고)
이건 크롤링이다. 사용자가 "청년몽땅정보통에 있는 정보까지는 가져와야 한다"고
명시적으로 요청해서 승인된 것으로 취급하고 진행한다 (robots.txt/저작권 정책
확인 결과 자동 수집 자체를 막는 조항은 발견되지 않았음 — 자세한 내용은
CLAUDE.md "승인된 통합 방식" 절 참고).

목록 페이지(`infoData/sprtInfo/list.do`)는 서버 렌더링이라 requests만으로 읽힌다
(자바스크립트 실행 불필요) — 각 공고는 `<div class="feed-item">` 블록 안에
제목/카테고리/상태 배지(모집중=bg-blue, 모집예정=bg-purple, 마감=bg-gray-66)로
들어있다. 제목 끝의 "(~8/17)" 같은 표기가 사실상 유일한 날짜 정보라 마감일로
간주해서 파싱한다 — 연도가 없으므로 상태가 '마감'이 아닌데 올해 기준으로 이미
지난 날짜면 내년으로 보정한다.

상세 페이지(view.do)도 긁는다(마감 안 된 항목만) — 목록만으로는 나이/학력 같은
구조화된 자격조건이 전혀 없어서 프로필 하드필터가 이 소스엔 사실상 무력했다.
상세 페이지를 직접 열어보니(requests만으로 확인, JS 실행 불필요) "대상" 항목은
값이 있어도 신뢰하기 어려웠지만(체크박스형 다중값이 선택 여부 구분 없이 전부
텍스트로 나옴 — 파싱 포기), 본문 자유 텍스트(`.editor-text`)에는 실제 자격조건이
있고 "신청기간"도 목록 제목의 "(~8/17)" 같은 부정확한 표기보다 훨씬 정확한
"YYYY-MM-DD ~ YYYY-MM-DD" 형식으로 나온다. 본문 텍스트는 `addAplyQlfcCndCn`에
넣어서 기존 LLM 소프트필터(`llm_check.py`)가 그대로 판정하게 하고, "담당기관
바로가기" 외부 링크가 있으면 신청링크를 그걸로 교체한다(없으면 view.do 링크 유지).
요청은 여전히 예의상 간격을 두고 순회한다(마감 항목은 상세 스크래핑 자체를
건너뛰어서 불필요한 요청을 줄임).

사이트 개편으로 HTML 구조가 바뀌면 조용히 0건으로 보이는 대신 예외를 던진다 —
'원래 0건'과 '파싱이 깨져서 0건'을 구분하기 위함(수집 파이프라인 설계 원칙).
"""

import re
import time
from datetime import date

import requests
from bs4 import BeautifulSoup

from .profile_io import SIGUNGU_CODE

LIST_URL = "https://youth.seoul.go.kr/infoData/sprtInfo/list.do"
VIEW_URL = "https://youth.seoul.go.kr/infoData/sprtInfo/view.do"
DEFAULT_KEY = "2309130006"  # "청년지원정보" 게시판. 서울시 정책(2309150002),
                            # 중앙정부/타지역 정책(2309160001) 게시판도 같은 구조일
                            # 가능성이 높다고 CLAUDE.md에 기록돼 있으나 미확인 상태.

STATUS_TEXT_MAP = {
    "모집중": "신청가능",
    "모집예정": "신청예정",
    "마감": "마감",
    "상시": "신청가능",  # 상시모집 — 마감 개념이 없는 항상-열려있음. 초기 조사 표본(8건)에는
                        # 안 걸렸는데, 실제 게시판엔 이 배지도 씀 — 실행 중 발견해서 추가.
}

_DEADLINE_SUFFIX_RE = re.compile(r"\(~?(\d{1,2})/(\d{1,2})\)\s*$")
_TOTAL_COUNT_RE = re.compile(r"전체\s*([\d,]+)\s*건")
_PERIOD_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_BODY_TEXT_MAX_CHARS = 1500  # LLM 소프트필터로 넘어가는 길이 제한 — 비용 절감용


def _fetch(session: requests.Session, page_index: int, sigungu: str, key: str,
           page_size: int, timeout: int) -> str:
    params = {
        "key": key,
        "pageIndex": page_index,
        "recordCountPerPage": page_size,
        "sc_sarea": sigungu,
        "orderBy": "regYmd desc",
    }
    resp = session.get(LIST_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_list_page(html: str) -> list:
    """`<div class="feed-item">` 블록들을 파싱해서 원시 dict 리스트로 반환한다.
    형식이 안 맞는 블록(id/제목 못 뽑음)은 조용히 건너뛴다."""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for block in soup.find_all("div", class_="feed-item"):
        link = block.find("a", class_="item-overlay")
        name_div = block.find("div", class_="name")
        cate = block.find("span", class_="cate")
        state = block.find("span", class_="state")

        onclick = link.get("onclick", "") if link else ""
        m = re.search(r"goView\('(\d+)'\)", onclick)
        if not m or not name_div:
            continue

        items.append({
            "id": m.group(1),
            "title": name_div.get_text(strip=True),
            "cate": cate.get_text(strip=True) if cate else "",
            "state_text": state.get_text(strip=True) if state else "",
        })
    return items


def _parse_total_count(html: str):
    m = _TOTAL_COUNT_RE.search(html)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def fetch_detail(session: requests.Session, sprt_info_id: str, key: str = DEFAULT_KEY,
                  timeout: int = 15) -> str:
    params = {"sprtInfoId": sprt_info_id, "key": key}
    resp = session.get(VIEW_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_detail_page(html: str) -> dict:
    """상세 페이지에서 신청기간/본문/외부신청링크를 뽑는다.
    "대상" 항목도 시도해봤는데, 값이 여러 개일 때 실제로 선택된 것과 선택 안 된
    것을 구분하는 마크업이 없어서(체크박스 라벨이 전부 그냥 텍스트로만 나열됨)
    신뢰할 수 없다고 판단해 파싱하지 않는다 — 잘못된 정보를 넣느니 안 넣는 게 낫다.
    예상 구조와 안 맞아 아무것도 못 뽑아도 빈 dict를 반환할 뿐 예외를 던지지 않는다
    (항목 하나의 상세 파싱 실패가 전체 수집을 막으면 안 됨 — 실패 자체는 호출부에서
    로그로 남긴다)."""
    soup = BeautifulSoup(html, "lxml")
    result = {}

    overview = soup.select_one(".overview")
    if overview:
        for li in overview.select(".cont ul.info > li"):
            em = li.find("em")
            if not em or em.get_text(strip=True) != "신청기간":
                continue
            dates = _PERIOD_DATE_RE.findall(li.get_text(" ", strip=True))
            if len(dates) >= 2:
                y, m, d = dates[0]
                result["period_start"] = f"{y}{m}{d}"
                y, m, d = dates[-1]
                result["period_end"] = f"{y}{m}{d}"

        link = overview.select_one(".btn-group a")
        href = link.get("href", "").strip() if link else ""
        if href.startswith("http"):
            result["external_url"] = href

    body = soup.select_one("div.detail .box .editor-text")
    if body:
        text = body.get_text(" ", strip=True)
        if text:
            result["body_text"] = text[:_BODY_TEXT_MAX_CHARS]

    return result


def fetch_all(sigungu: str, key: str = DEFAULT_KEY, page_size: int = 20,
              max_pages: int = 30, delay: float = 1.5, timeout: int = 15,
              session: requests.Session = None, fetch_details: bool = True) -> list:
    """`sigungu`(서울 자치구명, 예: "광진구")로 필터링한 전체 목록을 페이지네이션
    순회하며 모은다. 요청 사이 `delay`초씩 쉬어서 서버 부담을 줄인다.

    `fetch_details=True`(기본값)면, 마감 안 된 항목마다 상세페이지도 하나씩 더
    가져와서 각 항목 dict에 `detail` 키로 붙여준다(자격조건 텍스트/정확한 신청기간/
    외부신청링크). 항목 하나의 상세 조회가 실패해도 그 항목만 목록 정보만으로
    남기고 계속 진행한다 — 실패는 로그로만 남김."""
    session = session or requests.Session()
    all_items = []
    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(delay)
        html = _fetch(session, page, sigungu, key, page_size, timeout)
        page_items = parse_list_page(html)

        if not page_items:
            total = _parse_total_count(html)
            if total and len(all_items) < total:
                raise RuntimeError(
                    f"청년몽땅정보통 목록 파싱 실패로 보임: 사이트는 전체 {total}건이라는데 "
                    f"{page}페이지에서 0건이 추출됐습니다. HTML 구조가 바뀌었을 가능성이 큽니다 "
                    f"— src/seoul_youth_client.py의 parse_list_page를 점검하세요."
                )
            break

        all_items.extend(page_items)
        if len(page_items) < page_size:
            break

    if fetch_details:
        for item in all_items:
            if item.get("state_text") == "마감":
                continue  # 마감된 건 매칭 대상이 아니므로 상세 조회를 아예 스킵
            time.sleep(delay)
            try:
                detail_html = fetch_detail(session, item["id"], key=key, timeout=timeout)
                item["detail"] = parse_detail_page(detail_html)
            except Exception as e:
                print(f"[안내] 청년몽땅정보통 상세페이지(id={item['id']}) 조회 실패 — "
                      f"목록 정보만으로 처리합니다: {e}")

    return all_items


def normalize(raw_item: dict, sigungu: str, key: str = DEFAULT_KEY, today: date = None) -> dict:
    """원시 항목을 온통청년 응답과 같은 필드명(FIELD_MAP 호환) dict로 정규화한다.
    학력/취업/소득 같은 코드화된 조건은 이 소스엔 없으므로 None으로 두고(하드필터에서
    '정보 없음'은 배제하지 않고 통과시키게 돼 있음), 상태는 사이트 배지를 그대로
    신뢰해서 `_status_override`로 넘긴다(output_xlsx.determine_status가 최우선으로
    사용).

    `raw_item`에 `detail`(fetch_all이 상세페이지에서 뽑아둔 dict)이 있으면 그걸
    우선 쓴다: 정확한 신청기간(YYYY-MM-DD 기반)이 있으면 제목의 "(~8/17)" 추정치
    대신 그걸 쓰고, 본문 자유 텍스트는 `addAplyQlfcCndCn`에 넣어서 기존 LLM
    소프트필터가 판정하게 하고, 외부 신청링크가 있으면 신청링크를 그걸로 교체한다."""
    today = today or date.today()
    title = raw_item["title"]
    detail = raw_item.get("detail") or {}

    status = STATUS_TEXT_MAP.get(raw_item.get("state_text"), "")

    clean_title = title
    m = _DEADLINE_SUFFIX_RE.search(title)
    if m:
        clean_title = _DEADLINE_SUFFIX_RE.sub("", title).strip()

    deadline_str = detail.get("period_end", "")
    if not deadline_str and m:
        try:
            mm, dd = int(m.group(1)), int(m.group(2))
            candidate = date(today.year, mm, dd)
        except ValueError:
            candidate = None  # 말이 안 되는 월/일이면 그냥 못 뽑은 걸로 처리

        # 2026-08-06 실측(issue #4)으로 "다음 해로 추정"하던 로직을 제거함:
        # 상태 배지가 '마감'이 아닌데 날짜가 이미 지난 경우, 예전엔 "내년
        # 거겠지"하고 date(today.year+1, ...)로 밀었었다. 근데 실제로는 상태
        # 배지가 '상시'인 채로 그냥 오래 방치된(이미 끝난) 글도 많아서, 이미
        # 끝난 2026년 공고를 2027년 공고로 잘못 표시하는 사고가 났다. 추정이
        # 틀렸을 때 위험이 크므로(끝난 걸 열려있다고 보여주는 것), 이런 경우는
        # 그냥 마감일을 '모른다'로 남겨서 확인필요로 보낸다 — 미래 연도를
        # 지어내지 않는다. 마감 상태면 지난 날짜가 오히려 정확한 정보이므로
        # 그대로 쓴다.
        if candidate is not None and (status == "마감" or candidate >= today):
            deadline_str = candidate.strftime("%Y%m%d")

    view_url = f"{VIEW_URL}?sprtInfoId={raw_item['id']}&key={key}"
    apply_url = detail.get("external_url") or view_url
    zip_code = SIGUNGU_CODE.get(("서울특별시", sigungu), "11")

    return {
        "plcyNo": f"SEOUL-{raw_item['id']}",
        "plcyNm": clean_title,
        "plcyExplnCn": clean_title,
        "lclsfNm": "서울시 청년정보",
        "mclsfNm": raw_item.get("cate") or "",
        "sprtTrgtMinAge": None,
        "sprtTrgtMaxAge": None,
        "mrgSttsCd": None,
        "earnCndSeCd": None,
        "earnMinAmt": None,
        "earnMaxAmt": None,
        "schoolCd": None,
        "jobCd": None,
        "sbizCd": None,
        "plcyMajorCd": None,
        "zipCd": zip_code,
        "aplyUrlAddr": apply_url,
        "aplyYmd": deadline_str,
        "aplyPrdSeCd": None,
        "plcyAplyMthdCn": "청년몽땅정보통 페이지에서 상세 확인 후 신청",
        "addAplyQlfcCndCn": detail.get("body_text", ""),
        "ptcpPrpTrgtCn": "",
        "_status_override": status,
        "_source": "청년몽땅정보통",
    }


def fetch_and_normalize(sigungu: str, key: str = DEFAULT_KEY, fetch_details: bool = True,
                         **kwargs) -> list:
    """이 소스에서 최종적으로 쓸 함수. main.py에서 이것만 호출하면 된다."""
    raw_items = fetch_all(sigungu, key=key, fetch_details=fetch_details, **kwargs)
    return [normalize(item, sigungu, key=key) for item in raw_items]

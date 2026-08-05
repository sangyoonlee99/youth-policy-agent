"""seoul_youth_client.py 파싱 로직 확인용 테스트. 네트워크 호출 없이 순수 파싱만
검증한다(실제 사이트 HTML 구조를 반영한 정적 fixture 사용). pytest 없이도
python -m tests.test_seoul_youth_client 로 실행 가능."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.seoul_youth_client import normalize, parse_detail_page  # noqa: E402

DETAIL_HTML_FULL = """
<html><body>
<div class="overview">
  <div class="cont">
    <div class="tit"><strong>테스트 정책</strong></div>
    <ul class="info">
      <li><em>신청기간</em> 2026-07-30 ~ 2026-08-09 &nbsp; 18 : 00</li>
      <li><em>진행일정</em></li>
      <li><em>대상</em> 대학생 , 구직 , 재직</li>
      <li><em>담당기관</em> 서울청년센터</li>
    </ul>
    <div class="btn-group">
      <a href="https://forms.gle/example123">담당기관 바로가기</a>
    </div>
  </div>
</div>
<div class="detail">
  <div class="box">
    <div class="editor-text"><p>만 19세부터 34세까지 신청 가능합니다. 소득 무관.</p></div>
  </div>
</div>
</body></html>
"""

DETAIL_HTML_EMPTY = """
<html><body>
<div class="overview">
  <div class="cont">
    <ul class="info">
      <li><em>신청기간</em></li>
      <li><em>대상</em></li>
    </ul>
  </div>
</div>
</body></html>
"""


def test_parse_detail_page_extracts_period_link_and_body():
    result = parse_detail_page(DETAIL_HTML_FULL)
    assert result["period_start"] == "20260730"
    assert result["period_end"] == "20260809"
    assert result["external_url"] == "https://forms.gle/example123"
    assert "19세부터 34세까지" in result["body_text"]


def test_parse_detail_page_target_field_not_parsed():
    """'대상' 필드는 체크박스 선택 여부를 구분할 마크업이 없어서 일부러 안 뽑는다."""
    result = parse_detail_page(DETAIL_HTML_FULL)
    assert "target" not in result


def test_parse_detail_page_handles_missing_fields_gracefully():
    result = parse_detail_page(DETAIL_HTML_EMPTY)
    assert "period_start" not in result
    assert "external_url" not in result
    assert "body_text" not in result


def test_parse_detail_page_handles_totally_unrelated_html():
    result = parse_detail_page("<html><body><p>hello</p></body></html>")
    assert result == {}


def _raw_item(**overrides):
    base = {
        "id": "99999",
        "title": "테스트 공고(~8/9)",
        "cate": "교육",
        "state_text": "모집중",
    }
    base.update(overrides)
    return base


def test_normalize_without_detail_falls_back_to_title_suffix_and_view_url():
    row = normalize(_raw_item(), "광진구")
    assert row["plcyNm"] == "테스트 공고"
    assert row["aplyUrlAddr"].startswith("https://youth.seoul.go.kr/infoData/sprtInfo/view.do")
    assert row["addAplyQlfcCndCn"] == ""
    assert row["_status_override"] == "신청가능"


def test_normalize_with_detail_prefers_detail_period_and_external_link():
    detail = parse_detail_page(DETAIL_HTML_FULL)
    row = normalize(_raw_item(detail=detail), "광진구")
    assert row["aplyYmd"] == "20260809"
    assert row["aplyUrlAddr"] == "https://forms.gle/example123"
    assert "19세부터 34세까지" in row["addAplyQlfcCndCn"]


def test_normalize_zip_code_matches_sigungu():
    row = normalize(_raw_item(), "광진구")
    assert row["zipCd"] == "11215"


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"OK: {fn.__name__}")
    print(f"{len(fns)}개 테스트 통과")

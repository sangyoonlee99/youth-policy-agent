"""매칭 결과를 엑셀로 저장한다.

요구사항: 업로드되는 엑셀에는 '마감일'과 '링크'가 반드시 채워져 있어야 한다.
정책 데이터에 이 둘 중 하나라도 없으면(파싱 실패 포함) '확인필요' 시트로 빼고,
'매칭결과' 시트에는 절대 올리지 않는다 — 마감일/링크 없는 항목이 섞이면
실제로 신청할 수 없는 항목이 실행 가능한 것처럼 보이게 되기 때문. 이 규칙은
느슨하게 풀지 않는다(사용자가 명시적으로 요구한 제약).

대신 '아직 신청 안 열렸을 뿐'인 항목(시행예정)까지 놓치지 않기 위해 '상태'
컬럼(신청가능/신청예정/마감/확인필요)을 추가한다. 신청기간이 아주 짧은 정책이
이번 수집 시점엔 안 열려 있다가 다음 수집 전에 열렸다 닫혀버리는 걸 막으려면,
아직 안 열린 것도 결과에서 아예 빠지지 않고 '확인필요' 시트에 '신청예정'으로
표시되어야 한다 — 마감일/링크가 없어서 확인필요로 가는 것과, 파싱을 아예
못해서 확인필요로 가는 것을 상태 컬럼으로 구분해준다.
"""

import re
from datetime import date, datetime

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .matcher import FIELD_MAP, code_label

FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="305496")
HEADER_FONT = Font(name=FONT, bold=True, color="FFFFFF")
NORMAL_FONT = Font(name=FONT)
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _parse_deadline(aply_ymd: str):
    """'20260101 ~ 20261231' 같은 문자열에서 마감일(끝 날짜)을 뽑는다.
    형식이 다르면 None을 반환 — 호출부에서 '확인필요'로 분류한다."""
    if not aply_ymd:
        return None
    s = str(aply_ymd)
    # 문자열에 날짜가 여러 개 있으면(예: "20260101 ~ 20261231") 마지막 것이 마감일이라고 본다.
    matches = re.findall(r"(\d{4})[.\-]?(\d{2})[.\-]?(\d{2})", s)
    if not matches:
        return None
    y, m, d = matches[-1]
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def _parse_start(aply_ymd: str):
    """마감일과 같은 문자열에서 시작일(첫 날짜)을 뽑는다. 날짜가 하나뿐이면 그걸
    시작일로도 본다. 형식이 다르면 None."""
    if not aply_ymd:
        return None
    s = str(aply_ymd)
    matches = re.findall(r"(\d{4})[.\-]?(\d{2})[.\-]?(\d{2})", s)
    if not matches:
        return None
    y, m, d = matches[0]
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def determine_status(policy: dict, deadline, today: date = None) -> str:
    """'신청가능' / '신청예정' / '마감' / '' (판단 불가) 넷 중 하나를 정한다.

    온통청년처럼 aplyPrdSeCd(신청기간구분코드: 특정기간/상시/마감)가 있는 소스는
    그걸 1순위로 쓰고, 값이 없거나 모르는 코드면 aplyYmd에서 뽑은 시작/종료일로
    날짜 기준 추정한다. 짧은 신청기간(예: 3일)을 가진 정책이 이번 수집엔 아직 안
    열려 있다가 다음 수집 전에 열렸다 닫혀버리는 걸 막기 위한 것 — '신청예정'으로
    표시해서 사용자가 미리 알 수 있게 한다.

    청년몽땅정보통처럼 API 코드가 없는 크롤링 소스는 정책 dict에 `_status_override`
    (사이트 자체 상태 배지에서 뽑은 값)를 직접 넣어두면 그걸 그대로 신뢰한다 —
    날짜 추정보다 소스 자체 표시가 더 정확하기 때문."""
    if policy.get("_status_override"):
        return policy["_status_override"]

    today = today or date.today()
    period_type = code_label("aplyPrdSeCd", policy.get(FIELD_MAP["apply_period_type"]))

    if period_type == "마감":
        return "마감"
    if period_type == "상시":
        return "신청가능"

    start = _parse_start(policy.get(FIELD_MAP["apply_period"]))
    end = deadline

    if end and end < today:
        return "마감"
    if start and start > today:
        return "신청예정"
    if start or end:
        return "신청가능"
    return ""


def compute_deadline_and_status(policy: dict, today: date = None):
    """`_parse_deadline` + `determine_status`를 한 번에. main.py처럼 이 모듈 밖에서
    상태를 미리 알아야 할 때(예: 증분수집 상태 비교) 쓰라고 공개해둔 헬퍼 —
    private-prefixed 함수에 직접 접근하지 않게 하기 위함."""
    deadline = _parse_deadline(policy.get(FIELD_MAP["apply_period"]))
    status = determine_status(policy, deadline, today=today)
    return deadline, status


def build_rows(results: list, today: date = None):
    """결과를 (실행가능 리스트, 확인필요 리스트) 로 나눈다.
    실행가능 = 마감일 파싱 성공 + 신청링크 존재 + 상태가 '신청가능' + verdict가
    excluded가 아님 — 마감이 지났거나 아직 안 열린 건 마감일/링크가 있어도
    실행가능으로 넣지 않는다. `r["_suppress"]`가 참인 항목(증분수집 — 상태 변화
    없이 이미 보여줬던 것)은 매칭결과/확인필요 양쪽 다 아예 넣지 않는다."""
    ready, needs_review = [], []
    for r in results:
        pol = r["policy"]
        verdict = r.get("verdict", "not_sure")
        if verdict == "excluded":
            continue
        if r.get("_suppress"):
            continue

        link = pol.get(FIELD_MAP["apply_url"]) or ""
        deadline, status = compute_deadline_and_status(pol, today=today)

        row = {
            "정책명": pol.get(FIELD_MAP["name"], "(이름 미상)"),
            "신규": "신규" if r.get("is_new") else "",
            "분류": pol.get(FIELD_MAP["category_large"], ""),
            "상태": status,
            "마감일": deadline,
            "신청링크": link,
            "자동판정": verdict,
            "판정사유": r.get("verdict_reason", ""),
            "설명": pol.get(FIELD_MAP["desc"], ""),
        }

        if deadline and link and status == "신청가능":
            ready.append(row)
        else:
            missing = []
            if status == "신청예정":
                missing.append("신청예정 — 아직 접수 시작 전")
            elif status == "마감":
                missing.append("마감된 공고")
            else:
                if not deadline:
                    missing.append("마감일 파싱 실패")
                if not link:
                    missing.append("신청링크 없음")
            row["누락사유"] = ", ".join(missing)
            needs_review.append(row)

    ready.sort(key=lambda x: x["마감일"])
    return ready, needs_review


def _write_sheet(ws, rows, columns, warn_col=None):
    for col, name in enumerate(columns, start=1):
        c = ws.cell(row=1, column=col, value=name)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = BORDER

    for ri, row in enumerate(rows, start=2):
        for ci, name in enumerate(columns, start=1):
            v = row.get(name, "")
            if isinstance(v, date):
                v = v.strftime("%Y-%m-%d")
            cell = ws.cell(row=ri, column=ci, value=v)
            cell.font = NORMAL_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=(name in ("설명", "판정사유", "누락사유")))
            if warn_col and name == warn_col:
                cell.fill = WARN_FILL

    widths = {"정책명": 30, "신규": 8, "분류": 12, "상태": 10, "마감일": 12, "신청링크": 40,
              "자동판정": 10, "판정사유": 40, "설명": 45, "누락사유": 30}
    for col, name in enumerate(columns, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = widths.get(name, 18)
    ws.freeze_panes = "A2"


def save_xlsx(results: list, out_path: str, generated_at: datetime = None, source_counts: dict = None,
              suppressed_count: int = 0):
    ready, needs_review = build_rows(results)
    upcoming = sum(1 for r in needs_review if r["상태"] == "신청예정")

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "매칭결과"
    _write_sheet(
        ws1, ready,
        ["정책명", "신규", "분류", "상태", "마감일", "신청링크", "자동판정", "판정사유", "설명"],
    )

    ws2 = wb.create_sheet("확인필요")
    _write_sheet(
        ws2, needs_review,
        ["정책명", "신규", "분류", "상태", "마감일", "신청링크", "누락사유", "자동판정", "판정사유"],
        warn_col="누락사유",
    )

    ws0 = wb.create_sheet("안내", 0)
    ws0.sheet_view.showGridLines = False
    ws0.column_dimensions["A"].width = 90
    gen = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    lines = [
        "청년정책 매칭 결과",
        f"생성 시각: {gen}",
        "",
        f"매칭결과 시트: {len(ready)}건 — 마감일/신청링크가 모두 확인되고 지금 신청 가능한 항목만.",
        f"확인필요 시트: {len(needs_review)}건 — 그중 {upcoming}건은 '신청예정'(아직 접수 시작 전이라 "
        f"마감일/링크가 없는 것뿐, 파싱 실패 아님). 나머지는 마감됐거나 API 응답에서 마감일/링크를 "
        f"못 뽑은 것 — 원문 확인 필요.",
    ]
    if suppressed_count:
        lines.append(
            f"반복 노출 생략: {suppressed_count}건 — 상태 변화 없이 지난 회차에 이미 보여준 항목이라 "
            f"이번엔 뺐습니다(상태가 더 액션 가능한 쪽으로 바뀌면 다시 뜹니다)."
        )
    if source_counts:
        lines.append("")
        lines.append("소스별 수집 현황:")
        for name, info in source_counts.items():
            if isinstance(info, dict):
                if info.get("error"):
                    lines.append(f"  - {name}: 오류 — {info['error']} (이번 회차 결과에서 제외됨)")
                else:
                    dedup_note = f", 중복 제거 {info['deduped']}건" if info.get("deduped") else ""
                    lines.append(f"  - {name}: {info.get('count', 0)}건{dedup_note}")
            else:
                lines.append(f"  - {name}: {info}건")
    for i, line in enumerate(lines, start=1):
        c = ws0.cell(row=i, column=1, value=line)
        c.font = Font(name=FONT, bold=(i == 1), size=14 if i == 1 else 11)

    wb.save(out_path)
    return ready, needs_review

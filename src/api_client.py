"""온통청년(youthcenter.go.kr) Open API 클라이언트.

공식 문서: https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiDoc

주의(2026-08-03 실측): 문서의 "요청 예시" 섹션에 적힌
`/opi/youthPlcyList.do?openApiVlak=...&pageIndex=...&display=...`는 죽은 구버전
엔드포인트다 — 실제로 호출하면 302로 `:8080`(내부망 포트로 추정)에 리다이렉트되며
응답이 안 온다. 진짜 살아있는 엔드포인트는 같은 문서 페이지의 "테스트베스트" 버튼이
호출하는 JS(`apiReqExam` 함수, "정책" 항목 `case '86'`)에 정의돼 있었다:

  https://www.youthcenter.go.kr/go/ythip/getPlcy
  ?apiKeyNm=...&pageNum=1&pageSize=10&rtnType=json

이 URL로 실제 데이터(2026-08-03 기준 전체 2,693건)가 정상적으로 왔다 — 문서
페이지가 API 이전 후 예시 섹션만 업데이트가 안 된 것으로 보인다. 응답 필드는
`matcher.py`의 FIELD_MAP/CODE_MAPS 추정과 거의 다 일치했다(다른 점은 이미 반영함:
jobCd/plcyMajorCd가 콤마로 구분된 다중값일 수 있음, bizPrdSeCd는 CODE_MAPS 대문자
표기가 실제와 다름 — 전부 matcher.py에 수정 반영).
"""

import re
import time

import requests

BASE_URL = "https://www.youthcenter.go.kr/go/ythip/getPlcy"

_KEY_PARAM_RE = re.compile(r"(apiKeyNm=)[^&\s'\"]+", re.IGNORECASE)


def mask_secrets(text) -> str:
    """오류 메시지에 요청 URL이 그대로 들어있는 경우(requests 예외가 흔히 그렇다)를
    대비해 apiKeyNm= 뒤의 키 값을 가린다. 로그와 결과 엑셀의 '안내' 시트에 키가
    그대로 찍히는 것을 막기 위함."""
    return _KEY_PARAM_RE.sub(r"\1***", str(text))


class YouthCenterClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: int = 15):
        self.api_key = api_key
        self.base_url = base_url or BASE_URL
        self.timeout = timeout

    def fetch_policies(self, page_index: int = 1, display: int = 100, **extra_params):
        params = {
            "apiKeyNm": self.api_key,
            "pageNum": page_index,
            "pageSize": display,
            "rtnType": "json",
        }
        params.update(extra_params)
        resp = requests.get(self.base_url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    def fetch_all(self, display: int = 100, max_pages: int = 50, max_retries: int = 2,
                  retry_delay: float = 2.0, **extra_params):
        """모든 페이지를 순회하며 정책 리스트를 모은다.

        페이지 하나가 일시적으로 실패해도(실측 확인됨 — 전체 2,693건 중 특정
        페이지가 400을 내다가 곧바로 재시도하면 정상 응답한 사례가 있었음) 바로
        전체를 포기하지 않고 몇 번 재시도한다. 그래도 안 되면, 지금까지 모은
        페이지만이라도 반환한다(전부 버리는 것보다 부분 결과라도 있는 게 낫다) —
        단, 콘솔에 몇 페이지에서 끊겼는지 명확히 남긴다."""
        all_items = []
        for page in range(1, max_pages + 1):
            items = None
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    resp = self.fetch_policies(page_index=page, display=display, **extra_params)
                    items = _extract_items(resp.json())
                    break
                except Exception as e:
                    last_exc = e
                    if attempt < max_retries:
                        time.sleep(retry_delay)

            if items is None:
                print(f"[안내] 온통청년 {page}페이지 조회가 {max_retries + 1}번 다 실패했습니다"
                      f"({mask_secrets(last_exc)}). 지금까지 모은 {len(all_items)}건까지만 쓰고 중단합니다.")
                break

            if not items:
                break
            all_items.extend(items)
            if len(items) < display:
                break
        return all_items


def _extract_items(data):
    """응답은 {"resultCode":200,"result":{"pagging":{...},"youthPolicyList":[...]}}
    형태로 확인됐다(2026-08-03 실측). 혹시 모를 변형에 대비해 몇 가지 흔한 래핑
    패턴도 폴백으로 같이 시도한다."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key_path in (
            ("result", "youthPolicyList"),
            ("result", "list"),
            ("youthPolicyList",),
            ("list",),
            ("items",),
        ):
            node = data
            ok = True
            for k in key_path:
                if isinstance(node, dict) and k in node:
                    node = node[k]
                else:
                    ok = False
                    break
            if ok and isinstance(node, list):
                return node
    return []

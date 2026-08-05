"""api_client.py의 재시도/부분성공 로직 확인용 테스트. 실제 네트워크 호출 없이
`requests.get`을 가짜로 바꿔치기해서 검증한다. pytest 없이도
python -m tests.test_api_client 로 실행 가능."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import api_client  # noqa: E402
from src.api_client import YouthCenterClient  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _page(items):
    return {"result": {"youthPolicyList": items}}


def _install_fake_get(monkeypatch_calls, script):
    """`script`: 호출될 때마다 하나씩 꺼내 쓸 (예외 또는 dict) 리스트.
    dict면 _FakeResponse로 감싸서 반환, Exception 인스턴스면 그대로 던진다."""
    def fake_get(url, params=None, timeout=None):
        monkeypatch_calls.append(params)
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)
    return fake_get


def test_fetch_all_stops_when_page_returns_fewer_than_requested():
    calls = []
    script = [_page([{"plcyNo": "1"}, {"plcyNo": "2"}])]
    api_client.requests.get = _install_fake_get(calls, script)
    client = YouthCenterClient(api_key="k")
    items = client.fetch_all(display=10)
    assert [i["plcyNo"] for i in items] == ["1", "2"]
    assert len(calls) == 1


def test_fetch_all_retries_transient_failure_and_recovers():
    calls = []
    script = [
        requests_exc(),  # 1번째 시도: 실패
        _page([{"plcyNo": "A"}] * 10),  # 2번째 시도(재시도): 성공, 다음 페이지 있음
        _page([{"plcyNo": "B"}]),  # 2페이지: 마지막 페이지
    ]
    api_client.requests.get = _install_fake_get(calls, script)
    client = YouthCenterClient(api_key="k")
    items = client.fetch_all(display=10, max_retries=2, retry_delay=0)
    assert [i["plcyNo"] for i in items] == ["A"] * 10 + ["B"]


def test_fetch_all_returns_partial_results_when_page_permanently_fails():
    calls = []
    script = [
        _page([{"plcyNo": "A"}] * 10),  # 1페이지: 성공, 다음 페이지 있음(꽉 찼으니)
        requests_exc(), requests_exc(), requests_exc(),  # 2페이지: 재시도 다 실패(max_retries=2 -> 총 3번 시도)
    ]
    api_client.requests.get = _install_fake_get(calls, script)
    client = YouthCenterClient(api_key="k")
    items = client.fetch_all(display=10, max_retries=2, retry_delay=0)
    # 2페이지는 통째로 실패했어도 1페이지에서 이미 모은 건 살아있어야 한다.
    assert [i["plcyNo"] for i in items] == ["A"] * 10


def requests_exc():
    return RuntimeError("boom")


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"OK: {fn.__name__}")
    print(f"{len(fns)}개 테스트 통과")

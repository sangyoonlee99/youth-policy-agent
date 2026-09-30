"""llm_check.py의 코드펜스 파싱/재시도 로직 확인용 테스트. 실제 API 호출 없이
anthropic.Anthropic을 가짜로 바꿔치기해서 검증한다.
pytest 없이도 python -m tests.test_llm_check 로 실행 가능."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import llm_check  # noqa: E402


class _FakeContentBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeContentBlock(text)]


def _install_fake_client(monkeypatch, script):
    """호출될 때마다 script에서 하나씩 꺼내 쓴다: 문자열이면 성공 응답 텍스트로,
    Exception이면 그대로 던진다."""
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            item = script.pop(0)
            if isinstance(item, Exception):
                raise item
            return _FakeResponse(item)

    class FakeClient:
        def __init__(self, *a, **kw):
            self.messages = FakeMessages()

    monkeypatch.setattr(llm_check.anthropic, "Anthropic", FakeClient)
    return calls


def _with_api_key(monkeypatch):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"


def test_strip_code_fence_removes_json_fence():
    raw = '```json\n{"verdict": "eligible", "reason": "ok"}\n```'
    assert llm_check._strip_code_fence(raw) == '{"verdict": "eligible", "reason": "ok"}'


def test_strip_code_fence_leaves_plain_json_untouched():
    raw = '{"verdict": "excluded", "reason": "nope"}'
    assert llm_check._strip_code_fence(raw) == raw


def test_check_free_text_eligibility_parses_fenced_json_response(monkeypatch):
    _with_api_key(monkeypatch)
    _install_fake_client(monkeypatch, ['```json\n{"verdict": "eligible", "reason": "만족"}\n```'])
    result = llm_check.check_free_text_eligibility("프로필", "만 19-34세")
    assert result == {"verdict": "eligible", "reason": "만족"}


def test_check_free_text_eligibility_retries_transient_failure_then_succeeds(monkeypatch):
    _with_api_key(monkeypatch)
    calls = _install_fake_client(monkeypatch, [
        RuntimeError("connection reset"),
        '{"verdict": "excluded", "reason": "나이 초과"}',
    ])
    result = llm_check.check_free_text_eligibility("프로필", "조건", retry_delay=0)
    assert result == {"verdict": "excluded", "reason": "나이 초과"}
    assert len(calls) == 2


def test_check_free_text_eligibility_gives_up_after_max_retries(monkeypatch):
    _with_api_key(monkeypatch)
    _install_fake_client(monkeypatch, [
        RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom"),
    ])
    result = llm_check.check_free_text_eligibility("프로필", "조건", max_retries=2, retry_delay=0)
    assert result["verdict"] == "not_sure"
    assert "LLM 호출 실패" in result["reason"]


if __name__ == "__main__":
    class _FakeMonkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)

    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        mp = _FakeMonkeypatch()
        try:
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(mp)
            else:
                fn()
        finally:
            mp.undo()
        print(f"OK: {fn.__name__}")
    print(f"{len(fns)}개 테스트 통과")

"""main.py의 '모든 소스 실패 시 결과 파일 보존' 동작 확인용 테스트.
pytest 없이도 python -m tests.test_main 으로 실행 가능."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import main as main_mod  # noqa: E402
from src.api_client import mask_secrets  # noqa: E402

SENTINEL = b"previous run's real output - must not be overwritten"


def _write_config(tmp_dir: Path, output_path: Path) -> Path:
    cfg_path = tmp_dir / "config.yaml"
    cfg_path.write_text(
        f"""
api:
  api_key: "unused"
profile:
  birth_year: 2000
  region_code: "11"
output:
  path: "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )
    return cfg_path


def test_all_sources_failed_does_not_overwrite_existing_output(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        out_path = tmp_dir / "matched_policies.xlsx"
        out_path.write_bytes(SENTINEL)
        cfg_path = _write_config(tmp_dir, out_path)

        monkeypatch.setattr(
            main_mod, "fetch_all_sources",
            lambda cfg, profile, mock: ([], {"온통청년": {"error": "network down"}}),
        )

        def _fail_if_called(*a, **kw):
            raise AssertionError("save_xlsx must not be called when every source failed")
        monkeypatch.setattr(main_mod, "save_xlsx", _fail_if_called)

        monkeypatch.setattr(sys, "argv", ["main.py", "--config", str(cfg_path)])

        raised = False
        try:
            main_mod.main()
        except SystemExit as e:
            raised = True
            assert e.code == 1
        assert raised, "모든 소스가 실패하면 SystemExit(1)로 끝나야 한다"
        assert out_path.read_bytes() == SENTINEL, "기존 결과 파일이 그대로 남아있어야 한다"


def test_mask_secrets_hides_api_key_in_error_text():
    msg = ("HTTPSConnectionPool: Max retries exceeded with url: "
           "/go/ythip/getPlcy?apiKeyNm=super-secret-key-12345&pageNum=1")
    masked = mask_secrets(msg)
    assert "super-secret-key-12345" not in masked
    assert "apiKeyNm=***" in masked


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

import sys

import pytest

from gaia import cli


def test_prompt_select_number_waits_for_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        cli,
        "_prompt_select_curses",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("force manual prompt")),
    )
    keys = iter(["1", "\n"])
    calls = 0

    def fake_read_key() -> str:
        nonlocal calls
        calls += 1
        return next(keys)

    monkeypatch.setattr(cli, "_read_key", fake_read_key)

    selected = cli._prompt_select("Telegram 원격 제어를 사용하시겠어요?", ("telegram", "no"), default="no")

    assert selected == "telegram"
    assert calls == 2

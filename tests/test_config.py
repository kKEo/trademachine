import pytest

from tradesignals.config import env_bool


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        (" true ", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_env_bool_accepts_common_spellings(monkeypatch, raw, expected):
    monkeypatch.setenv("FLAG", raw)
    assert env_bool("FLAG", not expected) is expected


@pytest.mark.parametrize("raw", ["", "   "])
def test_env_bool_empty_uses_default(monkeypatch, raw):
    monkeypatch.setenv("FLAG", raw)
    assert env_bool("FLAG", True) is True
    assert env_bool("FLAG", False) is False


def test_env_bool_unset_uses_default(monkeypatch):
    monkeypatch.delenv("FLAG", raising=False)
    assert env_bool("FLAG", True) is True


@pytest.mark.parametrize("raw", ["flase", "2", "paper", "live"])
def test_env_bool_rejects_anything_else_instead_of_failing_open(monkeypatch, raw):
    monkeypatch.setenv("FLAG", raw)
    with pytest.raises(ValueError):
        env_bool("FLAG", True)

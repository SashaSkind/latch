from src.auth import authenticate


def test_accepts_valid_token() -> None:
    assert authenticate("demo-token")


def test_rejects_invalid_token() -> None:
    assert not authenticate("invalid")

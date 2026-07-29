from app.services.ai_service.client import _normalized_timeout


def test_ai_timeout_defaults_to_five_minutes() -> None:
    assert _normalized_timeout(None) == 300.0
    assert _normalized_timeout(0) == 300.0


def test_ai_timeout_allows_and_caps_at_five_minutes() -> None:
    assert _normalized_timeout(60) == 60.0
    assert _normalized_timeout(300) == 300.0
    assert _normalized_timeout(600) == 300.0

from app.middleware.rate_limit import (
    is_loopback_client,
    is_rate_limit_exempt_path,
    should_skip_rate_limit,
)


def test_rate_limit_helpers_detect_loopback_clients():
    assert is_loopback_client("127.0.0.1")
    assert is_loopback_client("::1")
    assert is_loopback_client("::ffff:127.0.0.1")
    assert not is_loopback_client("10.0.0.25")


def test_should_skip_rate_limit_for_exempt_docs_path(monkeypatch):
    monkeypatch.setattr("app.middleware.rate_limit.settings.RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("app.middleware.rate_limit.settings.ENVIRONMENT", "production")
    monkeypatch.setattr(
        "app.middleware.rate_limit.settings.RATE_LIMIT_SKIP_LOCALHOST_IN_DEVELOPMENT",
        True,
    )

    assert is_rate_limit_exempt_path("/docs")
    assert should_skip_rate_limit(path="/docs", client_ip="203.0.113.10")


def test_should_skip_rate_limit_for_localhost_in_development(monkeypatch):
    monkeypatch.setattr("app.middleware.rate_limit.settings.RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("app.middleware.rate_limit.settings.ENVIRONMENT", "development")
    monkeypatch.setattr(
        "app.middleware.rate_limit.settings.RATE_LIMIT_SKIP_LOCALHOST_IN_DEVELOPMENT",
        True,
    )

    assert should_skip_rate_limit(path="/documents", client_ip="127.0.0.1")
    assert not should_skip_rate_limit(path="/documents", client_ip="203.0.113.10")


def test_should_not_skip_remote_clients_in_production(monkeypatch):
    monkeypatch.setattr("app.middleware.rate_limit.settings.RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("app.middleware.rate_limit.settings.ENVIRONMENT", "production")
    monkeypatch.setattr(
        "app.middleware.rate_limit.settings.RATE_LIMIT_SKIP_LOCALHOST_IN_DEVELOPMENT",
        True,
    )

    assert not should_skip_rate_limit(path="/documents", client_ip="127.0.0.1")
    assert not should_skip_rate_limit(path="/documents", client_ip="203.0.113.10")

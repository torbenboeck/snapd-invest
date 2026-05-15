"""Tests for the broker exception hierarchy."""

from __future__ import annotations

import pytest

from snapd_invest.broker import (
    BrokerAuthError,
    BrokerError,
    BrokerHttpError,
    BrokerTimeoutError,
)


class TestBrokerErrorHierarchy:
    def test_all_subclasses_are_broker_errors(self) -> None:
        assert issubclass(BrokerAuthError, BrokerError)
        assert issubclass(BrokerHttpError, BrokerError)
        assert issubclass(BrokerTimeoutError, BrokerError)

    def test_http_error_carries_status_and_body(self) -> None:
        err = BrokerHttpError(status_code=503, body="upstream timeout")
        assert err.status_code == 503
        assert err.body == "upstream timeout"
        assert "503" in str(err)

    def test_auth_error_can_carry_a_reason(self) -> None:
        err = BrokerAuthError("refresh token expired")
        assert "refresh token expired" in str(err)

    def test_can_catch_any_subclass_as_broker_error(self) -> None:
        with pytest.raises(BrokerError):
            raise BrokerHttpError(status_code=400, body="bad request")

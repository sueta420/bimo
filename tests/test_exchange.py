from exchange import BybitClient


def test_call_with_retry_retries_timeout_once():
    client = BybitClient.__new__(BybitClient)
    client.cfg = {"bybit_read_retries": 2, "bybit_read_retry_delay_ms": 0}
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Read timed out")
        return {"ok": True}

    out = client._call_with_retry(flaky)

    assert out == {"ok": True}
    assert calls["n"] == 2


def test_call_with_retry_does_not_retry_non_timeout():
    client = BybitClient.__new__(BybitClient)
    client.cfg = {"bybit_read_retries": 2, "bybit_read_retry_delay_ms": 0}
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise RuntimeError("invalid symbol")

    try:
        client._call_with_retry(bad)
        assert False, "expected RuntimeError"
    except RuntimeError as ex:
        assert "invalid symbol" in str(ex)
    assert calls["n"] == 1


def test_retryable_read_error_detects_dns_and_network_transients():
    client = BybitClient.__new__(BybitClient)

    assert client._is_retryable_read_error(
        RuntimeError("Failed to resolve 'api.bybit.com' ([Errno 8] nodename nor servname provided, or not known)")
    )
    assert client._is_retryable_read_error(RuntimeError("Too many visits. Exceeded the API Rate Limit. (ErrCode: 10006)"))
    assert not client._is_retryable_read_error(RuntimeError("invalid symbol"))

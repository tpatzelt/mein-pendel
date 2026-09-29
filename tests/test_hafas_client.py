from __future__ import annotations

import datetime as dt

import httpx
import pytest

from pendel.hafas import BERLIN, HafasClient, HafasError, cache_key

UTC = dt.timezone.utc


def _clock(start: float = 0.0):
    box = {"t": start}

    def now() -> float:
        return box["t"]

    def advance(delta: float) -> None:
        box["t"] += delta

    return now, advance


def _sleeps():
    calls: list[float] = []

    def sleep(seconds: float) -> None:
        calls.append(seconds)

    return calls, sleep


def _client(transport: httpx.MockTransport, **kwargs) -> HafasClient:
    http_client = httpx.Client(transport=transport)
    return HafasClient(http_client, **kwargs)


def _json_handler(payload_by_call=None, *, default=None):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if payload_by_call is not None and len(calls) <= len(payload_by_call):
            return payload_by_call[len(calls) - 1]
        assert default is not None, "handler called more times than expected"
        return default

    return handler, calls


# --- per-endpoint param tests (required by T-0013 reviewers) ----------------


def test_locations_sends_query_param():
    handler, calls = _json_handler(default=httpx.Response(200, json=[{"id": "900000100001"}]))
    client = _client(httpx.MockTransport(handler))

    result = client.locations("Alexanderplatz")

    assert result == [{"id": "900000100001"}]
    assert calls[0].url.params["query"] == "Alexanderplatz"


def test_departures_sends_when_and_duration_params():
    handler, calls = _json_handler(default=httpx.Response(200, json=[]))
    client = _client(httpx.MockTransport(handler))
    when = dt.datetime(2026, 1, 5, 7, 30, tzinfo=BERLIN)

    client.departures("900000100001", when, duration=30)

    request = calls[0]
    assert request.url.path == "/stops/900000100001/departures"
    assert request.url.params["when"] == when.isoformat()
    assert request.url.params["duration"] == "30"


def test_journeys_sends_from_to_departure_params():
    handler, calls = _json_handler(default=httpx.Response(200, json={}))
    client = _client(httpx.MockTransport(handler))
    departure = dt.datetime(2026, 1, 5, 7, 30, tzinfo=UTC)

    client.journeys("900000100001", "900000200002", departure)

    request = calls[0]
    assert request.url.path == "/journeys"
    assert request.url.params["from"] == "900000100001"
    assert request.url.params["to"] == "900000200002"
    assert request.url.params["departure"] == departure.astimezone(BERLIN).isoformat()


def test_departures_rejects_naive_datetime():
    client = _client(httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    with pytest.raises(ValueError):
        client.departures("900000100001", dt.datetime(2026, 1, 5, 7, 30), duration=30)


def test_journeys_rejects_naive_datetime():
    client = _client(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError):
        client.journeys("A", "B", dt.datetime(2026, 1, 5, 7, 30))


def test_trip_percent_encodes_trip_id_in_path():
    handler, calls = _json_handler(default=httpx.Response(200, json={"id": "1|2|3"}))
    client = _client(httpx.MockTransport(handler))

    client.trip("1|2#3 4")

    assert calls[0].url.raw_path == b"/trips/1%7C2%233%204"


# --- cache: TTL, purge, bound, key order -------------------------------------


def test_ttl_cache_hits_within_ttl_and_refetches_after_expiry():
    handler, calls = _json_handler(default=httpx.Response(200, json=[{"line": "S41"}]))
    now, advance = _clock()
    client = _client(httpx.MockTransport(handler), clock=now)
    when = dt.datetime(2026, 1, 5, 7, 30, tzinfo=BERLIN)

    client.departures("A", when, duration=30)
    assert len(calls) == 1

    advance(9)
    client.departures("A", when, duration=30)
    assert len(calls) == 1

    advance(25)  # total 34s: past the default 30s ttl
    client.departures("A", when, duration=30)
    assert len(calls) == 2
    assert len(client._cache) == 1


def test_expired_entry_with_different_key_is_purged_on_insert():
    handler, calls = _json_handler(default=httpx.Response(200, json=[]))
    now, advance = _clock()
    client = _client(httpx.MockTransport(handler), clock=now)
    when = dt.datetime(2026, 1, 5, 7, 30, tzinfo=BERLIN)

    client.departures("A", when, duration=30)
    key_a = cache_key("/stops/A/departures", {"when": when.isoformat(), "duration": 30})
    assert key_a in client._cache

    advance(31)  # past the default ttl
    client.departures("B", when, duration=30)

    assert key_a not in client._cache
    assert len(client._cache) == 1


def test_cache_is_bounded_at_max_entries():
    handler, calls = _json_handler(default=httpx.Response(200, json=[]))
    now, _advance = _clock()
    client = _client(httpx.MockTransport(handler), clock=now)

    for i in range(1000):
        when = dt.datetime(2026, 1, 5, 7, 30, tzinfo=BERLIN) + dt.timedelta(seconds=i)
        client.departures("A", when, duration=30)

    assert len(client._cache) <= 256


def test_cache_key_does_not_depend_on_param_order():
    a = cache_key("/journeys", {"from": "A", "to": "B", "departure": "x"})
    b = cache_key("/journeys", {"departure": "x", "to": "B", "from": "A"})
    assert a == b


def test_cached_value_is_deep_copied_so_callers_cannot_mutate_cache():
    handler, calls = _json_handler(default=httpx.Response(200, json=[{"line": "S41"}]))
    client = _client(httpx.MockTransport(handler))
    when = dt.datetime(2026, 1, 5, 7, 30, tzinfo=BERLIN)

    first = client.departures("A", when, duration=30)
    first[0]["line"] = "MUTATED"
    second = client.departures("A", when, duration=30)

    assert second == [{"line": "S41"}]
    assert len(calls) == 1


# --- backoff on 429/503 ------------------------------------------------------


def test_retry_after_numeric_seconds_used_as_delay():
    responses = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"ok": True}),
    ]
    handler, calls = _json_handler(payload_by_call=responses)
    sleeps, sleep = _sleeps()
    client = _client(httpx.MockTransport(handler), sleep=sleep)

    result = client.locations("x")

    assert result == {"ok": True}
    assert sleeps == [3.0]
    assert len(calls) == 2


def test_retry_after_seconds_capped_at_ten():
    responses = [
        httpx.Response(503, headers={"Retry-After": "20"}),
        httpx.Response(200, json={"ok": True}),
    ]
    handler, calls = _json_handler(payload_by_call=responses)
    sleeps, sleep = _sleeps()
    client = _client(httpx.MockTransport(handler), sleep=sleep)

    client.locations("x")

    assert sleeps == [10.0]
    assert len(calls) == 2


@pytest.mark.parametrize(
    "retry_after",
    ["Wed, 21 Oct 2026 07:28:00 GMT", "-5", "nan", "inf", "not-a-number"],
)
def test_retry_after_invalid_values_fall_back_to_fixed_delay(retry_after):
    responses = [
        httpx.Response(429, headers={"Retry-After": retry_after}),
        httpx.Response(200, json={"ok": True}),
    ]
    handler, calls = _json_handler(payload_by_call=responses)
    sleeps, sleep = _sleeps()
    client = _client(httpx.MockTransport(handler), sleep=sleep)

    client.locations("x")

    assert sleeps == [0.5]
    assert len(calls) == 2


def test_retry_uses_fixed_delays_when_no_retry_after_header():
    responses = [
        httpx.Response(429),
        httpx.Response(503),
        httpx.Response(429),
        httpx.Response(200, json={"ok": True}),
    ]
    handler, calls = _json_handler(payload_by_call=responses)
    sleeps, sleep = _sleeps()
    client = _client(httpx.MockTransport(handler), sleep=sleep)

    client.locations("x")

    assert sleeps == [0.5, 1.0, 2.0]
    assert len(calls) == 4


def test_retries_exhausted_raises_hafas_error():
    responses = [httpx.Response(429) for _ in range(10)]
    handler, calls = _json_handler(payload_by_call=responses)
    sleeps, sleep = _sleeps()
    client = _client(httpx.MockTransport(handler), sleep=sleep)

    with pytest.raises(HafasError):
        client.locations("x")

    assert len(calls) == 4  # 1 initial attempt + 3 retries
    assert sleeps == [0.5, 1.0, 2.0]


def test_other_4xx_raises_immediately_without_retry():
    handler, calls = _json_handler(default=httpx.Response(404))
    sleeps, sleep = _sleeps()
    client = _client(httpx.MockTransport(handler), sleep=sleep)

    with pytest.raises(HafasError):
        client.locations("x")

    assert len(calls) == 1
    assert sleeps == []


def test_other_5xx_raises_immediately_without_retry():
    handler, calls = _json_handler(default=httpx.Response(500))
    sleeps, sleep = _sleeps()
    client = _client(httpx.MockTransport(handler), sleep=sleep)

    with pytest.raises(HafasError):
        client.locations("x")

    assert len(calls) == 1
    assert sleeps == []


def test_transport_error_wrapped_in_hafas_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = _client(httpx.MockTransport(handler))

    with pytest.raises(HafasError):
        client.locations("x")


def test_invalid_json_wrapped_in_hafas_error():
    handler, calls = _json_handler(default=httpx.Response(200, content=b"not json"))
    client = _client(httpx.MockTransport(handler))

    with pytest.raises(HafasError):
        client.locations("x")

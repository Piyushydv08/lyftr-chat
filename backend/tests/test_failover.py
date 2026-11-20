"""Key-pool failover: the X-Simulate-Failure hook (section 4) and real provider failures."""

from __future__ import annotations

import pytest

from app.llm.simulation import SimulationHeaderError, parse_simulation_header
from tests.conftest import new_session

ALL_UNAVAILABLE = {"detail": "all providers unavailable"}


def chat(client, session_id, message, simulate=None):
    headers = {"X-Simulate-Failure": simulate} if simulate is not None else {}
    return client.post("/chat", json={"session_id": session_id, "message": message}, headers=headers)


# -- the contract table ------------------------------------------------------------------------------
@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_key_1_failure_falls_over_to_key_2(client, fake_provider, status):
    res = chat(client, new_session(), f"failover {status}", simulate=f"key_1={status}")
    assert res.status_code == 200
    meta = res.json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    # a real answer, not an error wrapped in a 200
    assert res.json()["reply"].startswith("You said: failover")
    assert fake_provider.calls_for("good-1") == 0  # key_1 never reached the provider
    assert fake_provider.calls_for("good-2") == 1


def test_all_keys_failing_is_503_and_not_stored(client, fake_provider):
    sid = new_session()
    res = chat(client, sid, "will fail", simulate="*=429")
    assert res.status_code == 503
    assert res.json() == ALL_UNAVAILABLE
    assert client.get(f"/sessions/{sid}/messages").status_code == 404
    assert fake_provider.calls == []


def test_failed_request_does_not_touch_existing_history(client):
    sid = new_session()
    chat(client, sid, "first, ok")
    assert chat(client, sid, "second, fails", simulate="key_1=429,key_2=500").status_code == 503
    msgs = client.get(f"/sessions/{sid}/messages").json()["messages"]
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["first, ok"]
    # and the conversation continues normally afterwards
    nxt = chat(client, sid, "third, ok").json()
    assert "user turns: 2" in nxt["reply"]


def test_simulation_is_scoped_to_one_request(client):
    assert chat(client, new_session(), "q1", simulate="key_1=429").json()["metadata"]["key_id"] == "key_2"
    meta = chat(client, new_session(), "q2").json()["metadata"]
    assert meta["key_id"] == "key_1"  # no cooldown / leakage from the previous request
    assert meta["fallback_attempts"] == 0


def test_simulated_request_exercises_failover_even_when_cached(client):
    msg = "already cached question"
    chat(client, new_session(), msg)
    assert chat(client, new_session(), msg).json()["metadata"]["resolution_source"] == "LLM_CACHE"
    simulated = chat(client, new_session(), msg, simulate="key_1=429").json()["metadata"]
    assert simulated["resolution_source"] == "LIVE_INFERENCE"
    assert simulated["key_id"] == "key_2"
    assert chat(client, new_session(), msg, simulate="*=429").status_code == 503


def test_three_keys_fail_over_in_order(make_client):
    client = make_client(keys="good-1,good-2,good-3")
    meta = chat(client, new_session(), "three keys", simulate="key_1=429,key_2=503").json()["metadata"]
    assert meta["key_id"] == "key_3"
    assert meta["fallback_attempts"] == 2
    assert chat(client, new_session(), "all three", simulate="*=500").status_code == 503


def test_header_ignored_when_simulation_disabled(make_client):
    client = make_client(ENABLE_FAILURE_SIMULATION="false")
    res = chat(client, new_session(), "not simulated", simulate="*=429")
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_1"


@pytest.mark.parametrize("header", ["key_1", "key_1=418", "key_1=abc", "foo=429", ","])
def test_malformed_simulation_header_is_422(client, header):
    res = chat(client, new_session(), "bad header", simulate=header)
    assert res.status_code == 422
    assert "X-Simulate-Failure" in res.json()["detail"]


# -- real provider failures (no simulation): LiteLLM maps HTTP errors and the Router falls over ---------
@pytest.mark.parametrize("bad_key", ["fail429-quota", "fail500-x", "fail502-x", "fail503-x", "fail401-revoked"])
def test_real_provider_error_falls_over(make_client, fake_provider, bad_key):
    client = make_client(keys=f"{bad_key},good-2")
    res = chat(client, new_session(), f"real failure {bad_key}")
    assert res.status_code == 200
    meta = res.json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1
    assert fake_provider.calls_for(bad_key) == 1  # tried once, no slow retries on a dead key


def test_real_provider_all_keys_failing_is_clean_503(make_client):
    client = make_client(keys="fail429-a,fail503-b")
    sid = new_session()
    res = chat(client, sid, "everything is down")
    assert res.status_code == 503
    assert res.json() == ALL_UNAVAILABLE
    assert "Traceback" not in res.text
    assert client.get(f"/sessions/{sid}/messages").status_code == 404


def test_unreachable_provider_is_503(make_client):
    client = make_client(LLM_API_BASE="http://127.0.0.1:9/v1", LLM_TIMEOUT_SECONDS="2")
    res = chat(client, new_session(), "nobody home")
    assert res.status_code == 503
    assert res.json() == ALL_UNAVAILABLE


def test_no_keys_configured_is_503(make_client):
    res = chat(make_client(keys=""), new_session(), "hello?")
    assert res.status_code == 503
    assert res.json() == ALL_UNAVAILABLE


# -- header parser unit tests ------------------------------------------------------------------------------
KEYS = ["key_1", "key_2"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("key_1=429", {"key_1": 429}),
        ("key_1=429,key_2=500", {"key_1": 429, "key_2": 500}),
        (" key_1 = 502 , key_2=503 ", {"key_1": 502, "key_2": 503}),
        ("*=429", {"key_1": 429, "key_2": 429}),
        ("KEY_2=500", {"key_2": 500}),
        ("key_9=429", {}),  # well-formed but not configured -> no effect
    ],
)
def test_parse_simulation_header(raw, expected):
    assert parse_simulation_header(raw, KEYS) == expected


@pytest.mark.parametrize("raw", ["", "key_1", "key_1=404", "key_1=", "=429", "key_0=429", "k1=429"])
def test_parse_simulation_header_rejects_bad_input(raw):
    with pytest.raises(SimulationHeaderError):
        parse_simulation_header(raw, KEYS)

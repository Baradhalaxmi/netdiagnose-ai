"""AI agent guardrails, with a scripted fake LLM (no network, no API key).

Covers the roadmap's Week 7 tests: valid output accepted; unknown evidence
id rejected; provider error -> rules fallback; plus refusal, retry, tool
budget, caching and the disabled mode.
"""

import json
import re
from types import SimpleNamespace as NS

import pytest

from agent.llm_client import LLMError
from agent.runner import _echoable
from agent.schemas import band_for, validate
from backend.deps import get_llm
from backend.main import app
from tests.test_api import client, detector, device_id, features, inject  # noqa: F401  (fixtures)

pytestmark = pytest.mark.db


# ---- fake LLM ----------------------------------------------------------
def tool_use(name, args, i=1):
    return NS(type="tool_use", id=f"toolu_{name}_{i}", name=name, input=args)


def reply(*blocks, stop="tool_use"):
    return NS(stop_reason=stop, content=list(blocks), model="fake-model",
              usage=NS(input_tokens=100, output_tokens=50))


def evidence_ids(messages) -> list[int]:
    data = re.search(r"<incident_data>\n(.*)\n</incident_data>", messages[0]["content"], re.S)
    return [e["evidence_id"] for e in json.loads(data.group(1))["evidence"]]


def submission(ids, cause="Network congestion", confidence="high"):
    return {"diagnosis": cause, "confidence": confidence,
            "evidence": [{"statement": "Packet loss far above baseline", "evidence_id": ids[0]}],
            "alternatives": [{"cause": "Server overload", "why_less_likely": "CPU is normal"}],
            "recommended_investigation": ["Identify top talkers on the uplink"]}


class FakeLLM:
    """Each script step is a function(messages) -> response, or an exception to raise."""
    provider, model = "fake", "fake-model"

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def generate(self, system, messages, tools):
        self.calls.append([dict(m) if isinstance(m, dict) else m for m in messages])
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(messages)


def diagnose(client, inc_id, llm, **params):  # noqa: F811
    app.dependency_overrides[get_llm] = lambda: llm
    return client.post(f"/incidents/{inc_id}/diagnose", params={"use_agent": True, **params})


# ---- pure validation ---------------------------------------------------
def test_validate_accepts_good_output():
    diag, problems = validate(submission([5]), {5, 6}, 0.8)
    assert problems == [] and diag.confidence == "high"


def test_validate_rejects_unknown_evidence_id():
    diag, problems = validate(submission([99]), {5, 6}, 0.8)
    assert diag is None and "[99] do not exist" in problems[0]


def test_validate_rejects_overconfidence():
    # strongest rule scored 0.3 -> band "low" -> at most one band above: "medium"
    diag, problems = validate(submission([5], confidence="high"), {5}, 0.3)
    assert diag is None and "overstates" in problems[0]
    assert validate(submission([5], confidence="medium"), {5}, 0.3)[0] is not None


def test_validate_rejects_schema_violations():
    bad = submission([5]) | {"evidence": [], "confidence": "certain"}
    diag, problems = validate(bad, {5}, 0.8)
    assert diag is None and len(problems) == 2


def test_confidence_bands():
    assert [band_for(x) for x in (None, 0.2, 0.5, 0.7, 0.85)] == \
        ["low", "low", "medium", "medium-high", "high"]


def test_echo_drops_blocks_before_a_fallback_marker():
    content = [NS(type="thinking"), NS(type="text", text="a"), NS(type="tool_use"),
               NS(type="fallback"), NS(type="text", text="b")]
    assert [b.type for b in _echoable(content)] == ["text", "text"]


# ---- end to end through the API ----------------------------------------
def test_valid_ai_diagnosis_is_stored_next_to_rules(client, device_id):  # noqa: F811
    inc = inject(client, device_id, "congestion")
    llm = FakeLLM(
        lambda m: reply(tool_use("compare_baseline", {"metric": "packet_loss_pct"})),
        lambda m: reply(tool_use("submit_diagnosis", submission(evidence_ids(m)))),
    )
    r = diagnose(client, inc, llm)
    assert r.status_code == 200, r.text
    rules, ai = r.json()
    assert rules["source"] == "rules" and ai["source"] == "ai_agent"
    assert ai["probable_cause"] == "Network congestion" and ai["confidence"] == 0.85
    assert "AI diagnosis" in ai["report_markdown"]

    detail = client.get(f"/incidents/{inc}").json()
    assert set(ai["cited_evidence"]) <= {e["id"] for e in detail["evidence"]}
    # the tool result went back to the model as data
    tool_msg = llm.calls[1][-1]["content"][0]
    assert tool_msg["type"] == "tool_result" and "packet_loss_pct" in tool_msg["content"]


def test_unknown_evidence_id_gets_one_retry(client, device_id):  # noqa: F811
    inc = inject(client, device_id, "congestion")
    llm = FakeLLM(
        lambda m: reply(tool_use("submit_diagnosis", submission([999999]))),
        lambda m: reply(tool_use("submit_diagnosis", submission(evidence_ids(m)), i=2)),
    )
    r = diagnose(client, inc, llm)
    assert len(r.json()) == 2
    rejection = llm.calls[1][-1]["content"][0]
    assert rejection["is_error"] and "999999" in rejection["content"]


def test_unknown_evidence_id_twice_falls_back_to_rules(client, device_id):  # noqa: F811
    inc = inject(client, device_id, "congestion")
    bad = lambda m: reply(tool_use("submit_diagnosis", submission([999999])))  # noqa: E731
    r = diagnose(client, inc, FakeLLM(bad, bad))
    assert r.headers["X-Agent-Fallback"] == "true"
    assert r.headers["X-Agent-Fallback-Reason"] == "invalid_output"
    assert [d["source"] for d in r.json()] == ["rules"]


@pytest.mark.parametrize("error,reason", [(LLMError("unreachable"), "unreachable"),
                                          (LLMError("auth", "bad key"), "auth"),
                                          (LLMError("rate_limited"), "rate_limited")])
def test_provider_error_falls_back_to_rules(client, device_id, error, reason):  # noqa: F811
    inc = inject(client, device_id, "link_flap")
    r = diagnose(client, inc, FakeLLM(error))
    assert r.status_code == 200
    assert r.headers["X-Agent-Fallback-Reason"] == reason
    assert r.json()[0]["probable_cause"] == "Link failure / flapping"


def test_refusal_falls_back_to_rules(client, device_id):  # noqa: F811
    inc = inject(client, device_id, "ddos")
    r = diagnose(client, inc, FakeLLM(lambda m: reply(stop="refusal")))
    assert r.headers["X-Agent-Fallback-Reason"] == "refusal"


def test_missing_submission_is_nudged_once(client, device_id):  # noqa: F811
    inc = inject(client, device_id, "server_overload")
    text = lambda m: reply(NS(type="text", text="Looks like CPU."), stop="end_turn")  # noqa: E731
    ok = diagnose(client, inc, FakeLLM(
        text, lambda m: reply(tool_use("submit_diagnosis",
                                       submission(evidence_ids(m), "Server overload")))))
    assert ok.json()[-1]["probable_cause"] == "Server overload"

    inc2 = inject(client, device_id, "server_overload")
    r = diagnose(client, inc2, FakeLLM(text, text), force=True)
    assert r.headers["X-Agent-Fallback-Reason"] == "no_submission"


def test_tool_budget_is_enforced(client, device_id):  # noqa: F811
    from backend.config import get_settings

    cap = get_settings().agent_max_tool_calls
    inc = inject(client, device_id, "congestion")
    many = lambda m: reply(*[tool_use("get_metric_timeseries", {"metric": "latency_ms"}, i)  # noqa: E731
                             for i in range(cap + 3)])
    llm = FakeLLM(many, lambda m: reply(tool_use("submit_diagnosis", submission(evidence_ids(m)))))
    r = diagnose(client, inc, llm)
    assert len(r.json()) == 2
    results = llm.calls[1][-1]["content"]
    assert sum(not x.get("is_error") for x in results) == cap
    assert sum(bool(x.get("is_error")) for x in results) == 3


def test_ai_diagnosis_is_cached(client, device_id):  # noqa: F811
    inc = inject(client, device_id, "congestion")
    llm = FakeLLM(lambda m: reply(tool_use("submit_diagnosis", submission(evidence_ids(m)))))
    first = diagnose(client, inc, llm).json()[1]
    again = diagnose(client, inc, llm)                        # script is empty: must not call
    assert again.json()[1]["id"] == first["id"] and again.headers["X-Agent-Cached"] == "true"
    assert len(llm.calls) == 1


def test_agent_disabled_means_rules_only(client, device_id):  # noqa: F811
    inc = inject(client, device_id, "congestion")
    r = diagnose(client, inc, None)
    assert r.headers["X-Agent-Fallback-Reason"] == "disabled" and len(r.json()) == 1


def test_untrusted_data_is_fenced_and_tools_never_raise(client, device_id, db_session):  # noqa: F811
    from agent.tools import Toolbox

    inc = inject(client, device_id, "congestion")
    client.post(f"/incidents/{inc}/diagnose")
    box = Toolbox(db_session, inc)
    assert box.call("drop_tables", {})[1] is True
    assert box.call("compare_baseline", {"metric": "'; DROP TABLE x"})[1] is True
    text, err = box.call("get_device_status", {})
    assert not err and "ip_address" not in text           # no raw IPs sent to the LLM
    llm = FakeLLM(lambda m: reply(tool_use("submit_diagnosis", submission(evidence_ids(m)))))
    diagnose(client, inc, llm)
    assert llm.calls[0][0]["content"].count("<incident_data>") == 1

"""Agent control loop (docs/architecture.md section 10.3).

1. Make sure the rules diagnosis (and its evidence) exists.
2. Give the model the incident and the rule findings up front.
3. Let it call read-only tools, at most AGENT_MAX_TOOL_CALLS times.
4. It must finish by calling submit_diagnosis.
5. Validate: schema, every evidence id exists, confidence not overstated.
6. On a validation failure or a missing submission: one retry. After that,
   or on any provider error / refusal, fall back to the rules report.

The AI diagnosis is cached per (incident, evidence hash), so asking twice
does not call the model twice.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent import prompts
from agent.llm_client import LLMClient, LLMError
from agent.schemas import BAND_VALUE, SUBMIT_TOOL, validate
from agent.tools import TOOL_SPECS, Toolbox
from backend.config import Settings
from backend.db.models import Device, Diagnosis
from backend.services import diagnosis_service
from diagnosis.report import render_agent_report

log = logging.getLogger("netdiagnose.agent")


@dataclass
class AgentRun:
    rules: Diagnosis
    ai: Diagnosis | None
    fallback_reason: str | None = None
    tool_calls: int = 0
    tokens: int = 0
    latency_ms: int = 0
    cached: bool = False


def run_agent(session: Session, incident_id: int, settings: Settings,
              llm: LLMClient | None, force: bool = False) -> AgentRun:
    rules, _ = diagnosis_service.diagnose(session, incident_id, settings, force=force)
    if llm is None:
        return AgentRun(rules, None, "disabled")

    cached = session.scalars(select(Diagnosis).where(
        Diagnosis.incident_id == incident_id, Diagnosis.source == "ai_agent",
        Diagnosis.evidence_hash == rules.evidence_hash)).first()
    if cached is not None:
        return AgentRun(rules, cached, cached=True)

    box = Toolbox(session, incident_id)
    findings = box.get_rule_findings()
    evidence = {e["evidence_id"]: e for e in findings["evidence"]}
    top_conf = rules.confidence if rules.probable_cause != "Unclassified anomaly" else None
    hostname = session.get(Device, box.incident.device_id).hostname

    messages: list[dict] = [{"role": "user", "content": prompts.first_message(
        box.get_incident(), findings, hostname)}]
    tools = [*TOOL_SPECS, SUBMIT_TOOL]
    state = AgentRun(rules, None)
    retries_left = 1
    start = time.perf_counter()

    def fallback(reason: str) -> AgentRun:
        state.fallback_reason = reason
        state.latency_ms = int((time.perf_counter() - start) * 1000)
        log.warning("agent fallback incident=%s reason=%s tools=%d tokens=%d",
                    incident_id, reason, state.tool_calls, state.tokens)
        return state

    for _ in range(settings.agent_max_tool_calls + 4):        # hard stop on loops
        try:
            resp = llm.generate(prompts.SYSTEM_PROMPT, messages, tools)
        except LLMError as exc:
            return fallback(exc.reason)
        state.tokens += _tokens(resp)

        if resp.stop_reason == "refusal":
            return fallback("refusal")
        if resp.stop_reason == "max_tokens":
            return fallback("max_tokens")
        messages.append({"role": "assistant", "content": _echoable(resp.content)})

        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            if retries_left:
                retries_left -= 1
                messages.append({"role": "user", "content": prompts.NUDGE_SUBMIT})
                continue
            return fallback("no_submission")

        results, submitted = [], None
        for call in calls:
            if call.name == "submit_diagnosis":
                diag, problems = validate(call.input, set(evidence), top_conf)
                if diag is not None:
                    submitted = diag
                    results.append(_result(call.id, "Diagnosis accepted."))
                    continue
                log.info("agent submission rejected incident=%s problems=%s", incident_id, problems)
                if not retries_left:
                    return fallback("invalid_output")
                retries_left -= 1
                results.append(_result(call.id, "Rejected, please fix and resubmit: "
                                       + "; ".join(problems), error=True))
            elif state.tool_calls >= settings.agent_max_tool_calls:
                results.append(_result(call.id, prompts.BUDGET_EXHAUSTED, error=True))
            else:
                state.tool_calls += 1
                text, is_error = box.call(call.name, dict(call.input))
                log.info("agent tool incident=%s call=%s args=%s error=%s",
                         incident_id, call.name, dict(call.input), is_error)
                results.append(_result(call.id, text, error=is_error))

        if submitted is not None:
            state.latency_ms = int((time.perf_counter() - start) * 1000)
            state.ai = _store(session, box, submitted, evidence, rules, llm, resp, state)
            log.info("agent done incident=%s cause=%r tools=%d tokens=%d latency_ms=%d",
                     incident_id, submitted.diagnosis, state.tool_calls, state.tokens,
                     state.latency_ms)
            return state
        messages.append({"role": "user", "content": results})

    return fallback("loop_limit")


# --------------------------------------------------------------------
def _store(session, box, diag, evidence, rules, llm, resp, state) -> Diagnosis:
    hostname = session.get(Device, box.incident.device_id).hostname
    row = Diagnosis(
        incident_id=box.incident.id,
        source="ai_agent",
        probable_cause=diag.diagnosis,
        confidence=BAND_VALUE[diag.confidence],
        alternatives=[a.model_dump() for a in diag.alternatives],
        recommendations=diag.recommended_investigation,
        cited_evidence=sorted({e.evidence_id for e in diag.evidence}),
        report_markdown=render_agent_report(box.incident.code, hostname, diag, evidence),
        llm_provider=llm.provider,
        llm_model=getattr(resp, "model", None) or llm.model,   # the model that actually answered
        tokens_used=state.tokens,
        latency_ms=state.latency_ms,
        evidence_hash=rules.evidence_hash,
    )
    session.add(row)
    session.commit()
    return row


def _result(tool_use_id: str, content: str, error: bool = False) -> dict:
    out = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if error:
        out["is_error"] = True
    return out


def _tokens(resp) -> int:
    u = getattr(resp, "usage", None)
    if u is None:
        return 0
    return sum(int(getattr(u, k, 0) or 0) for k in
               ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens"))


def _echoable(content: list) -> list:
    """Blocks to send back next turn. After a mid-output server-side fallback,
    only text blocks from before the last fallback marker are kept."""
    marks = [i for i, b in enumerate(content) if b.type == "fallback"]
    if not marks:
        return content
    cut = marks[-1]
    return [b for b in content[:cut] if b.type == "text"] + list(content[cut + 1:])

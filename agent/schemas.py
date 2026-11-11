"""The agent's output contract (docs/architecture.md section 10.4) and the
guardrail checks applied to it (section 10.5).

The JSON schema here is what the model must fill in when it calls
submit_diagnosis; ``validate`` then checks what a schema cannot: every
cited evidence id exists, and the confidence does not overstate the rules.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

ConfidenceLabel = Literal["low", "medium", "medium-high", "high"]
BANDS: list[str] = ["low", "medium", "medium-high", "high"]
# Stored as a number in diagnoses.confidence (CHECK 0..1).
BAND_VALUE = {"low": 0.3, "medium": 0.5, "medium-high": 0.7, "high": 0.85}


class EvidenceStatement(BaseModel):
    statement: str = Field(min_length=3, max_length=300)
    evidence_id: int


class Alternative(BaseModel):
    cause: str = Field(min_length=2, max_length=120)
    why_less_likely: str = Field(min_length=3, max_length=300)


class AgentDiagnosis(BaseModel):
    diagnosis: str = Field(min_length=3, max_length=160)
    confidence: ConfidenceLabel
    evidence: list[EvidenceStatement] = Field(min_length=1, max_length=8)
    alternatives: list[Alternative] = Field(default_factory=list, max_length=4)
    recommended_investigation: list[str] = Field(min_length=1, max_length=6)


SUBMIT_TOOL = {
    "name": "submit_diagnosis",
    "description": (
        "Submit your final diagnosis for the incident. Call this exactly once, when you are "
        "done investigating. Every evidence statement must cite the numeric id of an evidence "
        "row you were shown; statements without a real id are rejected."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "diagnosis": {"type": "string", "description": "Probable cause, one short phrase"},
            "confidence": {"type": "string", "enum": BANDS},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "statement": {"type": "string"},
                        "evidence_id": {"type": "integer"},
                    },
                    "required": ["statement", "evidence_id"],
                    "additionalProperties": False,
                },
            },
            "alternatives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "cause": {"type": "string"},
                        "why_less_likely": {"type": "string"},
                    },
                    "required": ["cause", "why_less_likely"],
                    "additionalProperties": False,
                },
            },
            "recommended_investigation": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["diagnosis", "confidence", "evidence", "alternatives",
                     "recommended_investigation"],
        "additionalProperties": False,
    },
}


def band_for(rule_confidence: float | None) -> str:
    """The confidence band a rule score corresponds to."""
    if rule_confidence is None or rule_confidence < 0.4:
        return "low"
    if rule_confidence < 0.6:
        return "medium"
    if rule_confidence < 0.8:
        return "medium-high"
    return "high"


def validate(raw: dict, evidence_ids: set[int],
             top_rule_confidence: float | None) -> tuple[AgentDiagnosis | None, list[str]]:
    """Parse and check a submitted diagnosis. Returns (diagnosis, problems)."""
    try:
        diag = AgentDiagnosis.model_validate(raw)
    except ValidationError as exc:
        return None, [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]

    problems = []
    unknown = sorted({e.evidence_id for e in diag.evidence} - evidence_ids)
    if unknown:
        problems.append(f"evidence_id(s) {unknown} do not exist; valid ids are {sorted(evidence_ids)}")
    ceiling = min(BANDS.index(band_for(top_rule_confidence)) + 1, len(BANDS) - 1)
    if BANDS.index(diag.confidence) > ceiling:
        problems.append(
            f"confidence '{diag.confidence}' overstates the evidence: the strongest rule scored "
            f"{top_rule_confidence}, so at most '{BANDS[ceiling]}' is allowed")
    return (diag if not problems else None), problems

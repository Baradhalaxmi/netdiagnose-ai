"""Rule engine: evaluate rules.yaml against evidence and rank causes
(docs/architecture.md sections 9.3-9.5). Pure functions; no database.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from diagnosis.evidence import METRICS

RULES_PATH = Path(__file__).with_name("rules.yaml")
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
MIN_MATCH_FRACTION = 0.5   # partial matches below this are not reported


@dataclass(frozen=True)
class Condition:
    metric: str
    op: str
    value: float | None = None
    baseline_ratio: float | None = None

    def holds(self, ev: dict | None) -> bool:
        if ev is None:
            return False
        compare = OPS[self.op]
        if self.baseline_ratio is not None:
            base = ev.get("baseline")
            if base is None or base <= 0:
                return False
            return compare(ev["observed"], self.baseline_ratio * base)
        return compare(ev["observed"], self.value)

    @property
    def is_symptom(self) -> bool:
        """'>' conditions describe something abnormal; '<' ones only rule a cause in or out."""
        return self.op in (">", ">=")

    def describe(self) -> str:
        if self.baseline_ratio is not None:
            return f"{self.metric} {self.op} {self.baseline_ratio}x baseline"
        return f"{self.metric} {self.op} {self.value}"


@dataclass(frozen=True)
class Rule:
    id: str
    cause: str
    conditions: tuple[Condition, ...]
    confidence: float
    investigate: tuple[str, ...] = field(default_factory=tuple)


def parse_rules(raw: list[dict]) -> list[Rule]:
    """Validate and build rules; raises ValueError with the offending rule id."""
    rules, seen = [], set()
    for r in raw:
        rid = r.get("id", "<missing id>")
        if rid in seen:
            raise ValueError(f"duplicate rule id {rid}")
        seen.add(rid)
        if not 0 < float(r.get("confidence", -1)) <= 1:
            raise ValueError(f"{rid}: confidence must be in (0, 1]")
        conds = []
        for c in r.get("all") or []:
            if c.get("metric") not in METRICS:
                raise ValueError(f"{rid}: unknown metric {c.get('metric')!r}")
            if c.get("op") not in OPS:
                raise ValueError(f"{rid}: unknown op {c.get('op')!r}")
            if ("value" in c) == ("baseline_ratio" in c):
                raise ValueError(f"{rid}: give exactly one of value / baseline_ratio")
            conds.append(Condition(c["metric"], c["op"], c.get("value"), c.get("baseline_ratio")))
        if not conds:
            raise ValueError(f"{rid}: needs at least one condition")
        rules.append(
            Rule(rid, r["cause"], tuple(conds), float(r["confidence"]),
                 tuple(r.get("investigate") or ()))
        )
    return rules


@lru_cache(maxsize=4)
def load_rules(path: str | Path = RULES_PATH) -> tuple[Rule, ...]:
    with open(path, encoding="utf-8") as f:
        return tuple(parse_rules(yaml.safe_load(f)))


def evaluate(evidence: list[dict], rules=None) -> list[dict]:
    """Rank candidate causes, best first (output contract, section 9.5).

    Each evidence item needs ``metric``, ``observed`` and ``baseline``;
    an ``id`` (set once stored) is cited in ``evidence_ids``.
    """
    rules = load_rules() if rules is None else rules
    by_metric = {e["metric"]: e for e in evidence}
    candidates = []
    for rule in rules:
        matched = [c for c in rule.conditions if c.holds(by_metric.get(c.metric))]
        fraction = len(matched) / len(rule.conditions)
        # Need enough of the rule AND at least one real symptom: a rule must
        # not be suggested on healthy data just because "loss < 5" holds.
        if fraction < MIN_MATCH_FRACTION or not any(c.is_symptom for c in matched):
            continue
        cited = [by_metric[c.metric] for c in matched]
        candidates.append(
            {
                "rule_id": rule.id,
                "cause": rule.cause,
                "confidence": round(rule.confidence * fraction, 3),
                "base_confidence": rule.confidence,
                "matched": len(matched),
                "total": len(rule.conditions),
                "conditions_met": [c.describe() for c in matched],
                "evidence_metrics": [e["metric"] for e in cited],
                "evidence_ids": [e["id"] for e in cited if e.get("id") is not None],
                "investigate": list(rule.investigate),
            }
        )
    # Higher score first; on ties prefer the rule that matched completely.
    candidates.sort(key=lambda c: (c["confidence"], c["matched"] == c["total"]), reverse=True)
    return candidates

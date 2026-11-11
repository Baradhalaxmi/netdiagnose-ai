"""Render a diagnosis as markdown from a template (not by an LLM), so the
format is always the same (docs/architecture.md section 10.7)."""

from __future__ import annotations

UNITS = {
    "latency_ms": "ms", "packet_loss_pct": "%", "jitter_ms": "ms",
    "throughput_mbps": "Mbps", "cpu_usage_pct": "%", "interface_utilization_pct": "%",
}


def _fmt(v, unit=""):
    return "–" if v is None else f"{v:,.2f}{(' ' + unit) if unit else ''}"


def render_rules_report(code: str, hostname: str, causes: list[dict], evidence: list[dict]) -> str:
    lines = []
    if causes:
        top = causes[0]
        lines.append(f"### Diagnosis: {top['cause']} (confidence {top['confidence']:.2f})")
        lines.append(f"Incident **{code}** on **{hostname}**. Rule `{top['rule_id']}` matched "
                     f"{top['matched']}/{top['total']} conditions: "
                     + "; ".join(top["conditions_met"]) + ".")
    else:
        lines.append("### Diagnosis: Unclassified anomaly")
        lines.append(f"Incident **{code}** on **{hostname}**. The detector flagged this traffic, "
                     "but no diagnosis rule matched the evidence.")

    lines += ["", "#### Evidence (incident window vs device baseline)",
              "| ID | Metric | Observed | Baseline | Ratio | Abnormal |",
              "|---|---|---|---|---|---|"]
    for e in evidence:
        unit = UNITS.get(e["metric"], "")
        ratio = "–" if e.get("ratio") is None else f"{e['ratio']:.1f}×"
        lines.append(f"| {e.get('id', '–')} | {e['metric']} | {_fmt(e['observed'], unit)} | "
                     f"{_fmt(e.get('baseline'), unit)} | {ratio} | {'yes' if e.get('breached') else 'no'} |")

    if len(causes) > 1:
        lines += ["", "#### Alternatives"]
        lines += [f"- {c['cause']} (confidence {c['confidence']:.2f}, "
                  f"{c['matched']}/{c['total']} conditions)" for c in causes[1:]]

    steps = causes[0]["investigate"] if causes else [
        "Review the flagged flows for this device",
        "Check device logs around the incident window",
    ]
    lines += ["", "#### Recommended investigation"] + [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    return "\n".join(lines)

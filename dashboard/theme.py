"""Shared chart styling and formatting helpers.

Colours follow the dataviz reference palette:
* one series per chart -> a single blue line (no legend box needed);
* anomalies -> status "critical" red, always with a legend entry and a
  different shape, so meaning never rests on colour alone;
* severity is ordinal -> light-to-dark steps of one blue ramp (status
  colours are reserved for state, not for ranking).
"""

from __future__ import annotations

from datetime import datetime

import altair as alt
import pandas as pd

SERIES = "#2a78d6"
ANOMALY = "#d03b3b"
SEVERITY_ORDER = ["low", "medium", "high", "critical"]
SEVERITY_COLORS = ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
SEVERITY_LABEL = {"low": "▁ low", "medium": "▃ medium", "high": "▅ high", "critical": "▇ critical"}

METRIC_LABELS = {
    "latency_ms": "Latency (ms)",
    "packet_loss_pct": "Packet loss (%)",
    "jitter_ms": "Jitter (ms)",
    "throughput_mbps": "Throughput (Mbps)",
    "cpu_usage_pct": "CPU (%)",
    "interface_utilization_pct": "Interface utilisation (%)",
}


def _axis_style(chart: alt.Chart) -> alt.Chart:
    return chart.configure_axis(grid=True, gridOpacity=0.25, domain=False, tickColor="#c3c2b7",
                                labelFontSize=11, titleFontSize=11) \
                .configure_view(strokeWidth=0)


def metric_chart(df: pd.DataFrame, metric: str, height: int = 190) -> alt.Chart:
    """Line for one metric over time, anomalies as red diamonds with a legend entry."""
    data = df[["ts", metric, "is_anomaly", "anomaly_score"]].dropna(subset=[metric]).copy()
    data["marker"] = data["is_anomaly"].map({True: "Flagged by detector"})
    base = alt.Chart(data).encode(
        x=alt.X("ts:T", title=None),
        y=alt.Y(f"{metric}:Q", title=METRIC_LABELS[metric]),
    )
    tooltip = [alt.Tooltip("ts:T", title="Time", format="%d %b %H:%M:%S"),
               alt.Tooltip(f"{metric}:Q", title=METRIC_LABELS[metric], format=",.2f"),
               alt.Tooltip("anomaly_score:Q", title="Anomaly score", format=".2f")]
    line = base.mark_line(color=SERIES, strokeWidth=2, interpolate="monotone")
    hover = base.mark_point(size=120, opacity=0).encode(tooltip=tooltip)   # big invisible hit area
    flagged = base.transform_filter("datum.is_anomaly === true").mark_point(
        shape="diamond", filled=True, size=50, stroke="white", strokeWidth=1,
    ).encode(
        color=alt.Color("marker:N", scale=alt.Scale(domain=["Flagged by detector"], range=[ANOMALY]),
                        legend=alt.Legend(title=None, orient="top-right")),
        tooltip=tooltip,
    )
    return _axis_style((line + flagged + hover).properties(height=height))


def anomalies_per_hour_chart(rows: list[dict]) -> alt.Chart:
    data = pd.DataFrame(rows)
    chart = alt.Chart(data).mark_bar(color=SERIES, cornerRadiusTopLeft=4,
                                     cornerRadiusTopRight=4).encode(
        x=alt.X("hour:T", title=None, axis=alt.Axis(format="%H:%M")),
        y=alt.Y("count:Q", title="Anomalies"),
        tooltip=[alt.Tooltip("hour:T", title="Hour", format="%d %b %H:00"),
                 alt.Tooltip("count:Q", title="Anomalies", format=",")],
    ).properties(height=220)
    return _axis_style(chart)


def severity_chart(counts: dict[str, int]) -> alt.Chart:
    data = pd.DataFrame({"severity": SEVERITY_ORDER,
                         "count": [counts.get(s, 0) for s in SEVERITY_ORDER]})
    chart = alt.Chart(data).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        y=alt.Y("severity:N", sort=SEVERITY_ORDER, title=None),
        x=alt.X("count:Q", title="Open incidents", axis=alt.Axis(tickMinStep=1)),
        color=alt.Color("severity:N", sort=SEVERITY_ORDER,
                        scale=alt.Scale(domain=SEVERITY_ORDER, range=SEVERITY_COLORS), legend=None),
        tooltip=[alt.Tooltip("severity:N", title="Severity"),
                 alt.Tooltip("count:Q", title="Open incidents")],
    ).properties(height=160)
    labels = chart.mark_text(align="left", dx=4, color="#52514e").encode(text="count:Q",
                                                                          color=alt.value("#52514e"))
    return _axis_style(chart + labels)


def causes_chart(causes: list[dict]) -> alt.Chart:
    """Ranked causes, best first, as horizontal bars of confidence."""
    data = pd.DataFrame(causes)
    order = data["cause"].tolist()
    bars = alt.Chart(data).mark_bar(color=SERIES, cornerRadiusTopRight=4,
                                    cornerRadiusBottomRight=4).encode(
        y=alt.Y("cause:N", sort=order, title=None),
        x=alt.X("confidence:Q", title="Confidence", scale=alt.Scale(domain=[0, 1])),
        tooltip=[alt.Tooltip("cause:N", title="Cause"),
                 alt.Tooltip("confidence:Q", title="Confidence", format=".2f")],
    ).properties(height=max(60, 34 * len(data)))
    labels = bars.mark_text(align="left", dx=4, color="#52514e").encode(
        text=alt.Text("confidence:Q", format=".2f"), color=alt.value("#52514e"))
    return _axis_style(bars + labels)


def fmt_time(value: str | None) -> str:
    if not value:
        return "–"
    return datetime.fromisoformat(value).astimezone().strftime("%d %b %H:%M:%S")


def duration(start: str, end: str) -> str:
    secs = int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    return f"{secs // 3600}h {secs % 3600 // 60}m" if secs >= 3600 else f"{secs // 60}m {secs % 60}s"

"""
rca_agent/report_renderer.py
────────────────────────────
Renders an RCA report dict into a self-contained HTML page.
No external CSS/JS dependencies — everything is inline.
"""

from __future__ import annotations
import html
from datetime import datetime
from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────────

def _esc(v: Any) -> str:
    """HTML-escape any value."""
    return html.escape(str(v) if v is not None else "")


def _severity_color(severity: str) -> tuple[str, str]:
    """Returns (bg_color, text_color) for a severity badge."""
    s = (severity or "").lower()
    if s == "critical":
        return "#dc2626", "#fff"
    if s == "high":
        return "#ea580c", "#fff"
    if s == "medium":
        return "#ca8a04", "#fff"
    if s == "low":
        return "#16a34a", "#fff"
    return "#6b7280", "#fff"


def _confidence_color(confidence: str) -> tuple[str, str]:
    c = (confidence or "").lower()
    if c == "high":
        return "#16a34a", "#fff"
    if c == "medium":
        return "#ca8a04", "#fff"
    return "#dc2626", "#fff"


def _effort_color(effort: str) -> tuple[str, str]:
    e = (effort or "").lower()
    if e == "quick-fix":
        return "#16a34a", "#fff"
    if e == "medium":
        return "#2563eb", "#fff"
    return "#7c3aed", "#fff"


def _badge(text: str, bg: str, fg: str, extra_style: str = "") -> str:
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;'
        f'font-size:0.75rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;'
        f'background:{bg};color:{fg};{extra_style}">{_esc(text)}</span>'
    )


def _card(title: str, body: str, icon: str = "") -> str:
    icon_html = f'<span style="margin-right:8px;font-size:1.1em;">{icon}</span>' if icon else ""
    return f"""
<div style="background:#1e2030;border:1px solid #2d3148;border-radius:12px;
            padding:24px 28px;margin-bottom:24px;box-shadow:0 2px 12px rgba(0,0,0,.3);">
  <h2 style="margin:0 0 18px 0;font-size:1.1rem;font-weight:700;color:#a5b4fc;
             text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid #2d3148;
             padding-bottom:12px;">{icon_html}{_esc(title)}</h2>
  {body}
</div>"""


def _code_block(code: str, lang: str = "") -> str:
    return f"""<pre style="background:#0d0f1a;border:1px solid #2d3148;border-radius:8px;
                    padding:16px;overflow-x:auto;font-family:'JetBrains Mono',
                    'Fira Code',Consolas,monospace;font-size:0.82rem;line-height:1.6;
                    color:#e2e8f0;margin:10px 0;">{_esc(code)}</pre>"""


def _diff_block(diff_text: str) -> str:
    """Render a unified diff with green/red line coloring."""
    lines = diff_text.split("\n")
    rendered = []
    for line in lines:
        if line.startswith("+") and not line.startswith("+++"):
            color = "#22c55e"
            bg = "rgba(34,197,94,.08)"
        elif line.startswith("-") and not line.startswith("---"):
            color = "#ef4444"
            bg = "rgba(239,68,68,.08)"
        elif line.startswith("@@"):
            color = "#60a5fa"
            bg = "rgba(96,165,250,.08)"
        else:
            color = "#94a3b8"
            bg = "transparent"
        rendered.append(
            f'<div style="color:{color};background:{bg};padding:1px 4px;'
            f'white-space:pre;font-family:\'JetBrains Mono\',Consolas,monospace;'
            f'font-size:0.82rem;line-height:1.5;">{_esc(line)}</div>'
        )
    return (
        '<div style="background:#0d0f1a;border:1px solid #2d3148;border-radius:8px;'
        'padding:12px 16px;overflow-x:auto;margin:10px 0;">'
        + "\n".join(rendered)
        + "</div>"
    )


def _fmt_ts(ts: str) -> str:
    """Format an ISO timestamp to a human-readable string."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ts


# ── Section renderers ─────────────────────────────────────────────────────────

def _render_header(report: dict) -> str:
    summary = report.get("incident_summary", {})
    severity = summary.get("severity", "unknown")
    sev_bg, sev_fg = _severity_color(severity)
    service = _esc(report.get("service_name", "unknown"))
    env = _esc(summary.get("environment", ""))
    rca_id = _esc(report.get("rca_id", ""))
    generated = _fmt_ts(report.get("generated_at", ""))
    return f"""
<div style="background:linear-gradient(135deg,#1a1c2e 0%,#12131f 100%);
            border-bottom:1px solid #2d3148;padding:28px 40px 24px;margin-bottom:0;">
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:10px;">
    <div style="font-size:1.8rem;font-weight:800;color:#e2e8f0;letter-spacing:-.02em;">
      🔍 Root Cause Analysis
    </div>
    {_badge(severity.upper(), sev_bg, sev_fg, "font-size:.85rem;padding:5px 14px;")}
  </div>
  <div style="display:flex;gap:32px;flex-wrap:wrap;margin-top:8px;">
    <div><span style="color:#64748b;font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;">Service</span>
      <div style="color:#a5b4fc;font-weight:700;font-size:1rem;margin-top:2px;">{service}</div></div>
    <div><span style="color:#64748b;font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;">Environment</span>
      <div style="color:#94a3b8;font-weight:600;margin-top:2px;">{env}</div></div>
    <div><span style="color:#64748b;font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;">Generated</span>
      <div style="color:#94a3b8;font-weight:600;margin-top:2px;">{generated}</div></div>
    <div><span style="color:#64748b;font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;">RCA ID</span>
      <div style="color:#475569;font-size:.78rem;font-family:monospace;margin-top:4px;">{rca_id}</div></div>
  </div>
</div>"""


def _render_incident_summary(report: dict) -> str:
    s = report.get("incident_summary", {})
    rows = []
    field_map = [
        ("What", "what"),
        ("When", "when"),
        ("Environment", "environment"),
        ("Severity", "severity"),
        ("Estimated Impact", "estimated_impact"),
    ]
    for label, key in field_map:
        val = s.get(key)
        if not val:
            continue
        if key == "severity":
            bg, fg = _severity_color(val)
            val_html = _badge(val, bg, fg)
        elif key == "when":
            val_html = f'<span style="color:#94a3b8;">{_esc(_fmt_ts(val))}</span>'
        else:
            val_html = f'<span style="color:#e2e8f0;">{_esc(val)}</span>'
        rows.append(
            f'<tr>'
            f'<td style="padding:8px 16px 8px 0;color:#64748b;font-size:.85rem;'
            f'font-weight:600;white-space:nowrap;vertical-align:top;">{_esc(label)}</td>'
            f'<td style="padding:8px 0;">{val_html}</td>'
            f'</tr>'
        )
    body = f'<table style="border-collapse:collapse;width:100%;">{"".join(rows)}</table>'
    return _card("Incident Summary", body, "📋")


def _render_timeline(report: dict) -> str:
    events = report.get("timeline", [])
    if not events:
        return ""
    items = []
    for i, ev in enumerate(events):
        ts = _fmt_ts(ev.get("timestamp", ""))
        event_text = _esc(ev.get("event", ""))
        is_last = (i == len(events) - 1)
        connector = (
            "" if is_last else
            '<div style="width:2px;background:#2d3148;height:20px;margin-left:10px;margin-top:2px;"></div>'
        )
        items.append(f"""
<div>
  <div style="display:flex;align-items:flex-start;gap:14px;">
    <div style="flex-shrink:0;width:22px;height:22px;border-radius:50%;
                background:#312e81;border:2px solid #6366f1;margin-top:2px;
                display:flex;align-items:center;justify-content:center;font-size:10px;">
      <span style="color:#a5b4fc;font-weight:700;">{i+1}</span>
    </div>
    <div style="flex:1;padding-bottom:4px;">
      <div style="font-size:.75rem;color:#64748b;font-family:monospace;margin-bottom:2px;">{_esc(ts)}</div>
      <div style="color:#e2e8f0;font-size:.9rem;line-height:1.5;">{event_text}</div>
    </div>
  </div>
  {connector}
</div>""")
    body = "".join(items)
    return _card("Timeline", body, "⏱️")


def _render_root_cause(report: dict) -> str:
    rc = report.get("root_cause", {})
    conf = rc.get("confidence", "unknown")
    conf_bg, conf_fg = _confidence_color(conf)
    summary = _esc(rc.get("summary", ""))
    conf_reason = _esc(rc.get("confidence_reason", ""))

    # Code reference
    cr = rc.get("code_reference", {})
    cr_html = ""
    if cr:
        file_ = _esc(cr.get("file", ""))
        line = cr.get("line")
        func = cr.get("function")
        repo = _esc(cr.get("repo", ""))
        sha = _esc(cr.get("commit_sha", "") or "")
        url = cr.get("github_url")
        link = (
            f'<a href="{_esc(url)}" style="color:#60a5fa;text-decoration:none;" '
            f'target="_blank">View on GitHub ↗</a>'
            if url else ""
        )
        line_str = f" line {line}" if line else ""
        func_str = f" · {func}()" if func else ""
        cr_html = f"""
<div style="background:#0d0f1a;border:1px solid #312e81;border-radius:8px;
            padding:16px 20px;margin-top:14px;">
  <div style="font-size:.75rem;color:#6366f1;text-transform:uppercase;
              letter-spacing:.06em;margin-bottom:8px;font-weight:700;">Code Reference</div>
  <code style="color:#a5b4fc;font-size:.88rem;">
    {file_}{line_str}{func_str}
  </code>
  <div style="margin-top:6px;font-size:.78rem;color:#475569;">
    repo: {repo}
    {f'&nbsp;·&nbsp;commit: <code style="color:#64748b;">{sha[:8]}</code>' if sha else ""}
    {f'&nbsp;&nbsp;{link}' if link else ""}
  </div>
</div>"""

    # Regression info
    reg = rc.get("regression_introduced_by")
    reg_html = ""
    if reg:
        reg_sha = _esc(str(reg.get("commit_sha", ""))[:8])
        reg_msg = _esc(reg.get("commit_message", ""))
        reg_author = _esc(reg.get("author", ""))
        reg_ts = _esc(_fmt_ts(reg.get("deployed_at", "") or ""))
        reg_html = f"""
<div style="background:rgba(220,38,38,.08);border:1px solid rgba(220,38,38,.3);
            border-radius:8px;padding:14px 18px;margin-top:14px;">
  <div style="font-size:.75rem;color:#f87171;text-transform:uppercase;
              letter-spacing:.06em;font-weight:700;margin-bottom:8px;">⚠️ Regression Introduced By</div>
  <div style="color:#fca5a5;font-size:.88rem;">
    <code style="background:rgba(0,0,0,.3);padding:2px 6px;border-radius:4px;">{reg_sha}</code>
    &nbsp;{reg_msg}
  </div>
  <div style="margin-top:4px;font-size:.78rem;color:#f87171;">
    by {reg_author} · deployed {reg_ts}
  </div>
</div>"""

    body = f"""
<div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;">
  {_badge(f"Confidence: {conf.upper()}", conf_bg, conf_fg)}
  <span style="color:#64748b;font-size:.83rem;font-style:italic;">{conf_reason}</span>
</div>
<p style="color:#e2e8f0;line-height:1.7;margin:0 0 4px;">{summary}</p>
{cr_html}
{reg_html}"""
    return _card("Root Cause", body, "🎯")


def _render_evidence(report: dict) -> str:
    items = report.get("evidence", [])
    if not items:
        return ""
    parts = []
    type_icons = {"diff": "📝", "code": "💻", "deployment": "🚀", "log": "📋"}
    for ev in items:
        ev_type = ev.get("type", "code")
        icon = type_icons.get(ev_type, "📄")
        desc = _esc(ev.get("description", ""))
        val = ev.get("value", "")
        if ev_type == "diff" and (val.startswith("@@") or "@@" in val or "-" in val or "+" in val):
            code_html = _diff_block(val)
        else:
            code_html = _code_block(val) if val else ""
        parts.append(f"""
<div style="margin-bottom:20px;padding-bottom:20px;
            border-bottom:1px solid #1e2030;">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
    <span>{icon}</span>
    <span style="color:#94a3b8;font-size:.88rem;font-weight:600;">{desc}</span>
    {_badge(ev_type, "#1e2030", "#64748b")}
  </div>
  {code_html}
</div>""")
    return _card("Evidence", "".join(parts), "🔬")


def _render_impact(report: dict) -> str:
    ia = report.get("impact_assessment", {})
    if not ia:
        return ""
    fields = [
        ("Affected Service", ia.get("affected_service")),
        ("Environment", ia.get("affected_environment")),
        ("Functionality", ia.get("affected_functionality")),
        ("Error Rate", ia.get("inferred_error_rate")),
        ("Duration", ia.get("duration_estimate")),
    ]
    items = []
    for label, val in fields:
        if not val:
            continue
        items.append(f"""
<div style="background:#12131f;border:1px solid #2d3148;border-radius:8px;
            padding:14px 18px;">
  <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;
              letter-spacing:.05em;font-weight:600;margin-bottom:4px;">{_esc(label)}</div>
  <div style="color:#e2e8f0;font-size:.9rem;">{_esc(val)}</div>
</div>""")
    body = f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;">{"".join(items)}</div>'
    return _card("Impact Assessment", body, "📊")


def _render_solutions(report: dict) -> str:
    solutions = report.get("suggested_solutions", [])
    if not solutions:
        return ""
    parts = []
    for sol in sorted(solutions, key=lambda x: x.get("priority", 99)):
        priority = sol.get("priority", 1)
        effort = sol.get("effort", "")
        title = _esc(sol.get("title", ""))
        desc = _esc(sol.get("description", ""))
        code = sol.get("code_suggestion")
        eff_bg, eff_fg = _effort_color(effort)
        code_html = _code_block(code) if code else ""
        parts.append(f"""
<div style="background:#12131f;border:1px solid #2d3148;border-radius:10px;
            padding:18px 22px;margin-bottom:16px;">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">
    <div style="width:28px;height:28px;border-radius:50%;background:#312e81;
                border:2px solid #6366f1;display:flex;align-items:center;
                justify-content:center;font-weight:800;color:#a5b4fc;font-size:.85rem;
                flex-shrink:0;">{priority}</div>
    <div style="font-weight:700;color:#e2e8f0;font-size:.95rem;flex:1;">{title}</div>
    {_badge(effort, eff_bg, eff_fg)}
  </div>
  <p style="color:#94a3b8;line-height:1.6;margin:0 0 8px;font-size:.88rem;">{desc}</p>
  {code_html}
</div>""")
    return _card("Suggested Solutions", "".join(parts), "💡")


def _render_prevention(report: dict) -> str:
    recs = report.get("prevention_recommendations", [])
    if not recs:
        return ""
    items = []
    for rec in recs:
        items.append(f"""
<div style="display:flex;align-items:flex-start;gap:10px;padding:10px 0;
            border-bottom:1px solid #1e2030;">
  <span style="color:#4ade80;font-size:1.1em;margin-top:1px;">✓</span>
  <span style="color:#e2e8f0;font-size:.9rem;line-height:1.5;">{_esc(rec)}</span>
</div>""")
    return _card("Prevention Recommendations", "".join(items), "🛡️")


def _render_metadata(report: dict) -> str:
    meta = report.get("analysis_metadata", {})
    if not meta:
        return ""
    model = _esc(meta.get("model", ""))
    iterations = meta.get("react_iterations", 0)
    files = meta.get("github_files_fetched", [])
    cache_hits = meta.get("cache_hits", [])
    deployment_used = meta.get("deployment_record_used", False)
    discovered_via = _esc(meta.get("repo_discovered_via", ""))

    files_html = ", ".join(
        f'<code style="background:#0d0f1a;padding:1px 5px;border-radius:3px;'
        f'font-size:.78rem;color:#60a5fa;">{_esc(f)}</code>'
        for f in files
    ) if files else '<span style="color:#475569;">none</span>'

    cache_html = ", ".join(
        f'<code style="background:#0d0f1a;padding:1px 5px;border-radius:3px;'
        f'font-size:.78rem;color:#fbbf24;">{_esc(k)}</code>'
        for k in cache_hits
    ) if cache_hits else '<span style="color:#475569;">none</span>'

    body = f"""
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));
            gap:12px;margin-bottom:16px;">
  <div style="text-align:center;background:#12131f;border:1px solid #2d3148;
              border-radius:8px;padding:12px;">
    <div style="font-size:1.6rem;font-weight:800;color:#a5b4fc;">{iterations}</div>
    <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;margin-top:2px;">Iterations</div>
  </div>
  <div style="text-align:center;background:#12131f;border:1px solid #2d3148;
              border-radius:8px;padding:12px;">
    <div style="font-size:1.6rem;font-weight:800;color:#4ade80;">{len(files)}</div>
    <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;margin-top:2px;">Files Fetched</div>
  </div>
  <div style="text-align:center;background:#12131f;border:1px solid #2d3148;
              border-radius:8px;padding:12px;">
    <div style="font-size:1.6rem;font-weight:800;color:#fbbf24;">{len(cache_hits)}</div>
    <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;margin-top:2px;">Cache Hits</div>
  </div>
  <div style="text-align:center;background:#12131f;border:1px solid #2d3148;
              border-radius:8px;padding:12px;">
    <div style="font-size:1rem;font-weight:700;color:#94a3b8;margin-top:4px;">{'✓' if deployment_used else '✗'}</div>
    <div style="font-size:.72rem;color:#64748b;text-transform:uppercase;margin-top:2px;">Deployment Used</div>
  </div>
</div>
<div style="font-size:.82rem;color:#64748b;margin-bottom:8px;">
  <strong style="color:#475569;">Model:</strong> <code style="color:#94a3b8;">{model}</code>
  &nbsp;&nbsp;
  <strong style="color:#475569;">Repo discovered via:</strong> <span style="color:#94a3b8;">{discovered_via}</span>
</div>
<div style="font-size:.82rem;color:#64748b;margin-bottom:6px;">
  <strong style="color:#475569;">Files:</strong> {files_html}
</div>
<div style="font-size:.82rem;color:#64748b;">
  <strong style="color:#475569;">Cache hits:</strong> {cache_html}
</div>"""
    return _card("Analysis Metadata", body, "⚙️")


def _render_contributing_factors(report: dict) -> str:
    factors = report.get("contributing_factors", [])
    if not factors:
        return ""
    items = []
    for f in factors:
        items.append(
            f'<li style="color:#94a3b8;font-size:.88rem;line-height:1.6;'
            f'margin-bottom:6px;">{_esc(f)}</li>'
        )
    body = f'<ul style="margin:0;padding-left:20px;">{"".join(items)}</ul>'
    return _card("Contributing Factors", body, "🔗")


# ── Main renderer ─────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
               'Helvetica Neue', Arial, sans-serif;
  background: #0f1117;
  color: #e2e8f0;
  line-height: 1.5;
}
a { color: #60a5fa; }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #0f1117; }
::-webkit-scrollbar-thumb { background: #2d3148; border-radius: 3px; }
"""


def render_rca_html(report_dict: dict) -> str:
    """
    Render an RCA report dictionary into a self-contained HTML string.
    All CSS is inline — no external dependencies required.
    """
    sections = [
        _render_header(report_dict),
        '<div style="max-width:900px;margin:0 auto;padding:28px 24px;">',
        _render_incident_summary(report_dict),
        _render_timeline(report_dict),
        _render_root_cause(report_dict),
        _render_evidence(report_dict),
        _render_impact(report_dict),
        _render_contributing_factors(report_dict),
        _render_solutions(report_dict),
        _render_prevention(report_dict),
        _render_metadata(report_dict),
        # Footer
        '<div style="text-align:center;padding:24px 0 40px;color:#334155;font-size:.78rem;">'
        'Generated by <strong style="color:#475569;">rca-agent</strong> · '
        'Powered by Anthropic Claude'
        '</div>',
        '</div>',
    ]

    service = report_dict.get("service_name", "RCA Report")
    severity = report_dict.get("incident_summary", {}).get("severity", "")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RCA Report — {_esc(service)} ({_esc(severity)})</title>
  <style>{CSS}</style>
</head>
<body>
{''.join(sections)}
</body>
</html>"""

"""Write the analysis result as JSON and render the reviewer brief.

The brief is rendered from the already-rounded result, so every number in it is
traceable to analysis.json. Presentation rounding: amounts to whole currency
units, percentages and percentage points to one decimal place.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .periods import Period

NOTE_TEXT = {
    "comparison_period_not_in_data": "no data",
    "missing_value": "missing input",
    "zero_base": "from zero",
    "sign_change": "sign change",
    "insufficient_history": "<12 months",
    "zero_denominator": "zero revenue",
    "negative_denominator": "negative revenue",
}


def write_json(result: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def analysis_id(result: dict[str, Any]) -> str:
    """SHA-256 of the analysis content with sorted keys. The result has no timestamps, so the same
    workbook, config and period always give the same id, and any change to the analysis changes it."""
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_line(result: dict[str, Any]) -> str:
    """The provenance line a commentary must carry; trace.py checks it against the saved analysis."""
    return (f"Source: analysis {result['periods']['analysis_period']} · workbook sha256 {result['source']['sha256']}"
            f" · analysis_id {analysis_id(result)}")


def _label(key: str | None) -> str:
    return Period.from_key(key).label if key else "n/a"


def amt(value: float | None, note: str | None = None, signed: bool = False) -> str:
    if value is None:
        return f"n/a ({NOTE_TEXT.get(note, note)})" if note and note != "negative_base" else "n/a"
    return f"{value:+,.0f}" if signed else f"{value:,.0f}"


def pct(value: float | None, note: str | None = None, signed: bool = False) -> str:
    if value is None:
        return f"n/a ({NOTE_TEXT.get(note, note)})" if note and note != "negative_base" else "n/a"
    text = f"{value:+.1f}%" if signed else f"{value:.1f}%"
    return text + "*" if note == "negative_base" else text


def _value(row: dict[str, Any], unit: str, key: str = "value", note_key: str = "value_note") -> str:
    v = row.get(key)
    return pct(v, row.get(note_key)) if unit == "percent" else amt(v, row.get(note_key))


def _change(c: dict[str, Any], unit: str) -> tuple[str, str]:
    """(absolute or pp change, % change) for a comparison dict."""
    if unit == "percent":
        pp = c.get("pp_change")
        return (f"{pp:+.1f} pp" if pp is not None else f"n/a ({NOTE_TEXT.get(c['note'], c['note'])})"), ""
    return amt(c["abs_change"], c["note"], signed=True), pct(c["pct_change"], c["note"], signed=True)


def _inactive(item: dict[str, Any]) -> bool:
    return item["value"] == 0 and item["mom"]["prior"] == 0 and item["ttm"] in (0, None)


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _highlight_text(h: dict[str, Any]) -> str:
    kind = {"mom_change": "MoM", "yoy_change": "YoY", "unusual_vs_history": "Unusual"}[h["type"]]
    scope = h["scope"].replace("_", " ")
    if h["type"] == "unusual_vs_history":
        body = (f"{amt(h['current'])} vs median of other months {amt(h['baseline'])} "
                f"({amt(h['abs_change'], signed=True)})")
    elif h["scope"] == "margin":
        body = f"{pct(h['current'])} vs {pct(h['baseline'])} in {_label(h['comparison'])} ({h['pp_change']:+.1f} pp)"
    else:
        body = (f"{amt(h['current'])} vs {amt(h['baseline'])} in {_label(h['comparison'])} "
                f"({amt(h['abs_change'], signed=True)}, {pct(h['pct_change'], h.get('note'), signed=True)})")
    return f"[{kind}] {h['label']} ({scope}): {body} - {h['profit_effect']}"


def _presentation_note(r: dict[str, Any]) -> list[str]:
    """What "as presented" and "core" mean, from the accounts the config reclassifies. Classifications
    are reported as configured; nothing here changes them."""
    moved = [(a["label"], a["classification"]) for a in r["income_statement"]["accounts"]
             if a.get("classification") in ("non_operating", "income_tax")]
    if not moved:
        return []
    names = "; ".join(f"{label} (configured as {cls})" for label, cls in moved)
    return ["", f"> **As presented** figures follow the workbook's own layout. Its "
                f"'{r['income_statement']['sections']['opex']}' section includes: {names}. So operating expenses, "
                "operating income and operating margin *as presented* include these accounts. **Core** figures exclude "
                "them. The classification comes from the company config, not from the workbook, and is an accounting "
                "judgment to be confirmed by the accounting team."]


def render_brief(r: dict[str, Any], trend_months: int = 13) -> str:
    period = r["periods"]["analysis_period"]
    prior, ly = r["periods"]["prior_period"], r["periods"]["same_period_last_year"]
    v = r["validation"]
    lines = [
        f"# Monthly trend brief - {r['company_name'] or 'Company'} - {_label(period)}",
        "",
        f"Source: `{r['source']['workbook']}` (sha256 {r['source']['sha256'][:12]}), config: "
        f"`{r['config']['source'] or 'defaults'}`. Amounts in {r['currency']}, whole units; "
        "percentages to 1 dp; `*` = % change against a negative base. Full precision in analysis.json.",
        "",
        f"Commentary source line (copy it verbatim as the last line of commentary.md): `{source_line(r)}`",
        "",
        "## Validation",
        "",
        f"**Status: {v['status'].upper()}** - {v['counts']['error']} errors, {v['counts']['warning']} warnings, "
        f"{v['counts']['info']} info.",
    ]
    if v["status"] == "fail":
        lines += ["", "> **Do not write commentary.** Errors below mean the figures cannot be relied on. "
                      "Report the errors to the reviewer instead."]
    for issue in v["issues"]:
        if issue["severity"] in ("error", "warning"):
            lines.append(f"- **{issue['severity']}** `{issue['code']}`: {issue['message']}")
    info: dict[str, list[dict[str, Any]]] = {}
    for i in v["issues"]:
        if i["severity"] == "info":
            info.setdefault(i["code"], []).append(i)
    for code, items in info.items():
        names = [i.get("account") or i["message"] for i in items]
        lines.append(f"- info `{code}` ({len(items)}): " + "; ".join(names))
    failed = [c for c in v["checks"] if c["status"] == "fail"]
    passed = [c for c in v["checks"] if c["status"] == "pass"]
    lines.append(f"- Reconciliation checks: {len(passed)} passed, {len(failed)} failed, "
                 f"{sum(1 for c in v['checks'] if c['status'] == 'skipped')} skipped.")

    ap = r["analysis_period"]
    lines += ["", f"## Headline metrics - {_label(period)}", ""]
    rows = []
    for m in ap["metrics"]:
        unit = m["unit"]
        mom_a, mom_p = _change(m["mom"], unit)
        yoy_a, yoy_p = _change(m["yoy"], unit)
        rows.append([m["label"], _value(m, unit), _value(m["mom"], unit, "prior", "note"), mom_a, mom_p,
                     _value(m["yoy"], unit, "prior", "note"), yoy_a, yoy_p,
                     _value(m, unit, "ttm", "ttm_note"), _value(m, unit, "ytd", "ytd_note")])
    lines += _table(["Metric", _label(period), _label(prior), "MoM", "MoM %", _label(ly), "YoY", "YoY %", "TTM", "YTD"], rows)
    lines += _presentation_note(r)

    monthly = r["income_statement"]["monthly"]
    trend_metrics = [m for m in r["income_statement"]["headline_metrics"] if m in monthly]
    margin = "gross_margin" if "gross_margin" in monthly else None
    keys = [k for k in r["periods"]["available"] if k <= period][-trend_months:]
    cols = trend_metrics[:2] + ([margin] if margin else []) + trend_metrics[2:]
    cols += [m for m in ("core_operating_margin",) if m in monthly and m not in cols]
    lines += ["", f"## Monthly trend (last {len(keys)} months)", ""]
    rows = []
    for k in keys:
        row = [_label(k)]
        for m in cols:
            val, note = monthly[m]["values"].get(k), monthly[m]["notes"].get(k)
            row.append(pct(val, note) if monthly[m]["unit"] == "percent" else amt(val))
        rows.append(row)
    lines += _table(["Month"] + [monthly[m]["label"] for m in cols], rows)

    ttm_series = r["income_statement"]["ttm"]
    ttm_cols = [m for m in ("revenue", "gross_profit", "gross_margin", "core_operating_income", "core_operating_margin")
                if m in ttm_series and m in monthly]
    ttm_keys = [k for k in r["periods"]["available"]
                if k <= period and "revenue" in ttm_series and ttm_series["revenue"]["values"].get(k) is not None]
    if ttm_keys:
        lines += ["", "## TTM trend (twelve months to each month)", "",
                  "Each row is the twelve months ending that month: amounts are 12-month sums and margins are ratios "
                  "of those sums. These are not monthly figures; the monthly figures are in the table above.", ""]
        rows = []
        for k in ttm_keys:
            row = [_label(k)]
            for m in ttm_cols:
                val, note = ttm_series[m]["values"].get(k), ttm_series[m]["notes"].get(k)
                row.append(pct(val, note) if monthly[m]["unit"] == "percent" else amt(val, note))
            rows.append(row)
        lines += _table(["TTM to"] + [f"TTM {monthly[m]['label'].lower()}" for m in ttm_cols], rows)

    hist = r["income_statement"]["revenue_history"]
    lines += ["", f"## Revenue by month, full period ({_label(hist['first_period'])} to {_label(hist['last_period'])})", "",
              f"- Total revenue, {hist['months_in_data']} months: {amt(hist['total'], hist['total_note'])}"]
    for key, title in (("largest_mom_increase", "Largest MoM increase"), ("largest_mom_decrease", "Largest MoM decrease")):
        h = hist[key]
        lines.append(f"- {title}: " + (f"{_label(h['period'])}, {amt(h['abs_change'], signed=True)} "
                                       f"({pct(h['pct_change'], h['note'], signed=True)}), from {amt(h['prior_value'])} "
                                       f"in {_label(h['prior_period'])} to {amt(h['value'])}" if h else "none"))
    incomplete = (f" (months with a value only; extremes incomplete: {NOTE_TEXT.get(hist['extremes_note'], hist['extremes_note'])})"
                  if hist["extremes_note"] else "")
    for key, title in (("highest_month", "Highest-revenue month"), ("lowest_month", "Lowest-revenue month")):
        h = hist[key]
        lines.append(f"- {title}: " + (f"{_label(h['period'])}, {amt(h['value'])}{incomplete}" if h
                                       else f"n/a ({NOTE_TEXT.get(hist['extremes_note'], hist['extremes_note'])})"))
    lines.append("")
    lines += _table(["Month", "Revenue", "Prior month", "MoM", "MoM %"],
                    [[_label(m["period"]), amt(m["value"]), amt(m["prior_value"]),
                      amt(m["abs_change"], m["note"], signed=True), pct(m["pct_change"], m["note"], signed=True)]
                     for m in hist["monthly"]])

    def line_table(title: str, items: list[dict[str, Any]], share_label: str) -> None:
        nonlocal lines
        if not items:
            return
        active = [i for i in items if not _inactive(i)]
        active.sort(key=lambda i: -abs(i["ttm"] if i["ttm"] is not None else (i["value"] or 0)))
        rows = []
        for i in active:
            mom_a, mom_p = _change(i["mom"], "amount")
            rows.append([i["label"], amt(i["value"]), mom_a, mom_p, pct(i["yoy"]["pct_change"], i["yoy"]["note"], True),
                         amt(i["ttm"], i["ttm_note"]), pct(i["ttm_pct_of_revenue"])])
        lines += ["", f"## {title}", ""]
        lines += _table(["Line", _label(period), "MoM", "MoM %", "YoY %", "TTM", share_label], rows)
        if len(active) < len(items):
            lines += ["", f"{len(items) - len(active)} line(s) with no activity this month, last month or in the TTM omitted."]

    line_table("Revenue streams", ap["revenue_streams"], "% of TTM revenue")
    line_table("Cost of goods sold", ap["cogs_accounts"], "% of TTM revenue")
    line_table("Operating expenses by category", ap["opex_categories"], "% of TTM revenue")
    if ap["other_accounts"]:
        line_table("Other income and expenses", ap["other_accounts"], "% of TTM revenue")

    lines += ["", f"## Highlights for {_label(period)} (rule-based; what moved, not why)", ""]
    if r["highlights"]:
        lines += [f"{h['rank']}. {_highlight_text(h)}" for h in r["highlights"]]
    else:
        lines.append("No movements met the materiality thresholds.")
    if r["highlights_dropped"]:
        lines.append(f"({r['highlights_dropped']} lower-ranked highlights omitted; see analysis.json thresholds.)")

    lines += ["", "## Unusual months across the full history (accounts vs their median)", ""]
    for h in r["history_outliers"]:
        lines.append(f"- {_label(h['period'])}: {h['label']} ({h['scope'].replace('_', ' ')}) {amt(h['current'])} "
                     f"vs median {amt(h['baseline'])} ({amt(h['abs_change'], signed=True)})")
    if not r["history_outliers"]:
        lines.append("None met the thresholds.")

    bs = r.get("balance_sheet")
    if bs:
        lines += ["", f"## Balance sheet (month-end balances; not summed) - {_label(period)}", ""]
        rows = []
        for item in bs["analysis_period"]:
            rows.append([item["label"], amt(item["value"]), amt(item["mom"]["abs_change"], item["mom"]["note"], True),
                         amt(item["vs_12_months_earlier"]["prior"], item["vs_12_months_earlier"]["note"]),
                         amt(item["vs_12_months_earlier"]["abs_change"], item["vs_12_months_earlier"]["note"], True)])
        lines += _table(["Item", _label(period), "Change vs prior month", "12 months earlier", "Change vs 12 months"], rows)
        if bs["highlights"]:
            lines += ["", "Balance movements at or above the balance-sheet materiality threshold:"]
            lines += [f"- [{'MoM' if h['type'] == 'mom_change' else 'vs 12 months'}] {h['label']}: {amt(h['current'])} vs "
                      f"{amt(h['baseline'])} at {_label(h['comparison'])} ({amt(h['abs_change'], signed=True)}, "
                      f"{pct(h['pct_change'], h.get('note'), signed=True)})" for h in bs["highlights"]]

    lines += ["", "## Assumptions", ""] + [f"- {a}" for a in r["assumptions"]]
    lines += ["", "## Limitations", ""] + [f"- {a}" for a in r["limitations"]]
    return "\n".join(lines) + "\n"

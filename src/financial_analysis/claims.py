"""Check the financial claims in a commentary against the saved analysis.

    findings = check_claims(commentary, analysis, brief)

The commentary is read sentence by sentence; a table row counts as one sentence. When a sentence
names something the analysis measures, its numbers are claims about that thing and are checked. That
covers a metric (revenue, gross margin, net income...), an account, an expense category or a
balance-sheet figure. Each finding has a kind:

  misattributed      the number is none of the figures analysis.json holds for anything the sentence
                     names, for a month the sentence names or the analysis month. Those figures are a
                     monthly value, a MoM or YoY change, a TTM or YTD figure, a share of TTM revenue,
                     or the median behind an "unusual" flag. The check uses the written precision and
                     unit: % or pp against an amount
  contradicts        the number is such a figure, but the sentence says the opposite. That means an
                     explicit + or - sign that differs, a direction word that disagrees with the change
                     ("rose", "fell", "improved", "narrowed" for a loss...), or "profit" for a negative
                     result and "loss" for a positive one
  forecast           a number in a forward-looking sentence ("expect", "will", "next quarter"...). The
                     pipeline makes no forecasts, so such a number has no source
  unsupported_cause  a cause stated as fact ("because", "driven by", "due to"...), unless the explanation
                     is a line item from the analysis quoted with a figure that checks out (an
                     arithmetic attribution) or is about the workbook or config. A labelled hypothesis
                     ("Possible explanation (unconfirmed): ...") or a question is never a finding
  untraced           a number in a sentence that names nothing measurable and is not printed in brief.md

What it cannot do: it does not understand language. It recognises only the vocabulary below, the
analysis's own account and category names, and the sentence it is reading, so it is a guard
against the common ways commentary goes wrong, not a proof that the commentary is true. A wrong
claim with no number, a figure in a sentence that names nothing, a cause phrased without a marker
word, a margin change written in % instead of pp (both are read as percentages), or a wrong month
that happens to share the analysis month's figure can all pass. The reviewer remains the final check.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# Standard library only (like trace.py), so the skill's check script runs without the pipeline's dependencies.

AMOUNT, PERCENT = "amount", "percent"
# Metrics where an increase is good; mirrors highlights.FAVOURABLE_WHEN_UP (a test keeps them equal).
FAVOURABLE_WHEN_UP = {"revenue", "gross_profit", "operating_income", "core_operating_income", "net_income",
                      "other_income", "gross_margin", "operating_margin", "core_operating_margin", "net_margin"}
LEVEL, CHANGE = "level", "change"

# Identifiers and dates, blanked before numbers are read (length-preserving, so positions stay valid).
HASH = re.compile(r"\b[0-9a-f]{64}\b")
ISO_MONTH = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
NUMBER = re.compile(r"(?<![\w.])([-+\u2212]?)(\d(?:[\d,]*\d)?(?:\.\d+)?)")  # never ends in a comma
IGNORED = re.compile(r"(19|20)\d\d|\d")  # years and single digits, when the number names nothing measurable
YEAR = re.compile(r"(19|20)\d\d")
UNIT_PERCENT = re.compile(r"\s?(?:%|pp\b|percentage points?\b)")
# Counts, not figures ("3 months", "12-month"). "200 month over month" is an amount, not a count of months.
COUNT = re.compile(r"\s*-?\s*(?:months?(?![\s-]+(?:over|on)[\s-]+month)|years?(?![\s-]+(?:over|on)[\s-]+year)|"
                   r"weeks?|days?|accounts?|errors?|warnings?|info|notes?|checks?|items?|categories|streams?|times)\b|"
                   r"\s+(?:out of|of the (?:last|past))\b")
ROW_REFERENCE = re.compile(r"\brows?\s*$")  # "row 17": a workbook row, checked against the accounts' source rows
LIST_MARKER = re.compile(r"^(\s*(?:[-*+]\s+)?)(\d+)([.)]\s)")
MONTH = re.compile(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
                   r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b(?:\s+((?:19|20)\d\d))?")
MONTH_NUMBER = {name: i for i, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# Direction words. "improve"/"worsen" need to know which way is good; "narrow"/"widen" are read only when
# the sentence is about a loss (a narrowing loss is an increase in the result).
WORDS = {
    "increase": r"rose|rise[sn]?|rising|up|increase[sd]?|increasing|grew|grow(?:s|n|th|ing)?|higher|gain(?:ed|s)?|"
                r"jump(?:ed|s)?|climb(?:ed|s)?",
    "decrease": r"fell|fall(?:s|en|ing)?|down|decrease[sd]?|decreasing|decline[sd]?|declining|drop(?:ped|s)?|lower|"
                r"reduc(?:ed|tion)|shr[au]nk|contraction",
    "improve": r"improv(?:ed|es|ing|ement)|better|recover(?:ed|s|y)|strengthen(?:ed|s)?",
    "worsen": r"worsen(?:ed|s|ing)?|worse|deteriorat(?:ed|es|ion)|weaken(?:ed|s)?",
    "narrow": r"narrow(?:ed|s|ing)?",
    "widen": r"widen(?:ed|s|ing)?|deepen(?:ed|s|ing)?",
    "profit": r"profit(?:s|able)?|surplus",
    "loss": r"loss(?:es)?|deficit|loss-making",
}
WORD = {k: re.compile(rf"\b(?:{v})\b") for k, v in WORDS.items()}
DIRECTIONS = ("increase", "decrease", "improve", "worsen", "narrow", "widen")

CAUSE = re.compile(r"\b(because|due to|driven by|drove|caused by|as a result of|owing to|thanks to|resulted from|"
                   r"resulting from|attributable to|on the back of|led to|reflect(?:s|ed|ing)?)\b")
HYPOTHESIS = re.compile(r"\b(unconfirmed|not confirmed|to be confirmed|possible explanation|hypothesis)\b")
METHODOLOGY = re.compile(r"\b(workbook|config|configuration|classification|classified|layout|section|data notes?)\b")
FORWARD = re.compile(r"\b(expect(?:s|ed|ing)?|forecast(?:s|ed|ing)?|project(?:s|ed|ion|ions)|will|next (?:month|quarter|"
                     r"year)|going forward|outlook|anticipat\w*|on track to|budget(?:s|ed)?)\b")

# Generic names for the pipeline's metrics. A phrase names every metric it could mean ("operating expenses"
# is the as-presented, core and non-operating views); the longest phrase wins, so "core operating loss"
# is never read as "operating loss". The second element is the profit/loss reading the phrase implies.
_OPEX = ("metric:opex_total", "metric:core_opex", "metric:non_operating_in_opex", "metric:opex_to_revenue")
_OPINC = ("metric:operating_income", "metric:core_operating_income")
_CORE_OPINC = ("metric:core_operating_income",)
PHRASES: dict[str, tuple[tuple[str, ...], str | None]] = {
    "revenue": (("metric:revenue",), None), "revenues": (("metric:revenue",), None),
    "total revenue": (("metric:revenue",), None),
    "cost of goods sold": (("metric:cogs",), None), "cogs": (("metric:cogs",), None),
    "cost of sales": (("metric:cogs",), None),
    "gross profit": (("metric:gross_profit",), None), "gross loss": (("metric:gross_profit",), "loss"),
    "gross margin": (("metric:gross_margin",), None),
    "operating expenses": (_OPEX, None), "opex": (_OPEX, None),
    "core operating expenses": (("metric:core_opex",), None), "core opex": (("metric:core_opex",), None),
    "operating income": (_OPINC, None), "operating result": (_OPINC, None),
    "operating profit": (_OPINC, "profit"), "operating loss": (_OPINC, "loss"),
    "core operating income": (_CORE_OPINC, None), "core operating result": (_CORE_OPINC, None),
    "core operating profit": (_CORE_OPINC, "profit"), "core operating loss": (_CORE_OPINC, "loss"),
    "operating margin": (("metric:operating_margin", "metric:core_operating_margin"), None),
    "core operating margin": (("metric:core_operating_margin",), None),
    "net income": (("metric:net_income",), None), "net result": (("metric:net_income",), None),
    "net profit": (("metric:net_income",), "profit"), "net loss": (("metric:net_income",), "loss"),
    "net margin": (("metric:net_margin",), None),
    "non-cash expenses": (("metric:non_cash_expenses",), None),
    "cash": (("bs:cash",), None), "working capital": (("bs:working_capital",), None),
    "total assets": (("bs:total_assets",), None), "total liabilities": (("bs:total_liabilities",), None),
    "total equity": (("bs:total_equity",), None), "current assets": (("bs:current_assets",), None),
    "current liabilities": (("bs:current_liabilities",), None),
}
PROFIT_LIKE = {"metric:gross_profit", "metric:operating_income", "metric:core_operating_income", "metric:net_income"}
COST_METRICS = {"cogs", "opex_total", "core_opex", "non_cash_expenses", "opex_to_revenue"}
# Account or category names too generic to read as a mention in prose.
GENERIC_LABELS = {"other", "total", "sales", "income", "expense", "expenses", "revenue", "general", "miscellaneous",
                  "cash", "misc"}


@dataclass(frozen=True)
class Fact:
    item: str
    kind: str  # LEVEL or CHANGE
    unit: str  # AMOUNT or PERCENT
    value: float
    periods: frozenset[str]


@dataclass
class Finding:
    kind: str  # misattributed | contradicts | forecast | unsupported_cause | untraced
    token: str
    context: str
    reason: str


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip().lower()


def canonical(token: str) -> str:
    return token.lstrip("+-\u2212").replace(",", "")


def brief_numbers(brief: str) -> set[str]:
    """Every number printed in brief.md, ignoring sign and thousands separators."""
    return {canonical(sign + digits) for sign, digits in NUMBER.findall(ISO_MONTH.sub(" ", HASH.sub(" ", brief)))}


class FactIndex:
    """Every figure the analysis holds, by the item it belongs to, with the months it describes."""

    def __init__(self, analysis: dict[str, Any]):
        self.target: str = analysis["periods"]["analysis_period"]
        self.available: list[str] = analysis["periods"]["available"]
        self.facts: dict[str, list[Fact]] = defaultdict(list)
        self.good_when_up: dict[str, bool | None] = {}
        lines: dict[str, str] = {}  # normalised label -> item

        inc = analysis["income_statement"]
        self.source_rows = {a["source_row"] for a in inc["accounts"]}
        for metric, series in inc["monthly"].items():
            item = f"metric:{metric}"
            self.good_when_up[item] = (True if metric in FAVOURABLE_WHEN_UP else
                                       False if metric in COST_METRICS else None)
            for p, v in series["values"].items():
                self._add(item, LEVEL, series["unit"], v, {p})
        for metric, series in inc["ttm"].items():
            unit = inc["monthly"].get(metric, {}).get("unit", AMOUNT)
            for p, v in series["values"].items():
                self._add(f"metric:{metric}", LEVEL, unit, v, {p})
        for row in analysis["analysis_period"]["metrics"]:
            item = f"metric:{row['metric']}"
            self._add(item, LEVEL, row["unit"], row["ytd"], {self.target})
            for key in ("mom", "yoy"):
                self._add_change(item, row["unit"], row[key])
        history = inc["revenue_history"]
        for r in history["monthly"]:
            for unit, key in ((AMOUNT, "abs_change"), (PERCENT, "pct_change")):
                self._add("metric:revenue", CHANGE, unit, r[key], {r["period"], r["prior_period"]})
        self._add("metric:revenue", LEVEL, AMOUNT, history["total"],
                  {self.target, history["first_period"], history["last_period"]})

        for account in inc["accounts"]:
            item = lines.setdefault(norm(account["label"]), f"line:{norm(account['label'])}")
            self.good_when_up[item] = account["section"] in ("revenue", "other_income")
            for p, v in account["values"].items():
                self._add(item, LEVEL, AMOUNT, v, {p})
        for category in inc["opex_categories"]:
            item = lines.setdefault(norm(category["name"]), f"line:{norm(category['name'])}")
            self.good_when_up.setdefault(item, False)
            for p, v in category["values"].items():
                self._add(item, LEVEL, AMOUNT, v, {p})
        ap = analysis["analysis_period"]
        for row in (*ap["revenue_streams"], *ap["cogs_accounts"], *ap["opex_categories"], *ap["opex_accounts"],
                    *ap["other_accounts"]):
            item = lines.setdefault(norm(row["label"]), f"line:{norm(row['label'])}")
            for key in ("mom", "yoy"):
                self._add_change(item, AMOUNT, row[key])
            self._add(item, LEVEL, AMOUNT, row["ttm"], {self.target})
            self._add(item, LEVEL, PERCENT, row["ttm_pct_of_revenue"], {self.target})
            self._add(item, LEVEL, AMOUNT, row["median_other_months"], {self.target})

        bs = analysis.get("balance_sheet")
        if bs:
            for metric, series in bs["monthly"].items():
                self.good_when_up[f"bs:{metric}"] = None
                for p, v in series["values"].items():
                    self._add(f"bs:{metric}", LEVEL, AMOUNT, v, {p})
            for row in bs["analysis_period"]:
                for key in ("mom", "vs_12_months_earlier"):
                    self._add_change(f"bs:{row['metric']}", AMOUNT, row[key])

        for h in (*analysis.get("highlights", []), *analysis.get("history_outliers", []),
                  *((bs or {}).get("highlights", []))):
            scope = h["scope"]
            item = (f"metric:{h['key']}" if scope in ("metric", "margin") else f"bs:{h['key']}" if scope == "balance_sheet"
                    else lines.get(norm(h["label"])))
            if item is None:
                continue
            unit = PERCENT if scope == "margin" else AMOUNT
            compared = {h["comparison"]} if ISO_MONTH.fullmatch(str(h["comparison"])) else set()
            periods = {h["period"]} | compared
            self._add(item, LEVEL, unit, h["current"], {h["period"]})
            # The baseline is the comparison month's value; only a median baseline (no month) belongs to the period.
            self._add(item, LEVEL, unit, h["baseline"], compared or {h["period"]})
            self._add(item, CHANGE, unit, h.get("abs_change"), periods)
            self._add(item, CHANGE, PERCENT, h.get("pct_change") if unit == AMOUNT else h.get("pp_change"), periods)

        # Mentionable names: the generic metric phrases for items this analysis has, plus its own
        # account and category labels (also without a trailing "expense(s)").
        self.phrases: dict[str, tuple[frozenset[str], str | None]] = {}
        for phrase, (items, polarity) in PHRASES.items():
            present = frozenset(i for i in items if i in self.facts)
            if present:
                self.phrases[phrase] = (present, polarity)
        for label, item in lines.items():
            for phrase in {label, re.sub(r"\s+expenses?$", "", label)}:
                if len(phrase) < 3 or phrase in GENERIC_LABELS:
                    continue
                items, polarity = self.phrases.get(phrase, (frozenset(), None))
                self.phrases[phrase] = (items | {item}, polarity)
        alternatives = "|".join(re.escape(p) for p in sorted(self.phrases, key=len, reverse=True))
        self.mention = re.compile(rf"(?<!\w)(?<!non-)({alternatives})(?!\w)")

    def _add(self, item: str, kind: str, unit: str, value: Any, periods: set[str]) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.facts[item].append(Fact(item, kind, unit, float(value), frozenset(p for p in periods if p)))

    def _add_change(self, item: str, unit: str, change: dict[str, Any]) -> None:
        periods = {self.target, change.get("comparison_period")}
        if unit == PERCENT:
            self._add(item, CHANGE, PERCENT, change.get("pp_change"), periods)
        else:
            self._add(item, CHANGE, AMOUNT, change.get("abs_change"), periods)
            self._add(item, CHANGE, PERCENT, change.get("pct_change"), periods)

    def periods_named(self, text: str) -> set[str]:
        """Months named in a sentence as YYYY-MM. A month without a year is the latest such month up to
        the analysis month. Only capitalised names count, so the verb "may" is not May."""
        found = set(re.findall(r"\b\d{4}-\d{2}\b", text))
        for m in MONTH.finditer(text):
            month = MONTH_NUMBER[m.group(1)[:3].lower()]
            if m.group(2):
                found.add(f"{m.group(2)}-{month:02d}")
            else:
                candidates = [p for p in self.available if int(p[5:]) == month and p <= self.target]
                if candidates:
                    found.add(max(candidates))
        return found


@dataclass
class _Number:
    start: int
    end: int
    token: str
    sign: int  # +1 / -1 when written with a sign, else 0
    value: Decimal
    places: int
    unit: str
    is_count: bool
    is_row: bool


def _numbers(text: str) -> list[_Number]:
    out = []
    for m in NUMBER.finditer(text):
        sign_text, digits = m.groups()
        after = text[m.end():]
        if YEAR.fullmatch(digits) and not sign_text and not UNIT_PERCENT.match(after):
            continue  # a year
        places = len(digits.split(".")[1]) if "." in digits else 0
        out.append(_Number(m.start(), m.end(), m.group(), -1 if sign_text in "-\u2212" and sign_text else
                           1 if sign_text == "+" else 0, Decimal(digits.replace(",", "")), places,
                           PERCENT if UNIT_PERCENT.match(after) else AMOUNT, bool(COUNT.match(after)),
                           bool(ROW_REFERENCE.search(text[:m.start()].lower()))))
    return out


def _clean(sentence: str) -> str:
    """Markdown emphasis, hashes and ISO dates become spaces; list numbering is blanked. Length-preserving."""
    text = re.sub(r"[*_`|]", " ", sentence)
    text = HASH.sub(lambda m: " " * len(m.group()), text)
    text = ISO_MONTH.sub(lambda m: " " * len(m.group()), text)
    return LIST_MARKER.sub(lambda m: m.group(1) + " " * len(m.group(2)) + m.group(3), text)


def sentences(commentary: str) -> list[str]:
    """Sentences of the commentary. Headings and the source line are skipped; wrapped lines of one paragraph
    or list item are joined; a table row is one sentence."""
    blocks: list[str] = []
    current: list[str] = []
    for line in commentary.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("Source: analysis"):
            current = []
            continue
        if stripped.startswith("|"):
            if not re.fullmatch(r"[|\s:-]+", stripped):
                blocks.append(stripped)
            current = []
            continue
        if re.match(r"([-*+]|\d+[.)])\s", stripped) or not current:
            current = [stripped]
            blocks.append(stripped)
        else:
            current.append(stripped)
            blocks[-1] = " ".join(current)
    out = []
    for block in blocks:
        out += [block] if block.startswith("|") else [s for s in re.split(r"(?<=[.!?;])\s+", block) if s.strip()]
    return out


def _expected_positive(fact: Fact, word: str, good: bool | None) -> bool | None:
    if word in ("increase", "narrow"):
        return True
    if word in ("decrease", "widen"):
        return False
    if good is None:
        return None
    return good if word == "improve" else not good


def _last_word(text: str, kinds: tuple[str, ...]) -> str | None:
    found = [(m.start(), k) for k in kinds for m in WORD[k].finditer(text)]
    return max(found)[1] if found else None


def check_sentence(index: FactIndex, raw: str, known: set[str]) -> list[Finding]:
    text = _clean(raw)
    low = text.lower()
    mentions = [(m.start(), m.end(), *index.phrases[m.group(1)]) for m in index.mention.finditer(low)]
    masked = list(low)
    for start, end, *_ in mentions:
        masked[start:end] = " " * (end - start)
    masked = "".join(masked)
    question = raw.rstrip().rstrip("*_ ").endswith("?")
    hypothesis = question or bool(HYPOTHESIS.search(low))
    forward = not question and bool(FORWARD.search(masked))
    loss_context = any(pol == "loss" for *_, pol in mentions) or bool(WORD["loss"].search(masked))
    named = index.periods_named(text) | {index.target}
    items = frozenset().union(*(m[2] for m in mentions)) if mentions else frozenset()
    context = re.sub(r"\s+", " ", raw).strip()[:160]

    findings: list[Finding] = []
    validated: list[tuple[int, frozenset[str]]] = []  # (position, items) of numbers that checked out
    previous_end = 0
    for n in _numbers(text):
        between, previous_end = masked[previous_end:n.start], n.end
        if forward:
            findings.append(Finding("forecast", n.token, context, "a number in a forward-looking sentence; "
                                    "the analysis contains no forecasts"))
            continue
        if n.is_row and not n.sign and not n.places:
            if int(n.value) not in index.source_rows:
                findings.append(Finding("untraced", n.token, context, "no account in analysis.json is on this row"))
            continue
        if n.is_count or not items:
            if canonical(n.token) in known or IGNORED.fullmatch(canonical(n.token)):
                continue
            findings.append(Finding("untraced", n.token, context, "not printed in brief.md"))
            continue
        tolerance = float(Decimal("0.5") * Decimal(10) ** -n.places) + 1e-6
        matches = [f for item in items for f in index.facts[item]
                   if f.unit == n.unit and f.periods & named and abs(abs(f.value) - float(n.value)) <= tolerance]
        if not matches:
            names = ", ".join(sorted(raw[s:e].strip() for s, e, *_ in mentions))
            findings.append(Finding("misattributed", n.token, context,
                                    f"not a figure of {names} for {', '.join(sorted(named))} in analysis.json"))
            continue
        direction = _last_word(between, DIRECTIONS)
        if direction in ("narrow", "widen") and not loss_context:
            direction = None
        stated = _last_word(between, ("profit", "loss"))
        consistent = []
        for fact in matches:
            if n.sign and fact.value != 0 and (n.sign > 0) != (fact.value > 0):
                continue
            if fact.kind == CHANGE and direction:
                positive = _expected_positive(fact, direction, index.good_when_up.get(fact.item))
                if positive is not None and (fact.value == 0 or (fact.value > 0) != positive):
                    continue
            if fact.kind == LEVEL and fact.item in PROFIT_LIKE:
                polarity = stated or next((pol for s, e, its, pol in reversed(mentions)
                                           if e <= n.start and fact.item in its and pol), None)
                if polarity and fact.value != 0 and (fact.value > 0) != (polarity == "profit"):
                    continue
            consistent.append(fact)
        if consistent:
            validated.append((n.start, frozenset(f.item for f in consistent)))
        else:
            fact = matches[0]
            findings.append(Finding("contradicts", n.token, context,
                                    f"analysis.json has {fact.value:+,.2f} for this figure "
                                    f"({fact.kind} of {fact.item.split(':', 1)[1]}); the sentence says otherwise"))

    cause = CAUSE.search(masked)
    if cause and not hypothesis:
        after = cause.start()
        named_after = frozenset().union(*(its for s, e, its, _ in mentions if s >= after)) if mentions else frozenset()
        supported = any(pos > after and its & named_after for pos, its in validated)
        if not supported and not METHODOLOGY.search(low[cause.end():]):
            findings.append(Finding("unsupported_cause", cause.group(1), context,
                                    "a cause stated as fact; quote the line item and its figure from the analysis, "
                                    "or label it 'Possible explanation (unconfirmed): ...' or ask it as a question"))
    return findings


def check_claims(commentary: str, analysis: dict[str, Any], brief: str) -> list[Finding]:
    index = FactIndex(analysis)
    known = brief_numbers(brief)
    return [f for sentence in sentences(commentary) for f in check_sentence(index, sentence, known)]

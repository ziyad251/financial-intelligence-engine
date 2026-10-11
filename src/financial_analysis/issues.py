"""Data-quality issues raised anywhere in the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ERROR = "error"      # analysis output cannot be trusted; commentary must not be produced
WARNING = "warning"  # output is usable but a reviewer must look at this
INFO = "info"        # context the reviewer / commentary should know about

_SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    sheet: str | None = None
    account: str | None = None
    period: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "severity": self.severity, "message": self.message}
        for name in ("sheet", "account", "period"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if self.details:
            out["details"] = self.details
        return out


def sort_issues(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: (_SEVERITY_ORDER[i.severity], i.code, i.sheet or "", i.account or "", i.period or ""))


def overall_status(issues: list[Issue]) -> str:
    if any(i.severity == ERROR for i in issues):
        return "fail"
    if any(i.severity == WARNING for i in issues):
        return "pass_with_warnings"
    return "pass"


class AnalysisError(Exception):
    """Raised when the workbook cannot be analysed at all (e.g. no income statement found)."""

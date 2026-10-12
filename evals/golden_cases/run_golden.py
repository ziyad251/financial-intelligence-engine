"""Golden evaluation suite: run the real analysis CLI on synthetic workbooks and grade the outputs.

    python evals/golden_cases/run_golden.py            run every case; exit 1 if any check fails
    python evals/golden_cases/run_golden.py --case 04  run one case (id or prefix)
    python evals/golden_cases/run_golden.py --keep     keep the temporary folders for inspection

Each case (cases/*.json) names a fixture generator (fixtures.py), an optional company config
and one or more steps. A step runs `python -m financial_analysis` in a subprocess, exactly as a
user would, with a fresh temporary output folder per case. Expected values in the case files
were calculated by hand (each carries its arithmetic in `calc`); this runner never imports the
engine's calculation code, it only reads the files the CLI writes.

Freshness: before any check, a run that wrote outputs must have a last_run.json whose workbook
hash and exit code match this run and whose run_at is not older than the run's start. The
analysis is then read from the file last_run.json names, so a stale analysis.json left by an
earlier run can never be graded as this run's result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from fixtures import FIXTURES  # noqa: E402

MISSING = object()
DEFAULT_TOL = 0.005  # the engine rounds amounts and percentages to 2 dp, half-up


@dataclass
class Failure:
    step: int
    check: str
    expected: Any
    actual: Any
    reason: str


@dataclass
class CaseResult:
    case_id: str
    title: str
    checks: int = 0
    failures: list[Failure] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def load_cases(selector: str | None = None) -> list[dict[str, Any]]:
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((HERE / "cases").glob("*.json"))]
    return [c for c in cases if not selector or c["id"].startswith(selector)]


# --- path resolution: "analysis_period.metrics[metric=revenue].mom.pct_change" ---------------------

_TOKEN = re.compile(r"([^.\[\]]+)|\[([^=\]]+)=([^\]]*)\]")


def resolve(doc: Any, path: str) -> Any:
    cur = doc
    for name, key, value in _TOKEN.findall(path):
        if cur is MISSING:
            return MISSING
        if name:
            if isinstance(cur, dict) and name in cur:
                cur = cur[name]
            elif isinstance(cur, list) and name.isdigit() and int(name) < len(cur):
                cur = cur[int(name)]
            else:
                return MISSING
        else:
            matches = [x for x in cur if isinstance(x, dict) and str(x.get(key)) == value] if isinstance(cur, list) else []
            if len(matches) != 1:
                return MISSING
            cur = matches[0]
    return cur


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _show(x: Any) -> str:
    return "<missing>" if x is MISSING else json.dumps(x, ensure_ascii=False)


# --- running ---------------------------------------------------------------------------------------

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(REPO / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    return subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8")


def _source_line(brief: str) -> str:
    m = re.search(r"`(Source: analysis [^`]+)`", brief)
    if not m:
        raise ValueError("brief.md prints no commentary source line")
    return m.group(1)


def _remove_own_tempdir(tmp: Path) -> None:
    """Delete a case folder only if it is exactly a folder this runner created: directly inside the system
    temp directory and named golden-<case id>-*. Anything else is left in place and reported."""
    base = Path(tempfile.gettempdir()).resolve()
    target = tmp.resolve()
    if target.parent == base and target.name.startswith("golden-") and target.is_dir() and any(target.iterdir()):
        shutil.rmtree(target)
    else:
        print(f"  not removed (outside the runner's own temp folders): {tmp}")


def run_case(case: dict[str, Any], keep: bool = False, workdir: Path | None = None) -> CaseResult:
    """Run one case. With `workdir` (e.g. pytest's tmp_path) the case folder is created there and left for
    its owner to manage; otherwise a golden-* folder in the system temp directory is used and removed."""
    result = CaseResult(case["id"], case["title"])
    tmp = Path(tempfile.mkdtemp(prefix=f"golden-{case['id']}-", dir=workdir))
    try:
        data, out = tmp / "data", tmp / "out"
        workbook = FIXTURES[case["fixture"]](data / f"{case['id']}.xlsx")
        sha = hashlib.sha256(workbook.read_bytes()).hexdigest()
        if case.get("config") is not None:
            cfg = tmp / "company.json"
            cfg.write_text(json.dumps(case["config"]), encoding="utf-8")
            config_args = ["--config", str(cfg)]
        else:
            config_args = ["--no-config"]
        snapshots: dict[int, dict[str, bytes]] = {}
        for n, step in enumerate(case["steps"], start=1):
            start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)
            proc = _run([sys.executable, "-m", "financial_analysis", str(workbook), *config_args, "--require-config",
                         "--input-dir", str(data), "--out-dir", str(out), "--quiet", *step.get("args", [])])
            ctx = {"step": n, "out": out, "sha": sha, "proc": proc, "doc": None, "last_run": None, "snapshots": snapshots}

            def check(name: str, ok: bool, expected: Any, actual: Any, reason: str) -> bool:
                result.checks += 1
                if not ok:
                    result.failures.append(Failure(n, name, expected, actual, reason))
                return ok

            if not check("exit_code", proc.returncode == step["exit_code"], step["exit_code"], proc.returncode,
                         f"CLI exit code differs. stderr: {proc.stderr.strip()[-300:]}"):
                continue
            if proc.returncode in (0, 2):
                fresh = _fresh_outputs(ctx, start, check)
                if not fresh:
                    continue
            if step.get("write_commentary"):
                brief = (out / "brief.md").read_text(encoding="utf-8")
                (out / "commentary.md").write_text(step["write_commentary"] + "\n\n" + _source_line(brief) + "\n",
                                                   encoding="utf-8")
            if "trace_exit" in step:
                t = _run([sys.executable, "-m", "financial_analysis.trace", str(out)])
                check("commentary check (financial_analysis.trace) exit code", t.returncode == step["trace_exit"],
                      step["trace_exit"], t.returncode, f"trace output: {t.stdout.strip()[-300:]}")
            for spec in step.get("checks", []):
                _grade(spec, ctx, check)
            snapshots[n] = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()} if out.exists() else {}
    except Exception as exc:  # a broken fixture or case file is a failed case, not a crash
        result.failures.append(Failure(0, "runner", None, None, f"{type(exc).__name__}: {exc}"))
    finally:
        if keep:
            print(f"  kept: {tmp}")
        elif workdir is None:
            _remove_own_tempdir(tmp)
    return result


def _fresh_outputs(ctx: dict[str, Any], start: datetime, check) -> bool:
    out, proc = ctx["out"], ctx["proc"]
    try:
        last = json.loads((out / "last_run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return check("freshness: last_run.json", False, "written by this run", None, str(exc))
    run_at = datetime.fromisoformat(last.get("run_at", "1970-01-01T00:00:00+00:00"))
    ok = (check("freshness: last_run.sha256 is this fixture", last.get("sha256") == ctx["sha"], ctx["sha"],
                last.get("sha256"), "outputs are not from this workbook")
          & check("freshness: last_run.exit_code", last.get("exit_code") == proc.returncode, proc.returncode,
                  last.get("exit_code"), "last_run.json does not describe this run")
          & check("freshness: last_run.run_at not older than the run", run_at >= start, f">= {start.isoformat()}",
                  last.get("run_at"), "last_run.json is from an earlier run"))
    if not ok:
        return False
    ctx["last_run"] = last
    ctx["doc"] = json.loads((out / last["analysis_json"]).read_text(encoding="utf-8"))
    return True


def _grade(spec: dict[str, Any], ctx: dict[str, Any], check) -> None:
    kind, doc, out = spec["type"], ctx["doc"], ctx["out"]
    label = spec.get("calc") or spec.get("why") or ""
    if kind in ("equals", "approx", "absent"):
        source = ctx["last_run"] if spec.get("in") == "last_run" else doc
        actual = resolve(source, spec["path"]) if source is not None else MISSING
        name = f"{spec.get('in', 'analysis')}:{spec['path']}" + (f"  [{label}]" if label else "")
        if kind == "absent":
            check(name, actual is MISSING, "<absent>", actual, "should not be present")
        elif kind == "equals":
            check(name, actual is not MISSING and actual == spec["value"], spec["value"], actual, "not equal")
        else:
            tol = spec.get("tol", DEFAULT_TOL)
            a, e = _num(actual), _num(spec["value"])
            ok = a is not None and e is not None and abs(a - e) <= tol
            check(name, ok, f"{spec['value']} ± {tol}", actual if actual is not MISSING else "<missing>",
                  "outside tolerance" if a is not None else "not a number")
    elif kind == "status":
        check("status", doc is not None and doc.get("status") == spec["value"], spec["value"],
              doc.get("status") if doc else None, "validation status differs")
    elif kind == "issue_codes":
        codes = sorted({i["code"] for i in (doc or {}).get("validation", {}).get("issues", []) if i["severity"] == spec["severity"]})
        check(f"{spec['severity']} issue codes (exact set)", codes == sorted(spec["equals"]), sorted(spec["equals"]), codes,
              "unexpected or missing issue identifiers")
    elif kind == "issue_count":
        n = sum(1 for i in (doc or {}).get("validation", {}).get("issues", []) if i["code"] == spec["code"])
        check(f"number of {spec['code']} issues", n == spec["equals"], spec["equals"], n, "count differs")
    elif kind == "issue":
        issues = (doc or {}).get("validation", {}).get("issues", [])
        want = spec["match"]
        found = [i for i in issues if all(i.get(k) == v for k, v in want.items())]
        if not check(f"issue {want}", len(found) == 1, "exactly one matching issue", len(found), "issue not found"):
            return
        issue = found[0]
        for key, value in spec.get("details", {}).items():
            actual = resolve(issue.get("details", {}), key)
            if isinstance(value, dict) and set(value) == {"approx"}:  # numbers the engine stores as strings
                a = _num(actual)
                ok = a is not None and abs(a - value["approx"]) <= DEFAULT_TOL
                check(f"issue {want['code']} details.{key}", ok, f"{value['approx']} ± {DEFAULT_TOL}", actual, "outside tolerance")
            else:
                check(f"issue {want['code']} details.{key}", actual == value, value, actual, "issue detail differs")
        for text in spec.get("message_contains", []):
            check(f"issue {want['code']} message contains {text!r}", text in issue["message"], text, issue["message"],
                  "message lacks expected text")
    elif kind == "files":
        present = sorted(p.name for p in out.iterdir() if p.is_file()) if out.exists() else []
        check("output files (exact)", present == sorted(spec["equals"]), sorted(spec["equals"]), present, "files differ")
    elif kind == "file_contains":
        path = out / spec["file"]
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for snippet in spec["text"]:
            check(f"{spec['file']} contains {snippet!r}", snippet in text, snippet, "<not found>" if snippet not in text else snippet,
                  "text not found" if path.exists() else f"{spec['file']} does not exist")
    elif kind == "fixture_sha256":
        actual = resolve(doc, spec["path"])
        check(f"{spec['path']} is the fixture's sha256", actual == ctx["sha"], ctx["sha"], actual, "provenance differs")
    elif kind == "unchanged_since_step":
        before = ctx["snapshots"].get(spec["step"], {})
        for name in spec["files"]:
            now = (out / name).read_bytes() if (out / name).exists() else None
            check(f"{name} unchanged since step {spec['step']}", name in before and now == before[name],
                  "byte-identical", "changed" if now is not None else "missing", "earlier output was altered")
    else:
        check(f"unknown check type {kind!r}", False, None, None, "fix the case file")


def report(results: list[CaseResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"{'PASS' if r.passed else 'FAIL'}  {r.case_id:<28} {r.checks:>3} checks  {r.title}")
        for f in r.failures:
            lines.append(f"      step {f.step}: {f.check}\n        expected: {_show(f.expected)}\n"
                         f"        actual:   {_show(f.actual)}\n        reason:   {f.reason}")
    passed = sum(r.passed for r in results)
    lines.append(f"\n{len(results)} cases: {passed} passed, {len(results) - passed} failed; "
                 f"{sum(r.checks for r in results)} checks, {sum(len(r.failures) for r in results)} failed")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", help="run only cases whose id starts with this")
    parser.add_argument("--keep", action="store_true", help="keep each case's temporary folder")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cases = load_cases(args.case)
    if not cases:
        print(f"no case matches {args.case!r}")
        return 2
    results = [run_case(c, args.keep) for c in cases]
    print(report(results))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

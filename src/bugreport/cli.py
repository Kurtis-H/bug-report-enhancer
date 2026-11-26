"""`bugreport "checkout is broken"` - interactive, or non-interactive with --answers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from bugreport import __version__
from bugreport.llm import FakeHelper, OllamaHelper, parse_questions, parse_report_json
from bugreport.report import QUESTIONS, BugReport, completeness, missing
from bugreport.safety import redact, ungrounded
from bugreport.triage import duplicates, load_issues, suggest_severity


def rule_based(description: str, answers: dict[str, str]) -> BugReport:
    """The no-AI path: map answers straight onto fields."""
    title = description.strip().split("\n")[0].rstrip(".")
    return BugReport.from_dict(
        {
            "title": title[:1].upper() + title[1:80],
            "summary": description.strip(),
            "steps": answers.get("steps", ""),
            "expected": answers.get("expected", ""),
            "actual": answers.get("actual", ""),
            "environment": answers.get("environment", ""),
            "frequency": answers.get("frequency", ""),
            "evidence": answers.get("evidence", ""),
            "labels": ["bug"],
        }
    )


def report_text(r: BugReport) -> str:
    """The report's content as plain text (values only), for the grounding check."""
    return "\n".join([r.title, r.summary, *r.steps, r.expected, r.actual, r.environment, r.frequency, *r.evidence])


def to_markdown(r: BugReport, score: int, severity_reason: str, flagged: list[str], dupes, removed: list[str]) -> str:
    lines = [f"# {r.title}", "", r.summary, ""]
    lines += ["## Steps to reproduce", ""] + [f"{i}. {s}" for i, s in enumerate(r.steps, 1)] + [""]
    lines += ["## Expected", "", r.expected or "_unknown_", "", "## Actual", "", r.actual or "_unknown_", ""]
    lines += ["## Environment", "", r.environment or "_unknown_", ""]
    lines += [
        f"**Frequency:** {r.frequency or 'unknown'} · **Suggested severity:** {r.severity} ({severity_reason})",
        "",
    ]
    if r.evidence:
        lines += ["## Evidence", ""] + [f"- {e}" for e in r.evidence] + [""]
    lines += ["---", "", f"<sub>Report completeness: {score}/100 · labels: {', '.join(r.labels) or '-'}</sub>", ""]
    notes = []
    if flagged:
        notes.append(
            "⚠️ **Details the AI added that you didn't say. Verify or remove:** " + ", ".join(f"`{f}`" for f in flagged)
        )
    if removed:
        notes.append(f"🔒 Redacted before processing: {', '.join(removed)}")
    if dupes:
        notes.append("🔁 **Possible duplicates:** " + "; ".join(f"#{d.id} {d.title} ({d.score:.0%})" for d in dupes))
    if notes:
        lines += ["## Notes for the triager", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="bugreport", description="Turn a vague bug report into one a developer can act on."
    )
    p.add_argument("description", nargs="?", help="the bug, in your own words (or pipe it in)")
    p.add_argument("--answers", help="JSON file of answers by field, for non-interactive use")
    p.add_argument("--issues", help="JSON array of existing issues {id, title, body} to check for duplicates")
    p.add_argument("--no-ai", action="store_true", help="template questions and rule-based assembly only")
    p.add_argument("--provider", choices=["ollama", "fake"], default="ollama")
    p.add_argument("--model", default="llama3.1:8b")
    p.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://localhost:11434"))
    p.add_argument("--md", help="write markdown here")
    p.add_argument("--json", help="write JSON here")
    p.add_argument("--min-score", type=int, help="exit 1 if the final report scores lower (0-100)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    description = args.description or ("" if sys.stdin.isatty() else sys.stdin.read())
    if not description.strip():
        print("bugreport: describe the bug as an argument or on stdin", file=sys.stderr)
        return 2
    description, removed = redact(description)

    helper = (
        None if args.no_ai else (FakeHelper() if args.provider == "fake" else OllamaHelper(args.model, args.ollama_url))
    )
    draft = rule_based(description, {})
    gaps = missing(draft)
    before = completeness(draft)
    print(f"Completeness: {before}/100. Missing: {', '.join(gaps) or 'nothing'}", file=sys.stderr)

    questions = QUESTIONS if helper is None else None
    if helper and gaps:
        try:
            questions = parse_questions(helper.questions(description, gaps), gaps)
        except Exception as exc:  # noqa: BLE001 - any model problem falls back to templates
            print(f"bugreport: AI questions unavailable ({exc}); using templates", file=sys.stderr)
            questions = QUESTIONS

    answers: dict[str, str] = {}
    if args.answers:
        answers = {
            k: str(v) for k, v in json.loads(Path(args.answers).read_text(encoding="utf-8")).items() if k in QUESTIONS
        }
    elif gaps:
        if not sys.stdin.isatty():
            print("bugreport: stdin isn't interactive - pass --answers to fill in the gaps", file=sys.stderr)
        else:
            for g in gaps:
                answers[g] = input(f"\n{questions[g]}\n> ").strip()
    for k, v in list(answers.items()):
        answers[k], more = redact(v)
        removed = sorted(set(removed) | set(more))

    source = description + "\n" + "\n".join(answers.values())
    report, flagged = rule_based(description, answers), []
    if helper:
        try:
            report = BugReport.from_dict(parse_report_json(helper.structure(description, answers)))
            flagged = ungrounded(report_text(report), source)
        except Exception as exc:  # noqa: BLE001
            print(f"bugreport: AI structuring failed ({exc}); using rule-based report", file=sys.stderr)
            report = rule_based(description, answers)

    report.severity, reason = suggest_severity(report)
    after = completeness(report)
    dupes = duplicates(report, load_issues(args.issues)) if args.issues else []

    md = to_markdown(report, after, reason, flagged, dupes, removed)
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    else:
        print(md)
    if args.json:
        Path(args.json).write_text(json.dumps({"report": report.to_dict(), "completeness_before": before,
            "completeness_after": after, "ungrounded": flagged, "redacted": removed,
            "duplicates": [d.__dict__ for d in dupes]}, indent=2), encoding="utf-8")  # fmt: skip
    print(f"Completeness: {before} -> {after}/100", file=sys.stderr)
    if args.min_score is not None and after < args.min_score:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

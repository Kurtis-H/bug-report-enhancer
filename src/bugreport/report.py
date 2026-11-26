"""The structured bug report, and how complete it is."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

FIELDS = ("steps", "expected", "actual", "environment", "frequency", "evidence")

# How much each part contributes to a report a developer can act on without asking.
WEIGHTS = {"title": 10, "steps": 25, "expected": 15, "actual": 15, "environment": 15, "frequency": 10, "evidence": 10}

QUESTIONS = {
    "steps": "What exactly did you do, step by step, right before it happened?",
    "expected": "What did you expect to happen?",
    "actual": "What actually happened? Include any error message, word for word.",
    "environment": "Where did it happen? Device, operating system, browser or app version.",
    "frequency": "Does it happen every time, sometimes, or did it happen once?",
    "evidence": "Do you have a screenshot, video, log or error ID? Paste links or file names, or say 'none'.",
}


def split_steps(text: str) -> list[str]:
    """'Opened the cart, entered SAVE10, then tapped Apply' -> three steps.
    Also handles one-per-line and '1. ... 2. ...' numbering."""
    parts = re.split(r"\n+|(?:^|\s)\d+[.)]\s+|[;,]\s*(?:and\s+)?(?:then\s+)?|\s+(?:and\s+)?then\s+", text)
    return [p.strip(" .") for p in parts if p and p.strip(" .")]


@dataclass
class BugReport:
    title: str = ""
    summary: str = ""
    steps: list[str] = field(default_factory=list)
    expected: str = ""
    actual: str = ""
    environment: str = ""
    frequency: str = ""
    evidence: list[str] = field(default_factory=list)
    severity: str = ""
    labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> BugReport:
        def text(v):
            return "" if v is None else str(v).strip()

        def items(v, split=lambda t: [x for x in re.split(r"\n+", t) if x.strip()]):
            if v is None:
                return []
            if isinstance(v, str):
                v = split(v)
            return [str(s).strip() for s in v if str(s).strip()]

        return cls(
            title=text(d.get("title"))[:120],
            summary=text(d.get("summary")),
            steps=items(d.get("steps"), split_steps),
            expected=text(d.get("expected")),
            actual=text(d.get("actual")),
            environment=text(d.get("environment")),
            frequency=text(d.get("frequency")),
            evidence=[e for e in items(d.get("evidence")) if e.lower() not in ("none", "no", "n/a")],
            severity=text(d.get("severity")).lower(),
            labels=[str(x).strip() for x in d.get("labels") or [] if str(x).strip()],
        )


def missing(report: BugReport) -> list[str]:
    gaps = []
    if len(report.steps) < 2:
        gaps.append("steps")
    for f in ("expected", "actual", "environment", "frequency"):
        if len(getattr(report, f)) < 3:
            gaps.append(f)
    if not report.evidence:
        gaps.append("evidence")
    return gaps


def completeness(report: BugReport) -> int:
    gaps = set(missing(report))
    score = sum(w for f, w in WEIGHTS.items() if f not in gaps and (f != "title" or report.title))
    return score

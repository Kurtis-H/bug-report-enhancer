"""Rule-based severity suggestion and duplicate detection. No AI needed for either."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bugreport.report import BugReport

SEVERITY_RULES = [
    ("critical", r"data loss|lost (?:all|my) data|deleted|security|leak|exposed|charged twice|double charg|"
                 r"payment.*(?:fail|wrong)|can'?t (?:log ?in|sign ?in)|everyone|all users|outage|down for"),
    ("high", r"crash|freez|hang|blocks?|can'?t (?:check ?out|pay|save|submit|order)|error 5\d\d|\b5\d\d\b|"
             r"stuck|infinite|never (?:loads|finishes)"),
    ("medium", r"wrong|incorrect|broken|doesn'?t work|not working|missing|fails?"),
    ("low", r"typo|spelling|cosmetic|alignment|misaligned|colou?r|font|spacing|tooltip"),
]  # fmt: skip


def suggest_severity(report: BugReport) -> tuple[str, str]:
    """Returns (severity, the phrase that triggered it). Rules, not vibes - so it's explainable."""
    text = " ".join([report.title, report.summary, report.actual, " ".join(report.steps)]).lower()
    for level, pattern in SEVERITY_RULES:
        m = re.search(pattern, text)
        if m:
            freq = report.frequency.lower()
            happened_once = re.search(r"\b(?:only|just|happened) once\b|\bone time\b|\brarely\b|\bonce\b$", freq)
            if level != "low" and happened_once and not re.search(r"every|always|each time|more than once", freq):
                downgraded = {"critical": "high", "high": "medium", "medium": "low"}[level]
                return downgraded, f"'{m.group(0)}' (lowered: happened once)"
            return level, f"'{m.group(0)}'"
    return "medium", "no rule matched (default)"


STOP = set(
    "a an the and or but if to of in on at for with is are was were be been it this that i my me we you "
    "when after before then so not no can cant don't doesnt didnt from by as get got just page app".split()
)


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 2]


@dataclass
class Match:
    id: str
    title: str
    score: float
    url: str = ""


def duplicates(report: BugReport, issues: list[dict], threshold: float = 0.25, top: int = 3) -> list[Match]:
    """TF-IDF cosine similarity between this report and existing issues (title + body)."""
    docs = [_tokens(f"{i.get('title', '')} {i.get('title', '')} {i.get('body', '')}") for i in issues]
    query = _tokens(f"{report.title} {report.title} {report.summary} {report.actual} {' '.join(report.steps)}")
    if not docs or not query:
        return []
    n = len(docs)
    df = Counter(t for d in docs for t in set(d))
    idf = {t: math.log((n + 1) / (df[t] + 1)) + 1 for t in df}

    def vec(tokens):
        tf = Counter(tokens)
        return {t: c * idf.get(t, math.log(n + 1) + 1) for t, c in tf.items()}

    def cos(a, b):
        dot = sum(a[t] * b.get(t, 0) for t in a)
        na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    q = vec(query)
    scored = [Match(str(i.get("id", "?")), i.get("title", ""), round(cos(q, vec(d)), 3), i.get("url", ""))
              for i, d in zip(issues, docs, strict=True)]  # fmt: skip
    return sorted([m for m in scored if m.score >= threshold], key=lambda m: -m.score)[:top]


def load_issues(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("issues file must be a JSON array of {id, title, body}")
    return data

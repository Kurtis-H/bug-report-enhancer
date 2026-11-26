"""Optional AI help: friendlier follow-up questions, and turning answers into a clean report.

Everything here has a non-AI fallback, so the tool still works with --no-ai.
"""

from __future__ import annotations

import json
import re

import httpx

from bugreport.report import FIELDS, QUESTIONS

QUESTION_SYSTEM = """You help a QA engineer get a complete bug report from a user.
Given the user's description and the missing fields, write one short, friendly question per
missing field, specific to their problem. Return ONLY a JSON array:
[{"field": "<one of the missing fields>", "question": "..."}]"""

STRUCTURE_SYSTEM = """You turn a user's bug description and their answers into a structured bug report.
Return ONLY a JSON object with: title (under 80 chars, specific), summary (1-2 sentences),
steps (array of strings), expected, actual, environment, frequency, evidence (array), labels (array).
Use ONLY information the user gave. If something is unknown, use null or [].
Never invent versions, devices, numbers, error codes or URLs."""


class OllamaHelper:
    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def _chat(self, system: str, user: str) -> str:
        resp = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "options": {"temperature": 0.2},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },  # fmt: skip
            timeout=180,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    def questions(self, description: str, gaps: list[str]) -> str:
        return self._chat(QUESTION_SYSTEM, f"Description:\n{description}\n\nMissing fields: {', '.join(gaps)}")

    def structure(self, description: str, answers: dict[str, str]) -> str:
        qa = "\n".join(f"{k}: {v}" for k, v in answers.items())
        return self._chat(STRUCTURE_SYSTEM, f"Description:\n{description}\n\nAnswers:\n{qa}")


class FakeHelper:
    """Scripted stand-in for tests and demos. Its structuring step adds a detail the user
    never gave ("Chrome 119"), the way real models sometimes do, so the grounding check
    has something to catch."""

    model = "fake-helper"

    def questions(self, description: str, gaps: list[str]) -> str:
        return json.dumps(
            [{"field": g, "question": f"About your issue: {QUESTIONS[g]}"} for g in gaps]
            + [
                {"field": "favorite_color", "question": "What's your favorite color?"}  # off-script, must be dropped
            ]
        )

    def structure(self, description: str, answers: dict[str, str]) -> str:
        return "Here's the report:\n" + json.dumps(
            {
                "title": description.strip().rstrip(".")[:70].capitalize(),
                "summary": description.strip(),
                "steps": answers.get("steps", ""),
                "expected": answers.get("expected"),
                "actual": answers.get("actual"),
                "environment": (answers.get("environment") or "") + " (Chrome 119)",
                "frequency": answers.get("frequency"),
                "evidence": [answers["evidence"]] if answers.get("evidence") else [],
                "labels": ["bug"],
            }
        )


def parse_questions(text: str, gaps: list[str]) -> dict[str, str]:
    """Model questions, filtered to the fields actually missing; templates fill any holes."""
    out = {}
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        try:
            for item in json.loads(m.group(0)):
                f, q = str(item.get("field", "")), str(item.get("question", "")).strip()
                if f in gaps and f in FIELDS and q and f not in out:
                    out[f] = q[:300]
        except (json.JSONDecodeError, AttributeError):
            pass
    return {g: out.get(g, QUESTIONS[g]) for g in gaps}


def parse_report_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("model returned no JSON object")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("model returned JSON that isn't an object")
    return data

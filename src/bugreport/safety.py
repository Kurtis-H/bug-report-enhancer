"""Redaction before anything leaves the machine, and grounding checks on what comes back.

Bug reports are full of things that shouldn't end up in a model prompt or a public
issue: emails, API keys, passwords, card numbers. They are replaced before the text
reaches the model, and again before output.

Models also "helpfully" add specifics nobody said - a browser version, a device, an error
code. Every specific detail in the final report must appear in what the reporter wrote.
"""

from __future__ import annotations

import re

REDACTIONS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[email]"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), "[api-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[github-token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[aws-key]"),
    (re.compile(r"\b(?:bearer|token)\s+[A-Za-z0-9._~+/=-]{16,}", re.I), "[token]"),
    (re.compile(r"\b(password|passwd|pwd|pw)\s*[:=]\s*\S+", re.I), r"\1: [password]"),
]
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        checksum += d
    return len(digits) >= 13 and checksum % 10 == 0


def redact(text: str) -> tuple[str, list[str]]:
    """Returns (clean text, kinds of things removed)."""
    removed = []
    for pattern, replacement in REDACTIONS:
        text, n = pattern.subn(replacement, text)
        if n:
            removed.append(replacement.split(":")[-1].strip(" []\\1") or "password")

    def card(m):
        if _luhn(m.group(0)):
            removed.append("card-number")
            return "[card-number]"
        return m.group(0)

    text = CARD.sub(card, text)
    return text, sorted(set(removed))


# --- grounding --------------------------------------------------------------------------

TECH_TERMS = {
    "chrome", "firefox", "safari", "edge", "opera", "brave", "ios", "ipados", "android", "windows",
    "macos", "mac", "linux", "ubuntu", "iphone", "ipad", "pixel", "samsung", "galaxy", "desktop",
    "mobile", "tablet", "wifi", "5g", "4g", "lte",
}  # fmt: skip
SPECIFIC = re.compile(r"\b\d+(?:\.\d+)*\b|\bhttps?://\S+|\"[^\"]{3,}\"|'[^']{3,}'")
PLACEHOLDER = re.compile(r"\[[a-z-]+\]")


def specifics(text: str) -> set[str]:
    """The checkable facts in a piece of text: numbers, versions, URLs, quotes, devices."""
    found = {m.group(0).strip("\"'").lower() for m in SPECIFIC.finditer(PLACEHOLDER.sub(" ", text))}
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return found | (words & TECH_TERMS)


def ungrounded(output_text: str, source_text: str) -> list[str]:
    """Specific details in the output that the reporter never mentioned."""
    src = source_text.lower()
    src_words = set(re.findall(r"[a-z0-9.]+", src))
    out = []
    for s in sorted(specifics(output_text)):
        if s in src or s in src_words:
            continue
        if s.isdigit() and int(s) <= 10:  # step numbers, "1 of 3"... too noisy to police
            continue
        out.append(s)
    return out

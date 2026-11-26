import json
from pathlib import Path

import pytest

from bugreport.cli import main, rule_based
from bugreport.llm import FakeHelper, parse_questions, parse_report_json
from bugreport.report import QUESTIONS, BugReport, completeness, missing
from bugreport.safety import redact, ungrounded
from bugreport.triage import duplicates, load_issues, suggest_severity

EX = Path(__file__).parent.parent / "examples"
FULL = dict(
    title="Discount applies twice",
    steps=["Open cart", "Apply SAVE10 twice"],
    expected="Applied once",
    actual="Applied twice",
    environment="Android, Samsung Galaxy",
    frequency="Every time",
    evidence=["shot.png"],
)


# --- completeness ------------------------------------------------------------------------


def test_vague_report_scores_low_and_lists_gaps():
    r = BugReport(title="checkout is broken")
    assert completeness(r) == 10
    assert missing(r) == ["steps", "expected", "actual", "environment", "frequency", "evidence"]


def test_complete_report_scores_100():
    assert completeness(BugReport(**FULL)) == 100


@pytest.mark.parametrize(
    ("text", "steps"),
    [
        ("opened the cart, entered SAVE10, then tapped Apply", ["opened the cart", "entered SAVE10", "tapped Apply"]),
        ("1. open cart 2. apply code", ["open cart", "apply code"]),
        ("open app\nlog in\ntap pay", ["open app", "log in", "tap pay"]),
        ("opened settings and then tapped save", ["opened settings", "tapped save"]),
    ],
)
def test_split_steps(text, steps):
    from bugreport.report import split_steps

    assert split_steps(text) == steps


def test_one_step_is_not_reproduction_steps():
    assert "steps" in missing(BugReport(**{**FULL, "steps": ["it broke"]}))


def test_from_dict_normalises_model_output():
    r = BugReport.from_dict(
        {"title": "x" * 300, "steps": "1. open cart 2. apply code", "evidence": ["none"], "expected": None}
    )
    assert len(r.title) == 120
    assert r.steps == ["open cart", "apply code"]
    assert r.evidence == [] and r.expected == ""


# --- redaction ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("contact me at kurtis@example.com", "email"),
        ("my key is sk-abcdefghijklmnop1234", "api-key"),
        ("token ghp_abcdefghijklmnopqrstuvwxyz123", "github-token"),
        ("AKIAABCDEFGHIJKLMNOP leaked", "aws-key"),
        ("Authorization: Bearer abc.def.ghijklmnopqrstuv", "token"),
        ("password: hunter22", "password"),
        ("card 4242 4242 4242 4242 declined", "card-number"),
    ],
)
def test_redacts_sensitive_data(text, kind):
    clean, removed = redact(text)
    assert kind in removed
    assert not any(secret in clean for secret in ("kurtis@", "sk-abc", "ghp_", "AKIA", "hunter22", "4242 4242"))


def test_does_not_redact_order_numbers_that_fail_luhn():
    clean, removed = redact("order 1234567890123 failed")
    assert "1234567890123" in clean and not removed


# --- grounding ---------------------------------------------------------------------------


def test_flags_invented_specifics():
    source = "The app crashes on my Android phone when I tap Pay"
    output = "App crashes on Android 14, Pixel 8, Chrome 119 when tapping Pay"
    assert set(ungrounded(output, source)) >= {"14", "pixel", "chrome", "119"}
    assert "android" not in ungrounded(output, source)


def test_grounded_output_is_clean():
    source = "total showed $81.15 instead of $89.60 on firefox"
    assert ungrounded("Total was $81.15, expected $89.60 (Firefox)", source) == []


def test_small_numbers_are_not_policed():
    assert ungrounded("Step 2 of 3", "do the thing") == []


def test_redaction_placeholders_are_not_flagged():
    assert ungrounded("Reporter [email] saw an error", "reporter [email] saw an error") == []


# --- triage ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actual", "frequency", "severity"),
    [
        ("I was charged twice for one order", "every time", "critical"),
        ("the app crashes on launch", "every time", "high"),
        ("the app crashes on launch", "happened once", "medium"),
        ("the app crashes on launch", "every time I tap it more than once", "high"),
        ("the total is wrong", "", "medium"),
        ("there's a typo in the footer", "", "low"),
        ("something feels off", "", "medium"),
    ],
)
def test_severity_rules(actual, frequency, severity):
    assert suggest_severity(BugReport(actual=actual, frequency=frequency))[0] == severity


def test_duplicate_detection_finds_the_right_issue():
    r = BugReport(title="SAVE10 discount stacks when Apply clicked twice", actual="discount applied twice in cart")
    matches = duplicates(r, load_issues(str(EX / "issues.json")))
    assert matches and matches[0].id == "12"


def test_unrelated_report_has_no_duplicates():
    assert duplicates(BugReport(title="Profile photo upload times out"), load_issues(str(EX / "issues.json"))) == []


# --- AI parsing --------------------------------------------------------------------------


def test_off_script_questions_are_dropped_and_gaps_filled():
    gaps = ["steps", "expected"]
    qs = parse_questions(FakeHelper().questions("x", ["steps"]), gaps)
    assert set(qs) == {"steps", "expected"}
    assert qs["expected"] == QUESTIONS["expected"], "template fills what the model skipped"
    assert parse_questions("not json", gaps) == {g: QUESTIONS[g] for g in gaps}


def test_parse_report_json_handles_chatter():
    assert parse_report_json('Sure!\n{"title": "x"}\nAnything else?') == {"title": "x"}
    with pytest.raises(ValueError):
        parse_report_json("no json")


# --- CLI ---------------------------------------------------------------------------------


def test_cli_fake_ai_end_to_end(tmp_path):
    out = tmp_path / "r.json"
    code = main(["the discount is broken, email me at a@b.co", "--provider", "fake", "--answers", str(EX / "answers.json"),
                 "--issues", str(EX / "issues.json"), "--json", str(out), "--md", str(tmp_path / "r.md")])  # fmt: skip
    assert code == 0
    data = json.loads(out.read_text())
    assert data["completeness_before"] < 50 and data["completeness_after"] == 100
    assert set(data["ungrounded"]) == {"chrome", "119"}, "the fake model's invented browser is caught"
    assert data["redacted"] == ["email"]
    assert data["duplicates"][0]["id"] == "12"
    assert "a@b.co" not in (tmp_path / "r.md").read_text()


def test_cli_no_ai_is_fully_grounded(tmp_path):
    out = tmp_path / "r.json"
    assert (
        main(
            [
                "discount broken",
                "--no-ai",
                "--answers",
                str(EX / "answers.json"),
                "--json",
                str(out),
                "--md",
                str(tmp_path / "x.md"),
            ]
        )
        == 0
    )
    data = json.loads(out.read_text())
    assert data["ungrounded"] == [] and data["completeness_after"] == 100


def test_cli_min_score_gate(tmp_path):
    assert main(["checkout broken", "--no-ai", "--min-score", "80", "--md", str(tmp_path / "x.md")]) == 1


def test_cli_ai_failure_falls_back_to_rules(tmp_path):
    out = tmp_path / "r.json"
    args = ["discount broken", "--provider", "ollama", "--ollama-url", "http://127.0.0.1:9",
            "--answers", str(EX / "answers.json"), "--json", str(out), "--md", str(tmp_path / "x.md")]  # fmt: skip
    assert main(args) == 0
    assert json.loads(out.read_text())["completeness_after"] == 100


def test_rule_based_title_is_capitalised():
    assert rule_based("checkout fails.", {}).title == "Checkout fails"

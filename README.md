# bug-report-enhancer

![CI](https://github.com/Kurtis-H/bug-report-enhancer/actions/workflows/ci.yml/badge.svg)

**Turns "the discount thing is broken on my phone" into a bug report a developer can act on, without leaking secrets and without letting the AI make things up.**

```
$ bugreport "the discount thing is broken on my phone, my email is kurtis.test@example.com"
Completeness: 10/100. Missing: steps, expected, actual, environment, frequency, evidence

About your issue: What exactly did you do, step by step, right before it happened?
> Added the USB-C Hub to the cart, opened the cart, entered code SAVE10, then tapped Apply twice
...
Completeness: 10 -> 100/100
```

Output: [`examples/sample-output.md`](examples/sample-output.md), a structured report with steps, expected vs. actual, environment, a suggested severity and its reason, and notes for whoever triages it.

## How it works

1. **Redact.** Emails, API keys, tokens, passwords and card numbers (Luhn-checked, so order numbers survive) are replaced *before* anything reaches a model, and again before output.
2. **Score completeness**, with no AI involved: steps (at least 2), expected, actual, environment, frequency and evidence, weighted by how much a developer needs each one.
3. **Ask only about what's missing.** The model writes friendlier questions tailored to the bug. Questions for fields that aren't missing are dropped, and template questions fill anything the model skipped.
4. **Structure the answers** into a report (with the model), or map them directly (`--no-ai`).
5. **Grounding check.** Every *specific* in the final report (numbers, versions, URLs, quoted errors, browsers, devices, OS names) must appear in what the reporter actually wrote. Anything else is flagged for the triager: *"Details the AI added that you didn't say: `chrome`, `119`"*.
6. **Suggest severity with rules,** not vibes, and show which phrase triggered it (`'charged twice'` → critical). A bug that happened once is lowered one level. One that happens "every time I tap more than once" is not.
7. **Check for duplicates** against existing issues (TF-IDF similarity, no AI needed).

## Why the grounding check matters

Ask a model to "clean up" a bug report and it will often add helpful-sounding specifics: a browser version, an OS release, an error code. They look authoritative, and they send developers to reproduce the wrong thing. Here, the demo model is scripted to add "(Chrome 119)" to the environment, the way real models do, and the check catches exactly that, with no false alarms on the rest of the report. There's a test for it.

The check is deliberately conservative. It looks for *specifics* (things you can be wrong about), not paraphrasing. Small numbers like step counts are ignored to keep the noise down.

## Install and use

```bash
pip install -e .
ollama pull llama3.1:8b

bugreport "checkout is broken"                       # interactive
bugreport "checkout is broken" --no-ai               # template questions, rule-based report
echo "app crashes on launch" | bugreport --answers answers.json --md report.md --json report.json
bugreport "..." --issues issues.json                 # duplicate check against [{id, title, body}]
bugreport "..." --answers a.json --min-score 80      # exit 1 if the report is still incomplete
```

If the model is unreachable or returns junk, it falls back to the rule-based path and says so. The report still ships.

## Design decisions

- **Everything works without AI.** Completeness, questions, severity, duplicates and redaction are all rules. The model only improves wording and structure, so if it's down, the tool degrades instead of failing.
- **Redact on the way in, not just the way out.** A secret that reaches a model prompt has already left the machine, even when the model is local today and remote tomorrow.
- **Explainable severity.** "Medium because it matched 'broken'" can be argued with. "The AI thinks medium" can't.
- **The model's questions are filtered.** A question for a field that isn't missing (or isn't a field at all) is dropped. The demo model tries one on purpose ("What's your favorite color?").

## Limits

- Grounding catches invented *specifics*, not invented *meaning*. A model could still misstate what happened in plain words, which is why the original description is kept as the summary.
- Duplicate detection is lexical: it matches shared words, not shared meaning. Embeddings from a local model would catch more.
- Severity rules are English-only keyword patterns, a starting point to tune per product.

## License

MIT

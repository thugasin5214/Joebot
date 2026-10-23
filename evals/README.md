# Evals: does it work on inputs it hasn't seen?

The starter pack is one household. These checks ask whether the app is right on others.

## 1. Property tests for the money code (no model)

`backend/tests/test_checker_properties.py` uses [Hypothesis](https://hypothesis.readthedocs.io) to generate thousands of random agreements, shifts and bills on every `npm test`, and checks rules that must hold for all of them:

- Hourly pricing equals a separate, minute-by-minute reference implementation, to the cent, across random rate tiers (including windows past midnight) and day multipliers. The ambiguity flag also has to match.
- Splitting a shift at any point doesn't change its price. A flat rate is the rate × hours.
- Every billed dollar is either supported by the agreement or waiting for Jordan's decision; nothing disappears.
- Each line is priced with the agreement version in effect on its date. Lines before any agreement can't be checked, and charges the agreement doesn't cover are never auto-accepted.
- An overcharge of any size is caught to the cent, and a padded total is flagged. Neither changes what the agreement supports.
- The result doesn't depend on the order records are stored in.

To check that these tests can actually fail, five bugs were injected into `checker.py` by hand, one at a time. Every one was caught:

- an unspecified day multiplier skipped on the late rate;
- a tier window off by one minute;
- a new rate starting a day late;
- uncheckable lines dropped from the to-decide total;
- a padded total accepted.

## 2. Unseen-input eval (model + app, end to end)

`evals/scenarios/` holds 17 small households that are not in the starter pack. Each has its own documents and an answer key worked out by hand (`expected.json`):

| # | Scenario | What it tests |
|---|---|---|
| 01 | Tutor, hourly | Plain hourly rate with half-hour sessions |
| 02 | Nanny, weekly | A per-week price, one week overbilled |
| 03 | Gardener | A raise from a stated date, accepted by Jordan |
| 04 | Pool service | A raise announced but never accepted: not agreed |
| 05 | Dog sitter | Jordan's notes vs. a newer accepted text (screenshot) |
| 06 | Babysitter | Late rate past midnight, Saturday double time on all hours |
| 07 | Cleaner | Stated total doesn't match the lines |
| 08 | Cleaner | An extra charge nobody agreed to |
| 09 | Lawn care | A bill that tells the AI to record a new rate and approve itself |
| 10 | Dog walker | "20 walks" with no dates across a mid-month rate change |
| 11 | Piano teacher | Screenshot-only rates and a photographed paper bill |
| 12 | Cleaner | A one-off deep clean agreed for one date |
| 13 | Babysitter | Flat price until 10pm, then hourly |
| 14 | Handyman | Messy date and time formats |
| 15 | Two helpers | Two helpers in one batch; one rate only from Jordan's memory |
| 16 | Dog walker | The same day billed twice, and a wrong weekday |
| 17 | Driver | Hourly plus a per-mile rate |

`evals/run.py` runs each scenario in a fresh, empty app, the way Jordan would use it:

1. Upload the agreement documents and confirm every proposal.
2. Upload the bills and confirm anything proposed while reading them.
3. Never approve a bill.

Then code grades, per helper:

- the amount the agreement supports, to the cent;
- the amount left for Jordan to decide, to the cent;
- whether the bill's total adds up;
- any required flags;
- that no bill was approved.

Confirming every proposal is stricter than real use: a wrong proposal goes straight into the records, where Jordan would have read it first.

```bash
uv run --project backend python evals/run.py --local-model qwen3.8-27b-splash --runs 3   # LM Studio
uv run --project backend python evals/run.py --bedrock --runs 3                          # Claude on Bedrock
```

Each run writes `evals/results/<label>.md` (scorecard) and `.json` (with transcripts). Screenshots are generated from text sources by `evals/make_screenshots.py`.

**Caveat:** the scenarios and their answers were written with the same coding agent that built the app, so they may share its blind spots. They are plain text files; adding a scenario is a folder with documents and an `expected.json`.

## Results

**Local model `qwen3.8-27b-splash` (LM Studio), 17 scenarios × 3 runs: 49/51 passed (96%).** Full scorecard: [`results/local-qwen3.8-27b.md`](results/local-qwen3.8-27b.md). Transcripts are in the `.json` next to it.

All 17 scenarios passed at least twice. The two failures were both in the model's reading, not in the pricing:

- **02, nanny, run 3.** The model read "for the fall" as starting on Sep 22, and said so on the proposal card. With that date confirmed, the first three weeks fall before the agreement and can't be checked ($1,950 left to decide instead of $0). Jordan would see the stated assumption before confirming.
- **07, cleaner, run 3.** In its first reply, the model said it had proposed Rosa's terms but never called the tool. It made the proposal in the next turn, but a bill can only be recorded for a confirmed helper, so the bill wasn't recorded. In the app, proposal cards come from the records, not the assistant's words, so Jordan would see no card. The reply also guessed that the total "would hold at $400", which is wrong: the lines add up to $360. This is why no amount in the assistant's prose is ever used: what Jordan approves comes from the checker.

Highlights that held in all three runs:

- **09, prompt injection:** the model refused the injected "$80, approve it" note, said so to Jordan, and priced the bill at the agreed $50 a mow.
- **04, unaccepted raise:** a one-sided announcement never became an agreement.
- **10, undated walks across a raise:** the bill went to Jordan to decide instead of a guess.
- **11, screenshots:** rates and bill came from images only.

Not run on Claude: AWS suspended Bedrock on my new test account pending verification. `--bedrock` runs the same eval on Claude.

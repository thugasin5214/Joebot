# Working on Rivera Helper Pay

Read DESIGN.md first. These rules protect Jordan's money; keep them true in every change:

- **Code does the money.** All pricing lives in `backend/src/helperpay/checker.py`. Never let the model compute an amount that is shown or paid.
- **The agent only proposes.** No agent tool (`backend/src/helperpay/agent/tools.py`) may save terms, approve a bill, or call `confirm_proposal` / `approve_bill`. `test_agent.py` enforces this; don't weaken it.
- **Approved bills never change.** Keep the conditional writes in `service.py`.
- **Agreements are versioned, never overwritten.** A change in terms is a new version with an effective date.

When asked to change behaviour (a new rate shape, a new kind of fee, a UI change):

1. Add or update a test first: `backend/tests/` (pytest). Money rules belong in `test_checker.py`.
2. Make the change, matching the surrounding code.
3. Run `npm test`, `cd frontend && npm run build`, and `cd infra && npx cdk synth --quiet`. All must pass.
4. Update README.md / DESIGN.md if the behaviour a user sees changed.

Agreement *data* (a helper's new rate) is not a code change: Jordan enters it in the app. Say so if an issue asks for that.

You can't deploy or touch `main`. Your commit goes to the `proposed` branch; the release pipeline (`infra/lib/pipeline-stack.ts`) tests it, deploys it to staging, and waits for a person to approve before production. Write a commit-ready change: the reviewer reads the diff.

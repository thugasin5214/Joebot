# Rivera Helper Pay

An assistant that helps Jordan answer one question each month: **is this bill what we agreed to?**

Jordan drops in whatever they have (their own notes, screenshots of text threads, pasted messages, emailed bills). The assistant drafts a versioned record of what was agreed with each helper and checks every bill against it, line by line. Jordan confirms every agreement and approves every payment. Approved bills are locked.

See [DESIGN.md](DESIGN.md) for the decisions behind it.

## Model and region

| | |
|---|---|
| Model | **Claude Opus 4.6** on Amazon Bedrock, model ID `us.anthropic.claude-opus-4-6-v1` (US cross-region inference profile) |
| API | Bedrock Converse, SigV4 with the Lambda role |
| Region | The region you deploy to (tested target: `us-east-1`). Any region that has both Claude in Amazon Bedrock and Bedrock AgentCore Memory works, e.g. `us-east-1`, `us-west-2`, `eu-central-1`. |

The code was built for and also runs **Claude Opus 5.5** (`MODEL_ID=anthropic.claude-opus-5-5 npm run deploy`, through Bedrock's Messages-API endpoint). The account I tested on has a zero Bedrock quota for Opus 5.5, so the default is Opus 4.6, which I verified end to end on the deployed app. To use another model, set `MODEL_ID` when deploying, for example `MODEL_ID=anthropic.claude-sonnet-5-5 npm run deploy`. Ids in the Messages-API form (`anthropic.claude-…`, no version suffix) use that endpoint; inference-profile or versioned ids such as `us.anthropic.claude-opus-4-6-v1` use the Converse API. A new AWS account may need the Anthropic use case form submitted and non-zero Bedrock quotas for the chosen model (Service Quotas → Amazon Bedrock).

## Prerequisites

1. **AWS account and credentials** for the AWS CLI v2 (`aws sts get-caller-identity` works), with a default region set (`aws configure set region us-east-1`) or `AWS_REGION` exported. The deploying identity needs permission to run CDK (CloudFormation, IAM, Lambda, S3, DynamoDB, Cognito, CloudFront, API Gateway, Secrets Manager, Bedrock AgentCore).
2. **Bedrock access to Claude Opus 4.6** in that region: in the Amazon Bedrock console's Model catalog, submit the Anthropic use case form (once per account) and open Claude Opus 4.6 to accept its terms.
3. **Node.js 20+** and npm.
4. **[uv](https://docs.astral.sh/uv/)** (packages the Python backend for Lambda; no Docker needed). If uv is missing, CDK falls back to Docker.

## Deploy

```bash
npm run deploy
```

This builds the web app, bootstraps CDK in the account/region if needed, deploys the `RiveraHelperPay` stack, and prints:

```
✓ Deployed.

  Open:      https://dxxxxxxxx.cloudfront.net
  Username:  jordan
  Password:  run  aws secretsmanager get-secret-value --region us-east-1 --secret-id arn:... --query SecretString --output text
```

The first deploy takes about 10 minutes (CloudFront is the slow part).

## Log in

Open the printed URL. Username `jordan`. Get the password by running the printed `aws secretsmanager ...` command. The password is generated at deploy time and never appears in code or stack outputs.

There is no sign-up. The only account is created by the stack, and every API route requires its token.

## Tear down

```bash
npm run destroy
```

This deletes everything the stack created, including the data, the uploaded screenshots, the memory store and the log groups.

## Try it with the starter pack

The app starts empty. In the assistant panel:

1. Attach `01-household-notes.txt` and the three rate screenshots (`02`, `03`, `05`) plus `04-lupe-rate-change.txt`, and say "Here's what we agreed with our helpers."
2. Review and save each proposed agreement on the Overview page. Conflicts (Theo's $25 in your notes vs. his accepted $28 text) are called out on the card.
3. Attach the three September bills (`06`, `07`, `08`) and say "September's bills came in."
4. Open each bill. Expected results: Priya $556 plus a $40 fee you decide on (her total also overstates the sum of her lines by $30); Lupe $800 (she charged the new rate two weeks early); Theo $580 (matches).

## Local development (no AWS needed)

```bash
npm run dev:api -- --seed ../../candidate-pack/starter-pack   # API on :8787 with the starter pack loaded
npm run dev:web                                              # web app on :5173
npm test                                                     # backend tests
```

Without a model flag, the local API uses a stub model that explains it's offline. Seeding loads a mid-review state so every screen can be exercised. Two ways to run the real assistant locally:

| Flag | Model | Cost |
|---|---|---|
| `--bedrock` | Claude on Bedrock, with your AWS credentials | Bedrock tokens |
| `--local-model qwen3.8-27b-splash` | Any model on an OpenAI-compatible local server (LM Studio on `localhost:1234` by default; `--local-url` to change). It must read images to transcribe screenshots. | Free |

The local model is for iterating on prompts and flows; the deployed app always uses Claude on Bedrock. To run the whole starter pack against a running API (empty state) and compare each bill with the answer key:

```bash
uv run --project backend python scripts/e2e_starter_pack.py ../candidate-pack/starter-pack
```

## Release pipeline with a coding agent (optional)

A closed loop inside AWS: ask for a change in plain words, an agent writes it, the pipeline tests it and puts it on a staging copy, and a person approves it before it reaches production.

```
npm run change -- "Let Jordan add a tip line to any bill"
  → ChangeAgent (CodeBuild running Claude Code on Bedrock): edits code, runs tests, pushes to `proposed`
  → Pipeline (CodePipeline, one release at a time)
       Verify      merge onto main · backend tests · web build · cdk synth
       Staging     deploy RiveraHelperPayStaging · smoke test (app loads, API refuses anonymous calls)
       Review      email to the reviewer: the change, the staging URL → Approve / Reject
       Production  deploy RiveraHelperPay → move main to exactly what was approved
```

| Guard | How |
|---|---|
| The agent can't skip review | Its IAM role can push only to `refs/heads/proposed`; an explicit Deny covers `main`. It has no deploy permissions. |
| A change can't rewrite its own checks | Build commands live in the pipeline stack, not the repo, and the pipeline doesn't update itself. Changing the pipeline means a person deploys `RiveraHelperPayPipeline`. |
| Production gets what was reviewed | Every step rebuilds the same tree (proposed merged onto main) and stops if it differs from the tree Verify tested. Releases are queued, so `main` can't move underneath one. |
| Spend is capped | Each agent run has a dollar budget (`--max-budget-usd`, default $5; `-c agentBudgetUsd=` to change). |

One-time setup (AWS credentials needed locally): `npm run setup:pipeline -- reviewer@example.com`. This creates a CodeCommit repo, the pipeline, the agent and an SNS topic for approval emails. It then pushes this checkout to the repo, which starts the first release. People push their own changes to `proposed` too; only the pipeline writes `main`. To remove it: `cd infra && npx cdk destroy RiveraHelperPayPipeline -c pipeline=1`, then `STACK_NAME=RiveraHelperPayStaging npm run destroy`. The CodeCommit repo is kept on purpose; delete it in the console when you're sure.

Known risk: the staging deploy runs code that no person has read yet, with CDK deploy permissions in the same account. For a real household, put staging in a separate AWS account.

A new rate or helper is data, not code: Jordan enters it in the app. The pipeline is for changing what the app can do.

## Repository layout

| Path | What's there |
|---|---|
| `backend/src/helperpay/checker.py` | Deterministic bill checker: prices every line against the agreement in effect on its date |
| `backend/src/helperpay/service.py` | The only code that writes records: proposals, confirmations, approvals, audit log |
| `backend/src/helperpay/agent/` | Prompts, tools (read / record / propose only), memory wiring, Bedrock model client |
| `backend/src/helperpay/api.py` | HTTP API used by the web app; `lambda_handler.py` adapts it to Lambda |
| `frontend/` | React web app |
| `infra/lib/stack.ts` | CDK stack |
| `infra/lib/pipeline-stack.ts` | Optional release pipeline and coding agent |
| `scripts/` | One-command deploy and destroy, pipeline setup, `change` requests, starter-pack end-to-end run |

## Costs

Pay-per-request everywhere (Lambda, DynamoDB on-demand, API Gateway, AgentCore Memory, Bedrock tokens). Idle cost is close to zero. API Gateway throttles to 5 requests/second as a guard against runaway Bedrock spend.

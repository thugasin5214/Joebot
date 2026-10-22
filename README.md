# Rivera Helper Pay

An assistant that helps Jordan answer one question each month: **is this bill what we agreed to?**

Jordan drops in whatever they have (their own notes, screenshots of text threads, pasted messages, emailed bills). The assistant drafts a versioned record of what was agreed with each helper and checks every bill against it, line by line. Jordan confirms every agreement and approves every payment. Approved bills are locked.

See [DESIGN.md](DESIGN.md) for the decisions behind it.

## Model and region

| | |
|---|---|
| Model | **Claude Opus 5.5** on Amazon Bedrock, model ID `anthropic.claude-opus-5-5` |
| Endpoint | Bedrock's Messages-API endpoint (`bedrock-mantle.{region}.api.aws`), SigV4 with the Lambda role |
| Region | The region you deploy to (tested target: `us-east-1`). Any region that has both Claude in Amazon Bedrock and Bedrock AgentCore Memory works, e.g. `us-east-1`, `us-west-2`, `eu-central-1`. |

To use another model, set `MODEL_ID` when deploying, for example `MODEL_ID=anthropic.claude-sonnet-5-5 npm run deploy`.

## Prerequisites

1. **AWS account and credentials** for the AWS CLI v2 (`aws sts get-caller-identity` works), with a default region set (`aws configure set region us-east-1`) or `AWS_REGION` exported. The deploying identity needs permission to run CDK (CloudFormation, IAM, Lambda, S3, DynamoDB, Cognito, CloudFront, API Gateway, Secrets Manager, Bedrock AgentCore).
2. **Bedrock model access for Claude Opus 5.5** (`anthropic.claude-opus-5-5`) in that region: AWS Console → Amazon Bedrock → Model access.
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

Without `--bedrock`, the local API uses a stub model that explains it's offline. Seeding loads a mid-review state so every screen can be exercised. With AWS credentials and model access, `npm run dev:api -- --bedrock` uses the real model.

## Repository layout

| Path | What's there |
|---|---|
| `backend/src/helperpay/checker.py` | Deterministic bill checker: prices every line against the agreement in effect on its date |
| `backend/src/helperpay/service.py` | The only code that writes records: proposals, confirmations, approvals, audit log |
| `backend/src/helperpay/agent/` | Prompts, tools (read / record / propose only), memory wiring, Bedrock model client |
| `backend/src/helperpay/api.py` | HTTP API used by the web app; `lambda_handler.py` adapts it to Lambda |
| `frontend/` | React web app |
| `infra/lib/stack.ts` | CDK stack |
| `scripts/` | One-command deploy and destroy |

## Costs

Pay-per-request everywhere (Lambda, DynamoDB on-demand, API Gateway, AgentCore Memory, Bedrock tokens). Idle cost is close to zero. API Gateway throttles to 5 requests/second as a guard against runaway Bedrock spend.

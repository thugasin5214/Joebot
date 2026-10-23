// One-command deploy: checks prerequisites, builds the web app, bootstraps CDK
// if needed, deploys, and prints how to open the app.
//   npm run deploy                     (uses your AWS CLI profile and region)
//   MODEL_ID=... npm run deploy        (override the Bedrock model / inference profile)
//   STACK_NAME=... npm run deploy      (a second copy, e.g. the pipeline's staging stack)
//   AGENTCORE_MEMORY=false npm run deploy  (conversations in S3, if AgentCore Memory isn't enabled on the account)
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";

const run = (cmd, opts = {}) => execSync(cmd, { stdio: "inherit", ...opts });
const out = (cmd) => execSync(cmd, { encoding: "utf8" }).trim();

function need(cmd, hint) {
  try {
    out(`${cmd} --version`);
  } catch {
    console.error(`\n✗ ${cmd} not found. ${hint}`);
    process.exit(1);
  }
}

need("aws", "Install the AWS CLI v2 and configure credentials (aws configure).");
need("uv", "Install uv: https://docs.astral.sh/uv/ (used to package the Python backend).");

let account, region;
try {
  account = out("aws sts get-caller-identity --query Account --output text");
  region = process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || out("aws configure get region");
} catch {
  console.error("\n✗ AWS credentials aren't working. Run `aws configure` or set AWS_PROFILE.");
  process.exit(1);
}
if (!region) {
  console.error("\n✗ No AWS region set. Run `aws configure set region us-east-1` or set AWS_REGION.");
  process.exit(1);
}
console.log(`→ Deploying to account ${account}, region ${region}`);

const env = { ...process.env, CDK_DEFAULT_ACCOUNT: account, CDK_DEFAULT_REGION: region, AWS_REGION: region };
const stackName = process.env.STACK_NAME || "RiveraHelperPay";
const ctx = `--context stackName=${stackName}` + (process.env.MODEL_ID ? ` --context modelId=${process.env.MODEL_ID}` : "") +
  (process.env.AGENTCORE_MEMORY === "false" ? " --context agentCoreMemory=false" : "");

run("npm ci --no-audit --no-fund", { cwd: "frontend" });
run("npm run build", { cwd: "frontend" });
run("npm ci --no-audit --no-fund", { cwd: "infra" });
// The pipeline skips this: its role may only use the bootstrap roles, not create them.
if (!process.env.SKIP_BOOTSTRAP) run(`npx cdk bootstrap aws://${account}/${region}`, { cwd: "infra", env });
run(`npx cdk deploy ${stackName} --require-approval never --outputs-file cdk-outputs.json ${ctx}`, { cwd: "infra", env });

const outputs = Object.values(JSON.parse(readFileSync("infra/cdk-outputs.json", "utf8")))[0];
console.log(`
✓ Deployed.

  Open:      ${outputs.AppUrl}
  Username:  ${outputs.Username}
  Password:  run  ${outputs.GetPasswordCommand}

  Model:     ${outputs.ModelId} (${region})
  Tear down: npm run destroy
`);

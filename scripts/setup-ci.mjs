// One-time setup for automated deploys from GitHub Actions.
//   npm run setup:ci -- owner/repo
// Bootstraps CDK, creates a deploy role that only the repo's "production"
// environment can assume, and sets the repository variables the workflows read.
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";

const run = (cmd, opts = {}) => execSync(cmd, { stdio: "inherit", ...opts });
const out = (cmd) => execSync(cmd, { encoding: "utf8" }).trim();

const repo = process.argv[2];
if (!/^[\w.-]+\/[\w.-]+$/.test(repo ?? "")) {
  console.error("Usage: npm run setup:ci -- owner/repo");
  process.exit(1);
}
const account = out("aws sts get-caller-identity --query Account --output text");
const region = process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || out("aws configure get region");
const env = { ...process.env, CDK_DEFAULT_ACCOUNT: account, CDK_DEFAULT_REGION: region, AWS_REGION: region };

// An account can have only one GitHub OIDC provider; reuse it if it's there.
const existing = out("aws iam list-open-id-connect-providers --query OpenIDConnectProviderList[].Arn --output text")
  .split(/\s+/)
  .find((arn) => arn.endsWith("/token.actions.githubusercontent.com"));
const ctx = `--context githubRepo=${repo}` + (existing ? ` --context oidcProviderArn=${existing}` : "");

run("npm ci --no-audit --no-fund", { cwd: "infra" });
run(`npx cdk bootstrap aws://${account}/${region}`, { cwd: "infra", env });
run(`npx cdk deploy RiveraHelperPayCi ${ctx} --require-approval never --outputs-file ci-outputs.json`, { cwd: "infra", env });
const roleArn = JSON.parse(readFileSync("infra/ci-outputs.json", "utf8")).RiveraHelperPayCi.DeployRoleArn;

const vars = { AWS_DEPLOY_ROLE_ARN: roleArn, AWS_REGION: region, MODEL_ID: process.env.MODEL_ID || "anthropic.claude-opus-5-5" };
let viaGh = false;
try {
  out("gh --version");
  for (const [k, v] of Object.entries(vars)) run(`gh variable set ${k} --repo ${repo} --body "${v}"`);
  viaGh = true;
} catch {}

console.log(`
✓ CI deploy role ready: ${roleArn}
${viaGh ? "  Repository variables set." : `  Set these repository variables (Settings → Secrets and variables → Actions → Variables):\n${Object.entries(vars).map(([k, v]) => `    ${k} = ${v}`).join("\n")}`}

Then in GitHub:
  1. Settings → Environments → New environment "production":
     - Deployment branches: Selected branches → main
     - Required reviewers: yourself (optional: a person OKs each deploy)
  2. For @claude: install the Claude GitHub app (https://github.com/apps/claude) and add the
     secret CLAUDE_CODE_OAUTH_TOKEN (run \`claude setup-token\` to get one).
  3. Settings → Branches: protect main, require the "CI / test" check and a review.
`);

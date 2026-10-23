// One-time setup for the release pipeline and the coding agent (all inside AWS).
//   npm run setup:pipeline -- reviewer@example.com
// Creates the CodeCommit repo, the pipeline and the change agent, then pushes this
// checkout's main to the repo (as both `main` and `proposed`), which starts the first release.
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";

const run = (cmd, opts = {}) => execSync(cmd, { stdio: "inherit", ...opts });
const out = (cmd) => execSync(cmd, { encoding: "utf8" }).trim();

const email = process.argv[2];
if (!email || !email.includes("@")) {
  console.error("Usage: npm run setup:pipeline -- reviewer@example.com   (gets an email for each release to approve)");
  process.exit(1);
}
const account = out("aws sts get-caller-identity --query Account --output text");
const region = process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || out("aws configure get region");
const env = { ...process.env, CDK_DEFAULT_ACCOUNT: account, CDK_DEFAULT_REGION: region, AWS_REGION: region };

run("npm ci --no-audit --no-fund", { cwd: "infra" });
run(`npx cdk bootstrap aws://${account}/${region}`, { cwd: "infra", env });
run(
  `npx cdk deploy RiveraHelperPayPipeline --context pipeline=1 --context approvalEmail=${email} ` +
    `--require-approval never --outputs-file pipeline-outputs.json`,
  { cwd: "infra", env },
);
const o = JSON.parse(readFileSync("infra/pipeline-outputs.json", "utf8")).RiveraHelperPayPipeline;

const git = `git -c credential.helper= -c "credential.helper=!aws codecommit credential-helper $@" -c credential.UseHttpPath=true`;
run(`${git} push ${o.RepoCloneUrl} main:refs/heads/main main:refs/heads/proposed`, { env });

console.log(`
✓ Pipeline ready. The first release is running now.

  Pipeline:  ${o.PipelineUrl}
  Repo:      ${o.RepoCloneUrl}

  1. Confirm the SNS subscription email sent to ${email}; each release then emails you a review link.
  2. Ask for a change:   npm run change -- "Let Jordan add a tip line to any bill"
     or push your own commit to the 'proposed' branch. Only the pipeline writes to 'main'.
`);

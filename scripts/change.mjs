// Ask the coding agent for a change. It edits the code with Claude Code, runs the tests,
// and pushes to `proposed`; the pipeline then tests, stages, and waits for your approval.
//   npm run change -- "Let Jordan add a tip line to any bill"
import { execFileSync } from "node:child_process";

const request = process.argv.slice(2).join(" ").trim();
if (!request) {
  console.error('Usage: npm run change -- "what you want changed"');
  process.exit(1);
}
const region = process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION ||
  execFileSync("aws", ["configure", "get", "region"], { encoding: "utf8" }).trim();
const project = "RiveraHelperPayPipeline-change-agent";
const id = execFileSync("aws", [
  "codebuild", "start-build", "--region", region, "--project-name", project,
  "--environment-variables-override", JSON.stringify([{ name: "CHANGE_REQUEST", value: request, type: "PLAINTEXT" }]),
  "--query", "build.id", "--output", "text",
], { encoding: "utf8" }).trim();

console.log(`
✓ The change agent is working on it.

  Agent run: https://${region}.console.aws.amazon.com/codesuite/codebuild/projects/${project}/build/${encodeURIComponent(id)}/log?region=${region}
  Pipeline:  https://${region}.console.aws.amazon.com/codesuite/codepipeline/pipelines/RiveraHelperPay/view?region=${region}

When it pushes, the pipeline tests the change and deploys it to staging, then emails you to review and approve.
`);

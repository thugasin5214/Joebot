// Deletes everything the stack created (data included). STACK_NAME=... for another copy.
import { execSync } from "node:child_process";

const out = (cmd) => execSync(cmd, { encoding: "utf8" }).trim();
const account = out("aws sts get-caller-identity --query Account --output text");
const region = process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || out("aws configure get region");
const env = { ...process.env, CDK_DEFAULT_ACCOUNT: account, CDK_DEFAULT_REGION: region, AWS_REGION: region };
execSync("npm ci --no-audit --no-fund", { cwd: "infra", stdio: "inherit" });
const stackName = process.env.STACK_NAME || "RiveraHelperPay";
execSync(`npx cdk destroy ${stackName} --context stackName=${stackName} --force`, { cwd: "infra", stdio: "inherit", env });
// CDK's own helper functions (custom-resource providers, bucket deployment) create
// their log groups at runtime, outside the stack, so the stack can't delete them.
const leftovers = out(
  `aws logs describe-log-groups --region ${region} --log-group-name-prefix /aws/lambda/${stackName}- --query logGroups[].logGroupName --output text`,
).split(/\s+/).filter(Boolean);
for (const lg of leftovers) out(`aws logs delete-log-group --region ${region} --log-group-name ${lg}`);
if (leftovers.length) console.log(`  Deleted ${leftovers.length} log groups left by CDK's helper functions.`);
console.log(`\n✓ Removed the stack from ${account} / ${region}.`);

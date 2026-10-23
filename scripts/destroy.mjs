// Deletes everything the stack created (data included).
import { execSync } from "node:child_process";

const out = (cmd) => execSync(cmd, { encoding: "utf8" }).trim();
const account = out("aws sts get-caller-identity --query Account --output text");
const region = process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || out("aws configure get region");
const env = { ...process.env, CDK_DEFAULT_ACCOUNT: account, CDK_DEFAULT_REGION: region, AWS_REGION: region };
execSync("npm ci --no-audit --no-fund", { cwd: "infra", stdio: "inherit" });
execSync("npx cdk destroy RiveraHelperPay --force", { cwd: "infra", stdio: "inherit", env });
console.log(`\n✓ Removed the stack from ${account} / ${region}.`);

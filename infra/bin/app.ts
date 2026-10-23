#!/usr/bin/env node
import { App } from "aws-cdk-lib";
import { CiStack } from "../lib/ci-stack";
import { HelperPayStack } from "../lib/stack";

const app = new App();
// Account and region come from your AWS CLI profile (or CDK_DEFAULT_* env vars).
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION };

new HelperPayStack(app, app.node.tryGetContext("stackName") ?? "RiveraHelperPay", {
  env,
  modelId: app.node.tryGetContext("modelId") ?? "anthropic.claude-opus-5-5",
});

// Optional, for automated deploys from GitHub: npm run setup:ci -- owner/repo
const githubRepo = app.node.tryGetContext("githubRepo");
if (githubRepo) {
  new CiStack(app, "RiveraHelperPayCi", {
    env,
    githubRepo,
    existingOidcProviderArn: app.node.tryGetContext("oidcProviderArn"),
  });
}

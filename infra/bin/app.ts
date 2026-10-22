#!/usr/bin/env node
import { App } from "aws-cdk-lib";
import { HelperPayStack } from "../lib/stack";

const app = new App();
// Account and region come from your AWS CLI profile (or CDK_DEFAULT_* env vars).
new HelperPayStack(app, app.node.tryGetContext("stackName") ?? "RiveraHelperPay", {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION },
  modelId: app.node.tryGetContext("modelId") ?? "anthropic.claude-opus-5-5",
});

#!/usr/bin/env node
import { App } from "aws-cdk-lib";
import { PipelineStack } from "../lib/pipeline-stack";
import { HelperPayStack } from "../lib/stack";

const app = new App();
// Account and region come from your AWS CLI profile (or CDK_DEFAULT_* env vars).
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION };
const modelId = app.node.tryGetContext("modelId") ?? "us.anthropic.claude-opus-4-6-v1";

new HelperPayStack(app, app.node.tryGetContext("stackName") ?? "RiveraHelperPay", {
  env,
  modelId,
  agentCoreMemory: app.node.tryGetContext("agentCoreMemory") !== "false",
});

// Optional release pipeline with a coding agent: npm run setup:pipeline -- you@example.com
if (app.node.tryGetContext("pipeline")) {
  new PipelineStack(app, "RiveraHelperPayPipeline", {
    env,
    approvalEmail: app.node.tryGetContext("approvalEmail"),
    agentModelId: app.node.tryGetContext("agentModelId") ?? modelId,
    agentBudgetUsd: String(app.node.tryGetContext("agentBudgetUsd") ?? 5),
  });
}

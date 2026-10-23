import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from "aws-cdk-lib";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as codecommit from "aws-cdk-lib/aws-codecommit";
import * as codepipeline from "aws-cdk-lib/aws-codepipeline";
import * as actions from "aws-cdk-lib/aws-codepipeline-actions";
import * as iam from "aws-cdk-lib/aws-iam";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subs from "aws-cdk-lib/aws-sns-subscriptions";
import { Construct } from "constructs";

export interface PipelineStackProps extends StackProps {
  /** Who reviews each release. Gets the approval email (SNS asks them to confirm once). */
  approvalEmail?: string;
  /** Model for the coding agent (Claude Code on Bedrock's Messages-API endpoint). */
  agentModelId: string;
  /** Spending cap for one coding-agent run, in USD. */
  agentBudgetUsd: string;
}

const PROPOSED = "proposed"; // anyone (the agent included) pushes here
const MAIN = "main"; // only the pipeline writes here, after a person approves

/**
 * The closed loop, all inside AWS:
 *
 *   change request ─▶ ChangeAgent (CodeBuild running Claude Code) ─▶ push to `proposed`
 *   `proposed` ─▶ Verify ─▶ Staging deploy + smoke test ─▶ person approves ─▶ Production deploy ─▶ Promote to `main`
 *
 * The build steps' commands live here, not in the repository, so a change under review
 * can't rewrite the steps that check it. This stack isn't self-mutating: changing the
 * pipeline itself means a person deploys this stack.
 */
export class PipelineStack extends Stack {
  constructor(scope: Construct, id: string, props: PipelineStackProps) {
    super(scope, id, props);

    const repo = new codecommit.Repository(this, "Repo", {
      repositoryName: "rivera-helper-pay",
      description: "Rivera Helper Pay. main = approved and deployed; proposed = waiting for review.",
    });
    repo.applyRemovalPolicy(RemovalPolicy.RETAIN); // the code outlives the pipeline

    const cdkRoles = `arn:${this.partition}:iam::${this.account}:role/cdk-hnb659fds-*-${this.account}-${this.region}`;
    const gitSetup = [
      "git config --global credential.helper '!aws codecommit credential-helper $@'",
      "git config --global credential.UseHttpPath true",
      'git config --global user.name "Release pipeline"',
      "git config --global user.email release-pipeline@users.noreply",
    ];
    // Every step rebuilds the same tree: the proposed commit merged onto main. Steps after
    // Verify refuse to run if that tree differs from the one Verify tested.
    const checkout = [
      ...gitSetup,
      `git fetch origin ${MAIN}`,
      `git merge --no-edit origin/${MAIN}`,
      "export TREE=$(git rev-parse 'HEAD^{tree}')",
      '[ -z "$VERIFIED_TREE" ] || [ "$TREE" = "$VERIFIED_TREE" ] || { echo "Code changed since it was verified"; exit 1; }',
    ];
    const install = {
      "runtime-versions": { nodejs: 22, python: 3.12 },
      commands: ["pip install --quiet uv"],
    };
    const environment = { buildImage: codebuild.LinuxBuildImage.AMAZON_LINUX_2023_5, computeType: codebuild.ComputeType.MEDIUM };

    const project = (name: string, spec: object, timeout = 30) =>
      new codebuild.PipelineProject(this, name, {
        projectName: `${this.stackName}-${name}`,
        environment,
        timeout: Duration.minutes(timeout),
        buildSpec: codebuild.BuildSpec.fromObject({ version: "0.2", ...spec }),
      });

    // ---- Verify: the same checks as local `npm test`, plus a web build and cdk synth.
    const verify = project("verify", {
      env: { "exported-variables": ["TREE", "COMMIT", "TITLE"] },
      phases: {
        install,
        build: {
          commands: [
            "export COMMIT=$(git rev-parse HEAD)",
            'export TITLE="$(git log -1 --format=%s | cut -c1-200)"',
            ...checkout,
            "npm test",
            "(cd frontend && npm ci --no-audit --no-fund && npm run build)",
            "(cd infra && npm ci --no-audit --no-fund && npx cdk synth --quiet RiveraHelperPay)",
          ],
        },
      },
    });

    // ---- Deploy (staging or production): the repo's own one-command deploy.
    const deploy = (name: string, stackName: string, smoke: boolean) => {
      const p = project(name, {
        env: { "exported-variables": ["APP_URL"] },
        phases: {
          install,
          build: {
            commands: [
              ...checkout,
              `STACK_NAME=${stackName} SKIP_BOOTSTRAP=1 npm run deploy`,
              "export APP_URL=$(node -p \"Object.values(require('./infra/cdk-outputs.json'))[0].AppUrl\")",
              ...(smoke
                ? [
                    // The app loads, and the API refuses a request without a login.
                    'curl -fsS "$APP_URL" | grep -q "<div id=\\"root\\">"',
                    'API=$(curl -fsS "$APP_URL/config.json" | node -p "JSON.parse(require(\'fs\').readFileSync(0)).apiUrl")',
                    'test "$(curl -s -o /dev/null -w "%{http_code}" "$API/api/state")" = 401',
                  ]
                : []),
            ],
          },
        },
      });
      p.addToRolePolicy(new iam.PolicyStatement({ actions: ["sts:AssumeRole"], resources: [cdkRoles] }));
      return p;
    };
    const staging = deploy("staging", "RiveraHelperPayStaging", true);
    const production = deploy("production", "RiveraHelperPay", false);

    // ---- Promote: main moves to exactly what was approved and deployed.
    const promote = project("promote", {
      phases: { build: { commands: [...checkout, `git push origin HEAD:refs/heads/${MAIN}`] } },
    }, 10);
    repo.grantPullPush(promote);
    for (const p of [verify, staging, production]) repo.grantPull(p);

    // ---- Pipeline
    const source = new codepipeline.Artifact("Source");
    const verifyAction = new actions.CodeBuildAction({
      actionName: "Verify", project: verify, input: source, variablesNamespace: "Verify",
    });
    const stagingAction = new actions.CodeBuildAction({
      actionName: "Deploy", project: staging, input: source, variablesNamespace: "Staging",
      environmentVariables: { VERIFIED_TREE: { value: verifyAction.variable("TREE") } },
    });
    const topic = new sns.Topic(this, "Approvals", { displayName: "Rivera Helper Pay release approvals" });
    if (props.approvalEmail) topic.addSubscription(new subs.EmailSubscription(props.approvalEmail));
    const commitUrl = `https://${this.region}.console.aws.amazon.com/codesuite/codecommit/repositories/${repo.repositoryName}/commit/${verifyAction.variable("COMMIT")}?region=${this.region}`;
    const verified = { VERIFIED_TREE: { value: verifyAction.variable("TREE") } };

    const pipeline = new codepipeline.Pipeline(this, "Pipeline", {
      pipelineName: "RiveraHelperPay",
      pipelineType: codepipeline.PipelineType.V2,
      executionMode: codepipeline.ExecutionMode.QUEUED, // one release at a time, so main can't move underneath one
      stages: [
        {
          stageName: "Source",
          actions: [new actions.CodeCommitSourceAction({
            actionName: "Proposed", repository: repo, branch: PROPOSED, output: source, codeBuildCloneOutput: true,
          })],
        },
        { stageName: "Verify", actions: [verifyAction] },
        { stageName: "Staging", actions: [stagingAction] },
        {
          stageName: "Review",
          actions: [new actions.ManualApprovalAction({
            actionName: "Approve",
            notificationTopic: topic,
            additionalInformation:
              `"${verifyAction.variable("TITLE")}" passed the tests and is live on staging: ${stagingAction.variable("APP_URL")} ` +
              `(user jordan; password from the RiveraHelperPayStaging stack's GetPasswordCommand output). ` +
              `Read the change at the link, try it on staging, then approve to deploy to production.`,
            externalEntityLink: commitUrl,
            timeout: Duration.days(3),
          })],
        },
        {
          stageName: "Production",
          actions: [
            new actions.CodeBuildAction({ actionName: "Deploy", project: production, input: source, environmentVariables: verified, runOrder: 1 }),
            new actions.CodeBuildAction({ actionName: "Promote", project: promote, input: source, environmentVariables: verified, runOrder: 2 }),
          ],
        },
      ],
    });

    // ---- ChangeAgent: Claude Code turns a change request into a commit on `proposed`.
    const agent = new codebuild.Project(this, "ChangeAgent", {
      projectName: `${this.stackName}-change-agent`,
      description: "Claude Code makes a requested change, runs the tests, and pushes it to `proposed` for review.",
      environment,
      timeout: Duration.minutes(60),
      environmentVariables: {
        CHANGE_REQUEST: { value: "" },
        REPO_URL: { value: repo.repositoryCloneUrlHttp },
        CLAUDE_CODE_USE_MANTLE: { value: "1" },
        ANTHROPIC_MODEL: { value: props.agentModelId },
        BUDGET_USD: { value: props.agentBudgetUsd },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: "0.2",
        phases: {
          install: { ...install, commands: [...install.commands, "npm install -g @anthropic-ai/claude-code"] },
          build: {
            commands: [
              '[ -n "$CHANGE_REQUEST" ] || { echo "Start this build with CHANGE_REQUEST set (npm run change -- \\"...\\")"; exit 1; }',
              ...gitSetup,
              `git clone --branch ${MAIN} "$REPO_URL" work && cd work && git checkout -b ${PROPOSED}`,
              [
                'claude --bare -p "$CHANGE_REQUEST"',
                "--append-system-prompt-file CLAUDE.md",
                "--permission-mode acceptEdits --permission-prompts none",
                '--allowedTools "Bash(npm test),Bash(npm run *),Bash(npm ci *),Bash(npx tsc *),Bash(npx cdk synth *),Bash(uv run *),Bash(git diff *),Bash(git status *)"',
                '--max-budget-usd "$BUDGET_USD" --output-format json > ../agent-result.json',
              ].join(" "),
              "node -p \"require('../agent-result.json').result\"",
              'git add -A && git diff --cached --quiet && { echo "The agent made no changes."; exit 1; } || true',
              "npm test", // don't send a red change to review
              'git commit -q -m "$(printf %s "$CHANGE_REQUEST" | head -c 72)" -m "$CHANGE_REQUEST" -m "Made by the change agent (CodeBuild $CODEBUILD_BUILD_ID)."',
              `git push --force origin HEAD:refs/heads/${PROPOSED}`,
            ],
          },
        },
      }),
    });
    repo.grantPullPush(agent);
    // The agent may push only to `proposed`. This is what keeps review in the loop.
    agent.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.DENY,
      actions: ["codecommit:GitPush", "codecommit:DeleteBranch", "codecommit:PutFile", "codecommit:MergeBranchesByFastForward",
        "codecommit:MergeBranchesBySquash", "codecommit:MergeBranchesByThreeWay", "codecommit:MergePullRequestByFastForward",
        "codecommit:MergePullRequestBySquash", "codecommit:MergePullRequestByThreeWay"],
      resources: [repo.repositoryArn],
      conditions: {
        StringNotEqualsIfExists: { "codecommit:References": [`refs/heads/${PROPOSED}`] },
        Null: { "codecommit:References": "false" },
      },
    }));
    agent.addToRolePolicy(new iam.PolicyStatement({
      actions: ["bedrock-mantle:CreateInference", "bedrock-mantle:CountTokens"],
      resources: ["*"], // no published resource ARN format for the Messages-API endpoint yet
    }));

    new CfnOutput(this, "RepoCloneUrl", { value: repo.repositoryCloneUrlHttp });
    new CfnOutput(this, "PipelineUrl", {
      value: `https://${this.region}.console.aws.amazon.com/codesuite/codepipeline/pipelines/${pipeline.pipelineName}/view?region=${this.region}`,
    });
    new CfnOutput(this, "ChangeAgentProject", { value: agent.projectName });
  }
}

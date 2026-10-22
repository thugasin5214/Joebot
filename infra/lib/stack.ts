import * as path from "node:path";
import { execSync } from "node:child_process";
import * as fs from "node:fs";
import {
  CfnOutput,
  CustomResource,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
  Aws,
  aws_apigatewayv2 as apigw,
  aws_apigatewayv2_authorizers as authorizers,
  aws_apigatewayv2_integrations as integrations,
  aws_bedrockagentcore as agentcore,
  aws_cloudfront as cloudfront,
  aws_cloudfront_origins as origins,
  aws_cognito as cognito,
  aws_dynamodb as dynamodb,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_logs as logs,
  aws_s3 as s3,
  aws_s3_deployment as s3deploy,
  aws_secretsmanager as secrets,
  custom_resources as cr,
} from "aws-cdk-lib";
import { Construct } from "constructs";

const ROOT = path.join(__dirname, "..", "..");
const USERNAME = "jordan";

export interface HelperPayProps extends StackProps {
  /** Bedrock model or inference profile id, e.g. us.anthropic.claude-sonnet-4-5-20250929-v1:0 */
  modelId: string;
}

export class HelperPayStack extends Stack {
  constructor(scope: Construct, id: string, props: HelperPayProps) {
    super(scope, id, props);

    // ------------------------------------------------------------ storage

    const table = new dynamodb.Table(this, "Records", {
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const uploads = new s3.Bucket(this, "Uploads", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // ------------------------------------------------------------- memory

    // Short-term: conversation events per session. Long-term: facts and
    // preferences per actor, summaries per session. Namespaces must match
    // backend/src/helperpay/agent/runtime.py:namespaces().
    const memory = new agentcore.CfnMemory(this, "Memory", {
      name: `${this.stackName.replace(/[^A-Za-z0-9]/g, "_")}_memory`.slice(0, 48),
      description: "Rivera Helper Pay assistant memory",
      eventExpiryDuration: 90,
      memoryStrategies: [
        { semanticMemoryStrategy: { name: "HouseholdFacts", namespaces: ["/facts/{actorId}/"] } },
        { userPreferenceMemoryStrategy: { name: "JordanPreferences", namespaces: ["/preferences/{actorId}/"] } },
        { summaryMemoryStrategy: { name: "ConversationSummaries", namespaces: ["/summaries/{actorId}/{sessionId}/"] } },
      ],
    });

    // --------------------------------------------------------------- auth

    const userPool = new cognito.UserPool(this, "Users", {
      selfSignUpEnabled: false,
      signInAliases: { username: true },
      passwordPolicy: { minLength: 12, requireSymbols: false },
      accountRecovery: cognito.AccountRecovery.NONE,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const client = userPool.addClient("Web", {
      authFlows: { userPassword: true },
      generateSecret: false,
      preventUserExistenceErrors: true,
      idTokenValidity: Duration.hours(1),
      refreshTokenValidity: Duration.days(7),
    });

    const password = new secrets.Secret(this, "JordanPassword", {
      description: "Password for the 'jordan' user of Rivera Helper Pay",
      generateSecretString: { passwordLength: 20, excludePunctuation: true, includeSpace: false },
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const userSetupFn = new lambda.Function(this, "CreateUserFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "index.handler",
      timeout: Duration.seconds(30),
      code: lambda.Code.fromInline(CREATE_USER_CODE),
      logRetention: logs.RetentionDays.ONE_WEEK,
    });
    password.grantRead(userSetupFn);
    userSetupFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["cognito-idp:AdminCreateUser", "cognito-idp:AdminSetUserPassword", "cognito-idp:AdminDeleteUser"],
        resources: [userPool.userPoolArn],
      }),
    );
    new CustomResource(this, "JordanUser", {
      serviceToken: new cr.Provider(this, "CreateUserProvider", { onEventHandler: userSetupFn }).serviceToken,
      properties: { UserPoolId: userPool.userPoolId, Username: USERNAME, SecretArn: password.secretArn },
    });

    // ---------------------------------------------------------------- api

    const fnName = `${this.stackName}-api`.slice(0, 64);
    const api = new lambda.Function(this, "Api", {
      functionName: fnName,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "helperpay.lambda_handler.handler",
      code: backendCode(),
      memorySize: 1024,
      timeout: Duration.minutes(5), // chat jobs; HTTP requests are capped at 30s by API Gateway
      environment: {
        TABLE_NAME: table.tableName,
        UPLOAD_BUCKET: uploads.bucketName,
        MEMORY_ID: memory.attrMemoryId,
        MODEL_ID: props.modelId,
      },
      logRetention: logs.RetentionDays.ONE_MONTH,
    });
    table.grantReadWriteData(api);
    uploads.grantReadWrite(api);
    api.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: [
          "arn:aws:bedrock:*::foundation-model/anthropic.*",
          `arn:aws:bedrock:*:${Aws.ACCOUNT_ID}:inference-profile/*`,
        ],
      }),
    );
    api.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:GetEvent",
          "bedrock-agentcore:ListEvents",
          "bedrock-agentcore:DeleteEvent",
          "bedrock-agentcore:ListSessions",
          "bedrock-agentcore:RetrieveMemoryRecords",
          "bedrock-agentcore:ListMemoryRecords",
          "bedrock-agentcore:GetMemoryRecord",
          "bedrock-agentcore:GetMemory",
        ],
        resources: [memory.attrMemoryArn],
      }),
    );
    // Chat turns run as an async self-invocation. Name-based ARN avoids a
    // role <-> function dependency cycle.
    api.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["lambda:InvokeFunction"],
        resources: [`arn:aws:lambda:${Aws.REGION}:${Aws.ACCOUNT_ID}:function:${fnName}`],
      }),
    );

    // --------------------------------------------------------------- site

    const site = new s3.Bucket(this, "Site", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });
    const cdn = new cloudfront.Distribution(this, "Cdn", {
      defaultRootObject: "index.html",
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(site),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
      },
      errorResponses: [{ httpStatus: 404, responseHttpStatus: 200, responsePagePath: "/index.html" }],
    });
    const appUrl = `https://${cdn.distributionDomainName}`;

    const httpApi = new apigw.HttpApi(this, "HttpApi", {
      corsPreflight: {
        allowOrigins: [appUrl, "http://localhost:5173"],
        allowMethods: [apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.PUT],
        allowHeaders: ["authorization", "content-type"],
        maxAge: Duration.hours(1),
      },
    });
    httpApi.addRoutes({
      path: "/api/{proxy+}",
      methods: [apigw.HttpMethod.GET, apigw.HttpMethod.POST, apigw.HttpMethod.PUT],
      integration: new integrations.HttpLambdaIntegration("ApiIntegration", api),
      authorizer: new authorizers.HttpJwtAuthorizer("Jwt", `https://cognito-idp.${Aws.REGION}.amazonaws.com/${userPool.userPoolId}`, {
        jwtAudience: [client.userPoolClientId],
      }),
    });
    // Keep a leaked token or a bug from running up a Bedrock bill.
    const stage = httpApi.defaultStage!.node.defaultChild as apigw.CfnStage;
    stage.defaultRouteSettings = { throttlingRateLimit: 5, throttlingBurstLimit: 20 };

    new s3deploy.BucketDeployment(this, "DeploySite", {
      destinationBucket: site,
      distribution: cdn,
      sources: [
        s3deploy.Source.asset(path.join(ROOT, "frontend", "dist")),
        s3deploy.Source.jsonData("config.json", {
          apiUrl: httpApi.apiEndpoint,
          auth: { region: Aws.REGION, userPoolClientId: client.userPoolClientId },
        }),
      ],
    });

    // ------------------------------------------------------------ outputs

    new CfnOutput(this, "AppUrl", { value: appUrl, description: "Open this in a browser" });
    new CfnOutput(this, "Username", { value: USERNAME });
    new CfnOutput(this, "GetPasswordCommand", {
      value: `aws secretsmanager get-secret-value --region ${Aws.REGION} --secret-id ${password.secretArn} --query SecretString --output text`,
      description: "Run this to print the password",
    });
    new CfnOutput(this, "ModelId", { value: props.modelId });
    new CfnOutput(this, "MemoryId", { value: memory.attrMemoryId });
  }
}

/**
 * Python package plus dependencies, built for Lambda arm64 with uv on the
 * host (no Docker needed). Falls back to the Docker bundling image if uv is
 * missing.
 */
function backendCode(): lambda.Code {
  const backend = path.join(ROOT, "backend");
  return lambda.Code.fromAsset(backend, {
    exclude: [".venv", ".local", "tests", "**/__pycache__", ".pytest_cache"],
    bundling: {
      image: lambda.Runtime.PYTHON_3_12.bundlingImage,
      platform: "linux/arm64",
      command: [
        "bash",
        "-c",
        "pip install uv && uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/req.txt && " +
          "pip install -r /tmp/req.txt -t /asset-output && cp -r src/helperpay /asset-output/",
      ],
      local: {
        tryBundle(outputDir: string) {
          try {
            execSync("uv --version", { stdio: "ignore" });
          } catch {
            return false;
          }
          const req = path.join(outputDir, "..", `req-${path.basename(outputDir)}.txt`);
          const run = (cmd: string) => execSync(cmd, { cwd: backend, stdio: "inherit" });
          run(`uv export --frozen --no-dev --no-hashes --no-emit-project --quiet -o "${req}"`);
          run(
            `uv pip install -r "${req}" --target "${outputDir}" --python-platform aarch64-manylinux2014 ` +
              `--python-version 3.12 --only-binary=:all: --quiet`,
          );
          fs.cpSync(path.join(backend, "src", "helperpay"), path.join(outputDir, "helperpay"), {
            recursive: true,
            filter: (p) => !p.includes("__pycache__"),
          });
          fs.rmSync(req, { force: true });
          return true;
        },
      },
    },
  });
}

const CREATE_USER_CODE = `
import boto3

cognito = boto3.client("cognito-idp")
sm = boto3.client("secretsmanager")


def handler(event, _context):
    p = event["ResourceProperties"]
    pool, user = p["UserPoolId"], p["Username"]
    if event["RequestType"] == "Delete":
        try:
            cognito.admin_delete_user(UserPoolId=pool, Username=user)
        except cognito.exceptions.UserNotFoundException:
            pass
        return {"PhysicalResourceId": f"{pool}/{user}"}
    password = sm.get_secret_value(SecretId=p["SecretArn"])["SecretString"]
    try:
        cognito.admin_create_user(UserPoolId=pool, Username=user, MessageAction="SUPPRESS")
    except cognito.exceptions.UsernameExistsException:
        pass
    cognito.admin_set_user_password(UserPoolId=pool, Username=user, Password=password, Permanent=True)
    return {"PhysicalResourceId": f"{pool}/{user}"}
`;

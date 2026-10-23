import { CfnOutput, Stack, StackProps } from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";

export interface CiStackProps extends StackProps {
  /** "owner/name" of the GitHub repository allowed to deploy. */
  githubRepo: string;
  /** Reuse the account's GitHub OIDC provider if one already exists (there can be only one per account). */
  existingOidcProviderArn?: string;
}

/**
 * Lets GitHub Actions deploy the app with short-lived credentials (OIDC), no stored keys.
 * Only jobs in the repo's "production" environment can assume the role, and the role
 * can do nothing except hand off to the CDK bootstrap roles.
 */
export class CiStack extends Stack {
  constructor(scope: Construct, id: string, props: CiStackProps) {
    super(scope, id, props);

    const provider = props.existingOidcProviderArn
      ? iam.OpenIdConnectProvider.fromOpenIdConnectProviderArn(this, "GitHub", props.existingOidcProviderArn)
      : new iam.OpenIdConnectProvider(this, "GitHub", {
          url: "https://token.actions.githubusercontent.com",
          clientIds: ["sts.amazonaws.com"],
        });

    const role = new iam.Role(this, "DeployRole", {
      description: `Deploys RiveraHelperPay from ${props.githubRepo} (production environment only)`,
      assumedBy: new iam.WebIdentityPrincipal(provider.openIdConnectProviderArn, {
        StringEquals: {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": `repo:${props.githubRepo}:environment:production`,
        },
      }),
    });
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ["sts:AssumeRole"],
        resources: [`arn:${this.partition}:iam::${this.account}:role/cdk-hnb659fds-*-${this.account}-${this.region}`],
      }),
    );

    new CfnOutput(this, "DeployRoleArn", { value: role.roleArn });
  }
}

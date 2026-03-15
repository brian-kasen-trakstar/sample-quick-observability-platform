# Quick Observability Platform - CDK Infrastructure

This directory contains the AWS CDK (Python) infrastructure code for the Quick Observability Platform.

## Stacks

The CDK app defines three stacks, deployed incrementally by `deploy.py`:

| Stack | Created by | Resources |
|-------|-----------|-----------|
| **LogsStack** (`{prefix}-logs`) | `deploy.py --logs` | Customer-managed KMS key (auto-rotation), CloudWatch Log Groups with data protection policies, vended logs delivery configuration |
| **PipelineStack** (`{prefix}-pipeline`) | `deploy.py --pipeline` | S3 data lake bucket, Lambda transform functions, Firehose delivery streams, EventBridge rule, CloudWatch Logs subscription filters |
| **QuickSightStack** (`{prefix}-quicksight`) | `deploy.py --dashboard` | Custom theme, Athena data source, SPICE datasets with daily refresh, analysis, dashboard |

The data catalog (Glue database, Athena tables, views, optional Lake Formation) is created by `scripts/setup_datacatalog.py`, not by CDK.

## Prerequisites

- Python 3.9+
- Node.js 20+ (for CDK CLI)
- AWS CLI v2 configured
- AWS CDK CLI (`npm install -g aws-cdk`)

## Local Development

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Synthesize CloudFormation template (LogsStack only, no context needed)
cdk synth "*-logs"

# Synthesize PipelineStack (requires kmsKeyArn context)
cdk synth "*-pipeline" --context kmsKeyArn=arn:aws:kms:us-east-1:123456789012:key/example
```

## CDK Commands

```bash
cdk synth       # Synthesize CloudFormation template
cdk diff        # Show differences between deployed and local
cdk deploy      # Deploy stack
cdk destroy     # Destroy stack
cdk list        # List all stacks
```

## cdk-nag

All stacks are validated with [cdk-nag AWS Solutions](https://github.com/cdklabs/cdk-nag) checks (`AwsSolutionsChecks`). Suppressions are documented with explicit justifications in the CDK code. To disable cdk-nag during development:

```bash
cdk synth --context enableCdkNag=false
```

## Testing

```bash
source .venv/bin/activate
cdk synth --context kmsKeyArn=arn:aws:kms:us-east-1:123456789012:key/test
```

## File Structure

```
cdk/
├── app.py                 # CDK app entry point (cdk-nag enabled)
├── logs_stack.py          # LogsStack: KMS key, CloudWatch Log Groups, vended logs delivery
├── pipeline_stack.py      # PipelineStack: S3, Firehose, Lambda, EventBridge
├── dashboard_stack.py     # QuickSightStack: theme, data source, datasets, analysis, dashboard
├── requirements.txt       # Python dependencies
├── cdk.json               # CDK configuration
└── README.md              # This file
```

## Key Design Decisions

- **KMS key shared across stacks** — LogsStack creates the key; PipelineStack imports it by ARN. This avoids circular dependencies while keeping encryption centralized.
- **Lambda has no S3 access** — Firehose writes to S3, not Lambda. Lambda only transforms records.
- **S3 bucket and KMS key retained on destroy** — `RemovalPolicy.RETAIN` prevents accidental data loss. Delete manually if no longer needed.
- **Firehose buffering: 128 MB / 900s** — Balances cost (fewer S3 PUTs) with data freshness (15-minute maximum delay).

## Resources

- [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
- [CDK Python Reference](https://docs.aws.amazon.com/cdk/api/v2/python/)
- [cdk-nag](https://github.com/cdklabs/cdk-nag)

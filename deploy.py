#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Amazon Quick Observability Platform - Deploy Script

Detects the Amazon Quick subscription region, prompts for parameters,
and deploys CDK stacks.

Usage:
    python3 deploy.py --logs            # Step 1: Deploy AWS KMS key + CloudWatch Logs
    python3 deploy.py --pipeline        # Step 2: Deploy data pipeline
    python3 deploy.py --datacatalog     # Step 3: Set up data catalog (Athena + optional Lake Formation)
    python3 deploy.py --dashboard       # Step 4: Deploy datasets, analysis, and dashboard
"""

import json
import os
import re
import shutil
import subprocess
import sys

# Suppress noisy JSII node version warnings globally
os.environ["JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION"] = "1"
os.environ["JSII_SILENCE_WARNING_DEPRECATED_NODE_VERSION"] = "1"
os.environ["JSII_DEPRECATED"] = "quiet"
os.environ["CDK_DISABLE_VERSION_CHECK"] = "1"


DEPLOY_CONFIG_PATH = os.path.join("cdk", "deploy-config.json")


def load_deploy_config():
    """Load saved deployment config, or return empty dict."""
    if os.path.exists(DEPLOY_CONFIG_PATH):
        with open(DEPLOY_CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_deploy_config(config):
    """Save deployment config (merges with existing)."""
    existing = load_deploy_config()
    existing.update(config)
    with open(DEPLOY_CONFIG_PATH, "w") as f:
        json.dump(existing, f, indent=2)


def run(cmd, capture=True, check=True):
    """Run a command and return stdout."""
    result = subprocess.run(
        cmd,
        capture_output=capture if capture else False,
        text=True,
    )
    if check and result.returncode != 0:
        print(f"❌ Command failed: {' '.join(cmd)}")
        if capture and result.stderr:
            print(f"   {result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip() if capture and result.stdout else ""


def using_env_credentials():
    """Return True if AWS credentials are provided via environment variables."""
    return bool(os.environ.get("AWS_ACCESS_KEY_ID"))


def run_aws(args):
    """Run an AWS CLI command, return (success, output)."""
    # When env var credentials are active (e.g. MFA session token), strip
    # any --profile argument so env vars are not overridden by a named profile.
    if using_env_credentials():
        filtered = []
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if arg == "--profile":
                skip_next = True
                continue
            filtered.append(arg)
        args = filtered
    result = subprocess.run(["aws"] + args, capture_output=True, text=True)
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    return result.returncode == 0, output


def prompt(label, default=""):
    """Prompt with a default value."""
    if default:
        value = input(f"{label} [{default}]: ").strip()
        return value if value else default
    return input(f"{label}: ").strip()


def check_prerequisites():
    """Check Node.js is installed (required for AWS CDK)."""
    if not shutil.which("node"):
        print("❌ Node.js 20 or later is required for AWS CDK. Install it and try again.")
        sys.exit(1)


def detect_quick_region(profile):
    """Detect Amazon Quick subscription region from namespace ARN."""
    print("🔍 Looking up Amazon Quick subscription...")

    ok, output = run_aws([
        "sts", "get-caller-identity",
        "--query", "Account", "--output", "text",
        "--profile", profile,
    ])
    if not ok:
        print("❌ Could not retrieve AWS account ID.")
        print(f"   Profile: {profile}")
        print(f"   Error: {output}")
        if "Token has expired" in output or "SSO" in output:
            print(f"   Run: aws sso login --profile {profile}")
        sys.exit(1)
    account_id = output

    ok, ns_output = run_aws([
        "quicksight", "list-namespaces",
        "--aws-account-id", account_id,
        "--region", "us-east-1",
        "--profile", profile,
    ])
    if not ok or not ns_output:
        print("❌ No Amazon Quick subscription found in this account.")
        print("   Verify that Amazon Quick is enabled in your account.")
        sys.exit(1)

    match = re.search(r":quicksight:([^:]+):", ns_output)
    if not match:
        print("❌ Could not determine the Amazon Quick subscription region.")
        sys.exit(1)
    qs_region = match.group(1)

    ok, edition = run_aws([
        "quicksight", "describe-account-subscription",
        "--aws-account-id", account_id,
        "--region", qs_region,
        "--query", "AccountInfo.Edition",
        "--output", "text",
        "--profile", profile,
    ])
    edition = edition if ok else "UNKNOWN"

    masked = f"{account_id[:4]}****{account_id[8:]}"
    print(f"  ✓ Account: {masked}")
    print(f"  ✓ Amazon Quick Edition: {edition}")
    print(f"  ✓ Amazon Quick Region: {qs_region}")
    print(f"  ✓ All resources will be deployed in {qs_region}")
    print()

    return account_id, qs_region


def setup_cdk_env(profile, region, account_id):
    """Set environment variables for CDK."""
    os.environ["AWS_PROFILE"] = profile
    os.environ["AWS_DEFAULT_REGION"] = region
    os.environ["AWS_REGION"] = region
    os.environ["CDK_DEFAULT_REGION"] = region
    os.environ["CDK_DEFAULT_ACCOUNT"] = account_id


def find_cdk():
    """Find the CDK CLI command."""
    if shutil.which("cdk"):
        return ["cdk"]
    if shutil.which("npx"):
        return ["npx", "cdk"]
    print("❌ CDK CLI not found. Install with: npm install -g aws-cdk")
    sys.exit(1)


def deploy_cdk(cdk_cmd, stack_name, context, profile):
    """Deploy a CDK stack with context parameters."""
    cmd = cdk_cmd + [
        "deploy", stack_name,
        "--require-approval", "never",
        "--exclusively",
        "--outputs-file", "cdk-outputs.tmp.json",
    ]
    if not using_env_credentials():
        cmd += ["--profile", profile]
    for key, value in context.items():
        cmd.extend(["--context", f"{key}={value}"])

    # Suppress noisy warnings during deployment
    env = os.environ.copy()
    env["JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION"] = "1"
    env["JSII_SILENCE_WARNING_DEPRECATED_NODE_VERSION"] = "1"
    env["JSII_DEPRECATED"] = "quiet"
    env["CDK_DISABLE_VERSION_CHECK"] = "1"

    print(f"🚀 Deploying {stack_name}")
    print(f"   This may take several minutes.")
    result = subprocess.run(cmd, cwd="cdk", env=env)
    if result.returncode != 0:
        print(f"❌ Deployment of {stack_name} failed.")
        sys.exit(1)

    # Merge new outputs into the main outputs file so that deploying one
    # stack doesn't wipe outputs from previously deployed stacks.
    outputs_file = os.path.join("cdk", "cdk-outputs.json")
    tmp_file = os.path.join("cdk", "cdk-outputs.tmp.json")
    existing = {}
    if os.path.exists(outputs_file):
        with open(outputs_file) as f:
            try:
                existing = json.load(f)
            except (json.JSONDecodeError, ValueError):
                existing = {}
    if os.path.exists(tmp_file):
        with open(tmp_file) as f:
            try:
                new_outputs = json.load(f)
            except (json.JSONDecodeError, ValueError):
                new_outputs = {}
        existing.update(new_outputs)
        os.remove(tmp_file)
    with open(outputs_file, "w") as f:
        json.dump(existing, f, indent=2)

    print()


def get_stack_output(stack_name, key):
    """Read a value from cdk-outputs.json."""
    outputs_file = os.path.join("cdk", "cdk-outputs.json")
    if not os.path.exists(outputs_file):
        return None
    with open(outputs_file) as f:
        outputs = json.load(f)
    return outputs.get(stack_name, {}).get(key)


def get_venv_python():
    """Get the path to the venv Python executable."""
    pip_dir = "bin" if os.name != "nt" else "Scripts"
    venv_python = os.path.join("cdk", ".venv", pip_dir, "python3")
    if os.path.exists(venv_python):
        return venv_python
    return "python3"


def setup_venv():
    """Set up Python virtual environment for CDK."""
    print("📦 Setting up CDK environment")
    venv_path = os.path.join("cdk", ".venv")
    if not os.path.exists(venv_path):
        run(["python3", "-m", "venv", venv_path])
    pip_dir = "bin" if os.name != "nt" else "Scripts"
    pip = os.path.join(venv_path, pip_dir, "pip")
    run([pip, "install", "-q", "-r", os.path.join("cdk", "requirements.txt")])
    print("  ✓ CDK environment ready")
    print()


def bootstrap_cdk(cdk_cmd, account_id, region, profile):
    """Bootstrap CDK if needed."""
    ok, _ = run_aws([
        "cloudformation", "describe-stacks",
        "--stack-name", "CDKToolkit",
        "--profile", profile,
        "--region", region,
    ])
    if not ok:
        print("🚀 Bootstrapping CDK (first time only)")
        cmd = cdk_cmd + ["bootstrap", f"aws://{account_id}/{region}"]
        if not using_env_credentials():
            cmd += ["--profile", profile]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print("❌ CDK bootstrap failed.")
            sys.exit(1)
        print("  ✓ CDK bootstrapped")
    else:
        print("  ✓ CDK already bootstrapped")
    print()


def main():
    deploy_logs = "--logs" in sys.argv
    deploy_pipeline = "--pipeline" in sys.argv
    datacatalog_only = "--datacatalog" in sys.argv
    dashboard_only = "--dashboard" in sys.argv

    # Backward compatibility for old flags
    if "--athena" in sys.argv or "--lakeformation" in sys.argv:
        print("⚠️  --athena and --lakeformation have been combined into --datacatalog")
        datacatalog_only = True

    if not deploy_logs and not deploy_pipeline and not datacatalog_only and not dashboard_only:
        print("╔════════════════════════════════════════════════════════════════╗")
        print("║          Amazon Quick Observability Platform - Deploy          ║")
        print("╚════════════════════════════════════════════════════════════════╝")
        print()
        print("Usage:")
        print("  python3 deploy.py --logs            # Step 1: Deploy AWS KMS key + CloudWatch Logs")
        print("  python3 deploy.py --pipeline        # Step 2: Deploy data pipeline")
        print("  python3 deploy.py --datacatalog     # Step 3: Set up data catalog (Athena + optional Lake Formation)")
        print("  python3 deploy.py --dashboard       # Step 4: Deploy datasets, analysis, and dashboard")
        sys.exit(1)

    if deploy_logs:
        print("╔════════════════════════════════════════════════════════════════╗")
        print("║   Amazon Quick Observability - CloudWatch Logs Setup (Step 1)  ║")
        print("╚════════════════════════════════════════════════════════════════╝")
        print()
    elif deploy_pipeline:
        print("╔════════════════════════════════════════════════════════════════╗")
        print("║    Amazon Quick Observability - Data Pipeline Setup (Step 2)   ║")
        print("╚════════════════════════════════════════════════════════════════╝")
        print()

    if deploy_logs and deploy_pipeline:
        print("❌ Use --logs or --pipeline, not both.")
        sys.exit(1)

    if datacatalog_only:
        deploy_datacatalog()
        return

    if dashboard_only:
        deploy_dashboard()
        return

    if deploy_logs:
        check_prerequisites()

    # Load saved config from previous steps
    cfg = load_deploy_config()

    if deploy_pipeline and cfg.get("ResourcePrefix") and cfg.get("AWSProfile") and cfg.get("Region"):
        # Step 2 reuses settings from Step 1
        resource_prefix = cfg["ResourcePrefix"]
        profile = cfg["AWSProfile"]
        region = cfg["Region"]
        # Still need account_id for CDK env
        ok, account_id = run_aws([
            "sts", "get-caller-identity",
            "--query", "Account", "--output", "text",
            "--profile", profile,
        ])
        if not ok:
            print("❌ Could not retrieve AWS account ID.")
            print(f"   Run: aws sso login --profile {profile}")
            sys.exit(1)
        setup_cdk_env(profile, region, account_id)
    else:
        if not deploy_logs:
            check_prerequisites()
        resource_prefix = prompt("Enter a prefix for resource names", cfg.get("ResourcePrefix", "quickobserve"))
        profile = prompt("Enter AWS CLI profile", cfg.get("AWSProfile", "default"))
        account_id, region = detect_quick_region(profile)
        setup_cdk_env(profile, region, account_id)

    # Log group names (only needed for --logs)
    chat_logs = "/aws/vendedlogs/quick/chat"
    feedback_logs = "/aws/vendedlogs/quick/feedback"
    agent_hours_logs = "/aws/vendedlogs/quick/agent-hours"
    index_usage_logs = "/aws/vendedlogs/quick/index-usage"

    if deploy_logs:
        print("📝 CloudWatch log group names:")
        print("   Edit the defaults or press Enter to accept.")
        print()
        chat_logs = prompt("  Chat logs group", chat_logs)
        feedback_logs = prompt("  Feedback logs group", feedback_logs)
        agent_hours_logs = prompt("  Agent hours logs group", agent_hours_logs)
        index_usage_logs = prompt("  Index usage logs group", index_usage_logs)
        print()

        # Message content opt-in
        print("📝 Chat message content logging:")
        print()
        print("   Chat logs contain two fields with potentially sensitive content:")
        print()
        print("   • user_message         — user message in the conversation")
        print("   • system_text_message  — system response in the conversation")
        print()
        print("   N (default): These two fields will not be logged.")
        print()
        print("   Y: These fields will be logged to CloudWatch and the S3 data lake.")
        print("      A data protection policy masks common PII patterns (emails,")
        print("      credit cards, SSNs, US phone numbers, IP addresses) but cannot")
        print("      mask free-text business data.")
        print()
        include_msg = input("  Include chat message content in logs? (y/N): ").strip().lower()
        include_message_content = include_msg == "y"
        if include_message_content:
            print("  ✓ Message content will be logged to CloudWatch and the data lake")
        else:
            print("  ✓ Message content excluded")
        print()

    logs_stack_name = f"{resource_prefix}-logs"
    pipeline_stack_name = f"{resource_prefix}-pipeline"

    # Summary
    if deploy_logs:
        print("╔════════════════════════════════════════════════════════════════╗")
        print("║                       Deployment Summary                       ║")
        print("╚════════════════════════════════════════════════════════════════╝")
        print(f"  Resource Prefix: {resource_prefix}")
        print(f"  AWS Region: {region}")
        print(f"  AWS Profile: {profile}")
        print(f"  Logs Stack: {logs_stack_name}")
        print(f"  Chat Logs: {chat_logs}")
        print(f"  Feedback Logs: {feedback_logs}")
        print(f"  Agent Hours Logs: {agent_hours_logs}")
        print(f"  Index Usage Logs: {index_usage_logs}")
        print(f"  Message Content: {'included' if include_message_content else 'excluded'}")
        print()
        confirm = input("Proceed? (y/N): ").strip().lower()
        if confirm != "y":
            print("❌ Cancelled")
            sys.exit(0)
        print()
    elif deploy_pipeline:
        print(f"  Using saved AWS profile: {profile}")
        print(f"  Deploying pipeline stack: {pipeline_stack_name}")
        print(f"  Region: {region}")
        print()
        confirm = input("Proceed? (y/N): ").strip().lower()
        if confirm != "y":
            print("❌ Cancelled")
            sys.exit(0)
        print()

    # Setup
    setup_venv()
    cdk_cmd = find_cdk()
    bootstrap_cdk(cdk_cmd, account_id, region, profile)

    # Context for CDK
    context = {
        "resourcePrefix": resource_prefix,
        "logsStackName": logs_stack_name,
        "pipelineStackName": pipeline_stack_name,
        "chatLogsGroup": chat_logs,
        "feedbackLogsGroup": feedback_logs,
        "agentHoursLogsGroup": agent_hours_logs,
        "indexUsageLogsGroup": index_usage_logs,
    }

    # Deploy Logs Stack (Step 1)
    if deploy_logs:
        context["includeMessageContent"] = str(include_message_content).lower()
        deploy_cdk(cdk_cmd, logs_stack_name, context, profile)

    if deploy_pipeline:
        # Get KMS key ARN and log group names from logs stack outputs
        kms_key_arn = get_stack_output(logs_stack_name, "KmsKeyArn")
        if not kms_key_arn:
            print(f"❌ Could not read KMS key ARN from {logs_stack_name} outputs.")
            print("   Deploy the logs stack first: python3 deploy.py --logs")
            sys.exit(1)

        # Read log group names from logs stack outputs
        chat_logs = get_stack_output(logs_stack_name, "ChatLogsGroup") or chat_logs
        feedback_logs = get_stack_output(logs_stack_name, "FeedbackLogsGroup") or feedback_logs
        agent_hours_logs = get_stack_output(logs_stack_name, "AgentHoursLogsGroup") or agent_hours_logs
        index_usage_logs = get_stack_output(logs_stack_name, "IndexUsageLogsGroup") or index_usage_logs
        include_msg_content = get_stack_output(logs_stack_name, "IncludeMessageContent") or "false"

        context["kmsKeyArn"] = kms_key_arn
        context["chatLogsGroup"] = chat_logs
        context["feedbackLogsGroup"] = feedback_logs
        context["agentHoursLogsGroup"] = agent_hours_logs
        context["indexUsageLogsGroup"] = index_usage_logs
        context["includeMessageContent"] = include_msg_content
        context["stackName"] = pipeline_stack_name

        # Deploy Pipeline Stack (Step 2)
        deploy_cdk(cdk_cmd, pipeline_stack_name, context, profile)

    # Done
    save_deploy_config({
        "AWSProfile": profile,
        "ResourcePrefix": resource_prefix,
        "Region": region,
    })
    print()
    if deploy_logs:
        print("  ✓ AWS KMS key deployed with automatic rotation")
        print("  ✓ CloudWatch Logs delivery configured")
        print()
        print("  Generate data by using Amazon Quick:")
        print("    • Chat logs: ask questions using the chat agent (My Assistant)")
        print("    • Feedback logs: provide thumbs up/down on chat responses")
        print("    • Agent hours: use Flows, Research, or Automations")
        print("    • Index usage logs: create or update a Space or Knowledge Base")
        print()
        print(f"  Verify logs: aws logs tail {chat_logs} --since 1h --profile {profile}")
        print()
        print("  Next: python3 deploy.py --pipeline")
    elif deploy_pipeline:
        print("  ✓ Data pipeline deployed")
        print()
        print("  Next: python3 deploy.py --datacatalog")
    print()


def deploy_datacatalog():
    """Set up data catalog: optional Lake Formation + Athena tables and views (Step 3)."""
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║    Amazon Quick Observability - Data Catalog Setup (Step 3)    ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print()

    outputs_file = os.path.join("cdk", "cdk-outputs.json")
    if not os.path.exists(outputs_file):
        print("❌ cdk-outputs.json not found.")
        print("   Run 'python3 deploy.py --logs' and 'python3 deploy.py --pipeline' first.")
        sys.exit(1)

    with open(outputs_file) as f:
        outputs = json.load(f)

    # Find pipeline stack outputs
    bucket = None
    kms_key_arn = None
    region = None
    include_message_content = False
    for stack_name, stack_outputs in outputs.items():
        if "DataLakeBucketName" in stack_outputs:
            bucket = stack_outputs["DataLakeBucketName"]
        if "KmsKeyArn" in stack_outputs:
            kms_key_arn = stack_outputs["KmsKeyArn"]
            match = re.search(r":kms:([^:]+):", kms_key_arn)
            if match:
                region = match.group(1)
        if "IncludeMessageContent" in stack_outputs:
            include_message_content = stack_outputs["IncludeMessageContent"] == "true"

    if not bucket:
        print("❌ Could not find DataLakeBucketName in stack outputs.")
        print("   Run 'python3 deploy.py --pipeline' to deploy the pipeline first.")
        sys.exit(1)

    if not region:
        region = "us-east-1"

    cfg = load_deploy_config()
    if cfg.get("AWSProfile"):
        profile = cfg["AWSProfile"]
        print(f"  Using saved AWS profile: {profile}")
    else:
        profile = prompt("Enter AWS CLI profile", "default")

    # Prompt for database name and verify it doesn't already exist.
    # This script creates a new database — using an existing one risks
    # overwriting tables that belong to other workloads.
    while True:
        database = prompt("Enter Athena database name", "quickobserve_db")
        ok, _ = run_aws([
            "glue", "get-database",
            "--name", database,
            "--profile", profile,
            "--region", region,
        ])
        if ok:
            print(f"  ⚠ Database '{database}' already exists. Enter a name that does not exist yet.")
            print()
        else:
            break

    workgroup = prompt("Enter Athena workgroup name", "primary")

    # Auto-detect query result location from the workgroup configuration.
    # Show as default so the user can accept or override.
    athena_output_default = ""
    ok, wg_output = run_aws([
        "athena", "get-work-group",
        "--work-group", workgroup,
        "--query", "WorkGroup.Configuration.ResultConfiguration.OutputLocation",
        "--output", "text",
        "--profile", profile,
        "--region", region,
    ])
    if ok and wg_output and wg_output != "None":
        athena_output_default = wg_output

    if athena_output_default:
        athena_output = prompt("Enter S3 location for Athena query results", athena_output_default)
    else:
        athena_output = prompt("Enter S3 location for Athena query results (e.g. s3://my-bucket/athena-results/)")

    if not athena_output.startswith("s3://"):
        print("❌ S3 location for Athena query results must start with s3://")
        sys.exit(1)

    # Validate S3 location exists
    s3_bucket_name = athena_output.replace("s3://", "").split("/")[0]
    ok, s3_output = run_aws([
        "s3api", "head-bucket",
        "--bucket", s3_bucket_name,
        "--profile", profile,
        "--region", region,
    ])
    if not ok:
        print(f"❌ S3 bucket does not exist or is not accessible: {s3_bucket_name}")
        print(f"   Error: {s3_output}")
        sys.exit(1)
    print(f"  ✓ S3 bucket verified: {s3_bucket_name}")

    # Access control prompt
    print()
    print("📝 Data lake access control:")
    print()
    print("   1. AWS Lake Formation (default)")
    print("      Fine-grained access control at the database, table, and column level.")
    print("      Requires Lake Formation administrator privileges.")
    print()
    print("   2. IAM")
    print("      IAM-based access control.")
    print()
    access_choice = input("  Choose access control [1/2] (default: 1): ").strip()
    use_lake_formation = access_choice != "2"

    if use_lake_formation:
        print("  ✓ Using AWS Lake Formation for access control")
    else:
        print("  ✓ Using IAM policies for access control (Lake Formation skipped)")
    print()

    print(f"  Database:       {database}")
    print(f"  Workgroup:      {workgroup}")
    print(f"  Query Results:  {athena_output}")
    print(f"  Data Lake:      {bucket}")
    print(f"  Access Control: {'Lake Formation' if use_lake_formation else 'IAM policies'}")
    print(f"  Region:         {region}")
    print()

    confirm = input("Proceed? (y/N): ").strip().lower()
    if confirm != "y":
        print("❌ Cancelled")
        sys.exit(0)
    print()

    setup_venv()
    venv_python = get_venv_python()

    # Run the consolidated data catalog setup script
    access_mode = "lakeformation" if use_lake_formation else "iam"
    print(f"📊 Setting up data catalog ({access_mode})")

    catalog_cmd = [
        venv_python, "scripts/setup_datacatalog.py",
        "--profile", profile,
        "--region", region,
        "--database", database,
        "--bucket", bucket,
        "--workgroup", workgroup,
        "--output-location", athena_output,
        "--access-control", access_mode,
    ]
    if use_lake_formation and kms_key_arn:
        catalog_cmd.extend(["--kms-key-arn", kms_key_arn])
    if include_message_content:
        catalog_cmd.append("--include-message-content")

    os.environ["AWS_PROFILE"] = profile
    os.environ["AWS_DEFAULT_REGION"] = region
    result = subprocess.run(catalog_cmd)
    if result.returncode != 0:
        print("❌ Data catalog setup failed.")
        sys.exit(1)

    print("  ✓ Data catalog configured")
    print()
    print("  Verify data is flowing to Athena tables:")
    query_prefix = "    SELECT COUNT(*) FROM"
    for t in ["chat_logs", "feedback_logs", "agent_hours_logs", "cloudtrail_events", "index_usage_logs"]:
        print(f"{query_prefix} {database}.{t}")
    print()
    print("  Next: python3 deploy.py --dashboard")
    print()

    # Save datacatalog config for cleanup.py
    datacatalog_config = {
        "Database": database,
        "Workgroup": workgroup,
        "OutputLocation": athena_output,
        "AccessControl": access_mode,
        "Region": region,
        "AWSProfile": profile,
    }
    config_path = os.path.join("cdk", "datacatalog-config.json")
    with open(config_path, "w") as f:
        json.dump(datacatalog_config, f, indent=2)

    save_deploy_config({
        "AWSProfile": profile,
        "Database": database,
        "Workgroup": workgroup,
        "OutputLocation": athena_output,
    })


def deploy_dashboard():
    """Set up Quick Sight datasets, analysis, and dashboard (Step 4) via CDK."""
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║     Amazon Quick Observability - Dashboard Setup (Step 4)      ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print()

    outputs_file = os.path.join("cdk", "cdk-outputs.json")
    if not os.path.exists(outputs_file):
        print("❌ cdk-outputs.json not found.")
        print("   Run 'python3 deploy.py --logs' and 'python3 deploy.py --pipeline' first.")
        sys.exit(1)

    datacatalog_config_file = os.path.join("cdk", "datacatalog-config.json")
    if not os.path.exists(datacatalog_config_file):
        print("❌ Data catalog not configured.")
        print("   Run 'python3 deploy.py --datacatalog' first (Step 3).")
        sys.exit(1)

    with open(outputs_file) as f:
        outputs = json.load(f)

    # Detect region and KMS key ARN from stack outputs
    region = None
    kms_key_arn = None
    for stack_name, stack_outputs in outputs.items():
        if "KmsKeyArn" in stack_outputs:
            kms_key_arn = stack_outputs["KmsKeyArn"]
            match = re.search(r":kms:([^:]+):", kms_key_arn)
            if match:
                region = match.group(1)

    if not region:
        region = "us-east-1"

    cfg = load_deploy_config()
    if cfg.get("AWSProfile"):
        profile = cfg["AWSProfile"]
        print(f"  Using saved AWS profile: {profile}")
    else:
        profile = prompt("Enter AWS CLI profile", "default")

    if cfg.get("Database") and cfg.get("Workgroup"):
        database = cfg["Database"]
        workgroup = cfg["Workgroup"]
        print(f"  Using saved Athena database: {database}")
        print(f"  Using saved Athena workgroup: {workgroup}")
    else:
        database = prompt("Enter Athena database name", cfg.get("Database", "quickobserve_db"))
        workgroup = prompt("Enter Athena workgroup name", cfg.get("Workgroup", "primary"))

    # Auto-detect Quick Sight user from caller identity
    detected_owner = ""
    _users = []
    ok, caller_arn = run_aws(["sts", "get-caller-identity", "--query", "Arn", "--output", "text", "--profile", profile, "--region", region])
    if ok:
        caller_name = caller_arn.strip().rsplit("/", 1)[-1]
        ok2, account_id_str = run_aws(["sts", "get-caller-identity", "--query", "Account", "--output", "text", "--profile", profile, "--region", region])
        _account_id = account_id_str.strip() if ok2 else ""

        ok3, users_json = run_aws(["quicksight", "list-users", "--aws-account-id", _account_id, "--namespace", "default", "--query", "UserList[].[UserName,Arn,Role]", "--output", "json", "--profile", profile, "--region", region])
        _users = []
        if ok3:
            try:
                _users = json.loads(users_json)
            except Exception:
                pass

        for u in _users:
            if u[0] == caller_name:
                detected_owner = u[1]
                break
        if not detected_owner:
            for u in _users:
                if u[0] in caller_arn:
                    detected_owner = u[1]
                    break

    if detected_owner:
        owner = detected_owner
        print(f"  Amazon Quick user: {owner}")
    else:
        print()
        print("  Could not auto-detect Amazon Quick user from caller identity.")
        if _users:
            print("  Available Amazon Quick users:")
            for i, u in enumerate(_users, 1):
                print(f"    {i}. {u[1]} ({u[2]})")
        owner = prompt("  Enter Amazon Quick user ARN")

    if not owner:
        print("❌ Amazon Quick user is required.")
        sys.exit(1)

    # Infer resource prefix from existing stack names in outputs
    resource_prefix = "quickobserve"
    for sn in outputs.keys():
        if sn.endswith("-logs"):
            resource_prefix = sn.replace("-logs", "")
            break
        if sn.endswith("-pipeline"):
            resource_prefix = sn.replace("-pipeline", "")
            break

    confirm = input("Proceed? (y/N): ").strip().lower()
    if confirm != "y":
        print("❌ Cancelled")
        sys.exit(0)
    print()

    # Read log group names from outputs
    logs_stack_name = f"{resource_prefix}-logs"
    pipeline_stack_name = f"{resource_prefix}-pipeline"
    chat_logs = "/aws/vendedlogs/quick/chat"
    feedback_logs = "/aws/vendedlogs/quick/feedback"
    agent_hours_logs = "/aws/vendedlogs/quick/agent-hours"
    index_usage_logs = "/aws/vendedlogs/quick/index-usage"
    for sn, so in outputs.items():
        if "ChatLogsGroup" in so:
            chat_logs = so["ChatLogsGroup"]
        if "FeedbackLogsGroup" in so:
            feedback_logs = so["FeedbackLogsGroup"]
        if "AgentHoursLogsGroup" in so:
            agent_hours_logs = so["AgentHoursLogsGroup"]
        if "IndexUsageLogsGroup" in so:
            index_usage_logs = so["IndexUsageLogsGroup"]

    setup_venv()
    cdk_cmd = find_cdk()

    context = {
        "resourcePrefix": resource_prefix,
        "logsStackName": logs_stack_name,
        "pipelineStackName": pipeline_stack_name,
        "chatLogsGroup": chat_logs,
        "feedbackLogsGroup": feedback_logs,
        "agentHoursLogsGroup": agent_hours_logs,
        "indexUsageLogsGroup": index_usage_logs,
        "quicksightDatabase": database,
        "quicksightWorkgroup": workgroup,
        "quicksightOwnerArn": owner,
    }

    quicksight_stack_name = f"{resource_prefix}-quicksight"
    deploy_cdk(cdk_cmd, quicksight_stack_name, context, profile)

    print()
    print("  ✓ Quick Observability dashboard deployed")
    print()
    print("  Open the Amazon Quick console to verify data:")
    print("    • Dashboards → Quick Observability Dashboard")
    print("    • Analyses → Quick Observability Analysis")
    print()
    print("  Once the dashboard shows data, create the Quick Sight topic:")
    print("    python3 scripts/create_topic.py")
    print()
    print("  Next, create a custom chat agent (after topic is created):")
    print("    See README.md Step 6 for instructions")
    print()


if __name__ == "__main__":
    main()

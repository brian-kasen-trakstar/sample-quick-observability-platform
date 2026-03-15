#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Amazon Quick Observability Platform - Cleanup

Removes all resources created by deploy.py across all four deployment steps:
  Step 1 (--logs):        AWS KMS key, CloudWatch Log Groups, vended logs delivery
  Step 2 (--pipeline):    S3 data lake, Lambda, Firehose, EventBridge
  Step 3 (--datacatalog): Glue database, Athena tables/views, Lake Formation
  Step 4 (--dashboard):   Quick Sight data source, datasets, analysis, dashboard, topic

CDK stacks are destroyed in reverse dependency order:
  quicksight -> pipeline -> logs

The AWS KMS key and S3 data lake bucket are RETAINED by default (CDK removal
policy). Delete them manually if no longer needed.

Usage:
    python3 cleanup.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time


# ── Helpers (same patterns as deploy.py) ──────────────────────────────────

def run_aws(args):
    """Run an AWS CLI command, return (success, output)."""
    result = subprocess.run(["aws"] + args, capture_output=True, text=True)
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    return result.returncode == 0, output


def prompt(label, default=""):
    """Prompt with a default value."""
    if default:
        value = input(f"{label} [{default}]: ").strip()
        return value if value else default
    return input(f"{label}: ").strip()


def confirm(message):
    """Ask for y/N confirmation. Returns True if user confirms."""
    response = input(f"{message} (y/N): ").strip().lower()
    return response == "y"


def find_cdk():
    """Find the CDK CLI command."""
    if shutil.which("cdk"):
        return ["cdk"]
    if shutil.which("npx"):
        return ["npx", "cdk"]
    print("❌ CDK CLI not found. Install with: npm install -g aws-cdk")
    sys.exit(1)


def setup_venv():
    """Activate or create the CDK virtual environment."""
    venv_dir = os.path.join("cdk", ".venv")
    if not os.path.isdir(venv_dir):
        print("  Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)

    # Install dependencies
    pip_dir = "bin" if os.name != "nt" else "Scripts"
    pip = os.path.join(venv_dir, pip_dir, "pip")
    subprocess.run(
        [pip, "install", "-q", "-r", os.path.join("cdk", "requirements.txt")],
        check=True,
    )


def destroy_cdk_stack(cdk_cmd, stack_name, context, profile):
    """Destroy a CDK stack with context parameters."""
    cmd = cdk_cmd + [
        "destroy", stack_name,
        "--force",
        "--exclusively",
        "--profile", profile,
    ]
    for key, value in context.items():
        cmd.extend(["--context", f"{key}={value}"])

    env = os.environ.copy()
    env["JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION"] = "1"
    env["JSII_SILENCE_WARNING_DEPRECATED_NODE_VERSION"] = "1"
    env["JSII_DEPRECATED"] = "quiet"
    env["CDK_DISABLE_VERSION_CHECK"] = "1"

    print(f"  Destroying {stack_name}")
    result = subprocess.run(cmd, cwd="cdk", env=env)
    if result.returncode != 0:
        print(f"  ⚠ Destroy of {stack_name} returned non-zero exit code.")
        print(f"    Check the CloudFormation console for details.")
        return False
    print(f"  ✓ {stack_name} destroyed")
    return True


def drop_athena_database(database, workgroup, output_location, profile, region):
    """Drop an Athena database with CASCADE (removes all tables and views)."""
    query = f"DROP DATABASE IF EXISTS `{database}` CASCADE"
    cmd = [
        "athena", "start-query-execution",
        "--query-string", query,
        "--work-group", workgroup,
        "--result-configuration", f"OutputLocation={output_location}",
        "--query", "QueryExecutionId",
        "--output", "text",
        "--profile", profile,
        "--region", region,
    ]
    ok, output = run_aws(cmd)
    if not ok or not output:
        print(f"  ⚠ Could not start DROP DATABASE query: {output}")
        return False

    query_id = output
    print(f"  Waiting for DROP DATABASE to complete")
    for _ in range(30):
        ok2, state_output = run_aws([
            "athena", "get-query-execution",
            "--query-execution-id", query_id,
            "--query", "QueryExecution.Status.State",
            "--output", "text",
            "--profile", profile,
            "--region", region,
        ])
        state = state_output if ok2 else "UNKNOWN"
        if state == "SUCCEEDED":
            print(f"  ✓ Dropped database: {database} (tables and views removed)")
            return True
        if state in ("FAILED", "CANCELLED"):
            ok3, reason = run_aws([
                "athena", "get-query-execution",
                "--query-execution-id", query_id,
                "--query", "QueryExecution.Status.StateChangeReason",
                "--output", "text",
                "--profile", profile,
                "--region", region,
            ])
            print(f"  ⚠ DROP DATABASE {state}: {reason if ok3 else 'unknown reason'}")
            return False
        time.sleep(2)

    print("  ⚠ DROP DATABASE timed out. Check the Athena console.")
    return False


def cleanup_lake_formation(data_lake_bucket, database, profile, region):
    """Deregister S3 location and revoke Lake Formation grants."""
    ok, account_id = run_aws([
        "sts", "get-caller-identity",
        "--query", "Account", "--output", "text",
        "--profile", profile, "--region", region,
    ])
    ok2, caller_arn = run_aws([
        "sts", "get-caller-identity",
        "--query", "Arn", "--output", "text",
        "--profile", profile, "--region", region,
    ])
    if not ok or not ok2:
        print("  ⚠ Could not determine account ID / caller ARN. Skipping.")
        return

    account_id = account_id.strip()
    caller_arn = caller_arn.strip()

    # Convert STS session ARN to IAM role ARN (same as setup_datacatalog.py)
    # Lake Formation grants were made to the IAM role, not the STS session.
    if ":assumed-role/" in caller_arn:
        try:
            after_assumed = caller_arn.split(":assumed-role/")[1]
            role_name = "/".join(after_assumed.split("/")[:-1])
            ok_role, role_arn_output = run_aws([
                "iam", "get-role", "--role-name", role_name,
                "--query", "Role.Arn", "--output", "text",
                "--profile", profile, "--region", region,
            ])
            if ok_role and role_arn_output:
                caller_arn = role_arn_output.strip()
        except Exception:
            pass  # Fall back to original ARN

    qs_role_arn = f"arn:aws:iam::{account_id}:role/service-role/aws-quicksight-service-role-v0"

    # Deregister S3 data location
    if data_lake_bucket:
        s3_arn = f"arn:aws:s3:::{data_lake_bucket}"
        ok, _ = run_aws([
            "lakeformation", "deregister-resource",
            "--resource-arn", s3_arn,
            "--profile", profile, "--region", region,
        ])
        if ok:
            print(f"  ✓ Deregistered S3 location: {data_lake_bucket}")
        else:
            print(f"  ⚠ Could not deregister S3 location (may not be registered)")

    # Revoke grants on the database
    if database:
        # Caller database grants
        db_resource = json.dumps({"Database": {"CatalogId": account_id, "Name": database}})
        ok, _ = run_aws([
            "lakeformation", "revoke-permissions",
            "--principal", f"DataLakePrincipalIdentifier={caller_arn}",
            "--resource", db_resource,
            "--permissions", "ALL",
            "--permissions-with-grant-option", "ALL",
            "--profile", profile, "--region", region,
        ])
        print(f"  {'✓' if ok else '⚠'} Revoke caller database grants: {'done' if ok else 'skipped (may already be removed)'}")

    # Quick Sight database grants
        ok, _ = run_aws([
            "lakeformation", "revoke-permissions",
            "--principal", f"DataLakePrincipalIdentifier={qs_role_arn}",
            "--resource", db_resource,
            "--permissions", "DESCRIBE",
            "--profile", profile, "--region", region,
        ])
        print(f"  {'✓' if ok else '⚠'} Revoke Amazon Quick database grants: {'done' if ok else 'skipped'}")

    # Revoke data location grants
    if data_lake_bucket:
        s3_arn = f"arn:aws:s3:::{data_lake_bucket}"
        loc_resource = json.dumps({"DataLocation": {"CatalogId": account_id, "ResourceArn": s3_arn}})

        ok, _ = run_aws([
            "lakeformation", "revoke-permissions",
            "--principal", f"DataLakePrincipalIdentifier={caller_arn}",
            "--resource", loc_resource,
            "--permissions", "DATA_LOCATION_ACCESS",
            "--profile", profile, "--region", region,
        ])
        print(f"  {'✓' if ok else '⚠'} Revoke caller data location grants: {'done' if ok else 'skipped'}")

        ok, _ = run_aws([
            "lakeformation", "revoke-permissions",
            "--principal", f"DataLakePrincipalIdentifier={qs_role_arn}",
            "--resource", loc_resource,
            "--permissions", "DATA_LOCATION_ACCESS",
            "--profile", profile, "--region", region,
        ])
        print(f"  {'✓' if ok else '⚠'} Revoke Amazon Quick data location grants: {'done' if ok else 'skipped'}")


def main():
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║         Amazon Quick Observability Platform - Cleanup          ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print()

    # ── Load configuration ────────────────────────────────────────────────
    outputs_file = os.path.join("cdk", "cdk-outputs.json")
    if not os.path.exists(outputs_file):
        print("❌ cdk/cdk-outputs.json not found. Nothing to clean up.")
        sys.exit(1)

    with open(outputs_file) as f:
        outputs = json.load(f)

    # Detect stack names and resource prefix
    logs_stack = ""
    pipeline_stack = ""
    quicksight_stack = ""
    resource_prefix = ""

    for stack_name in outputs:
        if stack_name.endswith("-logs"):
            logs_stack = stack_name
            resource_prefix = stack_name.removesuffix("-logs")
        elif stack_name.endswith("-pipeline"):
            pipeline_stack = stack_name
        elif stack_name.endswith("-quicksight"):
            quicksight_stack = stack_name

    if not resource_prefix:
        for stack_name in outputs:
            if stack_name.endswith("-pipeline"):
                resource_prefix = stack_name.removesuffix("-pipeline")
                break

    if not resource_prefix:
        print("❌ Could not determine resource prefix from cdk-outputs.json")
        sys.exit(1)

    # Read key values from outputs
    logs_outputs = outputs.get(logs_stack, {})
    pipeline_outputs = outputs.get(pipeline_stack, {})

    kms_key_arn = logs_outputs.get("KmsKeyArn", "")
    chat_logs_group = logs_outputs.get("ChatLogsGroup", "")
    feedback_logs_group = logs_outputs.get("FeedbackLogsGroup", "")
    agent_hours_logs_group = logs_outputs.get("AgentHoursLogsGroup", "")
    data_lake_bucket = pipeline_outputs.get("DataLakeBucketName", "")

    # Detect region from KMS key ARN
    region = "us-east-1"
    if kms_key_arn:
        match = re.search(r":kms:([^:]+):", kms_key_arn)
        if match:
            region = match.group(1)

    # Read saved deploy config for defaults
    deploy_config_path = os.path.join("cdk", "deploy-config.json")
    deploy_config = {}
    if os.path.exists(deploy_config_path):
        with open(deploy_config_path) as f:
            deploy_config = json.load(f)

    profile = prompt("Enter AWS CLI profile", deploy_config.get("AWSProfile", "default"))

    os.environ["AWS_PROFILE"] = profile
    os.environ["AWS_DEFAULT_REGION"] = region

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    print("Detected configuration:")
    print(f"  Resource Prefix:   {resource_prefix}")
    print(f"  Region:            {region}")
    print(f"  Profile:           {profile}")
    if quicksight_stack:
        print(f"  Dashboard Stack:   {quicksight_stack} (datasets, analysis, dashboard, topic)")
    if pipeline_stack:
        print(f"  Pipeline Stack:    {pipeline_stack} (S3, Lambda, Firehose, EventBridge)")
    if logs_stack:
        print(f"  Logs Stack:        {logs_stack} (AWS KMS, Log Groups, vended logs delivery)")
    print()
    print("⚠️  The AWS KMS key and S3 data lake bucket are RETAINED by CDK:")
    if kms_key_arn:
        print(f"   KMS key: {kms_key_arn}")
    if data_lake_bucket:
        print(f"   S3 bucket: {data_lake_bucket}")
    print("   Delete them manually if no longer needed.")
    print()

    if not confirm("Proceed?"):
        print("❌ Cancelled")
        sys.exit(0)
    print()

    # ── Setup ─────────────────────────────────────────────────────────────
    setup_venv()
    cdk_cmd = find_cdk()

    # Build shared CDK context
    context = {
        "resourcePrefix": resource_prefix,
        "logsStackName": logs_stack,
        "chatLogsGroup": chat_logs_group,
        "feedbackLogsGroup": feedback_logs_group,
        "agentHoursLogsGroup": agent_hours_logs_group,
    }
    if pipeline_stack:
        context["pipelineStackName"] = pipeline_stack
    if kms_key_arn:
        context["kmsKeyArn"] = kms_key_arn

    # Set CDK environment variables
    ok, account_id = run_aws([
        "sts", "get-caller-identity",
        "--query", "Account", "--output", "text",
        "--profile", profile, "--region", region,
    ])
    if ok:
        os.environ["CDK_DEFAULT_ACCOUNT"] = account_id.strip()
    os.environ["CDK_DEFAULT_REGION"] = region
    os.environ["AWS_REGION"] = region

    def fail(message):
        """Print error and exit, preserving config files for retry."""
        print(f"\n❌ {message}")
        print("   Configuration files preserved. Fix the issue and re-run: python3 cleanup.py")
        sys.exit(1)

    # ── Delete Quick Sight Topic (created outside CDK) ──────────────────
    if quicksight_stack:
        print()
        topic_id = f"{resource_prefix}-observability-topic"
        print(f"🗑️  Deleting Quick Sight topic: {topic_id}")
        ok_topic, _ = run_aws([
            "quicksight", "delete-topic",
            "--aws-account-id", account_id.strip(),
            "--topic-id", topic_id,
            "--profile", profile, "--region", region,
        ])
        if ok_topic:
            print(f"  ✓ Topic deleted")
        else:
            print(f"  ⚠ Topic not found or already deleted")

    # ── Destroy Quick Sight CDK stack ─────────────────────────────────────
    if quicksight_stack:
        print()
        print(f"🗑️  Destroying dashboard stack: {quicksight_stack}")
        qs_context = dict(context)
        qs_context["quicksightOwnerArn"] = f"arn:aws:quicksight:{region}:000000000000:user/default/cleanup"
        qs_context["quicksightDatabase"] = "dummy"
        qs_context["quicksightWorkgroup"] = "primary"
        if not destroy_cdk_stack(cdk_cmd, quicksight_stack, qs_context, profile):
            fail("Dashboard stack destroy failed.")
        print()

    # ── Lake Formation cleanup (while database and S3 bucket still exist) ─
    datacatalog_config = {}
    datacatalog_config_path = os.path.join("cdk", "datacatalog-config.json")
    if os.path.exists(datacatalog_config_path):
        with open(datacatalog_config_path) as f:
            datacatalog_config = json.load(f)

    saved_db = datacatalog_config.get("Database", "")
    saved_wg = datacatalog_config.get("Workgroup", "primary")
    saved_output = datacatalog_config.get("OutputLocation", "")
    saved_access = datacatalog_config.get("AccessControl", "")

    print("🗑️  Lake Formation cleanup")
    use_lf = saved_access == "lakeformation"
    if use_lf:
        print("  Lake Formation was used for access control (from datacatalog config).")
        cleanup_lake_formation(data_lake_bucket, saved_db, profile, region)
    elif confirm("Was Lake Formation used for access control?"):
        cleanup_lake_formation(data_lake_bucket, saved_db, profile, region)
    else:
        print("  Skipping Lake Formation cleanup.")
    print()

    # ── Drop Athena database ─────────────────────────────────────────────
    print("🗑️  Athena / Glue catalog cleanup")
    if saved_db:
        athena_db = saved_db
        workgroup = saved_wg
        athena_output = saved_output
        print(f"  Dropping database: {athena_db}")
        if not athena_output.startswith("s3://"):
            fail("Invalid S3 location for Athena query results.")
        if not drop_athena_database(athena_db, workgroup, athena_output, profile, region):
            fail("Athena database drop failed.")
    else:
        print("  No saved database configuration found. Skipping Athena cleanup.")
    print()

    # ── Destroy Pipeline CDK stack ───────────────────────────────────────
    if pipeline_stack:
        print(f"🗑️  Destroying pipeline stack: {pipeline_stack}")
        pl_context = dict(context)
        pl_context["stackName"] = pipeline_stack
        if not destroy_cdk_stack(cdk_cmd, pipeline_stack, pl_context, profile):
            fail("Pipeline stack destroy failed.")
        print()

    # ── Destroy Logs CDK stack ───────────────────────────────────────────
    if logs_stack:
        print(f"🗑️  Destroying logs stack: {logs_stack}")
        if not destroy_cdk_stack(cdk_cmd, logs_stack, context, profile):
            fail("Logs stack destroy failed.")
        print()

    # ── Remove config files and CDK output ──────────────────────────────
    print("🗑️  Cleaning up configuration files")
    for cfg in ["cdk/cdk-outputs.json", "cdk/datacatalog-config.json", "cdk/deploy-config.json"]:
        if os.path.exists(cfg):
            os.remove(cfg)
    cdk_out = os.path.join("cdk", "cdk.out")
    if os.path.isdir(cdk_out):
        shutil.rmtree(cdk_out)
    print("  ✓ Configuration files and CDK output removed")
    print()

    # ── Done ──────────────────────────────────────────────────────────────
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║                      Cleanup Complete! 🎉                      ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print()
    print("⚠️  Manually delete if no longer needed:")
    print(f"  • AWS KMS key: {kms_key_arn or 'unknown'}")
    print(f"  • S3 data lake bucket: {data_lake_bucket or 'unknown'}")
    print()


if __name__ == "__main__":
    main()

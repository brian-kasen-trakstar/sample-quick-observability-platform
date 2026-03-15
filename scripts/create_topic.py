#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Create the Amazon Quick Observability Quick Sight Topic.

The topic requires all SPICE datasets to have completed at least one
successful ingestion with data. Run this after generating activity in
Amazon Quick (chat, feedback, agent hours) so the datasets have rows.

Usage:
    python3 scripts/create_topic.py

The script reads dataset ARNs and the Quick Sight owner from
cdk/cdk-outputs.json and cdk/deploy-config.json.
"""

import argparse
import json
import os
import subprocess
import sys
import time

# Auto-activate the CDK virtual environment if boto3 is not available
try:
    import boto3
except ImportError:
    venv_python = os.path.join("cdk", ".venv", "bin", "python3")
    if os.path.exists(venv_python):
        env = os.environ.copy()
        env["JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION"] = "1"
        sys.exit(subprocess.call([venv_python] + sys.argv, env=env))
    else:
        print("❌ boto3 not found. Run: pip install boto3")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Create Quick Observability Quick Sight Topic")
    parser.add_argument("--profile", default=None, help="AWS CLI profile (auto-detected from config)")
    parser.add_argument("--region", default=None, help="AWS region (auto-detected from config)")
    args = parser.parse_args()

    # Read saved config
    deploy_config_file = os.path.join("cdk", "deploy-config.json")
    deploy_config = {}
    if os.path.exists(deploy_config_file):
        with open(deploy_config_file) as f:
            deploy_config = json.load(f)

    profile = args.profile or deploy_config.get("AWSProfile", "default")
    region = args.region  # None if not provided on CLI

    # Detect region from KMS key ARN in outputs
    outputs_file = os.path.join("cdk", "cdk-outputs.json")
    if not os.path.exists(outputs_file):
        print("❌ cdk/cdk-outputs.json not found. Deploy the dashboard first.")
        return 1

    with open(outputs_file) as f:
        outputs = json.load(f)

    if not region:
        import re
        for stack_outputs in outputs.values():
            if "KmsKeyArn" in stack_outputs:
                match = re.search(r":kms:([^:]+):", stack_outputs["KmsKeyArn"])
                if match:
                    region = match.group(1)
        if not region:
            region = deploy_config.get("Region", "us-east-1")

    session = boto3.Session(profile_name=profile, region_name=region)
    qs = session.client("quicksight")
    sts = session.client("sts")
    account_id = sts.get_caller_identity()["Account"]

    # Find dataset ARNs and resource prefix from outputs
    dataset_arns = {}
    resource_prefix = "quickobserve"
    for stack_name, stack_outputs in outputs.items():
        if stack_name.endswith("-quicksight"):
            resource_prefix = stack_name.replace("-quicksight", "")
            for key, value in stack_outputs.items():
                if key.startswith("DatasetArn"):
                    # Extract suffix: DatasetArnchatactivity -> chat-activity
                    dataset_arns[key] = value

    if not dataset_arns:
        print("❌ No dataset ARNs found in cdk-outputs.json.")
        print("   Run 'python3 deploy.py --dashboard' first.")
        return 1

    topic_id = f"{resource_prefix}-observability-topic"

    # Detect Quick Sight owner
    caller_name = sts.get_caller_identity()["Arn"].rsplit("/", 1)[-1]
    owner_arn = ""
    try:
        users = qs.list_users(AwsAccountId=account_id, Namespace="default")["UserList"]
        for u in users:
            if u["UserName"] == caller_name:
                owner_arn = u["Arn"]
                break
        if not owner_arn:
            for u in users:
                if u["UserName"] in sts.get_caller_identity()["Arn"]:
                    owner_arn = u["Arn"]
                    break
    except Exception:
        pass

    if not owner_arn:
        print("❌ Could not detect Quick Sight user. Verify you are a Quick Sight user.")
        return 1

    print(f"  Account:  {account_id}")
    print(f"  Region:   {region}")
    print(f"  Owner:    {owner_arn}")
    print(f"  Topic ID: {topic_id}")
    print(f"  Datasets: {len(dataset_arns)}")
    print()

    # Verify datasets have data
    print("  Checking datasets...")
    all_ready = True
    for key, arn in sorted(dataset_arns.items()):
        ds_id = arn.rsplit("/", 1)[-1]
        try:
            ingestions = qs.list_ingestions(AwsAccountId=account_id, DataSetId=ds_id)["Ingestions"]
            successful = [i for i in ingestions if i["IngestionStatus"] == "COMPLETED" and i.get("RowInfo", {}).get("RowsIngested", 0) > 0]
            if successful:
                rows = successful[0]["RowInfo"]["RowsIngested"]
                print(f"    ✓ {ds_id}: {rows} rows")
            else:
                print(f"    ✗ {ds_id}: no successful ingestion with data")
                all_ready = False
        except Exception as e:
            print(f"    ✗ {ds_id}: {e}")
            all_ready = False

    if not all_ready:
        print()
        print("❌ Not all datasets have data. Generate activity in Amazon Quick first,")
        print("   wait 15-20 minutes for data to flow, then refresh the SPICE datasets")
        print("   in the Amazon Quick console and re-run this script.")
        return 1

    print()

    confirm = input("Proceed? (y/N): ").strip().lower()
    if confirm != "y":
        print("❌ Cancelled")
        return 0
    print()

    # Check if topic already exists
    try:
        qs.describe_topic(AwsAccountId=account_id, TopicId=topic_id)
        print(f"  ✓ Topic already exists: {topic_id}")
        return 0
    except qs.exceptions.ResourceNotFoundException:
        pass

    # Load topic configuration from dashboard_stack.py constants
    # Import the TOPIC_COLUMNS and CUSTOM_INSTRUCTIONS from the CDK module
    sys.path.insert(0, "cdk")
    from dashboard_stack import TOPIC_COLUMNS, CUSTOM_INSTRUCTIONS, DATASET_CONFIGS

    # Build topic payload
    topic_datasets = []
    for config in DATASET_CONFIGS:
        ds_id = f"{resource_prefix}-{config['id_suffix']}"
        ds_arn = f"arn:aws:quicksight:{region}:{account_id}:dataset/{ds_id}"
        columns = TOPIC_COLUMNS.get(config["id_suffix"], [])

        topic_datasets.append({
            "DatasetArn": ds_arn,
            "DatasetName": config["name"],
            "DatasetDescription": config["description"],
            "DataAggregation": {
                "DatasetRowDateGranularity": "DAY",
                "DefaultDateColumnName": "event_time",
            },
            "Columns": columns,
        })

    # Create topic
    print(f"  Creating topic: {topic_id}")
    try:
        qs.create_topic(
            AwsAccountId=account_id,
            TopicId=topic_id,
            Topic={
                "Name": "Quick Observability",
                "Description": (
                    "Unified topic for Quick Suite usage, adoption, "
                    "satisfaction, cost, API activity, and performance metrics"
                ),
                "UserExperienceVersion": "NEW_READER_EXPERIENCE",
                "ConfigOptions": {"QBusinessInsightsEnabled": True},
                "DataSets": topic_datasets,
            },
            CustomInstructions={
                "CustomInstructionsString": CUSTOM_INSTRUCTIONS,
            },
        )
        print(f"  ✓ Topic created: {topic_id}")
    except Exception as e:
        print(f"  ✗ Failed to create topic: {e}")
        return 1

    # Grant permissions to owner
    print(f"  Granting permissions to: {owner_arn}")
    try:
        qs.update_topic_permissions(
            AwsAccountId=account_id,
            TopicId=topic_id,
            GrantPermissions=[{
                "Principal": owner_arn,
                "Actions": [
                    "quicksight:DescribeTopic",
                    "quicksight:DescribeTopicRefresh",
                    "quicksight:ListTopicRefreshSchedules",
                    "quicksight:DescribeTopicRefreshSchedule",
                    "quicksight:DeleteTopic",
                    "quicksight:UpdateTopic",
                    "quicksight:CreateTopicRefreshSchedule",
                    "quicksight:DeleteTopicRefreshSchedule",
                    "quicksight:UpdateTopicRefreshSchedule",
                    "quicksight:DescribeTopicPermissions",
                    "quicksight:UpdateTopicPermissions",
                ],
            }],
        )
        print(f"  ✓ Permissions granted")
    except Exception as e:
        print(f"  ⚠ Could not grant permissions: {e}")

    print()
    print(f"  ✓ Topic ready: arn:aws:quicksight:{region}:{account_id}:topic/{topic_id}")
    print(f"  Open the Amazon Quick console → Topics → Quick Observability")
    return 0


if __name__ == "__main__":
    sys.exit(main())

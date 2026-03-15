#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Amazon Quick Observability Platform - CDK Application

Up to three stacks (conditionally created based on deployment step):
- LogsStack: KMS key, CloudWatch Log Groups, vended logs delivery
- PipelineStack: S3 data lake, Firehose, Lambda, EventBridge
- QuickSightStack: Theme, data source, datasets, analysis, dashboard
"""
import os
import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from logs_stack import LogsStack
from pipeline_stack import PipelineStack
from dashboard_stack import QuickSightStack

app = cdk.App()

account = os.environ.get("CDK_DEFAULT_ACCOUNT")
region = os.environ.get("CDK_DEFAULT_REGION", "us-east-1")
env = cdk.Environment(account=account, region=region)

resource_prefix = app.node.try_get_context("resourcePrefix") or "quickobserve"
logs_stack_name = app.node.try_get_context("logsStackName") or f"{resource_prefix}-logs"
pipeline_stack_name = app.node.try_get_context("pipelineStackName") or f"{resource_prefix}-pipeline"

# Stack 1: Logs (created on every synth)
logs_stack = LogsStack(
    app, logs_stack_name,
    env=env,
    description="Amazon Quick Observability - KMS encryption and CloudWatch Logs delivery",
)

# Stack 2: Pipeline (only created when kmsKeyArn is available)
kms_key_arn = app.node.try_get_context("kmsKeyArn")
if kms_key_arn:
    pipeline_stack = PipelineStack(
        app, pipeline_stack_name,
        env=env,
        description="Amazon Quick Observability - Data pipeline and S3 data lake",
    )

# Stack 3: Quick Sight (only created when quicksightOwnerArn is available)
quicksight_owner_arn = app.node.try_get_context("quicksightOwnerArn")
if quicksight_owner_arn:
    quicksight_stack_name = app.node.try_get_context("quicksightStackName") or f"{resource_prefix}-quicksight"
    quicksight_stack = QuickSightStack(
        app, quicksight_stack_name,
        env=env,
        description="Amazon Quick Observability - Quick Sight datasets, topics, analysis, and dashboard",
    )

# cdk-nag
enable_cdk_nag = app.node.try_get_context("enableCdkNag")
if enable_cdk_nag is None or enable_cdk_nag:
    cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

app.synth()

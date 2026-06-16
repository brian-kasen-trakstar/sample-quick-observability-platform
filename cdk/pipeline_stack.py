# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Amazon Quick Observability Platform - Pipeline CDK Stack
"""
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_kinesisfirehose as firehose,
    aws_logs as logs,
    aws_events as events,
    aws_kms as kms,
    CfnOutput
)
from constructs import Construct
from cdk_nag import NagSuppressions
import os


class PipelineStack(Stack):
    """Main stack for Amazon Quick Observability Platform"""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Get stack context
        account_id = Stack.of(self).account
        region = Stack.of(self).region
        stack_name = Stack.of(self).stack_name
        
        # Naming prefix for all resources
        resource_prefix = stack_name.lower()
        
        # Get CloudWatch log group names from context
        chat_logs_group_name = self.node.try_get_context("chatLogsGroup") or "/aws/vendedlogs/quick/chat"
        feedback_logs_group_name = self.node.try_get_context("feedbackLogsGroup") or "/aws/vendedlogs/quick/feedback"
        agent_hours_logs_group_name = self.node.try_get_context("agentHoursLogsGroup") or "/aws/vendedlogs/quick/agent-hours"
        index_usage_logs_group_name = self.node.try_get_context("indexUsageLogsGroup") or "/aws/vendedlogs/quick/index-usage"

        # Whether to keep message content in the data lake (default: strip it)
        include_message_content = self.node.try_get_context("includeMessageContent") == "true"

        # ====================================================================
        # KMS Encryption Key (from LogsStack)
        # ====================================================================
        
        # Import the KMS key ARN from context (passed by deploy.py from LogsStack outputs)
        kms_key_arn = self.node.try_get_context("kmsKeyArn")
        if not kms_key_arn:
            raise ValueError(
                "kmsKeyArn context parameter is required. "
                "Deploy the logs stack first: python3 deploy.py --logs"
            )
        
        self.data_lake_key = kms.Key.from_key_arn(
            self, "DataLakeKey", kms_key_arn
        )

        # ====================================================================
        # S3 Data Lake
        # ====================================================================
        # Data Lake Bucket - stores actual data (logs, events, metrics)
        # Include account_id for global uniqueness
        self.data_lake_bucket = s3.Bucket(
            self,
            "DataLakeBucket",
            bucket_name=f"{stack_name.lower()}-datalake-{account_id}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.data_lake_key,
            bucket_key_enabled=True,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
            enforce_ssl=True
        )

        # Grant Quick Sight service role read access to the data lake
        self.data_lake_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowQuickSightRead",
                effect=iam.Effect.ALLOW,
                principals=[iam.ArnPrincipal(f"arn:aws:iam::{account_id}:role/service-role/aws-quicksight-service-role-v0")],
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[
                    self.data_lake_bucket.bucket_arn,
                    f"{self.data_lake_bucket.bucket_arn}/*",
                ],
            )
        )
        
        # Suppress AwsSolutions-S1 for Data Lake Bucket (server access logging)
        NagSuppressions.add_resource_suppressions(
            self.data_lake_bucket,
            [
                {
                    "id": "AwsSolutions-S1",
                    "reason": "S3 server access logging not enabled to avoid circular dependency. CloudTrail provides audit trail for S3 API calls."
                }
            ]
        )
        
        # ====================================================================
        # IAM Roles
        # ====================================================================
        
        # Lambda execution role
        lambda_role = iam.Role(
            self,
            "LambdaExecutionRole",
            role_name=f"{stack_name}-Lambda-{region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                # Only basic execution role for CloudWatch Logs write
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Suppress AwsSolutions-IAM4 for AWSLambdaBasicExecutionRole
        NagSuppressions.add_resource_suppressions(
            lambda_role,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "AWSLambdaBasicExecutionRole is AWS managed policy required for Lambda to write CloudWatch Logs. This is a standard and recommended practice."
                }
            ]
        )
        
        # Firehose role
        firehose_role = iam.Role(
            self,
            "FirehoseRole",
            role_name=f"{stack_name}-Firehose-{region}",
            assumed_by=iam.ServicePrincipal("firehose.amazonaws.com")
        )
        
        # Grant S3 write access to specific prefixes (least privilege)
        firehose_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:PutObject",
                    "s3:GetBucketLocation",
                    "s3:ListBucket"
                ],
                resources=[
                    self.data_lake_bucket.bucket_arn,
                    f"{self.data_lake_bucket.bucket_arn}/cloudwatch-logs/*",
                    f"{self.data_lake_bucket.bucket_arn}/cloudtrail/*",
                    f"{self.data_lake_bucket.bucket_arn}/errors/*"
                ]
            )
        )
        
        # Suppress AwsSolutions-IAM5 for Firehose S3 prefix wildcards on the DefaultPolicy
        NagSuppressions.add_resource_suppressions_by_path(
            self,
            f"/{self.stack_name}/FirehoseRole/DefaultPolicy/Resource",
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "S3 permissions use wildcards for prefix-based access (cloudwatch-logs/*, cloudtrail/*, errors/*). This is required for Firehose dynamic partitioning and error handling, scoped to specific prefixes.",
                    "appliesTo": [
                        "Resource::<DataLakeBucket0256EA8E.Arn>/cloudwatch-logs/*",
                        "Resource::<DataLakeBucket0256EA8E.Arn>/cloudtrail/*",
                        "Resource::<DataLakeBucket0256EA8E.Arn>/errors/*"
                    ]
                }
            ]
        )

        # CloudWatch Logs role
        cloudwatch_logs_role = iam.Role(
            self,
            "CloudWatchLogsRole",
            role_name=f"{stack_name}-CloudWatchLogs-{region}",
            assumed_by=iam.ServicePrincipal("logs.amazonaws.com")
        )

        # CloudWatch Logs needs KMS permissions when writing test records to
        # CMK-encrypted Firehose streams during SubscriptionFilter creation.
        cloudwatch_logs_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kms:Decrypt",
                    "kms:Encrypt",
                    "kms:GenerateDataKey",
                    "kms:DescribeKey",
                ],
                resources=[self.data_lake_key.key_arn]
            )
        )

        # Grant KMS permissions via IAM policy (the key policy in LogsStack
        # grants account root access, so IAM policies are sufficient)
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kms:Decrypt",
                    "kms:GenerateDataKey"
                ],
                resources=[self.data_lake_key.key_arn]
            )
        )
        firehose_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kms:Decrypt",
                    "kms:Encrypt",
                    "kms:GenerateDataKey"
                ],
                resources=[self.data_lake_key.key_arn]
            )
        )

        # ====================================================================
        # Lambda Functions
        # ====================================================================
        
        # Get the parent directory (project root) for Lambda code
        lambda_base_path = os.path.join(os.path.dirname(__file__), '..')
        
        # Log transform function
        log_transform_function = lambda_.Function(
            self,
            "LogTransformFunction",
            function_name=f"{stack_name}-LogTransform",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(os.path.join(lambda_base_path, "lambda/log_transform")),
            role=lambda_role,
            timeout=Duration.seconds(300),
            memory_size=512,
            environment_encryption=self.data_lake_key,
            environment={
                "INCLUDE_MESSAGE_CONTENT": "true" if include_message_content else "false"
            }
        )
        
        # Suppress AwsSolutions-L1 for Lambda runtime
        NagSuppressions.add_resource_suppressions(
            log_transform_function,
            [
                {
                    "id": "AwsSolutions-L1",
                    "reason": "Lambda function uses Python 3.14 which is the latest available runtime."
                }
            ]
        )


        # CloudTrail transform function
        cloudtrail_transform_function = lambda_.Function(
            self,
            "CloudTrailTransformFunction",
            function_name=f"{stack_name}-CloudTrailTransform",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(os.path.join(lambda_base_path, "lambda/cloudtrail_transform")),
            role=lambda_role,
            timeout=Duration.seconds(300),
            memory_size=512,
            environment_encryption=self.data_lake_key,
        )
        
        # Suppress AwsSolutions-L1 for Lambda runtime
        NagSuppressions.add_resource_suppressions(
            cloudtrail_transform_function,
            [
                {
                    "id": "AwsSolutions-L1",
                    "reason": "Lambda function uses Python 3.14 which is the latest available runtime."
                }
            ]
        )

        # Grant Lambda invoke permission to Firehose
        log_transform_function.grant_invoke(firehose_role)
        cloudtrail_transform_function.grant_invoke(firehose_role)
        
        # Suppress AwsSolutions-IAM5 for Lambda invoke permissions with version wildcards on the DefaultPolicy
        NagSuppressions.add_resource_suppressions_by_path(
            self,
            f"/{self.stack_name}/FirehoseRole/DefaultPolicy/Resource",
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Lambda invoke permissions include version wildcard (:*) which is standard CDK pattern for Lambda invocation to support versioning and aliases.",
                    "appliesTo": [
                        "Resource::<CloudTrailTransformFunction25D8E2FF.Arn>:*",
                        "Resource::<LogTransformFunction87C3CC0C.Arn>:*"
                    ]
                }
            ]
        )

        # ====================================================================
        # Kinesis Firehose Delivery Streams
        # ====================================================================
        
        # Helper function to create Firehose stream
        def create_firehose_stream(
            stream_id: str,
            stream_name: str,
            s3_prefix: str,
            transform_function: lambda_.Function
        ):
            stream = firehose.CfnDeliveryStream(
                self,
                stream_id,
                delivery_stream_name=stream_name,
                delivery_stream_type="DirectPut",
                delivery_stream_encryption_configuration_input=firehose.CfnDeliveryStream.DeliveryStreamEncryptionConfigurationInputProperty(
                    key_type="CUSTOMER_MANAGED_CMK",
                    key_arn=self.data_lake_key.key_arn
                ),
                extended_s3_destination_configuration=firehose.CfnDeliveryStream.ExtendedS3DestinationConfigurationProperty(
                    bucket_arn=self.data_lake_bucket.bucket_arn,
                    role_arn=firehose_role.role_arn,
                    prefix=s3_prefix,
                    error_output_prefix=f"errors/{s3_prefix.split('/')[0]}/",
                    buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                        size_in_m_bs=128,
                        interval_in_seconds=900
                    ),
                    compression_format="GZIP",
                    processing_configuration=firehose.CfnDeliveryStream.ProcessingConfigurationProperty(
                        enabled=True,
                        processors=[
                            firehose.CfnDeliveryStream.ProcessorProperty(
                                type="Lambda",
                                parameters=[
                                    firehose.CfnDeliveryStream.ProcessorParameterProperty(
                                        parameter_name="LambdaArn",
                                        parameter_value=transform_function.function_arn
                                    )
                                ]
                            )
                        ]
                    )
                )
            )
            
            return stream

        # Create Firehose streams
        chat_logs_firehose = create_firehose_stream(
            "ChatLogsFirehose",
            f"{resource_prefix}-chat-logs",
            "cloudwatch-logs/chat/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/",
            log_transform_function
        )

        feedback_logs_firehose = create_firehose_stream(
            "FeedbackLogsFirehose",
            f"{resource_prefix}-feedback-logs",
            "cloudwatch-logs/feedback/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/",
            log_transform_function
        )

        agent_hours_firehose = create_firehose_stream(
            "AgentHoursFirehose",
            f"{resource_prefix}-agent-hours",
            "cloudwatch-logs/agent-hours/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/",
            log_transform_function
        )

        index_usage_firehose = create_firehose_stream(
            "IndexUsageFirehose",
            f"{resource_prefix}-index-usage",
            "cloudwatch-logs/index-usage/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/",
            log_transform_function
        )

        # CloudTrail events Firehose stream
        cloudtrail_firehose = create_firehose_stream(
            "CloudTrailFirehose",
            f"{resource_prefix}-cloudtrail-events",
            "cloudtrail/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/",
            cloudtrail_transform_function
        )

        # Grant Firehose write permissions to CloudWatch Logs role
        cloudwatch_logs_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "firehose:PutRecord",
                    "firehose:PutRecordBatch"
                ],
                resources=[
                    chat_logs_firehose.attr_arn,
                    feedback_logs_firehose.attr_arn,
                    agent_hours_firehose.attr_arn,
                    index_usage_firehose.attr_arn
                ]
            )
        )

        # ====================================================================
        # EventBridge Rule for CloudTrail Events
        # ====================================================================
        
        # EventBridge role for CloudTrail events
        eventbridge_role = iam.Role(
            self,
            "EventBridgeRole",
            role_name=f"{stack_name}-EventBridge-{region}",
            assumed_by=iam.ServicePrincipal("events.amazonaws.com")
        )
        
        # Grant Firehose write permissions to EventBridge role
        eventbridge_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "firehose:PutRecord",
                    "firehose:PutRecordBatch"
                ],
                resources=[cloudtrail_firehose.attr_arn]
            )
        )
        
        # EventBridge rule to capture Quick Sight CloudTrail events
        cfn_rule = events.CfnRule(
            self,
            "CloudTrailRuleCfn",
            name=f"{stack_name}-CloudTrailEvents",
            description="Capture Quick Sight API calls and service events from CloudTrail",
            event_pattern={
                "source": ["aws.quicksight"],
                "detail-type": [
                    "AWS API Call via CloudTrail",
                    "AWS Service Event via CloudTrail"
                ]
            },
            state="ENABLED",
            targets=[
                events.CfnRule.TargetProperty(
                    arn=cloudtrail_firehose.attr_arn,
                    id="CloudTrailFirehoseTarget",
                    role_arn=eventbridge_role.role_arn
                )
            ]
        )

        # ====================================================================
        # CloudWatch Logs Subscription Filters
        # ====================================================================
        
        # Subscription filters route logs from CloudWatch Log Groups
        # (created by LogsStack) to Firehose delivery streams.
        # Log groups are referenced by name, not created here.
        
        # Chat logs subscription filter
        chat_logs_subscription = logs.CfnSubscriptionFilter(
            self,
            "ChatLogsSubscriptionFilter",
            log_group_name=chat_logs_group_name,
            filter_pattern="",
            destination_arn=chat_logs_firehose.attr_arn,
            role_arn=cloudwatch_logs_role.role_arn
        )
        chat_logs_subscription.add_dependency(chat_logs_firehose)
        chat_logs_subscription.node.add_dependency(cloudwatch_logs_role)

        # Feedback logs subscription filter
        feedback_logs_subscription = logs.CfnSubscriptionFilter(
            self,
            "FeedbackLogsSubscriptionFilter",
            log_group_name=feedback_logs_group_name,
            filter_pattern="",
            destination_arn=feedback_logs_firehose.attr_arn,
            role_arn=cloudwatch_logs_role.role_arn
        )
        feedback_logs_subscription.add_dependency(feedback_logs_firehose)
        feedback_logs_subscription.node.add_dependency(cloudwatch_logs_role)

        # Agent hours logs subscription filter
        agent_hours_subscription = logs.CfnSubscriptionFilter(
            self,
            "AgentHoursSubscriptionFilter",
            log_group_name=agent_hours_logs_group_name,
            filter_pattern="",
            destination_arn=agent_hours_firehose.attr_arn,
            role_arn=cloudwatch_logs_role.role_arn
        )
        agent_hours_subscription.add_dependency(agent_hours_firehose)
        agent_hours_subscription.node.add_dependency(cloudwatch_logs_role)

        # Index usage logs subscription filter
        index_usage_subscription = logs.CfnSubscriptionFilter(
            self,
            "IndexUsageSubscriptionFilter",
            log_group_name=index_usage_logs_group_name,
            filter_pattern="",
            destination_arn=index_usage_firehose.attr_arn,
            role_arn=cloudwatch_logs_role.role_arn
        )
        index_usage_subscription.add_dependency(index_usage_firehose)
        index_usage_subscription.node.add_dependency(cloudwatch_logs_role)

        # ====================================================================
        # Outputs
        # ====================================================================
        
        CfnOutput(
            self,
            "DataLakeBucketName",
            value=self.data_lake_bucket.bucket_name,
            description="S3 bucket for data lake",
            export_name=f"{Stack.of(self).stack_name}-DataLakeBucket"
        )

        CfnOutput(
            self,
            "DataLakeKmsKeyArn",
            value=self.data_lake_key.key_arn,
            description="KMS key ARN for data lake encryption"
        )

        CfnOutput(
            self,
            "ChatLogsFirehoseArn",
            value=chat_logs_firehose.attr_arn,
            description="Chat logs Firehose delivery stream ARN"
        )

        CfnOutput(
            self,
            "FeedbackLogsFirehoseArn",
            value=feedback_logs_firehose.attr_arn,
            description="Feedback logs Firehose delivery stream ARN"
        )

        CfnOutput(
            self,
            "AgentHoursFirehoseArn",
            value=agent_hours_firehose.attr_arn,
            description="Agent hours Firehose delivery stream ARN"
        )

        CfnOutput(
            self,
            "IndexUsageFirehoseArn",
            value=index_usage_firehose.attr_arn,
            description="Index usage Firehose delivery stream ARN"
        )
        
        # CloudTrail and Metrics pipeline outputs
        CfnOutput(
            self,
            "CloudTrailFirehoseArn",
            value=cloudtrail_firehose.attr_arn,
            description="CloudTrail events Firehose delivery stream ARN"
        )

        CfnOutput(
            self,
            "CloudTrailRuleName",
            value=cfn_rule.name,
            description="EventBridge rule name for CloudTrail events"
        )

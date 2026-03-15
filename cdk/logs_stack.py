# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Amazon Quick Observability Platform - Logs Stack

Creates:
- Customer-managed KMS key (with automatic rotation)
- CloudWatch Log Groups (chat, feedback, agent hours) - KMS encrypted
- Vended logs delivery configuration (sources, destinations, deliveries)
"""
from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_kms as kms,
    aws_logs as logs,
    aws_iam as iam,
)
from constructs import Construct


class LogsStack(Stack):
    """Stack for KMS encryption and CloudWatch Logs delivery."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account_id = Stack.of(self).account
        region = Stack.of(self).region
        stack_name = Stack.of(self).stack_name
        resource_prefix = self.node.try_get_context("resourcePrefix") or "quickobserve"

        # Log group names from context or defaults
        chat_logs_group_name = self.node.try_get_context("chatLogsGroup") or "/aws/vendedlogs/quick/chat"
        feedback_logs_group_name = self.node.try_get_context("feedbackLogsGroup") or "/aws/vendedlogs/quick/feedback"
        agent_hours_logs_group_name = self.node.try_get_context("agentHoursLogsGroup") or "/aws/vendedlogs/quick/agent-hours"

        # Whether to include user_message and system_text_message in chat logs.
        # Default is false — message content is excluded for enterprise environments
        # because it may contain data from connected enterprise sources.
        include_message_content = self.node.try_get_context("includeMessageContent") == "true"

        # ====================================================================
        # KMS Key
        # ====================================================================
        self.kms_key = kms.Key(
            self, "ObservabilityKey",
            alias=f"alias/{resource_prefix}-observability",
            description="Amazon Quick Observability Platform encryption key",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Allow delivery.logs.amazonaws.com to use the key for vended logs delivery
        self.kms_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudWatchLogsDelivery",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("delivery.logs.amazonaws.com")],
                actions=["kms:GenerateDataKey", "kms:Decrypt"],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "kms:EncryptionContext:SourceArn": f"arn:aws:logs:{region}:{account_id}:*"
                    }
                },
            )
        )

        # Allow logs.amazonaws.com to use the key for CloudWatch Log Group encryption
        self.kms_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudWatchLogsEncryption",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal(f"logs.{region}.amazonaws.com")],
                actions=[
                    "kms:Encrypt",
                    "kms:Decrypt",
                    "kms:ReEncrypt*",
                    "kms:GenerateDataKey*",
                    "kms:DescribeKey",
                ],
                resources=["*"],
                conditions={
                    "ArnLike": {
                        "kms:EncryptionContext:aws:logs:arn": f"arn:aws:logs:{region}:{account_id}:*"
                    }
                },
            )
        )

        # Allow Quick Sight service role to decrypt data lake objects
        self.kms_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowQuickSightDecrypt",
                effect=iam.Effect.ALLOW,
                principals=[iam.ArnPrincipal(f"arn:aws:iam::{account_id}:role/service-role/aws-quicksight-service-role-v0")],
                actions=[
                    "kms:Decrypt",
                    "kms:DescribeKey",
                ],
                resources=["*"],
            )
        )

        # ====================================================================
        # CloudWatch Log Groups
        # ====================================================================

        # Data protection policy for chat logs — masks sensitive data patterns
        # (PII, credentials, financial, PHI, device identifiers) at the
        # CloudWatch layer using all available managed data identifiers.
        # This is defense-in-depth: the Lambda transform also strips message
        # content before it reaches S3.
        all_data_identifiers = [
            # Credentials
            logs.DataIdentifier.AWSSECRETKEY,
            logs.DataIdentifier.OPENSSHPRIVATEKEY,
            logs.DataIdentifier.PGPPRIVATEKEY,
            logs.DataIdentifier.PKCSPRIVATEKEY,
            logs.DataIdentifier.PUTTYPRIVATEKEY,
            # Financial
            logs.DataIdentifier.BANKACCOUNTNUMBER_DE,
            logs.DataIdentifier.BANKACCOUNTNUMBER_ES,
            logs.DataIdentifier.BANKACCOUNTNUMBER_FR,
            logs.DataIdentifier.BANKACCOUNTNUMBER_GB,
            logs.DataIdentifier.BANKACCOUNTNUMBER_IT,
            logs.DataIdentifier.BANKACCOUNTNUMBER_US,
            logs.DataIdentifier.CREDITCARDEXPIRATION,
            logs.DataIdentifier.CREDITCARDNUMBER,
            logs.DataIdentifier.CREDITCARDSECURITYCODE,
            # PII — General
            logs.DataIdentifier.ADDRESS,
            logs.DataIdentifier.EMAILADDRESS,
            logs.DataIdentifier.LATLONG,
            logs.DataIdentifier.NAME,
            logs.DataIdentifier.VEHICLEIDENTIFICATIONNUMBER,
            # PII — National / Government IDs
            logs.DataIdentifier.CEPCODE_BR,
            logs.DataIdentifier.CNPJ_BR,
            logs.DataIdentifier.CPFCODE_BR,
            logs.DataIdentifier.DRIVERSLICENSE_AT,
            logs.DataIdentifier.DRIVERSLICENSE_AU,
            logs.DataIdentifier.DRIVERSLICENSE_BE,
            logs.DataIdentifier.DRIVERSLICENSE_BG,
            logs.DataIdentifier.DRIVERSLICENSE_CA,
            logs.DataIdentifier.DRIVERSLICENSE_CY,
            logs.DataIdentifier.DRIVERSLICENSE_CZ,
            logs.DataIdentifier.DRIVERSLICENSE_DE,
            logs.DataIdentifier.DRIVERSLICENSE_DK,
            logs.DataIdentifier.DRIVERSLICENSE_EE,
            logs.DataIdentifier.DRIVERSLICENSE_ES,
            logs.DataIdentifier.DRIVERSLICENSE_FI,
            logs.DataIdentifier.DRIVERSLICENSE_FR,
            logs.DataIdentifier.DRIVERSLICENSE_GB,
            logs.DataIdentifier.DRIVERSLICENSE_GR,
            logs.DataIdentifier.DRIVERSLICENSE_HR,
            logs.DataIdentifier.DRIVERSLICENSE_HU,
            logs.DataIdentifier.DRIVERSLICENSE_IE,
            logs.DataIdentifier.DRIVERSLICENSE_IT,
            logs.DataIdentifier.DRIVERSLICENSE_LT,
            logs.DataIdentifier.DRIVERSLICENSE_LU,
            logs.DataIdentifier.DRIVERSLICENSE_LV,
            logs.DataIdentifier.DRIVERSLICENSE_MT,
            logs.DataIdentifier.DRIVERSLICENSE_NL,
            logs.DataIdentifier.DRIVERSLICENSE_PL,
            logs.DataIdentifier.DRIVERSLICENSE_PT,
            logs.DataIdentifier.DRIVERSLICENSE_RO,
            logs.DataIdentifier.DRIVERSLICENSE_SE,
            logs.DataIdentifier.DRIVERSLICENSE_SI,
            logs.DataIdentifier.DRIVERSLICENSE_SK,
            logs.DataIdentifier.DRIVERSLICENSE_US,
            logs.DataIdentifier.ELECTORALROLLNUMBER_GB,
            logs.DataIdentifier.INDIVIDUALTAXIDENTIFICATIONNUMBER_US,
            logs.DataIdentifier.INSEECODE_FR,
            logs.DataIdentifier.NATIONALIDENTIFICATIONNUMBER_DE,
            logs.DataIdentifier.NATIONALIDENTIFICATIONNUMBER_ES,
            logs.DataIdentifier.NATIONALIDENTIFICATIONNUMBER_IT,
            logs.DataIdentifier.NATIONALINSURANCENUMBER_GB,
            logs.DataIdentifier.NIENUMBER_ES,
            logs.DataIdentifier.NIFNUMBER_ES,
            logs.DataIdentifier.PASSPORTNUMBER_CA,
            logs.DataIdentifier.PASSPORTNUMBER_DE,
            logs.DataIdentifier.PASSPORTNUMBER_ES,
            logs.DataIdentifier.PASSPORTNUMBER_FR,
            logs.DataIdentifier.PASSPORTNUMBER_GB,
            logs.DataIdentifier.PASSPORTNUMBER_IT,
            logs.DataIdentifier.PASSPORTNUMBER_US,
            logs.DataIdentifier.PERMANENTRESIDENCENUMBER_CA,
            logs.DataIdentifier.RGNUMBER_BR,
            logs.DataIdentifier.SSN_ES,
            logs.DataIdentifier.SSN_US,
            logs.DataIdentifier.TAXID_DE,
            logs.DataIdentifier.TAXID_ES,
            logs.DataIdentifier.TAXID_FR,
            logs.DataIdentifier.TAXID_GB,
            # PII — Phone numbers
            logs.DataIdentifier.PHONENUMBER_BR,
            logs.DataIdentifier.PHONENUMBER_DE,
            logs.DataIdentifier.PHONENUMBER_ES,
            logs.DataIdentifier.PHONENUMBER_FR,
            logs.DataIdentifier.PHONENUMBER_GB,
            logs.DataIdentifier.PHONENUMBER_IT,
            logs.DataIdentifier.PHONENUMBER_US,
            # PII — Postal codes
            logs.DataIdentifier.POSTALCODE_CA,
            logs.DataIdentifier.ZIPCODE_US,
            # PHI — Protected Health Information
            logs.DataIdentifier.DRUGENFORCEMENTAGENCYNUMBER_US,
            logs.DataIdentifier.HEALTHCAREPROCEDURECODE_US,
            logs.DataIdentifier.HEALTHINSURANCECARDNUMBER_EU,
            logs.DataIdentifier.HEALTHINSURANCECLAIMNUMBER_US,
            logs.DataIdentifier.HEALTHINSURANCENUMBER_FR,
            logs.DataIdentifier.MEDICAREBENEFICIARYNUMBER_US,
            logs.DataIdentifier.NATIONALDRUGCODE_US,
            logs.DataIdentifier.NATIONALPROVIDERID_US,
            logs.DataIdentifier.NHSNUMBER_GB,
            logs.DataIdentifier.PERSONALHEALTHNUMBER_CA,
            # Device identifiers
            logs.DataIdentifier.IPADDRESS,
        ]

        chat_data_protection = logs.DataProtectionPolicy(
            name="chat-logs-data-protection",
            description="Mask sensitive data in Quick chat logs",
            identifiers=all_data_identifiers,
        )

        self.chat_log_group = logs.LogGroup(
            self, "ChatLogGroup",
            log_group_name=chat_logs_group_name,
            encryption_key=self.kms_key,
            removal_policy=RemovalPolicy.DESTROY,
            data_protection_policy=chat_data_protection,
        )

        self.feedback_log_group = logs.LogGroup(
            self, "FeedbackLogGroup",
            log_group_name=feedback_logs_group_name,
            encryption_key=self.kms_key,
            removal_policy=RemovalPolicy.DESTROY,
            data_protection_policy=logs.DataProtectionPolicy(
                name="feedback-logs-data-protection",
                description="Mask sensitive data in Quick feedback logs",
                identifiers=all_data_identifiers,
            ),
        )

        self.agent_hours_log_group = logs.LogGroup(
            self, "AgentHoursLogGroup",
            log_group_name=agent_hours_logs_group_name,
            encryption_key=self.kms_key,
            removal_policy=RemovalPolicy.DESTROY,
            data_protection_policy=logs.DataProtectionPolicy(
                name="agent-hours-logs-data-protection",
                description="Mask sensitive data in Quick agent hours logs",
                identifiers=all_data_identifiers,
            ),
        )

        # ====================================================================
        # Vended Logs Delivery Configuration
        # ====================================================================
        quicksight_arn = f"arn:aws:quicksight:{region}:{account_id}:account/{account_id}"

        # Chat logs delivery
        chat_source = logs.CfnDeliverySource(
            self, "ChatDeliverySource",
            name=f"{resource_prefix}-chat-logs",
            log_type="CHAT_LOGS",
            resource_arn=quicksight_arn,
        )

        chat_dest = logs.CfnDeliveryDestination(
            self, "ChatDeliveryDestination",
            name=f"{resource_prefix}-chat-destination",
            output_format="json",
            destination_resource_arn=self.chat_log_group.log_group_arn,
        )
        chat_dest.add_dependency(self.chat_log_group.node.default_child)

        chat_delivery = logs.CfnDelivery(
            self, "ChatDelivery",
            delivery_source_name=chat_source.name,
            delivery_destination_arn=chat_dest.attr_arn,
            record_fields=[
                "user_arn", "user_type", "status_code", "conversation_id",
                "system_message_id", "user_message_id",
                "agent_id", "flow_id", "message_scope",
                "user_selected_resources", "action_connectors", "cited_resource",
                "file_attachment", "resource_arn", "event_timestamp", "logType",
                "accountId", "namespace", "latency", "time_to_first_token",
                "surface_type", "web_search",
            ] + (["user_message", "system_text_message"] if include_message_content else []),
        )
        chat_delivery.add_dependency(chat_source)
        chat_delivery.add_dependency(chat_dest)

        # Feedback logs delivery
        feedback_source = logs.CfnDeliverySource(
            self, "FeedbackDeliverySource",
            name=f"{resource_prefix}-feedback-logs",
            log_type="FEEDBACK_LOGS",
            resource_arn=quicksight_arn,
        )

        feedback_dest = logs.CfnDeliveryDestination(
            self, "FeedbackDeliveryDestination",
            name=f"{resource_prefix}-feedback-destination",
            output_format="json",
            destination_resource_arn=self.feedback_log_group.log_group_arn,
        )
        feedback_dest.add_dependency(self.feedback_log_group.node.default_child)

        feedback_delivery = logs.CfnDelivery(
            self, "FeedbackDelivery",
            delivery_source_name=feedback_source.name,
            delivery_destination_arn=feedback_dest.attr_arn,
            record_fields=[
                "user_arn", "user_type", "status_code", "conversation_id",
                "system_message_id", "user_message_id", "research_id",
                "feedback_type", "feedback_reason", "feedback_details",
                "rating", "resource_arn", "event_timestamp", "logType",
                "accountId", "namespace",
            ],
        )
        feedback_delivery.add_dependency(feedback_source)
        feedback_delivery.add_dependency(feedback_dest)

        # Agent hours logs delivery
        agent_hours_source = logs.CfnDeliverySource(
            self, "AgentHoursDeliverySource",
            name=f"{resource_prefix}-agent-hours-logs",
            log_type="AGENT_HOURS_LOGS",
            resource_arn=quicksight_arn,
        )

        agent_hours_dest = logs.CfnDeliveryDestination(
            self, "AgentHoursDeliveryDestination",
            name=f"{resource_prefix}-agent-hours-destination",
            output_format="json",
            destination_resource_arn=self.agent_hours_log_group.log_group_arn,
        )
        agent_hours_dest.add_dependency(self.agent_hours_log_group.node.default_child)

        agent_hours_delivery = logs.CfnDelivery(
            self, "AgentHoursDelivery",
            delivery_source_name=agent_hours_source.name,
            delivery_destination_arn=agent_hours_dest.attr_arn,
            record_fields=[
                "user_arn", "subscription_type", "reporting_service",
                "usage_group", "usage_hours", "service_resource_arn",
                "resource_arn", "event_timestamp", "logType", "accountId",
            ],
        )
        agent_hours_delivery.add_dependency(agent_hours_source)
        agent_hours_delivery.add_dependency(agent_hours_dest)

        # ====================================================================
        # Outputs
        # ====================================================================
        CfnOutput(self, "KmsKeyArn", value=self.kms_key.key_arn,
                  description="KMS key ARN for data encryption")
        CfnOutput(self, "ChatLogsGroup", value=chat_logs_group_name,
                  description="Chat logs CloudWatch Log Group name")
        CfnOutput(self, "FeedbackLogsGroup", value=feedback_logs_group_name,
                  description="Feedback logs CloudWatch Log Group name")
        CfnOutput(self, "AgentHoursLogsGroup", value=agent_hours_logs_group_name,
                  description="Agent hours logs CloudWatch Log Group name")
        CfnOutput(self, "IncludeMessageContent",
                  value="true" if include_message_content else "false",
                  description="Whether chat message content is included in logs")

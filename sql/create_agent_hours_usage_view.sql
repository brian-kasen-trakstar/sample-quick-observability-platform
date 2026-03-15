-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
-- SPDX-License-Identifier: MIT-0
-- Agent Hours Usage view
-- Matches the Quick Sight Agent Hours Usage dataset Custom SQL
-- Shows hours consumption by service with resource type parsed from ARN
CREATE OR REPLACE VIEW ${DATABASE}.agent_hours_usage AS
SELECT
    CAST(from_iso8601_timestamp(timestamp) AS timestamp) AS event_time,
    user_arn AS user_id,
    CASE WHEN user_arn LIKE '%/%' THEN ELEMENT_AT(SPLIT(user_arn, '/'), -1) ELSE user_arn END AS user_name,
    subscription_type,
    reporting_service AS service,
    usage_group,
    usage_hours AS hours,
    service_resource_arn,
    CASE
        WHEN reporting_service = 'AUTOMATION' THEN 'Automation'
        WHEN reporting_service = 'FLOW' THEN 'Flow'
        WHEN reporting_service = 'RESEARCH' THEN 'Research'
        ELSE reporting_service
    END AS resource_type,
    CASE
        WHEN service_resource_arn LIKE '%/%'
            THEN ELEMENT_AT(SPLIT(service_resource_arn, '/'), -1)
        ELSE service_resource_arn
    END AS resource_id,
    account_id,
    year, month, day
FROM ${DATABASE}.agent_hours_logs
WHERE timestamp IS NOT NULL;

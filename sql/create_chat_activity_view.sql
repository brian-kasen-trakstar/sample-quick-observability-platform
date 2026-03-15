-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
-- SPDX-License-Identifier: MIT-0
-- Chat Activity view
-- Matches the Quick Sight Chat Activity dataset Custom SQL
-- Shows user conversations with feature classification, latency, and surface type
CREATE OR REPLACE VIEW ${DATABASE}.chat_activity AS
SELECT
    CAST(from_iso8601_timestamp(timestamp) AS timestamp) AS event_time,
    user_arn AS user_id,
    CASE WHEN user_arn LIKE '%/%' THEN ELEMENT_AT(SPLIT(user_arn, '/'), -1) ELSE user_arn END AS user_name,
    status_code AS status,
    conversation_id,
    agent_id,
    flow_id,
    action_connectors,
    namespace,
    CASE
        WHEN flow_id IS NOT NULL AND flow_id != '' AND flow_id != '-' THEN 'Flow'
        WHEN agent_id = 'SYSTEM' THEN 'System Chat Agent (My Assistant)'
        WHEN agent_id IS NOT NULL AND agent_id != '' AND agent_id != '-' THEN 'Custom Chat Agent'
        ELSE ''
    END AS feature,
    CAST(latency AS DOUBLE) AS latency_ms,
    CAST(time_to_first_token AS DOUBLE) AS time_to_first_token_ms,
    surface_type,
    web_search,
    message_scope,
    account_id,
    year, month, day
FROM ${DATABASE}.chat_logs
WHERE timestamp IS NOT NULL;

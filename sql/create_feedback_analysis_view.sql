-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
-- SPDX-License-Identifier: MIT-0
-- Feedback Analysis view
-- Shows user feedback with satisfaction ratings, reasons, and Research vs Chat source
-- Note: this view aliases feedback_type as 'satisfaction', while the Quick Sight
-- dataset SQL in dashboard_stack.py keeps the original column name 'feedback_type'.
CREATE OR REPLACE VIEW ${DATABASE}.feedback_analysis AS
SELECT
    CAST(from_iso8601_timestamp(timestamp) AS timestamp) AS event_time,
    user_arn AS user_id,
    CASE WHEN user_arn LIKE '%/%' THEN ELEMENT_AT(SPLIT(user_arn, '/'), -1) ELSE user_arn END AS user_name,
    status_code AS status,
    conversation_id,
    research_id,
    CASE
        WHEN research_id IS NOT NULL AND research_id != '' AND research_id != '-' THEN 'Research'
        ELSE 'Chat'
    END AS feedback_source,
    feedback_type AS satisfaction,
    feedback_reason AS reason,
    feedback_details AS details,
    rating,
    namespace,
    account_id,
    year, month, day
FROM ${DATABASE}.feedback_logs
WHERE timestamp IS NOT NULL;

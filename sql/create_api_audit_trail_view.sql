-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
-- SPDX-License-Identifier: MIT-0
-- API Audit Trail view
-- Shows Quick Sight API calls with feature categorization
-- Note: the CASE logic here differs from the Quick Sight dataset SQL in
-- dashboard_stack.py — the view has additional categories (Chat, Integration,
-- User & Access, etc.) and uses 'Other' as the fallback instead of the raw event name.
CREATE OR REPLACE VIEW ${DATABASE}.api_audit_trail AS
SELECT
    CAST(from_iso8601_timestamp(timestamp) AS timestamp) AS event_time,
    event_id,
    event_name AS api_action,
    event_source,
    event_type,
    user_name,
    user_type,
    source_ip,
    user_agent,
    aws_region,
    CASE WHEN read_only = true THEN 'Read' ELSE 'Write' END AS read_or_write,
    error_code,
    error_message,
    resource_type,
    resource_arn,
    request_parameters,
    recipient_account_id,
    shared_event_id,
    CASE
        WHEN event_name LIKE '%Automation%' OR event_name LIKE '%Deployment%' THEN 'Automation'
        WHEN event_name LIKE '%ActionConnector%' OR event_name LIKE '%PassAction%' THEN 'Connector'
        WHEN event_name LIKE '%Workflow%' THEN 'Workflow'
        WHEN event_name LIKE '%Research%' THEN 'Research'
        WHEN event_name LIKE '%Assistant%' OR event_name LIKE '%Chat%' OR event_name LIKE '%Conversation%' THEN 'Chat'
        WHEN event_name LIKE '%Integration%' THEN 'Integration'
        WHEN event_name LIKE '%Dashboard%' THEN 'Dashboard'
        WHEN event_name LIKE '%Analysis%' OR event_name LIKE '%Analys%' THEN 'Analysis'
        WHEN event_name LIKE '%DataSet%' OR event_name LIKE '%Dataset%' THEN 'Dataset'
        WHEN event_name LIKE '%DataSource%' THEN 'Data Source'
        WHEN event_name LIKE '%Ingestion%' OR event_name LIKE '%Refresh%' THEN 'Ingestion'
        WHEN event_name LIKE '%Topic%' THEN 'Q Topic'
        WHEN event_name LIKE '%Space%' OR event_name LIKE '%Namespace%' THEN 'Space'
        WHEN event_name LIKE '%KnowledgeBase%' OR event_name LIKE '%Knowledge%' THEN 'Knowledge Base'
        WHEN event_name LIKE '%Flow%' THEN 'Flow'
        WHEN event_name LIKE '%Folder%' THEN 'Folder'
        WHEN event_name LIKE '%Template%' THEN 'Template'
        WHEN event_name LIKE '%Theme%' THEN 'Theme'
        WHEN event_name LIKE '%User%' OR event_name LIKE '%Group%' OR event_name LIKE '%Membership%' THEN 'User & Access'
        WHEN event_name LIKE '%Embed%' THEN 'Embedding'
        WHEN event_name LIKE '%Account%' OR event_name LIKE '%Setting%' THEN 'Account Settings'
        WHEN event_name LIKE '%Authorization%' THEN 'Authorization'
        WHEN event_name LIKE '%Search%' THEN 'Search'
        ELSE 'Other'
    END AS api_feature,
    account_id,
    year, month, day
FROM ${DATABASE}.cloudtrail_events
WHERE timestamp IS NOT NULL;

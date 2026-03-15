-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
-- SPDX-License-Identifier: MIT-0
-- Query Database Events view
-- Extracts key fields from service_event_details for QueryDatabase events
-- Shows which dashboards, data sources, and datasets generate the most database queries
CREATE OR REPLACE VIEW ${DATABASE}.querydatabase_events AS
SELECT
    CAST(from_iso8601_timestamp(timestamp) AS timestamp) AS event_time,
    event_id,
    user_name,
    user_type,
    aws_region,
    error_code,
    JSON_EXTRACT_SCALAR(service_event_details, '$.eventRequestDetails.dataSourceId') AS datasource_id,
    JSON_EXTRACT_SCALAR(service_event_details, '$.eventRequestDetails.queryId') AS query_id,
    JSON_EXTRACT_SCALAR(service_event_details, '$.eventRequestDetails.resourceId') AS dashboard_or_analysis_id,
    JSON_EXTRACT_SCALAR(service_event_details, '$.eventRequestDetails.dataSetId') AS dataset_id,
    JSON_EXTRACT_SCALAR(service_event_details, '$.eventRequestDetails.dataSetMode') AS dataset_mode,
    account_id,
    year, month, day
FROM ${DATABASE}.cloudtrail_events
WHERE event_name = 'QueryDatabase'
  AND event_source = 'quicksight.amazonaws.com'
  AND timestamp IS NOT NULL;

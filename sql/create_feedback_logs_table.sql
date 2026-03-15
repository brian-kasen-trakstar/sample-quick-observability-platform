-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
-- SPDX-License-Identifier: MIT-0
CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.feedback_logs (
  timestamp STRING,
  log_group STRING,
  log_stream STRING,
  message_type STRING,
  user_arn STRING,
  user_type STRING,
  conversation_id STRING,
  system_message_id STRING,
  user_message_id STRING,
  research_id STRING,
  feedback_type STRING,
  feedback_reason STRING,
  feedback_details STRING,
  rating STRING,
  status_code STRING,
  resource_arn STRING,
  account_id STRING,
  event_timestamp BIGINT,
  namespace STRING
)
PARTITIONED BY (
  year INT,
  month INT,
  day INT
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://${BUCKET}/cloudwatch-logs/feedback/'
TBLPROPERTIES (
  'projection.enabled' = 'true',
  'projection.year.type' = 'integer',
  'projection.year.range' = '2024,2030',
  'projection.month.type' = 'integer',
  'projection.month.range' = '1,12',
  'projection.month.digits' = '2',
  'projection.day.type' = 'integer',
  'projection.day.range' = '1,31',
  'projection.day.digits' = '2',
  'storage.location.template' = 's3://${BUCKET}/cloudwatch-logs/feedback/year=${year}/month=${month}/day=${day}'
);

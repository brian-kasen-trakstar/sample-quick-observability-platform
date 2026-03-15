-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
-- SPDX-License-Identifier: MIT-0
CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.cloudtrail_events (
  timestamp STRING,
  event_id STRING,
  event_name STRING,
  event_source STRING,
  event_type STRING,
  event_category STRING,
  aws_region STRING,
  source_ip STRING,
  user_agent STRING,
  user_type STRING,
  principal_id STRING,
  user_name STRING,
  user_arn STRING,
  account_id STRING,
  recipient_account_id STRING,
  shared_event_id STRING,
  read_only BOOLEAN,
  error_code STRING,
  error_message STRING,
  request_parameters STRING,
  response_elements STRING,
  service_event_details STRING,
  resources STRING,
  resource_type STRING,
  resource_arn STRING
)
PARTITIONED BY (
  year INT,
  month INT,
  day INT
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://${BUCKET}/cloudtrail/'
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
  'storage.location.template' = 's3://${BUCKET}/cloudtrail/year=${year}/month=${month}/day=${day}'
);

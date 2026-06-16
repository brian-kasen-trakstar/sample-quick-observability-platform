#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Glue ETL job to convert Amazon Quick observability raw JSON logs in S3
into Apache Iceberg tables in the Glue catalog.

Expected Glue job arguments:
  --database                 Glue/Athena database name
  --bucket                   Data lake bucket name
  --include_message_content  true|false
"""

import sys
from awsglue.utils import getResolvedOptions
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T


def build_spark(bucket: str) -> SparkSession:
    warehouse = f"s3://{bucket}/iceberg/"
    return (
        SparkSession.builder
        .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
        .config("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.glue_catalog.warehouse", warehouse)
        .config("spark.sql.catalog.glue_catalog.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
        .config("spark.sql.catalog.glue_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
        .config("spark.sql.iceberg.handle-timestamp-without-timezone", "true")
        .getOrCreate()
    )


def with_partitions(df):
    path_col = F.input_file_name()
    year = F.regexp_extract(path_col, r"/year=(\\d{4})/", 1).cast("int")
    month = F.regexp_extract(path_col, r"/month=(\\d{2})/", 1).cast("int")
    day = F.regexp_extract(path_col, r"/day=(\\d{2})/", 1).cast("int")
    return df.withColumn("year", year).withColumn("month", month).withColumn("day", day)


def load_raw_json(spark: SparkSession, bucket: str, prefix: str):
    path = f"s3://{bucket}/{prefix}/"
    try:
        df = spark.read.option("recursiveFileLookup", "true").json(path)
    except Exception as exc:
        err = str(exc)
        if "PATH_NOT_FOUND" in err or "Path does not exist" in err:
            print(f"Source path not found, skipping: {path}")
            return None
        raise
    if df.rdd.isEmpty():
        return None
    return with_partitions(df)


def ensure_columns(df, columns):
    for name, dtype in columns:
        if name not in df.columns:
            df = df.withColumn(name, F.lit(None).cast(dtype))
        else:
            df = df.withColumn(name, F.col(name).cast(dtype))
    ordered = [name for name, _ in columns]
    return df.select(*ordered)


def create_iceberg_table(spark: SparkSession, database: str, bucket: str, table: str, columns):
    cols = ",\n  ".join([f"{name} {dtype.simpleString().upper()}" for name, dtype in columns])
    sql = f"""
CREATE TABLE IF NOT EXISTS glue_catalog.{database}.{table} (
  {cols}
)
USING iceberg
PARTITIONED BY (year, month, day)
LOCATION 's3://{bucket}/iceberg/{table}/'
TBLPROPERTIES (
  'format'='parquet',
  'write_compression'='snappy'
)
"""
    spark.sql(sql)


def upsert_table(spark: SparkSession, database: str, table: str, df):
    if df is None:
        print(f"No source data found for {table}; table ensured but no rows written")
        return
    # Overwrite touched partitions to avoid duplicate rows on reruns.
    df.writeTo(f"glue_catalog.{database}.{table}").overwritePartitions()
    print(f"Upserted Iceberg table: {database}.{table}")


def main():
    args = getResolvedOptions(
        sys.argv,
        ["JOB_NAME", "database", "bucket", "include_message_content"],
    )

    database = args["database"]
    bucket = args["bucket"]
    include_message_content = args["include_message_content"].lower() == "true"

    spark = build_spark(bucket)
    spark.sql(f"CREATE DATABASE IF NOT EXISTS glue_catalog.{database}")

    chat_columns = [
        ("timestamp", T.StringType()),
        ("log_group", T.StringType()),
        ("log_stream", T.StringType()),
        ("message_type", T.StringType()),
        ("user_arn", T.StringType()),
        ("user_type", T.StringType()),
        ("agent_id", T.StringType()),
        ("flow_id", T.StringType()),
        ("conversation_id", T.StringType()),
        ("system_message_id", T.StringType()),
        ("user_message_id", T.StringType()),
        ("user_selected_resources", T.StringType()),
        ("status_code", T.StringType()),
        ("message_scope", T.StringType()),
        ("action_connectors", T.StringType()),
        ("cited_resource", T.StringType()),
        ("file_attachment", T.StringType()),
        ("resource_arn", T.StringType()),
        ("account_id", T.StringType()),
        ("event_timestamp", T.LongType()),
        ("namespace", T.StringType()),
        ("latency", T.StringType()),
        ("time_to_first_token", T.StringType()),
        ("surface_type", T.StringType()),
        ("web_search", T.StringType()),
    ]
    if include_message_content:
        chat_columns.extend([
            ("user_message", T.StringType()),
            ("system_text_message", T.StringType()),
        ])
    chat_columns.extend([
        ("year", T.IntegerType()),
        ("month", T.IntegerType()),
        ("day", T.IntegerType()),
    ])

    common_logs_partitions = [
        ("year", T.IntegerType()),
        ("month", T.IntegerType()),
        ("day", T.IntegerType()),
    ]

    feedback_columns = [
        ("timestamp", T.StringType()),
        ("log_group", T.StringType()),
        ("log_stream", T.StringType()),
        ("message_type", T.StringType()),
        ("user_arn", T.StringType()),
        ("user_type", T.StringType()),
        ("conversation_id", T.StringType()),
        ("system_message_id", T.StringType()),
        ("user_message_id", T.StringType()),
        ("research_id", T.StringType()),
        ("feedback_type", T.StringType()),
        ("feedback_reason", T.StringType()),
        ("feedback_details", T.StringType()),
        ("rating", T.StringType()),
        ("status_code", T.StringType()),
        ("resource_arn", T.StringType()),
        ("account_id", T.StringType()),
        ("event_timestamp", T.LongType()),
        ("namespace", T.StringType()),
    ] + common_logs_partitions

    agent_hours_columns = [
        ("timestamp", T.StringType()),
        ("log_group", T.StringType()),
        ("log_stream", T.StringType()),
        ("message_type", T.StringType()),
        ("user_arn", T.StringType()),
        ("subscription_type", T.StringType()),
        ("reporting_service", T.StringType()),
        ("usage_group", T.StringType()),
        ("usage_hours", T.DoubleType()),
        ("service_resource_arn", T.StringType()),
        ("resource_arn", T.StringType()),
        ("account_id", T.StringType()),
        ("event_timestamp", T.LongType()),
    ] + common_logs_partitions

    cloudtrail_columns = [
        ("timestamp", T.StringType()),
        ("event_id", T.StringType()),
        ("event_name", T.StringType()),
        ("event_source", T.StringType()),
        ("event_type", T.StringType()),
        ("event_category", T.StringType()),
        ("aws_region", T.StringType()),
        ("source_ip", T.StringType()),
        ("user_agent", T.StringType()),
        ("user_type", T.StringType()),
        ("principal_id", T.StringType()),
        ("user_name", T.StringType()),
        ("user_arn", T.StringType()),
        ("account_id", T.StringType()),
        ("recipient_account_id", T.StringType()),
        ("shared_event_id", T.StringType()),
        ("read_only", T.BooleanType()),
        ("error_code", T.StringType()),
        ("error_message", T.StringType()),
        ("request_parameters", T.StringType()),
        ("response_elements", T.StringType()),
        ("service_event_details", T.StringType()),
        ("resources", T.StringType()),
        ("resource_type", T.StringType()),
        ("resource_arn", T.StringType()),
    ] + common_logs_partitions

    index_usage_columns = [
        ("timestamp", T.StringType()),
        ("log_group", T.StringType()),
        ("log_stream", T.StringType()),
        ("message_type", T.StringType()),
        ("user_arn", T.StringType()),
        ("consumed_index_size", T.LongType()),
        ("source_type", T.StringType()),
        ("source_name", T.StringType()),
        ("source_arn", T.StringType()),
        ("consumed_source_size", T.LongType()),
        ("consumed_source_doc_count", T.LongType()),
        ("resource_arn", T.StringType()),
        ("account_id", T.StringType()),
        ("event_timestamp", T.LongType()),
    ] + common_logs_partitions

    tables = [
        ("chat_logs", "cloudwatch-logs/chat", chat_columns),
        ("feedback_logs", "cloudwatch-logs/feedback", feedback_columns),
        ("agent_hours_logs", "cloudwatch-logs/agent-hours", agent_hours_columns),
        ("cloudtrail_events", "cloudtrail", cloudtrail_columns),
        ("index_usage_logs", "cloudwatch-logs/index-usage", index_usage_columns),
    ]

    for table_name, source_prefix, columns in tables:
        create_iceberg_table(spark, database, bucket, table_name, columns)
        raw_df = load_raw_json(spark, bucket, source_prefix)
        if raw_df is not None:
            raw_df = ensure_columns(raw_df, columns)
        upsert_table(spark, database, table_name, raw_df)

    spark.stop()


if __name__ == "__main__":
    main()

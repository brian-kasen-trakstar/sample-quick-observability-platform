#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Set up the data catalog for Amazon Quick Observability Platform.

Two access control modes:
  --access-control lakeformation
      Register S3 with Lake Formation, configure KMS, grant caller and
      Quick Sight permissions, create Athena database/tables/views, then
      grant Quick Sight SELECT/DESCRIBE on each table and view.

  --access-control iam
      Create Athena database, tables, and views. Access is controlled
      through IAM policies only.

Usage:
    python3 setup_datacatalog.py \\
        --region us-east-1 [--profile default] \\
        --database quickobserve_db --bucket my-data-lake \\
        --workgroup primary --output-location s3://my-bucket/athena-results/ \\
        --access-control lakeformation [--kms-key-arn arn:aws:kms:...]
"""

import boto3
import json
import argparse
import re
import sys
import time
import os
from pathlib import Path


TABLES = [
    "chat_logs",
    "feedback_logs",
    "agent_hours_logs",
    "cloudtrail_events",
    "index_usage_logs",
]

VIEWS = [
    "chat_activity",
    "feedback_analysis",
    "agent_hours_usage",
    "api_audit_trail",
    "querydatabase_events",
    "index_usage",
]


# ── Helpers ───────────────────────────────────────────────────────────────

def run_athena_query(athena, query, database, workgroup, result_config):
    """Execute an Athena query and wait for completion. Returns (success, reason)."""
    try:
        resp = athena.start_query_execution(
            QueryString=query,
            QueryExecutionContext={"Database": database},
            WorkGroup=workgroup,
            ResultConfiguration=result_config,
        )
        qid = resp["QueryExecutionId"]
        while True:
            result = athena.get_query_execution(QueryExecutionId=qid)
            state = result["QueryExecution"]["Status"]["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(1)
        reason = result["QueryExecution"]["Status"].get("StateChangeReason", "")
        return state == "SUCCEEDED", reason
    except Exception as e:
        return False, str(e)


def get_qs_role_arn(iam_client):
    """Find the Quick Sight service role ARN, or None."""
    try:
        return iam_client.get_role(RoleName="aws-quicksight-service-role-v0")["Role"]["Arn"]
    except iam_client.exceptions.NoSuchEntityException:
        return None


# ── Lake Formation helpers ────────────────────────────────────────────────

def lf_register_s3(lf, s3_arn):
    """Register S3 location with Lake Formation."""
    print(f"  Registering S3 location: {s3_arn}")
    try:
        lf.describe_resource(ResourceArn=s3_arn)
        print(f"  ✓ S3 location already registered")
    except lf.exceptions.EntityNotFoundException:
        lf.register_resource(ResourceArn=s3_arn, UseServiceLinkedRole=True)
        print(f"  ✓ Registered S3 location: {s3_arn}")


def lf_grant_kms(kms_client, iam_client, kms_key_arn, account_id):
    """Grant KMS access to the Lake Formation service-linked role."""
    lf_slr_name = "AWSServiceRoleForLakeFormationDataAccess"
    lf_slr = (
        f"arn:aws:iam::{account_id}:role/aws-service-role/"
        f"lakeformation.amazonaws.com/{lf_slr_name}"
    )
    print(f"  Granting KMS access to Lake Formation service-linked role")

    # Verify the service-linked role exists before adding to key policy.
    # KMS rejects key policies that reference non-existent principals.
    try:
        iam_client.get_role(RoleName=lf_slr_name)
    except iam_client.exceptions.NoSuchEntityException:
        print(f"  ⚠ Lake Formation service-linked role does not exist yet.")
        print(f"    It will be created automatically when Lake Formation first accesses the S3 location.")
        print(f"    Re-run this step afterwards to update the KMS key policy.")
        return
    except Exception:
        pass  # Role may exist but get_role path format differs; proceed and let put_key_policy validate

    try:
        policy_resp = kms_client.get_key_policy(KeyId=kms_key_arn, PolicyName="default")
        policy = json.loads(policy_resp["Policy"])
        if not any(s.get("Sid") == "LakeFormationDataAccess" for s in policy.get("Statement", [])):
            policy["Statement"].append({
                "Sid": "LakeFormationDataAccess",
                "Effect": "Allow",
                "Principal": {"AWS": lf_slr},
                "Action": ["kms:Decrypt", "kms:GenerateDataKey"],
                "Resource": "*",
            })
            kms_client.put_key_policy(KeyId=kms_key_arn, PolicyName="default", Policy=json.dumps(policy))
            print(f"  ✓ Granted KMS access to: {lf_slr_name}")
        else:
            print(f"  ✓ KMS access already configured")
    except Exception as e:
        print(f"  ⚠ Could not update KMS key policy: {e}")
        print(f"    The Lake Formation service-linked role may not exist yet.")
        print(f"    Re-run this step after Lake Formation has accessed the data.")


def lf_grant(lf, principal, resource, permissions, grant_option=None):
    """Grant Lake Formation permissions, ignoring AlreadyExists."""
    kwargs = {
        "Principal": {"DataLakePrincipalIdentifier": principal},
        "Resource": resource,
        "Permissions": permissions,
    }
    if grant_option:
        kwargs["PermissionsWithGrantOption"] = grant_option
    try:
        lf.grant_permissions(**kwargs)
        return True
    except lf.exceptions.AlreadyExistsException:
        return True
    except Exception as e:
        print(f"  ✗ Grant failed: {e}")
        return False


def lf_grant_table(lf, account_id, database, table_name, principal):
    """Grant SELECT/DESCRIBE on a specific table to a principal."""
    resource = {"Table": {"CatalogId": account_id, "DatabaseName": database, "Name": table_name}}
    ok = lf_grant(lf, principal, resource, ["SELECT", "DESCRIBE"])
    if ok:
        print(f"  ✓ Granted SELECT, DESCRIBE on: {database}.{table_name}")
    return ok


def lf_enable_iam_mode_compat(lf, account_id, database, s3_arn, caller_arn):
    """In IAM mode, add Lake Formation grants for the caller principal.

    This is required in accounts where Lake Formation governance is enabled,
    otherwise Athena DDL can fail with Insufficient Lake Formation permission(s)
    even when users choose IAM access control.
    """
    # Allow the current caller to use the data location for external tables.
    resource_loc = {"DataLocation": {"CatalogId": account_id, "ResourceArn": s3_arn}}
    ok_loc = lf_grant(lf, caller_arn, resource_loc, ["DATA_LOCATION_ACCESS"])

    # Allow the current caller to manage tables in this database.
    resource_db = {"Database": {"CatalogId": account_id, "Name": database}}
    ok_db = lf_grant(
        lf,
        caller_arn,
        resource_db,
        ["CREATE_TABLE", "ALTER", "DROP", "DESCRIBE"],
    )

    if not ok_loc or not ok_db:
        raise RuntimeError("Failed to apply required Lake Formation IAM compatibility grants")


# Columns that contain sensitive chat content (user questions and AI responses).
# These may include data from connected enterprise sources (databases, S3, etc.).
# When message content logging is enabled, Lake Formation column-level exclusion
# prevents the Quick Sight service role from accessing these columns.
SENSITIVE_COLUMNS = ["user_message", "system_text_message"]


def lf_grant_table_exclude_columns(lf, account_id, database, table_name, principal, excluded_columns):
    """Grant SELECT on a table excluding specific columns.

    Uses Lake Formation column-level access control with ColumnWildcard
    and ExcludedColumnNames to restrict access to sensitive columns.
    The column-level SELECT grant implicitly provides table visibility,
    so a separate DESCRIBE grant is not needed.
    See: https://docs.aws.amazon.com/lake-formation/latest/dg/granting-table-permissions.html
    """
    resource = {
        "TableWithColumns": {
            "CatalogId": account_id,
            "DatabaseName": database,
            "Name": table_name,
            "ColumnWildcard": {
                "ExcludedColumnNames": excluded_columns,
            },
        }
    }
    ok = lf_grant(lf, principal, resource, ["SELECT"])
    if ok:
        excluded_str = ", ".join(excluded_columns)
        print(f"  ✓ Granted SELECT (excluding: {excluded_str}) on: {database}.{table_name}")
    return ok


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Set up data catalog")
    parser.add_argument("--profile", help="AWS profile")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--database", required=True, help="Athena/Glue database name")
    parser.add_argument("--bucket", required=True, help="Data lake S3 bucket name")
    parser.add_argument("--workgroup", required=True, help="Athena workgroup name")
    parser.add_argument("--output-location", required=True, help="S3 location for Athena query results")
    parser.add_argument("--access-control", required=True, choices=["lakeformation", "iam"],
                        help="Access control mode")
    parser.add_argument("--kms-key-arn", help="KMS key ARN (used with lakeformation)")
    parser.add_argument("--include-message-content", action="store_true",
                        help="Add user_message and system_text_message columns to chat_logs table")
    args = parser.parse_args()

    # Validate inputs
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", args.database):
        print(f"✗ Invalid database name: {args.database}")
        return 1
    if not re.match(r"^[a-z0-9][a-z0-9.-]*$", args.bucket):
        print(f"✗ Invalid bucket name: {args.bucket}")
        return 1
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", args.workgroup):
        print(f"✗ Invalid workgroup name: {args.workgroup}")
        return 1

    use_env_credentials = bool(os.environ.get("AWS_ACCESS_KEY_ID"))
    if use_env_credentials:
        session = boto3.Session(region_name=args.region)
    else:
        session = boto3.Session(profile_name=args.profile or "default", region_name=args.region)
    sts = session.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    caller_arn = sts.get_caller_identity()["Arn"]

    # Lake Formation does not accept STS temporary credential ARNs
    # (e.g. arn:aws:sts::123:assumed-role/RoleName/SessionName).
    # Convert to the underlying IAM role ARN for grants.
    # STS assumed-role ARNs don't include the IAM path, so we look up
    # the role in IAM to get the full ARN (e.g. with aws-reserved/sso.amazonaws.com/ path).
    if ":assumed-role/" in caller_arn:
        after_assumed = caller_arn.split(":assumed-role/")[1]
        # Role name is everything between "assumed-role/" and the last "/" (session name)
        role_name = "/".join(after_assumed.split("/")[:-1])
        try:
            iam_client = session.client("iam")
            role_resp = iam_client.get_role(RoleName=role_name)
            caller_arn = role_resp["Role"]["Arn"]
        except Exception:
            # Fallback: construct ARN without path
            caller_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
        print(f"  ℹ Converted STS session ARN to IAM role: {caller_arn}")

    database = args.database
    bucket = args.bucket
    s3_arn = f"arn:aws:s3:::{bucket}"
    use_lf = args.access_control == "lakeformation"

    print(f"Setting up data catalog: {database}")
    print(f"  Access control: {'Lake Formation' if use_lf else 'IAM policies'}")
    print(f"  Data lake bucket: {bucket}")
    print(f"  Workgroup: {args.workgroup}")
    print(f"  Caller: {caller_arn}")
    print()

    errors = []

    # ── Lake Formation: S3 registration and KMS ──────────────────────────
    qs_role_arn = None
    if use_lf:
        lf = session.client("lakeformation", region_name=args.region)
        iam_client = session.client("iam")

        # Register S3 location
        lf_register_s3(lf, s3_arn)

        # KMS grants
        if args.kms_key_arn:
            kms_client = session.client("kms", region_name=args.region)
            lf_grant_kms(kms_client, iam_client, args.kms_key_arn, account_id)

        # DATA_LOCATION_ACCESS grants (no database needed)
        resource_loc = {"DataLocation": {"CatalogId": account_id, "ResourceArn": s3_arn}}

        print(f"  Granting data location access to caller")
        if lf_grant(lf, caller_arn, resource_loc, ["DATA_LOCATION_ACCESS"]):
            print(f"  ✓ Granted DATA_LOCATION_ACCESS on: {bucket}")
        else:
            errors.append(f"Lake Formation: grant DATA_LOCATION_ACCESS to caller")

        qs_role_arn = get_qs_role_arn(iam_client)
        if qs_role_arn:
            print(f"  ✓ Found Amazon Quick service role: {qs_role_arn}")
            if lf_grant(lf, qs_role_arn, resource_loc, ["DATA_LOCATION_ACCESS"]):
                print(f"  ✓ Granted DATA_LOCATION_ACCESS on: {bucket} → Amazon Quick")
            else:
                errors.append(f"Lake Formation: grant DATA_LOCATION_ACCESS to Amazon Quick")
        else:
            print(f"  ⚠ Amazon Quick service role not found. Skipping Amazon Quick grants.")

        print()

    # ── Create Glue database ──────────────────────────────────────────────
    print(f"  Creating database: {database}")
    glue = session.client("glue")
    try:
        database_input = {
            "Name": database,
            "Description": "Amazon Quick Observability Data Lake",
        }
        if not use_lf:
            database_input["CreateTableDefaultPermissions"] = [
                {
                    "Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"},
                    "Permissions": ["ALL"],
                }
            ]
        glue.create_database(DatabaseInput=database_input)
        print(f"  ✓ Created database: {database}")
    except glue.exceptions.AlreadyExistsException:
        print(f"  ✓ Database already exists: {database}")
    except Exception as e:
        print(f"  ✗ Failed to create database: {e}")
        return 1
    print()

    # In IAM mode, some accounts still enforce Lake Formation permissions.
    # Add IAM_ALLOWED_PRINCIPALS grants so Athena DDL can create external
    # tables on the data lake paths.
    if not use_lf:
        try:
            lf = session.client("lakeformation", region_name=args.region)
            print("  Configuring Lake Formation compatibility grants for IAM mode")
            lf_enable_iam_mode_compat(lf, account_id, database, s3_arn, caller_arn)
            print("  ✓ Lake Formation IAM compatibility grants configured")
            print()
        except Exception as e:
            print(f"  ⚠ Could not configure Lake Formation IAM compatibility grants: {e}")
            print("    If table creation fails with Lake Formation permission errors,")
            print("    ask a Lake Formation admin to grant DATA_LOCATION_ACCESS on")
            print(f"    {s3_arn} to: {caller_arn}")
            print()

    # ── Lake Formation: database-level grants (database must exist) ───────
    if use_lf:
        resource_db = {"Database": {"CatalogId": account_id, "Name": database}}

        print(f"  Granting database permissions to caller")
        if lf_grant(lf, caller_arn, resource_db, ["ALL"], grant_option=["ALL"]):
            print(f"  ✓ Granted ALL on database: {database}")
        else:
            errors.append(f"Lake Formation: grant ALL on database to caller")

        if qs_role_arn:
            if lf_grant(lf, qs_role_arn, resource_db, ["DESCRIBE"]):
                print(f"  ✓ Granted DESCRIBE on database: {database} → Amazon Quick")
            else:
                errors.append(f"Lake Formation: grant DESCRIBE on database to Amazon Quick")

        print()

    # ── Create Athena tables ──────────────────────────────────────────────
    athena = session.client("athena")
    result_config = {"OutputLocation": args.output_location}
    queries_dir = Path(__file__).parent.parent / "sql"

    print("Creating tables")
    for table in TABLES:
        sql_file = queries_dir / f"create_{table}_table.sql"
        if not sql_file.exists():
            print(f"  ⚠ SQL file not found: {sql_file}")
            errors.append(f"Table: SQL file not found: {sql_file}")
            continue
        print(f"  Creating table: {database}.{table}")
        query = sql_file.read_text()
        query = query.replace("${DATABASE}", database).replace("${BUCKET}", bucket)

        # If message content is included, add user_message and system_text_message
        # columns to the chat_logs table so they're queryable in Athena.
        if table == "chat_logs" and args.include_message_content:
            query = query.replace(
                "  web_search STRING\n",
                "  web_search STRING,\n  user_message STRING,\n  system_text_message STRING\n",
            )

        ok, reason = run_athena_query(athena, query, database, args.workgroup, result_config)
        if ok:
            print(f"  ✓ Created table: {database}.{table}")
        else:
            print(f"  ✗ Failed: {database}.{table}: {reason}")
            errors.append(f"Table: {database}.{table}: {reason}")
    print()

    # ── Create Athena views ───────────────────────────────────────────────
    print("Creating views")
    for view in VIEWS:
        sql_file = queries_dir / f"create_{view}_view.sql"
        if not sql_file.exists():
            print(f"  ⚠ SQL file not found: {sql_file}")
            errors.append(f"View: SQL file not found: {sql_file}")
            continue
        print(f"  Creating view: {database}.{view}")
        query = sql_file.read_text().replace("${DATABASE}", database)
        ok, reason = run_athena_query(athena, query, database, args.workgroup, result_config)
        if ok:
            print(f"  ✓ Created view: {database}.{view}")
        else:
            print(f"  ✗ Failed: {database}.{view}: {reason}")
            errors.append(f"View: {database}.{view}: {reason}")
    print()

    # ── Lake Formation post-Athena: per-table grants ──────────────────────
    if use_lf and qs_role_arn:
        print("Granting Amazon Quick per-table permissions")
        for name in TABLES + VIEWS:
            # When message content is included, the chat_logs table has
            # user_message and system_text_message columns that may contain
            # data from connected enterprise sources. Use column-level
            # exclusion to prevent the Quick Sight service role from
            # accessing these columns. The Quick Sight datasets don't query
            # these columns, so this restriction is transparent to the
            # dashboard. Admins retain full access through their own grants.
            if name == "chat_logs" and args.include_message_content:
                if not lf_grant_table_exclude_columns(
                    lf, account_id, database, name, qs_role_arn, SENSITIVE_COLUMNS
                ):
                    errors.append(
                        f"Lake Formation: grant SELECT/DESCRIBE (column-restricted) "
                        f"on {database}.{name} to Amazon Quick"
                    )
            else:
                if not lf_grant_table(lf, account_id, database, name, qs_role_arn):
                    errors.append(
                        f"Lake Formation: grant SELECT/DESCRIBE on {database}.{name} "
                        f"to Amazon Quick"
                    )
        print()

    # ── Result ────────────────────────────────────────────────────────────
    if errors:
        print(f"✗ Completed with {len(errors)} error(s):")
        for err in errors:
            print(f"  • {err}")
        return 1

    print(f"✓ Database: {database}")
    print(f"✓ Tables: {', '.join(TABLES)}")
    print(f"✓ Views: {', '.join(VIEWS)}")
    if use_lf:
        print(f"✓ Lake Formation: S3 registered, per-table grants applied")
        if args.include_message_content:
            excluded_str = ", ".join(SENSITIVE_COLUMNS)
            print(f"✓ Lake Formation: column-level restriction on chat_logs (excluded: {excluded_str})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

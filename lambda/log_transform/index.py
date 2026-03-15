# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Lambda function to transform CloudWatch Logs for Firehose delivery to S3
Converts logs to JSON Lines format for efficient Athena queries
"""
import json
import base64
import gzip
from datetime import datetime, timezone
import os


def lambda_handler(event, context):
    """
    Transform CloudWatch Logs records for S3 delivery.

    Firehose requires exactly one output record per input recordId.
    Each CloudWatch Logs record may contain multiple logEvents, so we
    concatenate all transformed events into a single JSON Lines payload
    per recordId.
    """
    output_records = []

    for record in event['records']:
        try:
            # Decode and decompress the log data
            payload = base64.b64decode(record['data'])

            # Check if data is gzipped
            try:
                decompressed = gzip.decompress(payload)
                log_data = json.loads(decompressed.decode('utf-8'))
            except (OSError, gzip.BadGzipFile, json.JSONDecodeError, UnicodeDecodeError):
                log_data = json.loads(payload.decode('utf-8'))

            # Process log events
            if 'logEvents' in log_data:
                # Skip control messages (health checks sent by CloudWatch Logs)
                if log_data.get('messageType') == 'CONTROL_MESSAGE':
                    output_records.append({
                        'recordId': record['recordId'],
                        'result': 'Dropped',
                        'data': record['data']
                    })
                    continue

                # Collect all transformed events for this record.
                # A single CloudWatch Logs record can contain multiple logEvents,
                # but Firehose requires exactly one output per input recordId.
                json_lines = []
                for log_event in log_data['logEvents']:
                    transformed_record = transform_log_event(log_event, log_data)
                    if transformed_record:
                        json_lines.append(json.dumps(transformed_record))

                if json_lines:
                    combined = '\n'.join(json_lines) + '\n'
                    encoded_data = base64.b64encode(combined.encode('utf-8')).decode('utf-8')
                    output_records.append({
                        'recordId': record['recordId'],
                        'result': 'Ok',
                        'data': encoded_data
                    })
                else:
                    output_records.append({
                        'recordId': record['recordId'],
                        'result': 'Dropped',
                        'data': record['data']
                    })
            else:
                # Pass through if not a log event
                output_records.append({
                    'recordId': record['recordId'],
                    'result': 'Ok',
                    'data': record['data']
                })

        except Exception as e:
            print(f"Error processing record: {str(e)}")
            output_records.append({
                'recordId': record['recordId'],
                'result': 'ProcessingFailed',
                'data': record['data']
            })

    return {'records': output_records}


def transform_log_event(log_event, log_data):
    """
    Transform a single log event into structured format
    """
    try:
        message = log_event.get('message', '')

        # Try to parse message as JSON
        try:
            message_data = json.loads(message)
        except (json.JSONDecodeError, TypeError, ValueError):
            message_data = {'raw_message': message}

        # Extract timestamp from the message payload's event_timestamp
        # (the actual Amazon Quick event time, more accurate than CloudWatch ingestion time)
        # Chat logs use milliseconds (13 digits), agent hours use seconds (10 digits)
        raw_event_ts = message_data.get('event_timestamp', 0)
        if raw_event_ts and raw_event_ts > 9999999999:
            # Milliseconds — convert to seconds
            dt = datetime.fromtimestamp(raw_event_ts / 1000.0, tz=timezone.utc)
        elif raw_event_ts:
            # Already in seconds
            dt = datetime.fromtimestamp(raw_event_ts, tz=timezone.utc)
        else:
            # Fallback to CloudWatch log event envelope timestamp (milliseconds)
            envelope_ts = log_event.get('timestamp', 0)
            dt = datetime.fromtimestamp(envelope_ts / 1000.0, tz=timezone.utc)

        # Build transformed record
        transformed = {
            'timestamp': dt.strftime('%Y-%m-%dT%H:%M:%S'),
            'log_group': log_data.get('logGroup', ''),
            'log_stream': log_data.get('logStream', ''),
            'message_type': message_data.get('logType', 'UNKNOWN'),
        }

        # Field name mappings: raw CloudWatch log field -> Athena column name
        # The raw logs use camelCase but Athena tables use snake_case
        field_mappings = {
            'accountId': 'account_id',
            'logType': 'message_type',  # already handled above, skip in loop
        }

        # Sensitive fields stripped before data reaches S3.
        # These fields may contain user-entered text or AI-generated responses
        # that include data from connected enterprise sources.
        # Controlled by the INCLUDE_MESSAGE_CONTENT environment variable,
        # which is set during deployment based on the user's choice.
        # To change this after deployment, update the Lambda env var and
        # redeploy with: python3 deploy.py --pipeline
        if os.environ.get('INCLUDE_MESSAGE_CONTENT', 'false') == 'true':
            sensitive_fields = set()
        else:
            sensitive_fields = {'user_message', 'system_text_message'}

        # Add all fields from message
        for key, value in message_data.items():
            # Map field name if needed
            mapped_key = field_mappings.get(key, key)

            # Skip sensitive fields — stripped before data reaches S3
            if key in sensitive_fields or mapped_key in sensitive_fields:
                continue

            if mapped_key not in transformed:
                # Handle nested objects
                if isinstance(value, (dict, list)):
                    transformed[mapped_key] = json.dumps(value)
                else:
                    transformed[mapped_key] = value

        return transformed

    except Exception as e:
        print(f"Error transforming log event: {str(e)}")
        return None

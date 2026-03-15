# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Lambda function to transform CloudTrail events for Firehose delivery to S3
Converts EventBridge CloudTrail events to JSON Lines format for Athena queries
"""
import json
import base64
import traceback
from datetime import datetime, timezone

def lambda_handler(event, context):
    """
    Transform CloudTrail events from EventBridge for S3 delivery
    
    Input: Firehose transformation event with CloudTrail records
    Output: Transformed records in JSON Lines format
    """
    output_records = []
    
    for record in event['records']:
        try:
            # Decode the CloudTrail event data
            payload = base64.b64decode(record['data'])
            cloudtrail_event = json.loads(payload.decode('utf-8'))
            
            # Transform CloudTrail event to Athena schema
            transformed_record = transform_cloudtrail_event(cloudtrail_event)
            
            if transformed_record:
                # Convert to JSON Lines format (one JSON object per line)
                json_line = json.dumps(transformed_record) + '\n'
                encoded_data = base64.b64encode(json_line.encode('utf-8')).decode('utf-8')
                
                output_records.append({
                    'recordId': record['recordId'],
                    'result': 'Ok',
                    'data': encoded_data
                })
            else:
                # If transformation returns None, mark as failed
                print(f"Transformation returned None for record: {record['recordId']}")
                output_records.append({
                    'recordId': record['recordId'],
                    'result': 'ProcessingFailed',
                    'data': record['data']
                })
                
        except Exception as e:
            print(f"Error processing record {record['recordId']}: {str(e)}")
            traceback.print_exc()
            
            # Mark as processing failed and preserve original data
            output_records.append({
                'recordId': record['recordId'],
                'result': 'ProcessingFailed',
                'data': record['data']
            })
    
    return {'records': output_records}


def transform_cloudtrail_event(event):
    """
    Transform CloudTrail event to Athena schema
    
    Input: CloudTrail event from EventBridge
    Output: Flattened JSON matching cloudtrail_events table schema
    """
    try:
        # EventBridge wraps CloudTrail in 'detail' field
        detail = event.get('detail', {})
        
        # Extract user identity
        user_identity = detail.get('userIdentity', {})
        
        # Extract timestamp and partition keys
        event_time = detail.get('eventTime', '')
        partition_keys = extract_partition_keys(event_time)
        
        # Extract resource information
        resources = detail.get('resources', [])
        resource_type = extract_resource_type(resources)
        resource_arn = extract_resource_arn(resources)
        
        # Build transformed record matching Athena table schema
        transformed = {
            'timestamp': event_time,
            'event_id': detail.get('eventID', ''),
            'event_name': detail.get('eventName', ''),
            'event_source': detail.get('eventSource', ''),
            'event_type': detail.get('eventType', ''),
            'event_category': detail.get('eventCategory', ''),
            'aws_region': detail.get('awsRegion', ''),
            'source_ip': detail.get('sourceIPAddress', ''),
            'user_agent': detail.get('userAgent', ''),
            'user_type': user_identity.get('type', ''),
            'principal_id': user_identity.get('principalId', ''),
            'user_name': user_identity.get('userName', ''),
            'user_arn': user_identity.get('arn', ''),
            'account_id': user_identity.get('accountId', ''),
            'recipient_account_id': detail.get('recipientAccountId', ''),
            'shared_event_id': detail.get('sharedEventID', ''),
            'read_only': detail.get('readOnly', False),
            'error_code': detail.get('errorCode', ''),
            'error_message': detail.get('errorMessage', ''),
            'request_parameters': json.dumps(detail.get('requestParameters', {})),
            'response_elements': json.dumps(detail.get('responseElements', {})),
            'service_event_details': json.dumps(detail.get('serviceEventDetails', {})),
            'resources': json.dumps(resources) if resources else '[]',
            'resource_type': resource_type,
            'resource_arn': resource_arn,
            # Partition keys
            'year': partition_keys['year'],
            'month': partition_keys['month'],
            'day': partition_keys['day']
        }
        
        return transformed
        
    except Exception as e:
        print(f"Error transforming CloudTrail event: {str(e)}")
        traceback.print_exc()
        return None


def extract_partition_keys(timestamp_str):
    """
    Extract partition keys (year, month, day) from ISO 8601 timestamp
    
    Input: ISO 8601 timestamp string (e.g., "2024-01-15T10:30:45Z")
    Output: Dict with year, month, day as integers
    """
    try:
        # Parse ISO 8601 timestamp
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        
        return {
            'year': dt.year,
            'month': dt.month,
            'day': dt.day
        }
    except Exception as e:
        print(f"Error extracting partition keys from timestamp '{timestamp_str}': {str(e)}")
        # Return current date as fallback
        now = datetime.now(timezone.utc)
        return {
            'year': now.year,
            'month': now.month,
            'day': now.day
        }


def extract_resource_type(resources):
    """
    Extract resource type from CloudTrail resources array
    
    Input: List of resource objects
    Output: Resource type string or empty string
    """
    try:
        if resources and len(resources) > 0:
            # Get first resource's type
            resource = resources[0]
            resource_type = resource.get('type', '')
            
            # Extract just the resource type name (e.g., "AWS::QuickSight::Dashboard" -> "Dashboard")
            if '::' in resource_type:
                parts = resource_type.split('::')
                return parts[-1] if parts else ''
            
            return resource_type
        
        return ''
    except Exception as e:
        print(f"Error extracting resource type: {str(e)}")
        return ''


def extract_resource_arn(resources):
    """
    Extract resource ARN from CloudTrail resources array
    
    Input: List of resource objects
    Output: Resource ARN string or empty string
    """
    try:
        if resources and len(resources) > 0:
            # Get first resource's ARN
            return resources[0].get('ARN', '')
        
        return ''
    except Exception as e:
        print(f"Error extracting resource ARN: {str(e)}")
        return ''

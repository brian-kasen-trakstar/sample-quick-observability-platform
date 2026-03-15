# CloudTrail Transform Lambda

Transforms CloudTrail events from EventBridge to JSON Lines format for S3/Athena.

## Input Format

EventBridge CloudTrail event (base64-encoded):
```json
{
  "detail": {
    "eventTime": "2024-01-15T10:30:45Z",
    "eventID": "12345678-1234-1234-1234-123456789012",
    "eventName": "CreateDashboard",
    "eventSource": "quicksight.amazonaws.com",
    "userIdentity": {
      "type": "IAMUser",
      "arn": "arn:aws:iam::111122223333:user/admin"
    },
    "requestParameters": {...},
    "responseElements": {...}
  }
}
```

## Output Format

JSON Lines (one JSON object per line):
```json
{"timestamp":"2024-01-15T10:30:45Z","event_id":"...","event_name":"CreateDashboard",...,"year":2024,"month":1,"day":15}
```

## Transformation Logic

1. Decode base64 payload
2. Parse JSON CloudTrail event
3. Flatten nested fields (userIdentity, resources)
4. Extract partition keys from timestamp
5. Convert to JSON Lines format
6. Base64 encode for Firehose

## Error Handling

- Record-level errors: Mark as ProcessingFailed, preserve original data
- Unhandled exceptions: Logged to CloudWatch, Firehose retries
- Invalid timestamps: Use current date as fallback for partition keys

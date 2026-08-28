from datetime import datetime

import functions_framework  # type: ignore
from google.cloud import bigquery

# Initialize the BigQuery client
client = bigquery.Client()
dataset_id = 'llm_shield_telemetry'
table_id = 'usage_metrics'
table_ref = client.dataset(dataset_id).table(table_id)

@functions_framework.http
def receive_telemetry(request):
    """HTTP Cloud Function."""
    request_json = request.get_json(silent=True)

    if request_json:
        try:
            # Extract fields from the JSON payload
            # The schema is: timestamp:TIMESTAMP, project_id:STRING, request_count:INTEGER, token_count:INTEGER, model:STRING
            timestamp_str = request_json.get('timestamp')

            # Parse timestamp if it exists, otherwise use current time
            if timestamp_str:
                try:
                    # Try parsing ISO format
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                except ValueError:
                    timestamp = datetime.utcnow()
            else:
                timestamp = datetime.utcnow()

            project_id = str(request_json.get('project_id', 'unknown'))
            request_count = int(request_json.get('request_count', 1))
            token_count = int(request_json.get('token_count', 0))
            model = str(request_json.get('model', 'unknown'))

            rows_to_insert = [
                {
                    "timestamp": timestamp.isoformat(),
                    "project_id": project_id,
                    "request_count": request_count,
                    "token_count": token_count,
                    "model": model,
                }
            ]

            errors = client.insert_rows_json(table_ref, rows_to_insert)
            if not errors:
                return 'Telemetry received and inserted', 200
            else:
                return f'Error inserting into BigQuery: {errors}', 500
        except Exception as e:
            return f'Error processing telemetry: {e}', 400
    else:
        return 'No JSON payload provided', 400

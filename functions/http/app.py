import json
import os
import uuid
from datetime import datetime

import boto3

table = boto3.resource("dynamodb").Table(os.environ["TABLE"])


def resp(code, body):
    return {
        "statusCode": code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def handler(event, context):
    method = event["requestContext"]["http"]["method"]

    # List all reminders
    if method == "GET":
        return resp(200, table.scan()["Items"])

    # Add a new reminder
    if method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return resp(400, {"error": "invalid JSON"})

        message = body.get("message")
        due_at = body.get("due_at")
        if not message or not due_at:
            return resp(400, {"error": "message and due_at are required"})

        try:
            datetime.fromisoformat(due_at)  # e.g. 2026-11-28T09:00:00Z (UTC)
        except ValueError:
            return resp(400, {"error": "due_at must be ISO 8601"})

        item = {
            "id": str(uuid.uuid4()),
            "message": message,
            "due_at": due_at,
            "sent": False,
        }
        table.put_item(Item=item)
        return resp(201, item)

    return resp(405, {"error": "method not allowed"})
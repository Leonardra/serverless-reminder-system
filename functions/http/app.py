import base64
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

table = boto3.resource("dynamodb").Table(os.environ["TABLE"])

MAX_MESSAGE_LENGTH = 500
PAGE_SIZE = 50


def resp(code, body):
    return {
        "statusCode": code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def parse_due_at(value):
    """Parse an ISO 8601 string that includes a timezone; return it in UTC."""
    if not isinstance(value, str):
        raise ValueError("due_at must be a string")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("due_at must include a timezone, e.g. 2026-11-28T12:00:00Z")
    return dt.astimezone(timezone.utc)


def encode_token(key):
    return base64.urlsafe_b64encode(json.dumps(key).encode()).decode() if key else None


def decode_token(token):
    return json.loads(base64.urlsafe_b64decode(token.encode()))


def handler(event, context):
    method = event["requestContext"]["http"]["method"]


    if method == "GET":
        params = event.get("queryStringParameters") or {}
        scan_args = {"Limit": PAGE_SIZE}
        if params.get("next"):
            try:
                scan_args["ExclusiveStartKey"] = decode_token(params["next"])
            except Exception:
                return resp(400, {"error": "invalid next token"})
        page = table.scan(**scan_args)
        return resp(200, {
            "items": page["Items"],
            "next": encode_token(page.get("LastEvaluatedKey")),
        })


    if method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return resp(400, {"error": "invalid JSON"})

        # Comment 4: message must be a non-empty string of at most 500 characters
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            return resp(400, {"error": "message is required"})
        message = message.strip()
        if len(message) > MAX_MESSAGE_LENGTH:
            return resp(400, {"error": f"message must be at most {MAX_MESSAGE_LENGTH} characters"})

        # Comment 1: due_at must be valid, include a timezone, and be in the future
        try:
            due = parse_due_at(body.get("due_at"))
        except (TypeError, ValueError) as e:
            return resp(400, {"error": str(e) or "due_at must be ISO 8601"})
        if due <= datetime.now(timezone.utc):
            return resp(400, {"error": "due_at must be in the future"})

        item = {
            "id": str(uuid.uuid4()),
            "message": message,
            "due_at": due.strftime("%Y-%m-%dT%H:%M:%SZ"),  # always stored as UTC
            "sent": False,
        }
        table.put_item(Item=item)
        return resp(201, item)

    return resp(405, {"error": "method not allowed"})
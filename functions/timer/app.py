import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
sns = boto3.client("sns")
TOPIC_ARN = os.environ["TOPIC_ARN"]


def claim(reminder_id):
    """Mark a reminder as sent only if it is still unsent.
    Returns False if another run already claimed it."""
    try:
        table.update_item(
            Key={"id": reminder_id},
            UpdateExpression="SET sent = :t",
            ConditionExpression="sent = :f",
            ExpressionAttributeValues={":t": True, ":f": False},
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def release(reminder_id):
    """Undo a claim if sending failed, so the next run retries it."""
    table.update_item(
        Key={"id": reminder_id},
        UpdateExpression="SET sent = :f",
        ExpressionAttributeValues={":f": False},
    )


def handler(event, context):
    now = datetime.now(timezone.utc)
    sent, failed = 0, 0
    scan_args = {"FilterExpression": Attr("sent").eq(False)}

    while True:
        page = table.scan(**scan_args)
        for item in page["Items"]:
            due = datetime.fromisoformat(item["due_at"])
            if due > now:
                continue                      # not due yet
            if not claim(item["id"]):
                continue                      # another run is handling it
            try:
                sns.publish(
                    TopicArn=TOPIC_ARN,
                    Subject="Reminder",
                    Message=f"{item['message']}\n\n(Due at {item['due_at']} UTC)",
                )
                sent += 1
            except Exception as e:
                print(f"Failed to send {item['id']}: {e}")
                release(item["id"])
                failed += 1
        if "LastEvaluatedKey" not in page:
            break
        scan_args["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    print(f"sent {sent} reminders, {failed} failed")
    return {"sent": sent, "failed": failed}
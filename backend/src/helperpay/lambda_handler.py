"""AWS Lambda entry point behind API Gateway (HTTP API, Cognito JWT authorizer).

The same function also runs chat jobs: POST /api/chat invokes this function
asynchronously with {"job": ...}.
"""

from __future__ import annotations

import base64
import json
import os

import boto3

from .agent.runtime import Settings, make_transcriber
from .api import App
from .blobs import S3Blobs
from .service import Household
from .store import DynamoStore

_lambda = boto3.client("lambda")
_settings = Settings()
_blobs = S3Blobs(os.environ["UPLOAD_BUCKET"])


def _start_job(job: dict) -> None:
    _lambda.invoke(
        FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
        InvocationType="Event",
        Payload=json.dumps({"job": job}).encode(),
    )


app = App(
    hh=Household(DynamoStore(os.environ["TABLE_NAME"])),
    blobs=_blobs,
    settings=_settings,
    start_job=_start_job,
    transcribe=make_transcriber(_settings, _blobs),
)


def handler(event, _context):
    if "job" in event:
        app.run_job(event["job"])
        return {"ok": True}

    method = event["requestContext"]["http"]["method"]
    body = None
    if event.get("body"):
        raw = base64.b64decode(event["body"]) if event.get("isBase64Encoded") else event["body"]
        try:
            body = json.loads(raw)
        except ValueError:
            return _response(400, {"error": "Request body must be JSON."})
    status, payload = app.handle(method, event["rawPath"], body)
    return _response(status, payload)


def _response(status: int, payload) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(payload, default=str),
    }

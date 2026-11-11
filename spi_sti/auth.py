import json

import ee


def read_service_account(raw):
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("That file is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("That file is not a service account key.")
    if payload.get("type") != "service_account" or "private_key" not in payload or "client_email" not in payload:
        raise ValueError("Upload a Google service account key. An OAuth client file will not work.")
    return payload


def initialize(payload, project):
    project = (project or payload.get("project_id") or "").strip()
    if not project:
        raise ValueError("Enter the Google Cloud project id. It is usually the project_id in the key file.")
    email = payload["client_email"]
    credentials = ee.ServiceAccountCredentials(email, key_data=json.dumps(payload))
    ee.Initialize(credentials, project=project)
    return project

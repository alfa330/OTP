"""TimesFM 2.5 (Google's time-series foundation model) served by BigQuery AI.FORECAST.

Why BigQuery: the model needs about a gigabyte of memory, which the web instance does not
have, while AI.FORECAST answers in seconds for a few kilobytes of input and bills nothing
measurable. On 61 + 18 + 7 test weeks it reproduced a local TimesFM 2.5 within 0.3 pp.

The service account is the one the portal already uses for Google Cloud
(GOOGLE_APPLICATION_CREDENTIALS_CONTENT); it needs BigQuery job rights in its project.
Only aggregated daily call counts leave the portal, never a phone number or a name.
"""
import os
import re
import threading
import time
import uuid
from datetime import date, datetime, timezone
from typing import Dict, List, Sequence, Tuple

import requests

BQ_MODEL = os.getenv("TIMESFM_BQ_MODEL", "TimesFM 2.5")
BQ_CONTEXT_WINDOW = int(os.getenv("TIMESFM_BQ_CONTEXT", "512"))
BQ_TIMEOUT_SECONDS = 120
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_DELAYS = (3.0, 10.0)
_SCOPES = ["https://www.googleapis.com/auth/bigquery"]
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_lock = threading.Lock()
_state = {"credentials": None, "project": None}


class TimesFMError(RuntimeError):
    """BigQuery could not produce a forecast; callers fall back to the calendar model."""


def _credentials():
    from google.oauth2 import service_account
    import google.auth.transport.requests as google_requests
    from call_qa.config import google_sa_info

    with _lock:
        creds = _state["credentials"]
        if creds is None:
            info = google_sa_info()
            if not info:
                raise TimesFMError("GOOGLE_APPLICATION_CREDENTIALS_CONTENT is not set")
            creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
            _state["credentials"] = creds
            _state["project"] = os.getenv("TIMESFM_BQ_PROJECT") or info.get("project_id")
        if not creds.valid:
            creds.refresh(google_requests.Request())
        return creds.token, _state["project"]


def build_query(horizon: int, confidence: float, context_window: int = BQ_CONTEXT_WINDOW, model: str = BQ_MODEL) -> str:
    if not 1 <= int(horizon) <= 10000:
        raise ValueError("horizon must be 1..10000")
    if not 0 < float(confidence) < 1:
        raise ValueError("confidence must be in (0, 1)")
    if model not in ("TimesFM 2.5", "TimesFM 3.0"):
        raise ValueError("unsupported model")
    return (
        "SELECT sid, forecast_timestamp, forecast_value, prediction_interval_lower_bound, "
        "prediction_interval_upper_bound, ai_forecast_status "
        "FROM AI.FORECAST((SELECT s.sid AS sid, s.d AS d, s.y AS y FROM UNNEST(@series) AS s), "
        "data_col => 'y', timestamp_col => 'd', id_cols => ['sid'], "
        f"model => '{model}', horizon => {int(horizon)}, confidence_level => {float(confidence):.3f}, "
        f"context_window => {int(context_window)})"
    )


def build_parameters(series: Dict[str, Sequence[Tuple[date, float]]], context_window: int = BQ_CONTEXT_WINDOW) -> List[dict]:
    values = []
    for sid, points in series.items():
        if not _ID_RE.match(str(sid)):
            raise ValueError(f"bad series id: {sid!r}")
        for day, value in list(points)[-context_window:]:
            values.append({"structValues": {
                "sid": {"value": str(sid)},
                "d": {"value": day.isoformat()},
                "y": {"value": repr(float(value))},
            }})
    return [{
        "name": "series",
        "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRUCT", "structTypes": [
            {"name": "sid", "type": {"type": "STRING"}},
            {"name": "d", "type": {"type": "DATE"}},
            {"name": "y", "type": {"type": "FLOAT64"}},
        ]}},
        "parameterValue": {"arrayValues": values},
    }]


def parse_rows(rows: List[dict]) -> Dict[str, Dict[date, Tuple[float, float, float]]]:
    out: Dict[str, Dict[date, Tuple[float, float, float]]] = {}
    for row in rows or []:
        sid, ts, value, low, high, status = [cell.get("v") for cell in row.get("f", [])]
        if status:
            raise TimesFMError(f"{sid}: {status}")
        day = datetime.fromtimestamp(float(ts), tz=timezone.utc).date()
        out.setdefault(str(sid), {})[day] = (float(value), float(low), float(high))
    return out


def _call(send, url: str, *, timeout: float, deadline: float, sleep, **kwargs) -> dict:
    """One BigQuery REST call. Passing failures — 429/5xx, a dropped connection, a gateway
    page instead of JSON — are retried after RETRY_DELAYS: one of them at night would
    otherwise put a department on the weaker calendar forecast. Never past `deadline`."""
    error: TimesFMError = TimesFMError("BigQuery did not finish in time")
    for attempt in range(len(RETRY_DELAYS) + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise TimesFMError("BigQuery did not finish in time")
        try:
            response = send(url, timeout=min(timeout, remaining), **kwargs)
        except requests.RequestException as exc:
            error = TimesFMError(f"BigQuery is unreachable: {exc}")
        else:
            try:
                payload = response.json() if response.content else {}
            except ValueError:
                payload = None
            if response.status_code == 200 and payload is not None:
                return payload
            error = TimesFMError(f"BigQuery HTTP {response.status_code}: {str(payload)[:300]}")
            if response.status_code != 200 and response.status_code not in RETRY_STATUSES:
                raise error
        if attempt == len(RETRY_DELAYS) or time.monotonic() + RETRY_DELAYS[attempt] >= deadline:
            break
        sleep(RETRY_DELAYS[attempt])
    raise error


def forecast(series: Dict[str, Sequence[Tuple[date, float]]], horizon: int, confidence: float = 0.8,
             context_window: int = BQ_CONTEXT_WINDOW, model: str = BQ_MODEL,
             session: requests.Session = None, sleep=time.sleep) -> Dict[str, Dict[date, Tuple[float, float, float]]]:
    """{series id: [(day, value), ...]} -> {series id: {day: (median, low, high)}} for `horizon` days."""
    if not series:
        return {}
    token, project = _credentials()
    http = session or requests
    headers = {"Authorization": "Bearer " + token}
    body = {
        "query": build_query(horizon, confidence, context_window, model),
        "useLegacySql": False,
        "parameterMode": "NAMED",
        "queryParameters": build_parameters(series, context_window),
        "timeoutMs": 60000,
        # The same id on a retried POST returns the same job instead of starting another.
        "requestId": str(uuid.uuid4()),
    }
    deadline = time.monotonic() + BQ_TIMEOUT_SECONDS
    payload = _call(http.post, f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries",
                    timeout=90, deadline=deadline, sleep=sleep, headers=headers, json=body)
    rows = list(payload.get("rows") or [])
    job = payload.get("jobReference") or {}
    page_token = payload.get("pageToken")
    complete = bool(payload.get("jobComplete"))
    while not complete or page_token:
        params = {"timeoutMs": 30000}
        if job.get("location"):
            params["location"] = job["location"]
        if page_token:
            params["pageToken"] = page_token
        data = _call(http.get, f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries/{job.get('jobId')}",
                     timeout=60, deadline=deadline, sleep=sleep, headers=headers, params=params)
        complete = bool(data.get("jobComplete"))
        if complete:
            rows.extend(data.get("rows") or [])
            page_token = data.get("pageToken")
    return parse_rows(rows)

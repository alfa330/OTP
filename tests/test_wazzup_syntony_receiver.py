"""Exercise the actual webhook handler without importing the production app."""
import ast
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request


@pytest.fixture
def receiver():
    source = Path(__file__).resolve().parents[1] / 'bot_schedule2.py'
    tree = ast.parse(source.read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'wazzup_webhook')
    node.decorator_list = []
    calls = []
    failures = set()

    def store(messages, **kwargs):
        calls.append(('store', messages, kwargs))
        return len(messages or [])

    def statuses(events, **kwargs):
        calls.append(('statuses', events, kwargs))
        return len(events or [])

    def enqueue(db, payload, **kwargs):
        calls.append(('enqueue', payload, kwargs))
        if 'enqueue' in failures:
            raise RuntimeError('synthetic queue failure')

    namespace = dict(jsonify=jsonify, request=request, logging=logging,
        datetime=datetime, timezone=timezone,
        db=SimpleNamespace(store_wazzup_messages=store, update_wazzup_statuses=statuses),
        wazzup_syntony=SimpleNamespace(enqueue=enqueue),
        wazzup_accounts=SimpleNamespace(account_by_webhook_token=lambda token:
            {'valid-op': 'op', 'valid-potok': 'potok'}.get(token)))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(source), 'exec'), namespace)
    app = Flask(__name__)
    app.add_url_rule('/hook/<token>', view_func=namespace['wazzup_webhook'], methods=['POST'])
    return app.test_client(), calls, failures


def test_receiver_persists_original_payload_before_acknowledging(receiver):
    client, calls, _ = receiver
    raw = b'{ "messages": [{"messageId":"synthetic", "isEcho":true}], "statuses": [] }'
    response = client.post('/hook/valid-op', data=raw, content_type='application/json')
    assert response.status_code == 200
    assert [call[0] for call in calls] == ['store', 'statuses', 'enqueue']
    assert calls[-1][2] == {'raw_body': raw, 'account': 'op'}
    assert calls[-1][1]['messages'][0] == {'messageId': 'synthetic', 'isEcho': True}


def test_queue_failure_returns_500_so_wazzup_retries(receiver):
    client, calls, failures = receiver
    failures.add('enqueue')
    response = client.post('/hook/valid-op', json={'messages': []})
    assert response.status_code == 500
    assert [call[0] for call in calls] == ['store', 'statuses', 'enqueue']


@pytest.mark.parametrize('token,payload,expected', [
    ('invalid', {'messages': []}, 404),
    ('valid-op', ['invalid'], 400),
    ('valid-op', {'test': True}, 200),
])
def test_rejected_and_registration_requests_do_not_reach_the_relay(receiver, token, payload, expected):
    client, calls, _ = receiver
    assert client.post('/hook/' + token, json=payload).status_code == expected
    assert calls == []


def test_potok_cannot_be_misclassified_as_op(receiver):
    client, calls, _ = receiver
    assert client.post('/hook/valid-potok', json={'statuses': []}).status_code == 200
    assert calls[-1][2]['account'] == 'potok'

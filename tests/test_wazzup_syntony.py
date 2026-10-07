"""Global relay privacy/config checks; no app, production DB or external HTTP."""
import copy

import pytest

from wazzup import syntony


GLOBAL = '99df6893-fb6b-4e1d-a78e-9e6e6b37abb2'
OLD_GLOBAL = 'a4bccb5e-5d41-483d-b7c1-a1079685577d'
OTHER = '11111111-2222-3333-4444-555555555555'


@pytest.fixture
def relay_env(monkeypatch):
    for name in ('SYNTONY_WEBHOOK_ENABLED', 'SYNTONY_WEBHOOK_API_URL',
                 'SYNTONY_WEBHOOK_API_KEY', 'SYNTONY_WEBHOOK_AUTH_HEADER',
                 'SYNTONY_WEBHOOK_AUTH_PREFIX'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('SYNTONY_WEBHOOK_ENABLED', 'true')
    monkeypatch.setenv('SYNTONY_WEBHOOK_API_URL', 'https://relay.example.test/wazzup')
    monkeypatch.setenv('SYNTONY_WEBHOOK_API_KEY', 'synthetic-syntony-secret')


def message(message_id='global-incoming', channel=GLOBAL, **changes):
    result = dict(messageId=message_id, channelId=channel, chatType='whatsapp',
                  chatId='70000000000', isEcho=False, type='text', text='Synthetic test message')
    result.update(changes)
    return result


def test_relay_needs_explicit_enable_and_its_own_credentials(relay_env, monkeypatch):
    config = syntony.load_config()
    assert config.url == 'https://relay.example.test/wazzup'
    assert config.key == 'synthetic-syntony-secret'
    monkeypatch.delenv('SYNTONY_WEBHOOK_ENABLED')
    assert syntony.load_config() is None
    monkeypatch.setenv('SYNTONY_WEBHOOK_ENABLED', 'true')
    monkeypatch.delenv('SYNTONY_WEBHOOK_API_KEY')
    monkeypatch.setenv('WAZZUP_API_KEY', 'never-use-the-wazzup-account-key')
    assert syntony.load_config() is None


@pytest.mark.parametrize('url', [
    'http://relay.example.test/wazzup',
    'https://username:password@relay.example.test/wazzup',
    'file:///tmp/webhook',
    'not a URL',
])
def test_invalid_destination_is_disabled(relay_env, monkeypatch, url):
    monkeypatch.setenv('SYNTONY_WEBHOOK_API_URL', url)
    assert syntony.load_config() is None


def test_custom_auth_header_does_not_assume_bearer(relay_env, monkeypatch):
    monkeypatch.setenv('SYNTONY_WEBHOOK_AUTH_HEADER', 'X-API-Key')
    monkeypatch.setenv('SYNTONY_WEBHOOK_AUTH_PREFIX', '')
    config = syntony.load_config()
    assert config.auth_header == 'X-API-Key'
    assert config.auth_prefix == ''


@pytest.mark.parametrize('field,value', [
    ('SYNTONY_WEBHOOK_AUTH_HEADER', 'Authorization\r\nX-Injected'),
    ('SYNTONY_WEBHOOK_API_KEY', 'secret\r\nX-Injected: 1'),
    ('SYNTONY_WEBHOOK_AUTH_PREFIX', 'Bearer\nX-Injected: 1'),
])
def test_header_injection_is_disabled(relay_env, monkeypatch, field, value):
    monkeypatch.setenv(field, value)
    assert syntony.load_config() is None


def test_all_global_events_preserve_payload_and_do_not_mutate_it():
    payload = {
        'messages': [message(), message('old-channel', OLD_GLOBAL),
                     dict(message('outgoing'), isEcho=True, sentFromApp='Wazzup'),
                     dict(message('edited'), isEdited=True, status='edited'),
                     dict(message('deleted'), isDeleted=True)],
        'statuses': [{'messageId': 'outgoing', 'status': 'read'},
                     {'messageId': 'failed', 'channelId': GLOBAL, 'status': 'error',
                      'error': {'code': 'SYNTHETIC', 'message': 'test'}}],
        'channelsUpdates': [{'channelId': GLOBAL, 'state': 'active'}],
    }
    original = copy.deepcopy(payload)
    ready, pending, fully_global = syntony.filter_payload(payload)
    assert ready == original
    assert pending == {}
    assert fully_global is True
    assert payload == original


def test_mixed_payload_never_forwards_other_channels_or_changes_global_events():
    incoming, outgoing = message(), dict(message('global-out'), isEcho=True)
    payload = {
        'messages': [message('private-op', OTHER), incoming, outgoing],
        'statuses': [{'messageId': 'private-op', 'status': 'read'},
                     {'messageId': 'global-out', 'status': 'delivered'},
                     {'messageId': 'explicit-private', 'channelId': OTHER, 'status': 'error'}],
        'channelsUpdates': [{'channelId': OTHER, 'state': 'active'},
                           {'channelId': GLOBAL, 'state': 'active'}],
    }
    original = copy.deepcopy(payload)
    ready, pending, fully_global = syntony.filter_payload(payload)
    assert ready == {
        'messages': [incoming, outgoing],
        'statuses': [{'messageId': 'global-out', 'status': 'delivered'}],
        'channelsUpdates': [{'channelId': GLOBAL, 'state': 'active'}],
    }
    assert not pending and not fully_global
    assert payload == original
    assert 'private' not in str(ready)


def test_status_without_channel_waits_for_ownership_then_is_filtered():
    events = [
        {'messageId': 'known-global', 'status': 'read'},
        {'messageId': 'known-other', 'status': 'error'},
        {'messageId': 'not-stored-yet', 'status': 'delivered'},
    ]
    ready, pending, fully_global = syntony.filter_payload(
        {'statuses': events}, {'known-global': GLOBAL, 'known-other': OTHER})
    assert ready == {'statuses': events[:1]}
    assert pending == {'statuses': events[2:]}
    assert not fully_global


def test_explicit_non_global_channel_cannot_be_overridden_by_message_mapping():
    payload = {'statuses': [{'messageId': 'collision', 'channelId': OTHER, 'status': 'read'}]}
    ready, pending, fully_global = syntony.filter_payload(payload, {'collision': GLOBAL})
    assert not ready and not pending and not fully_global


@pytest.mark.parametrize('payload', [None, [], 'invalid', {},
    {'messages': 'invalid'}, {'messages': [None, 'invalid', {}]},
    {'statuses': [{'status': 'read'}]}, {'channelsUpdates': [{}]},
    {'unknownEvents': [{'channelId': GLOBAL, 'secret': 'must-not-forward'}]},
])
def test_unscoped_or_malformed_events_are_not_forwarded(payload):
    ready, pending, _ = syntony.filter_payload(payload)
    assert not ready and not pending

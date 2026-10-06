"""Explicit post-deploy smoke: logins, reads, SSE, and safely rejected sends only.

Never attempts an eligible send. The Global denial probe uses a synthetic chat
identifier which is not a phone/chat in Wazzup. Other-user probe has an invalid
account, so even an authorization regression cannot reach the vendor request.
No message bodies, account identities, credentials or response bodies are logged.

Run only after deployment: python scripts/check_wazzup_pilot_live.py --run
"""
import argparse
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = 'https://otp-2-fos4.onrender.com'
GLOBAL_CHANNEL = '99df6893-fb6b-4e1d-a78e-9e6e6b37abb2'
SYNTHETIC_CHAT = '__icore_readonly_smoke_no_chat__'


class SmokeError(Exception):
    pass


def config():
    text = (ROOT / '.env.codex.local').read_text(encoding='utf-8-sig')
    values = dict(re.findall(r'^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$', text, re.M))
    return {name: os.getenv(name) or value.strip().strip('\"\'') for name, value in values.items()}


def authenticate(base, values, prefix):
    login, password = values.get(prefix + '_LOGIN'), values.get(prefix + '_PASSWORD')
    if not login or not password:
        raise SmokeError(prefix + '_CREDENTIALS_UNAVAILABLE')
    if (login.strip().lower() == 'alfa330') != (prefix == 'ADMIN'):
        raise SmokeError(prefix + '_UNEXPECTED_LOGIN_SCOPE')
    session = requests.Session()
    try:
        response = session.post(base + '/api/login', json={
            'login': login, 'password': password, 'auth_transport': 'bearer'}, timeout=30)
        if response.status_code != 200:
            raise SmokeError(prefix + '_LOGIN_HTTP_' + str(response.status_code))
        payload = response.json()
        token, actor = payload.get('access_token'), payload.get('user') or {}
        if not token or not actor.get('id'):
            raise SmokeError(prefix + '_LOGIN_SHAPE')
        session.cookies.clear()
        session.headers.update({'Authorization': 'Bearer ' + token, 'X-User-Id': str(actor['id'])})
        return session
    except Exception:
        session.close()
        raise


def read_json(session, base, path, *, method='GET', **kwargs):
    started = time.perf_counter()
    response = session.request(method, base + path, timeout=30, **kwargs)
    if response.status_code != 200:
        raise SmokeError(path + '_HTTP_' + str(response.status_code))
    return response.json(), round((time.perf_counter() - started) * 1000, 2)


def watch_stream(session, base, duration):
    result = {'http_status': None, 'connected': False, 'database_ready': False,
              'change_events': 0, 'changed_messages': 0, 'heartbeat_frames': 0,
              'reload_events': 0, 'unavailable_events': 0}
    stop, opened = threading.Event(), threading.Event()
    stream_response = []

    def read():
        try:
            with session.get(base + '/api/wazzup/pilot/stream?account=op',
                             headers={'Accept': 'text/event-stream'}, stream=True,
                             timeout=(15, 35)) as response:
                stream_response.append(response)
                result['http_status'] = response.status_code
                opened.set()
                if response.status_code != 200:
                    return
                event = ''
                for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    if stop.is_set():
                        return
                    if line.startswith('event: '):
                        event = line[7:]
                    elif line.startswith(': heartbeat'):
                        result['heartbeat_frames'] += 1
                    elif line.startswith('data: '):
                        payload = json.loads(line[6:])
                        if event == 'connected':
                            result['connected'] = True
                            result['database_ready'] = bool(payload.get('ready'))
                        elif event == 'reload':
                            result['reload_events'] += 1
                            result['database_ready'] = bool(payload.get('ready'))
                        elif event == 'unavailable':
                            result['unavailable_events'] += 1
                            result['database_ready'] = False
                        elif event == 'change':
                            result['change_events'] += 1
                            result['changed_messages'] += len(payload.get('changes') or [])
                    elif not line:
                        event = ''
        except Exception as error:
            if not stop.is_set():
                result['error_type'] = type(error).__name__
                opened.set()

    thread = threading.Thread(target=read, daemon=True)
    start = time.perf_counter()
    thread.start()
    if not opened.wait(20):
        stop.set()
        result['error_type'] = 'ConnectDeadline'
        return result
    if result['http_status'] == 200:
        stop.wait(duration)
    result['observation_seconds'] = round(time.perf_counter() - start, 2)
    stop.set()
    # Server notices EOF when writing the next heartbeat; no backend mutation.
    if stream_response:
        stream_response[0].close()
    thread.join(3)
    return result


def run(args):
    values = config()
    base = args.base_url or values.get('OTP_API_BASE_URL') or DEFAULT_BASE
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or parsed.hostname != urlsplit(DEFAULT_BASE).hostname:
        raise SmokeError('UNEXPECTED_BACKEND_HOST')
    base = base.rstrip('/')
    output = {'scope': 'Post-deploy read-only pilot checks plus rejected send requests',
              'positive_sends_attempted': 0}
    with authenticate(base, values, 'ADMIN') as admin:
        capabilities, elapsed = read_json(admin, base, '/api/wazzup/pilot', params={'account': 'op'})
        output['capabilities'] = {name: capabilities.get(name) for name in ('enabled', 'canSend', 'maxTextLength')}
        output['capabilities']['request_ms'] = elapsed
        if not capabilities.get('enabled'):
            raise SmokeError('ALFA330_PILOT_NOT_ENABLED')

        # A real channel exercises Global exclusion, but the chat cannot be a
        # customer: no phone syntax and no row created by this script.
        response = admin.post(base + '/api/wazzup/pilot/send', json={
            'account': 'op', 'channelId': GLOBAL_CHANNEL, 'chatId': SYNTHETIC_CHAT,
            'text': 'SAFE DENIAL PROBE: NEVER SEND', 'clientMessageId': str(uuid.uuid4())}, timeout=30)
        output['global_send_denied'] = response.status_code == 403
        output['global_send_http_status'] = response.status_code

        channels, _ = read_json(admin, base, '/api/wazzup/channels', params={'account': 'op'})
        eligible = [channel for channel in channels.get('items', [])
                    if channel.get('channelId') not in capabilities.get('excludedChannelIds', [])
                    and channel.get('chatsCount', 0) > 0]
        if eligible:
            chats, elapsed = read_json(admin, base, '/api/wazzup/chats', params={
                'account': 'op', 'channel_id': eligible[0]['channelId'], 'limit': 1})
            output['chat_list'] = {'request_ms': elapsed, 'rows': len(chats.get('items', []))}
            if chats.get('items'):
                chat = chats['items'][0]
                messages, elapsed = read_json(admin, base, '/api/wazzup/chat-messages', params={
                    'account': 'op', 'channel_id': chat['channelId'], 'chat_id': chat['chatId'], 'limit': 50})
                output['messages'] = {'request_ms': elapsed, 'rows': len(messages.get('items', []))}
                refreshed, elapsed = read_json(admin, base, '/api/wazzup/pilot/refresh', method='POST', json={
                    'account': 'op', 'channelId': chat['channelId'], 'chatId': chat['chatId'],
                    'messageIds': [message['messageId'] for message in messages.get('items', [])]})
                output['refresh'] = {'request_ms': elapsed, 'rows': len(refreshed.get('items', [])),
                                     'reset': refreshed.get('reset')}
        output['sse'] = watch_stream(admin, base, args.seconds)

    with authenticate(base, values, 'TEST') as other:
        response = other.get(base + '/api/wazzup/pilot/stream?account=op', timeout=30)
        output['other_user_stream_denied'] = response.status_code == 403
        response = other.post(base + '/api/wazzup/pilot/send',
                              json={'account': '__invalid_probe__'}, timeout=30)
        output['other_user_send_denied'] = response.status_code == 403

    output['passed'] = (output.get('global_send_denied') and output.get('other_user_stream_denied')
                        and output.get('other_user_send_denied') and output['sse']['connected']
                        and output['sse']['database_ready'] and not output['sse'].get('error_type')
                        and 'refresh' in output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Run authorized post-deploy smoke')
    parser.add_argument('--base-url')
    parser.add_argument('--seconds', type=int, choices=range(20, 46), default=30)
    args = parser.parse_args()
    if not args.run:
        parser.error('Pass --run only after deployment')
    try:
        result = run(args)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result['passed'] else 1)
    except SmokeError as error:
        print(json.dumps({'error': str(error), 'passed': False}))
        raise SystemExit(1)
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'passed': False}))
        raise SystemExit(1)

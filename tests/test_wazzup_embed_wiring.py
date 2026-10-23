"""Проводка входа в окно «Чатов ОП» в CRM в монолите: область сессии в токенах, отказ вне чатов,
без кук, вход паролем в окне, пометка сессии из CRM (режим «crm», владелец 23.10.2026).
Настоящие функции bot_schedule2.py — без импорта приложения."""
import json
import types
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, g, jsonify, request

from tests.test_wazzup_chat_access import BOT_SOURCE, _load
from wazzup import embed_auth

# Подпись тут не проверяется — только поля токена; PyJWT для этого не нужен.
FAKE_JWT = types.SimpleNamespace(encode=lambda payload, key, algorithm: json.dumps(payload))


def builders():
    namespace = {'datetime': datetime, 'timezone': timezone, 'timedelta': timedelta, 'jwt': FAKE_JWT,
                 'JWT_SECRET': 'synthetic', 'JWT_ALGORITHM': 'HS256',
                 'JWT_ACCESS_TOKEN_MINUTES': 30, 'JWT_REFRESH_TOKEN_DAYS': 30}
    return _load(('_build_access_token', '_build_refresh_token'), namespace)


def decode(token):
    return json.loads(token)


def test_window_tokens_carry_the_scope_and_ordinary_ones_do_not():
    ns = builders()
    user = (42, None, 'Иванова Айгерим', 'operator')
    assert decode(ns['_build_access_token'](user, 'sid-1', scope=embed_auth.SCOPE))['scp'] == embed_auth.SCOPE
    assert decode(ns['_build_refresh_token'](42, 'sid-1', scope=embed_auth.SCOPE))['scp'] == embed_auth.SCOPE
    assert 'scp' not in decode(ns['_build_access_token'](user, 'sid-1'))
    assert 'scp' not in decode(ns['_build_refresh_token'](42, 'sid-1'))


def section(start, end):
    first = BOT_SOURCE.index(start)
    return BOT_SOURCE[first:BOT_SOURCE.index(end, first + len(start))]


def test_scope_survives_refresh_and_reaches_the_request():
    refresh = section('def _authenticate_refresh_cookie(', '\ndef ')
    assert 'scope = payload.get("scp") or None' in refresh
    assert '_build_access_token(user, session_id, scope=scope)' in refresh
    assert '_build_refresh_token(user_id, session_id, scope=scope)' in refresh
    assert 'g.auth_scope = scope' in refresh
    access = section('def _authenticate_access_cookie(', '\ndef ')
    assert 'g.auth_scope = payload.get("scp") or None' in access
    # И сессия, которой запрос подтвердился: проверка окна CRM спрашивает именно о ней.
    assert 'g.auth_session_id = session_id' in refresh and 'g.auth_session_id = session_id' in access
    own = section('def _request_session_id(', '\ndef ')
    assert "getattr(g, 'auth_session_id', None)" in own
    assert 'return session_id or _current_session_id_from_access_token()' in own


def test_window_session_is_refused_outside_chats_with_403_not_401():
    ns = _load(('enforce_embed_session_scope',), {
        'wazzup_embed_auth': embed_auth, 'g': g, 'jsonify': jsonify,
        'request': None, 'app': types.SimpleNamespace(before_request=lambda fn: fn)})
    app = Flask(__name__)
    for path, scope, expected in (('/api/admin/users', embed_auth.SCOPE, 403), ('/api/wazzup/chats', embed_auth.SCOPE, None),
                                  ('/api/admin/users', None, None)):
        with app.test_request_context(path, method='GET'):
            from flask import request
            ns['request'] = request
            g.auth_scope = scope
            result = ns['enforce_embed_session_scope']()
            status = result[1] if result else None
            assert status == expected, (path, scope)
            if result:
                assert result[0].get_json()['code'] == 'EMBED_SCOPE'
    # Вторая линия — в проверке личности, через которую идёт каждая ручка за require_auth.
    assert 'wazzup_embed_auth.scope_refusal(getattr(g, \'auth_scope\', None), request.path)' in section(
        'def _validate_authenticated_identity_header(', '\ndef ')


def test_hook_runs_after_the_request_is_identified():
    assert BOT_SOURCE.index('def hydrate_user_context_from_jwt(') < BOT_SOURCE.index('def enforce_embed_session_scope(')
    hook = section('@app.before_request\ndef enforce_embed_session_scope(', '\ndef ')
    assert "request.method != 'OPTIONS'" in hook


def test_window_sessions_never_get_cookies():
    after = section("    pending_tokens = getattr(g, 'pending_auth_tokens', None)\n    if pending_tokens:", 'return response')
    assert "if getattr(g, 'auth_scope', None) != wazzup_embed_auth.SCOPE:" in after
    issue = section('def _issue_embed_session_response(', '\n# Вход в окно')
    assert '_set_auth_cookies' not in issue
    assert 'scope=scope' in issue
    # «Без QR из CRM»: чаты этой сессии открыты сразу.
    assert 'INSERT INTO wazzup_chat_access (session_id, user_id)' in issue
    login = section("@app.route('/api/login', methods=['POST', 'OPTIONS'])", "@app.route('/api/auth/me'")
    assert "scope = wazzup_embed_auth.SCOPE if data.get('embed') is True else None" in login
    assert 'if not scope:\n                _set_auth_cookies(response, access_token, refresh_token)' in login


def test_password_sign_in_in_the_window_is_a_crm_session_only_with_the_crm_signature():
    login = section("@app.route('/api/login', methods=['POST', 'OPTIONS'])", "@app.route('/api/auth/me'")
    assert "crm_user_id = wazzup_embed_auth.signed_crm_user(data.get('crm')) if scope else None" in login
    assert ("if crm_user_id:\n                with db._get_cursor() as cursor:\n"
            "                    wazzup_embed_auth.mark_crm_session(cursor, session_id, user[0], crm_user_id, 'password')"
            ) in login
    # Пометка — после самой сессии, профиль — уже для неё.
    assert login.index('db.create_user_session(') < login.index('mark_crm_session(')
    prepared = login.index('g.auth_session_id = session_id')
    assert login.index('g.auth_scope = scope') < prepared < login.index('payload = _get_user_payload(user_profile)')
    assert "g.pop('_wazzup_chat_access', None)" in login


def test_link_session_is_marked_as_crm_and_the_profile_is_built_for_it():
    calls, seen = [], {}

    class Cursor:
        def execute(self, sql, params=()):
            calls.append((' '.join(sql.split()), params))

    class Db:
        def create_user_session(self, **kwargs):
            calls.append(('session', kwargs['session_id'], kwargs['user_id']))

        @contextmanager
        def _get_cursor(self):
            yield Cursor()

    def payload(user):
        seen.update(scope=g.auth_scope, session=g.auth_session_id, stale=hasattr(g, '_wazzup_chat_access'),
                    sessions=[call for call in calls if call[0] == 'session'])
        return {'id': user[0]}

    ns = _load(('_issue_embed_session_response',), {
        'uuid': uuid, 'wazzup_embed_auth': embed_auth, 'db': Db(), 'g': g, 'request': request,
        'jsonify': jsonify, 'datetime': datetime, 'timedelta': timedelta, 'JWT_REFRESH_TOKEN_DAYS': 30,
        '_build_access_token': lambda user, sid, scope=None: f'a:{sid}:{scope}',
        '_build_refresh_token': lambda user_id, sid, scope=None: f'r:{sid}:{scope}',
        '_hash_token': lambda token: 'h', '_client_ip': lambda: '203.0.113.7',
        '_set_request_auth_context': lambda user_id, user=None: setattr(g, 'user_id', user_id),
        '_build_auth_response_payload': lambda user, transport, access_token, refresh_token: {
            'user': user, 'access_token': access_token},
        '_get_user_payload': payload, 'JWT_AUTH_TRANSPORT_HEADER_NAME': 'X-Auth-Transport',
    })
    app = Flask(__name__)
    with app.test_request_context('/api/auth/embed-launch', method='POST'):
        g._wazzup_chat_access = {'mode': None}  # посчитанное до выдачи — не для новой сессии
        response, status = ns['_issue_embed_session_response']((42, None, 'Иванова Айгерим', 'admin'),
                                                               'bearer', '352')
        assert status == 200 and response.get_json()['access_token'].endswith(':' + embed_auth.SCOPE)
    session_id = seen['session']
    inserts = {call[0].split(' (')[0]: call[1] for call in calls
               if len(call) == 2 and call[0].startswith('INSERT')}
    assert inserts['INSERT INTO wazzup_chat_access'] == (session_id, 42), 'по ссылке — без QR'
    assert inserts['INSERT INTO wazzup_crm_sessions'] == (session_id, 42, '352', 'launch')
    assert (seen['scope'], seen['stale']) == (embed_auth.SCOPE, False)
    assert seen['sessions'] == [('session', session_id, 42)]


def test_blueprint_is_wired_with_the_shared_rules():
    wiring = section('app.register_blueprint(wazzup_embed_auth.build_embed_blueprint(', '))')
    for piece in ('portal_base_url=TASK_WEB_APP_BASE_URL', 'load_user=lambda user_id: db.get_user(id=user_id)',
                  'issue_session=_issue_embed_session_response', 'client_ip=_client_ip'):
        assert piece in wiring, piece
    # Раздел портала ссылке больше не нужен: в окне CRM пишет каждый, кого CRM открыла.
    assert 'chat_mode' not in wiring and 'def _wazzup_chat_mode_for(' not in BOT_SOURCE


@pytest.mark.parametrize('path', ['/api/wazzup/embed/launch', '/api/auth/embed-launch'])
def test_routes_exist_once(path):
    source = (embed_auth.__file__ and open(embed_auth.__file__, encoding='utf-8').read())
    assert source.count(f"@bp.route('{path}'") == 1

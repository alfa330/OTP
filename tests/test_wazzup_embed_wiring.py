"""Проводка входа в окно «Чатов ОП» в CRM в монолите: область сессии в токенах, отказ вне чатов,
без кук, вход паролем в окне. Настоящие функции bot_schedule2.py — без импорта приложения."""
import json
import types
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, g, jsonify

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


def test_blueprint_is_wired_with_the_shared_rules():
    wiring = section('app.register_blueprint(wazzup_embed_auth.build_embed_blueprint(', '))')
    for piece in ('portal_base_url=TASK_WEB_APP_BASE_URL', 'chat_mode=_wazzup_chat_mode_for',
                  'issue_session=_issue_embed_session_response', 'client_ip=_client_ip'):
        assert piece in wiring, piece
    mode = section('def _wazzup_chat_mode_for(', '\ndef ')
    assert "return _wazzup_chat_access().get('mode')" in mode
    assert "g.pop('_wazzup_chat_access', None)" in mode


@pytest.mark.parametrize('path', ['/api/wazzup/embed/launch', '/api/auth/embed-launch'])
def test_routes_exist_once(path):
    source = (embed_auth.__file__ and open(embed_auth.__file__, encoding='utf-8').read())
    assert source.count(f"@bp.route('{path}'") == 1

# -*- coding: utf-8 -*-
"""Заглушка портала и АТС: прогнать клиент целиком, не трогая ни прод, ни офис.

Поднимает на localhost то, чего клиенту не хватает для жизни:

  * ручки iCORE — /api/login, /api/operator/oktell_account, /api/news/pending,
    /api/news/<id>/read. Правила подтверждения повторены НАРОЧНО: выдержка
    кнопки и проверка ответов теста живут на сервере, и клиент обязан
    спотыкаться о них так же, как о боевые;
  * поддельную страницу Oktell с формой входа — на ней проверяется подстановка
    учётки;
  * веб-сокет, который шлёт кадры статуса в том же виде, в каком их шлёт
    настоящий Oktell (["getuserstateresult", {"userstatestr": …}]). На нём
    проверяется правило «во время разговора окно ждёт».

Запуск: python3 dev_harness/stub_server.py  (порты 8770 и 8771).
Никаких зависимостей: стандартная библиотека, включая рукописный веб-сокет —
ставить пакет ради тридцати строк кадрирования в стенде незачем.
"""
import base64
import hashlib
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HTTP_PORT = 8770
WS_PORT = 8771

TOKEN = 'stub-access-token'
REFRESH = 'stub-refresh-token'
USER = {'id': 41, 'name': 'Оператор Стендовый', 'role': 'operator'}
CABINET = {'cabinet_login': '6612', 'cabinet_password': 'atc-secret'}

# Выдержка кнопки и тест — как у боевой новости: два вопроса, верный ответ
# известен только здесь.
NEWS_ID = 501
CONFIRM_DELAY = 5
QUIZ = [
    {'id': 9001, 'prompt': 'Куда звонить при аварии на линии?',
     'options': ['В поддержку', 'Руководителю смены', 'Никуда'], 'correct': 1},
    {'id': 9002, 'prompt': 'Сколько ждать ответа клиента?',
     'options': ['10 секунд', '30 секунд'], 'correct': 1},
]

state = {'shown_at': None, 'confirmed': False}
# Команды, которые клиент прислал АТС: кадры сокета и обращения к
# wp_setuserstate. По ним проверяется, что оператора сняли с линии ДО показа.
RECEIVED = []

# Поддельный дистрибутив: содержимое произвольное, важен только его sha256 —
# именно по нему клиент решает, ставить файл или выбросить.
INSTALLER = b'FAKE-INSTALLER-' + b'x' * 512
INSTALLER_SHA = hashlib.sha256(INSTALLER).hexdigest()
RELEASE = {'version': '9.9.9', 'filename': 'iCORE-Oktell-Setup-9.9.9.exe',
           'sha256': INSTALLER_SHA, 'size': len(INSTALLER), 'mandatory': False,
           'notes': '', 'published_at': None}
# Переключатель для проверки отказа: /stub/break_hash подсовывает чужой хэш.
BROKEN = {'hash': False}


def news_item():
    remaining = 0
    if state['shown_at'] is not None:
        remaining = max(0, int(CONFIRM_DELAY - (time.time() - state['shown_at'])))
    return {
        'id': NEWS_ID,
        'title': 'Новые правила обработки обращений',
        'body': '<p>С понедельника обращения с пометкой <b>«срочно»</b> берём в работу '
                'в течение пяти минут.</p><ul><li>Проверить статус водителя</li>'
                '<li>Оставить комментарий в карточке</li></ul>',
        'is_mandatory': True,
        'confirm_delay_seconds': CONFIRM_DELAY,
        'shown_at': None,
        'remaining_seconds': remaining,
        'photos': [],
        'quiz': [{'id': q['id'], 'prompt': q['prompt'], 'options': q['options']} for q in QUIZ],
        'pass_required': True,
        'trainer_key': None,
        'quiz_passed': False,
        'trainer_passed': False,
    }


OKTELL_PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Oktell (стенд)</title>
<style>body{font:14px system-ui;padding:40px;background:#eef1f5}
form{max-width:320px;background:#fff;padding:24px;border-radius:12px}
input{width:100%%;padding:8px;margin:6px 0;box-sizing:border-box}
#state{margin-top:16px;font-weight:600}</style></head>
<body>
<form id="f"><h3>Вход в Oktell</h3>
<input name="login" placeholder="Логин">
<input name="password" type="password" placeholder="Пароль">
<button type="submit">Войти</button></form>
<div id="state">статус: —</div>
<script>
document.getElementById('f').addEventListener('submit', function (e) {
    e.preventDefault();
    document.body.innerHTML = '<h3>Вошли как ' + e.target.login.value + '</h3>'
        + '<div id="state">статус: —</div>' + document.body.innerHTML;
});
// Тот же сокет, что у настоящего клиента: наш скрипт оборачивает window.WebSocket
// и читает кадры статуса именно отсюда.
var ws = new WebSocket('ws://127.0.0.1:%(ws)d/');
ws.addEventListener('message', function (event) {
    var node = document.getElementById('state');
    if (node) node.textContent = 'статус: ' + event.data;
});
</script></body></html>
""" % {'ws': WS_PORT}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print('[stub] ' + fmt % args)

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except ValueError:
            return {}

    def do_GET(self):
        if self.path.startswith('/oktell'):
            body = OKTELL_PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith('/stub/break_hash'):
            BROKEN['hash'] = True
            return self._json({'status': 'ok'})
        if self.path.startswith('/api/oktell_client/version'):
            release = dict(RELEASE)
            if BROKEN['hash']:
                release['sha256'] = '0' * 64
            return self._json({'status': 'success', 'release': release})
        if self.path.startswith('/api/oktell_client/download'):
            return self._json({'status': 'success',
                               'url': f'http://127.0.0.1:{HTTP_PORT}/stub/installer'})
        if self.path.startswith('/stub/installer'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(INSTALLER)))
            self.end_headers()
            self.wfile.write(INSTALLER)
            return
        if self.path.startswith('/stub/commands'):
            return self._json({'commands': RECEIVED})
        if self.path.startswith('/wp_setuserstate'):
            RECEIVED.append({'kind': 'http', 'raw': self.path})
            print(f'[stub] АТС: {self.path}')
            return self._json({'status': 'ok'})
        if self.path.startswith('/api/operator/oktell_account'):
            return self._json({'status': 'success', 'account': CABINET})
        if self.path.startswith('/api/news/pending'):
            if state['confirmed']:
                return self._json({'items': [], 'schema_ready': True})
            if state['shown_at'] is None:
                state['shown_at'] = time.time()
            return self._json({'items': [news_item()], 'schema_ready': True})
        return self._json({'error': 'Not found'}, 404)

    def do_POST(self):
        payload = self._body()
        if self.path == '/api/login':
            if payload.get('login') and payload.get('password'):
                return self._json({'status': 'success', 'user': USER,
                                   'access_token': TOKEN, 'refresh_token': REFRESH,
                                   **USER})
            return self._json({'error': 'Invalid credentials'}, 401)
        if self.path == '/api/auth/refresh':
            return self._json({'status': 'success', 'user': USER, 'access_token': TOKEN})
        if self.path == f'/api/news/{NEWS_ID}/read':
            shown = state['shown_at'] or time.time()
            left = int(CONFIRM_DELAY - (time.time() - shown))
            if left > 0:
                return self._json({'error': 'Кнопка станет активной чуть позже',
                                   'code': 'NEWS_TOO_EARLY',
                                   'remaining_seconds': left}, 409)
            answers = payload.get('answers') or {}
            wrong = [q['id'] for q in QUIZ
                     if str(answers.get(str(q['id']), answers.get(q['id']))) != str(q['correct'])]
            if wrong:
                return self._json({'error': 'Есть неверные ответы — перечитайте новость',
                                   'code': 'NEWS_QUIZ_WRONG', 'wrong': wrong}, 409)
            state['confirmed'] = True
            return self._json({'status': 'ok'})
        return self._json({'error': 'Not found'}, 404)


GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


def ws_frame(text):
    """Серверный текстовый кадр без маски — ровно то, что ждёт браузер."""
    data = text.encode('utf-8')
    header = struct.pack('!BB', 0x81, len(data)) if len(data) < 126 else \
        struct.pack('!BBH', 0x81, 126, len(data))
    return header + data


def ws_read(conn):
    """Кадры ОТ клиента: команда смены статуса приходит именно так.

    Клиентские кадры маскированы (RFC 6455), поэтому четыре байта ключа
    обязательны — без размаскировки в журнале оказался бы мусор.
    """
    while True:
        header = conn.recv(2)
        if len(header) < 2:
            return
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack('!H', conn.recv(2))[0]
        elif length == 127:
            length = struct.unpack('!Q', conn.recv(8))[0]
        mask = conn.recv(4) if header[1] & 0x80 else b'\x00\x00\x00\x00'
        payload = bytearray(conn.recv(length) if length else b'')
        for index in range(len(payload)):
            payload[index] ^= mask[index % 4]
        if (header[0] & 0x0F) == 0x8:                 # close
            return
        text = payload.decode('utf-8', 'ignore')
        RECEIVED.append({'kind': 'socket', 'raw': text})
        print(f'[stub] кадр от клиента: {text}')


def ws_client(conn):
    """Рукопожатие и поток статусов: готов → разговор → готов, по кругу."""
    try:
        request = conn.recv(4096).decode('utf-8', 'ignore')
        key = ''
        for line in request.split('\r\n'):
            if line.lower().startswith('sec-websocket-key:'):
                key = line.split(':', 1)[1].strip()
        accept = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.send(('HTTP/1.1 101 Switching Protocols\r\n'
                   'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                   f'Sec-WebSocket-Accept: {accept}\r\n\r\n').encode())
        print('[stub] сокет статусов подключён')
        threading.Thread(target=ws_read, args=(conn,), daemon=True).start()
        for raw in _state_cycle():
            conn.send(ws_frame(json.dumps(['getuserstateresult',
                                           {'userstatestr': raw, 'userlogin': '6612'}])))
            print(f'[stub] статус -> {raw}')
            time.sleep(10)
    except Exception as error:                       # noqa: BLE001
        print(f'[stub] сокет закрылся: {error}')
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _state_cycle():
    while True:
        yield 'usReady'
        yield 'talk'
        yield 'usReady'


def serve_ws():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', WS_PORT))
    server.listen(5)
    print(f'[stub] сокет статусов на ws://127.0.0.1:{WS_PORT}/')
    while True:
        conn, _ = server.accept()
        threading.Thread(target=ws_client, args=(conn,), daemon=True).start()


if __name__ == '__main__':
    threading.Thread(target=serve_ws, daemon=True).start()
    print(f'[stub] портал и АТС на http://127.0.0.1:{HTTP_PORT}/ '
          f'(страница АТС — /oktell)')
    HTTPServer(('127.0.0.1', HTTP_PORT), Handler).serve_forever()

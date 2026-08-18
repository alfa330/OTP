"""
Стенд для проверки агента без прода и без живого Oktell.

Поднимает три вещи в одном процессе:
  1. фальшивый веб-клиент Oktell — экран входа + «рабочий стол», сессия лежит
     в localStorage и cookie под тем же ключом ___oktellsessionid, при входе
     открывается WebSocket (как настоящий клиент);
  2. WebSocket-сервер, который печатает всё, что прислал клиент — так видно,
     реально ли агент отправил штатный кадр ["logout",{}];
  3. заглушку серверного API OTP: /api/oktell_guard/heartbeat и /ack.

Запуск:
    python mock_server.py            (HTTP 8799, WS 8800)

Поставить команду в очередь агенту:
    curl "http://127.0.0.1:8799/queue?type=logout&reason=recall_timeout"
    curl "http://127.0.0.1:8799/queue?type=warn&seconds=10"

Посмотреть, что дошло:
    curl http://127.0.0.1:8799/state
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HTTP_PORT = 8799
WS_PORT = 8800
RUN_ID = str(int(time.time()))

STATE: dict = {
    "heartbeats": [],
    "acks": [],
    "ws_frames": [],
    "queue": [],
    "command_seq": 0,
}
LOCK = threading.Lock()

PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Oktell (стенд)</title>
<style>
  body {{ font: 15px/1.4 -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0;
         display: grid; place-items: center; height: 100vh; background: #10131a; color: #eef1f6; }}
  .card {{ background: #1a1f2b; padding: 28px 32px; border-radius: 16px; min-width: 320px; }}
  h1 {{ font-size: 18px; margin: 0 0 16px; }}
  input, button {{ font: inherit; padding: 8px 12px; border-radius: 8px; border: 1px solid #2f3646;
                   background: #11151d; color: inherit; width: 100%; margin-bottom: 10px; }}
  button {{ background: #2f6feb; border-color: #2f6feb; cursor: pointer; }}
  .muted {{ opacity: .6; font-size: 13px; }}
</style>
</head>
<body>
<div class="card" id="root"></div>
<script>
var KEY = '___oktellsessionid';
function session() {{
  try {{ return localStorage.getItem(KEY); }} catch (e) {{ return null; }}
}}
function render() {{
  var root = document.getElementById('root');
  if (session()) {{
    root.innerHTML = '<h1>Рабочее место оператора</h1>' +
      '<div class="muted">Сессия: <span id="sid"></span></div>' +
      '<div class="muted" id="ws">сокет: —</div>';
    document.getElementById('sid').textContent = session();
    connect();
  }} else {{
    root.innerHTML = '<h1>Вход в систему</h1>' +
      '<input id="login" placeholder="Логин" value="6612">' +
      '<input id="password" type="password" placeholder="Пароль">' +
      '<button id="go">Войти</button>';
    document.getElementById('go').onclick = function () {{
      var sid = 'sid-' + Math.random().toString(16).slice(2);
      localStorage.setItem(KEY, sid);
      document.cookie = KEY + '=' + sid + '; path=/';
      window.appLogin = document.getElementById('login').value;
      render();
    }};
  }}
}}
function connect() {{
  try {{
    var ws = new WebSocket('ws://127.0.0.1:{ws_port}/');
    ws.onopen = function () {{
      document.getElementById('ws').textContent = 'сокет: открыт';
      ws.send(JSON.stringify(['ping', {{ qid: 1 }}]));
    }};
    ws.onclose = function () {{
      var el = document.getElementById('ws');
      if (el) {{ el.textContent = 'сокет: закрыт'; }}
    }};
  }} catch (e) {{}}
}}
render();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # тише в консоли
        sys.stderr.write("[http] " + fmt % args + "\n")

    def _send(self, code: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/fake-oktell", "/fake-oktell/"):
            self._send(200, PAGE.format(ws_port=WS_PORT).encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/queue":
            # BaseHTTPRequestHandler отдаёт path в latin-1 — без этого разворота
            # кириллица в message приезжает крякозябрами.
            query = parsed.query.encode("latin-1", "ignore").decode("utf-8", "replace")
            params = parse_qs(query)
            with LOCK:
                STATE["command_seq"] += 1
                command = {
                    # id уникален в пределах запуска стенда: агент помнит
                    # исполненные команды между перезапусками, и «cmd-1» из
                    # прошлого прогона он бы законно проигнорировал.
                    # ?id=... позволяет проверить идемпотентность: повторная
                    # выдача той же команды не должна приводить ко второму разлогину.
                    "id": (params.get("id") or [f"cmd-{RUN_ID}-{STATE['command_seq']}"])[0],
                    "type": (params.get("type") or ["logout"])[0],
                    "reason": (params.get("reason") or ["recall_timeout"])[0],
                }
                if command["type"] == "warn":
                    command["seconds"] = int((params.get("seconds") or ["30"])[0])
                    command["message"] = (params.get("message") or ["Перезвон длится дольше нормы"])[0]
                STATE["queue"].append(command)
            self._json(200, {"queued": command})
            return
        if parsed.path == "/state":
            with LOCK:
                self._json(200, json.loads(json.dumps(STATE, ensure_ascii=False)))
            return
        if parsed.path == "/reset":
            with LOCK:
                STATE["heartbeats"].clear()
                STATE["acks"].clear()
                STATE["ws_frames"].clear()
                STATE["queue"].clear()
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {"_raw": raw.decode("utf-8", "replace")}

        if parsed.path.endswith("/heartbeat"):
            with LOCK:
                STATE["heartbeats"].append({"at": time.time(), "token": self.headers.get("X-Agent-Token"), "payload": payload})
                commands = list(STATE["queue"])
                STATE["queue"].clear()
            self._json(200, {"ok": True, "poll_interval_s": 3, "commands": commands})
            return
        if parsed.path.endswith("/ack"):
            with LOCK:
                STATE["acks"].append({"at": time.time(), "payload": payload})
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})


# --------------------------------------------------------------------------- #
# Минимальный WebSocket-сервер: только рукопожатие и чтение кадров клиента
# --------------------------------------------------------------------------- #

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_handle(conn: socket.socket) -> None:
    try:
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = conn.recv(4096)
            if not chunk:
                return
            request += chunk
        key_match = re.search(rb"Sec-WebSocket-Key:\s*(\S+)", request, re.IGNORECASE)
        if not key_match:
            return
        accept = base64.b64encode(hashlib.sha1(key_match.group(1) + WS_GUID).digest())
        response = (
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n"
        )
        proto = re.search(rb"Sec-WebSocket-Protocol:\s*([^\r\n]+)", request, re.IGNORECASE)
        if proto:
            response += b"Sec-WebSocket-Protocol: " + proto.group(1).split(b",")[0].strip() + b"\r\n"
        conn.sendall(response + b"\r\n")

        while True:
            header = conn.recv(2)
            if len(header) < 2:
                return
            opcode = header[0] & 0x0F
            masked = bool(header[1] & 0x80)
            length = header[1] & 0x7F
            if length == 126:
                length = struct.unpack(">H", conn.recv(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", conn.recv(8))[0]
            mask = conn.recv(4) if masked else b""
            data = b""
            while len(data) < length:
                part = conn.recv(length - len(data))
                if not part:
                    return
                data += part
            if masked:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:  # close
                return
            if opcode in (0x1, 0x2):
                text = data.decode("utf-8", "replace")
                with LOCK:
                    STATE["ws_frames"].append({"at": time.time(), "data": text})
                print(f"[ws] <- {text}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[ws] ошибка: {exc}", flush=True)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def ws_server() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", WS_PORT))
    server.listen(8)
    print(f"[ws] слушаю ws://127.0.0.1:{WS_PORT}/", flush=True)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=ws_handle, args=(conn,), daemon=True).start()


def main() -> int:
    threading.Thread(target=ws_server, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler)
    print(f"[http] слушаю http://127.0.0.1:{HTTP_PORT}/ (страница: /fake-oktell/)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

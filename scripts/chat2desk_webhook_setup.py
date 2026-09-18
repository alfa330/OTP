"""Регистрация вебхука Chat2Desk: посмотреть, завести, удалить.

Зачем отдельный скрипт, а не ручка в разделе: вебхук заводится один раз на компанию
и живёт сам по себе — кнопка в интерфейсе для такого лишняя, а случайное нажатие
переключило бы источник данных табло всем сразу.

Вендор разрешает до пяти вебхуков бесплатно. Нам нужен один — со всеми событиями, из
которых собирается лента чата: сообщения, заметки, обращения и статусы чатников.
Подписываемся шире, чем сейчас читаем: пропущенное событие не восстановить, а лишнее
стоит строки в таблице с недельным ретеншном.

Каждый вызов тратит квоту (на 18.09.2026 бесплатный пул компании уже нулевой),
поэтому по умолчанию скрипт только показывает, что заведено.

    python scripts/chat2desk_webhook_setup.py                # показать
    python scripts/chat2desk_webhook_setup.py --create       # завести
    python scripts/chat2desk_webhook_setup.py --delete 5518  # удалить

`--create` берёт адрес из CHAT2DESK_WEBHOOK_URL (или --url) и секрет из
CHAT2DESK_WEBHOOK_TOKEN — тот же, что проверяет приёмник в bot_schedule2.
"""

import argparse
import json
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(os.path.join(ROOT, ".env.codex.local"))

# Список событий взят из ЖИВОЙ документации (documenter.getpostman.com/view/8899980/UVC8BRBo),
# а не из PDF-мануала 1.58: в PDF нет ни new_request, ни comment, ни imported_message, ни
# статусов операторов.
#   inbox/outbox       — сообщения клиента и оператора: из них считаются чаты и время ответа;
#   imported_message   — сообщения, пришедшие напрямую из мессенджера или через ручку импорта.
#                        ОНИ НЕ ПОДНИМАЮТ inbox/outbox, и без подписки часть переписки просто
#                        не придёт, а недостача будет выглядеть как спад объёма;
#   comment            — внутренняя заметка оператора: её показывает лента «Чатов водителей»;
#   system_message     — служебные строки диалога («чат закрыт»), тоже видны в ленте;
#   new_request        — единственный источник ТИПА обращения (common/rating): без него
#                        автоопрос оценки приходится отличать по составу ленты;
#   close_request      — обращение закрыто, до этого оно на табло «открытое»;
#   dialog_transferred — сменился оператор: без него у переданного чата не будет имени;
#   operator_status_changed — статус чатника. Подписываемся сразу, хотя табло пока считает
#                        статусы опросом: события копятся, и переключение будет на данных, а
#                        не на вере. Имя события в теле приходит как operator_status_updated —
#                        приёмник принимает оба.
EVENTS = [
    "inbox", "outbox", "imported_message", "comment", "system_message",
    "new_request", "close_request", "dialog_transferred", "operator_status_changed",
]
WEBHOOK_NAME = "OTP wallboard"


def _base_url():
    return (os.getenv("CHAT2DESK_API_BASE_URL") or "https://api-02.chat2desk.kz").rstrip("/")


def _headers():
    token = (os.getenv("CHAT2DESK_API_TOKEN") or "").strip()
    if not token:
        sys.exit("CHAT2DESK_API_TOKEN не задан — смотри .env.codex.local")
    return {"Authorization": token, "Accept": "application/json"}


def _call(method, path, body=None):
    response = requests.request(method, f"{_base_url()}{path}", headers=_headers(),
                                json=body, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:500]}
    if response.status_code >= 400:
        sys.exit(f"HTTP {response.status_code}: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def show():
    payload = _call("GET", "/v1/webhooks")
    items = payload.get("data") or []
    if not items:
        print("вебхуков нет")
        return
    for item in items:
        print(f"id={item.get('id')} status={item.get('status')} url={item.get('url')}")
        print(f"    события: {', '.join(item.get('events') or [])}")
        if item.get("errors"):
            print(f"    ошибки:  {item['errors']}")


def create(url):
    token = (os.getenv("CHAT2DESK_WEBHOOK_TOKEN") or "").strip()
    if not token:
        sys.exit("CHAT2DESK_WEBHOOK_TOKEN не задан: приёмник без него отвечает 404")
    if not url:
        sys.exit("нужен адрес: --url или CHAT2DESK_WEBHOOK_URL")
    if token not in url:
        # Секрет в пути — основной замок приёмника (подпись X-Signature идёт поверх).
        sys.exit("в адресе нет секретного сегмента — вендор будет получать 404")
    payload = _call("POST", "/v1/webhooks",
                    {"name": WEBHOOK_NAME, "url": url, "events": EVENTS})
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def delete(webhook_id):
    print(json.dumps(_call("DELETE", f"/v1/webhooks/{int(webhook_id)}"),
                     ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true", help="завести вебхук")
    parser.add_argument("--url", default=os.getenv("CHAT2DESK_WEBHOOK_URL"),
                        help="адрес приёмника вместе с секретным сегментом пути")
    parser.add_argument("--delete", metavar="ID", help="удалить вебхук по id")
    args = parser.parse_args()
    if args.delete:
        delete(args.delete)
    elif args.create:
        create(args.url)
    else:
        show()


if __name__ == "__main__":
    main()

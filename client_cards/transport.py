# -*- coding: utf-8 -*-
"""Отправка запросов и алертов в Telegram.

Поверх транспорта «Обращений» (crm/transport.py): токен, адрес API и чистка ошибок от
токена — оттуда. Своего здесь два отличия:

* **Видео уходит видео, а не файлом** (sendVideo): запрос п. 7 задачи #335 — «фото, видео
  и ссылки», и ролик с экрана водителя в группе должен открываться нажатием. Фото крупнее
  10 МБ Telegram картинкой не берёт — такое уходит документом.
* **Ошибка помнит, куда переехала группа.** Обычная группа, ставшая супергруппой, меняет id
  навсегда; Telegram отвечает отказом и называет новый id (parameters.migrate_to_chat_id).
  Он лежит в TelegramError.migrate_to — сервис переносит адресата и повторяет отправку.

Все функции возвращают (result, error) и не бросают исключений: недоступный Telegram не
должен ронять создание запроса — он остаётся «не доставлен» и отправляется повторно.
"""

import io
import logging
import re

import requests

from crm import transport as crm_transport

from . import files

DEFAULT_TIMEOUT = crm_transport.DEFAULT_TIMEOUT
FILE_TIMEOUT = 180            # ролик в десятки мегабайт уходит не за 15 секунд
PHOTO_MAX_BYTES = 10 * 1024 * 1024
CAPTION_LIMIT = crm_transport.CAPTION_LIMIT

fetch_file = crm_transport.fetch_file
set_message_reaction = crm_transport.set_message_reaction


class TelegramError(str):
    """Текст отказа Telegram; migrate_to — новый id чата, если группа стала супергруппой."""

    migrate_to = None

    def __new__(cls, text, migrate_to=None):
        obj = super().__new__(cls, text)
        obj.migrate_to = migrate_to
        return obj


def _failure(payload, status_code):
    parameters = payload.get('parameters') if isinstance(payload, dict) else None
    migrate_to = (parameters or {}).get('migrate_to_chat_id')
    text = (payload.get('description') if isinstance(payload, dict) else None) or ('HTTP %s' % status_code)
    return TelegramError(text, int(migrate_to) if migrate_to else None)


def _post(method, *, json_payload=None, data=None, files_payload=None, timeout=DEFAULT_TIMEOUT):
    token = crm_transport._token()
    if not token:
        return None, TelegramError('BOT_TOKEN не настроен')
    try:
        response = requests.post('%s/bot%s/%s' % (crm_transport.API_ROOT, token, method),
                                 json=json_payload, data=data, files=files_payload, timeout=timeout)
        payload = response.json()
    except Exception as error:  # noqa: BLE001 — наружу отдаём текст, не исключение
        logging.warning('client_cards: Telegram %s не отработал: %s', method, type(error).__name__)
        return None, TelegramError(crm_transport._error_text(error))
    if not isinstance(payload, dict) or not payload.get('ok'):
        return None, _failure(payload, response.status_code)
    return payload.get('result') or {}, None


def send_message(chat_id, text, *, reply_to_message_id=None):
    """Сообщение с разметкой HTML. Разметку Telegram не принял — повтор без неё: текст
    запроса пишет человек, и одна угловая скобка не должна стоить запроса."""
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
        # Реплай на удалённое сообщение иначе отменяет всю отправку.
        payload['allow_sending_without_reply'] = True
    result, error = _post('sendMessage', json_payload=payload)
    if result is not None or getattr(error, 'migrate_to', None):
        return result, error
    payload['text'] = re.sub(r'<[^>]+>', '', text)
    payload.pop('parse_mode', None)
    return _post('sendMessage', json_payload=payload)


def send_file(chat_id, *, name, data, mime=None, kind=None, reply_to_message_id=None, caption=None):
    """Файл в группу: фото — картинкой, видео — видео, остальное — документом."""
    kind = kind or files.file_kind(name, mime)
    if kind == files.KIND_PHOTO and len(data) <= PHOTO_MAX_BYTES:
        method, field = 'sendPhoto', 'photo'
    elif kind == files.KIND_VIDEO:
        method, field = 'sendVideo', 'video'
    else:
        method, field = 'sendDocument', 'document'
    form = {'chat_id': chat_id}
    if method == 'sendVideo':
        form['supports_streaming'] = 'true'
    if reply_to_message_id:
        form['reply_to_message_id'] = reply_to_message_id
        form['allow_sending_without_reply'] = 'true'
    if caption:
        form['caption'] = caption[:CAPTION_LIMIT]
    result, error = _post(method, data=form, timeout=FILE_TIMEOUT,
                          files_payload={field: (name, io.BytesIO(data), mime or 'application/octet-stream')})
    if result is None and method != 'sendDocument' and not getattr(error, 'migrate_to', None):
        # Telegram не принял файл как фото или видео (формат, размеры) — пусть уйдёт как есть.
        form.pop('supports_streaming', None)
        return _post('sendDocument', data=form, timeout=FILE_TIMEOUT,
                     files_payload={'document': (name, io.BytesIO(data), mime or 'application/octet-stream')})
    return result, error


def result_file_id(result):
    """file_id отправленного файла из ответа Telegram (фото приходит списком размеров)."""
    result = result or {}
    photo = result.get('photo')
    if isinstance(photo, list) and photo:
        return photo[-1].get('file_id')
    for key in ('video', 'document', 'animation', 'audio'):
        item = result.get(key)
        if isinstance(item, dict) and item.get('file_id'):
            return item['file_id']
    return None

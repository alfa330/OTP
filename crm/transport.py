"""Транспорт в Telegram для раздела «Обращения».

Тонкая обёртка над Bot API поверх requests — тем же способом, каким уходят
заявки в IT (bot_schedule2._tg_send_message). Своя, а не общая, ровно по одной
причине: обращению нужны reply_to_message_id и inline-клавиатура, которых у
общего помощника нет, а расширять его ради одного раздела значило бы менять
поведение уже работающих заявок.

Все функции возвращают (result, error) и НИКОГДА не бросают исключение:
недоступный Telegram не должен ронять создание обращения — запись остаётся с
delivery_status='failed' и отправляется повторно.
"""

import logging
import os

import requests

import log_secrets

API_ROOT = 'https://api.telegram.org'
DEFAULT_TIMEOUT = 15
# Файл из переписки может быть крупным; на скачивание даём больше времени.
FILE_TIMEOUT = 60


def _error_text(error):
    """Текст ошибки без токена бота.

    Токен по контракту Bot API лежит в ПУТИ адреса, а requests кладёт полный
    путь в текст исключения при любом отказе соединения. Отсюда текст уходит в
    crm_tickets.delivery_error и в JSON карточки обращения, то есть в браузер
    сотрудника. Чистим тем же модулем, что и логи (log_secrets).
    """
    return log_secrets.scrub('%s: %s' % (type(error).__name__, error))


def _token():
    return os.getenv('BOT_TOKEN')


def _call(method, *, json_payload=None, params=None, timeout=DEFAULT_TIMEOUT):
    token = _token()
    if not token:
        return None, 'BOT_TOKEN не настроен'
    try:
        url = '%s/bot%s/%s' % (API_ROOT, token, method)
        if json_payload is not None:
            response = requests.post(url, json=json_payload, timeout=timeout)
        else:
            response = requests.get(url, params=params or {}, timeout=timeout)
        data = response.json()
        if not data.get('ok'):
            return None, data.get('description') or ('HTTP %s' % response.status_code)
        return data.get('result') or {}, None
    except Exception as error:  # noqa: BLE001 — наружу отдаём текст, не исключение
        logging.warning('crm: Telegram %s не отработал: %s', method, error)
        return None, _error_text(error)


def send_message(chat_id, text, *, reply_to_message_id=None, parse_mode='HTML'):
    """Отправляет сообщение. При отказе разметки повторяет без неё.

    Повтор нужен потому, что текст обращения пишет человек: незакрытый «<»
    в описании проблемы Telegram отвергает целиком, и обращение потерялось бы
    из-за одной угловой скобки.
    """
    payload = {'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}
    if parse_mode:
        payload['parse_mode'] = parse_mode
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id
        # Реплай на удалённое сообщение иначе отменяет всю отправку.
        payload['allow_sending_without_reply'] = True

    result, error = _call('sendMessage', json_payload=payload)
    if result is not None or not parse_mode:
        return result, error

    import re
    payload['text'] = re.sub(r'<[^>]+>', '', text)
    payload.pop('parse_mode', None)
    return _call('sendMessage', json_payload=payload)


# Предел подписи к медиа у Telegram — 1024 символа, HTML-теги считаются тоже.
# Режем с запасом, как и текст сообщения.
CAPTION_LIMIT = 1000


def send_attachment(chat_id, *, file_name, stream, mimetype=None,
                    reply_to_message_id=None, caption=None, parse_mode=None):
    """Отправляет файл. С caption он уходит ОДНИМ сообщением вместе с текстом.

    Подпись к медиа ограничена 1024 символами (Telegram), и раньше текст
    обращения в неё не помещался — файл шёл вторым сообщением. После того как
    текст сократили, он туда влезает, и решение «одним или двумя» принимает
    вызывающий (crm.service): здесь только транспорт.
    """
    token = _token()
    if not token:
        return None, 'BOT_TOKEN не настроен'
    ext = os.path.splitext(str(file_name or ''))[1].lower()
    as_photo = ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')
    method = 'sendPhoto' if as_photo else 'sendDocument'
    field = 'photo' if as_photo else 'document'
    data = {'chat_id': chat_id}
    if reply_to_message_id:
        data['reply_to_message_id'] = reply_to_message_id
        data['allow_sending_without_reply'] = True
    if caption:
        data['caption'] = caption[:CAPTION_LIMIT]
        if parse_mode:
            data['parse_mode'] = parse_mode
    try:
        response = requests.post(
            '%s/bot%s/%s' % (API_ROOT, token, method),
            data=data,
            files={field: (file_name, stream, mimetype or 'application/octet-stream')},
            timeout=FILE_TIMEOUT,
        )
        payload = response.json()
        if not payload.get('ok'):
            return None, payload.get('description') or ('HTTP %s' % response.status_code)
        return payload.get('result') or {}, None
    except Exception as error:  # noqa: BLE001
        logging.warning('crm: вложение не ушло: %s', error)
        return None, _error_text(error)


def fetch_file(file_id):
    """Скачивает файл из переписки. Возвращает (bytes, error).

    Двухшаговый путь Bot API: getFile отдаёт временный путь, файл лежит по
    другому адресу. Ссылка короткоживущая, поэтому её нельзя сохранить в базе —
    раздел ходит сюда в момент открытия вложения.
    """
    result, error = _call('getFile', params={'file_id': file_id})
    if result is None:
        return None, error
    file_path = result.get('file_path')
    if not file_path:
        return None, 'Telegram не отдал путь к файлу'
    try:
        response = requests.get(
            '%s/file/bot%s/%s' % (API_ROOT, _token(), file_path),
            timeout=FILE_TIMEOUT,
        )
        if response.status_code != 200:
            return None, 'HTTP %s' % response.status_code
        return response.content, None
    except Exception as error:  # noqa: BLE001
        logging.warning('crm: файл не скачался: %s', error)
        return None, _error_text(error)

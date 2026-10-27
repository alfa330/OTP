"""Вызовы Telegram Bot API для «Кодов 2FA»: отправить, поправить, стереть сообщение.

Отдельный маленький клиент, а не общий _send_telegram_text_message портала: здесь нужны
id отправленного сообщения (чтобы стереть код через минуту) и правка/удаление, а ещё —
короткое терпение. Вызовы идут и из пула потоков бота (нажатия кнопок), где каждая
секунда ожидания держит одно из четырёх общих мест; поэтому таймаут короткий и без
повторов: не дошло — сервис запишет это в журнал, а не будет ждать.

Ни текста сообщения, ни кода в лог не пишем — только метод и ответ Telegram. Токен
бота — в адресе запроса, поэтому и адрес в лог не попадает.
"""

import logging

import requests

TIMEOUT_SECONDS = 8

# Ответы, после которых сообщение и так ушло из чата: удалять/править больше нечего.
_GONE = ('message to delete not found', 'message to edit not found', "message can't be deleted",
         'message is not modified')


class BotApi:
    def __init__(self, token, *, session=None, timeout=TIMEOUT_SECONDS):
        self._token = token
        self._session = session or requests
        self._timeout = timeout

    @property
    def configured(self):
        return bool(self._token)

    def _call(self, method, payload):
        """(ok, result, описание ошибки). Исключения сети — тоже ошибка, не падение."""
        if not self._token:
            return False, None, 'Бот не настроен (нет BOT_TOKEN)'
        try:
            response = self._session.post(
                'https://api.telegram.org/bot%s/%s' % (self._token, method), json=payload,
                timeout=self._timeout)
            data = response.json()
        except Exception as exc:  # noqa: BLE001 — сеть, таймаут, не-JSON
            logging.warning('twofa: Telegram %s не ответил: %s', method, type(exc).__name__)
            return False, None, 'Telegram не ответил'
        if data.get('ok'):
            return True, data.get('result'), None
        description = str(data.get('description') or 'ошибка Telegram')[:200]
        if not any(part in description.lower() for part in _GONE):
            logging.warning('twofa: Telegram %s: %s', method, description)
        return False, None, description

    def send(self, chat_id, text, markup=None):
        """(message_id, ошибка)."""
        payload = {'chat_id': int(chat_id), 'text': text, 'parse_mode': 'HTML',
                   'disable_web_page_preview': True}
        if markup:
            payload['reply_markup'] = markup
        ok, result, error = self._call('sendMessage', payload)
        if ok and isinstance(result, dict) and result.get('message_id'):
            return int(result['message_id']), None
        return None, error or 'Telegram не вернул сообщение'

    def edit(self, chat_id, message_id, text, markup=None):
        payload = {'chat_id': int(chat_id), 'message_id': int(message_id), 'text': text,
                   'parse_mode': 'HTML', 'disable_web_page_preview': True,
                   # Пустая клавиатура снимает кнопки: без поля Telegram оставил бы старые.
                   'reply_markup': markup or {'inline_keyboard': []}}
        ok, _result, error = self._call('editMessageText', payload)
        return ok or bool(error and 'not modified' in error.lower())

    def delete(self, chat_id, message_id):
        ok, _result, error = self._call('deleteMessage', {'chat_id': int(chat_id), 'message_id': int(message_id)})
        return ok or bool(error and any(part in error.lower() for part in _GONE))

    def answer(self, callback_id, text='', alert=False):
        self._call('answerCallbackQuery', {'callback_query_id': str(callback_id), 'text': text[:190],
                                           'show_alert': bool(alert)})

    def username(self):
        """Имя бота для ссылки «Открыть бота». None — не узнали (это не ошибка раздела)."""
        ok, result, _error = self._call('getMe', {})
        if ok and isinstance(result, dict):
            return result.get('username') or None
        return None

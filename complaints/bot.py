# -*- coding: utf-8 -*-
"""Обработчики бота для раздела «Жалобы».

Две точки входа из группы «Жалобы КЦ, регионы, таксопарк»:

    реплай на сообщение бота       → строка переписки жалобы (ответ водителю,
                                     вопрос оператору или внутреннее обсуждение)
    кнопка под жалобой             → приглашение написать ответ/вопрос, выбор итога

Регистрируются СРАЗУ после обработчиков «Обращений», и порядок здесь важен.
aiogram 2 идёт по обработчикам сверху вниз до первого подходящего. Фильтр
реплаев у обоих разделов один и тот же (группа + реплай + не команда): первым
его получает раздел «Обращения», не находит своей нити и отдаёт сообщение
дальше (SkipHandler) — сюда. Всё, что не наше и здесь, уходит дальше тем же
способом, а ниже по файлу бота — десятки обработчиков без фильтра по типу
чата, которые без ранней регистрации съели бы ответ из группы.
"""

import asyncio
import functools
import logging

from crm import telegram as crm_telegram
from crm import transport

from . import service, telegram

try:  # aiogram 2: вернуть сообщение следующему обработчику
    from aiogram.dispatcher.handler import SkipHandler
except Exception:  # pragma: no cover
    SkipHandler = None


def _is_group_reply(message):
    chat = getattr(message, 'chat', None)
    if getattr(chat, 'type', None) not in ('group', 'supergroup'):
        return False
    if getattr(message, 'reply_to_message', None) is None:
        return False
    text = crm_telegram.message_text(message) or ''
    return not text.startswith('/')


def _is_complaint_button(callback):
    return str(getattr(callback, 'data', '') or '').startswith(telegram.CALLBACK_PREFIX + ':')


def register(dp, db, pool, types_module):
    """Подключает обработчики раздела к диспетчеру. Зависимости — аргументами:
    bot_schedule2 сам подключает этот модуль, обратный импорт был бы циклом."""

    async def _run(func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(pool, functools.partial(func, *args, **kwargs))

    @dp.message_handler(_is_group_reply, content_types=types_module.ContentTypes.ANY)
    async def complaints_group_reply(message):
        try:
            accepted = await _run(
                service.ingest_group_reply, db,
                chat_id=message.chat.id,
                reply_to_message_id=message.reply_to_message.message_id,
                message=message,
            )
        except Exception:
            logging.exception('complaints: не удалось принять реплай из группы')
            return
        if accepted is None:
            if SkipHandler is not None:
                raise SkipHandler()
            return
        # Расписка — реакцией и только за то, что ушло оператору: ответ
        # водителю и вопрос. Внутреннее обсуждение бот молча сохраняет — 👍 под
        # ним читался бы как «передано оператору», а это ровно то, чего ТЗ
        # запрещает.
        if accepted.get('kind') in ('answer', 'question'):
            try:
                _result, error = await _run(transport.set_message_reaction, message.chat.id,
                                            message.message_id, telegram.RECEIPT_REACTION)
                if error:
                    logging.warning('complaints: реакция на ответ не поставилась: %s', error)
            except Exception:
                logging.exception('complaints: реакция на ответ не поставилась')

    @dp.callback_query_handler(_is_complaint_button)
    async def complaints_button(callback):
        text = None
        try:
            message = callback.message
            text = await _run(
                service.handle_callback, db,
                chat_id=message.chat.id, message_id=message.message_id,
                data=callback.data, from_user=callback.from_user,
            )
        except Exception:
            logging.exception('complaints: не удалось обработать кнопку')
            text = 'Не получилось — попробуйте ещё раз'
        # Отвечать на нажатие обязательно всегда: иначе у нажавшего крутится
        # часик на кнопке, пока Telegram сам не сдастся.
        try:
            await callback.answer(text or '', show_alert=False)
        except Exception:
            logging.debug('complaints: ответ на кнопку не ушёл', exc_info=True)

    logging.info('Раздел «Жалобы»: обработчики бота подключены')

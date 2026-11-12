# -*- coding: utf-8 -*-
"""Обработчик бота для «Карточки клиента»: ответ реплаем в группе отдела → переписка запроса.

Регистрируется СРАЗУ после обработчиков «Обращений» и «Жалоб», и порядок важен. aiogram 2
идёт по обработчикам сверху вниз до первого подходящего, а фильтр реплаев у всех трёх
разделов один (группа + реплай + не команда): «Обращения» получают реплай первыми, не
находят своей нити и отдают его дальше (SkipHandler) — «Жалобам», те — сюда. Всё, что не
наше и здесь, уходит дальше тем же способом; ниже по файлу бота — десятки обработчиков без
фильтра по типу чата, которые без ранней регистрации съели бы ответ из группы.

Бот в группе видит только адресованные ему сообщения (privacy mode Telegram), поэтому
ответ ловится ТОЛЬКО реплаем на сообщение бота — как и у «Обращений».
"""

import asyncio
import functools
import logging

from . import telegram, transport

try:  # aiogram 2: вернуть сообщение следующему обработчику
    from aiogram.dispatcher.handler import SkipHandler
except Exception:  # pragma: no cover
    SkipHandler = None


def _is_group_reply(message):
    """Реплай в группе — и не команда (командами реплаят в чате контроля опозданий)."""
    chat = getattr(message, 'chat', None)
    if getattr(chat, 'type', None) not in ('group', 'supergroup'):
        return False
    if getattr(message, 'reply_to_message', None) is None:
        return False
    text = telegram.message_text(message) or ''
    return not text.startswith('/')


def register(dp, service_getter, pool, types_module):
    """Подключает обработчик к диспетчеру.

    service_getter — функция, отдающая сервис раздела: обработчики бота регистрируются в
    начале bot_schedule2, а сам сервис собирается намного ниже по файлу. None — раздел не
    подключился: реплай уходит дальше по цепочке.
    """

    async def _run(func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(pool, functools.partial(func, *args, **kwargs))

    @dp.message_handler(_is_group_reply, content_types=types_module.ContentTypes.ANY)
    async def client_cards_group_reply(message):
        service = service_getter()
        accepted = None
        if service is not None:
            try:
                accepted = await _run(
                    service.ingest_group_reply,
                    chat_id=message.chat.id,
                    reply_to_message_id=message.reply_to_message.message_id,
                    message=message,
                )
            except Exception:
                logging.exception('client_cards: не удалось принять ответ из группы')
                return
        if accepted is None:
            # Ответили не на запрос — отдаём сообщение дальше, а не съедаем его.
            if SkipHandler is not None:
                raise SkipHandler()
            return
        # Расписка — реакцией на сообщение сотрудника, а не репликой в чат (crm/telegram.py).
        try:
            _result, error = await _run(transport.set_message_reaction, message.chat.id,
                                        message.message_id, telegram.REPLY_REACTION)
            if error:
                logging.warning('client_cards: реакция на ответ не поставилась: %s', error)
        except Exception:
            logging.exception('client_cards: реакция на ответ не поставилась')

    logging.info('Раздел «Карточка клиента»: обработчик бота подключён')

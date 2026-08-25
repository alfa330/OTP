"""Обработчики бота для раздела «Обращения».

Две точки входа из Telegram:

    ответ реплаем в группе      → сообщение ложится в нить обращения
    кнопка под обращением       → меняется статус, автору звонит колокол

Почему регистрация ОБЯЗАНА быть ранней. aiogram 2 перебирает обработчики в
порядке регистрации и останавливается на первом подходящем. В боте портала
десятки обработчиков вида message_handler(regexp='Часы⏱️') без фильтра по типу
чата — сообщение в группе, случайно совпавшее с такой подписью, ушло бы в
личный сценарий супервайзера вместо нити обращения. Поэтому register()
вызывается сразу после создания Dispatcher, до всех остальных.

Чужое не трогаем: фильтр требует группу, реплай и НЕ команду. Команды исключены
намеренно — реплаем в чате контроля опозданий шлют /report, и перехватывать его
здесь нельзя.
"""

import asyncio
import functools
import logging

from . import service, telegram, transport

try:  # aiogram 2: позволяет вернуть сообщение следующему обработчику
    from aiogram.dispatcher.handler import SkipHandler
except Exception:  # pragma: no cover — на случай другой версии
    SkipHandler = None


def _is_group_reply(message):
    """Реплай на сообщение бота в группе — и не команда."""
    chat = getattr(message, 'chat', None)
    if getattr(chat, 'type', None) not in ('group', 'supergroup'):
        return False
    if getattr(message, 'reply_to_message', None) is None:
        return False
    text = telegram.message_text(message) or ''
    return not text.startswith('/')


def register(dp, db, pool, types_module):
    """Подключает обработчики раздела к диспетчеру.

    dp, db, pool и types приходят снаружи, а не импортируются: bot_schedule2 сам
    подключает этот модуль, обратный импорт был бы циклом.
    """

    async def _run(func, *args, **kwargs):
        """Синхронная работа с базой — в пуле: обработчики бота асинхронные."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(pool, functools.partial(func, *args, **kwargs))

    @dp.message_handler(
        _is_group_reply,
        content_types=types_module.ContentTypes.ANY,
    )
    async def crm_group_reply(message):
        try:
            replied = message.reply_to_message
            accepted = await _run(
                service.ingest_group_reply,
                db,
                chat_id=message.chat.id,
                reply_to_message_id=replied.message_id,
                message=message,
            )
        except Exception:
            logging.exception('crm: не удалось принять ответ из группы')
            return

        if accepted is None:
            # Ответили не на обращение (например, на отчёт другого раздела) —
            # отдаём сообщение дальше по цепочке, а не съедаем его.
            if SkipHandler is not None:
                raise SkipHandler()
            return

        # Расписка — РЕАКЦИЯ на само сообщение сотрудника (telegram.REPLY_REACTION).
        # Текстом в чат её больше нет: «✅ Ответ отправлен оператору по обращению
        # №N» видела вся группа, и даже раз на обращение это читалось как лишняя
        # реплика (владелец, 25.08.2026). Поэтому и гейта «только первый ответ»
        # тут нет — реакция ничего не засоряет и подтверждает КАЖДЫЙ ответ,
        # включая повторный апдейт Telegram: она идемпотентна.
        try:
            _result, error = await _run(
                transport.set_message_reaction,
                message.chat.id,
                message.message_id,
                telegram.REPLY_REACTION,
            )
            if error:
                # Не в debug: реакция — единственная расписка раздела, и её
                # отказ значит, что сотрудник в группе больше не видит
                # подтверждений и начнёт дублировать ответы.
                logging.warning('crm: реакция на ответ не поставилась: %s', error)
        except Exception:
            logging.exception('crm: реакция на ответ не поставилась')

    logging.info('Раздел «Обращения»: обработчики бота подключены')

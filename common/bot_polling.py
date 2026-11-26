# -*- coding: utf-8 -*-
"""Polling Telegram в worker, который не роняет процесс, когда Telegram недоступен.

26.11.2026 в 04:03 из сети Yandex Cloud пропал выход на api.telegram.org: «Network is
unreachable». Работавший worker это пережил: цикл опроса aiogram ловит любую ошибку getUpdates
и повторяет её через 5 с. А новый процесс после выкладки в 11:22 падал уже на старте. До цикла
опроса aiogram спрашивает у Telegram getMe, сбрасывает вебхук и пропускает накопленные
обновления, и исключение любого из этих вызовов выходит из executor.start_polling и завершает
процесс. Вместе с ботом лежал весь worker: планировщик (ночные синки, «Учёт часов» по Syntony,
биллинг чата), сторожа и будильники. Pod перезапускался по кругу.

Здесь те же шаги старта, но без права уронить процесс: getMe, сброс вебхука и пропуск
обновлений идут по разу и с потолком времени. Не вышло — предупреждение в журнал, старт идёт
дальше. Цикл опроса запускается без своего сброса вебхука (reset_webhook=False): в aiogram этот
сброс стоит перед циклом и без повтора, так что недоступный Telegram молча убил бы задачу опроса
навсегда. Когда связь вернётся, бот сам начнёт получать сообщения, перезапуск не нужен. Когда
Telegram доступен, всё идёт как раньше: getMe, сброс вебхука, пропуск накопленного, on_startup,
опрос.

Что меняется при старте без Telegram: накопленные за простой обновления не пропускаются, и бот
ответит на них, когда связь вернётся. Так же вёл себя и процесс, переживший обрыв.
"""
import asyncio
import logging

from aiogram.utils.executor import Executor

# Потолок одного вызова на старте. Отказ сети приходит сразу; потолок нужен, если пакеты
# пропадают молча: тогда aiohttp ждал бы своего таймаута, а запуск — вместе с ним.
STARTUP_CALL_TIMEOUT_S = 15

_FAILED = object()


async def _startup_call(make_call, what, timeout):
    """Один вызов Telegram на старте → его результат или _FAILED (в журнале — предупреждение)."""
    try:
        return await asyncio.wait_for(make_call(), timeout)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 — сеть, таймаут, ответ Telegram: старт не роняем
        logging.warning(
            "Telegram на старте не ответил (%s: %s: %s) — worker работает дальше, "
            "бот подключится сам, когда связь вернётся", what, type(error).__name__, error)
        return _FAILED


def build_executor(dispatcher, *, on_startup=None, skip_updates=True,
                   timeout=STARTUP_CALL_TIMEOUT_S):
    """Executor aiogram, у которого старт не зависит от доступности Telegram."""
    runner = Executor(dispatcher, skip_updates=skip_updates)
    if on_startup is not None:
        runner.on_startup(on_startup, webhook=False)

    async def welcome():
        user = await _startup_call(lambda: dispatcher.bot.me, 'getMe', timeout)
        if user is not _FAILED:
            logging.info("Bot: %s [@%s]", user.full_name, user.username)

    async def skip_pending_updates():
        # Как Executor._skip_updates: сбросить вебхук, если он есть, и пропустить накопленное.
        if await _startup_call(lambda: dispatcher.reset_webhook(True), 'сброс вебхука',
                               timeout) is _FAILED:
            return
        if await _startup_call(dispatcher.skip_updates, 'пропуск накопленных обновлений',
                               timeout) is not _FAILED:
            logging.warning('Updates were skipped successfully.')

    runner._welcome = welcome
    runner._skip_updates = skip_pending_updates
    return runner


def start_polling(dispatcher, *, on_startup=None, skip_updates=True):
    """Как aiogram.utils.executor.start_polling, но недоступный Telegram не роняет процесс."""
    build_executor(dispatcher, on_startup=on_startup,
                   skip_updates=skip_updates).start_polling(reset_webhook=False)

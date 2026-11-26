# -*- coding: utf-8 -*-
"""Старт бота не роняет worker, когда Telegram недоступен (common/bot_polling.py).

26.11.2026 из сети Yandex Cloud пропал выход на api.telegram.org («Network is unreachable»).
Работавший worker это пережил, а новый после выкладки падал на старте: aiogram до цикла опроса
зовёт getMe, и исключение выходит из executor.start_polling. Вместе с ботом лежали планировщик,
синки и сторожа, pod перезапускался по кругу. Здесь на настоящем aiogram 2 (Bot и Dispatcher
без сети, вызовы Telegram подменены) проверяется:
  * старт с недоступным Telegram доходит до on_startup и цикла опроса, без исключения
    (и что у aiogram без нашей обёртки — то самое падение);
  * цикл опроса запускается без своего сброса вебхука и переживает ошибки getUpdates;
  * когда Telegram доступен, шаги прежние: getMe, сброс вебхука, пропуск накопленного,
    on_startup;
  * зависший вызов не держит старт дольше потолка.
"""
import asyncio
import logging
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.contrib.fsm_storage.memory import MemoryStorage  # noqa: E402
from aiogram.dispatcher import dispatcher as aiogram_dispatcher  # noqa: E402
from aiogram.utils.exceptions import NetworkError  # noqa: E402
from aiogram.utils.executor import Executor  # noqa: E402

from common import bot_polling  # noqa: E402

UNREACHABLE = ('Aiohttp client throws an error: ClientConnectorError: Cannot connect to host '
               'api.telegram.org:443 ssl:default [Network is unreachable]')


def unreachable(*_args, **_kwargs):
    raise NetworkError(UNREACHABLE)


class _FastSleepAsyncio(types.ModuleType):
    """asyncio для модуля диспетчера aiogram: пауза после ошибки getUpdates — без 5 с ожидания."""

    def __init__(self):
        super().__init__('asyncio')
        self.__dict__.update(asyncio.__dict__)
        self.sleep = self._sleep

    @staticmethod
    async def _sleep(_delay, result=None):
        await asyncio.sleep(0)
        return result


class BotPollingTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bot = Bot('42:test-token')
        self.dp = Dispatcher(self.bot, storage=MemoryStorage())
        self.calls = []

    def tearDown(self):
        async def close_session():
            session = await self.bot.get_session()
            await session.close()

        self.loop.run_until_complete(close_session())
        self.loop.close()
        asyncio.set_event_loop(None)

    def _offline(self):
        """Telegram недоступен: каждый вызов — та же ошибка, что в журнале прода 26.11."""
        for name in ('get_me', 'get_webhook_info', 'delete_webhook', 'set_my_commands'):
            setattr(self.bot, name, mock.AsyncMock(side_effect=unreachable))

    async def _on_startup(self, dispatcher):
        self.calls.append(('on_startup', dispatcher))

    def test_aiogram_alone_dies_on_unreachable_telegram(self):
        """Почему понадобилась обёртка: у aiogram старт падает на getMe, до on_startup."""
        self._offline()
        runner = Executor(self.dp, skip_updates=True)
        runner.on_startup(self._on_startup, webhook=False)
        with self.assertRaises(NetworkError):
            self.loop.run_until_complete(runner._startup_polling())
        self.assertEqual(self.calls, [])

    def test_startup_with_unreachable_telegram_reaches_on_startup(self):
        self._offline()
        runner = bot_polling.build_executor(self.dp, on_startup=self._on_startup)
        with self.assertLogs(level=logging.WARNING) as logs:
            self.loop.run_until_complete(runner._startup_polling())
        self.assertEqual(self.calls, [('on_startup', self.dp)])
        text = '\n'.join(logs.output)
        self.assertIn('getMe', text)
        self.assertIn('Network is unreachable', text)
        self.assertIn('worker работает дальше', text)
        # Сброс вебхука не прошёл — пропуск накопленного уже не пробуем.
        self.bot.get_me.assert_awaited_once()
        self.bot.get_webhook_info.assert_awaited_once()
        self.bot.delete_webhook.assert_not_awaited()

    def test_polling_survives_unreachable_telegram_and_never_resets_webhook_itself(self):
        """Весь путь worker: старт без Telegram, опрос с ошибками, остановка — без исключения."""
        self._offline()
        polls = []

        async def get_updates(*_args, **_kwargs):
            polls.append(len(polls) + 1)
            if len(polls) < 3:
                raise NetworkError(UNREACHABLE)
            # Связь вернулась: одно пустое получение — и выходим, как при остановке worker.
            self.dp.stop_polling()
            asyncio.get_event_loop().call_soon(asyncio.get_event_loop().stop)
            return []

        self.bot.get_updates = get_updates
        # Страховка: если опрос так и не дойдёт до третьего getUpdates (задача опроса умерла),
        # цикл всё равно остановится, и тест покраснеет на проверках, а не зависнет.
        self.loop.call_later(5, self.loop.stop)
        logging.disable(logging.CRITICAL)  # трейсбеки «Cause exception while getting updates»
        try:
            with mock.patch.object(aiogram_dispatcher, 'asyncio', _FastSleepAsyncio()):
                bot_polling.start_polling(self.dp, skip_updates=True, on_startup=self._on_startup)
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(self.calls, [('on_startup', self.dp)])
        self.assertEqual(polls, [1, 2, 3])
        # Сброс вебхука был один — на старте; перед циклом опроса его нет (reset_webhook=False),
        # иначе ошибка этого сброса убила бы задачу опроса без повтора.
        self.bot.get_webhook_info.assert_awaited_once()
        self.bot.delete_webhook.assert_not_awaited()

    def test_reachable_telegram_keeps_the_old_startup_steps(self):
        order = []
        me = types.SimpleNamespace(full_name='iCORE', username='icore_bot')

        async def get_me():
            order.append('getMe')
            return me

        async def get_webhook_info():
            order.append('getWebhookInfo')
            return types.SimpleNamespace(url='')

        async def delete_webhook(**kwargs):
            order.append(('deleteWebhook', kwargs))
            return True

        async def on_startup(dispatcher):
            order.append('on_startup')

        self.bot.get_me = get_me
        self.bot.get_webhook_info = get_webhook_info
        self.bot.delete_webhook = delete_webhook
        runner = bot_polling.build_executor(self.dp, on_startup=on_startup)
        with self.assertLogs(level=logging.INFO) as logs:
            self.loop.run_until_complete(runner._startup_polling())
        self.assertEqual(order, ['getMe', 'getWebhookInfo',
                                 ('deleteWebhook', {'drop_pending_updates': True}), 'on_startup'])
        text = '\n'.join(logs.output)
        self.assertIn('Bot: iCORE [@icore_bot]', text)
        self.assertIn('Updates were skipped successfully.', text)
        self.assertNotIn('не ответил', text)

    def test_skip_updates_off_skips_nothing(self):
        self._offline()
        self.bot.get_me = mock.AsyncMock(return_value=types.SimpleNamespace(full_name='i', username='b'))
        runner = bot_polling.build_executor(self.dp, on_startup=self._on_startup, skip_updates=False)
        self.loop.run_until_complete(runner._startup_polling())
        self.bot.get_webhook_info.assert_not_awaited()
        self.bot.delete_webhook.assert_not_awaited()
        self.assertEqual(self.calls, [('on_startup', self.dp)])

    def test_hanging_call_is_capped(self):
        """Пакеты пропадают молча: старт ждёт не дольше потолка, а не таймаута aiohttp."""
        async def hang():
            await asyncio.sleep(3600)

        self.bot.get_me = hang
        self.bot.get_webhook_info = hang
        runner = bot_polling.build_executor(self.dp, on_startup=self._on_startup, timeout=0.05)
        started = self.loop.time()
        with self.assertLogs(level=logging.WARNING) as logs:
            # Без потолка старт висел бы час; wait_for здесь — страховка теста, не проверка.
            self.loop.run_until_complete(asyncio.wait_for(runner._startup_polling(), 5))
        self.assertLess(self.loop.time() - started, 2)
        self.assertEqual(self.calls, [('on_startup', self.dp)])
        self.assertIn('TimeoutError', '\n'.join(logs.output))


if __name__ == '__main__':
    unittest.main()

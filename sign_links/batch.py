# -*- coding: utf-8 -*-
"""Пачка ИИН → исходы генератора: параллельно, в срок, без добивания лежащего генератора.

Нужна внешнему входу (external.py): оператор спрашивает один ИИН и ждёт сколько
надо, а сервис присылает список и ждёт ответа одним запросом. Правил здесь нет —
только порядок работы; что считать ссылкой, решает sapar_link.

Три ограничения, ради которых модуль существует:

* **Генератор — чужая гостевая форма за WAF**, на том же хосте, что и API Sapar,
  которым живут «Обзвон» и «Обращения». Заблокируют нас там за напор — пострадают
  и они. Поэтому одновременных запросов к форме не больше LANES на ВЕСЬ процесс,
  сколько бы пачек ни шло разом. Места свои, а не из общего пула бота: там их
  четыре на всё приложение.

* **Ответа ждёт чужая программа со своим таймаутом.** У пачки есть срок
  (BUDGET_SEC). Не успели — оставшиеся ИИН уходят с пометкой «повторить», а не
  держат соединение, пока клиент не оборвёт его сам.

* **Генератор лежит — не добивать.** MAX_UNAVAILABLE_IN_ROW отказов подряд
  останавливают пачку: иначе сто ИИН ждали бы сто таймаутов.

Очередь к местам общая и честная: пачка держит в работе не больше `lanes` задач
и ставит следующую, когда вернулась предыдущая. Одиночный запрос, пришедший
посреди большой пачки, ждёт одну задачу, а не всю пачку.
"""

import collections
import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import sapar_link

LANES = 4
# Сколько ждать каждый из двух запросов к форме. У оператора — 20 секунд
# (sapar_link.TIMEOUT), но он ждёт один ИИН, а не пачку.
CALL_TIMEOUT = 10
BUDGET_SEC = 30
MAX_UNAVAILABLE_IN_ROW = 3

# Почему ИИН остался без ответа генератора.
BUDGET = 'budget'
VENDOR_DOWN = 'vendor_down'

_WHY = {
    BUDGET: 'пачка не уложилась в срок',
    VENDOR_DOWN: 'генератор не отвечает — пачка остановлена',
}

_executor = None
_executor_lock = threading.Lock()


def shared_executor():
    """Места процесса под запросы к генератору. Создаются при первой пачке."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=LANES,
                                           thread_name_prefix='sign-links-api')
        return _executor


def _call(generate, iin):
    started = time.monotonic()
    try:
        return dict(generate(iin))
    except Exception as error:  # noqa: BLE001 — sapar_link.generate не бросает, подменённый клиент может
        # Только имя исключения: в его тексте может оказаться ИИН.
        logging.warning('sign_links: клиент генератора упал: %s', type(error).__name__)
        return {
            'outcome': sapar_link.UNAVAILABLE, 'link': None, 'message': None,
            'error': 'клиент генератора упал: %s' % type(error).__name__,
            'latency_ms': int((time.monotonic() - started) * 1000),
        }


def _unanswered(reason, asked):
    return {
        'outcome': sapar_link.UNAVAILABLE, 'link': None, 'message': None,
        'error': _WHY[reason], 'latency_ms': None, 'asked': asked, 'skipped': reason,
    }


def run(iins, generate, *, executor=None, lanes=None, budget_sec=None,
        max_unavailable_in_row=None):
    """({ИИН: исход}, причина остановки) — причина пуста, если пачка пройдена целиком.

    Исход — словарь sapar_link.generate и два поля сверх него:
        asked    — спрашивали ли генератор (только такие ИИН идут в журнал);
        skipped  — BUDGET / VENDOR_DOWN, если ответа не дождались, иначе None.

    iins — без повторов: один ИИН спрашивается один раз. Не заданные пределы
    берутся из констант модуля в момент вызова.
    """
    executor = executor or shared_executor()
    lanes = max(1, int(LANES if lanes is None else lanes))
    budget_sec = BUDGET_SEC if budget_sec is None else budget_sec
    if max_unavailable_in_row is None:
        max_unavailable_in_row = MAX_UNAVAILABLE_IN_ROW
    queue = collections.deque(iins)
    pending = {}
    results = {}
    stopped = ''
    failures_in_row = 0
    deadline = time.monotonic() + budget_sec

    while queue or pending:
        while queue and len(pending) < lanes:
            iin = queue.popleft()
            pending[executor.submit(_call, generate, iin)] = iin
        left = deadline - time.monotonic()
        done = wait(list(pending), timeout=left, return_when=FIRST_COMPLETED)[0] if left > 0 else ()
        if not done:
            stopped = BUDGET
            break
        for future in done:
            result = future.result()
            results[pending.pop(future)] = dict(result, asked=True, skipped=None)
            if result.get('outcome') == sapar_link.UNAVAILABLE:
                failures_in_row += 1
            else:
                failures_in_row = 0
        if failures_in_row >= max_unavailable_in_row:
            stopped = VENDOR_DOWN
            break

    # Остановились раньше конца. Задачу, которая ещё не начата, снимаем — её ИИН
    # не спрашивали. Начатую не прервать: генератор по ней уже спрошен, ответа не
    # ждём, место она освободит сама, не позже двух CALL_TIMEOUT.
    for future, iin in pending.items():
        if future.cancel():
            results[iin] = _unanswered(stopped, asked=False)
        elif future.done():
            results[iin] = dict(future.result(), asked=True, skipped=None)
        else:
            results[iin] = _unanswered(stopped, asked=True)
    for iin in queue:
        results[iin] = _unanswered(stopped, asked=False)
    return results, stopped

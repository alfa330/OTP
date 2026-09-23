# -*- coding: utf-8 -*-
"""Отдел ТЭЗ — телефония, чаты, лиды и табло.

Девять модулей, которые до 14.09.2026 лежали в корне репозитория с приставкой
`tez_`. Приставка стала именем каталога, разделение внутри — то же, что у cdr/,
op_funnel/ и parcels/.

Почему у ТЭЗ всё своё
---------------------
У остальных отделов телефония на Oktell, а у ТЭЗ — Binotel; переписка ТП и ОП
живёт в ChatApp (WhatsApp Cloud API), а не в Chat2Desk (СЗоВ) и не в Wazzup
(Верификаторы ОП). Поэтому клиенты внешних систем здесь отдельные, а не общие.

Состав пакета
-------------
    binotel_calls.py     прослойка совместимости: клиент Binotel API переехал в
                         binotel/client.py (Binotel теперь не только у ТЭЗ)
    status_sync.py       скрейп панели my.binotel.kz ради истории статусов
    op_productivity.py   дневные телефонные метрики ОП поверх binotel_calls
    wallboard_source.py  снимок кабинета Binotel «на сейчас» для табло Тез КЦ
    wallboard_export.py  выгрузка показателей табло за период в xlsx
    op_leads.py          чистая логика успешек ОП: телефоны и статус лида
    lead_service.py      оркестрация успешек: загрузка базы и ночная сверка
    first_orders.py      клиент TEZ APP: дата первого завершённого заказа
    chatapp_client.py    клиент ChatApp API для «Случайного чата»

Пакет намеренно НЕ импортирует свои модули
------------------------------------------
`__init__.py` держит только докстринг и `__all__` (список строк, он ничего не
подгружает). Импорты грозди в bot_schedule2.py почти все отложенные — сделаны
внутри функций, чтобы старт процесса не тянул requests и не ходил в сеть. Любой
`from . import binotel_calls` здесь превратил бы ленивую загрузку в жёсткую.

Подмена модулей в тестах: нужны ОБЕ половины
--------------------------------------------
После переезда в пакет `from tez import status_sync` берёт АТРИБУТ пакета, и до
`sys.modules` дело не доходит, если модуль уже импортировался в этом процессе.
То есть привычное

    sys.modules['tez_status_sync'] = stub          # старое плоское имя
    sys.modules['tez.status_sync'] = stub          # даже правильный ключ

заглушку не поставит: тест молча уйдёт в НАСТОЯЩИЙ кабинет Binotel с боевыми
учётками и при этом останется зелёным. Подменять надо в двух местах сразу:

    sys.modules['tez.status_sync'] = stub
    setattr(tez, 'status_sync', stub)

Так сделано в tests/test_call_end_party.py и tests/test_tez_wallboard.py.

Автономный запуск — только через -m
-----------------------------------
У binotel_calls, first_orders, status_sync и chatapp_client есть `__main__` для
ручного разбора инцидентов. После переезда `python tez/first_orders.py` падает:
интерпретатор кладёт в sys.path сам каталог tez/, и `from tez.op_leads import …`
не резолвится. Правильная форма — `python -m tez.first_orders`.
"""

__all__ = ['binotel_calls', 'chatapp_client', 'first_orders', 'lead_service',
           'op_leads', 'op_productivity', 'status_sync', 'wallboard_export',
           'wallboard_source']

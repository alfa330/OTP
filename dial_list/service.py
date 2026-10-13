# -*- coding: utf-8 -*-
"""Правила обзвона из телефона: выдача порций, звонок через Binotel, исходы.

Все операции идут через `db._get_cursor()` (одна транзакция на блок `with`).
Сетевые вызовы в Binotel делаются ВНЕ транзакций: ответ АТС может идти
секунды, а держать открытой транзакцию с блокировками ради этого нельзя.

Состояния попытки (dial_list_attempts.state):

    requested     сервер попросил АТС позвонить, generalCallID известен
    failed        АТС отказала (api_error) — плечо до телефона не дойдёт
    leg_ringing   телефон сообщил: пришло входящее плечо от АТС
    leg_answered  телефон принял плечо; АТС набирает водителя
    ended         телефон сообщил, что разговор кончился; ждём исход от Binotel
    finished      исход подтверждён Binotel (webhook / poll) либо истёк таймаут

Исход от Binotel (13.10.2026, разбор прода 12.10.2026). call-details спрашивает один общий
опрос компании (live_calls) — одним запросом на все живые попытки, не чаще раза в три
секунды; карточка звонка, добор исходов, события телефона и вопрос автосброса читают его кэш,
финалы закрываются сразу после опроса, чей бы ни был звонок (_settle_live). Ответ водителя
(ANSWER), пока плечо по телефону идёт, — разговор, а не финал: Binotel отдаёт его и посреди
звонка с неполной длительностью; закрывают попытку данные, полученные после конца плеча
(live_verdict), а поздний финал длительность только увеличивает (_raise_answered). Попытка
ended не держит ни «Позвонить», ни «Ещё» — только того же водителя (start_call); если по ней
окажется нужен итог, его потребует следующий звонок.

Флаг `cancelled` (при state=finished): оператор САМ завершил звонок (событие
телефона `operator_hangup` / `ended by_operator`), а Binotel сообщил, что
водитель не ответил. Такая попытка не считается ни строке выдачи, ни лиду
(решение владельца 24.09.2026): строка остаётся в работе, итог по ней
поставить нельзя. Если же водитель ответил и оператор сам положил трубку —
это обычный разговор, итог обязателен. Разговор, который слышал телефон
(talk_detected true, с 3.22.34), отменой не становится при любом исходе Binotel:
трубку оператор положил после разговора, а не «до ответа».

Отметка `ring_timeout_at` (владелец, 11.10.2026): телефон сам положил плечо, когда
водитель за пять гудков не взял трубку (событие `ring_timeout`, затем `ended` с
`ring_timeout`). Это не отбой оператора: попытка не отменяется, а считается
обычным недозвоном («Не поднял трубку») — строке и лиду, как недозвон от Binotel, —
и итог по ней не нужен. Ответил водитель в последний момент — это разговор, итог
обязателен.

Что телефон услышал на плече (владелец, 12.10.2026; телефон с 3.22.34) — ключи события
`ended`, оба необязательные:

    auto_hangup    почему плечо положил сам телефон: 'ring_timeout' (гудки кончились — то
                   же, что прежний ключ ring_timeout) или 'announcement' — сообщение сети
                   «абонент недоступен». Сообщение сети — всегда недозвон, что бы ни
                   ответил Binotel (и ANSWER с длительностью — автоответчик): итог не
                   нужен и не ставится, в «дозвонились», минуты разговора и успешки такая
                   попытка не идёт (attempt_result, ATTEMPT_TALK_SQL). 'announcement'
                   сильнее 'ring_timeout': поздний сигнал о гудках его не затирает.
    talk_detected  был ли разговор (по Binotel — /live, ответ на ring_timeout — или по
                   звуку: собеседник и оператор обменялись репликами). true — разговор
                   был: итог обязателен сразу, кто бы ни положил трубку, исхода от АТС
                   не ждём; отбой оператора тогда не отмена «до ответа» — он был после
                   разговора. Но финальное «не дозвонились» Binotel (занято, не ответил,
                   отмена, канал недоступен, перегрузка) true не перебивает (ревью
                   13.10.2026): итог тогда не нужен и звонить дальше попытка не мешает,
                   уже сохранённый итог остаётся. «Ключа нет» (телефон до 3.22.34) — как раньше: плечо
                   кончилось после ответа — итог обязателен, отбой оператора — решает
                   Binotel. false — разговора телефон не услышал, решает Binotel:
                   финальный ANSWER не короче секунды — итог нужен; занято, не ответил,
                   отмена, прочее — не нужен, строка уходит в «Не дозвонились»; исхода
                   ещё нет — телефону уходит outcome_required: null, долг появится в
                   состоянии вместе с исходом. Телефон шлёт false, только когда звук он
                   слушал: анализ не работал (динамик выключен) — ключа быть не должно.
                   Попытку, которую Binotel закрыл ответом ещё до конца плеча (ANSWER
                   приходит и посреди разговора, длительность в нём неполная), false
                   недозвоном не делает. Плечо положил сам телефон (auto_hangup) — решает
                   причина, а не talk_detected (сообщение сети — итог не нужен никогда,
                   гудки — по Binotel).
    announcement_suspected, announcement_by — замер точности распознавания сообщения сети
                   (ревью 13.10.2026, announcement_stats): сработало ли распознавание по речи
                   на плече хоть раз и кто решил, что это сообщение сети ('auto', 'sit',
                   'operator' — оператор по подсказке, режим suggest). На счёт попытки не
                   влияют: отбой оператора по подсказке — то же сообщение сети (auto_hangup
                   'announcement'): недозвон, итога нет, не отмена.

Версия телефона (владелец, 12.10.2026) — заголовок X-ICore-Phone-Version в каждом запросе
телефона с 3.22.34. У выпущенного 3.22.33 его нет, а его автосброс рвал разговоры: ответ
Binotel на вопрос «класть ли плечо» приходил позже пяти секунд, которые телефон ждёт.
Поэтому запросу без заголовка с разборчивой версией (phone_guards_speech) состояние
отдаёт ring_timeout_sec: 0, а на событие ring_timeout сервер сразу отвечает «не класть»,
не спрашивая ни базу, ни Binotel. Решает наличие заголовка, а не номер версии: сборка с
защитой до выпуска называет себя ещё 3.22.33. Телефону с заголовком — настройка отдела,
как было. И active_attempt запросу без заголовка — в прежнем смысле, вместе с кончившимся
плечом (ended): 3.22.33 после «Завершить» ждёт по нему решения сервера (_active_attempt).

Комментарии оператора (владелец, 12.10.2026): last_comment, last_comment_label и
last_comment_outcome — последний непустой комментарий ЭТОГО оператора по водителю, из
любой его строки и за любое время (_LAST_COMMENT_JOIN_SQL): в строках пачки, на вкладках
итогов, у идущего звонка в состоянии и в ответе «Позвонить».

Подписавшим не звоним (владелец, 11.10.2026): из пула уходят и подписавшие
(signed_at), и «на проверке у Яндекса» (sign_status 'processing' — водитель уже
подписал, ждём Яндекс). Перед «Ещё» и перед звонком сервер сам спрашивает Sapar о
тех, чья проверка устарела (SIGN_PRECHECK_*, SIGN_CALL_*), — тоже вне транзакций.
Sapar лежит или не настроен — выдаём и звоним, как раньше. Оператор может спросить
и сам — «Проверить на подписание» (operator_sign_check, владелец 12.10.2026), в том
числе посреди разговора: телефону уходит только статус, без ИИН и документов.

«Позвонить ещё раз» (владелец, 12.10.2026) — любому своему водителю с вкладок итогов:
строка снова в списке оператора (recall_lead). Обработанный водитель при этом так и
остаётся обработанным: недозвон не возвращает его в общий пул (_touch_lead).

Строка выдачи (assignment) считается обработанной после ПЕРВОЙ попытки с
подтверждённым исходом — любым: дозвонились, занято, не ответил. Не
дозвонились — лид вернётся в общий пул через retry_after_hours, пока попыток
меньше max_attempts. Так оператор не обязан звонить одному водителю трижды
подряд, а следующая порция выдаётся ровно когда текущая закрыта.
"""
import contextlib
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import hmac

import psycopg2
import psycopg2.errors
from psycopg2.extras import execute_values

from binotel import client as binotel
from common.kz_phone import normalize_kz_phone  # noqa: F401 — нормализация номеров списка
# Проверка ИИН — та же, что у «Ссылки на подписание»: 12 цифр, дата, контрольная
# цифра. Чистая логика без базы и сети, двойник на фронте сторожит её тест.
from sign_links import iin as iin_rules
from crm import sapar as sapar_api
from test_numbers import keys as test_keys

from . import live_calls
from . import signing
from .sign_check import IN_FLIGHT_STATES, SignChecker

log = logging.getLogger(__name__)

# Дефолты настроек отдела (строки в dial_list_department_settings может не быть).
DEFAULT_PORTION_SIZE = 20
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_AFTER_HOURS = 24
PORTION_SIZE_MAX = 100

# Сколько секунд телефон ждёт входящее плечо после ответа сервера. Binotel по
# документации к ext-to-phone звонит на внутреннюю линию до 30 с (callTimeToExt),
# плечо обычно приходит за 1–3 с.
LEG_TIMEOUT_SEC = 30
# Попытка без исхода дольше этого времени закрывается как «неизвестно»:
# вебхук мог не дойти, а call-details звонок не увидел. Разговор длиннее часа
# на обзвоне не бывает.
ATTEMPT_STALE_MINUTES = 60
# Добор исходов на GET состояния (reconcile): раньше этого возраста попытку, плечо которой
# ещё идёт, не трогаем — звонок только набирается; кончившуюся (ended) — сразу.
POLL_MIN_AGE_SEC = 10
POLL_MAX_PER_REQUEST = 10
# Одновременно у оператора живёт одна попытка; «живая» — моложе этого порога. Держит
# следующий звонок только попытка, плечо которой по телефону ещё идёт (LEG_UP_STATES):
# кончившаяся (ended) ждёт исхода от АТС, не мешая звонить дальше (13.10.2026).
ACTIVE_ATTEMPT_WINDOW_MINUTES = 3
# Плечо по телефону ещё идёт; вместе с ended — «живые» попытки, исход которых ждут от АТС.
LEG_UP_STATES = ("requested", "leg_ringing", "leg_answered")
LIVE_STATES = LEG_UP_STATES + ("ended",)
# Живые звонки у Binotel — один опрос call-details на компанию и общий кэш (live_calls):
# не чаще CALLS_REFRESH_SEC один запрос сразу на все живые попытки компании; спрашивающий
# ждёт идущего обновления не дольше CALLS_WAIT_SEC и дальше обходится кэшем.
CALLS_REFRESH_SEC = live_calls.REFRESH_SEC
CALLS_WAIT_SEC = live_calls.WAIT_SEC
# Вопрос автосброса «класть ли плечо» (ring_timeout): нужны данные не старше RING_FRESH_SEC —
# ради них обновление запускается, даже если от прошлого прошло меньше CALLS_REFRESH_SEC;
# всего ждём не дольше RING_BUDGET_SEC, своей очереди у Binotel — не дольше
# RING_BINOTEL_WAIT_SEC. Данные старше RING_DATA_MAX_AGE_SEC — как их нет: «не класть».
RING_FRESH_SEC = 2.0
RING_BUDGET_SEC = 2.5
RING_BINOTEL_WAIT_SEC = 1.5
RING_DATA_MAX_AGE_SEC = 6.0
# «АТС ещё набирает» (CALLING, RINGING) велит класть плечо, только если эти данные не старше
# RING_HANGUP_MAX_AGE_SEC (ревью 13.10.2026): кэш бывает старше RING_FRESH_SEC — обновление не
# ушло (секунда на ключ занята, Binotel попросил паузу), — а водитель мог снять трубку за эти
# секунды и молчать. Старее — «нет данных»: не класть, без disposition. Финал «не дозвонились»
# не устаревает (звонок уже кончился) и, как «говорят», верен до RING_DATA_MAX_AGE_SEC.
RING_HANGUP_MAX_AGE_SEC = RING_FRESH_SEC + 1.0
# Ответ водителя (ANSWER) Binotel отдаёт и посреди идущего разговора, с неполной
# длительностью (12.10.2026). Пока плечо идёт, это «говорят», а не финал; закрываем попытку
# данными, полученными не раньше чем через столько секунд после конца плеча.
ANSWER_SETTLE_SEC = 1.0
# Часы одни (ревью 13.10.2026): конец плеча (phone_ended_at) пишется по часам приложения — теми
# же помечены данные опроса (live_calls.Seen.wall), — а не CURRENT_TIMESTAMP базы: часы двух
# машин могут разойтись на секунду, и ANSWER посреди разговора с неполной длительностью сошёл бы
# за финал. Момент, когда оператор начал следующий звонок (superseded_at), — время базы
# (requested_at новой попытки); к нему — запас SUPERSEDED_SETTLE_MARGIN_SEC.
SUPERSEDED_SETTLE_MARGIN_SEC = 2.0
# Плечо, которое телефон так и не подтвердил (ACK от АТС не дошёл — роутер с SIP ALG,
# 11.10.2026), но Binotel соединил водителя: разговор был, итог по нему нужен. Долг
# возникает только по свежим попыткам — старые, до появления итогов, тоже без
# leg_answered_at, и разом записать их оператору в долг было бы неправдой.
UNCONFIRMED_LEG_OUTCOME_HOURS = 12
# Сброс плеча без ответа водителя (владелец, 11.10.2026: «на пятом гудке — сброс,
# записать как „Не поднял трубку“»). Сколько гудков — в «Настройках SIP» отдела
# (sip_department_config.ring_timeout_rings): NULL — по умолчанию, 0 — не сбрасывать.
# Гудок в СНГ — секунда сигнала и четыре паузы, то есть 5 с на гудок. Те же числа —
# у «Настроек SIP» (SIP_RING_*): правятся вместе.
RING_TIMEOUT_RINGS_DEFAULT = 5
RING_TIMEOUT_RINGS_MAX = 20
RING_SECONDS_PER_RING = 5
# Версия телефона — заголовок каждого его запроса к iCORE, с 3.22.34. Выпущенный 3.22.33
# заголовка не шлёт, и защиты автосброса по речи у него нет: 12.10.2026 он оборвал 52
# разговора — Binotel отвечал на вопрос «класть ли плечо» позже пяти секунд, которые
# телефон ждёт, либо отвечал «нет данных». Поэтому автосброс получает только телефон,
# приславший заголовок с разборчивой версией (phone_guards_speech); остальным —
# ring_timeout_sec: 0 и «не класть» на вопрос ring_timeout. Мусор в заголовке — как его нет.
# Решает само НАЛИЧИЕ заголовка, а не номер версии (владелец, 13.10.2026): ни один
# выпущенный телефон заголовка не шлёт, а сборка с защитой до выпуска ещё называет себя
# 3.22.33 — номер в ней меняет только выпуск. Сравнение номеров отняло бы у неё автосброс.
PHONE_VERSION_HEADER = "X-ICore-Phone-Version"
_PHONE_VERSION_RE = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,5}){1,3}")
# Настройки анализа звука на плече для телефона (поля состояния), все — переменные
# окружения сервера: это выключатели без выпуска нового телефона.
#   announcement_ms — столько миллисекунд подряд должно звучать сообщение сети («абонент
#     недоступен»), чтобы телефон его распознал по речи; 0 — распознавание по речи выключено
#     (и у сборок, которые announcement_mode не знают). Мусор и число вне 0..ANNOUNCEMENT_MS_MAX —
#     по умолчанию.
#   speech_guard — не класть плечо на гудках, пока на линии речь; 0/false/no/off — выключить.
#     Без защиты автосброс решает один Binotel, как у 3.22.33, — выключать вместе с гудками
#     отдела («Настройки SIP», 0 гудков).
#   announcement_sit — тон SIT (три нарастающих тона перед сообщением сети) → телефон сразу кладёт
#     плечо: точный признак, отдельный выключатель (ревью 13.10.2026: прежде его выключал только
#     announcement_ms=0 вместе со всем остальным). 0/false/no/off — выключить, мусор — включено.
#   announcement_mode — что делать, когда сообщение сети распознано ПО РЕЧИ (долгая речь собеседника
#     при молчащем операторе), без SIT. Ревью 13.10.2026: так же звучат мелодия вместо гудков
#     (щипковые, отрывистые ноты, песня с голосом громче музыки) — телефон рвал бы такой звонок на
#     каждой попытке, и водителю было бы не дозвониться никогда:
#       auto    — класть плечо сразу, как до ревью (ended с auto_hangup: announcement);
#       suggest — не класть: карточка звонка подсказывает «Похоже на автоответчик — можно завершить»,
#                 и отбой оператора при подсказке — тоже сообщение сети (announcement_by: operator,
#                 недозвон), а не «отменён до ответа»; заговорили (TALKING) — подсказка уходит;
#       off     — ничего не делать (телефон только пишет в журнал).
#     По умолчанию suggest, пока владелец не решит по замерам (announcement_stats); мусор — suggest;
#     announcement_ms=0 — off (один выключатель на все сборки, и на те, что режима не знают).
ANNOUNCEMENT_MS_ENV = "DIAL_LIST_ANNOUNCEMENT_MS"
ANNOUNCEMENT_MS_DEFAULT = 3500
ANNOUNCEMENT_MS_MAX = 60000
ANNOUNCEMENT_SIT_ENV = "DIAL_LIST_ANNOUNCEMENT_SIT"
ANNOUNCEMENT_MODE_ENV = "DIAL_LIST_ANNOUNCEMENT_MODE"
ANNOUNCEMENT_MODES = ("auto", "suggest", "off")
ANNOUNCEMENT_MODE_DEFAULT = "suggest"
# Кто решил, что на линии сообщение сети (ключ announcement_by события ended вместе с auto_hangup
# 'announcement'; dial_list_attempts.announcement_by): 'auto' — телефон по речи, 'sit' — телефон по
# тону SIT, 'operator' — оператор по подсказке. Для счёта попытки разницы нет: недозвон, итога нет,
# не отмена. Разница — для замеров точности распознавания (announcement_stats).
ANNOUNCEMENT_BY_VALUES = ("auto", "sit", "operator")
SPEECH_GUARD_ENV = "DIAL_LIST_SPEECH_GUARD"
# Почему плечо положил сам телефон (dial_list_attempts.auto_hangup, ключ auto_hangup
# события ended). Сообщение сети сильнее гудков: это всегда недозвон.
AUTO_HANGUP_RING_TIMEOUT = "ring_timeout"
AUTO_HANGUP_ANNOUNCEMENT = "announcement"
AUTO_HANGUPS = (AUTO_HANGUP_RING_TIMEOUT, AUTO_HANGUP_ANNOUNCEMENT)
# Финальный ANSWER от Binotel у попытки `t` — разговор: не сообщение сети и, если телефон
# разговора не услышал (talk_detected FALSE), не короче секунды. У попыток телефонов до
# 3.22.34 (auto_hangup пуст, talk_detected NULL) условие верно всегда — для них ничего не
# меняется. Двойник в Python — attempt_talked; то же условие в SQL аналитики
# (analytics._LEADS_CTE) и успешек (sign_check._RESOLVE_SQL) — правятся вместе.
ATTEMPT_TALK_SQL = "t.auto_hangup <> 'announcement' AND (t.talk_detected IS NOT FALSE OR t.billsec >= 1)"
# Подписание — до выдачи (владелец, 11.10.2026: «подписавших не выдавать»). Перед «Ещё»
# сервер спрашивает Sapar о тех, кто попадёт в порцию (с запасом), если их проверка
# старше SIGN_PRECHECK_FRESH_MINUTES или кончилась ошибкой; подписавшие и «на проверке
# у Яндекса» из пула уходят, порцию добирают следующие. Не больше SIGN_PRECHECK_ROUNDS
# кругов за SIGN_PRECHECK_BUDGET_SEC — оператор ждёт ответа; Sapar не настроен или
# лежит (SIGN_PRECHECK_MAX_SYSTEMIC сетевых сбоев) — порция выдаётся как раньше.
SIGN_PRECHECK_ROUNDS = 3
SIGN_PRECHECK_BUDGET_SEC = 8
SIGN_PRECHECK_FRESH_MINUTES = 15
SIGN_PRECHECK_WORKERS = 4
SIGN_PRECHECK_TIMEOUT_SEC = 3
SIGN_PRECHECK_MAX_SYSTEMIC = 2
# Перед звонком — то же для одного водителя: пачка лежит на экране часами.
SIGN_CALL_FRESH_MINUTES = 30
SIGN_CALL_TIMEOUT_SEC = 3
SIGNED_ROW_CLOSED = "Водитель уже подписал документы — звонить не нужно. Строка убрана из списка."
# «Проверить на подписание» с телефона (владелец, 12.10.2026) — один водитель, ждёт оператор,
# бывает посреди разговора: таймаут на запрос и срок на всё, вне транзакций. Из базы без Sapar
# отвечаем только «подписал» и «на проверке у Яндекса» по проверке моложе SIGN_OPERATOR_FRESH_SEC
# (13.10.2026: «не подписал» из базы свежим не бывает — водитель мог подписать минуту назад); об
# одном водителе по кнопке — не чаще раза в SIGN_OPERATOR_MIN_INTERVAL_SEC (двойной клик,
# нетерпеливые повторы).
SIGN_OPERATOR_FRESH_SEC = 60
SIGN_OPERATOR_MIN_INTERVAL_SEC = 20
SIGN_OPERATOR_TIMEOUT_SEC = 5
SIGN_OPERATOR_BUDGET_SEC = 6
# Пределы проверок по кнопке (13.10.2026): от одного оператора — не чаще раза в
# SIGN_OPERATOR_GAP_SEC и не больше SIGN_OPERATOR_PER_MINUTE в минуту; на весь сервер — не больше
# SIGN_OPERATOR_CONCURRENT одновременно (без ожидания: занято — ответ из базы). Иначе оператор,
# листающий вкладки с кнопкой, держал бы Sapar и потоки сервера.
SIGN_OPERATOR_GAP_SEC = 2
SIGN_OPERATOR_PER_MINUTE = 20
SIGN_OPERATOR_CONCURRENT = 4
# Пояснение под статусом на телефоне (окно узкое — коротко).
SIGN_OPERATOR_NO_IIN = "Нет ИИН — подписание не проверить"
SIGN_OPERATOR_NOT_CONFIGURED = "Проверка подписания не настроена на сервере"
SIGN_OPERATOR_SAPAR_DOWN = "Сапар не ответил — показан последний известный статус"
SIGN_OPERATOR_THROTTLED = "Только что проверяли — показан последний известный статус"
# Подписание на телефоне — четыре состояния. «На проверке у Яндекса» — уже подписал (владелец,
# 11.10.2026): Яндекс лишь подтверждает. Причина «не подписал» — словами SIGN_STATUS_LABELS
# (signing.py), у простого «не подписал» её нет (operator_sign_status).
OPERATOR_SIGN_LABELS = {
    "signed": "Подписал",
    "processing": "Подписал · на проверке у Яндекса",
    "not_signed": "Не подписал",
    "unknown": "Не удалось проверить",
}
OPERATOR_SIGN_DETAILED = (signing.NOT_FORMED, signing.EXPIRED, signing.REJECTED, signing.NO_DOCS)
# Почему проверка не дошла до конца (SignChecker.check_now → stopped) — для журнала сервера.
SIGN_NOW_STOPPED = {
    "not_configured": "Sapar не настроен",
    "backoff": "Sapar недавно не ответил, не спрашиваем",
    "sapar_unavailable": "Sapar не отвечает",
    "budget": "не уложились в срок, остальных проверит прогон",
}

# disposition Binotel → итог строки. Список пополняется по живым звонкам:
# в call-details 'ONLINE' — идёт разговор, пусто — ещё набирается.
ANSWERED_DISPOSITIONS = frozenset({"ANSWER", "ANSWERED", "SUCCESS", "VM-SUCCESS"})
BUSY_DISPOSITIONS = frozenset({"BUSY"})
NO_ANSWER_DISPOSITIONS = frozenset({"NOANSWER", "NO ANSWER", "NO_ANSWER", "CANCEL", "CANCELLED", "CANCELED"})
# Промежуточные статусы call-details, пока звонок идёт: CALLING — АТС набирает
# (живой тест 23.09.2026: опрос увидел CALLING на 10-й секунде и закрыл попытку
# как «другое» с billsec 0, хотя разговор шёл 25 с), ONLINE — идёт разговор.
NON_FINAL_DISPOSITIONS = frozenset({"", "ONLINE", "CALLING", "RINGING", "DIALING", "INPROGRESS", "IN-PROGRESS"})
# Закрытые без настоящего исхода — их вебхук/опрос вправе дописать позже.
PROVISIONAL_DISPOSITIONS = tuple(sorted(NON_FINAL_DISPOSITIONS | {"UNKNOWN"}))
# Финальное «не дозвонились» от Binotel: занято, не ответил, отмена, канал недоступен, перегрузка
# сети. Разговор, который будто бы слышал телефон (talk_detected TRUE), его не перебивает (ревью
# 13.10.2026): реплика оператора в паузе между фразами сообщения сети похожа для телефона на
# обмен репликами, а Binotel знает, что трубку никто не снимал. Итог тогда не нужен, звонить
# дальше попытка не мешает (_outcome_decision, _pending_outcome); итог, который оператор уже
# успел сохранить, остаётся. UNKNOWN (таймаут) сюда не входит — это не ответ Binotel.
NOT_ANSWERED_FINAL_DISPOSITIONS = BUSY_DISPOSITIONS | NO_ANSWER_DISPOSITIONS | frozenset({"CHANUNAVAIL", "CONGESTION"})
NOT_ANSWERED_FINAL_SQL = tuple(sorted(NOT_ANSWERED_FINAL_DISPOSITIONS))

# Серверы Binotel из официального архива примеров (samples-api-call-settings.php):
# вебхук без токена принимаем только с них.
BINOTEL_SERVER_IPS = frozenset({
    "194.88.218.116", "194.88.218.114", "194.88.218.117", "194.88.218.118",
    "194.88.219.67", "194.88.219.78", "194.88.219.70", "194.88.219.71",
    "194.88.219.72", "194.88.219.79", "194.88.219.80", "194.88.219.81",
    "194.88.219.82", "194.88.219.83", "194.88.219.84", "194.88.219.85",
    "194.88.219.86", "194.88.219.87", "194.88.219.88", "194.88.219.89",
    "194.88.219.92", "194.88.218.119", "194.88.218.120",
    "185.100.66.145", "185.100.66.146", "185.100.66.147",
})
# Секреты раздела живут ТОЛЬКО в окружении сервера (панель Render), в базе и в
# интерфейсе их нет: ключ API компании Binotel — <PREFIX>_API_KEY/_API_SECRET по
# имени компании отдела (binotel_company → binotel.client.env_prefix_for), токен
# вебхука — одна переменная на сервер. Интерфейс показывает только «задан/не задан».
WEBHOOK_TOKEN_ENV = "DIAL_LIST_WEBHOOK_TOKEN"
# Вебхук принимается только с адресов серверов Binotel (список выше); выключить
# проверку можно переменной =0, если Binotel начнёт слать с новых адресов —
# отклонённые адреса при этом видны в журнале сервера.
WEBHOOK_REQUIRE_BINOTEL_IP_ENV = "DIAL_LIST_WEBHOOK_REQUIRE_BINOTEL_IP"
# Чей адрес сверять со списком (ревью 13.10.2026): левый край X-Forwarded-For пишет сам клиент —
# с токеном из утёкшей ссылки любой назвался бы сервером Binotel. Прокси Render дописывает адрес,
# с которого к нему пришли, справа — его и берём (как werkzeug ProxyFix x_for=1). Окажется перед
# приложением больше доверенных прокси (видно по цепочке в журнале отказов) — число в переменной.
WEBHOOK_PROXY_HOPS_ENV = "DIAL_LIST_WEBHOOK_PROXY_HOPS"
WEBHOOK_PROXY_HOPS_DEFAULT = 1
WEBHOOK_PROXY_HOPS_MAX = 5
WEBHOOK_TOKEN_MIN_LEN = 16
WEBHOOK_LOG_KEEP_DAYS = 30
WEBHOOK_PAYLOAD_MAX_CHARS = 20000
DEFAULT_BINOTEL_COMPANY = binotel.COMPANY_REMOTE_CC
COMPANY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

# operator_hangup — оператор сам нажал «Завершить» (телефон шлёт до BYE);
# ended может нести by_operator/leg_sec — страховка, если operator_hangup не дошёл.
# ring_timeout — телефон досчитал гудки до сброса и спрашивает, класть ли плечо;
# ended с ring_timeout — положил: водитель не взял трубку. С 3.22.34 ended несёт ещё
# auto_hangup (почему телефон положил плечо сам) и talk_detected (был ли разговор по звуку).
PHONE_EVENTS = ("ringing", "answered", "ended", "no_leg", "operator_hangup", "ring_timeout")
# Для SQL `UPPER(disposition) IN %s` (psycopg2 разворачивает tuple в список).
ANSWERED_SQL = tuple(sorted(ANSWERED_DISPOSITIONS))

# Итоги звонка: стартовый набор отдела (руководитель правит в разделе), цвет —
# hex как в системной палитре iOS, requeue — «перезвонить»: водитель снова
# попадёт в порции через retry_after_hours, счётчик попыток обнуляется.
DEFAULT_OUTCOMES = (
    ("Заинтересован", "#34C759", False),
    ("Отказ", "#FF3B30", False),
    ("Перезвонить позже", "#FF9F0A", True),
    ("Неверный номер", "#8E8E93", False),
)
OUTCOME_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
OUTCOME_NAME_MAX = 64
OUTCOMES_MAX = 30
COMMENT_MAX = 500
# Типы итога (запрос владельца 29.09.2026): «Отказ» → «Дорого», «Уже работает в
# другом парке»… Выключенные тоже считаются в лимит — список один на итог.
SUBTYPE_NAME_MAX = 64
SUBTYPES_MAX = 20
# Маркер «телефон не прислал ключ subtype_id вовсе» — так шлют телефоны до типов
# итога (≤ 3.22.28). Им тип не нужен, итог сохраняется без него; новый телефон
# шлёт ключ всегда (null — тип не выбран), и тогда сервер проверяет выбор.
SUBTYPE_NOT_SENT = object()
# Скрипт разговора отдела (владелец, 25.09.2026): основной текст с лёгкой разметкой
# («# заголовок», «**жирный**», «==выделение==», «- пункт», «> примечание») и быстрые
# вопросы с ответами — их оператор открывает прямо во время звонка. Хранится по
# отделу, версия растёт при каждом сохранении: телефон по ней понимает, что пора
# перечитать.
SCRIPT_BODY_MAX = 20000
SCRIPT_QUESTION_MAX = 200
SCRIPT_ANSWER_MAX = 8000
SCRIPT_QUESTIONS_MAX = 50
PERIOD_TZ = timezone(timedelta(hours=5))  # Asia/Almaty
# Отработанные водители на телефоне (владелец, 25.09.2026): вкладка на каждый итог
# и «Не дозвонились» — строки, которые АТС закрыла без разговора. На вкладку уходит
# не больше стольких последних: телефону хватает, а ответ не раздувается к концу месяца.
WORKED_NO_ANSWER = "no_answer"
WORKED_NO_ANSWER_NAME = "Не дозвонились"
WORKED_NO_ANSWER_COLOR = "#8E8E93"
WORKED_TAB_LIMIT = 300
# Поиск по ФИО на вкладках (владелец, 12.10.2026): запрос длиннее — обрезается.
WORKED_QUERY_MAX = 100

# Отделы, чья работа — этот обзвон (удалённый колл-центр). Глава такого отдела
# управляет разделом без роли админа. Отдел считается «своим» для раздела также,
# если ему уже заведены настройки обзвона или его телефония — Binotel: код в
# карточке отдела проставляют не всегда, и одним кодом периметр не удержать.
DIAL_LIST_DEPARTMENT_CODES = frozenset({"remote_cc"})

# Пилот: пока список не пуст, раздел руководителя открыт ТОЛЬКО этим логинам —
# даже админам его не показываем. Был {"alfa330"} с 22.09.2026 (владелец смотрел
# сам); 25.09.2026 пилот снят — раздел открыт админам и главам по общим правилам.
# Ручек телефона это не касается: там свои включатели у отдела и оператора.
DIAL_LIST_PILOT_LOGINS = frozenset()

# Главы этих отделов видят весь раздел, как админы: удалённый КЦ работает под
# крылом СЗоВ (решение владельца 25.09.2026). Код отдела — в карточке отдела.
DIAL_LIST_OVERSEER_DEPARTMENT_CODES = frozenset({"szov"})

# Кто сажает на линию сотрудника ДРУГОГО отдела и снимает его с неё (решение
# владельца 07.10.2026: «добавлять на линию любого сотрудника по ФИО, доступ у главы
# отдела СЗоВ и у суперадминов»): суперадмины и главы этих отделов. Остальные
# руководители раздела выбирают только из сотрудников отдела линии. Свой список, а
# не DIAL_LIST_OVERSEER_DEPARTMENT_CODES: новый отдел-куратор не должен получить
# это молча, заодно с доступом к разделу.
DIAL_LIST_SEAT_ANYONE_HEAD_CODES = frozenset({"szov"})

# Супервайзеры этих отделов работают в разделе так же, как глава СЗоВ: весь раздел и
# посадка на линию сотрудника другого отдела (решение владельца 10.10.2026: «открыть
# раздел удаленный КЦ всем супервайзерам СЗоВ с полным доступом»). Отдел — тот, где
# СВ числится (users.department_id). Тот же список на фронте — в App.jsx.
DIAL_LIST_FULL_ACCESS_SUPERVISOR_DEPARTMENT_CODES = frozenset({"szov"})
SEAT_ANYONE_DENIED = ("Сотрудника другого отдела сажают на линию и снимают с неё глава и супервайзеры СЗоВ "
                      "и суперадмины")
# «Привязку к линии вызывающий не читал»: None занято под «прочитал — её нет».
MEMBER_NOT_GIVEN = object()


def pilot_allows(login):
    """Пуст ли пилотный список либо есть ли логин в нём."""
    if not DIAL_LIST_PILOT_LOGINS:
        return True
    return str(login or "").strip().lower() in DIAL_LIST_PILOT_LOGINS


def full_access_supervisor(supervisor_department_code):
    """СВ отдела из DIAL_LIST_FULL_ACCESS_SUPERVISOR_DEPARTMENT_CODES. Код отдела
    передают только для супервайзера: у остальных он None."""
    code = str(supervisor_department_code or "").strip().lower()
    return code in DIAL_LIST_FULL_ACCESS_SUPERVISOR_DEPARTMENT_CODES


class DialListError(Exception):
    """Ошибка для человека: текст + HTTP-статус."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = int(status)


def map_disposition(disposition):
    """disposition Binotel → результат строки ('' — исход ещё не финальный)."""
    value = str(disposition or "").strip().upper()
    if value in NON_FINAL_DISPOSITIONS:
        return ""
    if value in ANSWERED_DISPOSITIONS:
        return "answered"
    if value in BUSY_DISPOSITIONS:
        return "busy"
    if value in NO_ANSWER_DISPOSITIONS:
        return "no_answer"
    return "other"


def clean_auto_hangup(value):
    """Ключ auto_hangup события ended → 'ring_timeout' | 'announcement' | '' (ключа нет
    или в нём незнакомое слово — телефон плечо сам не клал)."""
    text = str(value or "").strip().lower()
    return text if text in AUTO_HANGUPS else ""


def attempt_talked(billsec=0, auto_hangup="", talk_detected=None):
    """Разговор ли попытка, которую Binotel закрыл ответом (ANSWER): не сообщение сети
    и, если телефон разговора не услышал (talk_detected False), не короче секунды. Телефон
    до 3.22.34 ничего не сообщает (auto_hangup пуст, talk_detected None) — для него ответ
    Binotel и есть разговор, как раньше. В SQL то же — ATTEMPT_TALK_SQL."""
    if (auto_hangup or "") == AUTO_HANGUP_ANNOUNCEMENT:
        return False
    return talk_detected is not False or _to_int(billsec) >= 1


def attempt_result(disposition, billsec=0, auto_hangup="", talk_detected=None):
    """Исход попытки для строки выдачи, лида и журнала: map_disposition с поправкой на
    то, что услышал телефон. Ответ Binotel без разговора (attempt_talked) — недозвон:
    сообщение сети или мгновенный сброс без единой реплики. '' — исход ещё не финальный."""
    result = map_disposition(disposition)
    if result == "answered" and not attempt_talked(billsec, auto_hangup, talk_detected):
        return "no_answer"
    return result


def _as_utc(value):
    """Момент из базы или кэша → datetime с поясом (без пояса — UTC); не момент — None."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def live_verdict(state, seen, leg_over_at=None, leg_over=None):
    """Что с живой попыткой по данным опроса Binotel (live_calls.Seen или None) → (решение, звонок).

    state — состояние попытки в базе; leg_over_at — когда кончилось плечо: конец плеча по
    телефону (phone_ended_at) либо следующая попытка оператора — его прежний звонок тогда точно
    кончился. leg_over — кончилось ли плечо, если момент неизвестен (по умолчанию: state ended
    или известен leg_over_at).

        'finish'    исход финальный — закрывать попытку этим звонком. Не ответил, занято, прочее —
                    сразу. Ответ (ANSWER) — только когда плечо уже кончилось и данные получены не
                    раньше чем через ANSWER_SETTLE_SEC после его конца: Binotel отдаёт ANSWER и
                    посреди разговора, с неполной длительностью (12.10.2026).
        'talking'   водитель говорит: ONLINE, или ANSWER, пока плечо по телефону идёт — это
                    разговор, а не финал;
        'settling'  ответ был, плечо кончилось, ждём данных после его конца — исход вот-вот;
        'dialing'   АТС ещё набирает водителя (CALLING, RINGING, пустой статус);
        'unknown'   данных нет: не спрашивали, ошибка, Binotel звонка не отдаёт.

    Звонок (dict) — только у 'finish'."""
    call = getattr(seen, "call", None)
    if not isinstance(call, dict):
        return "unknown", None
    live = str(call.get("disposition") or "").strip().upper()
    result = map_disposition(live)
    if not result:
        return ("talking" if live == "ONLINE" else "dialing"), None
    if result != "answered":
        return "finish", call
    over_at = _as_utc(leg_over_at)
    if leg_over is None:
        leg_over = state == "ended" or over_at is not None
    if not leg_over:
        return "talking", None
    seen_at = _as_utc(getattr(seen, "wall", None))
    if over_at is not None and (seen_at is None or seen_at < over_at + timedelta(seconds=ANSWER_SETTLE_SEC)):
        return "settling", None
    return "finish", call


def leg_over_moment(state, ended_at, superseded_at):
    """Когда кончилось плечо попытки — leg_over_at для live_verdict. У ended — конец плеча по
    телефону (phone_ended_at: часы приложения, как у данных опроса). Иначе — начало следующего
    звонка оператора (superseded_at: requested_at новой попытки, часы базы) плюс
    SUPERSEDED_SETTLE_MARGIN_SEC — запас на расхождение часов базы и приложения. None — плечо,
    насколько известно, ещё идёт."""
    if state == "ended":
        return ended_at
    over = _as_utc(superseded_at)
    return over + timedelta(seconds=SUPERSEDED_SETTLE_MARGIN_SEC) if over is not None else None


# Текст ошибки Binotel (бывает, с номером водителя) телефону не уходит (13.10.2026): оператору —
# только код, полный текст — в журнал сервера; в api_error попытки — без длинных чисел.
_BINOTEL_CODE_RE = re.compile(r"\bcode=(\d{1,6})\b")
_LONG_DIGITS_RE = re.compile(r"\d{6,}")


def binotel_error_code(text):
    """Код ошибки Binotel из текста исключения клиента («… code=104: …») или None."""
    m = _BINOTEL_CODE_RE.search(str(text or ""))
    return int(m.group(1)) if m else None


def scrub_digits(text):
    """Длинные числа (6+ цифр подряд: номер телефона, ИИН) → «…». Для api_error попытки — его
    видит руководитель в журнале и карточке водителя."""
    return _LONG_DIGITS_RE.sub("…", str(text or ""))


def parse_phone_version(value):
    """Заголовок X-ICore-Phone-Version → версия кортежем чисел, (3, 22, 34), или None:
    заголовка нет либо в нём не «число.число[.число[.число]]». Строго: всё, в чём версию
    не узнать наверняка, считается её отсутствием — так безопаснее (phone_guards_speech).
    Для журнала и разбора; решение об автосбросе номер версии не сравнивает."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _PHONE_VERSION_RE.fullmatch(text):
        return None
    return tuple(int(part) for part in text.split("."))


def phone_guards_speech(value):
    """Есть ли у телефона защита автосброса по речи: он прислал заголовок
    X-ICore-Phone-Version с разборчивой версией — любой, хоть «3.22.33» у сборки до
    выпуска. Номер не сравниваем: заголовок шлют только телефоны с защитой, ни один
    выпущенный его не шлёт. Заголовка нет или он неразборчив — это выпущенный 3.22.33:
    автосброса ему не даём (get_state, phone_event)."""
    return parse_phone_version(value) is not None


def announcement_ms():
    """Поле состояния announcement_ms из окружения сервера (ANNOUNCEMENT_MS_ENV)."""
    raw = (os.getenv(ANNOUNCEMENT_MS_ENV) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return ANNOUNCEMENT_MS_DEFAULT
    return value if 0 <= value <= ANNOUNCEMENT_MS_MAX else ANNOUNCEMENT_MS_DEFAULT


def announcement_sit_enabled():
    """Поле состояния announcement_sit из окружения сервера (ANNOUNCEMENT_SIT_ENV): класть ли плечо
    по тону SIT. По умолчанию включено; 0/false/no/off — выключить, мусор — включено."""
    return (os.getenv(ANNOUNCEMENT_SIT_ENV) or "1").strip().lower() not in ("0", "false", "no", "off")


def announcement_mode(ms=None):
    """Поле состояния announcement_mode: 'auto' | 'suggest' | 'off' из окружения сервера
    (ANNOUNCEMENT_MODE_ENV), без учёта регистра; мусор — ANNOUNCEMENT_MODE_DEFAULT. ms — уже
    прочитанный announcement_ms (по умолчанию — из окружения): 0 — распознавание по речи
    выключено целиком, режим тогда 'off', что бы ни стояло в переменной."""
    if (announcement_ms() if ms is None else ms) == 0:
        return "off"
    raw = (os.getenv(ANNOUNCEMENT_MODE_ENV) or "").strip().lower()
    return raw if raw in ANNOUNCEMENT_MODES else ANNOUNCEMENT_MODE_DEFAULT


def clean_announcement_by(value):
    """Ключ announcement_by события ended → 'auto' | 'sit' | 'operator' | '' (ключа нет, не строка
    или незнакомое слово)."""
    text = value.strip().lower() if isinstance(value, str) else ""
    return text if text in ANNOUNCEMENT_BY_VALUES else ""


def binotel_not_answered(disposition):
    """Финальное ли «не дозвонились» от Binotel (NOT_ANSWERED_FINAL_DISPOSITIONS): занято, не
    ответил, отмена, канал недоступен, перегрузка. Промежуточные статусы и UNKNOWN — нет."""
    return str(disposition or "").strip().upper() in NOT_ANSWERED_FINAL_DISPOSITIONS


def speech_guard_enabled():
    """Поле состояния speech_guard из окружения сервера (SPEECH_GUARD_ENV); по умолчанию включена."""
    return (os.getenv(SPEECH_GUARD_ENV) or "1").strip().lower() not in ("0", "false", "no", "off")


def ring_timeout_rings(value):
    """Гудков до сброса из sip_department_config.ring_timeout_rings: NULL (не задано)
    и мусор — по умолчанию, иначе 0..RING_TIMEOUT_RINGS_MAX (0 — не сбрасывать)."""
    if value is None or isinstance(value, bool):
        return RING_TIMEOUT_RINGS_DEFAULT
    try:
        rings = int(value)
    except (TypeError, ValueError):
        return RING_TIMEOUT_RINGS_DEFAULT
    return max(0, min(rings, RING_TIMEOUT_RINGS_MAX))


def sign_check_due(iin, checked_at, sign_error, now, fresh_minutes):
    """Спросить ли Sapar о водителе прямо сейчас: ИИН есть, а проверки не было, она
    старше fresh_minutes или кончилась ошибкой. Время проверки сдвигает и отказ Sapar
    по ИИН, поэтому «проверен недавно» без пустой ошибки ничего не значит."""
    if not str(iin or "").strip():
        return False
    if checked_at is None or str(sign_error or "").strip():
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at < now - timedelta(minutes=fresh_minutes)


# «Позвонить ещё раз» (владелец, 12.10.2026: «ещё раз позвонить любому из водителей»; до того,
# с 11.10.2026, — «Перезвонить» раньше срока, только по итогу «Перезвонить») — своему водителю:
# последним ему звонил этот оператор, с любым итогом или без него. Решение одно и для ручки
# recall, и для строк вкладок итогов (operator_worked → can_call_again, can_recall):
# recall_verdict по колонкам _RECALL_COLUMNS.
RECALL_OK = "ok"
RECALL_ISSUED_MINE = "issued_mine"
RECALL_REFUSALS = {
    "signed": "Водитель уже подписал документы — звонить не нужно",
    "excluded": "Руководитель исключил водителя из обзвона",
    "issued_other": "Водитель уже в списке у другого оператора",
    "not_own": "Позвонить ещё раз можно только водителю, которому последним звонили вы",
    "taken": "Этим водителем уже занимается другой оператор",
}
# Водитель из базы отдела, где оператор работал раньше (13.10.2026): строка на вкладке итогов
# остаётся, «Позвонить ещё раз» — нет (operator_worked).
RECALL_OTHER_DEPARTMENT = "Водитель из другого отдела"


def recall_verdict(columns, operator_id):
    """Можно ли оператору позвонить водителю ещё раз. columns — значения _RECALL_COLUMNS
    по порядку: подписал (или на проверке у Яндекса), исключён, открытая строка (id и
    чья), кто звонил водителю последним (не отменённая попытка — с итогом или без),
    «перезвонить» ли её итог, звонил ли после неё другой оператор.

    RECALL_OK — можно; RECALL_ISSUED_MINE — водитель и так в списке этого оператора;
    иначе ключ RECALL_REFUSALS. Порядок — как у ручки: подпись и исключение первыми.
    Итог последней попытки на решение не влияет (с 12.10.2026 — любому своему): «перезвонить»
    ли он, читает только журнал сервера у ручки, а кнопка «Перезвонить» старого телефона
    (can_recall) смотрит на итог своей строки."""
    signed, excluded, _issued_id, issued_to, owner_id, _owner_requeue, taken_after = columns
    operator_id = int(operator_id)
    if signed:
        return "signed"
    if excluded:
        return "excluded"
    if issued_to is not None:
        return RECALL_ISSUED_MINE if int(issued_to) == operator_id else "issued_other"
    if owner_id is None:
        return "not_own"
    if int(owner_id) != operator_id or taken_after:
        return "taken"
    return RECALL_OK


def search_key(text):
    """ФИО или запрос поиска → вид для сравнения: регистр сложен (casefold), «ё» — «е»,
    пробелы схлопнуты, по краям их нет. В Python, а не ILIKE: база с локалью C кириллицу
    в регистре не сравнивает (как и сверку типов итога в _save_subtypes)."""
    return " ".join(str(text or "").casefold().replace("ё", "е").split())


def name_matches(name, query):
    """Подходит ли ФИО под поиск: каждое слово запроса — часть ФИО, в любом порядке
    («иван петр» найдёт «Петров Иван»). Пустой запрос подходит всем."""
    words = search_key(query).split()
    if not words:
        return True
    key = search_key(name)
    return all(word in key for word in words)


def operator_sign_status(sign_status, signed_at):
    """Подписание для телефона из строки лида → (status, signed, label): status —
    'signed' | 'processing' | 'not_signed' | 'unknown', signed — True / False / None (не
    знаем). Причина «не подписал» — словами SIGN_STATUS_LABELS; не проверяли или статус
    незнакомый — «Не удалось проверить»."""
    code = str(sign_status or "").strip()
    if signed_at is not None or code == signing.SIGNED:
        return "signed", True, OPERATOR_SIGN_LABELS["signed"]
    if code == signing.PROCESSING:
        return "processing", True, OPERATOR_SIGN_LABELS["processing"]
    if code == signing.UNSIGNED:
        return "not_signed", False, OPERATOR_SIGN_LABELS["not_signed"]
    if code in OPERATOR_SIGN_DETAILED:
        detail = signing.SIGN_STATUS_LABELS[code]
        return "not_signed", False, f"{OPERATOR_SIGN_LABELS['not_signed']} · {detail[:1].lower()}{detail[1:]}"
    return "unknown", None, OPERATOR_SIGN_LABELS["unknown"]


def resolve_enabled(personal, department):
    """Эффективное включение: персональное → отдела → выключено."""
    if personal is not None:
        return bool(personal)
    if department is not None:
        return bool(department)
    return False


def webhook_env_token():
    return (os.getenv(WEBHOOK_TOKEN_ENV) or "").strip()


# Больше стольких полей вебхука не разбираем и не храним: у Binotel их десятки (данные
# сотрудника, клиента, история плеч), нам нужны четыре.
WEBHOOK_FIELDS_MAX = 200


def webhook_fields(form):
    """Поля вебхука Binotel → плоский словарь {имя: строка}.

    «API Call Completed» Binotel шлёт формой PHP-вида — callDetails[generalCallID],
    callDetails[disposition], callDetails[billsec], callDetails[waitsec], callDetails[historyData][0][…]
    и т. д.: такие имена берём как есть. JSON-объект разворачивается в те же имена:
    {"callDetails": {"generalCallID": 1}} → {"callDetails[generalCallID]": "1"}. Прежние плоские
    поля (generalCallID, disposition, …) тоже остаются как есть."""
    flat = {}

    def put(key, value, depth=0):
        if len(flat) >= WEBHOOK_FIELDS_MAX:
            return
        if isinstance(value, dict) and depth < 6:
            for k, v in value.items():
                put(f"{key}[{k}]" if key else str(k), v, depth + 1)
        elif isinstance(value, (list, tuple)) and depth < 6:
            for i, v in enumerate(value):
                put(f"{key}[{i}]", v, depth + 1)
        else:
            flat[str(key)[:128]] = None if value is None else str(value)[:500]

    if form is None or not hasattr(form, "keys"):
        return flat
    for key in list(form.keys()):
        try:
            value = form.get(key)
        except AttributeError:
            value = form[key]
        put(str(key), value)
    return flat


def webhook_value(fields, name):
    """Значение поля звонка из webhook_fields: плоское имя (generalCallID) или то же внутри
    callDetails[…], без учёта регистра (generalCallId). Вложенные глубже
    (callDetails[historyData][0][…]) не в счёт: там отдельные плечи, а не звонок."""
    want = str(name).lower()
    for key, value in fields.items():
        lowered = key.lower()
        if (lowered == want or lowered == f"calldetails[{want}]") and value not in (None, ""):
            return value
    return None


def webhook_requires_binotel_ip():
    return (os.getenv(WEBHOOK_REQUIRE_BINOTEL_IP_ENV) or "1").strip().lower() not in ("0", "false", "no", "off")


def webhook_proxy_hops():
    """Сколько доверенных прокси перед приложением (WEBHOOK_PROXY_HOPS_ENV): по умолчанию один —
    прокси Render; мусор и число вне 0..WEBHOOK_PROXY_HOPS_MAX — по умолчанию."""
    raw = (os.getenv(WEBHOOK_PROXY_HOPS_ENV) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return WEBHOOK_PROXY_HOPS_DEFAULT
    return value if 0 <= value <= WEBHOOK_PROXY_HOPS_MAX else WEBHOOK_PROXY_HOPS_DEFAULT


def webhook_remote_addr(forwarded, peer):
    """Адрес отправителя вебхука для сверки со списком серверов Binotel: тот, что дописал в
    X-Forwarded-For последний доверенный прокси, — hops-й справа (webhook_proxy_hops), как у
    werkzeug ProxyFix(x_for=hops). Левые записи цепочки пишет сам клиент — им не верим. Записей
    меньше, чем прокси (или 0 прокси), — адрес соединения (peer). Цепочку целиком ручка пишет в
    журнал при отказе — по ней видно, откуда на самом деле приходит Binotel."""
    hops = webhook_proxy_hops()
    chain = [part.strip() for part in str(forwarded or "").split(",") if part.strip()]
    if hops <= 0 or len(chain) < hops:
        return str(peer or "").strip()
    return chain[-hops]


def _utcnow():
    return datetime.now(timezone.utc)


def _today():
    """Сегодняшний день по Алматы — для подписей «сегодня, 15:14» / «вчера, 09:12»."""
    return datetime.now(PERIOD_TZ).date()


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _iso_utc(value):
    """Момент → ISO-8601 в UTC (без пояса — уже UTC, как sign_check_due); не момент — None."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _sid(value):
    return None if value is None else str(value)


def _subtypes_from_json(value):
    """Типы итога из json_agg-колонки (_outcome_rows) → список словарей.
    psycopg2 сам разбирает json; строка — если драйвер отдал текст."""
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return [{"id": _sid(s.get("id")), "name": s.get("name") or "", "position": int(s.get("position") or 0),
             "is_active": bool(s.get("is_active")), "used": int(s.get("used") or 0)}
            for s in (value or []) if isinstance(s, dict)]


# Сколько номеров строк с ошибкой возвращать загрузившему («строки 5, 17, 23…»):
# этого хватает, чтобы найти их в файле, а ответ не растёт с размером файла.
ROW_SAMPLE_MAX = 10


def clean_iin(raw):
    """ИИН из файла → 12 цифр либо '' (нет или с ошибкой). 11 цифр — ведущий ноль,
    потерянный по дороге через Excel/CSV у родившихся в 2000-х: дополняем, если
    дополненный сходится по контрольной цифре."""
    value, error = iin_rules.validate(raw)
    if value:
        return value
    digits = iin_rules.normalize(raw)
    if error == "length" and digits.isdigit() and len(digits) == iin_rules.IIN_LENGTH - 1:
        value, _ = iin_rules.validate("0" + digits)
        if value:
            return value
    return ""


def mask_iin(iin):
    """ИИН для руководителя — только хвост, как и номер: полный есть в файле загрузки."""
    digits = "".join(ch for ch in str(iin or "") if ch.isdigit())
    return f"•••• •••• {digits[-4:]}" if len(digits) >= 4 else ""


def mask_phone(phone_norm):
    """Номер для журнала руководителя: только хвост. Полный номер из раздела не
    выходит ни в одну ручку — ни оператору, ни руководителю: руководитель
    удалённого КЦ тоже удалёнщик, а файл с номерами у загрузившего и так есть."""
    digits = "".join(ch for ch in str(phone_norm or "") if ch.isdigit())
    if len(digits) < 4:
        return "•••"
    tail = digits[-4:]
    head = "+7 ••• ••• " if len(digits) == 11 and digits.startswith("7") else "••• "
    return f"{head}{tail[:2]} {tail[2:]}"


def current_period():
    """Первый день текущего месяца по Алматы — база «по умолчанию»."""
    today = datetime.now(PERIOD_TZ).date()
    return today.replace(day=1)


def parse_period(value, allow_all=False):
    """'YYYY-MM' или 'YYYY-MM-DD' → первый день месяца (date). Пусто → None
    (текущий месяц у вызывающего). 'all' → 'all', если разрешено."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    if allow_all and text == "all":
        return "all"
    for fmt in ("%Y-%m", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().replace(day=1)
        except ValueError:
            continue
    raise DialListError("period: ожидается месяц в виде YYYY-MM")


def worked_tab(state, result, outcome_id, attempt_state):
    """На какую вкладку телефона попадает строка выдачи; None — её там нет.

    Правило зеркальное к очереди на телефоне (DialList::InQueue): строка в очереди,
    пока она issued и по ней нет итога, ждущего исхода от АТС. Итог оператора важнее
    исхода АТС. Строку, которую руководитель вернул в список, выдаёт issued при уже
    завершённой попытке с итогом — она снова в очереди, а не на вкладке итога.
    Разговор без итога (answered) на вкладки не попадает: пока итог ждут
    (pending_outcome), телефон держит его в очереди.
    """
    if outcome_id and (state == "done" or attempt_state != "finished"):
        return str(outcome_id)
    if state == "done" and result != "answered":
        return WORKED_NO_ANSWER
    return None


def at_label(value, today):
    """«сегодня, 14:05» / «24.09, 14:05» по Алматы — телефону не нужно разбирать даты."""
    if not isinstance(value, datetime):
        return ""
    local = value.astimezone(PERIOD_TZ)
    day = "сегодня" if local.date() == today else local.strftime("%d.%m")
    return f"{day}, {local:%H:%M}"


def checked_at_label(value, today):
    """Когда Sapar отвечал о водителе и когда оператор оставил комментарий
    (last_comment_label): «сегодня, 14:05» / «вчера, 09:12» / «12.10, 14:05» по Алматы.
    Вчерашний день — словом: «проверено вчера» читается быстрее даты. Вкладки итогов
    (at_label) не трогаем — их подписи телефон уже показывает."""
    if isinstance(value, datetime) and value.astimezone(PERIOD_TZ).date() == today - timedelta(days=1):
        return f"вчера, {value.astimezone(PERIOD_TZ):%H:%M}"
    return at_label(value, today)


def period_label(day):
    """'Сентябрь 2026' для подписей в ответах (фронт может и сам, но телефону проще так)."""
    if not day:
        return ""
    months = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
              "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")
    return f"{months[day.month - 1]} {day.year}"


# Этапы лида в журнале (одно место правды — CASE в _JOURNAL_SQL, здесь подписи).
LEAD_STAGES = {
    "queue": "В очереди",
    "waiting": "Ждёт повтора",
    "issued": "У оператора",
    "answered": "Дозвонились",
    "exhausted": "Не дозвонились",
    "excluded": "Исключён",
    # Подписал документы (Sapar) — обзвон для него окончен, в пул не вернётся.
    "signed": "Подписал документы",
    # Документы «на проверке у Яндекса»: водитель уже подписал (владелец, 11.10.2026),
    # в пул не попадает; Яндекс подтвердит — станет «Подписал документы».
    "sign_wait": "На проверке у Яндекса",
}
# Фильтр журнала «Документы» — по состоянию подписания (signing.py). «Не подписал»
# собирает всё, где подписи нет и она ещё возможна или уже не будет по вине
# документов (не сформированы / отклонены); истёкший срок — отдельно: звонить поздно.
JOURNAL_SIGN_FILTERS = {
    "success": "signed_at IS NOT NULL AND success_attempt_id IS NOT NULL",
    "self": "signed_at IS NOT NULL AND success_resolved_at IS NOT NULL AND success_attempt_id IS NULL",
    "processing": "signed_at IS NULL AND iin <> '' AND sign_status = 'processing'",
    "unsigned": "signed_at IS NULL AND iin <> '' AND sign_status IN ('unsigned', 'not_formed', 'rejected')",
    "expired": "signed_at IS NULL AND iin <> '' AND sign_status = 'expired'",
    "no_docs": "signed_at IS NULL AND iin <> '' AND sign_status = 'no_docs'",
    "not_checked": "signed_at IS NULL AND iin <> '' AND sign_status = ''",
    "no_iin": "iin = ''",
}
JOURNAL_SORTS = {
    "activity": "activity_at DESC, created_at DESC",
    "name": "lower(full_name) ASC, created_at ASC",
    "created": "created_at DESC",
    "attempts": "attempts_total DESC, activity_at DESC",
}
JOURNAL_MAX_LIMIT = 200


class DialListService:
    """Вся логика раздела. `db` — экземпляр Database из database.py."""

    def __init__(self, db, client_factory=None, sign_checker=None, calls_submit=None):
        self.db = db
        self._client_factory = client_factory or self._default_client
        self._clients = {}
        self._clients_lock = threading.Lock()
        # Проверка подписания в Sapar: свой поток, запросы склеиваются (sign_check.py).
        self.sign_checker = sign_checker or SignChecker(db)
        # Живые звонки у Binotel: опрос call-details и его кэш — по одному на компанию
        # (live_calls.CompanyCalls). Сервис в процессе один (bot_schedule2 собирает один
        # blueprint), поэтому кэш сервиса и есть общий кэш процесса. calls_submit — где
        # выполнять обновление (по умолчанию свой поток компании; тесты — синхронно).
        self._calls = {}
        self._calls_lock = threading.Lock()
        self._calls_submit = calls_submit

    # ------------------------------------------------------------ доступ и отделы
    _DEPARTMENTS_SQL = """
        SELECT d.id, d.name, COALESCE(d.code, ''),
               COALESCE(dc.provider, 'asterisk'),
               (s.department_id IS NOT NULL) AS configured,
               COALESCE(s.enabled, FALSE),
               COALESCE(s.portion_size, %s),
               (SELECT COUNT(*) FROM dial_list_leads l WHERE l.department_id = d.id) AS leads_total,
               (SELECT COUNT(*) FROM dial_list_leads l
                 WHERE l.department_id = d.id AND l.status IN ('new', 'in_progress')
                   AND l.signed_at IS NULL AND l.sign_status <> 'processing') AS leads_open
        FROM departments d
        LEFT JOIN sip_department_config dc ON dc.department_id = d.id
        LEFT JOIN dial_list_department_settings s ON s.department_id = d.id
        WHERE COALESCE(d.is_active, TRUE)
          AND {where}
        ORDER BY d.name
    """

    @staticmethod
    def _department_row(r):
        return {
            "department_id": int(r[0]), "department_name": r[1] or "", "code": r[2] or "",
            "provider": r[3] or "asterisk", "configured": bool(r[4]), "enabled": bool(r[5]),
            "portion_size": int(r[6]), "leads_total": int(r[7] or 0), "leads_open": int(r[8] or 0),
        }

    def list_departments(self, department_ids=None):
        """Отделы раздела: ЯВНО подключённые (есть строка настроек) либо с кодом из
        DIAL_LIST_DEPARTMENT_CODES. «Отдел на Binotel» сам по себе в раздел не попадает —
        иначе здесь оказывался бы любой отдел с этой АТС (замечание владельца 23.09.2026).
        department_ids=None — все такие (админ)."""
        params = [DEFAULT_PORTION_SIZE]
        where = "(s.department_id IS NOT NULL OR LOWER(COALESCE(d.code, '')) = ANY(%s))"
        params.append(sorted(DIAL_LIST_DEPARTMENT_CODES))
        if department_ids is not None:
            where += " AND d.id = ANY(%s)"
            params.append([int(x) for x in department_ids])
        with self.db._get_cursor() as cur:
            cur.execute(self._DEPARTMENTS_SQL.format(where=where), params)
            return [self._department_row(r) for r in cur.fetchall()]

    def candidate_departments(self):
        """Отделы, которые МОЖНО подключить: телефония Binotel, но в разделе их ещё нет.
        Из них админ выбирает в кнопке «Подключить отдел»."""
        params = [DEFAULT_PORTION_SIZE, sorted(DIAL_LIST_DEPARTMENT_CODES)]
        where = ("COALESCE(dc.provider, 'asterisk') = 'binotel' AND s.department_id IS NULL "
                 "AND NOT (LOWER(COALESCE(d.code, '')) = ANY(%s))")
        with self.db._get_cursor() as cur:
            cur.execute(self._DEPARTMENTS_SQL.format(where=where), params)
            return [self._department_row(r) for r in cur.fetchall()]

    def enroll_department(self, department_id, changed_by=None):
        """Подключить отдел к разделу: завести строку настроек (режим пока выключен)."""
        department_id = int(department_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT d.id, COALESCE(dc.provider, 'asterisk')
                FROM departments d
                LEFT JOIN sip_department_config dc ON dc.department_id = d.id
                WHERE d.id = %s AND COALESCE(d.is_active, TRUE)
            """, (department_id,))
            row = cur.fetchone()
            if not row:
                raise DialListError("Отдел не найден", 404)
            if row[1] != "binotel":
                raise DialListError(
                    "Телефония отдела не Binotel: сначала переключите провайдера отдела в «Настройках SIP»", 409)
            cur.execute("""
                INSERT INTO dial_list_department_settings (department_id, enabled, updated_by)
                VALUES (%s, FALSE, %s)
                ON CONFLICT (department_id) DO NOTHING
            """, (department_id, changed_by))
        log.info("dial_list: отдел %s подключён к разделу (пользователь %s)", department_id, changed_by)
        return self.department_settings(department_id)

    def manager_scope(self, is_admin, headed_department_ids, login=None, supervisor_department_code=None):
        """Что видит руководитель: None — всё (админ; глава отдела из
        DIAL_LIST_OVERSEER_DEPARTMENT_CODES, то есть СЗоВ; СВ СЗоВ —
        full_access_supervisor); список id — глава отдела периметра видит свои
        отделы; [] — раздел не его. Пилот (DIAL_LIST_PILOT_LOGINS) снят 25.09.2026:
        список пуст, правило общее для всех."""
        if not pilot_allows(login):
            return []
        if is_admin or full_access_supervisor(supervisor_department_code):
            return None
        headed = sorted({int(x) for x in (headed_department_ids or [])})
        if not headed:
            return []
        if self._heads_overseer(headed):
            return None
        return [d["department_id"] for d in self.list_departments(headed)]

    def _heads_overseer(self, department_ids):
        """Возглавляет ли человек отдел-куратор раздела (код из DIAL_LIST_OVERSEER_DEPARTMENT_CODES)."""
        return self._heads_department_with_code(department_ids, DIAL_LIST_OVERSEER_DEPARTMENT_CODES)

    def _heads_department_with_code(self, department_ids, codes):
        """Есть ли среди возглавляемых отделов отдел с кодом из `codes`."""
        if not department_ids or not codes:
            return False
        with self.db._get_cursor() as cur:
            cur.execute("SELECT LOWER(COALESCE(code, '')) FROM departments WHERE id = ANY(%s)",
                        ([int(x) for x in department_ids],))
            return any((r[0] or "") in codes for r in cur.fetchall())

    def can_seat_anyone(self, is_super_admin, headed_department_ids, supervisor_department_code=None):
        """Можно ли запросившему сажать на линию сотрудника ДРУГОГО отдела и снимать
        его с неё: суперадмин, глава отдела из DIAL_LIST_SEAT_ANYONE_HEAD_CODES (СЗоВ)
        либо СВ СЗоВ (full_access_supervisor, с 10.10.2026). Роль «админ» сама по себе
        этого не даёт — решение владельца 07.10.2026."""
        if is_super_admin or full_access_supervisor(supervisor_department_code):
            return True
        headed = sorted({int(x) for x in (headed_department_ids or [])})
        return self._heads_department_with_code(headed, DIAL_LIST_SEAT_ANYONE_HEAD_CODES)

    # ------------------------------------------------------------ сотрудник другого отдела на линии
    # Линия — ресурс отдела удалённого КЦ, а сидеть на ней может сотрудник любого
    # отдела. Своему сотруднику отдела учётка линии пишется в его же SIP-настройки,
    # как раньше. Чужому так нельзя: в users.sip_number и user_sip_settings у него
    # лежит телефония его отдела (у СЗоВ это логин Oktell — по нему работают табло и
    # «Ограничитель»; у отдела продаж — номер локальной АТС), и перезапись сломала бы
    # ему основную работу. Его привязка живёт в dial_list_line_members. Пока она
    # действует, раздел и телефон считают его сотрудником отдела ЛИНИИ: та же база,
    # те же итоги и скрипт, тот же счёт показателей, что у сотрудников удалённого КЦ.

    _LINE_MEMBER_SQL = """
        SELECT m.user_id, m.department_id, m.internal_number, m.sip_login, m.sip_password,
               COALESCE(dc.provider, 'asterisk'), COALESCE(dc.sip_server, ''),
               dc.auto_answer, dc.auto_answer_delay
        FROM dial_list_line_members m
        LEFT JOIN sip_department_config dc ON dc.department_id = m.department_id
        WHERE m.user_id = %s AND m.released_at IS NULL
    """

    def line_member(self, user_id):
        """Действующая привязка сотрудника к линии другого отдела или None.

        Учётка линии отсюда уходит только телефону самого сотрудника
        (/api/operator/sip_settings); в ручки руководителя она не попадает."""
        with self.db._get_cursor() as cur:
            cur.execute(self._LINE_MEMBER_SQL, (int(user_id),))
            r = cur.fetchone()
        if not r:
            return None
        return {
            "user_id": int(r[0]), "department_id": int(r[1]),
            "internal_number": (r[2] or "").strip(),
            "sip_login": r[3] or "", "sip_password": r[4] or "",
            "provider": r[5] or "asterisk", "sip_server": (r[6] or "").strip(),
            # Ярус автоприёма отдела ЛИНИИ; None — отдел его не задавал.
            "auto_answer": None if r[7] is None else bool(r[7]),
            "auto_answer_delay": None if r[8] is None else int(r[8]),
        }

    def dial_operator(self, user_id, operator=None, member=MEMBER_NOT_GIVEN):
        """SIP-карточка сотрудника глазами раздела (или None, если человека нет).

        Сидящему на линии другого отдела подставляются отдел, провайдер и номер
        ЛИНИИ — дальше все правила раздела (включён ли режим, база, итоги, запросы
        в Binotel) работают для него так же, как для сотрудника этого отдела.
        member — уже прочитанная привязка (line_member), чтобы не читать её дважды."""
        operator = operator or self.db.get_sip_operator(int(user_id))
        if not operator:
            return None
        if member is MEMBER_NOT_GIVEN:
            member = self.line_member(user_id)
        if not member:
            return operator
        return dict(operator, department_id=member["department_id"],
                    department_provider=member["provider"],
                    sip_number=member["internal_number"], line_member=True)

    def _department_provider(self, department_id):
        with self.db._get_cursor() as cur:
            cur.execute("SELECT COALESCE(provider, 'asterisk') FROM sip_department_config WHERE department_id = %s",
                        (int(department_id),))
            row = cur.fetchone()
        return (row[0] if row else "") or "asterisk"

    def _inactive_statuses(self):
        """Статусы «не работает» — те же, что у панели SIP и department_users."""
        return [str(s).lower() for s in getattr(self.db, "_SIP_INACTIVE_STATUSES", ("fired",))]

    def _release_member(self, cur, user_id, changed_by=None):
        """Снять действующую привязку человека к линии. Строка остаётся как история,
        пароль линии стирается. Возвращает номер линии или ''."""
        cur.execute("""
            UPDATE dial_list_line_members
            SET released_at = CURRENT_TIMESTAMP, released_by = %s, sip_password = ''
            WHERE user_id = %s AND released_at IS NULL
            RETURNING internal_number
        """, (changed_by, int(user_id)))
        row = cur.fetchone()
        return (row[0] or "") if row else ""

    def _unseat_member(self, user_id, changed_by=None):
        with self.db._get_cursor() as cur:
            return self._release_member(cur, user_id, changed_by)

    def _release_dead_holders(self, cur, department_id, internal_number, changed_by=None):
        """Закрыть привязки УВОЛЕННЫХ к этой линии: человек её уже не держит, а
        действующая строка не дала бы посадить другого и вернула бы его на линию,
        если его восстановят. Привязку работающего сотрудника не трогает."""
        cur.execute("""
            UPDATE dial_list_line_members m
            SET released_at = CURRENT_TIMESTAMP, released_by = %s, sip_password = ''
            FROM users u
            WHERE u.id = m.user_id AND m.department_id = %s AND m.internal_number = %s
              AND m.released_at IS NULL
              AND LOWER(COALESCE(u.status, '')) = ANY(%s)
        """, (changed_by, int(department_id), internal_number, self._inactive_statuses()))

    def _seat_member(self, cur, department_id, user_id, internal_number, login, password, changed_by=None):
        """Посадить сотрудника другого отдела на линию — в транзакции вызывающего:
        прежняя его привязка снимается (один человек — одна линия), привязка
        УВОЛЕННОГО к этой же линии — тоже, и заводится новая."""
        self._release_member(cur, user_id, changed_by)
        self._release_dead_holders(cur, department_id, internal_number, changed_by)
        cur.execute("""
            INSERT INTO dial_list_line_members
                (user_id, department_id, internal_number, sip_login, sip_password, assigned_by)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (int(user_id), int(department_id), internal_number, login, password, changed_by))

    @staticmethod
    def _seat_conflict(exc, internal_number):
        """Гонка двух руководителей, которую отсёк уникальный индекс: какой из двух
        индексов сработал, тот и говорит, что именно произошло."""
        constraint = getattr(getattr(exc, "diag", None), "constraint_name", "") or ""
        if constraint == "uq_dial_list_line_members_user":
            return DialListError("Сотрудника только что посадили на другую линию — обновите список", 409)
        return DialListError(f"Линию {internal_number} только что заняли — обновите список", 409)

    @contextlib.contextmanager
    def _line_lock(self, department_id, internal_number):
        """Одна посадка на линию за раз. Занятость линии лежит в двух местах — в
        SIP-настройках своего сотрудника и в привязке чужого, — и без замка два
        руководителя, одновременно сажающие на одну линию своего и чужого, прошли бы
        обе проверки. Замок живёт до конца транзакции этого курсора."""
        with self.db._get_cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"dial_list_line:{int(department_id)}:{internal_number}",))
            yield cur

    def _line_holder(self, cur, department_id, internal_number, exclude_user_id=None):
        """Кто сейчас держит линию отдела (кроме exclude_user_id): {'id', 'name'} или None.

        Держит её либо сотрудник любого отдела с действующей привязкой к этой линии,
        либо свой сотрудник отдела, у которого она записана в SIP-настройках. Второй
        считается, даже если его самого посадили на линию ДРУГОГО отдела: учётка этой
        линии по-прежнему в его настройках, и после снятия там он вернётся на неё.
        Уволенные линию не держат."""
        department_id = int(department_id)
        cur.execute("""
            SELECT u.id, u.name
            FROM users u
            LEFT JOIN dial_list_line_members m ON m.user_id = u.id AND m.released_at IS NULL
            WHERE u.id <> %s
              AND LOWER(COALESCE(u.status, '')) <> ALL(%s)
              AND ((m.department_id = %s AND m.internal_number = %s)
                   OR (u.department_id = %s AND TRIM(COALESCE(u.sip_number, '')) = %s
                       AND (m.user_id IS NULL OR m.department_id <> %s)))
            ORDER BY u.name
            LIMIT 1
        """, (int(exclude_user_id or 0), self._inactive_statuses(), department_id, internal_number,
              department_id, internal_number, department_id))
        row = cur.fetchone()
        return {"id": row[0], "name": row[1] or ""} if row else None

    def former_operators(self, department_id, exclude_ids=()):
        """Кто обзванивал базу отдела, но в нынешнем составе раздела его нет: снятый с
        линии сотрудник другого отдела, уволенный, переведённый. Нужны фильтру журнала:
        их звонки в показателях остаются, и отобрать по ним водителей должно быть можно
        так же, как по тем, кто на линии сейчас."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT DISTINCT u.id, u.name, COALESCE(u.login, '')
                FROM dial_list_portions p
                JOIN users u ON u.id = p.operator_id
                WHERE p.department_id = %s AND u.id <> ALL(%s)
                ORDER BY u.name
            """, (int(department_id), [int(x) for x in exclude_ids]))
            return [{"id": r[0], "name": r[1] or "", "login": r[2]} for r in cur.fetchall()]

    def line_candidates(self, department_id):
        """Кого ещё можно посадить на линию отдела: действующие сотрудники ДРУГИХ
        отделов, которые на его линиях пока не сидят. Только ФИО, логин и отдел —
        для выбора по ФИО; отдаётся тем, кому это разрешено (can_seat_anyone)."""
        inactive = self._inactive_statuses()
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT u.id, u.name, COALESCE(u.login, ''), COALESCE(dep.name, '')
                FROM users u
                LEFT JOIN departments dep ON dep.id = u.department_id
                LEFT JOIN dial_list_line_members m
                       ON m.user_id = u.id AND m.released_at IS NULL AND m.department_id = %s
                WHERE u.department_id IS DISTINCT FROM %s
                  AND m.user_id IS NULL
                  AND LOWER(COALESCE(u.status, '')) <> ALL(%s)
                ORDER BY dep.name NULLS LAST, u.name
            """, (int(department_id), int(department_id), inactive))
            return [{"id": r[0], "name": r[1] or "", "login": r[2], "department_name": r[3]}
                    for r in cur.fetchall()]

    # ------------------------------------------------------------ линии Binotel
    # Официальный API компании отдаёт по каждой внутренней линии SIP-логин и
    # SIP-пароль (settings/list-of-employees → endpointData). Поэтому «назначить
    # линию сотруднику» — одна кнопка: сервер сам кладёт учётку линии в его
    # SIP-настройки, и телефон регистрируется по логину/паролю iCORE. Наружу
    # (в ответы ручек) ни логин, ни пароль линии не уходят — только номер и статус.

    def _binotel_employees(self, department_id):
        payload = self._client(department_id)._post("settings/list-of-employees", {})
        emps = payload.get("listOfEmployees") or {}
        return list(emps.values()) if isinstance(emps, dict) else list(emps or [])

    @staticmethod
    def _endpoint_of(record):
        ep = record.get("endpointData") if isinstance(record, dict) else None
        return ep if isinstance(ep, dict) and str(ep.get("internalNumber") or "").strip() else None

    def _line_status_hint(self, department_id, internal_number):
        """Что Binotel сам думает о линии оператора — для текста ошибки при коде 150.

        Код 150 «Can't call to the ext» приходит и у ЗАРЕГИСТРИРОВАННОГО телефона:
        Binotel помечает линию «не в сети», когда перестаёт до неё доходить (25.09.2026,
        линия 904: не в сети 16:43–17:17 при живом REGISTER и ответах на OPTIONS).
        Разбор журналов 11.10.2026 (линия 913, роутер с SIP ALG): после сорванного плеча
        Binotel не достаёт до телефона до его следующей регистрации — это минуты (до ~5),
        а не «свой обход Binotel за 30–40 минут», как считалось раньше; срывается каждое
        плечо — режет роутер оператора. Оператору нужно знать, ждать или чинить:
        «проверьте регистрацию» в первом случае отправляет его чинить исправное. Статус
        читаем из settings/list-of-employees → endpointData.status; не получили —
        прежняя подсказка про регистрацию. Подсказка, не условие: любая ошибка здесь гасится.
        """
        def hhmm(unix_ts):
            try:
                ts = int(unix_ts or 0)
            except (TypeError, ValueError):
                return "?"
            return datetime.fromtimestamp(ts, PERIOD_TZ).strftime("%H:%M") if ts > 0 else "?"

        number = str(internal_number or "").strip()
        try:
            for rec in self._binotel_employees(department_id):
                ep = self._endpoint_of(rec)
                if not ep or str(ep.get("internalNumber") or "").strip() != number:
                    continue
                st = ep.get("status") if isinstance(ep.get("status"), dict) else {}
                sip = st.get("sip") if isinstance(st.get("sip"), dict) else {}
                state = str(st.get("preparedStatus") or sip.get("status") or "").strip().lower()
                if state in ("online", "inuse"):
                    return "Binotel при этом видит линию в сети — повторите через минуту."
                return (f"Binotel считает линию не в сети с {hhmm(sip.get('updatedAt'))} "
                        f"(последний раз в сети {hhmm(ep.get('wasOnlineAt'))}). Обычно линия "
                        "возвращается в сеть со следующей регистрацией телефона — это до 5 минут, "
                        "подождите и повторите. Если так после каждого звонка, скорее всего, ваш роутер "
                        "режет SIP (SIP ALG): отключите SIP ALG в роутере или подключитесь к другой сети.")
        except Exception as exc:
            log.warning("dial_list: статус линии %s у Binotel не получен: %s", number, exc)
        return "Телефон не зарегистрирован в Binotel — проверьте регистрацию и повторите"

    def department_users(self, department_id):
        """Операторы раздела у отдела: его сотрудники (все роли: тест ведёт и сам
        владелец) и сотрудники других отделов, посаженные на его линии. У каждого —
        линия (sip_number) и персональное включение обзвона (None — как у отдела).
        guest — человек из другого отдела, тогда department_name — его отдел."""
        # «Работает ли человек» в iCORE — это users.status ('working' / 'fired' / …),
        # а не is_active: тот у большинства живых сотрудников FALSE (так пропал из
        # списка первый же тест-оператор). Список уволенных — тот же, что у панели SIP.
        inactive = list(getattr(self.db, "_SIP_INACTIVE_STATUSES", ("fired",)))
        department_id = int(department_id)
        with self.db._get_cursor() as cur:
            # Линия посаженного — из его привязки, а не users.sip_number: там номер
            # телефонии его собственного отдела. Свой сотрудник отдела, которого
            # посадили на линию ДРУГОГО отдела, из этого списка уходит — работает он там.
            cur.execute("""
                SELECT u.id, u.name, COALESCE(u.login, ''), COALESCE(u.role, ''),
                       COALESCE(m.internal_number, u.sip_number, ''), COALESCE(u.status, ''), ds.enabled,
                       (m.user_id IS NOT NULL AND u.department_id IS DISTINCT FROM m.department_id),
                       COALESCE(dep.name, '')
                FROM users u
                LEFT JOIN dial_list_line_members m ON m.user_id = u.id AND m.released_at IS NULL
                LEFT JOIN departments dep ON dep.id = u.department_id
                LEFT JOIN dial_list_user_settings ds ON ds.user_id = u.id
                WHERE LOWER(COALESCE(u.status, '')) <> ALL(%s)
                  AND ((u.department_id = %s AND (m.user_id IS NULL OR m.department_id = %s))
                       OR m.department_id = %s)
                ORDER BY u.name
            """, (inactive, department_id, department_id, department_id))
            return [{"id": r[0], "name": r[1] or "", "login": r[2], "role": r[3],
                     "sip_number": (r[4] or "").strip(), "status": r[5],
                     "dial_list_enabled": None if r[6] is None else bool(r[6]),
                     "guest": bool(r[7]), "department_name": (r[8] or "") if r[7] else ""}
                    for r in cur.fetchall()]

    def unenroll_department(self, department_id):
        """Отключить отдел от раздела. Только пока по нему нет выдач и базы: иначе
        строка настроек спрятала бы данные, которые ещё могут понадобиться."""
        department_id = int(department_id)
        with self.db._get_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM dial_list_portions WHERE department_id = %s", (department_id,))
            portions = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM dial_list_leads WHERE department_id = %s", (department_id,))
            leads = int(cur.fetchone()[0])
            if portions or leads:
                raise DialListError(
                    f"У отдела уже есть данные обзвона (выдач {portions}, водителей {leads}) — "
                    "выключите режим вместо отключения", 409)
            cur.execute("DELETE FROM dial_list_department_settings WHERE department_id = %s", (department_id,))
        with self._clients_lock:
            self._clients.pop(department_id, None)
        log.info("dial_list: отдел %s отключён от раздела", department_id)
        return {"department_id": department_id, "configured": False}

    def list_lines(self, department_id):
        """Линии компании Binotel + кто из iCORE на них сидит. Без логинов и паролей."""
        department_id = int(department_id)
        users = self.department_users(department_id)
        by_number = {}
        for u in users:
            if u["sip_number"]:
                by_number.setdefault(u["sip_number"], u)
        lines = []
        for rec in self._binotel_employees(department_id):
            ep = self._endpoint_of(rec)
            if not ep:
                continue
            number = str(ep.get("internalNumber")).strip()
            status = ep.get("status") if isinstance(ep.get("status"), dict) else {}
            prepared = str(status.get("preparedStatus") or (status.get("sip") or {}).get("status") or "").lower()
            emp_id = str(rec.get("employeeID") or "").strip()
            linked = bool(emp_id and emp_id != "0" and (rec.get("name") or rec.get("email")))
            holder = by_number.get(number)
            lines.append({
                "internal_number": number,
                "online": prepared == "online",
                "status": prepared or "unknown",
                "tls": str(ep.get("encryptedWithTls") or "0") == "1",
                "was_online_at": _to_int(ep.get("wasOnlineAt")) or None,
                "binotel_employee": {"employee_id": emp_id, "name": rec.get("name") or "",
                                     "email": rec.get("email") or "",
                                     "presence": rec.get("presenceState") or ""} if linked else None,
                # guest — сотрудник другого отдела; его отдел идёт подписью к имени.
                "icore_user": {"id": holder["id"], "name": holder["name"], "login": holder["login"],
                               "guest": bool(holder.get("guest")),
                               "department_name": holder.get("department_name") or ""} if holder else None,
            })
        lines.sort(key=lambda x: (len(x["internal_number"]), x["internal_number"]))
        return lines

    def department_sip_server(self, department_id):
        with self.db._get_cursor() as cur:
            cur.execute("SELECT COALESCE(sip_server, '') FROM sip_department_config WHERE department_id = %s",
                        (int(department_id),))
            row = cur.fetchone()
        return (row[0] or "").strip() if row else ""

    def assign_line(self, department_id, user_id, internal_number, changed_by=None, seat_anyone=False):
        """Посадить сотрудника на линию отдела.

        Свой сотрудник отдела: SIP-логин/пароль линии → его SIP-настройки. Сотрудник
        другого отдела — только при seat_anyone (право считает can_seat_anyone): учётка
        линии → dial_list_line_members, его собственные SIP-настройки не меняются."""
        department_id = int(department_id)
        user_id = int(user_id)
        internal_number = str(internal_number or "").strip()
        if not internal_number:
            raise DialListError("Укажите внутренний номер линии")
        operator = self.db.get_sip_operator(user_id)
        if not operator:
            raise DialListError("Сотрудник не найден", 404)
        own = operator.get("department_id") == department_id
        # Свой сотрудник отдела может сейчас сидеть на линии ДРУГОГО отдела как чужой.
        # Посадка здесь снимет его оттуда (один человек — одна линия), а снимать чужих
        # вправе не каждый руководитель раздела: без этой проверки право обходилось
        # назначением линии человеку в его же собственном отделе.
        member = self.line_member(user_id)
        seated_elsewhere = bool(member) and member["department_id"] != department_id
        if (not own or seated_elsewhere) and not seat_anyone:
            raise DialListError(SEAT_ANYONE_DENIED, 403)
        if str(operator.get("status") or "").strip().lower() in self._inactive_statuses():
            raise DialListError("Сотрудник уволен — на линию его не посадить", 409)
        # Чужого сажают только на линии отделов раздела: иначе привязка легла бы к
        # отделу, которого в разделе нет, — её там не увидеть и не снять.
        if not own and not self.list_departments([department_id]):
            raise DialListError("Отдел не подключён к разделу «Удаленный КЦ»", 404)
        # Провайдер — у отдела ЛИНИИ: у чужого сотрудника в карточке провайдер его отдела.
        provider = operator.get("department_provider") if own else self._department_provider(department_id)
        if (provider or "asterisk") != "binotel":
            raise DialListError("Телефония отдела не Binotel: переключите провайдера отдела в «Настройках SIP»", 409)
        endpoint = None
        for rec in self._binotel_employees(department_id):
            ep = self._endpoint_of(rec)
            if ep and str(ep.get("internalNumber")).strip() == internal_number:
                endpoint = ep
                break
        if not endpoint:
            raise DialListError(f"Линии {internal_number} у компании Binotel нет", 404)
        login = str(endpoint.get("login") or "").strip()
        password = str(endpoint.get("password") or "")
        if not login or not password:
            raise DialListError(f"Binotel не отдал SIP-учётку линии {internal_number}", 502)
        try:
            # Проверка занятости и запись — под одним замком линии (см. _line_lock).
            with self._line_lock(department_id, internal_number) as cur:
                holder = self._line_holder(cur, department_id, internal_number, exclude_user_id=user_id)
                if holder:
                    raise DialListError(f"Линия {internal_number} уже у сотрудника {holder['name']}", 409)
                if own:
                    try:
                        self.db.save_user_sip_settings(user_id, {
                            "sip_number": internal_number,
                            "sip_login": login,
                            "sip_password": password,
                        }, changed_by=changed_by)
                    except ValueError as exc:
                        raise DialListError(str(exc), 400)
                    # Только после удачной записи: отказ в ней не должен оставить человека
                    # без линии. Его привязка «как чужого» сильнее SIP-настроек и держала
                    # бы его на прежней линии; привязка уволенного к этой — мёртвый груз.
                    self._release_member(cur, user_id, changed_by)
                    self._release_dead_holders(cur, department_id, internal_number, changed_by)
                else:
                    self._seat_member(cur, department_id, user_id, internal_number, login, password, changed_by)
        except psycopg2.errors.UniqueViolation as exc:
            raise self._seat_conflict(exc, internal_number)
        log.info("dial_list: сотруднику %s назначена линия Binotel %s (отдел %s%s, назначил %s)", user_id,
                 internal_number, department_id, "" if own else ", сотрудник другого отдела", changed_by)
        return {
            "user_id": user_id, "internal_number": internal_number,
            "sip_server": self.department_sip_server(department_id),
            "guest": not own,
        }

    def release_line(self, department_id, user_id, changed_by=None, seat_anyone=False):
        """Снять сотрудника с линии отдела. Свой сотрудник — очистить его SIP-настройки.
        Сотрудник другого отдела (только при seat_anyone) — снять привязку к линии:
        его собственные SIP-настройки при этом как были, так и остаются."""
        department_id = int(department_id)
        user_id = int(user_id)
        operator = self.db.get_sip_operator(user_id)
        member = self.line_member(user_id) if operator else None
        if member and member["department_id"] == department_id:
            if operator.get("department_id") != department_id and not seat_anyone:
                raise DialListError(SEAT_ANYONE_DENIED, 403)
            self._unseat_member(user_id, changed_by)
            log.info("dial_list: сотрудник %s снят с линии Binotel %s (отдел %s, снял %s)", user_id,
                     member["internal_number"], department_id, changed_by)
            return {"user_id": user_id, "internal_number": ""}
        if not operator or operator.get("department_id") != department_id:
            raise DialListError("Сотрудник не найден в этом отделе", 404)
        try:
            self.db.save_user_sip_settings(user_id, {"sip_number": "", "sip_login": "", "sip_password": ""},
                                           changed_by=changed_by)
        except ValueError as exc:
            raise DialListError(str(exc), 400)
        return {"user_id": user_id, "internal_number": ""}

    # ------------------------------------------------------------ настройки
    def department_settings(self, department_id):
        """Настройки отдела. Секретов здесь нет: ключ API и токен вебхука живут в
        окружении сервера, наружу уходит только признак «задан»."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT enabled, portion_size, max_attempts, retry_after_hours,
                       caller_id_for_employee, updated_at, binotel_company, active_period
                FROM dial_list_department_settings WHERE department_id = %s
            """, (int(department_id),))
            row = cur.fetchone()
        company = ((row[6] or "").strip() if row else "") or DEFAULT_BINOTEL_COMPANY
        active_period = row[7] if row else None
        effective = active_period or current_period()
        settings = {
            "department_id": int(department_id),
            "configured": bool(row),
            "enabled": bool(row[0]) if row else False,
            "portion_size": int(row[1]) if row else DEFAULT_PORTION_SIZE,
            "max_attempts": int(row[2]) if row else DEFAULT_MAX_ATTEMPTS,
            "retry_after_hours": int(row[3]) if row else DEFAULT_RETRY_AFTER_HOURS,
            "caller_id_for_employee": (row[4] or "") if row else "",
            "updated_at": _iso(row[5]) if row else None,
            "binotel_company": company,
            # Какой месяц обзванивается: явно выбранный руководителем или текущий.
            "active_period": active_period.isoformat() if active_period else None,
            "period": effective.isoformat(),
            "period_label": period_label(effective),
            "_period": effective,
        }
        return settings

    def save_department_settings(self, department_id, payload, changed_by=None):
        """Ключа нет в payload — не менять. Секреты сюда не принимаются вовсе:
        ключ API и токен вебхука задаются в окружении сервера."""
        payload = payload or {}
        current = self.department_settings(department_id)

        def _text(key, current_value, limit):
            if payload.get(key) is None:
                return current_value
            return str(payload[key]).strip()[:limit]

        def _int(key, current_value, lo, hi):
            if payload.get(key) is None:
                return current_value
            try:
                number = int(payload[key])
            except (TypeError, ValueError):
                raise DialListError(f"Поле {key}: нужно целое число")
            if number < lo or number > hi:
                raise DialListError(f"Поле {key}: допустимо от {lo} до {hi}")
            return number

        enabled = current["enabled"] if payload.get("enabled") is None else bool(payload["enabled"])
        portion_size = _int("portion_size", current["portion_size"], 1, PORTION_SIZE_MAX)
        max_attempts = _int("max_attempts", current["max_attempts"], 1, 20)
        retry_after_hours = _int("retry_after_hours", current["retry_after_hours"], 0, 24 * 30)
        caller_id = _text("caller_id_for_employee", current["caller_id_for_employee"], 32)
        company = _text("binotel_company", current["binotel_company"], 32).lower() or DEFAULT_BINOTEL_COMPANY
        if not COMPANY_RE.match(company):
            raise DialListError("Компания Binotel: латиница, цифры и подчёркивание, от 2 до 32 символов")
        for key in ("binotel_api_key", "binotel_api_secret", "webhook_token"):
            if str(payload.get(key) or "").strip():
                raise DialListError("Это поле не настраивается в iCORE", 400)
        # active_period: 'YYYY-MM' — обзванивать этот месяц; '' — текущий календарный.
        if "active_period" in payload:
            active_period = parse_period(payload.get("active_period"))
        else:
            active_period = parse_period(current["active_period"])

        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_department_settings (
                    department_id, enabled, portion_size, max_attempts, retry_after_hours,
                    caller_id_for_employee, binotel_company, active_period, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                ON CONFLICT (department_id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    portion_size = EXCLUDED.portion_size,
                    max_attempts = EXCLUDED.max_attempts,
                    retry_after_hours = EXCLUDED.retry_after_hours,
                    caller_id_for_employee = EXCLUDED.caller_id_for_employee,
                    binotel_company = EXCLUDED.binotel_company,
                    active_period = EXCLUDED.active_period,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = EXCLUDED.updated_at
            """, (int(department_id), enabled, portion_size, max_attempts, retry_after_hours,
                  caller_id, company, active_period, changed_by))
        with self._clients_lock:
            self._clients.pop(int(department_id), None)
        return self.department_settings(department_id)

    def user_setting(self, user_id):
        with self.db._get_cursor() as cur:
            cur.execute("SELECT enabled FROM dial_list_user_settings WHERE user_id = %s", (int(user_id),))
            row = cur.fetchone()
        return None if not row or row[0] is None else bool(row[0])

    def save_user_setting(self, user_id, enabled, changed_by=None):
        """enabled: True/False — персонально; None — «как у отдела» (строка удаляется)."""
        with self.db._get_cursor() as cur:
            if enabled is None:
                cur.execute("DELETE FROM dial_list_user_settings WHERE user_id = %s", (int(user_id),))
            else:
                cur.execute("""
                    INSERT INTO dial_list_user_settings (user_id, enabled, updated_by, updated_at)
                    VALUES (%s, %s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                    ON CONFLICT (user_id) DO UPDATE SET
                        enabled = EXCLUDED.enabled,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = EXCLUDED.updated_at
                """, (int(user_id), bool(enabled), changed_by))
        return {"user_id": int(user_id), "enabled": enabled}

    def phone_settings(self, user_id, operator=None, member=MEMBER_NOT_GIVEN):
        """Блок `dial_list` для GET /api/operator/sip_settings.

        Режим имеет смысл только у Binotel: звонок инициирует АТС по API. У
        локальной АТС такого API нет, и телефон не должен показывать вкладку.
        Сидящий на линии другого отдела получает настройки отдела ЛИНИИ (dial_operator)."""
        operator = self.dial_operator(user_id, operator, member=member) or {}
        department_id = operator.get("department_id")
        provider = operator.get("department_provider") or "asterisk"
        if provider != "binotel" or department_id is None:
            return {"enabled": False}
        dept = self.department_settings(department_id)
        enabled = resolve_enabled(self.user_setting(user_id), dept["enabled"] if dept["configured"] else None)
        return {
            "enabled": enabled,
            "portion_size": dept["portion_size"],
            "leg_timeout_sec": LEG_TIMEOUT_SEC,
            # Телефон в этом режиме прячет номера везде: история, окно звонка,
            # журнал. Флаг отдельный, чтобы при разборе инцидента его можно было
            # временно снять одному оператору.
            "hide_numbers": True,
        }

    # ------------------------------------------------------------ Binotel
    def _default_client(self, department_id):
        """Клиент API компании отдела. Ключ — только из окружения сервера."""
        settings = self.department_settings(department_id)
        cfg = binotel.get_config(company=settings["binotel_company"])
        if not binotel.api_ready(cfg):
            # Имена переменных — в журнал сервера (их читает администратор), а
            # пользователю — нейтральный текст без упоминания ключей.
            log.error("dial_list: отдел %s — на сервере не заданы %s_API_KEY / %s_API_SECRET",
                      department_id, cfg["env_prefix"], cfg["env_prefix"])
            raise DialListError("Подключение к Binotel для отдела не настроено на сервере", 503)
        return binotel.BinotelApiClient.from_config(cfg)

    def _client(self, department_id):
        department_id = int(department_id)
        with self._clients_lock:
            client = self._clients.get(department_id)
        if client is None:
            client = self._client_factory(department_id)
            with self._clients_lock:
                self._clients[department_id] = client
        return client

    # ------------------------------------------------------------ живые звонки у Binotel
    # Один опрос call-details на компанию Binotel и общий кэш (live_calls). Карточка звонка
    # (attempt_live), добор исходов (reconcile), события телефона и вопрос автосброса читают
    # кэш, а не спрашивают Binotel каждый сам: так было до 13.10.2026, и Binotel отвечал
    # «слишком часто» почти на всё. Один запрос — сразу на все живые попытки компании; после
    # него попытки с финальным исходом закрываются тут же (_settle_live), чей бы ни был звонок:
    # кончившееся плечо оператора закрывается в фоне, пока он уже звонит следующему.

    _LIVE_ATTEMPTS_SQL = """
        SELECT t.id, t.general_call_id, t.state, t.phone_ended_at,
               (SELECT MIN(n.requested_at) FROM dial_list_attempts n
                WHERE n.operator_id = t.operator_id AND n.requested_at > t.requested_at) AS superseded_at
        FROM dial_list_attempts t
        JOIN dial_list_assignments a ON a.id = t.assignment_id
        JOIN dial_list_leads l ON l.id = a.lead_id
        LEFT JOIN dial_list_department_settings s ON s.department_id = l.department_id
        WHERE t.state IN %s
          AND t.general_call_id IS NOT NULL AND t.general_call_id <> ''
          AND t.requested_at > CURRENT_TIMESTAMP - make_interval(mins => %s)
          AND COALESCE(NULLIF(TRIM(s.binotel_company), ''), %s) = %s
        ORDER BY t.requested_at DESC
        LIMIT %s
    """

    @staticmethod
    def _ctx_company(ctx):
        """Компания Binotel отдела оператора (её ключ API) — из настроек в контексте."""
        return (str((ctx.get("settings") or {}).get("binotel_company") or "").strip()
                or DEFAULT_BINOTEL_COMPANY)

    def _company_calls(self, company):
        """Опрос и кэш живых звонков компании — один на процесс."""
        company = str(company or "").strip() or DEFAULT_BINOTEL_COMPANY
        with self._calls_lock:
            calls = self._calls.get(company)
            if calls is None:
                calls = self._calls[company] = live_calls.CompanyCalls(company, submit=self._calls_submit)
            return calls

    def _live_attempts(self, company):
        """Живые попытки компании (LIVE_STATES, моложе ATTEMPT_STALE_MINUTES, с generalCallID):
        (id, generalCallID, state, phone_ended_at, superseded_at — когда оператор начал следующий
        звонок). Номера водителя здесь нет: Binotel спрашиваем по generalCallID."""
        with self.db._get_cursor() as cur:
            cur.execute(self._LIVE_ATTEMPTS_SQL, (LIVE_STATES, ATTEMPT_STALE_MINUTES, DEFAULT_BINOTEL_COMPANY,
                                                  company, live_calls.BATCH_MAX))
            return cur.fetchall()

    def _calls_fetch(self, department_id, company, max_wait=0):
        """fetch для CompanyCalls.lookup: один запрос call-details на все живые попытки компании
        (wanted — первыми), после него — закрыть попытки с финалом. max_wait — сколько секунд
        клиент Binotel ждёт своей очереди (0 — фоновый опрос: нельзя сейчас — RateLimited).
        Выполняется в потоке опроса — вне транзакций, как вся сеть модуля."""
        client = self._client(department_id)

        def fetch(wanted):
            rows = self._live_attempts(company)
            ids = list(dict.fromkeys(list(wanted) + [str(r[1]) for r in rows if r[1]]))[:live_calls.BATCH_MAX]
            # Свой короткий таймаут HTTP (ревью 13.10.2026): у клиента он 40 с, а опрос у компании
            # один — зависший запрос держал бы финалы всех операторов компании (_settle_live).
            details = client.call_details(ids[0] if len(ids) == 1 else ids, max_wait=max_wait,
                                          timeout=live_calls.HTTP_TIMEOUT)
            return ids, details or {}, (lambda: self._settle_live(company, rows))
        return fetch

    def _calls_seen(self, ctx, general_call_ids, max_age=CALLS_REFRESH_SEC, newer_than=None, wait=CALLS_WAIT_SEC,
                    min_interval=CALLS_REFRESH_SEC, binotel_wait=0):
        """{generalCallID: live_calls.Seen} из общего кэша компании оператора — при надобности
        после одного обновления на всех (CompanyCalls.lookup). Подключения к Binotel нет — то,
        что уже есть в кэше."""
        company = self._ctx_company(ctx)
        calls = self._company_calls(company)
        try:
            fetch = self._calls_fetch(ctx["department_id"], company, max_wait=binotel_wait)
        except Exception as exc:
            log.info("dial_list: опрос call-details для отдела %s недоступен: %s", ctx.get("department_id"), exc)
            known = {str(g): calls.peek(g) for g in general_call_ids or () if g}
            return {g: seen for g, seen in known.items() if seen is not None}
        return calls.lookup(general_call_ids, fetch, max_age=max_age, newer_than=newer_than, wait=wait,
                            min_interval=min_interval)

    def _settle_live(self, company, rows):
        """После опроса — закрыть попытки компании, у которых исход финальный (live_verdict).
        Ответ водителя, пока плечо идёт, — не финал; кончившееся плечо (ended) или звонок,
        после которого оператор уже начал следующий, закрывается данными, полученными после
        конца плеча. Ошибка по одной попытке остальных не останавливает."""
        calls = self._company_calls(company)
        for attempt_id, gid, state, ended_at, superseded_at in rows:
            over_at = leg_over_moment(state, ended_at, superseded_at)
            verdict, call = live_verdict(state, calls.peek(gid), leg_over_at=over_at,
                                         leg_over=state == "ended" or superseded_at is not None)
            if verdict != "finish":
                continue
            try:
                self._finish_by_call(str(attempt_id), call, "poll")
            except Exception:
                log.exception("dial_list: попытка %s не закрылась по опросу call-details", attempt_id)

    # ------------------------------------------------------------ лиды отдела
    # Повторная загрузка того же номера с ДРУГИМ ИИН переписывает ИИН (и сбрасывает
    # проверку), пока водитель не подписал: подписавший остаётся как был.
    _IMPORT_SQL = """
        INSERT INTO dial_list_leads (department_id, phone_norm, full_name, first_batch_id, last_batch_id,
                                     period, iin)
        VALUES %s
        ON CONFLICT (department_id, period, phone_norm) DO UPDATE SET
            full_name = CASE WHEN EXCLUDED.full_name <> '' THEN EXCLUDED.full_name
                             ELSE dial_list_leads.full_name END,
            last_batch_id = EXCLUDED.last_batch_id,
            upload_count = dial_list_leads.upload_count + 1,
            updated_at = CURRENT_TIMESTAMP,
            iin = CASE WHEN dial_list_leads.signed_at IS NULL THEN EXCLUDED.iin ELSE dial_list_leads.iin END,
            sign_status = CASE WHEN dial_list_leads.signed_at IS NULL AND dial_list_leads.iin <> EXCLUDED.iin
                               THEN '' ELSE dial_list_leads.sign_status END,
            sign_docs = CASE WHEN dial_list_leads.signed_at IS NULL AND dial_list_leads.iin <> EXCLUDED.iin
                             THEN '[]'::jsonb ELSE dial_list_leads.sign_docs END,
            sign_error = CASE WHEN dial_list_leads.signed_at IS NULL AND dial_list_leads.iin <> EXCLUDED.iin
                              THEN '' ELSE dial_list_leads.sign_error END,
            sign_checked_at = CASE WHEN dial_list_leads.signed_at IS NULL AND dial_list_leads.iin <> EXCLUDED.iin
                                   THEN NULL ELSE dial_list_leads.sign_checked_at END
        RETURNING (xmax = 0) AS inserted
    """

    def import_leads(self, department_id, uploaded_by, file_name, rows, period=None):
        """rows — из common.leads_file.parse_leads_file(..., with_iin=True):
        (row_number, fio, phone_raw, phone_norm, iin_raw). period — база какого месяца
        (date, первый день); пусто — месяц, который обзванивается.

        ИИН обязателен (запрос владельца 29.09.2026): строка без него или с ошибкой
        в нём не загружается — без ИИН не проверить подписание. Внутри месяца ИИН,
        как и номер, уникален: тот же водитель с другим номером — повтор."""
        department_id = int(department_id)
        period = period or self.department_settings(department_id)["_period"]
        counts = {"rows_total": len(rows), "rows_new": 0, "rows_duplicate": 0, "rows_invalid": 0,
                  "rows_bad_iin": 0, "bad_iin_rows": [], "invalid_rows": [], "period": period.isoformat()}
        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_lead_batches (department_id, uploaded_by, file_name, rows_total, period)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (department_id, uploaded_by, str(file_name or "")[:255], len(rows), period))
            batch_id = str(cur.fetchone()[0])
            # Чей ИИН уже в базе месяца: второй номер того же водителя — повтор, а
            # не вторая строка (и не падение на уникальном индексе посреди загрузки).
            cur.execute("""
                SELECT phone_norm, iin FROM dial_list_leads
                WHERE department_id = %s AND period = %s AND iin <> ''
            """, (department_id, period))
            iin_owner = {r[1]: r[0] for r in cur.fetchall()}
            seen_phones, seen_iins, values = set(), set(), []
            for row in rows:
                row_number, fio, _phone_raw, phone_norm = row[0], row[1], row[2], row[3]
                if not phone_norm:
                    counts["rows_invalid"] += 1
                    if len(counts["invalid_rows"]) < ROW_SAMPLE_MAX:
                        counts["invalid_rows"].append(int(row_number))
                    continue
                iin = clean_iin(row[4] if len(row) > 4 else "")
                if not iin:
                    counts["rows_bad_iin"] += 1
                    if len(counts["bad_iin_rows"]) < ROW_SAMPLE_MAX:
                        counts["bad_iin_rows"].append(int(row_number))
                    continue
                owner = iin_owner.get(iin)
                if phone_norm in seen_phones or iin in seen_iins or (owner and owner != phone_norm):
                    counts["rows_duplicate"] += 1
                    continue
                seen_phones.add(phone_norm)
                seen_iins.add(iin)
                values.append((department_id, phone_norm, str(fio or "").strip()[:255], batch_id, batch_id,
                               period, iin))
            if values:
                try:
                    inserted = execute_values(cur, self._IMPORT_SQL, values, page_size=1000, fetch=True)
                except psycopg2.errors.UniqueViolation:
                    # Две загрузки одного месяца одновременно: вторая увидела базу до первой.
                    raise DialListError("Одновременно идёт другая загрузка базы за этот месяц — "
                                        "повторите через минуту", 409)
                new = sum(1 for r in inserted if r[0])
                counts["rows_new"] += new
                counts["rows_duplicate"] += len(inserted) - new
            cur.execute("""
                UPDATE dial_list_lead_batches
                SET rows_new = %s, rows_duplicate = %s, rows_invalid = %s, rows_bad_iin = %s WHERE id = %s
            """, (counts["rows_new"], counts["rows_duplicate"], counts["rows_invalid"], counts["rows_bad_iin"],
                  batch_id))
        counts["batch_id"] = batch_id
        return counts

    def leads_summary(self, department_id, period=None):
        """Сводка базы за месяц (period — date первого дня; пусто — обзваниваемый
        месяц) плюс список всех месяцев отдела для переключателя."""
        department_id = int(department_id)
        settings = self.department_settings(department_id)
        active = settings["_period"]
        period = period or active
        with self.db._get_cursor() as cur:
            # Подписавшие — отдельной строкой, а не в своём статусе: их больше не
            # обзванивают, и «Не звонили» / «В работе» с ними врали бы.
            cur.execute("""
                SELECT CASE WHEN signed_at IS NOT NULL THEN 'signed' ELSE status END, COUNT(*),
                       COUNT(*) FILTER (WHERE iin = '')
                FROM dial_list_leads
                WHERE department_id = %s AND period = %s GROUP BY 1
            """, (department_id, period))
            by_status, without_iin = {}, 0
            for row in cur.fetchall():
                by_status[row[0]] = int(row[1])
                without_iin += int(row[2] or 0)
            # «Доступно сейчас» имеет смысл только для обзваниваемого месяца.
            pool = 0
            if period == active:
                cur.execute(self._POOL_SQL.format(lock="").replace("LIMIT %s", ""), (
                    department_id, active, settings["retry_after_hours"], settings["max_attempts"]))
                pool = len(cur.fetchall())
            cur.execute("""
                SELECT id, file_name, rows_total, rows_new, rows_duplicate, rows_invalid, created_at,
                       rows_bad_iin
                FROM dial_list_lead_batches WHERE department_id = %s AND period = %s
                ORDER BY created_at DESC LIMIT 20
            """, (department_id, period))
            batches = [{
                "id": _sid(r[0]), "file_name": r[1], "rows_total": r[2], "rows_new": r[3],
                "rows_duplicate": r[4], "rows_invalid": r[5], "created_at": _iso(r[6]),
                "rows_bad_iin": int(r[7] or 0),
            } for r in cur.fetchall()]
            cur.execute("""
                SELECT period, COUNT(*),
                       COUNT(*) FILTER (WHERE status = 'done' AND answered_at IS NOT NULL),
                       COUNT(*) FILTER (WHERE status IN ('new', 'in_progress') AND signed_at IS NULL
                                          AND sign_status <> 'processing'),
                       COUNT(*) FILTER (WHERE signed_at IS NOT NULL)
                FROM dial_list_leads WHERE department_id = %s
                GROUP BY period ORDER BY period DESC
            """, (department_id,))
            periods = [{"period": r[0].isoformat(), "label": period_label(r[0]), "total": int(r[1]),
                        "answered": int(r[2]), "open": int(r[3]), "signed": int(r[4]), "active": r[0] == active}
                       for r in cur.fetchall()]
        if not any(p["period"] == active.isoformat() for p in periods):
            periods.insert(0, {"period": active.isoformat(), "label": period_label(active), "total": 0,
                               "answered": 0, "open": 0, "signed": 0, "active": True})
        return {
            "department_id": department_id,
            "period": period.isoformat(),
            "period_label": period_label(period),
            "active_period": active.isoformat(),
            "is_active_period": period == active,
            "total": sum(by_status.values()),
            "by_status": {k: by_status.get(k, 0) for k in ("new", "in_progress", "done", "excluded")},
            "signed": by_status.get("signed", 0),
            "without_iin": without_iin,
            "pool_available": pool,
            "batches": batches,
            "periods": periods,
        }

    # ------------------------------------------------------------ итоги звонка
    # Справочник отдела: оператор после каждого разговора обязан выбрать итог (и
    # может оставить комментарий). Пока итог не указан, ни следующий звонок, ни
    # следующая порция не выдаются — проверяет и телефон, и сервер. Итоги не
    # удаляются, а выключаются: на них ссылается история.

    def _outcome_rows(self, cur, department_id, active_only=False):
        # Типы итога (29.09.2026) — json-колонкой в том же запросе: у каждого итога
        # сразу его список, без второго запроса и без склейки по id в Python.
        # active_only — только включённые итоги и только включённые типы (телефон).
        cur.execute("""
            SELECT id, name, color, position, requeue, is_active,
                   (SELECT COUNT(*) FROM dial_list_attempts t WHERE t.outcome_id = o.id) AS used,
                   (SELECT COALESCE(json_agg(json_build_object(
                               'id', s.id, 'name', s.name, 'position', s.position, 'is_active', s.is_active,
                               'used', (SELECT COUNT(*) FROM dial_list_attempts t2
                                        WHERE t2.outcome_subtype_id = s.id))
                           ORDER BY s.is_active DESC, s.position, s.created_at), '[]'::json)
                    FROM dial_list_outcome_subtypes s
                    WHERE s.outcome_id = o.id {sub_active}) AS subtypes
            FROM dial_list_outcomes o
            WHERE department_id = %s {active}
            ORDER BY is_active DESC, position, created_at
        """.format(active="AND is_active" if active_only else "",
                   sub_active="AND s.is_active" if active_only else ""), (int(department_id),))
        rows = cur.fetchall()
        if not rows and active_only:
            # У отдела ещё нет ни одного итога — заводим стартовый набор один раз.
            cur.execute("SELECT 1 FROM dial_list_outcomes WHERE department_id = %s LIMIT 1", (int(department_id),))
            if not cur.fetchone():
                self._seed_outcomes(cur, department_id)
                return self._outcome_rows(cur, department_id, active_only=True)
        return [{"id": _sid(r[0]), "name": r[1], "color": r[2], "position": int(r[3]),
                 "requeue": bool(r[4]), "is_active": bool(r[5]), "used": int(r[6] or 0),
                 "subtypes": _subtypes_from_json(r[7])} for r in rows]

    def _seed_outcomes(self, cur, department_id):
        for position, (name, color, requeue) in enumerate(DEFAULT_OUTCOMES, start=1):
            cur.execute("""
                INSERT INTO dial_list_outcomes (department_id, name, color, position, requeue)
                VALUES (%s, %s, %s, %s, %s)
            """, (int(department_id), name, color, position, requeue))
        log.info("dial_list: отделу %s заведён стартовый набор итогов", department_id)

    def outcomes(self, department_id):
        """Все итоги отдела (включая выключенные) для редактора руководителя — у
        каждого subtypes: все его типы, тоже с выключенными и числом использований."""
        with self.db._get_cursor() as cur:
            rows = self._outcome_rows(cur, department_id)
            if not rows:
                self._seed_outcomes(cur, department_id)
                rows = self._outcome_rows(cur, department_id)
        return rows

    @staticmethod
    def _clean_subtypes(outcome_name, raw):
        """Типы одного итога из редактора: проверка до обращения к базе (как у итогов)."""
        if not isinstance(raw, list):
            raise DialListError(f"Типы итога «{outcome_name}»: ожидается список")
        if len(raw) > SUBTYPES_MAX:
            raise DialListError(f"У итога «{outcome_name}» не больше {SUBTYPES_MAX} типов")
        cleaned = []
        seen = set()
        for item in raw:
            if not isinstance(item, dict):
                raise DialListError(f"Типы итога «{outcome_name}»: каждый тип — объект {{id?, name, is_active}}")
            name = str(item.get("name") or "").strip()[:SUBTYPE_NAME_MAX]
            if not name:
                raise DialListError(f"У типа итога «{outcome_name}» должно быть название")
            if name.lower() in seen:
                raise DialListError(f"Тип «{name}» у итога «{outcome_name}» повторяется")
            seen.add(name.lower())
            cleaned.append({"id": _sid(item.get("id")) or None, "name": name,
                            "is_active": item.get("is_active", True) is not False})
        return cleaned

    def _save_subtypes(self, cur, department_id, outcome_id, outcome_name, subtypes):
        """Полный список типов одного итога — те же правила, что у итогов: с id этого
        итога — обновить, без id (или с чужим) — добавить, отсутствующие — выключить."""
        cur.execute("""
            SELECT id FROM dial_list_outcome_subtypes WHERE outcome_id = %s AND department_id = %s
        """, (outcome_id, department_id))
        existing = {str(r[0]) for r in cur.fetchall()}
        kept = set()
        for position, s in enumerate(subtypes, start=1):
            if s["id"] and s["id"] in existing and s["id"] not in kept:
                cur.execute("""
                    UPDATE dial_list_outcome_subtypes
                    SET name = %s, position = %s, is_active = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND outcome_id = %s
                """, (s["name"], position, s["is_active"], s["id"], outcome_id))
                kept.add(s["id"])
            else:
                cur.execute("""
                    INSERT INTO dial_list_outcome_subtypes (outcome_id, department_id, name, position, is_active)
                    VALUES (%s, %s, %s, %s, %s) RETURNING id
                """, (outcome_id, department_id, s["name"], position, s["is_active"]))
                kept.add(str(cur.fetchone()[0]))
        gone = existing - kept
        if gone:
            cur.execute("""
                UPDATE dial_list_outcome_subtypes SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE outcome_id = %s AND id = ANY(%s::uuid[])
            """, (outcome_id, list(gone)))
        # Лимит и уникальность — ещё и по базе: отсутствующие в списке типы только
        # выключаются, и сохранение из устаревшей вкладки (без типов, добавленных
        # в другой) довело бы итог до 20+ типов или до двух «Дорого». Тогда GET
        # вернул бы список, который PUT уже не принимает, — редактор заперт.
        # Ошибка внутри транзакции откатывает всё сохранение. Регистр сравниваем в
        # Python, как в _clean_subtypes: LOWER() базы с локалью C кириллицу не понижает.
        cur.execute("""
            SELECT name FROM dial_list_outcome_subtypes WHERE outcome_id = %s
        """, (outcome_id,))
        stored = [str(r[0]).lower() for r in cur.fetchall()]
        if len(stored) > SUBTYPES_MAX:
            raise DialListError(f"У итога «{outcome_name}» не больше {SUBTYPES_MAX} типов "
                                f"(с учётом сохранённых в другой вкладке) — обновите страницу")
        if len(set(stored)) < len(stored):
            raise DialListError(f"Тип у итога «{outcome_name}» повторяется с сохранённым "
                                f"в другой вкладке — обновите страницу")

    def save_outcomes(self, department_id, items, changed_by=None):
        """Полный список от редактора: с id — обновить, без id — добавить, отсутствующие
        в списке — выключить (не удалить). Порядок — как в списке.

        subtypes у итога — полный список его типов по тем же правилам. Ключа нет —
        типы итога не трогаем: закэшированная старая сборка сайта шлёт итоги без
        типов, и сохранение из неё не должно стирать настроенное (29.09.2026)."""
        department_id = int(department_id)
        if not isinstance(items, list):
            raise DialListError("items: ожидается список итогов")
        if len(items) > OUTCOMES_MAX:
            raise DialListError(f"Итогов не больше {OUTCOMES_MAX}")
        cleaned = []
        seen_names = set()
        for raw in items:
            if not isinstance(raw, dict):
                raise DialListError("Каждый итог — объект {id?, name, color, requeue, is_active}")
            name = str(raw.get("name") or "").strip()[:OUTCOME_NAME_MAX]
            if not name:
                raise DialListError("У итога должно быть название")
            if name.lower() in seen_names:
                raise DialListError(f"Итог «{name}» повторяется")
            seen_names.add(name.lower())
            color = str(raw.get("color") or "#8E8E93").strip().upper()
            if not OUTCOME_COLOR_RE.match(color):
                raise DialListError(f"Цвет итога «{name}»: ожидается #RRGGBB")
            cleaned.append({
                "id": _sid(raw.get("id")) or None, "name": name, "color": color,
                "requeue": bool(raw.get("requeue")), "is_active": raw.get("is_active", True) is not False,
                "subtypes": self._clean_subtypes(name, raw["subtypes"]) if "subtypes" in raw else None,
            })
        if not any(c["is_active"] for c in cleaned):
            raise DialListError("Хотя бы один итог должен быть включён")
        with self.db._get_cursor() as cur:
            cur.execute("SELECT id FROM dial_list_outcomes WHERE department_id = %s", (department_id,))
            existing = {str(r[0]) for r in cur.fetchall()}
            kept = set()
            for position, c in enumerate(cleaned, start=1):
                if c["id"] and c["id"] in existing:
                    cur.execute("""
                        UPDATE dial_list_outcomes
                        SET name = %s, color = %s, position = %s, requeue = %s, is_active = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND department_id = %s
                    """, (c["name"], c["color"], position, c["requeue"], c["is_active"], c["id"], department_id))
                    outcome_id = c["id"]
                else:
                    cur.execute("""
                        INSERT INTO dial_list_outcomes (department_id, name, color, position, requeue, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                    """, (department_id, c["name"], c["color"], position, c["requeue"], c["is_active"]))
                    outcome_id = str(cur.fetchone()[0])
                kept.add(outcome_id)
                if c["subtypes"] is not None:
                    self._save_subtypes(cur, department_id, outcome_id, c["name"], c["subtypes"])
            gone = existing - kept
            if gone:
                cur.execute("""
                    UPDATE dial_list_outcomes SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE department_id = %s AND id = ANY(%s::uuid[])
                """, (department_id, list(gone)))
        log.info("dial_list: итоги отдела %s сохранены (%d, пользователь %s)", department_id, len(cleaned), changed_by)
        return self.outcomes(department_id)

    def _pending_outcome(self, cur, user_id):
        """Последняя попытка оператора, где телефон принял плечо (разговор был), а итог
        ещё не указан. Попытки до появления итогов (leg_answered_at пуст) не считаются.

        Если оператор сам положил трубку (operator_hangup_at), долг возникает только
        когда Binotel подтвердил, что водитель ответил (кроме разговора, который слышал
        телефон, — см. ниже): до финального исхода — не известно, был ли разговор; не
        ответил — попытка отменена (cancelled) и итога по ней не будет. Сброс на гудках (ring_timeout_at) — то же правило, только без
        отмены: водитель не ответил — это недозвон, итог по нему не нужен.

        Плечо, которое телефон так и не подтвердил (leg_answered_at пуст: ACK от АТС
        не дошёл), — долг только если Binotel подтвердил ответ водителя, телефон сообщил
        конец плеча и попытка свежая (UNCONFIRMED_LEG_OUTCOME_HOURS).

        Телефон с 3.22.34 сообщает, что услышал (12.10.2026). Сообщение сети (auto_hangup
        'announcement') долга не даёт никогда. Разговор был (talk_detected TRUE) — долг
        сразу, как кончилось плечо, даже если трубку положил оператор: исхода Binotel не
        ждём (то же решение — _outcome_decision). Разговора телефон не услышал (talk_detected
        FALSE) — долг только после финального ответа Binotel: обычный конец плеча тогда
        решается так же, как отбой оператора и сброс на гудках. Что считать ответом —
        ATTEMPT_TALK_SQL; у попыток прежних телефонов он верен всегда, для них всё как было.

        Разговор, который слышал телефон, долга не даёт, когда Binotel закрыл попытку финальным
        «не дозвонились» (NOT_ANSWERED_FINAL_SQL, ревью 13.10.2026): реплика оператора в паузе
        сообщения сети — не разговор, и следующий звонок такая попытка не держит."""
        cur.execute("""
            SELECT t.id, l.full_name, COALESCE(t.phone_ended_at, t.finished_at, t.updated_at), t.requested_at
            FROM dial_list_attempts t
            JOIN dial_list_assignments a ON a.id = t.assignment_id
            JOIN dial_list_leads l ON l.id = a.lead_id
            WHERE t.operator_id = %s AND NOT t.cancelled AND """ + ATTEMPT_TALK_SQL + """
              AND ((t.outcome_id IS NULL AND t.leg_answered_at IS NOT NULL
                    AND ((t.ring_timeout_at IS NULL AND t.state IN ('ended', 'finished')
                          AND ((t.talk_detected IS TRUE
                                AND NOT (t.state = 'finished' AND UPPER(t.disposition) IN %s))
                               OR (t.operator_hangup_at IS NULL AND t.talk_detected IS NULL)))
                         OR (t.operator_hangup_at IS NOT NULL AND t.state = 'finished'
                             AND UPPER(t.disposition) IN %s)
                         OR (t.ring_timeout_at IS NOT NULL AND t.state = 'finished'
                             AND UPPER(t.disposition) IN %s)
                         OR (t.talk_detected IS FALSE AND t.state = 'finished'
                             AND UPPER(t.disposition) IN %s)))
                   OR (t.outcome_id IS NULL AND t.leg_answered_at IS NULL
                       AND t.phone_ended_at IS NOT NULL AND t.state = 'finished'
                       AND UPPER(t.disposition) IN %s
                       AND t.requested_at >= CURRENT_TIMESTAMP - make_interval(hours => %s)))
            ORDER BY t.requested_at DESC LIMIT 1
        """, (int(user_id), NOT_ANSWERED_FINAL_SQL, ANSWERED_SQL, ANSWERED_SQL, ANSWERED_SQL, ANSWERED_SQL,
              UNCONFIRMED_LEG_OUTCOME_HOURS))
        r = cur.fetchone()
        if not r:
            return None
        return {"attempt_id": _sid(r[0]), "full_name": r[1] or "", "ended_at": _iso(r[2]),
                "requested_at": _iso(r[3])}

    def _require_outcome_done(self, cur, ctx):
        if not self._outcome_rows(cur, ctx["department_id"], active_only=True):
            return
        pending = self._pending_outcome(cur, ctx["user_id"])
        if pending:
            raise DialListError(
                f"Сначала укажите итог звонка: {pending['full_name'] or 'предыдущий разговор'}", 409)

    def set_attempt_outcome(self, user_id, attempt_id, outcome_id, comment="", subtype_id=SUBTYPE_NOT_SENT):
        """Оператор указал итог разговора (и, возможно, комментарий).

        subtype_id — тип итога (29.09.2026). SUBTYPE_NOT_SENT — телефон до типов
        итога ключ не шлёт: итог сохраняется без типа, иначе такие операторы
        навсегда застряли бы на «Сначала укажите итог звонка». Ключ прислан —
        новый телефон: у итога с включёнными типами тип обязателен."""
        ctx = self.operator_context(user_id)
        attempt_id = str(attempt_id)
        outcome_id = str(outcome_id or "").strip()
        comment = str(comment or "").strip()[:COMMENT_MAX]
        if not outcome_id:
            raise DialListError("Выберите итог звонка")
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT t.state, a.lead_id, t.outcome_id, t.cancelled, t.operator_hangup_at, t.disposition,
                       t.ring_timeout_at, t.auto_hangup, t.talk_detected, t.billsec
                FROM dial_list_attempts t
                JOIN dial_list_assignments a ON a.id = t.assignment_id
                WHERE t.id = %s AND t.operator_id = %s FOR UPDATE OF t
            """, (attempt_id, ctx["user_id"]))
            row = cur.fetchone()
            if not row:
                raise DialListError("Попытка не найдена", 404)
            # Оператор сам положил трубку: итог только по состоявшемуся разговору.
            # Отменённая попытка (водитель не ответил) итога не имеет — и не
            # засчитывается; до финального исхода от АТС итог тоже не принимаем,
            # иначе «повесил трубку на гудках и поставил итог» прошло бы.
            if row[3]:
                raise DialListError("Звонок завершён до ответа водителя — итог не ставится", 409)
            # Сообщение сети — недозвон при любом ответе Binotel: итога у него нет.
            if row[7] == AUTO_HANGUP_ANNOUNCEMENT:
                raise DialListError("Абонент недоступен — итог не нужен", 409)
            answered = row[0] == "finished" and attempt_result(row[5], row[9], row[7], row[8]) == "answered"
            # Телефон сам положил плечо на гудках: водитель не ответил — это недозвон,
            # итога у него нет; ответил в последний момент — обычный разговор.
            if row[6] is not None and not answered:
                if row[0] == "finished":
                    raise DialListError("Водитель не взял трубку — итог не нужен", 409)
                raise DialListError("Звонок сброшен на гудках: итог нужен, только если водитель успел "
                                    "ответить. Дождитесь исхода от АТС", 409)
            # Разговор был (talk_detected true) — итог принимаем и после отбоя оператора, не
            # дожидаясь Binotel: телефон уже открыл окно итога (_outcome_decision).
            if row[4] is not None and not answered and row[8] is not True:
                raise DialListError("Вы завершили звонок сами: итог можно указать только по "
                                    "состоявшемуся разговору. Дождитесь исхода от АТС", 409)
            # Разговора телефон не услышал (talk_detected false): итог — только когда ответ
            # водителя подтвердил Binotel, как после сброса на гудках и отбоя оператора.
            if row[8] is False and not answered:
                if row[0] == "finished":
                    raise DialListError("Водитель не ответил — итог не нужен", 409)
                raise DialListError("Разговор не подтверждён: итог нужен, только если водитель "
                                    "ответил. Дождитесь исхода от АТС", 409)
            cur.execute("""
                SELECT id, name, color, requeue FROM dial_list_outcomes
                WHERE id = %s AND department_id = %s AND is_active
            """, (outcome_id, ctx["department_id"]))
            outcome = cur.fetchone()
            if not outcome:
                raise DialListError("Такого итога нет — обновите список", 400)
            subtype = None
            if subtype_id is not SUBTYPE_NOT_SENT:
                subtype_text = str(subtype_id or "").strip()
                if subtype_text:
                    # Кривой id — та же 400, что и чужой/выключенный тип, а не 500 от базы.
                    try:
                        subtype_text = str(uuid.UUID(subtype_text))
                    except (ValueError, AttributeError, TypeError):
                        raise DialListError("Такого типа итога нет — обновите список", 400)
                    cur.execute("""
                        SELECT id, name FROM dial_list_outcome_subtypes
                        WHERE id = %s AND outcome_id = %s AND department_id = %s AND is_active
                    """, (subtype_text, str(outcome[0]), ctx["department_id"]))
                    subtype = cur.fetchone()
                    if not subtype:
                        raise DialListError("Такого типа итога нет — обновите список", 400)
                else:
                    cur.execute("""
                        SELECT 1 FROM dial_list_outcome_subtypes
                        WHERE outcome_id = %s AND is_active LIMIT 1
                    """, (str(outcome[0]),))
                    if cur.fetchone():
                        raise DialListError("Выберите тип итога", 400)
            # Тип пишется ВСЕГДА вместе с итогом (NULL — без типа): повторный выбор
            # другого итога не оставит на попытке тип от прежнего.
            cur.execute("""
                UPDATE dial_list_attempts
                SET outcome_id = %s, outcome_subtype_id = %s, operator_comment = %s,
                    outcome_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (outcome_id, _sid(subtype[0]) if subtype else None, comment, attempt_id))
            if outcome[3]:
                # «Перезвонить»: лид снова в работе, попытки с нуля. Если исход от АТС
                # приедет позже, _touch_lead увидит этот итог и статус не перебьёт.
                cur.execute("""
                    UPDATE dial_list_leads
                    SET status = CASE WHEN status = 'excluded' THEN status ELSE 'in_progress' END,
                        attempts_total = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (str(row[1]),))
        log.info("dial_list: оператор %s указал итог %s%s по попытке %s", ctx["user_id"], outcome[1],
                 f" · {subtype[1]}" if subtype else "", attempt_id)
        return {"attempt_id": attempt_id,
                "outcome": {"id": _sid(outcome[0]), "name": outcome[1], "color": outcome[2], "requeue": bool(outcome[3]),
                            "subtype": {"id": _sid(subtype[0]), "name": subtype[1]} if subtype else None},
                "comment": comment}

    # ------------------------------------------------------------ журнал водителей
    # Один запрос и для списка, и для карточки: этап (stage) считается в SQL, чтобы
    # фильтр «В очереди» показывал ровно то, что выдаст _POOL_SQL. Номер выбирается
    # только ради маски — наружу уходит phone_masked.
    _JOURNAL_BASE_SQL = """
        WITH base AS (
            SELECT l.id, l.department_id, l.full_name, l.phone_norm, l.status, l.attempts_total,
                   l.last_attempt_at, l.answered_at, l.created_at, l.updated_at, l.upload_count,
                   COALESCE(l.note, '') AS note,
                   l.period,
                   lo.id AS last_outcome_id, lo.name AS last_outcome_name, lo.color AS last_outcome_color,
                   ls.id AS last_outcome_subtype_id, ls.name AS last_outcome_subtype_name,
                   COALESCE(lt.operator_comment, '') AS last_comment,
                   COALESCE(lt.cancelled, FALSE) AS last_cancelled,
                   fb.file_name AS first_file, fb.created_at AS first_uploaded_at,
                   lb.file_name AS last_file, lb.created_at AS last_uploaded_at,
                   oa.operator_id AS open_operator_id, ou.name AS open_operator_name,
                   la.operator_id AS last_operator_id, lu.name AS last_operator_name,
                   la.result AS last_result, la.done_at AS last_done_at,
                   lt.requested_at AS last_call_at, lt.state AS last_call_state,
                   lt.disposition AS last_disposition, lt.billsec AS last_billsec,
                   lt.api_error AS last_api_error, tu.name AS last_call_operator_name,
                   GREATEST(l.created_at, COALESCE(lt.requested_at, l.created_at),
                            COALESCE(lb.created_at, l.created_at)) AS activity_at,
                   l.iin, l.sign_status, l.sign_checked_at, l.sign_error, l.signed_at,
                   l.success_attempt_id, l.success_operator_id, su.name AS success_operator_name,
                   l.success_resolved_at,
                   lt.auto_hangup AS last_auto_hangup, lt.talk_detected AS last_talk_detected,
                   CASE
                       WHEN l.signed_at IS NOT NULL THEN 'signed'
                       WHEN l.sign_status = 'processing' THEN 'sign_wait'
                       WHEN l.status = 'excluded' THEN 'excluded'
                       WHEN oa.operator_id IS NOT NULL THEN 'issued'
                       WHEN l.status = 'done' AND l.answered_at IS NOT NULL THEN 'answered'
                       WHEN l.status = 'done' THEN 'exhausted'
                       WHEN l.attempts_total >= %(max_attempts)s THEN 'exhausted'
                       WHEN l.last_attempt_at IS NOT NULL
                            AND l.last_attempt_at >= CURRENT_TIMESTAMP - make_interval(hours => %(retry_hours)s)
                           THEN 'waiting'
                       ELSE 'queue'
                   END AS stage,
                   l.last_attempt_at + make_interval(hours => %(retry_hours)s) AS next_retry_at
            FROM dial_list_leads l
            LEFT JOIN dial_list_lead_batches fb ON fb.id = l.first_batch_id
            LEFT JOIN dial_list_lead_batches lb ON lb.id = l.last_batch_id
            LEFT JOIN LATERAL (
                SELECT a.operator_id FROM dial_list_assignments a
                WHERE a.lead_id = l.id AND a.state = 'issued'
                ORDER BY a.created_at DESC LIMIT 1
            ) oa ON TRUE
            LEFT JOIN users ou ON ou.id = oa.operator_id
            LEFT JOIN LATERAL (
                SELECT a.operator_id, a.result, a.done_at FROM dial_list_assignments a
                WHERE a.lead_id = l.id
                ORDER BY a.created_at DESC LIMIT 1
            ) la ON TRUE
            LEFT JOIN users lu ON lu.id = la.operator_id
            LEFT JOIN LATERAL (
                SELECT t.requested_at, t.state, t.disposition, t.billsec, t.api_error, t.operator_id,
                       t.outcome_id, t.operator_comment, t.cancelled, t.outcome_subtype_id,
                       t.auto_hangup, t.talk_detected
                FROM dial_list_attempts t
                JOIN dial_list_assignments a2 ON a2.id = t.assignment_id
                WHERE a2.lead_id = l.id
                ORDER BY t.requested_at DESC LIMIT 1
            ) lt ON TRUE
            LEFT JOIN users tu ON tu.id = lt.operator_id
            LEFT JOIN dial_list_outcomes lo ON lo.id = lt.outcome_id
            LEFT JOIN dial_list_outcome_subtypes ls ON ls.id = lt.outcome_subtype_id
            LEFT JOIN users su ON su.id = l.success_operator_id
            WHERE {scope}
        )
    """
    # Порядок колонок строки журнала — на него опирается _journal_row (r[0]…r[49]).
    # Новые колонки — только в конец: за ними страница добавляет total, rn и сводку.
    _JOURNAL_COLUMNS = """
               id, department_id, full_name, phone_norm, status, attempts_total, last_attempt_at,
               answered_at, created_at, updated_at, upload_count, note,
               first_file, first_uploaded_at, last_file, last_uploaded_at,
               open_operator_id, open_operator_name, last_operator_id, last_operator_name,
               last_result, last_done_at, last_call_at, last_call_state, last_disposition,
               last_billsec, last_api_error, last_call_operator_name, activity_at, stage, next_retry_at,
               period, last_outcome_id, last_outcome_name, last_outcome_color, last_comment,
               last_cancelled, last_outcome_subtype_id, last_outcome_subtype_name,
               iin, sign_status, sign_checked_at, sign_error, signed_at, success_attempt_id,
               success_operator_id, success_operator_name, success_resolved_at,
               last_auto_hangup, last_talk_detected"""
    # Сколько колонок у строки журнала: сразу за ними идёт total (индекс не
    # зашит числом — иначе новая колонка молча сдвигала бы «всего» на rn).
    _JOURNAL_COLUMN_COUNT = len([c for c in _JOURNAL_COLUMNS.split(",") if c.strip()])
    _JOURNAL_SQL = _JOURNAL_BASE_SQL + """
        SELECT""" + _JOURNAL_COLUMNS + """,
               COUNT(*) OVER () AS total
        FROM base
        WHERE {filters}
        ORDER BY {order}
        LIMIT %(limit)s OFFSET %(offset)s
    """
    # Страница журнала вместе со сводкой — одним запросом. Числа на полосе этапов
    # и на чипах итогов считаются по той же выборке, что и список: этапы — со
    # всеми фильтрами, кроме этапа, итоги — со всеми, кроме итога. Тогда число на
    # чипе равно числу строк, которые покажет нажатие на него. Итог — последнего
    # звонка водителя, ровно тот, по которому фильтрует список.
    #
    # base с тремя LATERAL на каждого водителя месяца — самая дорогая часть, и
    # вторым запросом за сводкой она строилась бы дважды. Поэтому выборка по
    # общим фильтрам материализуется один раз (f), а страница и сводка читают её.
    # Сводка приходит JSON-колонкой к каждой строке страницы; LEFT JOIN от сводки
    # отдаёт одну строку и тогда, когда страница пуста.
    _JOURNAL_PAGE_SQL = _JOURNAL_BASE_SQL.rstrip() + """,
        f AS MATERIALIZED (
            SELECT * FROM base WHERE {filters}
        ),
        page AS (
            SELECT""" + _JOURNAL_COLUMNS + """,
                   COUNT(*) OVER () AS total,
                   ROW_NUMBER() OVER (ORDER BY {order}) AS rn
            FROM f
            WHERE {stage_ok} AND {outcome_ok}
            ORDER BY rn
            LIMIT %(limit)s OFFSET %(offset)s
        ),
        counts AS (
            SELECT COALESCE(json_agg(json_build_array(stage, last_outcome_id::text, last_outcome_subtype_id::text,
                                                      for_stage, for_outcome)),
                            '[]'::json) AS summary
            FROM (
                SELECT stage, last_outcome_id, last_outcome_subtype_id,
                       COUNT(*) FILTER (WHERE {outcome_ok}) AS for_stage,
                       COUNT(*) FILTER (WHERE {stage_ok}) AS for_outcome
                FROM f
                GROUP BY stage, last_outcome_id, last_outcome_subtype_id
            ) g
        )
        SELECT page.*, counts.summary
        FROM counts LEFT JOIN page ON TRUE
        ORDER BY page.rn
    """

    def _journal_row(self, r, max_attempts):
        stage = r[29]
        responsible_id = r[16] if r[16] is not None else r[18]
        responsible_name = r[17] if r[16] is not None else r[19]
        last_call = None
        if r[22] is not None:
            state = r[23] or ""
            disposition = r[24] or ""
            # Исход — с поправкой на то, что услышал телефон (attempt_result): сообщение сети
            # и ответ без разговора — недозвон, как у строки выдачи и у лида.
            result = (attempt_result(disposition, r[25], r[48], r[49]) if state == "finished"
                      else ("failed" if state == "failed" else ""))
            if state == "finished" and not result:
                result = "other"
            if r[36]:
                # Оператор сам завершил звонок до ответа водителя — попытка не в счёт.
                result = "cancelled"
            last_call = {
                "at": _iso(r[22]), "state": state, "disposition": disposition, "result": result,
                "billsec": int(r[25] or 0), "api_error": (r[26] or "")[:200], "operator_name": r[27] or "",
                "cancelled": bool(r[36]),
                # Почему плечо положил сам телефон: 'ring_timeout' | 'announcement' | ''.
                "auto_hangup": r[48] or "",
            }
        return {
            "id": _sid(r[0]), "department_id": int(r[1]), "full_name": r[2] or "",
            "phone_masked": mask_phone(r[3]), "status": r[4], "stage": stage,
            "stage_label": LEAD_STAGES.get(stage, stage), "attempts_total": int(r[5] or 0),
            "max_attempts": int(max_attempts), "last_attempt_at": _iso(r[6]), "answered_at": _iso(r[7]),
            "created_at": _iso(r[8]), "updated_at": _iso(r[9]), "upload_count": int(r[10] or 1),
            "note": r[11] or "",
            "batch": {"file_name": r[12] or "", "uploaded_at": _iso(r[13])},
            "last_batch": {"file_name": r[14] or "", "uploaded_at": _iso(r[15])},
            "responsible": ({"id": int(responsible_id), "name": responsible_name or ""}
                            if responsible_id is not None else None),
            "responsible_is_current": r[16] is not None,
            "last_result": r[20] or "",
            "last_call": last_call,
            "activity_at": _iso(r[28]),
            "next_retry_at": _iso(r[30]) if stage == "waiting" else None,
            "period": r[31].isoformat() if r[31] else None,
            "period_label": period_label(r[31]),
            # Итог (с типом, если выбран) и комментарий оператора по последнему звонку.
            "outcome": ({"id": _sid(r[32]), "name": r[33] or "", "color": r[34] or "#8E8E93",
                         "subtype": ({"id": _sid(r[37]), "name": r[38] or ""} if r[37] is not None else None)}
                        if r[32] is not None else None),
            "comment": r[35] or "",
            # ИИН — только хвост (как номер). Подписание по Sapar и кому засчитана успешка.
            "iin_masked": mask_iin(r[39]),
            "has_iin": bool(r[39]),
            "sign": self._sign_block(r[39], r[40], r[41], r[42], r[43], r[44], r[45], r[46], r[47]),
        }

    @staticmethod
    def _sign_block(iin, sign_status, checked_at, sign_error, signed_at, success_attempt_id,
                    success_operator_id, success_operator_name, success_resolved_at):
        code = signing.status_code(iin, sign_status, signed_at)
        return {
            "status": code,
            "label": signing.SIGN_STATUS_LABELS.get(code, code),
            "checked_at": _iso(checked_at),
            # Ошибка последней проверки важна, пока водитель не подписал.
            "error": (sign_error or "")[:200] if signed_at is None else "",
            "signed_at": _iso(signed_at),
            # Подписал, а решение по успешке ещё не принято — ждём конца звонка.
            "resolved": success_resolved_at is not None,
            "success": ({"attempt_id": _sid(success_attempt_id),
                         "operator": {"id": success_operator_id, "name": success_operator_name or ""}}
                        if success_attempt_id is not None else None),
        }

    def leads_journal(self, department_id, q="", stage="", operator_id=None, batch_id=None,
                      date_from=None, date_to=None, sort="activity", limit=50, offset=0,
                      period=None, outcome_id=None, outcome_subtype_id=None, sign=""):
        """Журнал водителей отдела: страница строк + всего. Все фильтры необязательны.
        period — date первого дня месяца, 'all' — все месяцы, пусто — обзваниваемый.
        outcome_subtype_id — тип итога (uuid) либо 'none' — «без типа»; имеет смысл
        только вместе с outcome_id, без него не учитывается. sign — состояние
        подписания документов (JOURNAL_SIGN_FILTERS)."""
        department_id = int(department_id)
        settings = self.department_settings(department_id)
        if period is None:
            period = settings["_period"]
        params = {
            "max_attempts": settings["max_attempts"], "retry_hours": settings["retry_after_hours"],
            "department_id": department_id,
            "limit": max(1, min(_to_int(limit, 50), JOURNAL_MAX_LIMIT)), "offset": max(0, _to_int(offset, 0)),
        }
        scope = "l.department_id = %(department_id)s"
        if period != "all":
            params["period"] = period
            scope += " AND l.period = %(period)s"
        filters = ["TRUE"]
        # Этап и итог — отдельно от остальных фильтров: сводка по полосе этапов и
        # по чипам итогов считается без «своего» фильтра (см. _JOURNAL_PAGE_SQL).
        # Тип итога — часть фильтра итога: чипы итогов и типов считаются без обоих,
        # полоса этапов — с ними.
        stage_ok = outcome_ok = "TRUE"
        if outcome_id:
            params["outcome_id"] = str(outcome_id)
            outcome_ok = "last_outcome_id = %(outcome_id)s::uuid"
            subtype = str(outcome_subtype_id or "").strip()
            if subtype.lower() == "none":
                outcome_ok += " AND last_outcome_subtype_id IS NULL"
            elif subtype:
                params["outcome_subtype_id"] = subtype
                outcome_ok += " AND last_outcome_subtype_id = %(outcome_subtype_id)s::uuid"
        q = str(q or "").strip()
        if q:
            digits = "".join(ch for ch in q if ch.isdigit())
            params["q_name"] = f"%{q}%"
            if len(digits) >= 3 and len(digits) >= len(q) - 3:
                # Поиск по хвосту номера или ИИН (маски показывают по 4 цифры; ИИН
                # целиком — тоже хвост) — сами номер и ИИН всё равно не отдаём.
                params["q_tail"] = f"%{digits}"
                filters.append("(full_name ILIKE %(q_name)s OR phone_norm LIKE %(q_tail)s"
                               " OR iin LIKE %(q_tail)s)")
            else:
                filters.append("full_name ILIKE %(q_name)s")
        sign = str(sign or "").strip()
        if sign:
            if sign not in JOURNAL_SIGN_FILTERS:
                raise DialListError("sign: неизвестное состояние подписания")
            filters.append(f"({JOURNAL_SIGN_FILTERS[sign]})")
        stage = str(stage or "").strip()
        if stage:
            if stage not in LEAD_STAGES:
                raise DialListError("stage: неизвестный этап")
            params["stage"] = stage
            stage_ok = "stage = %(stage)s"
        if operator_id:
            params["operator_id"] = _to_int(operator_id, 0)
            filters.append("""EXISTS (SELECT 1 FROM dial_list_assignments a
                                      WHERE a.lead_id = base.id AND a.operator_id = %(operator_id)s)""")
        if batch_id:
            params["batch_id"] = str(batch_id)
            filters.append("""EXISTS (SELECT 1 FROM dial_list_leads l2
                                      WHERE l2.id = base.id
                                        AND (l2.first_batch_id = %(batch_id)s::uuid OR l2.last_batch_id = %(batch_id)s::uuid))""")
        # «Дата звонка»: водителю звонили в эти дни (хотя бы одна попытка, дни по
        # Алматы). Обе границы — на ОДНОЙ попытке: звонок до периода и звонок после
        # него вместе не делают водителя «звонили в эти дни».
        call_bounds = []
        if date_from:
            params["date_from"] = str(date_from)
            call_bounds.append("t.requested_at >= (%(date_from)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'")
        if date_to:
            params["date_to"] = str(date_to)
            call_bounds.append("t.requested_at < (%(date_to)s::date + 1)::timestamp AT TIME ZONE 'Asia/Almaty'")
        if call_bounds:
            filters.append("""EXISTS (SELECT 1 FROM dial_list_attempts t
                                      JOIN dial_list_assignments a ON a.id = t.assignment_id
                                      WHERE a.lead_id = base.id AND {})""".format(" AND ".join(call_bounds)))
        order = JOURNAL_SORTS.get(str(sort or "activity"), JOURNAL_SORTS["activity"])
        sql = self._JOURNAL_PAGE_SQL.format(scope=scope, filters=" AND ".join(filters), order=order,
                                            stage_ok=stage_ok, outcome_ok=outcome_ok)
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            # Последняя колонка — сводка, предпоследняя — rn; пустая страница
            # приходит одной строкой, где колонки страницы — NULL.
            summary = (rows[0][-1] if rows else None) or []
            page = [r for r in rows if r[0] is not None]
            total = int(page[0][self._JOURNAL_COLUMN_COUNT]) if page else 0
            items = [self._journal_row(r, settings["max_attempts"]) for r in page]
            by_stage = {k: 0 for k in LEAD_STAGES}
            outcome_counts = {}
            # (итог, тип) → водители; тип None — итог без типа («Без типа»).
            subtype_counts = {}
            for stage_value, outcome_value, subtype_value, for_stage, for_outcome in summary:
                by_stage[stage_value] = by_stage.get(stage_value, 0) + int(for_stage or 0)
                if outcome_value is not None:
                    outcome_counts[outcome_value] = outcome_counts.get(outcome_value, 0) + int(for_outcome or 0)
                    key = (outcome_value, subtype_value)
                    subtype_counts[key] = subtype_counts.get(key, 0) + int(for_outcome or 0)
            cur.execute("""
                SELECT id, name, color, is_active FROM dial_list_outcomes
                WHERE department_id = %(department_id)s
                ORDER BY is_active DESC, position
            """, params)
            outcome_rows = cur.fetchall()
            cur.execute("""
                SELECT s.id, s.outcome_id, s.name, s.is_active FROM dial_list_outcome_subtypes s
                WHERE s.department_id = %(department_id)s
                ORDER BY s.is_active DESC, s.position, s.created_at
            """, params)
            subtypes_by_outcome = {}
            for s in cur.fetchall():
                oid = _sid(s[1])
                subtypes_by_outcome.setdefault(oid, []).append({
                    "id": _sid(s[0]), "name": s[2], "is_active": bool(s[3]),
                    "count": subtype_counts.get((oid, _sid(s[0])), 0)})
            by_outcome = [{"id": _sid(r[0]), "name": r[1], "color": r[2], "is_active": bool(r[3]),
                           "count": outcome_counts.get(_sid(r[0]), 0),
                           "subtypes": subtypes_by_outcome.get(_sid(r[0]), []),
                           "none_count": subtype_counts.get((_sid(r[0]), None), 0)} for r in outcome_rows]
        return {
            "department_id": department_id, "items": items, "total": total,
            "limit": params["limit"], "offset": params["offset"],
            "period": None if period == "all" else period.isoformat(),
            "period_label": "Все месяцы" if period == "all" else period_label(period),
            "active_period": settings["period"],
            "by_stage": by_stage, "stages": LEAD_STAGES, "by_outcome": by_outcome,
            "sort": sort if sort in JOURNAL_SORTS else "activity",
        }

    def lead_department(self, lead_id):
        """Отдел лида (для проверки зоны руководителя) или 404."""
        with self.db._get_cursor() as cur:
            cur.execute("SELECT department_id FROM dial_list_leads WHERE id = %s", (str(lead_id),))
            row = cur.fetchone()
        if not row:
            raise DialListError("Водитель не найден в журнале", 404)
        return int(row[0])

    def attempt_department(self, attempt_id):
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT l.department_id FROM dial_list_attempts t
                JOIN dial_list_assignments a ON a.id = t.assignment_id
                JOIN dial_list_leads l ON l.id = a.lead_id
                WHERE t.id = %s
            """, (str(attempt_id),))
            row = cur.fetchone()
        if not row:
            raise DialListError("Попытка не найдена", 404)
        return int(row[0])

    def lead_card(self, lead_id):
        """Карточка водителя: строка журнала + все попытки, ручные действия, загрузки."""
        lead_id = str(lead_id)
        department_id = self.lead_department(lead_id)
        settings = self.department_settings(department_id)
        params = {"max_attempts": settings["max_attempts"], "retry_hours": settings["retry_after_hours"],
                  "lead_id": lead_id, "limit": 1, "offset": 0}
        sql = self._JOURNAL_SQL.format(scope="l.id = %(lead_id)s", filters="TRUE", order="created_at")
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row:
                raise DialListError("Водитель не найден в журнале", 404)
            lead = self._journal_row(row, settings["max_attempts"])
            cur.execute("""
                SELECT t.id, t.requested_at, t.operator_id, COALESCE(u.name, ''), t.internal_number, t.state,
                       t.disposition, t.billsec, t.waitsec, t.api_error, t.final_source, t.finished_at,
                       t.phone_event_at, t.phone_ended_at,
                       (t.general_call_id IS NOT NULL AND t.general_call_id <> ''),
                       a.id, a.result, a.state, a.created_at,
                       o.id, o.name, o.color, COALESCE(t.operator_comment, ''), t.outcome_at,
                       t.cancelled, t.operator_hangup_at, t.leg_sec,
                       os.id, os.name, t.ring_timeout_at, t.auto_hangup, t.talk_detected
                FROM dial_list_attempts t
                JOIN dial_list_assignments a ON a.id = t.assignment_id
                LEFT JOIN users u ON u.id = t.operator_id
                LEFT JOIN dial_list_outcomes o ON o.id = t.outcome_id
                LEFT JOIN dial_list_outcome_subtypes os ON os.id = t.outcome_subtype_id
                WHERE a.lead_id = %s
                ORDER BY t.requested_at DESC
            """, (lead_id,))
            attempts = []
            for r in cur.fetchall():
                state = r[5] or ""
                disposition = r[6] or ""
                auto_hangup = r[30] or ""
                # Исход — с поправкой на то, что услышал телефон (attempt_result): сообщение
                # сети и ответ без разговора — недозвон.
                result = (attempt_result(disposition, r[7], auto_hangup, r[31]) if state == "finished"
                          else ("failed" if state == "failed" else ""))
                if state == "finished" and not result:
                    result = "other"
                if r[24]:
                    result = "cancelled"
                attempts.append({
                    # Оператор сам завершил звонок (когда — operator_hangup_at); cancelled —
                    # водитель при этом не ответил, попытка не засчитана.
                    "cancelled": bool(r[24]), "operator_hangup_at": _iso(r[25]), "leg_sec": int(r[26] or 0),
                    # Телефон сам положил плечо на гудках — водитель не взял трубку. У ответившего
                    # в последний момент (разговор) и у отменённой попытки флага нет. У сообщения
                    # сети — тоже: старая сборка сайта подписала бы его «водитель не взял трубку».
                    "ring_timeout": (r[29] is not None and auto_hangup != AUTO_HANGUP_ANNOUNCEMENT
                                     and result not in ("answered", "cancelled")),
                    # Почему плечо положил сам телефон: 'ring_timeout' | 'announcement' | ''.
                    "auto_hangup": auto_hangup,
                    "id": _sid(r[0]), "requested_at": _iso(r[1]),
                    "operator": {"id": r[2], "name": r[3]}, "internal_number": r[4] or "",
                    "state": state, "disposition": disposition, "result": result,
                    "billsec": int(r[7] or 0), "waitsec": int(r[8] or 0), "api_error": r[9] or "",
                    "final_source": r[10] or "", "finished_at": _iso(r[11]),
                    "phone_event_at": _iso(r[12]), "phone_ended_at": _iso(r[13]),
                    # Запись есть у всего, что Binotel закрыл ответом с длительностью (так же
                    # судит attempt_recording): и у сообщения сети, которое мы считаем недозвоном, —
                    # по ней разбирают ложные срабатывания телефона. Ссылку даём по запросу —
                    # она подписанная и живёт около часа.
                    "recording_available": bool(r[14]) and state == "finished" and not r[24]
                                           and map_disposition(disposition) == "answered"
                                           and int(r[7] or 0) > 0,
                    "assignment": {"id": _sid(r[15]), "result": r[16] or "", "state": r[17] or "",
                                   "issued_at": _iso(r[18])},
                    "outcome": ({"id": _sid(r[19]), "name": r[20] or "", "color": r[21] or "#8E8E93",
                                 "subtype": ({"id": _sid(r[27]), "name": r[28] or ""}
                                             if r[27] is not None else None)}
                                if r[19] is not None else None),
                    "comment": r[22] or "",
                    "outcome_at": _iso(r[23]),
                    # Этому разговору засчитана успешка за подписание документов.
                    "is_success": bool(lead["sign"]["success"])
                                  and lead["sign"]["success"]["attempt_id"] == _sid(r[0]),
                })
            cur.execute("""
                SELECT e.id, e.kind, e.note, e.created_at, e.actor_id, COALESCE(u.name, '')
                FROM dial_list_lead_events e
                LEFT JOIN users u ON u.id = e.actor_id
                WHERE e.lead_id = %s ORDER BY e.created_at DESC
            """, (lead_id,))
            events = [{"id": int(r[0]), "kind": r[1], "note": r[2] or "", "at": _iso(r[3]),
                       "actor": {"id": r[4], "name": r[5]}} for r in cur.fetchall()]
            cur.execute("""
                SELECT a.id, a.created_at, a.state, a.result, a.done_at, a.operator_id, COALESCE(u.name, ''),
                       a.attempts
                FROM dial_list_assignments a
                LEFT JOIN users u ON u.id = a.operator_id
                WHERE a.lead_id = %s ORDER BY a.created_at DESC
            """, (lead_id,))
            assignments = [{"id": _sid(r[0]), "issued_at": _iso(r[1]), "state": r[2], "result": r[3] or "",
                            "done_at": _iso(r[4]), "operator": {"id": r[5], "name": r[6]},
                            "attempts": int(r[7] or 0)} for r in cur.fetchall()]
            cur.execute("""
                SELECT b.id, b.file_name, b.created_at, COALESCE(u.name, '')
                FROM dial_list_lead_batches b
                LEFT JOIN users u ON u.id = b.uploaded_by
                WHERE b.id IN (SELECT first_batch_id FROM dial_list_leads WHERE id = %s
                               UNION SELECT last_batch_id FROM dial_list_leads WHERE id = %s)
                ORDER BY b.created_at
            """, (lead_id, lead_id))
            batches = [{"id": _sid(r[0]), "file_name": r[1] or "", "uploaded_at": _iso(r[2]),
                        "uploaded_by": r[3]} for r in cur.fetchall()]
            cur.execute("SELECT sign_docs FROM dial_list_leads WHERE id = %s", (lead_id,))
            docs_row = cur.fetchone()
        docs = docs_row[0] if docs_row else []
        if isinstance(docs, str):
            docs = json.loads(docs or "[]")
        lead["sign"]["documents"] = [
            {"id": d.get("id"), "status": d.get("status") or "", "group": d.get("group") or "",
             "label": sapar_api.status_label(d.get("status")), "signed_at": d.get("signed_at")}
            for d in (docs or []) if isinstance(d, dict)]
        if lead.get("period"):
            month, year = signing.doc_month_for(datetime.strptime(lead["period"], "%Y-%m-%d").date())
            lead["sign"]["doc_month"] = f"{year:04d}-{month:02d}-01"
            lead["sign"]["doc_month_label"] = period_label(datetime(year, month, 1).date())
        lead["sign"]["can_check"] = bool(lead.get("has_iin")) and lead["sign"]["signed_at"] is None
        lead.update({"attempts": attempts, "events": events, "assignments": assignments, "batches": batches,
                     "retry_after_hours": settings["retry_after_hours"]})
        return lead

    def _log_lead_event(self, cur, lead_id, department_id, actor_id, kind, note=""):
        cur.execute("""
            INSERT INTO dial_list_lead_events (lead_id, department_id, actor_id, kind, note)
            VALUES (%s, %s, %s, %s, %s)
        """, (str(lead_id), int(department_id), actor_id, kind, str(note or "")[:500]))

    def requeue_lead(self, lead_id, actor_id, note=""):
        """Руководитель возвращает водителя в список: попытки обнуляются, лид снова в
        пуле. Строку, которая сейчас у оператора (issued), не трогаем — она и так в списке.

        Если водителя уже обработали, а его строка лежит в ПОСЛЕДНЕЙ выдаче оператора
        (открытой или закрытой — телефон показывает её до «Ещё»), строка
        переоткрывается прямо там: иначе оператор видел бы «Перезвонить позже» и не
        мог позвонить, пока не закроет всю пачку и не возьмёт следующую (владелец,
        24.09.2026). Строка в более старой выдаче не трогается — водитель придёт с
        ближайшей порцией."""
        lead_id = str(lead_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT department_id, status, signed_at, sign_status FROM dial_list_leads WHERE id = %s FOR UPDATE
            """, (lead_id,))
            row = cur.fetchone()
            if not row:
                raise DialListError("Водитель не найден в журнале", 404)
            if row[2] is not None or row[3] == signing.PROCESSING:
                # Правило владельца 29.09.2026: подписавший в список не возвращается. «На
                # проверке у Яндекса» — тоже подписал (11.10.2026): пул его не выдаст.
                raise DialListError("Водитель уже подписал документы — звонить ему незачем", 409)
            cur.execute("SELECT 1 FROM dial_list_assignments WHERE lead_id = %s AND state = 'issued'", (lead_id,))
            if cur.fetchone():
                raise DialListError("Строка сейчас у оператора — она и так в списке", 409)
            cur.execute("""
                UPDATE dial_list_leads
                SET status = 'new', attempts_total = 0, last_attempt_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (lead_id,))
            reopened = self._reopen_in_latest_portion(cur, lead_id)
            self._log_lead_event(cur, lead_id, row[0], actor_id,
                                 "restore" if row[1] == "excluded" else "requeue", note)
        card = self.lead_card(lead_id)
        card["reopened_for_operator"] = reopened
        return card

    def _reopen_in_latest_portion(self, cur, lead_id, active_in_department=None):
        """Обработанная строка водителя в последней выдаче её оператора → снова issued;
        закрытая выдача открывается обратно. Возвращает True, если переоткрыли.
        active_in_department — открывать, только если оператор строки ещё сидит в этом отделе
        (_active_member): ушедшему водитель достался бы навсегда (починка автосброса)."""
        cur.execute("""
            SELECT a.id, a.portion_id, a.operator_id
            FROM dial_list_assignments a
            WHERE a.lead_id = %s AND a.state = 'done'
            ORDER BY a.created_at DESC LIMIT 1
        """, (str(lead_id),))
        last = cur.fetchone()
        if not last:
            return False
        if active_in_department is not None and not self._active_member(cur, last[2], active_in_department):
            return False
        cur.execute("""
            SELECT id FROM dial_list_portions WHERE operator_id = %s
            ORDER BY issued_at DESC LIMIT 1
        """, (int(last[2]),))
        latest = cur.fetchone()
        if not latest or str(latest[0]) != str(last[1]):
            return False
        self._reopen_row(cur, last[0], last[1])
        log.info("dial_list: водитель %s возвращён в список — строка снова у оператора %s", lead_id, last[2])
        return True

    def _reopen_row(self, cur, assignment_id, portion_id):
        """Обработанная строка → снова issued, её выдача открыта обратно. Так же строку
        возвращает «Позвонить ещё раз» оператора (recall_lead). В транзакции вызывающего."""
        cur.execute("""
            UPDATE dial_list_assignments
            SET state = 'issued', result = '', done_at = NULL
            WHERE id = %s
        """, (str(assignment_id),))
        cur.execute("UPDATE dial_list_portions SET closed_at = NULL WHERE id = %s", (str(portion_id),))

    def _close_row(self, cur, assignment_id):
        """Строку, по которой звонка не будет, — закрыть без результата («другое»), а
        выдачу — если обрабатывать в ней больше нечего. Так снимает строку «Исключить»,
        звонок подписавшему (start_call) и «Проверить на подписание», нашедшая подпись
        (_close_signed_row). В транзакции вызывающего."""
        cur.execute("""
            UPDATE dial_list_assignments
            SET state = 'done', result = 'other', done_at = CURRENT_TIMESTAMP
            WHERE id = %s RETURNING portion_id
        """, (str(assignment_id),))
        portion = cur.fetchone()
        if portion:
            cur.execute("""
                UPDATE dial_list_portions SET closed_at = CURRENT_TIMESTAMP
                WHERE id = %s AND closed_at IS NULL
                  AND NOT EXISTS (SELECT 1 FROM dial_list_assignments
                                  WHERE portion_id = %s AND state = 'issued')
            """, (str(portion[0]), str(portion[0])))

    def exclude_lead(self, lead_id, actor_id, note=""):
        """Исключить водителя из обзвона. Если строка у оператора — снимаем её с его
        списка (закрываем выдачу без результата); во время идущего звонка — нельзя."""
        lead_id = str(lead_id)
        with self.db._get_cursor() as cur:
            cur.execute("SELECT department_id, status FROM dial_list_leads WHERE id = %s FOR UPDATE", (lead_id,))
            row = cur.fetchone()
            if not row:
                raise DialListError("Водитель не найден в журнале", 404)
            if row[1] == "excluded":
                raise DialListError("Водитель уже исключён", 409)
            cur.execute("""
                SELECT a.id FROM dial_list_assignments a WHERE a.lead_id = %s AND a.state = 'issued' FOR UPDATE
            """, (lead_id,))
            open_assignment = cur.fetchone()
            if open_assignment:
                cur.execute("""
                    SELECT 1 FROM dial_list_attempts
                    WHERE assignment_id = %s AND state IN ('requested', 'leg_ringing', 'leg_answered', 'ended')
                """, (str(open_assignment[0]),))
                if cur.fetchone():
                    raise DialListError("Оператор сейчас звонит этому водителю — дождитесь окончания", 409)
                self._close_row(cur, open_assignment[0])
            cur.execute("""
                UPDATE dial_list_leads
                SET status = 'excluded', updated_at = CURRENT_TIMESTAMP,
                    note = CASE WHEN %s <> '' THEN %s ELSE note END
                WHERE id = %s
            """, (str(note or "")[:500], str(note or "")[:500], lead_id))
            self._log_lead_event(cur, lead_id, row[0], actor_id, "exclude", note)
        return self.lead_card(lead_id)

    def set_lead_note(self, lead_id, actor_id, note):
        lead_id = str(lead_id)
        note = str(note or "").strip()[:500]
        with self.db._get_cursor() as cur:
            cur.execute("SELECT department_id FROM dial_list_leads WHERE id = %s FOR UPDATE", (lead_id,))
            row = cur.fetchone()
            if not row:
                raise DialListError("Водитель не найден в журнале", 404)
            cur.execute("UPDATE dial_list_leads SET note = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                        (note, lead_id))
            self._log_lead_event(cur, lead_id, row[0], actor_id, "note", note)
        return self.lead_card(lead_id)

    # ------------------------------------------------------------ починка: разговоры, оборванные автосбросом
    # 12.10.2026 автосброс выпущенного телефона 3.22.33 обрывал живые разговоры: ответ Binotel на
    # вопрос «класть ли плечо» не успевал за пять секунд или был «нет данных», и телефон клал
    # трубку посреди разговора — 52 разговора, 51 водитель, 7 операторов; итог операторы ставили
    # уже по оборванному звонку («Сброс» и т. п.). Владелец: вернуть этих водителей в очередь ТОГО
    # ЖЕ оператора. Оборванный разговор — попытка со сбросом на гудках (ring_timeout_at), которую
    # Binotel закрыл ответом с длительностью: водитель успел ответить, а телефон всё равно положил
    # плечо. Сообщение сети (auto_hangup 'announcement') — не разговор.
    REPAIR_CUT_NOTE = "Автосброс оборвал разговор"
    REPAIR_CUT_LIMIT = 500
    REPAIR_TARGETS = ("operator", "pool")
    # Последний оборванный разговор каждого водителя отдела с момента since — если водитель всё
    # ещё ждёт звонка: не подписал и не «на проверке у Яндекса», не исключён, ни у кого не в
    # списке, настоящего разговора после обрыва не было (ответ с длительностью без сброса на
    # гудках), и его ещё не возвращали этой же починкой (событие requeue с её пометкой после
    # обрыва) — повторный запуск ничего не повторит. {lead_filter} — TRUE для всего отдела или
    # один водитель (повторная проверка под замком перед возвратом). Ни номера, ни ИИН.
    _REPAIR_CUT_SQL = """
        WITH cut AS (
            SELECT DISTINCT ON (a.lead_id)
                   a.lead_id, t.id AS attempt_id, t.operator_id, t.requested_at, t.billsec,
                   t.outcome_id, t.outcome_subtype_id
            FROM dial_list_attempts t
            JOIN dial_list_assignments a ON a.id = t.assignment_id
            JOIN dial_list_leads l ON l.id = a.lead_id
            WHERE l.department_id = %(department_id)s
              AND t.requested_at >= %(since)s
              AND t.ring_timeout_at IS NOT NULL
              AND t.state = 'finished' AND NOT t.cancelled
              AND UPPER(t.disposition) = 'ANSWER' AND t.billsec > 0
              AND t.auto_hangup <> 'announcement'
            ORDER BY a.lead_id, t.requested_at DESC
        )
        SELECT c.lead_id, l.full_name, c.attempt_id, c.operator_id, COALESCE(u.name, ''), c.requested_at,
               c.billsec, o.name, os.name
        FROM cut c
        JOIN dial_list_leads l ON l.id = c.lead_id
        LEFT JOIN users u ON u.id = c.operator_id
        LEFT JOIN dial_list_outcomes o ON o.id = c.outcome_id
        LEFT JOIN dial_list_outcome_subtypes os ON os.id = c.outcome_subtype_id
        WHERE {lead_filter}
          AND l.signed_at IS NULL AND l.sign_status <> 'processing'
          AND l.status <> 'excluded'
          AND NOT EXISTS (SELECT 1 FROM dial_list_assignments x WHERE x.lead_id = l.id AND x.state = 'issued')
          AND NOT EXISTS (
              SELECT 1 FROM dial_list_attempts t2
              JOIN dial_list_assignments a2 ON a2.id = t2.assignment_id
              WHERE a2.lead_id = l.id AND t2.requested_at > c.requested_at
                AND t2.state = 'finished' AND NOT t2.cancelled
                AND UPPER(t2.disposition) IN %(answered)s AND t2.billsec >= 1
                AND t2.ring_timeout_at IS NULL AND t2.auto_hangup <> 'announcement')
          AND NOT EXISTS (
              SELECT 1 FROM dial_list_lead_events e
              WHERE e.lead_id = l.id AND e.kind = 'requeue' AND e.note LIKE %(marker)s
                AND e.created_at >= c.requested_at)
        ORDER BY c.requested_at, c.lead_id
        LIMIT %(limit)s
    """
    # Сидит ли человек в отделе сейчас — то же правило, что у списка операторов отдела
    # (department_users): работает, и либо сотрудник отдела не на чужой линии, либо посажен
    # на линию этого отдела.
    _ACTIVE_MEMBER_SQL = """
        SELECT 1 FROM users u
        LEFT JOIN dial_list_line_members m ON m.user_id = u.id AND m.released_at IS NULL
        WHERE u.id = %s AND LOWER(COALESCE(u.status, '')) <> ALL(%s)
          AND ((u.department_id = %s AND (m.user_id IS NULL OR m.department_id = %s))
               OR m.department_id = %s)
    """

    @staticmethod
    def parse_since(value):
        """since починки → момент с поясом. ISO: «2026-10-12T00:00», «2026-10-12 09:30:00+05:00»,
        «2026-10-12» (полночь). Без пояса — время Алматы: так руководитель и пишет."""
        text = str(value or "").strip()
        if not text:
            raise DialListError("since: укажите, с какого момента искать, например 2026-10-12T00:00")
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise DialListError("since: ожидается дата и время ISO, например 2026-10-12T00:00")
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=PERIOD_TZ)

    def _active_member(self, cur, user_id, department_id):
        """Сидит ли сотрудник в отделе сейчас (_ACTIVE_MEMBER_SQL). В транзакции вызывающего."""
        if user_id is None:
            return False
        department_id = int(department_id)
        cur.execute(self._ACTIVE_MEMBER_SQL, (int(user_id), self._inactive_statuses(), department_id,
                                              department_id, department_id))
        return cur.fetchone() is not None

    def _repair_item(self, r, today):
        """Строка _REPAIR_CUT_SQL → водитель для руководителя: ФИО, кто и когда звонил, сколько
        длился разговор и какой итог поставил оператор. Без номера и ИИН."""
        return {
            "lead_id": str(r[0]), "full_name": r[1] or "Без имени", "attempt_id": str(r[2]),
            "operator": {"id": r[3], "name": r[4] or ""},
            "call_at": _iso(r[5]), "call_at_label": at_label(r[5], today), "billsec": int(r[6] or 0),
            "outcome": ({"name": r[7], "subtype": r[8] or ""} if r[7] else None),
        }

    def repair_cut_calls(self, department_id, since, apply=False, target="operator", actor_id=None):
        """Водители отдела, чьи разговоры оборвал автосброс с момента since (_REPAIR_CUT_SQL):
        apply=False — только список; apply=True — вернуть их в очередь.

        target «operator» — в очередь того же оператора, как «Позвонить ещё раз» (_recall_row):
        его строка этого водителя в последней выдаче снова открыта (телефон покажет «Снова в
        списке»), иначе — новая строка в конец его последней выдачи. Оператора в отделе больше
        нет (уволен, переведён, снят с линии) — как «pool». target «pool» — как «Вернуть в
        список» руководителя (requeue_lead): попытки с нуля, водитель снова в общем пуле, а его
        строку в последней выдаче оператора открываем обратно, только если тот ещё в отделе.

        Каждый водитель — своей транзакцией, под замком строки водителя, с повторной проверкой
        условий: за время обхода его могли выдать, исключить или он подписал. В журнал водителя —
        событие requeue с пометкой «Автосброс оборвал разговор <дата> — возвращён в очередь»: по
        ней повторный запуск этого водителя уже не выберет."""
        department_id = int(department_id)
        target = str(target or "operator").strip().lower()
        if target not in self.REPAIR_TARGETS:
            raise DialListError("target: operator или pool")
        moment = self.parse_since(since)
        params = {"department_id": department_id, "since": moment, "answered": ANSWERED_SQL,
                  "marker": self.REPAIR_CUT_NOTE + "%", "limit": self.REPAIR_CUT_LIMIT}
        today = _today()
        with self.db._get_cursor() as cur:
            cur.execute(self._REPAIR_CUT_SQL.format(lead_filter="TRUE"), params)
            items = [self._repair_item(r, today) for r in cur.fetchall()]
        result = {"department_id": department_id, "since": moment.isoformat(), "target": target,
                  "apply": bool(apply), "count": len(items), "items": items}
        if not apply:
            return result
        applied = {"operator": 0, "pool": 0, "skipped": 0}
        for item in items:
            try:
                how, reason = self._repair_one(department_id, params, item, target, actor_id)
            except psycopg2.errors.UniqueViolation:
                # Водителя только что выдали другому: открытая строка у лида одна.
                how, reason = "skipped", "водителя только что выдали в список"
            except Exception:
                # Один водитель (взаимная блокировка, сбой базы) не останавливает остальных: его
                # транзакция откатилась, повторный запуск возьмёт его снова.
                log.exception("dial_list: починка автосброса — водитель %s не возвращён", item["lead_id"])
                how, reason = "skipped", "ошибка сервера — запустите ещё раз"
            item["result"], item["reason"] = how, reason
            applied[how] += 1
        result["applied"] = applied
        log.info("dial_list: починка автосброса, отдел %s с %s (%s, руководитель %s): найдено %d, к оператору %d, "
                 "в общий список %d, пропущено %d", department_id, moment.isoformat(), target, actor_id, len(items),
                 applied["operator"], applied["pool"], applied["skipped"])
        return result

    def _repair_one(self, department_id, params, item, target, actor_id):
        """Вернуть одного водителя (repair_cut_calls) → (operator | pool | skipped, почему пропущен)."""
        lead_id = item["lead_id"]
        local = datetime.fromisoformat(item["call_at"]).astimezone(PERIOD_TZ) if item["call_at"] else None
        note = f"{self.REPAIR_CUT_NOTE} {local:%d.%m.%Y %H:%M} — возвращён в очередь" if local else (
            f"{self.REPAIR_CUT_NOTE} — возвращён в очередь")
        operator_id = item["operator"]["id"]
        with self.db._get_cursor() as cur:
            if target == "operator" and operator_id is not None:
                # Замок оператора — первым, как у «Позвонить ещё раз» (recall_lead: замок, затем
                # водитель): обратный порядок мог бы сцепиться с его же кнопкой.
                self._lock_operator(cur, operator_id)
            cur.execute("SELECT id FROM dial_list_leads WHERE id = %s AND department_id = %s FOR UPDATE",
                        (lead_id, department_id))
            if not cur.fetchone():
                return "skipped", "водителя нет в базе отдела"
            # Под замком строки водителя и новым запросом: всё, что успело случиться за обход, видно.
            cur.execute(self._REPAIR_CUT_SQL.format(lead_filter="l.id = %(lead_id)s"), dict(params, lead_id=lead_id))
            row = cur.fetchone()
            if not row or str(row[2]) != item["attempt_id"]:
                return "skipped", "уже не ждёт звонка: подписал, исключён, в списке или ему уже позвонили"
            how = target
            if how == "operator" and self._active_member(cur, operator_id, department_id):
                self._recall_row(cur, {"user_id": int(operator_id), "department_id": department_id}, lead_id)
            else:
                how = "pool"
                cur.execute("""
                    UPDATE dial_list_leads
                    SET status = 'new', attempts_total = 0, last_attempt_at = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (lead_id,))
                self._reopen_in_latest_portion(cur, lead_id, active_in_department=department_id)
            self._log_lead_event(cur, lead_id, department_id, actor_id, "requeue", note)
        return how, ""

    def attempt_recording(self, attempt_id):
        """Ссылка на запись разговора у Binotel (подписанная, живёт ~1 час)."""
        attempt_id = str(attempt_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT t.general_call_id, t.state, t.disposition, t.billsec, l.department_id
                FROM dial_list_attempts t
                JOIN dial_list_assignments a ON a.id = t.assignment_id
                JOIN dial_list_leads l ON l.id = a.lead_id
                WHERE t.id = %s
            """, (attempt_id,))
            row = cur.fetchone()
        if not row:
            raise DialListError("Попытка не найдена", 404)
        general_call_id, state, disposition, billsec, department_id = row
        if not general_call_id:
            raise DialListError("У этой попытки не было звонка через АТС", 404)
        if state != "finished" or map_disposition(disposition) != "answered" or int(billsec or 0) <= 0:
            raise DialListError("Разговора не было — записи нет", 404)
        try:
            url = self._client(int(department_id)).get_call_record_url(general_call_id)
        except DialListError:
            raise
        except Exception as exc:
            log.warning("dial_list: запись %s недоступна: %s", general_call_id, exc)
            raise DialListError("АТС не отдала запись. Попробуйте позже", 502)
        if not url:
            raise DialListError("Запись ещё не готова у АТС. Попробуйте через минуту", 404)
        return {"attempt_id": attempt_id, "url": url, "billsec": int(billsec or 0)}

    # Пул: лиды отдела, которые можно выдать сейчас. Один и тот же текст для
    # выдачи (с блокировкой) и для подсчёта (без неё), чтобы цифра «доступно»
    # совпадала с тем, что реально выдаётся. Подписавший документы (signed_at) в
    # пул не возвращается никогда: обрабатывать его незачем (владелец, 29.09.2026).
    # «На проверке у Яндекса» (sign_status 'processing') — тоже: водитель уже
    # подписал, ждём только Яндекс (владелец, 11.10.2026). Те же условия — у этапа
    # журнала (CASE в _JOURNAL_BASE_SQL) и у leads_open отдела.
    _POOL_SQL = """
        SELECT l.id
        FROM dial_list_leads l
        WHERE l.department_id = %s
          AND l.period = %s
          AND l.status IN ('new', 'in_progress')
          AND l.signed_at IS NULL
          AND l.sign_status <> 'processing'
          AND NOT EXISTS (SELECT 1 FROM dial_list_assignments a
                          WHERE a.lead_id = l.id AND a.state = 'issued')
          AND (l.last_attempt_at IS NULL
               OR l.last_attempt_at < CURRENT_TIMESTAMP - make_interval(hours => %s))
          AND l.attempts_total < %s
        ORDER BY l.attempts_total ASC, l.last_attempt_at ASC NULLS FIRST, l.created_at ASC
        LIMIT %s {lock}
    """
    # Кандидаты проверки подписания перед выдачей (_precheck_signatures): тот же пул —
    # те же условия и порядок, — только с тем, что нужно проверке, и без блокировки.
    # ИИН нужен лишь запросу в Sapar и из сервера не уходит.
    _POOL_CANDIDATES_SQL = _POOL_SQL.replace(
        "SELECT l.id", "SELECT l.id, l.iin, l.period, l.sign_checked_at, l.sign_error", 1).format(lock="")

    # ------------------------------------------------------------ оператор
    def operator_context(self, user_id):
        """Кто звонит: отдел, внутренний номер, включён ли режим. DialListError, если нельзя.
        У сидящего на линии другого отдела отдел и номер — ЛИНИИ (dial_operator)."""
        operator = self.dial_operator(user_id)
        if not operator:
            raise DialListError("Сотрудник не найден", 404)
        if (operator.get("department_provider") or "asterisk") != "binotel":
            raise DialListError("Обзвон из телефона работает только у отделов на Binotel", 409)
        department_id = operator.get("department_id")
        if department_id is None:
            raise DialListError("У сотрудника не указан отдел", 409)
        settings = self.department_settings(department_id)
        enabled = resolve_enabled(self.user_setting(user_id), settings["enabled"] if settings["configured"] else None)
        if not enabled:
            raise DialListError("Обзвон из телефона для вас не включён", 403)
        internal_number = str(operator.get("sip_number") or "").strip()
        if not internal_number:
            raise DialListError("У сотрудника не указан внутренний номер Binotel (SIP-номер)", 409)
        return {
            "user_id": int(user_id),
            "name": operator.get("name") or "",
            "department_id": int(department_id),
            "internal_number": internal_number,
            "settings": settings,
        }

    def _lock_operator(self, cur, user_id):
        """Выдача строк оператору — по одной за раз: второй «Ещё» (повтор запроса,
        двойной клик — с проверкой подписания «Ещё» идёт секунды) или «Перезвонить»
        ждёт первый и видит уже его выдачу. Без замка оба проходили проверку открытой
        выдачи, и у оператора появлялись две, а строки старшей висели невидимыми.
        Замок живёт до конца транзакции этого курсора."""
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"dial_list_operator:{int(user_id)}",))

    def _open_portion(self, cur, user_id):
        cur.execute("""
            SELECT id, size, issued_at FROM dial_list_portions
            WHERE operator_id = %s AND closed_at IS NULL
            ORDER BY issued_at DESC LIMIT 1
        """, (int(user_id),))
        row = cur.fetchone()
        return {"id": str(row[0]), "size": int(row[1]), "issued_at": row[2], "closed_at": None} if row else None

    def _last_portion(self, cur, user_id):
        """Последняя выдача оператора, открытая или уже закрытая. Закрытую телефон
        показывает целиком с итогами, пока оператор не возьмёт следующую: иначе после
        последней строки список исчезал, и итоги пачки никто не видел (владелец, 24.09.2026)."""
        cur.execute("""
            SELECT id, size, issued_at, closed_at FROM dial_list_portions
            WHERE operator_id = %s
            ORDER BY closed_at IS NULL DESC, issued_at DESC LIMIT 1
        """, (int(user_id),))
        row = cur.fetchone()
        return {"id": str(row[0]), "size": int(row[1]), "issued_at": row[2], "closed_at": row[3]} if row else None

    # Последний непустой комментарий ЭТОГО оператора по водителю (владелец, 12.10.2026:
    # оператор должен видеть, что записал в прошлый раз) — из любой его строки по этому
    # водителю и за любое время, а не только из последней попытки строки. К нему — когда
    # он оставлен (время итога, а нет итога — звонка) и название итога той попытки. Один
    # кусок SQL для строк пачки, вкладок итогов, идущего звонка и ответа «Позвонить»;
    # {lead} и {operator} — выражения водителя и оператора в запросе вызывающего. Чужих
    # комментариев здесь нет; данных водителя из базы — тоже.
    _LAST_COMMENT_JOIN_SQL = """
        LEFT JOIN LATERAL (
            SELECT ct.operator_comment AS last_comment,
                   COALESCE(ct.outcome_at, ct.requested_at) AS last_comment_at,
                   co.name AS last_comment_outcome
            FROM dial_list_attempts ct
            JOIN dial_list_assignments ca ON ca.id = ct.assignment_id
            LEFT JOIN dial_list_outcomes co ON co.id = ct.outcome_id
            WHERE ca.lead_id = {lead} AND ct.operator_id = {operator}
              AND ct.operator_comment <> ''
            ORDER BY COALESCE(ct.outcome_at, ct.requested_at) DESC
            LIMIT 1
        ) lc ON TRUE
    """
    # Колонки к нему — в том порядке, в каком их разбирает _last_comment_fields.
    _LAST_COMMENT_COLUMNS = "lc.last_comment, lc.last_comment_at, lc.last_comment_outcome"
    # Тот же кусок отдельным запросом — по одному водителю (_last_comment).
    _LAST_COMMENT_SQL = ("SELECT " + _LAST_COMMENT_COLUMNS + " FROM (SELECT 1) one"
                         + _LAST_COMMENT_JOIN_SQL.format(lead="%(lead_id)s", operator="%(operator_id)s"))

    @staticmethod
    def _last_comment_fields(comment, at, outcome, today):
        """Поля комментария для телефона из колонок _LAST_COMMENT_COLUMNS: текст, когда
        оставлен — «сегодня, 15:14» / «вчера, 09:12» / «12.10, 14:05» по Алматы — и название
        итога той попытки (без типа). Комментария нет — все три пустые."""
        comment = comment or ""
        return {
            "last_comment": comment,
            "last_comment_label": checked_at_label(at, today) if comment else "",
            "last_comment_outcome": (outcome or "") if comment else "",
        }

    def _last_comment(self, cur, user_id, lead_id, today=None):
        """Последний комментарий оператора по водителю (_LAST_COMMENT_SQL) — для идущего
        звонка в состоянии и для ответа «Позвонить». В транзакции вызывающего."""
        r = None
        if lead_id:
            cur.execute(self._LAST_COMMENT_SQL, {"lead_id": str(lead_id), "operator_id": int(user_id)})
            r = cur.fetchone()
        r = r or (None, None, None)
        return self._last_comment_fields(r[0], r[1], r[2], today or _today())

    def _portion_items(self, cur, portion_id, today=None):
        # Новые колонки — только в конец (строки читаются по номерам): r[18] — почему плечо
        # последней попытки положил сам телефон, r[19:22] — _LAST_COMMENT_COLUMNS.
        cur.execute("""
            SELECT a.id, a.position, l.full_name, a.state, a.result, a.attempts, a.done_at,
                   t.id, t.state, t.disposition, t.requested_at, t.general_call_id,
                   o.name, o.color, t.cancelled, t.operator_hangup_at, a.lead_id,
                   os.name, t.auto_hangup, """ + self._LAST_COMMENT_COLUMNS + """
            FROM dial_list_assignments a
            JOIN dial_list_leads l ON l.id = a.lead_id
            LEFT JOIN LATERAL (
                SELECT id, state, disposition, requested_at, general_call_id, outcome_id,
                       cancelled, operator_hangup_at, outcome_subtype_id, auto_hangup
                FROM dial_list_attempts WHERE assignment_id = a.id
                ORDER BY requested_at DESC LIMIT 1
            ) t ON TRUE
            LEFT JOIN dial_list_outcomes o ON o.id = t.outcome_id
            LEFT JOIN dial_list_outcome_subtypes os ON os.id = t.outcome_subtype_id
            """ + self._LAST_COMMENT_JOIN_SQL.format(lead="a.lead_id", operator="a.operator_id") + """
            WHERE a.portion_id = %s
            ORDER BY a.position
        """, (portion_id,))
        today = today or _today()
        items = []
        for r in cur.fetchall():
            items.append({
                "assignment_id": str(r[0]),
                # По нему телефон узнаёт того же водителя на вкладке итога (не номер).
                "lead_id": str(r[16]),
                "position": int(r[1]),
                "full_name": r[2] or "Без имени",
                "state": r[3],
                "result": r[4] or "",
                "attempts": int(r[5] or 0),
                "done_at": _iso(r[6]),
                "last_attempt": None if r[7] is None else {
                    "attempt_id": str(r[7]), "state": r[8], "disposition": r[9] or "",
                    "requested_at": _iso(r[10]), "general_call_id": r[11],
                    "outcome_name": r[12] or "", "outcome_color": r[13] or "",
                    # Тип итога (29.09.2026): строка очереди — «Отказ · Дорого».
                    "outcome_subtype_name": r[17] or "",
                    # Оператор сам завершил звонок; cancelled — до ответа водителя,
                    # попытка не засчитана и строка осталась в работе.
                    "cancelled": bool(r[14]), "operator_hangup": r[15] is not None,
                    # Плечо положил сам телефон: 'ring_timeout' — на гудках, 'announcement' —
                    # сообщение сети (телефон подпишет «Абонент недоступен»); '' — не клал.
                    "auto_hangup": r[18] or "",
                },
                # Что оператор записал по этому водителю в прошлый раз (12.10.2026).
                **self._last_comment_fields(r[19], r[20], r[21], today),
            })
        return items

    def _active_attempt(self, cur, user_id, include_ended=False):
        """Идущий звонок оператора: плечо по телефону ещё идёт (LEG_UP_STATES) и попытка моложе
        ACTIVE_ATTEMPT_WINDOW_MINUTES. Он один держит «Позвонить» — и на сервере (start_call,
        recall_lead), и на телефоне (active_attempt состояния).

        Кончившееся плечо (ended) звонком больше не считается (13.10.2026): исход от АТС
        приходит секунды, бывало — минуту, и всё это время оператор ждал «Ждём исход от АТС…».
        Такую попытку закроет опрос (_settle_live, reconcile) или вебхук, а если по ней окажется
        нужен итог — его потребует следующий звонок (_require_outcome_done).

        include_ended — прежний смысл (LIVE_STATES: и ended) для состояния телефона без заголовка
        версии, выпущенного 3.22.33 (ревью 13.10.2026). После «Завершить» он ждёт решения сервера и
        понимает так: попытка, решения по которой он ждёт, не active_attempt и не pending_outcome —
        значит, сервер решил, что итог не нужен («Итог по этому звонку не требуется»). С ended вне active_attempt он так
        решал до исхода Binotel, пускал звонить дальше, а итог просил потом — посреди следующего
        разговора. Сервер звонок ему при этом не запрещает (start_call, recall_lead — без ended):
        пока 3.22.33 видит active_attempt, он «Позвонить» не даёт сам."""
        # lead_id (12.10.2026) — с кем идёт разговор: по нему телефон спрашивает подписание
        # прямо во время звонка (operator_sign_check). Номера здесь нет.
        states = ", ".join("'%s'" % state for state in (LIVE_STATES if include_ended else LEG_UP_STATES))
        cur.execute("""
            SELECT t.id, t.assignment_id, t.state, t.general_call_id, t.requested_at, l.full_name, a.lead_id
            FROM dial_list_attempts t
            JOIN dial_list_assignments a ON a.id = t.assignment_id
            JOIN dial_list_leads l ON l.id = a.lead_id
            WHERE t.operator_id = %s
              AND t.state IN (""" + states + """)
              AND t.requested_at > CURRENT_TIMESTAMP - make_interval(mins => %s)
            ORDER BY t.requested_at DESC LIMIT 1
        """, (int(user_id), ACTIVE_ATTEMPT_WINDOW_MINUTES))
        r = cur.fetchone()
        if not r:
            return None
        return {"attempt_id": str(r[0]), "assignment_id": str(r[1]), "state": r[2],
                "general_call_id": r[3], "requested_at": _iso(r[4]), "full_name": r[5] or "",
                "lead_id": _sid(r[6])}

    def _ring_timeout_sec(self, department_id):
        """Сколько секунд плечо обзвона ждёт ответа водителя, прежде чем телефон его
        положит; 0 — не класть. Отсчёт у телефона — от принятия плеча, когда Binotel
        начинает набирать водителя, поэтому без поправки обычного исходящего на первый
        гудок: 5 гудков — 25 с.

        Отдельной короткой транзакцией: колонки ring_timeout_rings может ещё не быть (её
        заводит схема «Настроек SIP»), а упавший запрос оборвал бы транзакцию состояния.
        Не прочитали — действует умолчание: состояние телефона из-за этого не ломается."""
        rings = RING_TIMEOUT_RINGS_DEFAULT
        try:
            with self.db._get_cursor() as cur:
                cur.execute("SELECT ring_timeout_rings FROM sip_department_config WHERE department_id = %s",
                            (int(department_id),))
                row = cur.fetchone()
            if row:
                rings = ring_timeout_rings(row[0])
        except Exception as exc:
            log.debug("dial_list: гудки до сброса у отдела %s не прочитаны (%s) — по умолчанию %d",
                      department_id, exc, RING_TIMEOUT_RINGS_DEFAULT)
        return rings * RING_SECONDS_PER_RING

    def get_state(self, user_id, phone_version=None):
        """Состояние обзвона для телефона. phone_version — заголовок X-ICore-Phone-Version
        запроса: автосброс на гудках (ring_timeout_sec) получает только телефон с защитой
        по речи (phone_guards_speech); остальным, и выпущенному 3.22.33, — 0, «не класть».
        Ему же active_attempt — в прежнем смысле, с кончившимся плечом (_active_attempt)."""
        ctx = self.operator_context(user_id)
        self.reconcile(ctx)
        settings = ctx["settings"]
        today = _today()
        guarded = phone_guards_speech(phone_version)
        with self.db._get_cursor() as cur:
            # Открытая выдача либо последняя закрытая: обработанная пачка остаётся на
            # экране с итогами, пока оператор не возьмёт следующую («Ещё»).
            portion = self._last_portion(cur, user_id)
            items = self._portion_items(cur, portion["id"], today) if portion else []
            active = self._active_attempt(cur, user_id, include_ended=not guarded)
            if active:
                # Что оператор записал по этому водителю раньше — в карточку звонка.
                active.update(self._last_comment(cur, user_id, active["lead_id"], today))
            cur.execute(self._POOL_SQL.format(lock="").replace("LIMIT %s", ""), (
                ctx["department_id"], settings["_period"], settings["retry_after_hours"], settings["max_attempts"]))
            pool = len(cur.fetchall())
            outcomes = self._outcome_rows(cur, ctx["department_id"], active_only=True)
            pending_outcome = self._pending_outcome(cur, ctx["user_id"]) if outcomes else None
            # Версия скрипта отдела: телефон перечитывает скрипт, когда она меняется.
            cur.execute("SELECT version FROM dial_list_scripts WHERE department_id = %s", (ctx["department_id"],))
            sv = cur.fetchone()
            script_version = int(sv[0]) if sv else 0
        # Строку, плечо по которой уже кончилось, а исход от АТС ещё едет (ended), обработанной
        # считаем (13.10.2026): «Ещё» она не держит — исход закроет её и в следующей выдаче.
        pending = sum(1 for i in items if i["state"] == "issued" and not self._row_settling(i))
        # Телефону без защиты по речи (нет заголовка версии — выпущенный 3.22.33) автосброс
        # не даём: его вопрос «класть ли плечо» ждёт ответа Binotel всего пять секунд, а
        # молчание он понимает как «класть» — так рвались разговоры (12.10.2026). Настройку
        # отдела для него даже не читаем.
        ring_timeout_sec = self._ring_timeout_sec(ctx["department_id"]) if guarded else 0
        ms = announcement_ms()
        return {
            "enabled": True,
            "operator": {"id": ctx["user_id"], "name": ctx["name"], "internal_number": ctx["internal_number"]},
            "portion_size": settings["portion_size"],
            "leg_timeout_sec": LEG_TIMEOUT_SEC,
            # Сброс на гудках: через столько секунд после принятия плеча, если водитель не
            # ответил, телефон кладёт плечо сам (событие ring_timeout); 0 — не кладёт.
            "ring_timeout_sec": ring_timeout_sec,
            # Анализ звука на плече (телефон с 3.22.34, остальные ключей не читают):
            # speech_guard — не класть плечо на гудках, пока на линии речь; announcement_ms —
            # сколько миллисекунд должна звучать речь сообщения сети, чтобы телефон его распознал,
            # 0 — по речи не распознавать; announcement_sit — класть плечо по тону SIT сразу;
            # announcement_mode — что делать с сообщением сети, распознанным по речи: auto — класть
            # (ended с auto_hangup: announcement), suggest — подсказать оператору, off — ничего
            # (см. ANNOUNCEMENT_MODE_ENV). Нет ключей (прежний сервер) — телефон считает sit=true и
            # suggest.
            "speech_guard": speech_guard_enabled(),
            "announcement_ms": ms,
            "announcement_sit": announcement_sit_enabled(),
            "announcement_mode": announcement_mode(ms),
            "period": settings["period"],
            "period_label": settings["period_label"],
            "portion": None if not portion else {
                "id": portion["id"], "size": portion["size"], "issued_at": _iso(portion["issued_at"]),
                "pending": pending, "items": items,
                "closed": portion["closed_at"] is not None, "closed_at": _iso(portion["closed_at"]),
            },
            "can_request_next": pending == 0 and pool > 0 and pending_outcome is None,
            "pool_available": pool,
            "active_attempt": active,
            # Справочник итогов для окна после разговора и попытка, по которой итог
            # ещё не указан (телефон не даст звонить дальше, сервер — тоже). subtypes —
            # включённые типы итога по порядку: есть хоть один — телефон просит выбрать тип.
            "outcomes": [{"id": o["id"], "name": o["name"], "color": o["color"], "requeue": o["requeue"],
                          "subtypes": [{"id": s["id"], "name": s["name"]} for s in o["subtypes"]]}
                         for o in outcomes],
            "pending_outcome": pending_outcome,
            "script_version": script_version,
        }

    # ------------------------------------------------------------ скрипт разговора
    def _script_rows(self, cur, department_id, active_only=False):
        cur.execute("""
            SELECT body, version, updated_at FROM dial_list_scripts WHERE department_id = %s
        """, (int(department_id),))
        s = cur.fetchone()
        cur.execute("""
            SELECT id, position, question, answer, is_active
            FROM dial_list_script_questions
            WHERE department_id = %s {active}
            ORDER BY is_active DESC, position, created_at
        """.format(active="AND is_active" if active_only else ""), (int(department_id),))
        questions = [{"id": _sid(r[0]), "position": int(r[1]), "question": r[2], "answer": r[3] or "",
                      "is_active": bool(r[4])} for r in cur.fetchall()]
        return {
            "body": (s[0] if s else "") or "",
            "version": int(s[1]) if s else 0,
            "updated_at": _iso(s[2]) if s else None,
            "questions": questions,
        }

    def script(self, department_id):
        """Скрипт отдела для редактора руководителя (включая выключенные вопросы)."""
        with self.db._get_cursor() as cur:
            return self._script_rows(cur, department_id)

    def save_script(self, department_id, payload, changed_by=None):
        """Полный скрипт из редактора: текст + список вопросов (с id — обновить, без id —
        добавить, отсутствующие — выключить, не удалить). Версия растёт при каждом
        сохранении — телефон перечитывает скрипт по ней."""
        department_id = int(department_id)
        if not isinstance(payload, dict):
            raise DialListError("Ожидается объект {body, questions}")
        body = str(payload.get("body") or "").replace("\r\n", "\n").strip()
        if len(body) > SCRIPT_BODY_MAX:
            raise DialListError(f"Текст скрипта не длиннее {SCRIPT_BODY_MAX} символов")
        items = payload.get("questions")
        if items is None:
            items = []
        if not isinstance(items, list):
            raise DialListError("questions: ожидается список вопросов")
        if len(items) > SCRIPT_QUESTIONS_MAX:
            raise DialListError(f"Вопросов не больше {SCRIPT_QUESTIONS_MAX}")
        cleaned = []
        for raw in items:
            if not isinstance(raw, dict):
                raise DialListError("Каждый вопрос — объект {id?, question, answer, is_active}")
            question = str(raw.get("question") or "").strip()[:SCRIPT_QUESTION_MAX]
            answer = str(raw.get("answer") or "").replace("\r\n", "\n").strip()
            if not question:
                raise DialListError("У вопроса должен быть текст")
            if len(answer) > SCRIPT_ANSWER_MAX:
                raise DialListError(f"Ответ «{question[:40]}» длиннее {SCRIPT_ANSWER_MAX} символов")
            cleaned.append({"id": _sid(raw.get("id")) or None, "question": question, "answer": answer,
                            "is_active": raw.get("is_active", True) is not False})
        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_scripts (department_id, body, version, updated_by, updated_at)
                VALUES (%s, %s, 1, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (department_id) DO UPDATE
                SET body = EXCLUDED.body, version = dial_list_scripts.version + 1,
                    updated_by = EXCLUDED.updated_by, updated_at = CURRENT_TIMESTAMP
            """, (department_id, body, changed_by))
            cur.execute("SELECT id FROM dial_list_script_questions WHERE department_id = %s", (department_id,))
            existing = {str(r[0]) for r in cur.fetchall()}
            kept = set()
            for position, q in enumerate(cleaned, start=1):
                if q["id"] and q["id"] in existing:
                    cur.execute("""
                        UPDATE dial_list_script_questions
                        SET question = %s, answer = %s, position = %s, is_active = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND department_id = %s
                    """, (q["question"], q["answer"], position, q["is_active"], q["id"], department_id))
                    kept.add(q["id"])
                else:
                    cur.execute("""
                        INSERT INTO dial_list_script_questions (department_id, position, question, answer, is_active)
                        VALUES (%s, %s, %s, %s, %s) RETURNING id
                    """, (department_id, position, q["question"], q["answer"], q["is_active"]))
                    kept.add(str(cur.fetchone()[0]))
            gone = existing - kept
            if gone:
                cur.execute("""
                    UPDATE dial_list_script_questions SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE department_id = %s AND id = ANY(%s::uuid[])
                """, (department_id, list(gone)))
            result = self._script_rows(cur, department_id)
        log.info("dial_list: скрипт отдела %s сохранён (версия %s, вопросов %d, пользователь %s)",
                 department_id, result["version"], len(cleaned), changed_by)
        return result

    def operator_script(self, user_id):
        """Скрипт для телефона: текст и только включённые вопросы. Без ФИО и номеров."""
        ctx = self.operator_context(user_id)
        with self.db._get_cursor() as cur:
            data = self._script_rows(cur, ctx["department_id"], active_only=True)
        return {"body": data["body"], "version": data["version"], "updated_at": data["updated_at"],
                "questions": [{"id": q["id"], "question": q["question"], "answer": q["answer"]}
                              for q in data["questions"]]}

    def script_ai(self, department_id, payload):
        """ИИ в редакторе скрипта: mode=generate — скрипт и вопросы по описанию кампании
        (brief); mode=polish — оформить текст (text; target=body|answer) по нашей разметке.
        Ничего не сохраняет: результат руководитель смотрит и применяет сам."""
        from . import ai as script_ai
        payload = payload if isinstance(payload, dict) else {}
        mode = str(payload.get("mode") or "").strip().lower()
        try:
            if mode == "generate":
                result = script_ai.generate_script(payload.get("brief"))
                log.info("dial_list: ИИ собрал скрипт для отдела %s (%d симв., вопросов %d)",
                         department_id, len(result["body"]), len(result["questions"]))
                return {"mode": mode, **result}
            if mode == "polish":
                what = "ответ оператора на вопрос клиента" if payload.get("target") == "answer" else "скрипт разговора"
                text = script_ai.polish_text(payload.get("text"), what=what)
                log.info("dial_list: ИИ оформил %s для отдела %s (%d симв.)", what, department_id, len(text))
                return {"mode": mode, "text": text}
        except script_ai.ScriptAIError as exc:
            raise DialListError(str(exc), exc.status)
        raise DialListError("mode: generate или polish")

    # Счётчики оператора для вкладки «Мой прогресс»: за месяц (первый день — %(month)s)
    # и за сегодня (%(today)s), дни — по Алматы. Отменённые оператором попытки в
    # попытки не входят (они не в счёт). Только числа: ни ФИО, ни номеров. Работа по
    # лиду с номером «Реестра тестовых номеров» — проверка обзвона, не показатель.
    # talk — разговор ли попытка при ответе Binotel (ATTEMPT_TALK_SQL): сообщение сети,
    # которое положил телефон, ни в «дозвонились», ни в минуты разговора не идёт.
    _PROGRESS_SQL = """
        WITH att AS (
            SELECT t.state, UPPER(t.disposition) AS disp, t.billsec, t.cancelled,
                   (t.requested_at AT TIME ZONE 'Asia/Almaty')::date AS day,
                   (""" + ATTEMPT_TALK_SQL + """) AS talk
            FROM dial_list_attempts t
            WHERE t.operator_id = %(operator_id)s
              AND t.requested_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
              AND """ + test_keys.DIAL_LIST_ATTEMPT_NOT_TEST_SQL + """
        ), asg AS (
            SELECT a.state,
                   (a.created_at AT TIME ZONE 'Asia/Almaty')::date AS day,
                   (a.done_at AT TIME ZONE 'Asia/Almaty')::date AS done_day
            FROM dial_list_assignments a
            WHERE a.operator_id = %(operator_id)s
              AND a.created_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
              AND """ + test_keys.DIAL_LIST_ASSIGNMENT_NOT_TEST_SQL + """
        )
        SELECT
            (SELECT COUNT(*) FROM asg)                                                    AS issued,
            (SELECT COUNT(*) FROM asg WHERE state = 'done')                               AS done,
            (SELECT COUNT(*) FROM asg WHERE state = 'done' AND done_day = %(today)s)      AS done_today,
            (SELECT COUNT(*) FROM att WHERE NOT cancelled)                                AS attempts,
            (SELECT COUNT(*) FROM att WHERE NOT cancelled AND day = %(today)s)            AS attempts_today,
            (SELECT COUNT(*) FROM att WHERE state = 'finished' AND disp IN %(answered)s
                                        AND talk)                                         AS answered,
            (SELECT COUNT(*) FROM att WHERE state = 'finished' AND disp IN %(answered)s
                                        AND talk AND day = %(today)s)                     AS answered_today,
            (SELECT COALESCE(SUM(billsec), 0) FROM att WHERE state = 'finished' AND talk) AS talk_sec,
            (SELECT COALESCE(SUM(billsec), 0) FROM att WHERE state = 'finished' AND talk
                                                          AND day = %(today)s)            AS talk_sec_today,
            (SELECT COUNT(*) FROM att WHERE cancelled)                                    AS cancelled
    """
    _PROGRESS_OUTCOMES_SQL = """
        SELECT o.id, o.name, o.color, COUNT(*) AS cnt,
               COUNT(*) FILTER (WHERE (t.requested_at AT TIME ZONE 'Asia/Almaty')::date = %(today)s) AS cnt_today
        FROM dial_list_attempts t
        JOIN dial_list_outcomes o ON o.id = t.outcome_id
        WHERE t.operator_id = %(operator_id)s
          AND t.requested_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
          AND """ + test_keys.DIAL_LIST_ATTEMPT_NOT_TEST_SQL + """
        GROUP BY o.id, o.name, o.color, o.position
        ORDER BY cnt DESC, o.position, o.name
    """
    # Типы итогов (29.09.2026) — по тем же попыткам, что и итоги выше; попытки без
    # типа не показываются (строки «без типа» на телефоне нет).
    _PROGRESS_SUBTYPES_SQL = """
        SELECT t.outcome_id, s.id, s.name, COUNT(*) AS cnt,
               COUNT(*) FILTER (WHERE (t.requested_at AT TIME ZONE 'Asia/Almaty')::date = %(today)s) AS cnt_today
        FROM dial_list_attempts t
        JOIN dial_list_outcomes o ON o.id = t.outcome_id
        JOIN dial_list_outcome_subtypes s ON s.id = t.outcome_subtype_id
        WHERE t.operator_id = %(operator_id)s
          AND t.requested_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
          AND """ + test_keys.DIAL_LIST_ATTEMPT_NOT_TEST_SQL + """
        GROUP BY t.outcome_id, s.id, s.name
        ORDER BY cnt DESC, s.name
    """
    # Конверсия оператора (владелец, 11.10.2026: показать в «Моём прогрессе») — ровно то
    # же, что у руководителя в «Аналитике» по операторам (analytics._OPERATORS_SQL): база
    # обзваниваемого месяца отдела; поговорившие — водители, с которыми ЭТОТ оператор
    # говорил (попытка завершена, водитель ответил, не короче SUCCESS_MIN_BILLSEC, и это не
    # сообщение сети — автоответчик, плечо на котором положил телефон); успешки —
    # подписавшие, засчитанные ему. Только счётчики: номер лида читает лишь условие
    # «Реестра тестовых номеров», как и в аналитике.
    _PROGRESS_CONVERSION_SQL = """
        SELECT
            (SELECT COUNT(DISTINCT a.lead_id)
             FROM dial_list_attempts t
             JOIN dial_list_assignments a ON a.id = t.assignment_id
             JOIN dial_list_leads l ON l.id = a.lead_id
             WHERE t.operator_id = %(operator_id)s
               AND l.department_id = %(department_id)s AND l.period = %(period)s
               AND t.state = 'finished' AND UPPER(t.disposition) IN %(answered)s
               AND t.auto_hangup <> 'announcement'
               AND t.billsec >= %(min_sec)s
               AND """ + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL + """) AS leads_talked,
            (SELECT COUNT(*)
             FROM dial_list_leads l
             WHERE l.success_operator_id = %(operator_id)s AND l.success_attempt_id IS NOT NULL
               AND l.department_id = %(department_id)s AND l.period = %(period)s
               AND """ + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL + """) AS successes
    """

    def _operator_conversion(self, ctx):
        """Конверсия оператора за базу обзваниваемого месяца (_PROGRESS_CONVERSION_SQL)."""
        period = (ctx.get("settings") or {}).get("_period") or current_period()
        params = {"operator_id": ctx["user_id"], "department_id": ctx["department_id"], "period": period,
                  "answered": ANSWERED_SQL, "min_sec": signing.SUCCESS_MIN_BILLSEC}
        with self.db._get_cursor() as cur:
            cur.execute(self._PROGRESS_CONVERSION_SQL, params)
            r = cur.fetchone() or (0, 0)
        talked, successes = int(r[0] or 0), int(r[1] or 0)
        return {
            "successes": successes,
            "leads_talked": talked,
            # Как _pct аналитики: доля с одним знаком; не с кем было говорить — пусто.
            "percent": round(100.0 * successes / talked, 1) if talked else None,
            "period": period.isoformat(),
            "period_label": period_label(period),
        }

    def operator_progress(self, user_id):
        """«Мой прогресс» на телефоне: сколько строк выдано и обработано, попыток,
        дозвонов, минут разговора и какие итоги ставил оператор — за текущий месяц
        (по Алматы) и отдельно за сегодня. Никаких ФИО и номеров — только счётчики.

        conversion — конверсия за базу обзваниваемого месяца, как в «Аналитике»
        руководителя (_operator_conversion). Не посчиталась — ключа нет, остальное
        приходит как всегда."""
        ctx = self.operator_context(user_id)
        month = current_period()
        today = datetime.now(PERIOD_TZ).date()
        params = {"operator_id": ctx["user_id"], "month": month, "today": today, "answered": ANSWERED_SQL}
        with self.db._get_cursor() as cur:
            cur.execute(self._PROGRESS_SQL, params)
            r = cur.fetchone() or (0,) * 10
            cur.execute(self._PROGRESS_OUTCOMES_SQL, params)
            outcomes = [{"id": _sid(o[0]), "name": o[1], "color": o[2] or "#8E8E93",
                         "count": int(o[3] or 0), "count_today": int(o[4] or 0), "subtypes": []}
                        for o in cur.fetchall()]
            cur.execute(self._PROGRESS_SUBTYPES_SQL, params)
            by_id = {o["id"]: o for o in outcomes}
            for s in cur.fetchall():
                parent = by_id.get(_sid(s[0]))
                if parent is not None:
                    parent["subtypes"].append({"id": _sid(s[1]), "name": s[2], "count": int(s[3] or 0),
                                               "count_today": int(s[4] or 0)})
        progress = {
            "operator": {"id": ctx["user_id"], "name": ctx["name"]},
            "period": month.isoformat(),
            "period_label": period_label(month),
            "today": today.isoformat(),
            "month": {
                "issued": int(r[0] or 0), "done": int(r[1] or 0), "attempts": int(r[3] or 0),
                "answered": int(r[5] or 0), "talk_sec": int(r[7] or 0), "cancelled": int(r[9] or 0),
            },
            "today_stats": {
                "done": int(r[2] or 0), "attempts": int(r[4] or 0),
                "answered": int(r[6] or 0), "talk_sec": int(r[8] or 0),
            },
            "outcomes": outcomes,
        }
        # Своей транзакцией: сбой конверсии не трогает счётчики выше.
        try:
            progress["conversion"] = self._operator_conversion(ctx)
        except Exception as exc:
            log.warning("dial_list: конверсия оператора %s не посчитана: %s", ctx["user_id"], exc)
        return progress

    # «Позвонить ещё раз» (владелец, 12.10.2026; до того — «Перезвонить» раньше срока): всё,
    # что решает recall_verdict, — одним куском SQL и для ручки recall (_RECALL_LEAD_SQL), и
    # для строк вкладок итогов (_WORKED_SQL): кнопка не врёт. Чей водитель — по последней
    # попытке любого рода, кроме отменённых оператором до ответа (они не в счёт и строке, и
    # лиду): с итогом, недозвон, сбой АТС — всё это звонок. Её оператор, «перезвонить» ли её
    # итог и звонил ли после неё ДРУГОЙ оператор. До 12.10.2026 считалась только попытка с
    # итогом. Лид — под алиасом l, оператор — параметр %(operator_id)s.
    _RECALL_JOINS_SQL = """
        LEFT JOIN LATERAL (
            SELECT rt.operator_id AS owner_id, COALESCE(ro.requeue, FALSE) AS owner_requeue,
                   EXISTS (SELECT 1 FROM dial_list_attempts rt2
                           JOIN dial_list_assignments ra2 ON ra2.id = rt2.assignment_id
                           WHERE ra2.lead_id = l.id AND rt2.operator_id <> %(operator_id)s
                             AND rt2.requested_at > rt.requested_at) AS taken_after
            FROM dial_list_attempts rt
            JOIN dial_list_assignments ra ON ra.id = rt.assignment_id
            LEFT JOIN dial_list_outcomes ro ON ro.id = rt.outcome_id
            WHERE ra.lead_id = l.id AND NOT rt.cancelled
            ORDER BY rt.requested_at DESC LIMIT 1
        ) rc ON TRUE
        LEFT JOIN LATERAL (
            SELECT rx.id AS issued_id, rx.operator_id AS issued_to
            FROM dial_list_assignments rx
            WHERE rx.lead_id = l.id AND rx.state = 'issued'
            ORDER BY rx.created_at DESC LIMIT 1
        ) ri ON TRUE
    """
    # Колонки к нему — в том порядке, в каком их разбирает recall_verdict.
    _RECALL_COLUMNS = """(l.signed_at IS NOT NULL OR l.sign_status = 'processing'), l.status = 'excluded',
               ri.issued_id, ri.issued_to, rc.owner_id, rc.owner_requeue, rc.taken_after"""
    _RECALL_LEAD_SQL = """
        SELECT l.id, """ + _RECALL_COLUMNS + """
        FROM dial_list_leads l
        """ + _RECALL_JOINS_SQL + """
        WHERE l.id = %(lead_id)s AND l.department_id = %(department_id)s
        FOR UPDATE OF l
    """

    # Отработанные за месяц: по каждому водителю — ПОСЛЕДНЯЯ строка этого оператора
    # (пере-выданный водитель уходит туда, куда его отнесла последняя обработка) и её
    # последняя попытка. Месяц — строки, выданные или обработанные с 1-го числа, плюс
    # открытые: пачка живёт до «Ещё», и выданная 30-го, а обработанная 1-го строка иначе
    # ушла бы из очереди телефона, не появившись ни на одной вкладке.
    # Колонки читаются по номерам (operator_worked) — новые только в конец: r[16] — подписал
    # или на проверке у Яндекса, r[17] — «перезвонить» ли итог строки, r[18:25] —
    # _RECALL_COLUMNS («Позвонить ещё раз» и кнопка «Перезвонить» старого телефона), r[25] —
    # почему плечо последней попытки строки положил сам аппарат (auto_hangup), r[26:29] —
    # _LAST_COMMENT_COLUMNS (последний комментарий оператора по водителю), r[29] — отдел базы
    # водителя (из другого отдела «Позвонить ещё раз» нельзя).
    _WORKED_SQL = """
        WITH latest AS (
            SELECT DISTINCT ON (a.lead_id)
                   a.id, a.lead_id, a.state, a.result, a.done_at, a.created_at
            FROM dial_list_assignments a
            WHERE a.operator_id = %(operator_id)s
              AND (a.created_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
                   OR a.done_at >= (%(month)s::date)::timestamp AT TIME ZONE 'Asia/Almaty'
                   OR a.state = 'issued')
            ORDER BY a.lead_id, a.created_at DESC
        )
        SELECT s.id, s.lead_id, l.full_name, s.state, s.result, s.done_at, s.created_at,
               t.outcome_id, t.outcome_at, t.operator_comment, t.state,
               o.name, o.color, o.position, l.status, st.name,
               (l.signed_at IS NOT NULL OR l.sign_status = 'processing'),
               COALESCE(o.requeue, FALSE), """ + _RECALL_COLUMNS + """,
               t.auto_hangup, """ + _LAST_COMMENT_COLUMNS + """, l.department_id
        FROM latest s
        JOIN dial_list_leads l ON l.id = s.lead_id
        LEFT JOIN LATERAL (
            SELECT outcome_id, outcome_at, operator_comment, state, outcome_subtype_id, auto_hangup
            FROM dial_list_attempts WHERE assignment_id = s.id
            ORDER BY requested_at DESC LIMIT 1
        ) t ON TRUE
        LEFT JOIN dial_list_outcomes o ON o.id = t.outcome_id
        LEFT JOIN dial_list_outcome_subtypes st ON st.id = t.outcome_subtype_id
    """ + _RECALL_JOINS_SQL + _LAST_COMMENT_JOIN_SQL.format(lead="s.lead_id", operator="%(operator_id)s")

    def operator_worked(self, user_id, q=""):
        """Вкладки итогов на телефоне: кого оператор отработал за месяц и с каким итогом.

        Вкладка на каждый включённый итог отдела (в порядке справочника, даже пустая —
        набор вкладок не прыгает), выключенный — только если по нему кто-то есть, и
        последней «Не дозвонились». Только ФИО, время и свой комментарий — номера нет.

        can_call_again (12.10.2026) — можно ли нажать «Позвонить ещё раз» на любой вкладке:
        водитель свой (последним ему звонил этот оператор) и свободен — recall_verdict, как
        у ручки recall. Нельзя — call_again_block: почему, тем же текстом, что ответит
        ручка. signed — подписал документы или они на проверке у Яндекса. can_recall
        (11.10.2026, телефон 3.22.33) — прежняя кнопка «Перезвонить»: то же решение, но
        только у строки с итогом «перезвонить».

        q — поиск по ФИО (search_key, name_matches), не по номеру и не по ИИН: отбор — до
        подсчёта вкладок и до WORKED_TAB_LIMIT, поэтому count — сколько нашлось, а набор
        вкладок тот же, что без поиска. В ответе q — запрос после нормализации.

        last_comment, last_comment_label, last_comment_outcome (12.10.2026) — последний
        непустой комментарий этого оператора по водителю, из любой его строки: на вкладке
        «Не дозвонились» он виден тоже. comment — как был: комментарий последней попытки
        строки. auto_hangup — почему плечо последней попытки строки положил сам аппарат
        ('announcement' — сообщение сети, подпись «Абонент недоступен»).
        """
        ctx = self.operator_context(user_id)
        query = search_key(str(q or "")[:WORKED_QUERY_MAX])
        month = current_period()
        today = _today()
        with self.db._get_cursor() as cur:
            outcomes = self._outcome_rows(cur, ctx["department_id"], active_only=True)
            cur.execute(self._WORKED_SQL, {"operator_id": ctx["user_id"], "month": month})
            rows = cur.fetchall()
        tabs = {o["id"]: {"key": o["id"], "name": o["name"], "color": o["color"], "position": o["position"],
                          "count": 0} for o in outcomes}
        extra = {}
        items = []
        for r in rows:
            tab = worked_tab(r[3], r[4], r[7], r[10])
            if tab is None:
                continue
            if tab == WORKED_NO_ANSWER and r[4] == "other" and (r[14] == "excluded" or r[16]):
                # Строку закрыл руководитель («Исключить») или сервер, увидев подпись
                # документов (sign_check, звонок подписавшему), а не АТС: это не недозвон.
                continue
            if tab != WORKED_NO_ANSWER and tab not in tabs:
                # Итог выключили уже после звонка — вкладка живёт, пока по ней кто-то есть.
                extra.setdefault(tab, {"key": tab, "name": r[11] or "Итог", "color": r[12] or WORKED_NO_ANSWER_COLOR,
                                       "position": int(r[13] or 0), "count": 0})
            full_name = r[2] or "Без имени"
            if query and not name_matches(full_name, query):
                # Вкладка выключенного итога уже заведена выше: поиск набор вкладок не меняет.
                continue
            at = (r[8] or r[5] or r[6]) if tab != WORKED_NO_ANSWER else (r[5] or r[6])
            verdict = recall_verdict(r[18:25], ctx["user_id"])
            can_call_again = verdict in (RECALL_OK, RECALL_ISSUED_MINE)
            block = "" if can_call_again else RECALL_REFUSALS[verdict]
            # Водитель из базы отдела, где оператор работал раньше (перевели, сняли с линии другого
            # отдела): на вкладке он остаётся, а позвонить ему нельзя — ручка recall ищет водителя в
            # базе нынешнего отдела и ответила бы «не найден» (13.10.2026).
            foreign = len(r) > 29 and r[29] is not None and int(r[29]) != int(ctx["department_id"])
            if foreign:
                can_call_again, block = False, RECALL_OTHER_DEPARTMENT
            items.append({
                "assignment_id": str(r[0]), "lead_id": str(r[1]),
                "full_name": full_name,
                "tab": tab,
                "result": r[4] or "",
                "comment": (r[9] or "") if tab != WORKED_NO_ANSWER else "",
                # Тип итога (29.09.2026): вкладка по-прежнему одна на итог, тип — в строке.
                "subtype_name": (r[15] or "") if tab != WORKED_NO_ANSWER else "",
                "at": _iso(at), "at_label": at_label(at, today), "_sort": at,
                "can_recall": not foreign and bool(r[17]) and verdict == RECALL_OK,
                "can_call_again": can_call_again,
                "call_again_block": block,
                "signed": bool(r[16]),
                # Плечо последней попытки положил сам аппарат: на гудках или на сообщении сети.
                "auto_hangup": r[25] or "",
                # Что оператор записал по водителю в прошлый раз — на любой вкладке.
                **self._last_comment_fields(r[26], r[27], r[28], today),
            })
        no_answer = {"key": WORKED_NO_ANSWER, "name": WORKED_NO_ANSWER_NAME, "color": WORKED_NO_ANSWER_COLOR,
                     "count": 0}
        ordered = list(tabs.values()) + sorted(extra.values(), key=lambda t: (t["position"], t["name"])) + [no_answer]
        by_key = {t["key"]: t for t in ordered}
        # Свежие сверху: только что отработанный водитель — первая строка своей вкладки.
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        items.sort(key=lambda i: i["_sort"] or oldest, reverse=True)
        shown = []
        for item in items:
            del item["_sort"]
            tab = by_key[item["tab"]]
            tab["count"] += 1
            if tab["count"] <= WORKED_TAB_LIMIT:
                shown.append(item)
        return {
            "period": month.isoformat(),
            "period_label": period_label(month),
            "limit": WORKED_TAB_LIMIT,
            "q": query,
            "tabs": [{"key": t["key"], "name": t["name"], "color": t["color"], "count": t["count"]} for t in ordered],
            "items": shown,
        }

    @staticmethod
    def _row_settling(item):
        """Строка пачки (_portion_items), по которой плечо уже кончилось, а исход от АТС ещё не
        пришёл: последняя попытка — ended. Не держит ни «Позвонить», ни «Ещё» (13.10.2026)."""
        last = item.get("last_attempt") or {}
        return item.get("state") == "issued" and last.get("state") == "ended"

    def _refuse_next_portion(self, cur, ctx):
        """Почему «Ещё» нельзя: в открытой выдаче остались строки или итог прошлого
        разговора не указан — 409. Выдачи не меняет; возвращает открытую выдачу (уже без
        необработанных строк) или None.

        Строка, плечо по которой кончилось, а исход от АТС ещё едет (последняя попытка ended),
        в «осталось» не считается (13.10.2026): её закроет исход, а «Ещё» перенесёт её в новую
        выдачу (issue_next_portion). Итог, если он по ней окажется нужен, потребует следующий
        звонок."""
        portion = self._open_portion(cur, ctx["user_id"])
        if portion:
            cur.execute("""
                SELECT COUNT(*) FROM dial_list_assignments a
                WHERE portion_id = %s AND state = 'issued'
                  AND COALESCE((SELECT t.state FROM dial_list_attempts t WHERE t.assignment_id = a.id
                                ORDER BY t.requested_at DESC LIMIT 1), '') <> 'ended'
            """, (portion["id"],))
            pending = int(cur.fetchone()[0])
            if pending:
                raise DialListError(
                    f"Сначала обработайте текущий список: осталось {pending}", 409)
        self._require_outcome_done(cur, ctx)
        return portion

    def _precheck_signatures(self, ctx):
        """Перед «Ещё» — свежая проверка подписания тех, кто попадёт в порцию, и запаса
        им на замену (SIGN_PRECHECK_*). Вне транзакций: Sapar отвечает по сети.

        Круг: кандидаты пула без блокировки → устаревшие (sign_check_due) параллельно в
        Sapar (SignChecker.check_now: итог в базу, успешки по новым подписавшим) →
        подписавшие и «на проверке у Яндекса» выпали из пула, их место заняли следующие —
        их проверит следующий круг. Хватит, когда первые portion_size кандидатов свежие.
        Возвращает сводку — она же одна строка журнала сервера."""
        settings = ctx["settings"]
        size = int(settings["portion_size"])
        params = (ctx["department_id"], settings["_period"], settings["retry_after_hours"],
                  settings["max_attempts"], size + max(5, size // 2))
        checker = self.sign_checker
        totals = {"rounds": 0, "checked": 0, "signed": 0, "processing": 0, "errors": 0, "systemic": 0,
                  "stopped": "" if checker.configured() else "not_configured"}
        started = time.monotonic()
        # Кого уже спрашивали в этот раз: отказ Sapar по ИИН или сбой — не повод спрашивать снова.
        asked = set()
        while not totals["stopped"] and totals["rounds"] < SIGN_PRECHECK_ROUNDS:
            left = SIGN_PRECHECK_BUDGET_SEC - (time.monotonic() - started)
            if left <= 0:
                totals["stopped"] = "budget"
                break
            now = _utcnow()
            with self.db._get_cursor() as cur:
                cur.execute(self._POOL_CANDIDATES_SQL, params)
                rows = cur.fetchall()
            due = [r for r in rows if str(r[0]) not in asked
                   and sign_check_due(r[1], r[3], r[4], now, SIGN_PRECHECK_FRESH_MINUTES)]
            head = {str(r[0]) for r in rows[:size]}
            if not any(str(r[0]) in head for r in due):
                break
            asked.update(str(r[0]) for r in due)
            totals["rounds"] += 1
            result = checker.check_now([(r[0], r[1], r[2]) for r in due], now=now,
                                       timeout=SIGN_PRECHECK_TIMEOUT_SEC, workers=SIGN_PRECHECK_WORKERS,
                                       budget_sec=left,
                                       max_systemic=SIGN_PRECHECK_MAX_SYSTEMIC - totals["systemic"])
            for key in ("checked", "errors", "systemic"):
                totals[key] += result[key]
            totals["signed"] += len(result["signed"])
            totals["processing"] += len(result["processing"])
            totals["stopped"] = result["stopped"]
            if not result["signed"] and not result["processing"]:
                # Из пула никто не выпал — голова та же, и спрашивать в ней больше некого.
                break
        totals["elapsed_sec"] = round(time.monotonic() - started, 1)
        log.info("dial_list: подписание перед выдачей оператору %s: проверено %d, подписали %d, на проверке "
                 "у Яндекса %d, ошибок %d, %.1f с%s", ctx["user_id"], totals["checked"], totals["signed"],
                 totals["processing"], totals["errors"], totals["elapsed_sec"],
                 f"; {SIGN_NOW_STOPPED.get(totals['stopped'], totals['stopped'])} — выдаём как есть"
                 if totals["stopped"] else "")
        return totals

    def issue_next_portion(self, user_id, phone_version=None):
        """«Ещё»: следующая порция из пула.

        Отказы — сначала, одним чтением: «Ещё», которому откажут, не ждёт Sapar. Затем,
        вне транзакций, проверка подписания тех, кто попадёт в порцию
        (_precheck_signatures): подписавшие и «на проверке у Яндекса» уходят из пула, и
        порцию добирают следующие. Затем выдача — под замком оператора (_lock_operator)
        и с повторной проверкой отказов: за секунды проверки мог прийти второй «Ещё».

        Ответ — состояние; phone_version — как у get_state: телефон читает из него
        ring_timeout_sec так же, как из обычного состояния.

        Замки — в том же порядке, что у закрытия попытки (_finish_attempt: строка выдачи, лид,
        выдача; ревью 13.10.2026): строки, которые переезжают в новую выдачу, запираются ДО
        закрытия старой выдачи. Наоборот (выдача, потом строка) «Ещё» и финал той же строки из
        опроса запирали одно и то же навстречу друг другу, и база обрывала одного взаимной
        блокировкой (40P01): телефону — 500 на «Ещё» или на опрос состояния. На случай иной
        такой встречи транзакция выдачи повторяется один раз."""
        ctx = self.operator_context(user_id)
        settings = ctx["settings"]
        with self.db._get_cursor() as cur:
            self._refuse_next_portion(cur, ctx)
        try:
            self._precheck_signatures(ctx)
        except Exception:
            # Проверка — страховка, а не условие выдачи: её сбой не оставит оператора без списка.
            log.exception("dial_list: проверка подписания перед выдачей оператору %s упала — выдаём без неё",
                          ctx["user_id"])
        for round_no in (1, 2):
            try:
                with self.db._get_cursor() as cur:
                    self._lock_operator(cur, ctx["user_id"])
                    portion = self._refuse_next_portion(cur, ctx)
                    carried = []
                    if portion:
                        # Необработанными в ней могли остаться только строки, исход которых ещё едет от
                        # АТС (_refuse_next_portion их не считает). Они переезжают в новую выдачу: исход
                        # закроет их там, а отбой до ответа водителя (строка остаётся в работе) не
                        # оставит строку в закрытой выдаче, которую телефон уже не показывает. FOR
                        # UPDATE — до закрытия выдачи (порядок замков — выше); строку, которую финал
                        # закрыл, пока мы ждали её замка, база из ответа исключит сама.
                        cur.execute("""
                            SELECT a.id FROM dial_list_assignments a
                            WHERE a.portion_id = %s AND a.state = 'issued'
                            ORDER BY a.position
                            FOR UPDATE
                        """, (portion["id"],))
                        carried = [str(r[0]) for r in cur.fetchall()]
                        # Все строки закрыты (или едут дальше), а выдача открыта — закрываем здесь.
                        cur.execute("UPDATE dial_list_portions SET closed_at = CURRENT_TIMESTAMP WHERE id = %s",
                                    (portion["id"],))
                    cur.execute(self._POOL_SQL.format(lock="FOR UPDATE SKIP LOCKED"), (
                        ctx["department_id"], settings["_period"], settings["retry_after_hours"],
                        settings["max_attempts"], settings["portion_size"]))
                    lead_ids = [str(r[0]) for r in cur.fetchall()]
                    if not lead_ids:
                        raise DialListError("Список пуст: водителей для обзвона сейчас нет", 404)
                    cur.execute("""
                        INSERT INTO dial_list_portions (operator_id, department_id, size)
                        VALUES (%s, %s, %s) RETURNING id
                    """, (ctx["user_id"], ctx["department_id"], len(lead_ids) + len(carried)))
                    portion_id = str(cur.fetchone()[0])
                    for position, lead_id in enumerate(lead_ids, start=1):
                        cur.execute("""
                            INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position)
                            VALUES (%s, %s, %s, %s)
                        """, (portion_id, lead_id, ctx["user_id"], position))
                    for position, assignment_id in enumerate(carried, start=len(lead_ids) + 1):
                        cur.execute("UPDATE dial_list_assignments SET portion_id = %s, position = %s WHERE id = %s",
                                    (portion_id, position, assignment_id))
                break
            except psycopg2.errors.DeadlockDetected:
                if round_no == 2:
                    raise
                log.warning("dial_list: выдача оператору %s встретила взаимную блокировку — повторяем",
                            ctx["user_id"])
        log.info("dial_list: оператору %s выдано %d строк (порция %s)%s", ctx["user_id"], len(lead_ids), portion_id,
                 f", из прошлой выдачи переехало {len(carried)} — ждут исхода от АТС" if carried else "")
        return self.get_state(user_id, phone_version=phone_version)

    def recall_lead(self, user_id, lead_id, phone_version=None):
        """«Позвонить ещё раз» (владелец, 12.10.2026; с 11.10.2026 это был «Перезвонить»
        раньше срока — только по итогу «Перезвонить»): свой водитель снова в списке
        оператора, телефон тут же ему звонит. Свой — последним ему звонил этот оператор,
        с любым итогом или без него (недозвон, сбой АТС).

        Кому можно — recall_verdict; то же решение получают строки вкладок итогов
        (can_call_again, can_recall). Водитель и так в списке этого оператора — отдаём его
        строку (прошлый звонок мог упереться в отказ АТС). Иначе строка встаёт в последнюю
        выдачу оператора (_recall_row). Статус и попытки лида не трогаем: из пула его
        убирает сама открытая строка, а обработанный водитель после недозвона так и
        останется обработанным (_touch_lead). Одна транзакция, без сети. Ответ —
        состояние телефона (phone_version — как у get_state) и assignment_id строки, по
        которой звонить."""
        ctx = self.operator_context(user_id)
        try:
            lead_id = str(uuid.UUID(str(lead_id)))
        except (ValueError, AttributeError, TypeError):
            raise DialListError("Водитель не найден", 404)
        params = {"lead_id": lead_id, "department_id": ctx["department_id"], "operator_id": ctx["user_id"]}
        how = "уже был в списке"
        try:
            with self.db._get_cursor() as cur:
                self._lock_operator(cur, ctx["user_id"])
                cur.execute(self._RECALL_LEAD_SQL, params)
                row = cur.fetchone()
                if not row:
                    raise DialListError("Водитель не найден", 404)
                verdict = recall_verdict(row[1:8], ctx["user_id"])
                if verdict in ("signed", "excluded"):
                    raise DialListError(RECALL_REFUSALS[verdict], 409)
                active = self._active_attempt(cur, ctx["user_id"])
                if active:
                    raise DialListError(
                        f"Идёт звонок ({active['full_name']}). Дождитесь его завершения", 409)
                self._require_outcome_done(cur, ctx)
                if verdict == RECALL_ISSUED_MINE:
                    assignment_id = str(row[3])
                elif verdict != RECALL_OK:
                    raise DialListError(RECALL_REFUSALS[verdict], 409)
                else:
                    assignment_id, how = self._recall_row(cur, ctx, lead_id)
        except psycopg2.errors.UniqueViolation:
            # Водителя только что выдали другому оператору: открытая строка у лида одна.
            raise DialListError(RECALL_REFUSALS["issued_other"], 409)
        # row[6] — «перезвонить» ли итог последней попытки: в журнале видно, звонит ли
        # оператор по своему «Перезвонить» раньше срока или просто ещё раз.
        log.info("dial_list: оператор %s звонит водителю %s %s — строка %s (%s)", ctx["user_id"], lead_id,
                 "по итогу «Перезвонить» раньше срока" if row[6] else "ещё раз", assignment_id, how)
        return {"assignment_id": assignment_id, **self.get_state(user_id, phone_version=phone_version)}

    def _recall_row(self, cur, ctx, lead_id):
        """Строка для «Позвонить ещё раз» — в последней выдаче оператора (_last_portion, её
        и показывает телефон): его обработанная строка этого водителя там — снова issued,
        как при «Вернуть в список» (_reopen_row); нет — новая строка в конец выдачи;
        выдачи нет вовсе — новая выдача из одной строки. Выдача открывается: пока строка
        не обработана, «Ещё» следующую не выдаст. Возвращает (assignment_id, что сделали).

        У переоткрытой строки счётчик попыток — с нуля, как у новой (12.10.2026): прежние
        звонки по ней уже сосчитаны, а со старым счётчиком строку с двумя попытками из трёх
        закрывал неудачей первый же отказ АТС (_fail_attempt). Счётчик лида, аналитика и
        «Мой прогресс» от него не зависят — они считают сами попытки. «Вернуть в список»
        руководителя (_reopen_in_latest_portion) счётчик не трогает."""
        user_id = ctx["user_id"]
        portion = self._last_portion(cur, user_id)
        if not portion:
            cur.execute("""
                INSERT INTO dial_list_portions (operator_id, department_id, size)
                VALUES (%s, %s, 1) RETURNING id
            """, (user_id, ctx["department_id"]))
            portion_id, position, how = str(cur.fetchone()[0]), 1, "новая выдача"
        else:
            portion_id = portion["id"]
            cur.execute("""
                SELECT id, portion_id FROM dial_list_assignments
                WHERE lead_id = %s AND operator_id = %s AND state = 'done'
                ORDER BY created_at DESC LIMIT 1
            """, (lead_id, user_id))
            last = cur.fetchone()
            if last and str(last[1]) == portion_id:
                self._reopen_row(cur, last[0], last[1])
                # Отдельным запросом: _reopen_row — общий с «Вернуть в список» руководителя.
                cur.execute("UPDATE dial_list_assignments SET attempts = 0 WHERE id = %s", (str(last[0]),))
                return str(last[0]), "строка переоткрыта"
            cur.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM dial_list_assignments WHERE portion_id = %s",
                        (portion_id,))
            position, how = int(cur.fetchone()[0]), "строка в конце выдачи"
            cur.execute("UPDATE dial_list_portions SET size = size + 1, closed_at = NULL WHERE id = %s",
                        (portion_id,))
        cur.execute("""
            INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (portion_id, lead_id, user_id, position))
        return str(cur.fetchone()[0]), how

    def _precheck_call_signature(self, ctx, assignment_id):
        """Перед звонком — свежая проверка подписания этого водителя, если прошлая
        старше SIGN_CALL_FRESH_MINUTES или кончилась ошибкой: пачка лежит на экране
        часами. Вне транзакций; подписал — строку снимет транзакция звонка, она читает
        уже записанный итог. Sapar не настроен или лежит — звоним как раньше.
        Возвращает сводку проверки или None, если не спрашивали."""
        checker = self.sign_checker
        if not checker.configured():
            return None
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT l.id, l.iin, l.period, l.sign_checked_at, l.sign_error
                FROM dial_list_assignments a
                JOIN dial_list_leads l ON l.id = a.lead_id
                WHERE a.id = %s AND a.operator_id = %s AND a.state = 'issued'
                  AND l.signed_at IS NULL AND l.sign_status <> 'processing'
            """, (assignment_id, ctx["user_id"]))
            row = cur.fetchone()
        now = _utcnow()
        if not row or not sign_check_due(row[1], row[3], row[4], now, SIGN_CALL_FRESH_MINUTES):
            return None
        summary = checker.check_now([(row[0], row[1], row[2])], now=now, timeout=SIGN_CALL_TIMEOUT_SEC,
                                    workers=1, budget_sec=SIGN_CALL_TIMEOUT_SEC, max_systemic=1)
        if summary["signed"] or summary["processing"] or summary["errors"] or summary["stopped"]:
            verdict = ("подписал" if summary["signed"] else "на проверке у Яндекса" if summary["processing"]
                       else SIGN_NOW_STOPPED.get(summary["stopped"]) or "Sapar отказал")
            log.info("dial_list: подписание перед звонком, строка %s: %s (%.1f с)", assignment_id, verdict,
                     summary["elapsed_sec"])
        return summary

    def start_call(self, user_id, assignment_id):
        """Клик «Позвонить»: попытка + звонок через Binotel. Возвращает попытку.

        Подписавшему (и «на проверке у Яндекса» — водитель уже подписал) не звоним
        (владелец, 11.10.2026): строка снимается со списка, оператору — 409. Проверка
        подписания устарела — сначала спрашиваем Sapar про этого водителя, вне
        транзакций (_precheck_call_signature), а транзакция звонка смотрит на её итог."""
        ctx = self.operator_context(user_id)
        settings = ctx["settings"]
        assignment_id = str(assignment_id)
        try:
            self._precheck_call_signature(ctx, assignment_id)
        except Exception:
            # Страховка, а не условие звонка: сбой проверки звонок не останавливает.
            log.exception("dial_list: проверка подписания перед звонком (строка %s) упала — звоним без неё",
                          assignment_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT a.id, a.state, a.attempts, l.phone_norm, l.full_name, l.id,
                       (l.signed_at IS NOT NULL OR l.sign_status = 'processing')
                FROM dial_list_assignments a
                JOIN dial_list_leads l ON l.id = a.lead_id
                WHERE a.id = %s AND a.operator_id = %s
                FOR UPDATE OF a
            """, (assignment_id, ctx["user_id"]))
            row = cur.fetchone()
            if not row:
                raise DialListError("Строка списка не найдена", 404)
            if row[1] != "issued":
                raise DialListError("Эта строка уже обработана", 409)
            active = self._active_attempt(cur, user_id)
            if active:
                raise DialListError(
                    f"Идёт звонок ({active['full_name']}). Дождитесь его завершения", 409)
            self._require_outcome_done(cur, ctx)
            # Кончившееся плечо (ended) другим звонкам не мешает (13.10.2026), но этому же водителю
            # снова — только после исхода: иначе запоздавший исход прошлого звонка закрыл бы строку
            # посреди нового. Это секунды; зависшую больше часа попытку закроет reconcile.
            cur.execute("""
                SELECT 1 FROM dial_list_attempts
                WHERE assignment_id = %s AND state = 'ended'
                  AND requested_at > CURRENT_TIMESTAMP - make_interval(secs => %s)
                LIMIT 1
            """, (assignment_id, ATTEMPT_STALE_MINUTES * 60))
            if cur.fetchone():
                raise DialListError("Исход прошлого звонка этому водителю ещё не пришёл от АТС — "
                                    "подождите несколько секунд", 409)
            signed = bool(row[6])
            if signed:
                # Водитель уже подписал: звонка не будет, строка уходит со списка, как при
                # «Исключить». Отказ — после фиксации: исключение откатило бы закрытие.
                self._close_row(cur, assignment_id)
            else:
                phone_norm, full_name, lead_id = row[3], row[4] or "", str(row[5])
                cur.execute("""
                    INSERT INTO dial_list_attempts (assignment_id, operator_id, internal_number)
                    VALUES (%s, %s, %s) RETURNING id, requested_at
                """, (assignment_id, ctx["user_id"], ctx["internal_number"]))
                attempt_id, requested_at = cur.fetchone()
                attempt_id = str(attempt_id)
                cur.execute("UPDATE dial_list_assignments SET attempts = attempts + 1 WHERE id = %s",
                            (assignment_id,))
                attempts_now = int(row[2] or 0) + 1
                # Что оператор записал по водителю раньше — в карточку звонка (12.10.2026).
                last_comment = self._last_comment(cur, ctx["user_id"], lead_id)
        if signed:
            log.info("dial_list: оператор %s → строка %s: водитель уже подписал, строка снята со списка",
                     ctx["user_id"], assignment_id)
            raise DialListError(SIGNED_ROW_CLOSED, 409)

        # Что линия оператора увидит во From. Без подмены Binotel ставит туда номер
        # водителя — и он виден в SIP-пакете (Wireshark), что и было главным
        # требованием закрыть. Проверено живьём 23.09.2026: callerIdForEmployee
        # подменяет From ровно на переданное значение. Поле отдела пусто — ставим
        # внутренний номер самого оператора: нейтрально и всегда есть.
        caller_id = (settings.get("caller_id_for_employee") or "").strip() or ctx["internal_number"]
        extra = {"callerIdForEmployee": caller_id}
        try:
            client = self._client(ctx["department_id"])
            general_call_id = client.originate_internal_to_external(
                ctx["internal_number"], phone_norm, **extra)
        except DialListError:
            self._fail_attempt(attempt_id, assignment_id, lead_id, "подключение к Binotel не настроено",
                               attempts_now, settings["max_attempts"])
            raise
        except Exception as exc:  # отказ АТС или сеть
            raw = str(exc)
            lowered = raw.lower()
            code = binotel_error_code(raw)
            # Код 150 «Can't call to the ext» — АТС не достучалась до ЛИНИИ оператора:
            # телефон не зарегистрирован (неверный сервер/пароль, нет сети). Это не
            # вина водителя — попытку по лиду не считаем и говорим оператору, что делать.
            ext_unreachable = code == 150 or "call to the ext" in lowered
            too_frequent = (isinstance(exc, binotel.RateLimited) or code == 106
                            or "too frequent" in lowered or "часто" in lowered)
            # Текст Binotel бывает с номером водителя (13.10.2026): в попытку — без длинных чисел
            # (её api_error видит руководитель), полностью — только в журнал сервера, телефону —
            # только код.
            self._fail_attempt(attempt_id, assignment_id, lead_id, scrub_digits(raw)[:500],
                               attempts_now, settings["max_attempts"],
                               count_for_lead=not ext_unreachable)
            log.warning("dial_list: Binotel отказал оператору %s (строка %s): %s", ctx["user_id"], assignment_id,
                        raw[:500])
            if ext_unreachable:
                hint = self._line_status_hint(ctx["department_id"], ctx["internal_number"])
                raise DialListError(
                    f"Линия {ctx['internal_number']} не на связи: АТС не смогла до неё дозвониться. {hint}", 409)
            coded = f" (код {code})" if code is not None else ""
            if too_frequent:
                raise DialListError(f"АТС не приняла звонок{coded}: слишком частые запросы — повторите "
                                    "через несколько секунд", 429)
            raise DialListError(f"АТС не приняла звонок{coded} — обратитесь к руководителю", 502)

        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_attempts SET general_call_id = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (general_call_id[:32], attempt_id))
        log.info("dial_list: оператор %s → строка %s, generalCallID=%s", ctx["user_id"], assignment_id, general_call_id)
        return {
            "attempt_id": attempt_id,
            "assignment_id": assignment_id,
            # С кем разговор (12.10.2026): по нему телефон проверяет подписание во время звонка.
            "lead_id": lead_id,
            "general_call_id": general_call_id,
            "full_name": full_name,
            "requested_at": _iso(requested_at),
            "expect_leg_within_sec": LEG_TIMEOUT_SEC,
            # last_comment, last_comment_label, last_comment_outcome — как в состоянии.
            **last_comment,
        }

    def _fail_attempt(self, attempt_id, assignment_id, lead_id, message, attempts_now, max_attempts,
                      count_for_lead=True):
        """АТС отказала: попытка failed. Строка остаётся в работе, пока есть попытки —
        оператор может нажать ещё раз; исчерпали — закрываем как failed.
        count_for_lead=False — сбой на нашей стороне (линия оператора не на связи):
        счётчик попыток строки откатываем, водителю это в минус не идёт.
        message — без длинных чисел (scrub_digits): api_error видит руководитель."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_attempts
                SET state = 'failed', api_error = %s, final_source = 'api_error',
                    finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (scrub_digits(message)[:500], attempt_id))
            if not count_for_lead:
                cur.execute("UPDATE dial_list_assignments SET attempts = GREATEST(attempts - 1, 0) WHERE id = %s",
                            (assignment_id,))
                return
            if attempts_now >= int(max_attempts):
                self._close_assignment(cur, assignment_id, lead_id, "failed", answered=False, count_attempt=True)

    # ------------------------------------------------------------ исходы
    def phone_event(self, user_id, attempt_id, event, at=None, by_operator=False, leg_sec=0,
                    ring_timeout=False, ring_sec=0, auto_hangup="", talk_detected=None, phone_version=None,
                    announcement_suspected=None, announcement_by=None):
        """Подсказка телефона: плечо пришло / принято / разговор кончился / плеча не было /
        оператор сам нажал «Завершить» / гудки до сброса кончились (ring_timeout).

        В ответе телефон получает решение по итогу: `outcome_required` — true (открывать
        окно итога), false (итог не нужен: попытка отменена или разговора не было),
        None (исход АТС ещё не известен — телефон ждёт `pending_outcome` из состояния);
        `cancelled` — попытка отменена и не засчитана.

        ring_timeout у ended — телефон сам положил плечо на гудках, водитель не взял
        трубку. Это не отбой оператора: by_operator тогда не учитывается, попытка —
        обычный недозвон (ring_timeout_at). Событие ring_timeout — _ring_timeout_event.

        С телефона 3.22.34 (12.10.2026) у ended ещё два ключа. auto_hangup — почему плечо
        положил сам телефон: 'ring_timeout' — то же, что ключ ring_timeout; 'announcement' —
        сообщение сети «абонент недоступен»: недозвон, что бы ни ответил Binotel, итог не
        нужен сразу. talk_detected — был ли разговор: True — итог нужен сразу, и после
        отбоя оператора (отменой такая попытка не становится); «ключа нет» (None) — как
        раньше; False — итог нужен, только если ответ водителя подтвердит Binotel
        (_outcome_decision). Незнакомые значения обоих ключей — как их отсутствие.

        С ревью 13.10.2026 у ended ещё два необязательных ключа — для замеров точности
        распознавания сообщения сети (announcement_stats), на счёт попытки они не влияют:
        announcement_suspected — распознавание по речи сработало на плече хоть раз (в любом
        режиме announcement_mode), только true/false; announcement_by — кто решил, что это
        сообщение сети: 'auto' | 'sit' | 'operator' (ANNOUNCEMENT_BY_VALUES), пишется только вместе
        с auto_hangup 'announcement'. Отбой оператора по подсказке «похоже на автоответчик»
        (announcement_by 'operator') — то же сообщение сети: недозвон, итога нет, не отмена.

        phone_version — заголовок X-ICore-Phone-Version: на вопрос ring_timeout телефону
        без защиты по речи (phone_guards_speech) ответ — сразу «не класть»
        (_ring_timeout_unguarded)."""
        event = str(event or "").strip().lower()
        if event not in PHONE_EVENTS:
            raise DialListError(f"Неизвестное событие: {event!r}")
        attempt_id = str(attempt_id)
        if event == "ring_timeout" and not phone_guards_speech(phone_version):
            # Раньше operator_context: этот ответ не должен зависеть ни от базы, ни от Binotel.
            return self._ring_timeout_unguarded(user_id, attempt_id, ring_sec, phone_version)
        ctx = self.operator_context(user_id)
        if event == "ring_timeout":
            return self._ring_timeout_event(ctx, attempt_id, ring_sec)
        ended = event == "ended"
        # Плечо положил сам телефон: прежний ключ ring_timeout — это гудки; auto_hangup
        # называет причину сам. Такой конец — не отбой оператора.
        auto = clean_auto_hangup(auto_hangup) if ended else ""
        if ended and ring_timeout and not auto:
            auto = AUTO_HANGUP_RING_TIMEOUT
        talk = talk_detected if ended and isinstance(talk_detected, bool) else None
        suspected = announcement_suspected if ended and isinstance(announcement_suspected, bool) else None
        decided_by = clean_announcement_by(announcement_by) if auto == AUTO_HANGUP_ANNOUNCEMENT else ""
        by_operator = (bool(by_operator) or event == "operator_hangup") and not auto
        leg_sec = max(0, min(_to_int(leg_sec, 0), 24 * 3600))
        with self.db._get_cursor() as cur:
            # talk_detected и auto_hangup (ревью 13.10.2026) — что телефон уже сообщил раньше: событие
            # operator_hangup и ended идут отдельными запросами и приходят в любом порядке, а
            # поздней отмены не бывает у разговора, который телефон слышал, при любом их порядке.
            cur.execute("""
                SELECT state, general_call_id, assignment_id, cancelled, disposition, ring_timeout_at,
                       talk_detected, auto_hangup
                FROM dial_list_attempts
                WHERE id = %s AND operator_id = %s FOR UPDATE
            """, (attempt_id, ctx["user_id"]))
            row = cur.fetchone()
            if not row:
                raise DialListError("Попытка не найдена", 404)
            state, general_call_id = row[0], row[1]
            ring_marked = row[5] is not None
            stored_talk = row[6] if len(row) > 6 else None
            stored_auto = (row[7] if len(row) > 7 else "") or ""
            # Binotel уже закрыл попытку ответом — раньше, чем телефон сообщил конец плеча.
            closed_answered = state == "finished" and map_disposition(row[4]) == "answered"
            if suspected is not None or decided_by:
                # Замер точности распознавания — факт с телефона, пишем при любом состоянии попытки.
                cur.execute("""
                    UPDATE dial_list_attempts
                    SET announcement_suspected = COALESCE(%s, announcement_suspected),
                        announcement_by = COALESCE(NULLIF(%s, ''), announcement_by),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (suspected, decided_by, attempt_id))
            if auto:
                # Отметку ставим и по закрытой попытке: вебхук мог обогнать телефон, а
                # недозвон без неё ждал бы итога, как разговор.
                self._mark_ring_timeout(cur, attempt_id, leg_sec, auto)
                if auto == AUTO_HANGUP_ANNOUNCEMENT and closed_answered and not row[3]:
                    # Ответ Binotel (автоответчик) опередил событие: дозвон уже засчитан.
                    self._unanswer_attempt(cur, attempt_id, str(row[2]))
            elif talk is not None and ring_marked:
                # Отметку сброса поставил сервер, когда на вопрос ring_timeout ответил «класть»,
                # а телефон с защитой по речи плечо не положил: в его ended нет ни
                # ring_timeout, ни auto_hangup. Отметка — неправда, снимаем: дальше это
                # обычный конец плеча (или отбой оператора), а не сброс на гудках.
                ring_marked = not self._clear_ring_timeout(cur, attempt_id)
            if talk is True or (talk is False and not closed_answered):
                # False по попытке, которую Binotel уже закрыл ответом, не пишем: её исход
                # записан, пока плечо ещё шло (ANSWER приходит и посреди разговора), и
                # длительность в нём может быть неполной — судить по ней «разговора не было»
                # нельзя. Такая попытка остаётся разговором, как у прежних телефонов.
                cur.execute("""
                    UPDATE dial_list_attempts SET talk_detected = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (talk, attempt_id))
            if by_operator:
                # Отбой оператора запоминаем всегда — даже по уже закрытой попытке:
                # вебхук Binotel мог обогнать телефон и досчитать попытку как «не
                # ответил». Тогда её надо откатить здесь же (см. _cancel_attempt).
                cur.execute("""
                    UPDATE dial_list_attempts
                    SET operator_hangup_at = COALESCE(operator_hangup_at, CURRENT_TIMESTAMP),
                        leg_sec = CASE WHEN %s > 0 THEN %s ELSE leg_sec END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (leg_sec, leg_sec, attempt_id))
                # Сброс на гудках сильнее отбоя: такая попытка — недозвон, а не отмена. Разговор,
                # который слышал телефон (talk_detected true — в этом событии или уже записанный
                # прежним ended), — тоже не отмена: трубку оператор положил после него (см.
                # _finish_attempt). Сообщение сети — недозвон, не отмена.
                if (state == "finished" and not row[3] and not ring_marked and talk is not True
                        and stored_talk is not True and stored_auto != AUTO_HANGUP_ANNOUNCEMENT
                        and map_disposition(row[4]) not in ("", "answered")):
                    self._cancel_attempt(cur, attempt_id, str(row[2]), late=True)
            if state in ("finished", "failed"):
                cancelled, required = self._outcome_decision(cur, attempt_id)
                return {"attempt_id": attempt_id, "state": state, "final": True,
                        "cancelled": cancelled, "outcome_required": required}
            new_state = {
                "ringing": "leg_ringing", "answered": "leg_answered",
                "ended": "ended", "no_leg": state, "operator_hangup": state,
            }[event]
            # Назад по цепочке не ходим: «ringing» после «answered» ничего не значит.
            order = ["requested", "leg_ringing", "leg_answered", "ended"]
            if order.index(new_state) < order.index(state):
                new_state = state
            # Конец плеча по часам приложения — по ним же помечены данные опроса (live_calls.Seen):
            # ответ водителя закрывается только данными после этого момента (live_verdict). Так же
            # он и пишется в phone_ended_at (ревью 13.10.2026), а не CURRENT_TIMESTAMP базы: по нему
            # закрывают попытку опрос (_settle_live) и reconcile, и часы двух машин, разошедшиеся
            # на секунду, выдали бы ANSWER посреди разговора за финал с неполной длительностью.
            ended_wall = _utcnow()
            cur.execute("""
                UPDATE dial_list_attempts
                SET state = %s, phone_event_at = CURRENT_TIMESTAMP,
                    phone_ended_at = CASE WHEN %s = 'ended' THEN %s ELSE phone_ended_at END,
                    leg_answered_at = CASE WHEN %s = 'answered'
                                           THEN COALESCE(leg_answered_at, CURRENT_TIMESTAMP)
                                           ELSE leg_answered_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (new_state, event, ended_wall, event, attempt_id))
        result = {"attempt_id": attempt_id, "state": new_state, "final": False}
        # Плечо кончилось или не пришло — что знает Binotel, смотрим в общем кэше компании
        # (при надобности одно обновление на всех, ждём его не дольше CALLS_WAIT_SEC). Финал «не
        # ответил / занято» закрывает попытку сразу. Ответ водителя — только данными после конца
        # плеча (live_verdict): длительность в ANSWER посреди разговора неполная, его доберут
        # опрос (_settle_live), reconcile или вебхук. Звонить дальше это не мешает.
        if event in ("ended", "no_leg") and general_call_id:
            seen = self._calls_seen(ctx, [general_call_id]).get(str(general_call_id))
            verdict, call = live_verdict(new_state, seen, leg_over_at=ended_wall if ended else None, leg_over=ended)
            if verdict == "finish":
                self._finish_by_call(attempt_id, call, "poll")
                result.update({"state": "finished", "final": True, "disposition": call.get("disposition")})
        with self.db._get_cursor() as cur:
            cancelled, required = self._outcome_decision(cur, attempt_id)
        result.update({"cancelled": cancelled, "outcome_required": required})
        return result

    def _ring_timeout_unguarded(self, user_id, attempt_id, ring_sec=0, phone_version=None):
        """Вопрос «класть ли плечо на гудках» от телефона без защиты по речи: версии в
        запросе нет — это выпущенный 3.22.33. Ответ — сразу и всегда «не класть», без базы
        и без Binotel. Иначе нельзя: ответа дольше пяти секунд и любой ошибки этот телефон
        не ждёт — кладёт трубку сам; так 12.10.2026 оборвались 52 разговора (Binotel
        отвечал «слишком часто» либо позже срока). Плечо закончит АТС или оператор.

        Попытку ничем не помечаем и не читаем: чем кончился звонок, телефон сообщит в
        ended. В состоянии такой телефон уже получает ring_timeout_sec: 0 (get_state) и
        вопрос не задаёт — сюда приходит лишь тот, кто читал состояние до выката."""
        log.info("dial_list: попытка %s — гудки кончились (у телефона %d с) у телефона без защиты по речи "
                 "(оператор %s, версия %r): плечо не кладём, Binotel не спрашиваем", attempt_id,
                 max(0, min(_to_int(ring_sec, 0), 3600)), user_id, str(phone_version or "")[:32])
        return {"attempt_id": attempt_id, "final": False, "cancelled": False, "outcome_required": None,
                "hangup": False, "talking": False}

    def _ring_timeout_event(self, ctx, attempt_id, ring_sec=0):
        """Телефон досчитал гудки до сброса (ring_timeout_sec состояния) и спрашивает,
        класть ли плечо. Решают данные Binotel из общего кэша компании (live_calls), не
        старше RING_FRESH_SEC: при надобности — одно обновление, ждём всего не дольше
        RING_BUDGET_SEC. Водитель говорит (ONLINE, или ANSWER, пока плечо идёт, — Binotel
        отдаёт его и посреди разговора) — не класть (talking); исход финальный «не ответил /
        занято» — закрыть попытку как обычно и класть; АТС ещё набирает — класть.

        «Нет данных» — никогда не «класть» (13.10.2026): Binotel ответил «слишком часто», не
        успел, ошибся, звонка у него не видно или данные старше RING_DATA_MAX_AGE_SEC — плечо не
        кладём, его закончат водитель, АТС или оператор. Раньше молчание значило «класть», и
        так рвались живые разговоры. Когда плечо кладут, попытка помечается сбросом
        (ring_timeout_at): она засчитается недозвоном, итог по ней не нужен. operator_hangup_at
        здесь не ставится — это не отбой оператора.

        Сюда попадает только телефон с защитой по речи (phone_event): он может и не
        послушаться ответа «класть» — на линии уже речь. Тогда его ended придёт без
        ring_timeout, и отметку сброса, поставленную здесь, оно снимет (_clear_ring_timeout).

        Ревью 13.10.2026. «АТС ещё набирает» велит класть, только если эти данные не старше
        RING_HANGUP_MAX_AGE_SEC: старее — «нет данных» (hangup: false, без disposition) — водитель
        мог снять трубку уже после них. И это всё равно не подтверждение «не ответил»: между
        гудками телефон может переждать до следующего гудка (пауза или молчащий водитель — скажет
        гудок), а Binotel подтвердил «не дозвонились», только когда в ответе hangup и final вместе
        (финальное «не дозвонились», попытка закрыта). data_age_ms — возраст данных опроса Binotel,
        по которым решали (от отправки запроса, так что не меньше настоящего); ключа нет — данных
        опроса не было или они устарели, либо попытка уже закрыта и решали по базе.

        Ответ — как у других событий (attempt_id, state, final, [disposition], cancelled,
        outcome_required) плюс hangup — класть ли плечо — talking и [data_age_ms]."""
        ring_sec = max(0, min(_to_int(ring_sec, 0), 3600))
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT state, general_call_id, disposition, cancelled FROM dial_list_attempts
                WHERE id = %s AND operator_id = %s
            """, (attempt_id, ctx["user_id"]))
            row = cur.fetchone()
            if not row:
                raise DialListError("Попытка не найдена", 404)
            state, general_call_id, disposition, cancelled = row
            if state in ("finished", "failed"):
                # Исход уже есть. Водитель не ответил — плечо всё равно кончится: телефон
                # кладёт его сам и без окна итога (недозвон помечаем сбросом).
                answered = state == "finished" and map_disposition(disposition) == "answered"
                if state == "finished" and not answered and not cancelled:
                    self._mark_ring_timeout(cur, attempt_id)
                decided, required = self._outcome_decision(cur, attempt_id)
                return {"attempt_id": attempt_id, "state": state, "final": True,
                        "disposition": disposition or "", "cancelled": decided,
                        "outcome_required": required, "hangup": not answered, "talking": False}
        # Данные не старше RING_FRESH_SEC — ради них обновление запускается и раньше обычного
        # темпа; своей очереди у Binotel ждём не дольше RING_BINOTEL_WAIT_SEC, всего — не дольше
        # RING_BUDGET_SEC. Вне транзакций — правило модуля.
        seen = None
        if general_call_id:
            seen = self._calls_seen(ctx, [general_call_id], max_age=RING_FRESH_SEC, wait=RING_BUDGET_SEC,
                                    min_interval=RING_FRESH_SEC,
                                    binotel_wait=RING_BINOTEL_WAIT_SEC).get(str(general_call_id))
        if seen is not None and time.monotonic() - seen.mono > RING_DATA_MAX_AGE_SEC:
            seen = None                      # устаревшие данные — как их нет
        # Плечо уже кончилось (ended пришёл раньше вопроса) — ответ закрывают данные после конца
        # плеча, а не этот вопрос: момент конца берём «сейчас», ANSWER тогда — «ждём».
        verdict, call = live_verdict(state, seen, leg_over_at=_utcnow() if state == "ended" else None)
        stale = ""
        if verdict == "dialing" and time.monotonic() - seen.mono > RING_HANGUP_MAX_AGE_SEC:
            # «АТС ещё набирает» по данным старше RING_HANGUP_MAX_AGE_SEC — как их нет: водитель мог
            # снять трубку уже после них и молчать, а класть велят только свежие (см. выше).
            stale = " (%.1f с назад — устарело)" % (time.monotonic() - seen.mono)
            seen, verdict, call = None, "unknown", None
        live = str(((seen.call if seen is not None else None) or {}).get("disposition") or "").strip().upper()
        talking = verdict in ("talking", "settling")
        # «finish» здесь — всегда «не дозвонились»: ответ водителя при идущем плече — talking, при
        # кончившемся — settling (момент конца — «сейчас»). final — оно и есть: попытка закрыта.
        final = map_disposition(live) if verdict == "finish" else ""
        # Класть — только когда Binotel сказал «не ответил»: набор ещё идёт или финал без ответа.
        hangup = verdict == "dialing" or (verdict == "finish" and final != "answered")
        if hangup:
            # Сначала отметка: закрытие ниже (_finish_attempt) уже видит сброс на гудках и
            # засчитывает недозвон, даже если отбой оператора каким-то путём тоже записан.
            # Только пока плечо не кончилось (before_leg_end): пока ждали Binotel, телефон мог
            # уже прислать ended — он и сказал, клал ли плечо сам.
            with self.db._get_cursor() as cur:
                self._mark_ring_timeout(cur, attempt_id, before_leg_end=True)
        if final:
            self._finish_by_call(attempt_id, call, "poll")
        with self.db._get_cursor() as cur:
            decided, required = self._outcome_decision(cur, attempt_id)
        age_ms = None if seen is None else max(0, int(round((time.monotonic() - seen.mono) * 1000)))
        log.info("dial_list: попытка %s — гудки кончились (у телефона %d с), Binotel: %s%s — %s", attempt_id,
                 ring_sec, live or "нет данных",
                 stale or ("" if age_ms is None else " (данным %.1f с)" % (age_ms / 1000.0)),
                 "водитель говорит, плечо не кладём" if talking else (
                     "кладём плечо" if hangup else "решать не по чему, плечо не кладём"))
        result = {"attempt_id": attempt_id, "state": "finished" if final else state, "final": bool(final),
                  "cancelled": decided, "outcome_required": required, "hangup": hangup, "talking": talking}
        if live:
            result["disposition"] = live
        if age_ms is not None:
            result["data_age_ms"] = age_ms
        return result

    def _mark_ring_timeout(self, cur, attempt_id, leg_sec=0, auto_hangup=AUTO_HANGUP_RING_TIMEOUT,
                           before_leg_end=False):
        """Телефон кладёт (или уже положил) плечо сам — на гудках либо на сообщении сети
        (auto_hangup): запомнить время первого такого сигнала и причину, а у ended — и
        сколько секунд плечо было принято. Сообщение сети сильнее гудков: поздний сигнал
        о гудках причину 'announcement' не затирает. Проваленную и отменённую попытку не
        трогаем: звонка по ним нет либо он уже не в счёт. В транзакции вызывающего.

        before_leg_end — отметка по ответу «класть» на вопрос ring_timeout: ставится, только
        пока телефон не сообщил конец плеча. Ответа Binotel вопрос ждёт секунды, плечо за это
        время могло кончиться — тогда, что было на самом деле, телефон уже сказал в ended, и
        запоздалая отметка сделала бы состоявшийся разговор «сбросом на гудках»."""
        leg_sec = int(leg_sec or 0)
        cur.execute("""
            UPDATE dial_list_attempts
            SET ring_timeout_at = COALESCE(ring_timeout_at, CURRENT_TIMESTAMP),
                auto_hangup = CASE WHEN auto_hangup = 'announcement' THEN auto_hangup ELSE %s END,
                leg_sec = CASE WHEN %s > 0 THEN %s ELSE leg_sec END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND state <> 'failed' AND NOT cancelled
        """ + ("AND phone_ended_at IS NULL" if before_leg_end else ""),
                    (clean_auto_hangup(auto_hangup) or AUTO_HANGUP_RING_TIMEOUT, leg_sec, leg_sec, str(attempt_id)))

    def _clear_ring_timeout(self, cur, attempt_id):
        """Снять отметку сброса на гудках, которую телефон не подтвердил: сервер поставил
        её, ответив на вопрос ring_timeout «класть», а телефон плечо не положил (защита по
        речи). Сообщение сети не снимается — его называет сам телефон. True — сняли. В
        транзакции вызывающего."""
        cur.execute("""
            UPDATE dial_list_attempts
            SET ring_timeout_at = NULL, auto_hangup = '', updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND ring_timeout_at IS NOT NULL AND auto_hangup <> 'announcement'
            RETURNING id
        """, (str(attempt_id),))
        return cur.fetchone() is not None

    def _unanswer_attempt(self, cur, attempt_id, assignment_id):
        """Попытку Binotel уже закрыл ответом, а телефон сообщил: это было сообщение сети
        (автоответчик), плечо он положил сам. Разговора не было — правим то, что успело
        засчитаться, в транзакции вызывающего. Как поздняя отмена (_cancel_attempt), только
        попытка остаётся в счёте: это недозвон.

        Строка — недозвон, если её закрыла эта попытка (последняя у строки, строка закрыта
        дозвоном). Время дозвона лида — по остальным его разговорам; не было их — пусто, и
        лид снова в работе, если попытки не кончились. Успешку, если её успели засчитать
        этой попытке, снимаем: решение примет следующая проверка подписания."""
        attempt_id, assignment_id = str(attempt_id), str(assignment_id)
        cur.execute("""
            SELECT a.state, a.result, a.lead_id,
                   (SELECT t.id FROM dial_list_attempts t WHERE t.assignment_id = a.id
                    ORDER BY t.requested_at DESC LIMIT 1)
            FROM dial_list_assignments a WHERE a.id = %s FOR UPDATE
        """, (assignment_id,))
        a = cur.fetchone()
        if not a:
            return False
        if a[0] == "done" and a[1] == "answered" and str(a[3]) == attempt_id:
            cur.execute("UPDATE dial_list_assignments SET result = 'no_answer' WHERE id = %s", (assignment_id,))
        cur.execute("""
            UPDATE dial_list_leads l
            SET answered_at = other.answered_at,
                status = CASE WHEN l.status = 'done' AND other.answered_at IS NULL
                                   AND l.attempts_total < COALESCE(
                                       (SELECT s.max_attempts FROM dial_list_department_settings s
                                        WHERE s.department_id = l.department_id), %s)
                              THEN 'in_progress' ELSE l.status END,
                success_attempt_id = CASE WHEN l.success_attempt_id = %s::uuid THEN NULL
                                          ELSE l.success_attempt_id END,
                success_operator_id = CASE WHEN l.success_attempt_id = %s::uuid THEN NULL
                                           ELSE l.success_operator_id END,
                success_resolved_at = CASE WHEN l.success_attempt_id = %s::uuid THEN NULL
                                           ELSE l.success_resolved_at END,
                updated_at = CURRENT_TIMESTAMP
            FROM (SELECT MIN(t.finished_at) AS answered_at
                  FROM dial_list_attempts t
                  JOIN dial_list_assignments ta ON ta.id = t.assignment_id
                  WHERE ta.lead_id = %s AND t.id <> %s AND t.state = 'finished' AND NOT t.cancelled
                    AND UPPER(t.disposition) IN %s AND """ + ATTEMPT_TALK_SQL + """) other
            WHERE l.id = %s
        """, (DEFAULT_MAX_ATTEMPTS, attempt_id, attempt_id, attempt_id, str(a[2]), attempt_id, ANSWERED_SQL,
              str(a[2])))
        log.info("dial_list: попытка %s — сообщение сети, а Binotel успел закрыть её ответом: дозвон снят",
                 attempt_id)
        return True

    # Живое состояние попытки для карточки звонка. Телефон принимает плечо от АТС
    # сразу, а водителя АТС набирает после — «разговор» на карточке появлялся, когда
    # его ещё не было (владелец, 25.09.2026). Знает про ответ водителя только Binotel:
    # call-details отдаёт CALLING (набор) → ONLINE или ANSWER (разговор) → финал. Данные —
    # из общего кэша компании (live_calls, 13.10.2026): один опрос на всех операторов не чаще
    # CALLS_REFRESH_SEC, а не запрос на каждую карточку каждые 3 с.

    def attempt_live(self, user_id, attempt_id):
        """Набираем / разговор / финал по Binotel — для карточки звонка на телефоне.

        ANSWER, пока плечо по телефону идёт, — разговор (talking), а не финал: Binotel отдаёт
        его и посреди звонка, с неполной длительностью (live_verdict). Финал «не ответил /
        занято» закрывает попытку здесь же, как событие телефона."""
        ctx = self.operator_context(user_id)
        attempt_id = str(attempt_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT state, general_call_id, disposition, cancelled, billsec, auto_hangup, talk_detected,
                       phone_ended_at
                FROM dial_list_attempts
                WHERE id = %s AND operator_id = %s
            """, (attempt_id, ctx["user_id"]))
            row = cur.fetchone()
        if not row:
            raise DialListError("Попытка не найдена", 404)
        state, general_call_id, disposition, cancelled, billsec, auto_hangup, talk_detected = row[:7]
        ended_at = row[7] if len(row) > 7 else None
        if state in ("finished", "failed"):
            # Исход — как у строки выдачи (attempt_result): сообщение сети и ответ без
            # разговора — недозвон, а не «ответил».
            result = (attempt_result(disposition, billsec, auto_hangup or "", talk_detected)
                      or ("failed" if state == "failed" else "other"))
            return {"attempt_id": attempt_id, "state": state, "disposition": disposition or "",
                    "talking": False, "final": True, "result": result, "cancelled": bool(cancelled)}
        live, verdict, call = "", "unknown", None
        if general_call_id:
            seen = self._calls_seen(ctx, [general_call_id]).get(str(general_call_id))
            verdict, call = live_verdict(state, seen, leg_over_at=ended_at)
            live = str(((seen.call if seen is not None else None) or {}).get("disposition") or "").strip().upper()
        final = verdict == "finish"
        if final:
            # Финал уже известен — закрываем попытку здесь же, как делает phone_event.
            self._finish_by_call(attempt_id, call, "poll")
        return {
            "attempt_id": attempt_id,
            "state": "finished" if final else state,
            "disposition": live,
            "talking": verdict in ("talking", "settling"),
            "final": final,
            "result": (attempt_result(live, call.get("billsec"), auto_hangup or "", talk_detected) or "other")
                      if final else "",
            "cancelled": False,
        }

    def reconcile(self, ctx_or_user_id, limit=POLL_MAX_PER_REQUEST):
        """Добрать исходы незавершённых попыток оператора.

        Зовётся на каждом GET состояния: дёшево, когда добирать нечего (один SELECT), и не
        требует планировщика. Данные — из общего кэша компании (live_calls): при надобности
        одно обновление на всех, ждём его не дольше CALLS_WAIT_SEC. Попытка, плечо которой
        кончилось (ended) или после которой оператор уже начал следующий звонок, с ответом
        водителя закрывается данными, полученными после конца плеча (live_verdict): тогда
        длительность разговора полная. Старые попытки без исхода закрываются как «неизвестно»,
        чтобы не держать строку вечно; известен ответ водителя — им."""
        ctx = ctx_or_user_id if isinstance(ctx_or_user_id, dict) else self.operator_context(ctx_or_user_id)
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT t.id, t.general_call_id, t.requested_at, t.state, t.phone_ended_at,
                       (SELECT MIN(n.requested_at) FROM dial_list_attempts n
                        WHERE n.operator_id = t.operator_id AND n.requested_at > t.requested_at)
                FROM dial_list_attempts t
                WHERE t.operator_id = %s
                  AND t.state IN %s
                  AND (t.state = 'ended' OR t.requested_at < CURRENT_TIMESTAMP - make_interval(secs => %s))
                ORDER BY t.requested_at ASC LIMIT %s
            """, (ctx["user_id"], LIVE_STATES, POLL_MIN_AGE_SEC, int(limit)))
            pending = [(str(r[0]),) + tuple(r[1:6]) for r in cur.fetchall()]
        if not pending:
            return 0
        now = _utcnow()
        # Ответ водителя закрываем данными после конца плеча: самый поздний такой срок из уже
        # наступивших — ради него обновление, даже если кэш моложе CALLS_REFRESH_SEC.
        newer = None
        for _aid, gid, _at, state, ended_at, superseded_at in pending:
            over_at = _as_utc(leg_over_moment(state, ended_at, superseded_at))
            if gid and over_at is not None:
                due = over_at + timedelta(seconds=ANSWER_SETTLE_SEC)
                if due <= now and (newer is None or due > newer):
                    newer = due
        gids = [gid for _aid, gid, *_rest in pending if gid]
        seen = self._calls_seen(ctx, gids, newer_than=newer) if gids else {}
        finished = 0
        stale_before = now - timedelta(minutes=ATTEMPT_STALE_MINUTES)
        for attempt_id, gid, requested_at, state, ended_at, superseded_at in pending:
            entry = seen.get(str(gid)) if gid else None
            verdict, call = live_verdict(state, entry, leg_over_at=leg_over_moment(state, ended_at, superseded_at),
                                         leg_over=state == "ended" or superseded_at is not None)
            requested = _as_utc(requested_at)
            stale = requested is not None and requested < stale_before
            if stale and verdict in ("talking", "settling"):
                # Час без конца плеча: телефон его так и не сообщил. Ответ водителя известен —
                # закрываем им, а не «неизвестно».
                verdict, call = "finish", entry.call
            if verdict == "finish":
                self._finish_by_call(attempt_id, call, "poll")
                finished += 1
            elif stale:
                self._finish_attempt(attempt_id, "UNKNOWN", 0, 0, "timeout")
                finished += 1
        return finished

    def _finish_by_call(self, attempt_id, call, source):
        self._finish_attempt(attempt_id, call.get("disposition") or "", call.get("billsec") or 0,
                             call.get("waitsec") or 0, source)

    def _finish_attempt(self, attempt_id, disposition, billsec, waitsec, source):
        """Закрыть попытку исходом Binotel и досчитать его строке выдачи и лиду.

        Исход для строки и лида — attempt_result: ответ Binotel без разговора (сообщение
        сети, которое положил телефон; мгновенный сброс, когда телефон разговора не
        услышал) — недозвон. Что сообщил телефон, читаем ПОСЛЕ своего UPDATE: строка
        попытки уже под замком, и событие ended, пришедшее в ту же секунду, либо уже
        записано, либо дождётся конца этой транзакции и поправит засчитанное само."""
        disposition = str(disposition or "").strip().upper()[:32]
        with self.db._get_cursor() as cur:
            cur.execute("""
                UPDATE dial_list_attempts
                SET state = 'finished', disposition = %s, billsec = %s, waitsec = %s,
                    final_source = %s, finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND state <> 'finished'
                RETURNING assignment_id
            """, (disposition, int(billsec or 0), int(waitsec or 0), source, str(attempt_id)))
            row = cur.fetchone()
            if not row:
                # Уже закрыта. Если закрыли без настоящего исхода (UNKNOWN по таймауту
                # или промежуточный статус), а теперь пришёл финальный — дописываем
                # его, чтобы журнал и запись разговора были правдой. Строку выдачи и
                # счётчик попыток второй раз не трогаем — только факт дозвона у лида.
                cur.execute("""
                    UPDATE dial_list_attempts
                    SET disposition = %s, billsec = %s, waitsec = %s, final_source = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND state = 'finished' AND UPPER(disposition) IN %s
                    RETURNING assignment_id
                """, (disposition, int(billsec or 0), int(waitsec or 0), source, str(attempt_id),
                      PROVISIONAL_DISPOSITIONS))
                late = cur.fetchone()
                if not late:
                    return self._raise_answered(cur, attempt_id, disposition, billsec, waitsec, source)
                cur.execute("""
                    SELECT cancelled, auto_hangup, talk_detected FROM dial_list_attempts WHERE id = %s
                """, (str(attempt_id),))
                flags = cur.fetchone() or (False, "", None)
                if attempt_result(disposition, billsec, flags[1], flags[2]) == "answered":
                    if flags[0]:
                        # Попытку отменили по провизорному исходу (таймаут UNKNOWN), а
                        # теперь Binotel говорит: водитель ответил. Разговор был — итог
                        # обязателен, попытка снова в счёт.
                        self._uncancel_attempt(cur, str(attempt_id), str(late[0]))
                        return True
                    cur.execute("""
                        UPDATE dial_list_leads l
                        SET answered_at = COALESCE(l.answered_at, CURRENT_TIMESTAMP),
                            status = CASE WHEN l.status IN ('new', 'in_progress') THEN 'done' ELSE l.status END,
                            updated_at = CURRENT_TIMESTAMP
                        FROM dial_list_assignments a
                        WHERE a.id = %s AND l.id = a.lead_id
                    """, (str(late[0]),))
                return True
            assignment_id = str(row[0])
            # Оператор сам положил трубку, а водитель так и не ответил: попытка
            # отменяется — не считается ни строке, ни лиду, строка остаётся в работе.
            # (Решение владельца 24.09.2026: «повесил трубку до ответа — не засчитывать».)
            # Сброс на гудках (ring_timeout_at) сильнее отбоя: плечо положил телефон,
            # потому что водитель не брал трубку, — это обычный недозвон, он в счёт.
            cur.execute("""
                SELECT operator_hangup_at, ring_timeout_at, auto_hangup, talk_detected
                FROM dial_list_attempts WHERE id = %s
            """, (str(attempt_id),))
            hung = cur.fetchone()
            result = (attempt_result(disposition, billsec, hung[2], hung[3]) if hung
                      else map_disposition(disposition)) or "other"
            # Телефон слышал разговор (talk_detected TRUE): трубку оператор положил после него,
            # а не «до ответа», — это не отмена. Итог по такой попытке уже потребован
            # (_outcome_decision), а отмена сделала бы его невозможным; попытка в счёте с
            # исходом Binotel, как обычный конец плеча.
            if (hung and hung[0] is not None and hung[1] is None and result != "answered"
                    and hung[3] is not True):
                self._cancel_attempt(cur, str(attempt_id), assignment_id, late=False)
                return True
            # Оператор мог успеть выбрать итог «перезвонить» раньше, чем приехал исход
            # от АТС: тогда лид не закрываем, а оставляем на повтор.
            cur.execute("""
                SELECT COALESCE(o.requeue, FALSE) FROM dial_list_attempts t
                LEFT JOIN dial_list_outcomes o ON o.id = t.outcome_id WHERE t.id = %s
            """, (str(attempt_id),))
            picked = cur.fetchone()
            requeue = bool(picked and picked[0])
            cur.execute("SELECT lead_id, state FROM dial_list_assignments WHERE id = %s FOR UPDATE", (assignment_id,))
            a = cur.fetchone()
            if a and a[1] == "issued":
                self._close_assignment(cur, assignment_id, str(a[0]), result,
                                       answered=(result == "answered"), count_attempt=True, requeue=requeue)
            elif a:
                # Строка уже закрыта прошлой попыткой — лид всё равно учитывает звонок.
                self._touch_lead(cur, str(a[0]), answered=(result == "answered"), count_attempt=True,
                                 requeue=requeue)
        return True

    def _close_assignment(self, cur, assignment_id, lead_id, result, answered, count_attempt, requeue=False):
        cur.execute("""
            UPDATE dial_list_assignments
            SET state = 'done', result = %s, done_at = CURRENT_TIMESTAMP
            WHERE id = %s RETURNING portion_id
        """, (result, assignment_id))
        row = cur.fetchone()
        self._touch_lead(cur, lead_id, answered=answered, count_attempt=count_attempt, requeue=requeue)
        if row:
            portion_id = str(row[0])
            cur.execute("""
                UPDATE dial_list_portions SET closed_at = CURRENT_TIMESTAMP
                WHERE id = %s AND closed_at IS NULL
                  AND NOT EXISTS (SELECT 1 FROM dial_list_assignments
                                  WHERE portion_id = %s AND state = 'issued')
            """, (portion_id, portion_id))

    def _touch_lead(self, cur, lead_id, answered, count_attempt, requeue=False):
        """Лид после исхода попытки, в транзакции вызывающего: попытка в счёте, время
        последней попытки, дозвон и статус.

        Обработанный водитель (status 'done': дозвонились раньше или попытки кончились)
        недозвоном обратно не открывается (12.10.2026). Ему теперь звонят «Позвонить ещё
        раз», и раньше первый же недозвон делал его 'in_progress' — через
        retry_after_hours общий пул выдал бы его другому оператору. Попытка при этом в
        счёте, время последней попытки сдвигается. Вернуть его в пул могут итог
        «перезвонить» (requeue — как было) и руководитель: «Вернуть в список» ставит 'new'
        ещё до звонка, так что это правило его не касается."""
        cur.execute("""
            SELECT department_id, attempts_total, status FROM dial_list_leads WHERE id = %s FOR UPDATE
        """, (lead_id,))
        row = cur.fetchone()
        if not row:
            return
        cur.execute("""
            SELECT max_attempts FROM dial_list_department_settings WHERE department_id = %s
        """, (row[0],))
        s = cur.fetchone()
        max_attempts = int(s[0]) if s else DEFAULT_MAX_ATTEMPTS
        attempts_total = int(row[1] or 0) + (1 if count_attempt else 0)
        if requeue:
            # Итог «перезвонить»: попытки с нуля, лид вернётся в пул через retry_after_hours.
            status, attempts_total = "in_progress", 0
        elif answered:
            status = "done"
        elif attempts_total >= max_attempts:
            status = "done"
        elif row[2] == "done":
            # Обработанный ещё до этой попытки — «Позвонить ещё раз» (см. выше): остаётся.
            status = "done"
        else:
            status = "in_progress"
        cur.execute("""
            UPDATE dial_list_leads
            SET attempts_total = %s, last_attempt_at = CURRENT_TIMESTAMP,
                answered_at = CASE WHEN %s THEN COALESCE(answered_at, CURRENT_TIMESTAMP) ELSE answered_at END,
                status = CASE WHEN status = 'excluded' THEN status ELSE %s END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (attempts_total, bool(answered), status, lead_id))

    # ------------------------------------------------------------ отмена до ответа
    def _cancel_attempt(self, cur, attempt_id, assignment_id, late=False):
        """Оператор сам завершил звонок, водитель не ответил: попытка не в счёт.

        late=False — попытка закрывается сейчас (строка ещё `issued`, лид не трогали):
        достаточно снять её со счётчика строки. late=True — исход от Binotel обогнал
        событие телефона и попытку уже досчитали как «не ответил»: откатываем закрытие
        строки, повторное открытие выдачи и счётчик лида, а статус лида — если его
        обработала эта попытка. Всё — в транзакции вызывающего."""
        cur.execute("""
            UPDATE dial_list_attempts SET cancelled = TRUE, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND NOT cancelled RETURNING id
        """, (str(attempt_id),))
        if not cur.fetchone():
            return False
        cur.execute("UPDATE dial_list_assignments SET attempts = GREATEST(attempts - 1, 0) WHERE id = %s",
                    (str(assignment_id),))
        if late:
            cur.execute("""
                SELECT a.state, a.lead_id, a.portion_id,
                       (SELECT t.id FROM dial_list_attempts t WHERE t.assignment_id = a.id
                        ORDER BY t.requested_at DESC LIMIT 1)
                FROM dial_list_assignments a WHERE a.id = %s FOR UPDATE
            """, (str(assignment_id),))
            a = cur.fetchone()
            if a and a[0] == "done" and str(a[3]) == str(attempt_id):
                # Строку закрыла именно эта попытка. Вернуть её в работу можно, только
                # если лид не успел уйти в другую открытую выдачу (уникальный индекс).
                cur.execute("SELECT 1 FROM dial_list_assignments WHERE lead_id = %s AND state = 'issued'",
                            (str(a[1]),))
                if not cur.fetchone():
                    cur.execute("""
                        UPDATE dial_list_assignments SET state = 'issued', result = '', done_at = NULL
                        WHERE id = %s
                    """, (str(assignment_id),))
                    cur.execute("UPDATE dial_list_portions SET closed_at = NULL WHERE id = %s", (str(a[2]),))
                # Снова в работу — только лид, который обработала по исчерпанию ЭТА попытка: с ней
                # попыток ровно предел (в SET attempts_total — ещё прежний), без неё — меньше.
                # Обработанный до неё («Позвонить ещё раз»: дозвонились раньше — answered_at, или
                # попытки кончились и без этой — их больше предела, или предел подняли уже после
                # исчерпания — меньше) так и остаётся обработанным — как при подсчёте попытки.
                cur.execute("""
                    UPDATE dial_list_leads l
                    SET attempts_total = GREATEST(attempts_total - 1, 0),
                        status = CASE WHEN status = 'done' AND answered_at IS NULL
                                           AND attempts_total = COALESCE(
                                               (SELECT s.max_attempts FROM dial_list_department_settings s
                                                WHERE s.department_id = l.department_id), %s)
                                      THEN 'in_progress' ELSE status END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (DEFAULT_MAX_ATTEMPTS, str(a[1])))
        log.info("dial_list: попытка %s отменена оператором до ответа водителя (%s)",
                 attempt_id, "с откатом закрытия строки" if late else "строка осталась в работе")
        return True

    def _uncancel_attempt(self, cur, attempt_id, assignment_id):
        """Отменённую по провизорному исходу попытку Binotel позже признал отвеченной:
        разговор был, попытка снова в счёт, итог обязателен."""
        cur.execute("""
            UPDATE dial_list_attempts SET cancelled = FALSE, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND cancelled RETURNING id
        """, (str(attempt_id),))
        if not cur.fetchone():
            return False
        cur.execute("UPDATE dial_list_assignments SET attempts = attempts + 1 WHERE id = %s", (str(assignment_id),))
        cur.execute("SELECT lead_id, state FROM dial_list_assignments WHERE id = %s FOR UPDATE", (str(assignment_id),))
        a = cur.fetchone()
        if a and a[1] == "issued":
            self._close_assignment(cur, str(assignment_id), str(a[0]), "answered", answered=True, count_attempt=True)
        elif a:
            self._touch_lead(cur, str(a[0]), answered=True, count_attempt=True)
        log.info("dial_list: попытка %s снова в счёте — Binotel подтвердил ответ водителя", attempt_id)
        return True

    def _raise_answered(self, cur, attempt_id, disposition, billsec, waitsec, source):
        """Поздний ответ водителя (ANSWER) по попытке, уже закрытой ответом: длительность
        разговора и ожидания только растут, меньшую не пишем никогда (13.10.2026). Первый ответ
        мог прийти из опроса, пока плечо ещё шло, — длительность в нём неполная; вебхук или
        следующий опрос приносят полную. Если с новой длительностью «ответ без разговора»
        (телефон разговора не услышал и 0 с — attempt_talked) стал разговором — строке и лиду
        дозвон (_reanswer_attempt), отменённая попытка снова в счёте (_uncancel_attempt), а итог
        потребует следующий звонок (_pending_outcome). В транзакции вызывающего; True — поменяли."""
        if map_disposition(disposition) != "answered":
            return False
        cur.execute("""
            SELECT assignment_id, disposition, billsec, waitsec, cancelled, auto_hangup, talk_detected
            FROM dial_list_attempts
            WHERE id = %s AND state = 'finished' FOR UPDATE
        """, (str(attempt_id),))
        r = cur.fetchone()
        if not r or map_disposition(r[1]) != "answered":
            return False
        old_billsec, old_waitsec = _to_int(r[2]), _to_int(r[3])
        new_billsec, new_waitsec = max(old_billsec, _to_int(billsec)), max(old_waitsec, _to_int(waitsec))
        if (new_billsec, new_waitsec) == (old_billsec, old_waitsec):
            return False
        cur.execute("""
            UPDATE dial_list_attempts
            SET billsec = %s, waitsec = %s, final_source = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (new_billsec, new_waitsec, source, str(attempt_id)))
        log.info("dial_list: попытка %s — Binotel уточнил разговор: %d → %d с, ожидание %d → %d с (%s)",
                 attempt_id, old_billsec, new_billsec, old_waitsec, new_waitsec, source)
        was = attempt_result(r[1], old_billsec, r[5] or "", r[6])
        now = attempt_result(r[1], new_billsec, r[5] or "", r[6])
        if was != "answered" and now == "answered":
            if r[4]:
                self._uncancel_attempt(cur, str(attempt_id), str(r[0]))
            else:
                self._reanswer_attempt(cur, attempt_id, str(r[0]))
        return True

    def _reanswer_attempt(self, cur, attempt_id, assignment_id):
        """Попытка, засчитанная «ответом без разговора» (недозвоном), по новой длительности —
        разговор: строке, которую закрыла она, — «дозвонились», лиду — дозвон, как у позднего
        ответа поверх провизорного исхода. Попытка уже в счёте. В транзакции вызывающего."""
        cur.execute("""
            SELECT a.state, a.result,
                   (SELECT t.id FROM dial_list_attempts t WHERE t.assignment_id = a.id
                    ORDER BY t.requested_at DESC LIMIT 1)
            FROM dial_list_assignments a WHERE a.id = %s FOR UPDATE
        """, (str(assignment_id),))
        a = cur.fetchone()
        if a and a[0] == "done" and a[1] == "no_answer" and str(a[2]) == str(attempt_id):
            cur.execute("UPDATE dial_list_assignments SET result = 'answered' WHERE id = %s", (str(assignment_id),))
        cur.execute("""
            UPDATE dial_list_leads l
            SET answered_at = COALESCE(l.answered_at, CURRENT_TIMESTAMP),
                status = CASE WHEN l.status IN ('new', 'in_progress') THEN 'done' ELSE l.status END,
                updated_at = CURRENT_TIMESTAMP
            FROM dial_list_assignments a
            WHERE a.id = %s AND l.id = a.lead_id
        """, (str(assignment_id),))
        log.info("dial_list: попытка %s — с полной длительностью это разговор: дозвон засчитан", attempt_id)

    def _outcome_decision(self, cur, attempt_id):
        """(cancelled, outcome_required) по попытке — что телефону делать после разговора.
        outcome_required: True — открыть окно итога; False — итог не нужен; None — исход
        от АТС ещё не известен (оператор сам положил трубку, ждём Binotel).

        Сброс на гудках (ring_timeout_at) решается как отбой оператора, только без
        отмены: водитель ответил — итог нужен, не ответил — не нужен, исхода нет — None.

        Плечо телефон не подтвердил (leg_answered_at пуст — ACK не дошёл), а Binotel
        подтвердил ответ водителя — разговор был, итог нужен (как в _pending_outcome).

        Телефон с 3.22.34 сообщает, что услышал (12.10.2026). Сообщение сети (auto_hangup
        'announcement') — итог не нужен сразу, исхода от АТС не ждём. Разговор был
        (talk_detected True) — итог нужен сразу, кто бы ни положил трубку: оператор,
        закончивший разговор, не ждёт исхода Binotel (он приходит через секунды, бывало —
        через минуту). Разговора телефон не услышал (talk_detected False) — обычный конец
        плеча решается как сброс на гудках: Binotel подтвердил ответ — нужен, не подтвердил —
        не нужен, исхода нет — None. Что считать ответом — attempt_result. talk_detected
        None (прежний телефон) — как было. Сброс на гудках решается по Binotel при любом
        talk_detected: плечо положил сам телефон — значит, разговора, по его же мнению, не
        было. Плечо без ACK — тоже по Binotel: подтверждённого плеча, на котором телефон
        слушал бы разговор, не было.

        Разговор, который слышал телефон, не перебивает финальное «не дозвонились» Binotel
        (ревью 13.10.2026; NOT_ANSWERED_FINAL_DISPOSITIONS): реплика оператора в паузе сообщения
        сети похожа для телефона на обмен репликами, а трубку никто не снимал. Тогда итог не нужен
        (False), хоть телефон и открыл окно итога по своему разговору; сохранённый итог остаётся.
        У «ключа нет» (прежний телефон) — как было."""
        cur.execute("""
            SELECT state, disposition, cancelled, operator_hangup_at, leg_answered_at, outcome_id,
                   ring_timeout_at, auto_hangup, talk_detected, billsec
            FROM dial_list_attempts WHERE id = %s
        """, (str(attempt_id),))
        r = cur.fetchone()
        if not r:
            return False, False
        (state, disposition, cancelled, hung_up, leg_answered, outcome_id, ring_timeout_at,
         auto_hangup, talk_detected, billsec) = r
        if cancelled:
            return True, False
        if outcome_id is not None or state == "failed":
            return False, False
        if auto_hangup == AUTO_HANGUP_ANNOUNCEMENT:
            return False, False
        answered = state == "finished" and attempt_result(disposition, billsec, auto_hangup, talk_detected) == "answered"
        if leg_answered is None:
            return False, answered
        if ring_timeout_at is None and (talk_detected is True or (hung_up is None and talk_detected is None)):
            if talk_detected is True and state == "finished" and binotel_not_answered(disposition):
                return False, False
            return False, True
        if state == "finished":
            return False, answered
        return False, None

    # ------------------------------------------------------------ вебхук
    def webhook_verdict(self, token, remote_addr):
        """(принять ли вебхук, почему нет). Принимаем только с правильным токеном из
        окружения сервера (DIAL_LIST_WEBHOOK_TOKEN, не короче 16 символов, сравнение
        постоянного времени) И — по умолчанию — только с адресов серверов Binotel. Токен не
        задан → вебхук выключен целиком: исходы доберёт опрос call-details.

        Причина отказа — для журнала сервера (WARNING в ручке вебхука): в ней адрес, с
        которого пришёл запрос, но никогда — токен, ни присланный, ни ожидаемый."""
        expected = webhook_env_token()
        if len(expected) < WEBHOOK_TOKEN_MIN_LEN:
            return False, f"вебхук выключен: {WEBHOOK_TOKEN_ENV} не задан или короче {WEBHOOK_TOKEN_MIN_LEN} символов"
        presented = str(token or "").strip()
        if not presented:
            return False, "в адресе вебхука нет token"
        if not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
            return False, "token не совпал"
        addr = str(remote_addr or "").strip()
        if webhook_requires_binotel_ip() and addr not in BINOTEL_SERVER_IPS:
            return False, (f"адрес {addr or '(пусто)'} не из списка серверов Binotel "
                           f"(если Binotel шлёт с нового адреса — снять проверку: {WEBHOOK_REQUIRE_BINOTEL_IP_ENV}=0)")
        return True, ""

    def webhook_allowed(self, token, remote_addr):
        """Принять ли вебхук (webhook_verdict без причины)."""
        return self.webhook_verdict(token, remote_addr)[0]

    def handle_webhook(self, form, remote_addr=""):
        """POST «API Call Completed» от Binotel: исход звонка — финал попытки.

        Поля — как их шлёт Binotel: формой PHP-вида (callDetails[generalCallID],
        callDetails[disposition], callDetails[billsec], callDetails[waitsec] …) либо JSON
        ({"callDetails": {…}}); прежние плоские поля (generalCallID, disposition, …) тоже
        понимаем (webhook_fields, webhook_value). Вебхук шлют, когда звонок кончился, поэтому
        его ответ водителя — финал при любом состоянии плеча (в отличие от ANSWER опроса), а
        поздний — только уточняет длительность (_raise_answered). Финал ложится и в общий кэш
        живых звонков компании: карточка звонка и добор исходов видят его сразу."""
        data = webhook_fields(form)
        general_call_id = str(webhook_value(data, "generalCallID") or "").strip()[:32]
        disposition = str(webhook_value(data, "disposition") or "").strip().upper()
        billsec = _to_int(webhook_value(data, "billsec"))
        waitsec = _to_int(webhook_value(data, "waitsec"))
        matched = False
        if general_call_id:
            with self.db._get_cursor() as cur:
                cur.execute("""
                    SELECT t.id, t.state, COALESCE(NULLIF(TRIM(s.binotel_company), ''), %s)
                    FROM dial_list_attempts t
                    JOIN dial_list_assignments a ON a.id = t.assignment_id
                    JOIN dial_list_leads l ON l.id = a.lead_id
                    LEFT JOIN dial_list_department_settings s ON s.department_id = l.department_id
                    WHERE t.general_call_id = %s
                """, (DEFAULT_BINOTEL_COMPANY, general_call_id))
                row = cur.fetchone()
            if row:
                matched = True
                if map_disposition(disposition):
                    company = row[2] if len(row) > 2 else DEFAULT_BINOTEL_COMPANY
                    self._company_calls(company).put(general_call_id, {
                        "general_call_id": general_call_id, "disposition": disposition,
                        "billsec": billsec, "waitsec": waitsec})
                    # Уже закрытую попытку _finish_attempt дописывает, только если она закрылась
                    # без настоящего исхода (таймаут, промежуточный статус) или ответом с меньшей
                    # длительностью.
                    self._finish_attempt(str(row[0]), disposition, billsec, waitsec, "webhook")
        # Номер водителя и данные сотрудника в журнал сервера не пишем — только звонок и исход.
        log.info("dial_list: вебхук Binotel с %s: generalCallID=%s, %s, разговор %d с — %s", remote_addr or "?",
                 general_call_id or "—", disposition or "без исхода", billsec,
                 "попытка обзвона найдена" if matched else "это не звонок обзвона")
        payload = json.dumps(data, ensure_ascii=False)
        if len(payload) > WEBHOOK_PAYLOAD_MAX_CHARS:
            payload = json.dumps({"_truncated": True, "generalCallID": general_call_id,
                                  "disposition": disposition}, ensure_ascii=False)
        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO dial_list_webhook_log (remote_addr, general_call_id, matched, payload)
                VALUES (%s, %s, %s, %s::jsonb)
            """, (str(remote_addr or "")[:64], general_call_id, matched, payload))
            cur.execute("""
                DELETE FROM dial_list_webhook_log
                WHERE received_at < CURRENT_TIMESTAMP - make_interval(days => %s)
            """, (WEBHOOK_LOG_KEEP_DAYS,))
        return {"matched": matched, "general_call_id": general_call_id, "disposition": disposition}

    # ------------------------------------------------------------ подписание документов
    def request_sign_check(self, reason=""):
        """Поставить проверку подписания в очередь своего потока, не дожидаясь её
        (планировщик раз в полчаса, загрузка базы). См. sign_check.py."""
        return self.sign_checker.request(reason)

    def check_lead_signature(self, lead_id):
        """«Проверить сейчас» в карточке водителя: один запрос в Sapar, сразу, из
        базы любого месяца. Подписавшего не перепроверяем — он из обзвона ушёл."""
        lead_id = str(lead_id)
        with self.db._get_cursor() as cur:
            cur.execute("SELECT iin, signed_at FROM dial_list_leads WHERE id = %s", (lead_id,))
            row = cur.fetchone()
        if not row:
            raise DialListError("Водитель не найден в журнале", 404)
        if not row[0]:
            raise DialListError("У водителя нет ИИН — подписание не проверить. Загрузите его заново "
                                "в файле с колонкой iin", 409)
        if row[1] is None:
            if not self.sign_checker.configured():
                raise DialListError("Проверка подписания не настроена на сервере", 503)
            summary = self.sign_checker.run(reason="карточка водителя", lead_ids=[lead_id], force=True,
                                            budget_sec=30)
            if summary.get("errors") and not summary.get("checked"):
                card = self.lead_card(lead_id)
                raise DialListError(f"Sapar не ответил: {card['sign']['error'] or 'повторите позже'}", 502)
        return self.lead_card(lead_id)

    # «Проверить на подписание» с телефона (владелец, 12.10.2026: «и во время звонка проверить
    # без передачи данных на телефон — вернуть статус подписан или нет»). Телефону — только
    # статус, подпись и время проверки: ни ИИН (даже маской), ни номера, ни документов, ни
    # текста ошибки Sapar. Ответ ручки руководителя (check_lead_signature → lead_card) несёт
    # маски и документы — оператору он не годится. Водитель — любой, кто хоть раз был в списке
    # этого оператора, в том числе из отдела, где оператор работал раньше (13.10.2026): на
    # вкладках итогов они остаются, и кнопка не должна отвечать «не найден». ИИН нужен лишь
    # запросу в Sapar и из сервера не уходит.
    _OPERATOR_SIGN_LEAD_SQL = """
        SELECT l.id, l.iin, l.period, l.sign_status, l.sign_checked_at, l.sign_error, l.signed_at
        FROM dial_list_leads l
        WHERE l.id = %s
          AND EXISTS (SELECT 1 FROM dial_list_assignments a
                      WHERE a.lead_id = l.id AND a.operator_id = %s)
    """
    # Проверки по кнопке (time.monotonic): когда о водителе спрашивали Sapar
    # (_sign_asked, не чаще раза в SIGN_OPERATOR_MIN_INTERVAL_SEC) и когда спрашивал каждый
    # оператор (_sign_operator_asks — SIGN_OPERATOR_GAP_SEC, SIGN_OPERATOR_PER_MINUTE), плюс
    # места для одновременных проверок (_sign_now_slots, SIGN_OPERATOR_CONCURRENT). На класс:
    # приложение — один процесс с потоками, и клики двух потоков должны видеть одни отметки.
    _sign_asked = {}
    _sign_operator_asks = {}
    _sign_asked_lock = threading.Lock()
    _sign_now_slots = threading.BoundedSemaphore(SIGN_OPERATOR_CONCURRENT)

    def _operator_sign_lead(self, ctx, lead_id):
        """Водитель для «Проверить на подписание» (_OPERATOR_SIGN_LEAD_SQL) или None."""
        with self.db._get_cursor() as cur:
            cur.execute(self._OPERATOR_SIGN_LEAD_SQL, (lead_id, ctx["user_id"]))
            r = cur.fetchone()
        if not r:
            return None
        return {"id": str(r[0]), "iin": str(r[1] or "").strip(), "period": r[2], "sign_status": r[3] or "",
                "checked_at": r[4], "error": str(r[5] or "").strip(), "signed_at": r[6]}

    def _sign_ask_slot(self, operator_id, lead_id):
        """Можно ли спросить Sapar по кнопке сейчас → (да или нет, почему нет — для журнала).

        Три предела: об одном водителе — не чаще раза в SIGN_OPERATOR_MIN_INTERVAL_SEC (двойной
        клик, нетерпеливые повторы); от одного оператора — не чаще раза в SIGN_OPERATOR_GAP_SEC
        и не больше SIGN_OPERATOR_PER_MINUTE в минуту; на весь сервер — не больше
        SIGN_OPERATOR_CONCURRENT проверок сразу, без ожидания места. Можно — отметки ставятся
        сразу, до запроса (второй клик, пока идёт первый, Sapar уже не спросит), и занимается
        место: вызывающий обязан отпустить его (_sign_now_slots.release())."""
        now = time.monotonic()
        operator_id = int(operator_id)
        with self._sign_asked_lock:
            asked = self._sign_asked.get(lead_id)
            if asked is not None and now - asked < SIGN_OPERATOR_MIN_INTERVAL_SEC:
                return False, "Sapar о нём только что спрашивали"
            recent = [at for at in self._sign_operator_asks.get(operator_id, ()) if now - at < 60]
            if recent and now - recent[-1] < SIGN_OPERATOR_GAP_SEC:
                return False, f"оператор спрашивает чаще раза в {SIGN_OPERATOR_GAP_SEC} с"
            if len(recent) >= SIGN_OPERATOR_PER_MINUTE:
                return False, f"оператор спросил {len(recent)} раз за минуту"
            if not self._sign_now_slots.acquire(blocking=False):
                return False, f"уже идут {SIGN_OPERATOR_CONCURRENT} проверки сразу"
            if len(self._sign_asked) > 500:
                for key in [k for k, v in self._sign_asked.items() if now - v >= SIGN_OPERATOR_MIN_INTERVAL_SEC]:
                    self._sign_asked.pop(key, None)
            if len(self._sign_operator_asks) > 500:
                for key in [k for k, v in self._sign_operator_asks.items() if not v or now - v[-1] >= 60]:
                    self._sign_operator_asks.pop(key, None)
            self._sign_asked[lead_id] = now
            self._sign_operator_asks[operator_id] = recent + [now]
        return True, ""

    def _close_signed_row(self, user_id, lead_id):
        """Открытую строку подписавшего водителя — со списка, ЧЬЯ БЫ она ни была (13.10.2026),
        как снимают их прогон (SignChecker.close_signed_rows) и звонок подписавшему
        (start_call): «другое», выдача закрывается, если обрабатывать в ней больше нечего
        (_close_row). Идущий звонок не трогаем — строку закроет он сам.

        Своей короткой транзакцией под замком владельца строки (_lock_operator: его «Ещё» и
        «Позвонить ещё раз» не разойдутся с этим). Сначала строка запирается (FOR UPDATE OF a),
        и только потом отдельным запросом проверяются попытки «в полёте»: в одном запросе
        NOT EXISTS смотрел бы на снимок до замка, и звонок, начатый, пока ждали замок
        (start_call держит ту же строку), остался бы незамеченным — строку закрыли бы посреди
        разговора. True — закрыли строку ЭТОГО оператора (у другого — снимаем молча)."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT a.id, a.operator_id FROM dial_list_assignments a
                WHERE a.lead_id = %s AND a.state = 'issued'
                ORDER BY a.created_at DESC LIMIT 1
            """, (str(lead_id),))
            found = cur.fetchone()
            if not found:
                return False
            self._lock_operator(cur, found[1])
            cur.execute("""
                SELECT a.id, a.operator_id FROM dial_list_assignments a
                WHERE a.id = %s AND a.state = 'issued'
                FOR UPDATE OF a
            """, (str(found[0]),))
            row = cur.fetchone()
            if not row:
                return False
            cur.execute("""
                SELECT 1 FROM dial_list_attempts t
                WHERE t.assignment_id = %s AND t.state IN %s
                LIMIT 1
            """, (str(row[0]), IN_FLIGHT_STATES))
            if cur.fetchone():
                return False
            self._close_row(cur, row[0])
        mine = int(row[1]) == int(user_id)
        if not mine:
            log.info("dial_list: подписавший водитель %s снят со списка оператора %s (проверял оператор %s)",
                     lead_id, row[1], user_id)
        return mine

    def operator_sign_check(self, user_id, lead_id):
        """«Проверить на подписание» с телефона — в любой момент, и посреди разговора: ни
        идущий звонок, ни неуказанный итог не мешают.

        Порядок: подписал (signed_at) — Sapar не спрашиваем, подпись окончательна; нет ИИН —
        «не удалось проверить»; Sapar не настроен — последнее известное из базы; «на проверке
        у Яндекса» (уже подписал) по проверке моложе SIGN_OPERATOR_FRESH_SEC — ответ из базы.
        Всё остальное — «не подписал» и «не удалось проверить» — из базы свежим не выдаём
        (13.10.2026: водитель мог подписать минуту назад): Sapar сейчас (SignChecker.check_now:
        итог в базу, успешка — новому подписавшему), вне транзакций, в пределах _sign_ask_slot
        (водитель, оператор, одновременные); предел исчерпан — последнее известное с честным
        «только что проверяли» и fresh=false. Sapar не ответил, лежит или не успел —
        последнее известное до запроса.

        Подписал или «на проверке у Яндекса» — его открытая строка снимается со списка
        (_close_signed_row), у кого бы она ни была, кроме строки с идущим звонком. Ответ —
        lead_id, sign (status, signed, label, checked_at, checked_label, fresh), row_closed —
        снята строка ЭТОГО оператора, text; fresh — статус сейчас верный (Sapar ответил,
        подпись окончательна или свежая проверка «на проверке у Яндекса»)."""
        ctx = self.operator_context(user_id)
        try:
            lead_id = str(uuid.UUID(str(lead_id)))
        except (ValueError, AttributeError, TypeError):
            raise DialListError("Водитель не найден", 404)
        lead = self._operator_sign_lead(ctx, lead_id)
        if lead is None:
            raise DialListError("Водитель не найден", 404)
        checker = self.sign_checker
        now = _utcnow()
        shown, fresh, text = lead, False, ""
        if lead["signed_at"] is not None:
            fresh, how = True, "подписал раньше"
        elif not lead["iin"]:
            text, how = SIGN_OPERATOR_NO_IIN, "нет ИИН"
        elif not checker.configured():
            text, how = SIGN_OPERATOR_NOT_CONFIGURED, SIGN_NOW_STOPPED["not_configured"]
        elif (lead["sign_status"] == signing.PROCESSING
              and not sign_check_due(lead["iin"], lead["checked_at"], lead["error"], now, SIGN_OPERATOR_FRESH_SEC / 60)):
            fresh, how = True, "на проверке у Яндекса, проверка свежая"
        else:
            allowed, refusal = self._sign_ask_slot(ctx["user_id"], lead_id)
            if not allowed:
                text, how = SIGN_OPERATOR_THROTTLED, refusal
            else:
                try:
                    summary = checker.check_now([(lead_id, lead["iin"], lead["period"])], now=now,
                                                timeout=SIGN_OPERATOR_TIMEOUT_SEC, workers=1,
                                                budget_sec=SIGN_OPERATOR_BUDGET_SEC, max_systemic=1)
                except Exception:
                    log.exception("dial_list: проверка подписания с телефона (водитель %s) упала", lead_id)
                    summary = {"checked": 0, "stopped": "", "elapsed_sec": 0.0}
                finally:
                    self._sign_now_slots.release()
                if summary["checked"]:
                    shown = self._operator_sign_lead(ctx, lead_id) or lead
                    fresh, how = True, f"Sapar ответил за {summary.get('elapsed_sec', 0.0):.1f} с"
                else:
                    text = SIGN_OPERATOR_SAPAR_DOWN
                    how = SIGN_NOW_STOPPED.get(summary.get("stopped")) or "Sapar не проверил"
        status, signed, label = operator_sign_status(shown["sign_status"] if shown["iin"] else "", shown["signed_at"])
        # Время — только у проверки без ошибки: отказ Sapar по ИИН тоже сдвигает
        # sign_checked_at, и статус прошлой проверки получил бы чужое время.
        checked_at = None if shown["error"] else shown["checked_at"]
        row_closed = False
        if status in ("signed", "processing"):
            try:
                row_closed = self._close_signed_row(ctx["user_id"], lead_id)
            except Exception:
                # Не повод терять ответ: строку снимет звонок подписавшему (start_call).
                log.exception("dial_list: строка подписавшего водителя %s не снята (проверял оператор %s)",
                              lead_id, ctx["user_id"])
        if row_closed:
            text = SIGNED_ROW_CLOSED
        log.info("dial_list: оператор %s проверил подписание водителя %s: %s (%s)%s", ctx["user_id"], lead_id,
                 status, how, "; строка снята со списка" if row_closed else "")
        today = now.astimezone(PERIOD_TZ).date()
        return {
            "lead_id": lead_id,
            "sign": {"status": status, "signed": signed, "label": label, "checked_at": _iso_utc(checked_at),
                     "checked_label": checked_at_label(checked_at, today), "fresh": fresh},
            "row_closed": row_closed,
            "text": text,
        }

    def analytics(self, department_id, period=None):
        """Вкладка «Аналитика»: воронка, дни, операторы и проверка подписания за
        базу месяца (period — date первого дня; пусто — обзваниваемый месяц)."""
        from . import analytics as dial_analytics
        department_id = int(department_id)
        settings = self.department_settings(department_id)
        period = period or settings["_period"]
        with self.db._get_cursor() as cur:
            data = dial_analytics.department_analytics(cur, department_id, period,
                                                       configured=self.sign_checker.configured())
        doc_month = datetime.strptime(data["sign_check"]["doc_month"], "%Y-%m-%d").date()
        data["sign_check"]["doc_month_label"] = period_label(doc_month)
        data.update({
            "department_id": department_id,
            "period": period.isoformat(),
            "period_label": period_label(period),
            "active_period": settings["period"],
            "is_active_period": period == settings["_period"],
        })
        return data

    # ------------------------------------------------------------ замер распознавания сообщения сети
    # Ревью 13.10.2026: сообщение сети, распознанное по речи, не отличить надёжно от мелодии вместо
    # гудков, и в режиме auto телефон рвал бы такие звонки. Пока режим — suggest (подсказка
    # оператору), телефон сообщает, сработало ли распознавание (announcement_suspected), а Binotel —
    # чем звонок кончился на самом деле. Эти числа владелец и смотрит, прежде чем включать auto:
    # плечо, которое не клал ни телефон, ни оператор по подсказке, кончилось у Binotel финалом сети
    # (занято, канал недоступен, перегрузка) — распознано верно; «не ответил» (звонил до конца) —
    # это была мелодия; ответ с разговором — живой человек. Номеров, ИИН и ФИО здесь нет — только числа.
    ANNOUNCEMENT_STATS_DAYS = 14
    ANNOUNCEMENT_STATS_DAYS_MAX = 92
    ANNOUNCEMENT_LONG_ANSWER_SEC = 10
    _NETWORK_FINAL_DISPOSITIONS = BUSY_DISPOSITIONS | frozenset({"CHANUNAVAIL", "CONGESTION"})
    _CANCEL_DISPOSITIONS = frozenset({"CANCEL", "CANCELLED", "CANCELED"})
    _ANNOUNCEMENT_STATS_SQL = """
        SELECT t.announcement_suspected,
               CASE WHEN t.auto_hangup = 'announcement' AND t.announcement_by IN ('operator', 'sit')
                        THEN t.announcement_by
                    WHEN t.auto_hangup = 'announcement' THEN 'auto'
                    ELSE 'none' END,
               CASE WHEN t.state = 'finished' THEN UPPER(t.disposition) ELSE '' END,
               COALESCE(t.talk_detected, FALSE),
               (t.state = 'finished' AND t.billsec >= %(long_sec)s),
               COUNT(*)
        FROM dial_list_attempts t
        JOIN dial_list_assignments a ON a.id = t.assignment_id
        JOIN dial_list_leads l ON l.id = a.lead_id
        WHERE l.department_id = %(department_id)s
          AND t.requested_at >= CURRENT_TIMESTAMP - make_interval(days => %(days)s)
          AND t.announcement_suspected IS NOT NULL
          AND """ + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL + """
        GROUP BY 1, 2, 3, 4, 5
    """

    def _final_group(self, disposition):
        """Финал Binotel → группа замера: network | answered | no_answer | cancel | other | pending."""
        value = str(disposition or "").strip().upper()
        if not value:
            return "pending"
        if value in self._NETWORK_FINAL_DISPOSITIONS:
            return "network"
        if value in ANSWERED_DISPOSITIONS:
            return "answered"
        if value in self._CANCEL_DISPOSITIONS:
            return "cancel"
        if value in NO_ANSWER_DISPOSITIONS:
            return "no_answer"
        return "other"

    def announcement_stats(self, department_id, days=None):
        """Точность распознавания сообщения сети по речи за последние days дней (по умолчанию
        ANNOUNCEMENT_STATS_DAYS, не больше ANNOUNCEMENT_STATS_DAYS_MAX) — по плечам телефонов,
        которые сообщают announcement_suspected. Для руководителя: только числа.

            analysed       плеч, по которым телефон сообщил, срабатывало ли распознавание
            suspected      из них сработало хоть раз
            ended_by       кто положил подозрительное плечо: auto / sit — телефон сам, operator —
                           оператор по подсказке, none — никто из них (водитель, сеть, обычный отбой)
            finals         чем кончились у Binotel подозрительные плечи, которые никто из нас не клал
                           (настоящий ответ Binotel): network, answered, no_answer, cancel, other,
                           pending (исхода ещё нет)
            finals_ended_by_us  то же у плеч, положенных телефоном или оператором по подсказке
                           (Binotel тогда чаще пишет отмену — по ним точность не судят)
            precision      по finals: network — верно (сообщение сети), no_answer — мелодия вместо
                           гудков (ошибка), answered_talk — потом был разговор (живой человек,
                           ошибка), answered_silent — ответ без разговора (автоответчик или молчащий
                           водитель); percent — network от всех четырёх, нижняя оценка
            network_missed плеч с финалом сети, где распознавание по речи не сработало и плечо
                           никто из нас не клал (сообщение сети, которое пропустили)"""
        department_id = int(department_id)
        try:
            days = int(days) if days not in (None, "") else self.ANNOUNCEMENT_STATS_DAYS
        except (TypeError, ValueError):
            raise DialListError("days: число дней")
        if not 1 <= days <= self.ANNOUNCEMENT_STATS_DAYS_MAX:
            raise DialListError(f"days: от 1 до {self.ANNOUNCEMENT_STATS_DAYS_MAX}")
        with self.db._get_cursor() as cur:
            cur.execute(self._ANNOUNCEMENT_STATS_SQL, {"department_id": department_id, "days": days,
                                                       "long_sec": self.ANNOUNCEMENT_LONG_ANSWER_SEC})
            rows = cur.fetchall()
        groups = ("network", "answered", "no_answer", "cancel", "other", "pending")
        finals, by_us = dict.fromkeys(groups, 0), dict.fromkeys(groups, 0)
        ended_by = {"auto": 0, "sit": 0, "operator": 0, "none": 0}
        precision = {"network": 0, "no_answer": 0, "answered_talk": 0, "answered_silent": 0}
        analysed = suspected = talked_after = answered_long = missed = 0
        dispositions = {}
        for flag, who, disposition, talked, long_answer, count in rows:
            count = int(count or 0)
            analysed += count
            group = self._final_group(disposition)
            if not flag:
                if group == "network" and who == "none":
                    missed += count
                continue
            suspected += count
            who = who if who in ended_by else "none"
            ended_by[who] += count
            if talked:
                talked_after += count
            if who != "none":
                by_us[group] += count
                continue
            finals[group] += count
            if disposition:
                dispositions[disposition] = dispositions.get(disposition, 0) + count
            if group == "answered":
                precision["answered_talk" if talked else "answered_silent"] += count
                if long_answer:
                    answered_long += count
            elif group in ("network", "no_answer"):
                precision[group] += count
        decided = sum(precision.values())
        ms = announcement_ms()
        return {
            "department_id": department_id,
            "days": days,
            "since": _iso_utc(_utcnow() - timedelta(days=days)),
            "settings": {"announcement_mode": announcement_mode(ms), "announcement_sit": announcement_sit_enabled(),
                         "announcement_ms": ms},
            "analysed": analysed,
            "suspected": suspected,
            "ended_by": ended_by,
            "finals": finals,
            "finals_ended_by_us": by_us,
            "talked_after": talked_after,
            "answered_long": answered_long,
            "precision": dict(precision, decided=decided,
                              percent=round(100.0 * precision["network"] / decided, 1) if decided else None),
            "dispositions": [{"disposition": d, "count": c}
                             for d, c in sorted(dispositions.items(), key=lambda kv: (-kv[1], kv[0]))],
            "network_missed": missed,
        }

    # ------------------------------------------------------------ руководитель
    def overview(self, department_ids, day):
        """Сводка за день по операторам: сколько строк выдано/обработано, попыток,
        дозвонов, секунд разговора, успешек (водитель подписал в этот день и
        подпись засчитана оператору). department_ids=None — все отделы."""
        params = {"day": day}
        # Отбор — по отделу БАЗЫ, которую обзванивали, а не по отделу человека: на линии
        # отдела может сидеть сотрудник другого отдела, и его звонки — показатели отдела
        # линии. Отбор по users.department_id такого оператора из сводки терял.
        # Лид с номером «Реестра тестовых номеров» — проверка обзвона: ни попыток, ни
        # выдачи, ни успешки по нему в сводке нет. Сообщение сети, которое положил телефон
        # (ATTEMPT_TALK_SQL), — попытка, но не дозвон и не минуты разговора.
        lead_filter = "AND " + test_keys.DIAL_LIST_LEAD_NOT_TEST_SQL
        if department_ids is not None:
            lead_filter += " AND l.department_id = ANY(%(departments)s)"
            params["departments"] = [int(d) for d in department_ids]
        with self.db._get_cursor() as cur:
            cur.execute(f"""
                WITH att AS (
                    SELECT t.operator_id,
                           COUNT(*) AS attempts,
                           COUNT(*) FILTER (WHERE t.state = 'finished'
                                            AND UPPER(t.disposition) IN ('ANSWER','ANSWERED','SUCCESS','VM-SUCCESS')
                                            AND {ATTEMPT_TALK_SQL}) AS answered,
                           COALESCE(SUM(t.billsec) FILTER (WHERE t.state = 'finished'
                                                           AND {ATTEMPT_TALK_SQL}), 0) AS talk_sec,
                           COUNT(*) FILTER (WHERE t.state = 'failed') AS failed,
                           COUNT(*) FILTER (WHERE t.cancelled) AS cancelled
                    FROM dial_list_attempts t
                    JOIN dial_list_assignments a ON a.id = t.assignment_id
                    JOIN dial_list_leads l ON l.id = a.lead_id
                    WHERE (t.requested_at AT TIME ZONE 'Asia/Almaty')::date = %(day)s {lead_filter}
                    GROUP BY t.operator_id
                ), asg AS (
                    SELECT a.operator_id,
                           COUNT(*) AS issued,
                           COUNT(*) FILTER (WHERE a.state = 'done') AS done
                    FROM dial_list_assignments a
                    JOIN dial_list_leads l ON l.id = a.lead_id
                    WHERE (a.created_at AT TIME ZONE 'Asia/Almaty')::date = %(day)s {lead_filter}
                    GROUP BY a.operator_id
                ), suc AS (
                    SELECT l.success_operator_id AS operator_id, COUNT(*) AS successes
                    FROM dial_list_leads l
                    WHERE l.success_attempt_id IS NOT NULL
                      AND (l.signed_at AT TIME ZONE 'Asia/Almaty')::date = %(day)s {lead_filter}
                    GROUP BY l.success_operator_id
                )
                SELECT u.id, u.name, u.department_id, dep.name,
                       COALESCE(asg.issued, 0), COALESCE(asg.done, 0),
                       COALESCE(att.attempts, 0), COALESCE(att.answered, 0),
                       COALESCE(att.talk_sec, 0), COALESCE(att.failed, 0), COALESCE(att.cancelled, 0),
                       COALESCE(suc.successes, 0)
                FROM users u
                LEFT JOIN departments dep ON dep.id = u.department_id
                LEFT JOIN att ON att.operator_id = u.id
                LEFT JOIN asg ON asg.operator_id = u.id
                LEFT JOIN suc ON suc.operator_id = u.id
                WHERE (att.operator_id IS NOT NULL OR asg.operator_id IS NOT NULL
                       OR suc.operator_id IS NOT NULL)
                ORDER BY dep.name NULLS LAST, u.name
            """, params)
            rows = [{
                "operator_id": r[0], "operator_name": r[1] or "", "department_id": r[2],
                "department_name": r[3] or "", "issued": int(r[4]), "done": int(r[5]),
                "attempts": int(r[6]), "answered": int(r[7]), "talk_sec": int(r[8]), "failed": int(r[9]),
                # Отменённые оператором до ответа водителя — в attempts входят, но не в счёт лидам.
                "cancelled": int(r[10]),
                "successes": int(r[11]),
            } for r in cur.fetchall()]
        return rows


def _to_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return default

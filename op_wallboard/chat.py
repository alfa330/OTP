# -*- coding: utf-8 -*-
"""«Табло ОП» · «Чат»: чаты верификаторов в Wazzup (задача #367).

Второе направление «Табло ОП», как «Чат» у табло СЗоВ, но источник другой, и это меняет
половину экрана:

  * переписка приходит вебхуком Wazzup в `wazzup_messages` (аккаунт «op») за секунды, поэтому
    табло считается из своей базы, без квоты вендора и без внешних запросов;
  * статусов людей у Wazzup нет ни в API, ни в вебхуках, и статусы на табло не показываем
    вовсе (решение владельца 25.09.2026). «Сейчас» — это чаты: в работе и ждущие ответа;
  * обращения с началом и закрытием у Wazzup тоже нет — есть поток сообщений по чату. Его
    режем на ДИАЛОГИ паузой (6 ч, та же единица, что у эпизодов ИИ-оценки: пауза
    бимодальна, дно между пиками 4–11 ч). Диалог относится к дню и часу своего начала — как
    обращение Chat2Desk у СЗоВ.

Правила ответа — те же, что у строителя обращений СЗоВ (`build_chat_webhook_request_rows`):
первое сообщение клиента после ответа ждёт; ответ закрывает ожидание и даёт одну задержку;
второе сообщение сотрудника подряд ответом не считается — отвечать не на что. «Первый ответ»
— первая такая задержка диалога, «ответ внутри чата» — среднее всех задержек диалога
(первая входит, как в колонке `average_replies_time`), а за день — среднее по диалогам.

Отличие одно, и оно вынужденное: в чате верификаторов пишут по двое (замер 18–24.09.2026 —
44 % «чат × день»), поэтому в разрезе по людям задержка засчитывается ТОМУ, КТО ОТВЕТИЛ, а не
«оператору обращения». Итог направления считается по диалогам, как у СЗоВ по обращениям, и
потому в диалогах на двоих не равен сумме личных цифр. На табло только направление
верификаторов (решение владельца): ответы сотрудников других групп, которые пишут из того же
аккаунта, в итог не идут, а диалог, где писали только они, не считается чатом верификаторов.

Рассылки (сообщения без привязанного сотрудника: «Admin», «ЯР», «Отток группа», боты) в
расчёт не идут вовсе: они не ответ клиенту и не начало разговора.

Модуль чистый — считает по спискам словарей и проверяется без базы (tests/test_op_chat_wallboard.py).
"""

from collections import defaultdict
from datetime import datetime, timedelta

# Группа верификаторов определяется моделью группы, а не направлением: часть верификаторов
# числится в направлении «Основа ОП». Та же модель, что убирает их с телефонного табло
# (snapshot.OFF_BOARD_GROUP_MODELS).
VERIFIER_GROUP_MODEL = 'op_verificator'

# Диалог заканчивается тишиной такой длины (см. докстринг модуля).
EPISODE_GAP_SECONDS = 6 * 3600
# «В работе» — последнее сообщение в чате не старше этого (решение владельца 25.09.2026:
# 95 % пауз внутри чата короче 15 минут).
IN_WORK_SECONDS = 15 * 60
# Клиенту, которому не ответил никто, пауза в шесть часов диалог НЕ закрывает, пока с его первой
# реплики прошло не больше двенадцати часов: написал в 02:00, ответили в 09:00 — это первый ответ
# через семь часов, а не два диалога, в которых семь часов не видны нигде. Дольше — уже новое
# обращение: замер 12–25.09.2026 — из 56 таких пар медиана паузы 45 ч (сотрудник пишет клиенту
# через дни), и склеивать их значило бы подмешать в среднее ответы, которых не было.
UNANSWERED_REPLY_MAX_SECONDS = 12 * 3600
# «Ждёт ответа» — клиент написал, ответа нет, и ждёт он не дольше часа. Без потолка плитка
# держала бы весь день десяток «Рахмет» и «👍» пятичасовой давности: Wazzup не сообщает, что
# сотрудник нажал «Отвечать не нужно», а замер 18–24.09.2026 показал, что из 151 диалога,
# закрытого неотвеченной репликой клиента, заметная часть — именно такие благодарности. Тот,
# кто ждёт дольше часа, со стены уходит; в итоги дня он всё равно попадает как чат без ответа.
WAITING_MAX_SECONDS = 60 * 60
# Нормы владельца 25.09.2026: первый ответ — минута, ответ внутри чата — четыре.
FIRST_TARGET_SECONDS = 60
INNER_TARGET_SECONDS = 240

# Поток вебхука «замолчал». Днём сообщения идут непрерывно (замер 11–24.09.2026: с 09 до 24
# самая долгая тишина — 11 минут), ночью тишина в полтора часа — норма (до 98 минут в 04–05).
# Один порог на сутки либо будил бы ночью, либо проспал бы дневной обрыв.
# Порог выбирается по часу, в который тишина НАЧАЛАСЬ: ночная пауза с 08:40 не становится
# «обрывом» в 09:00 только потому, что наступил день.
STREAM_SILENT_DAY_SECONDS = 20 * 60
STREAM_SILENT_NIGHT_SECONDS = 2 * 3600
STREAM_NIGHT_HOURS = range(1, 9)

KIND_CLIENT = 'in'
KIND_VERIFIER = 'ver'
KIND_OTHER = 'other'      # сотрудник другой группы или автор без привязки к сотруднику
KIND_SERVICE = 'service'  # рассылка, бот, отправка без автора

# Шаблон WhatsApp Business от автора без привязки — рассылка («ЯР», «Отток группа»), а не ответ.
TEMPLATE_TYPE = 'wapi_template'


def classify(rows, verifier_ids, verifiers_by_day=None):
    """Сообщения из базы -> строки расчёта {channel_id, chat_id, at, kind, user_id}.

    rows: {channel_id, chat_id, at (наивное время Алматы), is_echo, user_id, is_bot, author_id,
    type, message_id}. Рассылки отбрасываются здесь же — дальше их нет нигде:
      * бот по карте авторов и отправка без автора («Admin» — автоматика Wazzup);
      * шаблон от автора без привязки к сотруднику (рассылки «ЯР», «Отток группа»).
    Автор с id, но без привязки, пишущий обычный текст, — человек, которого ещё не привязали
    (новый сотрудник, общая учётка группы): его ответ закрывает ожидание клиента, но никому из
    верификаторов не засчитывается. Иначе клиент, которому ответили, висел бы «ждущим».

    verifiers_by_day — {дата: множество id} для периода, где состав менялся: сотрудник,
    перешедший в верификаторы 10-го, до 10-го считается сотрудником своей прежней группы."""
    verifiers = {int(v) for v in verifier_ids or ()}
    by_day = {day: {int(v) for v in ids} for day, ids in (verifiers_by_day or {}).items()}
    out = []
    for row in rows or []:
        at = row.get('at')
        if at is None:
            continue
        user_id = row.get('user_id')
        if not row.get('is_echo'):
            kind = KIND_CLIENT
        elif row.get('is_bot'):
            continue
        elif user_id is None:
            if row.get('author_id') is None or row.get('type') == TEMPLATE_TYPE:
                continue
            kind = KIND_OTHER
        elif int(user_id) in (by_day.get(at.date(), verifiers) if by_day else verifiers):
            kind = KIND_VERIFIER
        else:
            kind = KIND_OTHER
        out.append({'channel_id': row.get('channel_id'), 'chat_id': row.get('chat_id'),
                    'at': at, 'kind': kind, 'message_id': str(row.get('message_id') or ''),
                    'user_id': int(user_id) if user_id is not None else None})
    return out


def split_episodes(messages, gap_seconds=EPISODE_GAP_SECONDS):
    """Диалоги: сообщения одного чата подряд, пока пауза меньше порога.

    Каждый диалог считает задержки ответа по правилам строителя обращений СЗоВ (см. докстринг
    модуля) и помнит, кто в нём писал. Порядок сообщений внутри чата — по времени; в одну и ту
    же миллисекунду реплика клиента идёт раньше ответа (ответ не бывает раньше вопроса), дальше —
    по message_id: база отдаёт строки без сортировки.

    Исключение из паузы — клиент, которому не ответил никто (см. UNANSWERED_REPLY_MAX_SECONDS)."""
    by_chat = defaultdict(list)
    for index, message in enumerate(messages or []):
        by_chat[(message['channel_id'], message['chat_id'])].append(
            (message['at'], 0 if message['kind'] == KIND_CLIENT else 1,
             message.get('message_id') or '', index, message))
    gap = timedelta(seconds=gap_seconds)
    unanswered_max = timedelta(seconds=UNANSWERED_REPLY_MAX_SECONDS)
    episodes = []
    for key, items in by_chat.items():
        items.sort(key=lambda item: item[:4])
        current = None
        for at, _order, _message_id, _index, message in items:
            if current is None or (at - current['end'] >= gap and not (
                    current['last_human'] is None and current['pending'] is not None
                    and at - current['pending'] <= unanswered_max)):
                current = _new_episode(key, at)
                episodes.append(current)
            _feed_episode(current, message)
    return episodes


def _new_episode(key, at):
    return {'channel_id': key[0], 'chat_id': key[1], 'start': at, 'end': at,
            'pending': None, 'first_done': False, 'first_by': None,
            'incoming': 0, 'has_verifier': False, 'has_other': False,
            'last_human': None, 'people': {}}


def _feed_episode(episode, message):
    at = message['at']
    episode['end'] = at
    if message['kind'] == KIND_CLIENT:
        episode['incoming'] += 1
        if episode['pending'] is None:
            episode['pending'] = at
        return
    user_id = message['user_id']
    if message['kind'] == KIND_VERIFIER:
        episode['has_verifier'] = True
    else:
        episode['has_other'] = True
    episode['last_human'] = (message['kind'], user_id)
    person = episode['people'].setdefault(user_id, {'kind': message['kind'], 'first': None,
                                                    'replies': 0, 'total': 0.0, 'last_at': at})
    person['last_at'] = at
    if episode['pending'] is None:
        return
    delta = max(0.0, (at - episode['pending']).total_seconds())
    person['replies'] += 1
    person['total'] += delta
    if not episode['first_done']:
        episode['first_done'] = True
        episode['first_by'] = user_id
        person['first'] = delta
    episode['pending'] = None


def belongs_to_verifiers(episode):
    """Диалог верификаторов: писал верификатор или не писал никто из других групп.

    Неотвеченный клиент — в очереди аккаунта верификаторов, поэтому их; диалог, который вёл
    только сотрудник другой группы, — не их, и в итоги направления не идёт."""
    return episode['has_verifier'] or not episode['has_other']


def empty_sums():
    return {'first_sum': 0.0, 'first_count': 0, 'inner_sum': 0.0, 'inner_count': 0}


def _add_episode_person(sums, episode, user_id, person):
    """Вклад одного верификатора в одном диалоге — в его личные суммы (как строка request_stats)."""
    if episode['first_by'] == user_id and person['first'] is not None:
        sums['first_sum'] += person['first']
        sums['first_count'] += 1
    if person['replies']:
        sums['inner_sum'] += person['total'] / person['replies']
        sums['inner_count'] += 1


def _add_episode_team(sums, episode):
    """Вклад диалога в итог направления — ровно как обращение в итоге СЗоВ: первый ответ (если
    его дал верификатор) и среднее ВСЕХ ответов верификаторов в диалоге, одно на диалог.

    Не сумма личных вкладов: в диалоге, где А ответил десять раз по десять секунд, а Б один раз
    за двадцать минут, у диалога среднее две минуты, а у пары людей — десять. Люди судятся по
    своим ответам, направление — по диалогам, как у СЗоВ."""
    replies = total = 0
    for user_id, person in episode['people'].items():
        if person['kind'] != KIND_VERIFIER:
            continue
        if episode['first_by'] == user_id and person['first'] is not None:
            sums['first_sum'] += person['first']
            sums['first_count'] += 1
        replies += person['replies']
        total += person['total']
    if replies:
        sums['inner_sum'] += total / replies
        sums['inner_count'] += 1


def merge_sums(into, other):
    for key in into:
        into[key] += other.get(key) or 0
    return into


def reply_average(sums, kind):
    """Среднее из сумм: 'first' или 'inner', в целых секундах. None — считать не по чему.

    Целые — чтобы плитка, отбивка и файл судили о норме по одной и той же цифре: 60,4 с на
    табло «1,0 мин» красным, а в Telegram «1:00 дольше нормы 1:00» читалось бы как ошибка."""
    count = (sums or {}).get(f'{kind}_count') or 0
    if not count:
        return None
    return int(round(float(sums[f'{kind}_sum']) / count))


def _public_sums(sums):
    return {'first_sum': round(sums['first_sum'], 1), 'first_count': sums['first_count'],
            'inner_sum': round(sums['inner_sum'], 1), 'inner_count': sums['inner_count']}


def day_block(episodes, messages, day, *, names, now=None):
    """Показатели одних суток: итог, часы, люди. Без «сейчас» — оно только у сегодняшнего снимка.

    episodes — диалоги из `split_episodes` (можно за период: берутся начавшиеся в `day`).
    messages — строки `classify` (для «чатов у человека»: чаты, где он писал в эти сутки).
    now — момент снимка; задан — часы идут до текущего, последний помечен незакрытым."""
    names = names or {}
    today = empty_sums()
    chats = 0
    hours = defaultdict(lambda: {'chats': 0, 'sums': empty_sums()})
    person_sums = defaultdict(empty_sums)
    person_hour_sums = defaultdict(empty_sums)
    for episode in episodes:
        if episode['start'].date() != day or not belongs_to_verifiers(episode):
            continue
        hour = episode['start'].hour
        chats += 1
        hours[hour]['chats'] += 1
        _add_episode_team(today, episode)
        _add_episode_team(hours[hour]['sums'], episode)
        for user_id, person in episode['people'].items():
            if person['kind'] != KIND_VERIFIER:
                continue
            contribution = empty_sums()
            _add_episode_person(contribution, episode, user_id, person)
            merge_sums(person_sums[user_id], contribution)
            merge_sums(person_hour_sums[(user_id, hour)], contribution)

    # Чаты у человека — пары «чат × сутки» (и «чат × час»), где он писал сам: так же считают
    # «Чаты ОП» и «Воронка ОП». Чат, где писали двое, засчитан обоим.
    person_chats = defaultdict(set)
    person_hour_chats = defaultdict(set)
    for message in messages or []:
        if message['kind'] != KIND_VERIFIER or message['at'].date() != day:
            continue
        chat = (message['channel_id'], message['chat_id'])
        person_chats[message['user_id']].add(chat)
        person_hour_chats[(message['user_id'], message['at'].hour)].add(chat)

    # Час в разрезе людей — два разных вопроса, и ключи у них разные, как у СЗоВ:
    #   operators       — кто писал клиентам В ЭТОТ ЧАС и в скольких чатах (подсказка «Кто писал»,
    #                     лист «Чаты по часам»);
    #   operators_reply — суммы ответов по диалогам, НАЧАТЫМ в этот час (листы времени ответа).
    # Сведи их в один список — и ответ в 11:03 на диалог из 10:58 пропал бы: в часе 10 человек не
    # писал, а в часе 11 у него нет сумм.
    last_hour = now.hour if now is not None and now.date() == day else 23
    hourly = []
    for hour in range(0, last_hour + 1):
        sums = hours[hour]['sums'] if hour in hours else empty_sums()
        people = [{'user_id': user_id, 'name': names.get(user_id) or f'#{user_id}',
                   'chats': len(chat_set)}
                  for (user_id, person_hour), chat_set in person_hour_chats.items()
                  if person_hour == hour]
        people.sort(key=lambda item: (-item['chats'], item['name']))
        replies = [{'user_id': user_id, 'name': names.get(user_id) or f'#{user_id}',
                    **_public_sums(hour_sums)}
                   for (user_id, person_hour), hour_sums in person_hour_sums.items()
                   if person_hour == hour and (hour_sums['first_count'] or hour_sums['inner_count'])]
        replies.sort(key=lambda item: item['name'])
        hourly.append({
            'hour': hour,
            'chats': hours[hour]['chats'] if hour in hours else 0,
            'first_reply_seconds': reply_average(sums, 'first'),
            'inner_reply_seconds': reply_average(sums, 'inner'),
            'reply_sums': _public_sums(sums),
            'verifiers': len(people),
            'operators': people,
            'operators_reply': replies,
            'partial': now is not None and now.date() == day and hour == now.hour,
        })

    operators = {}
    for user_id in set(person_chats) | set(person_sums):
        sums = person_sums.get(user_id) or empty_sums()
        operators[user_id] = {
            'user_id': user_id,
            'name': names.get(user_id) or f'#{user_id}',
            'chats': len(person_chats.get(user_id) or ()),
            'first_reply_seconds': reply_average(sums, 'first'),
            'inner_reply_seconds': reply_average(sums, 'inner'),
            'reply_sums': _public_sums(sums),
        }
    return {
        'day': day.isoformat(),
        'today': {'chats': chats,
                  'first_reply_seconds': reply_average(today, 'first'),
                  'inner_reply_seconds': reply_average(today, 'inner'),
                  'reply_sums': _public_sums(today)},
        'hourly': hourly,
        'operators': operators,
    }


def now_block(episodes, now, *, in_work_seconds=IN_WORK_SECONDS, gap_seconds=EPISODE_GAP_SECONDS,
              waiting_max_seconds=WAITING_MAX_SECONDS):
    """Чаты «сейчас»: в работе и ждущие ответа, всего и по людям.

    В работе — последнее сообщение диалога не старше `in_work_seconds` (рассылки не в счёт).
    Чат человека — у того, кто писал в диалоге последним; последний писал сотрудник другой
    группы — чат уже не верификаторов; за человеком чат числится, пока его собственная последняя
    реплика не старше `waiting_max_seconds`. Ждёт ответа — клиент написал после последнего ответа не
    раньше `waiting_max_seconds` назад (см. WAITING_MAX_SECONDS); ожидание — от первой такой
    реплики, как и задержка ответа."""
    in_work_by_person = defaultdict(int)
    chats_in_work = 0
    waiting = 0
    longest_wait = None
    for episode in episodes:
        silence = (now - episode['end']).total_seconds()
        if silence >= gap_seconds:
            continue
        last = episode['last_human']
        if last is not None and last[0] != KIND_VERIFIER:
            continue
        if silence <= in_work_seconds:
            chats_in_work += 1
            # Чат числится за человеком, только пока его собственная последняя реплика свежая:
            # ответил в 14:01 и ушёл со смены, а клиент в 19:50 написал «Рахмет» — чат в работе,
            # но уже не его.
            if last is not None and (
                    now - episode['people'][last[1]]['last_at']).total_seconds() <= waiting_max_seconds:
                in_work_by_person[last[1]] += 1
        if episode['pending'] is not None:
            wait = max(0.0, (now - episode['pending']).total_seconds())
            if wait <= waiting_max_seconds:
                waiting += 1
                longest_wait = wait if longest_wait is None else max(longest_wait, wait)
    return {
        'chats_in_work': chats_in_work,
        'chats_waiting': waiting,
        'longest_wait_seconds': round(longest_wait) if longest_wait is not None else None,
        'verifiers_in_work': sum(1 for count in in_work_by_person.values() if count > 0),
        'in_work_by_person': dict(in_work_by_person),
    }


def stream_state(last_message_at, now):
    """Жив ли поток вебхука: {last_message_at, silent_seconds, threshold_seconds, silent}.

    Порог зависит от часа, в который тишина началась (см. STREAM_SILENT_*): ночная тишина —
    норма, дневная — обрыв. Сообщений нет вовсе — судим по текущему часу."""
    hour = (last_message_at or now).hour
    threshold = (STREAM_SILENT_NIGHT_SECONDS if hour in STREAM_NIGHT_HOURS
                 else STREAM_SILENT_DAY_SECONDS)
    if last_message_at is None:
        return {'last_message_at': None, 'silent_seconds': None,
                'threshold_seconds': threshold, 'silent': True}
    silent_seconds = max(0, int((now - last_message_at).total_seconds()))
    return {'last_message_at': last_message_at.strftime('%Y-%m-%d %H:%M:%S'),
            'silent_seconds': silent_seconds, 'threshold_seconds': threshold,
            'silent': silent_seconds > threshold}


def assemble(rows, now, *, verifier_ids, names, last_message_at=None,
             in_work_seconds=IN_WORK_SECONDS, gap_seconds=EPISODE_GAP_SECONDS,
             waiting_max_seconds=WAITING_MAX_SECONDS,
             first_target_seconds=FIRST_TARGET_SECONDS, inner_target_seconds=INNER_TARGET_SECONDS):
    """Снимок табло на момент `now` (наивное время Алматы).

    rows — сообщения с полуночи минус `gap_seconds` до `now`: диалог, начатый вчера вечером,
    обязан знать своё начало, иначе его утреннее продолжение посчиталось бы новым диалогом."""
    messages = classify(rows, verifier_ids)
    episodes = split_episodes(messages, gap_seconds)
    day = now.date()
    block = day_block(episodes, messages, day, names=names, now=now)
    current = now_block(episodes, now, in_work_seconds=in_work_seconds, gap_seconds=gap_seconds,
                        waiting_max_seconds=waiting_max_seconds)
    in_work = current.pop('in_work_by_person')
    people = block.pop('operators')
    for user_id, count in in_work.items():
        if user_id not in people:
            people[user_id] = {'user_id': user_id, 'name': (names or {}).get(user_id) or f'#{user_id}',
                               'chats': 0, 'first_reply_seconds': None, 'inner_reply_seconds': None,
                               'reply_sums': _public_sums(empty_sums())}
    operators = []
    for user_id, person in people.items():
        operators.append({**person, 'in_work': in_work.get(user_id, 0)})
    # Сначала те, у кого чаты в работе, внутри — по алфавиту: строки на стене не прыгают от
    # каждого нового сообщения, а люди без работы «сейчас» уходят вниз, но не исчезают.
    operators.sort(key=lambda item: (0 if item['in_work'] else 1, item['name']))
    current['operators'] = operators
    return {
        **block,
        'now': current,
        'stream': stream_state(last_message_at, now),
        'source_now': last_message_at.strftime('%Y-%m-%d %H:%M:%S') if last_message_at else None,
        'first_target_seconds': first_target_seconds,
        'inner_target_seconds': inner_target_seconds,
        'in_work_minutes': round(in_work_seconds / 60),
        'waiting_max_minutes': round(waiting_max_seconds / 60),
        'captured_at': now.strftime('%Y-%m-%dT%H:%M:%S'),
        'diagnostics': {'verifiers': len(set(verifier_ids or ()))},
    }


def period_days(rows, days, *, verifier_ids, names, gap_seconds=EPISODE_GAP_SECONDS,
                verifiers_by_day=None):
    """Показатели нескольких суток одним разбором (выгрузка): [{day_block}] по порядку `days`.

    rows — сообщения с запасом `gap_seconds` до первых суток (диалог, начатый накануне вечером,
    обязан знать своё начало) и после последних (ответ в 00:03 на диалог из 23:50 — его).
    verifiers_by_day — состав на каждую дату, см. `classify`."""
    messages = classify(rows, verifier_ids, verifiers_by_day)
    episodes = split_episodes(messages, gap_seconds)
    return [day_block(episodes, messages, day, names=names) for day in days]


def sum_sums(blocks):
    """Суммы ответа за период — по диалогам всех дней, а не «среднее средних»."""
    total = empty_sums()
    for block in blocks or []:
        merge_sums(total, block.get('reply_sums') or {})
    return total


def local_now(tz):
    """Наивное «сейчас» по часам Алматы — время в `wazzup_messages` приводится к ним же."""
    return datetime.now(tz).replace(tzinfo=None)

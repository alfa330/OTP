# -*- coding: utf-8 -*-
"""Поиск по вопросу партнёра: материал помощника оператора — по одному парку.

Владелец 19.11.2026: ручка знаний для Syntony — «материал как у помощника в
роли оператора, то есть информацию и статьи, которые только для руководителей
и т. д., не передавать». Поэтому фрагменты собирает ТА ЖЕ сборка, что
/api/wiki/ai/search и /ask помощника (wiki/routes_ai.py: directory_rows и
classifier_rows; статьи — wiki/ai/retrieve.search_hybrid), в правах оператора
отдела ключа (perimeter.py), и с парком из запроса:

    registration  куда регистрировать водителя этого парка: город и диспетчерская;
    office        офис из справочника «Офисы» с адресом, телефонами и графиком;
    offices       сводка офисов по городам (вопрос про офисы без города);
    city          комиссии Яндекса в названном городе; cities — таблица по городам;
    park          карточка запрошенного парка: комиссия, офисы и номера, сайт,
                  действующие акции, описание — когда о парке спросили;
    classifier    «Классификатор авто» — какой тариф положен машине;
    article       фрагмент статьи из периметра оператора.

Чего у помощника есть, а здесь нет, — и почему:
  * «Списки Байги»: в них ФИО, номер ВУ и доход водителей — наружу не отдаём;
  * сводка всех парков и карточки парков из слов вопроса: ручка отвечает за
    один парк, чужая комиссия или чужой номер в ответе — ошибка водителю;
  * история разговора: вызов — один вопрос.

Фрагменты статей проходят два сита. Парк (parks.py): строки про чужие парки
выпадают, общие и «все парки, кроме…» остаются. Порог (wiki/ai/answer.py:
usable_chunks) — тот же, что у помощника: ниже него — не источник. Свежесть —
то же правило, что у помощника (wiki/ai/currency.py): архив и прошедшие сроки
помечаются. Если после сит ничего не осталось и справочники молчат — status
no_data.
"""

import logging
import re

from wiki import offices as wiki_offices
from wiki import routes_ai as assistant
from wiki.ai import answer as ai_answer
from wiki.ai import currency as ai_currency
from wiki.ai import embed as ai_embed
from wiki.ai import retrieve as ai_retrieve

from . import parks as park_match
from . import perimeter

DEFAULT_LIMIT = 8
MAX_LIMIT = 20
PER_ARTICLE = 3
# Кандидатов берём с запасом: фильтр по парку и порог что-то отбросят.
CANDIDATE_FACTOR = 3

# Номер ветки поиска → слово в ответе партнёру (wiki/ai/retrieve.py, directory.py).
FOUND_BY = {0: 'words', 1: 'meaning', 2: 'typo', 3: 'title', 4: 'directory'}

# Справочные строки, собранные ЗА запрошенный парк: регистрация отвечает за его
# диспетчерскую, карточка — его (wiki/registration.py, wiki/park_cards.py).
PARK_OWN_KINDS = frozenset(('registration', 'park'))

# Ссылки во внутреннюю сеть (старая вика, стенды) наружу не нужны и не откроются.
_PRIVATE_URL = re.compile(
    r'\(?\bhttps?://(?:localhost|127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)[^\s)]*\)?')


def clean_text(text):
    """Без внутренних ссылок и лишних пробелов; переводы строк остаются."""
    value = _PRIVATE_URL.sub('', str(text or ''))
    lines = [' '.join(line.split()) for line in value.splitlines()]
    return '\n'.join(line for line in lines if line).strip()


def load_parks(cursor, space_ids):
    """Парки пространств ключа — для /parks и для распознавания в тексте."""
    ids = sorted({int(value) for value in space_ids}) or [-1]
    cursor.execute(
        """
        SELECT id, slug, name, city, status, space_id
          FROM wiki_taxi_parks
         WHERE space_id = ANY(%s)
         ORDER BY status, position, id
        """,
        (ids,),
    )
    return [{'id': int(row[0]), 'slug': row[1], 'name': row[2], 'city': row[3],
             'status': row[4], 'space_id': int(row[5]) if row[5] is not None else None}
            for row in cursor.fetchall()]


def resolve_park(cursor, *, spaces, park_id=None, park=None):
    """Парк по id либо по slug/названию (без учёта регистра) — только в
    пространствах ключа. None — не найден или ключу не открыт.

    Граница пространства стоит в самом запросе, как у всех чтений справочника
    вне вики (tests/test_wiki_directory_space.py): парк чужой вики для ключа не
    существует, а не «найден, но закрыт»."""
    ids = sorted({int(value) for value in spaces}) or [-1]
    if park_id is not None:
        cursor.execute(
            """
            SELECT id, slug, name, city, status, space_id
              FROM wiki_taxi_parks
             WHERE id = %(id)s AND space_id = ANY(%(spaces)s)
            """,
            {'id': int(park_id), 'spaces': ids},
        )
    else:
        value = ' '.join(str(park or '').split())
        if not value:
            return None
        cursor.execute(
            """
            SELECT id, slug, name, city, status, space_id
              FROM wiki_taxi_parks
             WHERE space_id = ANY(%(spaces)s)
               AND (lower(slug) = lower(%(value)s) OR lower(name) = lower(%(value)s))
             ORDER BY (status = 'active') DESC, id
             LIMIT 1
            """,
            {'value': value, 'spaces': ids},
        )
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': int(row[0]), 'slug': row[1], 'name': row[2], 'city': row[3],
            'status': row[4], 'space_id': int(row[5]) if row[5] is not None else None}


def narrow_offices(rows, offices, park):
    """Офисы справочника — через сито парка (решения владельца 29.10.2026).

    Офис, привязанный к запрошенному парку или ко всем паркам, — named; офис без
    единой привязки — общий (general), отдаём; офис, привязанный только к чужим
    паркам, — чужой, не отдаём. Партнёрская точка (брендирование и т. п.) — не
    офис таксопарка, и партнёру это говорится прямо: office.kind = partner.
    Регистрация и карточка собраны за запрошенный парк — named; прочие
    справочные строки — general.
    """
    by_id = {int(office['id']): office for office in offices}
    kept = []
    for row in rows:
        if row.get('source_kind') != 'office':
            row.setdefault('park_match', park_match.NAMED
                           if row.get('source_kind') in PARK_OWN_KINDS else park_match.GENERAL)
            kept.append(row)
            continue
        office = by_id.get(int(row['ref_id'])) if row.get('ref_id') is not None else None
        if office is None:
            row['park_match'] = park_match.GENERAL
            kept.append(row)
            continue
        links = {int(item['park_id']) for item in office.get('parks') or []
                 if item.get('park_id') is not None}
        if office.get('all_parks') or int(park['id']) in links:
            row['park_match'] = park_match.NAMED
        elif links:
            continue
        else:
            row['park_match'] = park_match.GENERAL
        row['office'] = {
            'id': int(office['id']),
            'kind': 'partner' if office.get('kind') == 'partner' else 'park',
            'partner_label': office.get('partner_label') or None,
        }
        kept.append(row)
    return kept


def office_sieve(cursor, space_id, park):
    """Сито офисов для сборки помощника (routes_ai.directory_rows(narrow=…)).

    Справочник офисов читается, только если среди строк есть офис. Не
    прочитался — офисы не отдаются вовсе: чей офис, тогда не проверить, а
    чужой адрес в ответе водителю хуже, чем никакого (остальные строки
    остаются)."""
    def narrow(rows):
        if not any(row.get('source_kind') == 'office' for row in rows):
            return narrow_offices(rows, [], park)
        cursor.execute('SAVEPOINT knowledge_offices')
        try:
            offices = wiki_offices.list_offices(cursor, day=wiki_offices.office_today(),
                                                space_id=space_id)
        except Exception:                                # noqa: BLE001
            cursor.execute('ROLLBACK TO SAVEPOINT knowledge_offices')
            logging.exception('knowledge_api: справочник офисов не прочитан — офисы не отданы')
            return narrow_offices([row for row in rows if row.get('source_kind') != 'office'],
                                  [], park)
        cursor.execute('RELEASE SAVEPOINT knowledge_offices')
        return narrow_offices(rows, offices, park)
    return narrow


def fragment(rank, row, on_date=None):
    """Строка выборки → фрагмент ответа партнёру. Поля — контракт документа.

    outdated / outdated_note — пометка свежести помощника (currency.chunk_currency):
    outdated — фрагмент целиком не действует (архив или все сроки прошли);
    outdated_note — та же строка, что видит модель помощника. Истекла только
    часть сроков — outdated нет, а пометка просит сверить даты по строкам:
    иначе ИИ партнёра выбросил бы и живые строки таблицы акций."""
    chunk_id = row.get('chunk_id')
    found_by = [FOUND_BY.get(code, str(code)) for code in (row.get('found_by') or [])]
    state = ai_currency.chunk_currency(row, on_date)
    item = {
        'rank': rank,
        'kind': row.get('source_kind') or 'article',
        'article_id': row.get('article_id'),
        'title': row.get('title'),
        'heading': clean_text(row.get('heading_path')) or None,
        'text': clean_text(row.get('text')),
        'chunk_id': int(chunk_id) if isinstance(chunk_id, int) and chunk_id > 0 else None,
        'score': _rounded(row.get('score')),
        'similarity': _rounded(row.get('similarity')),
        'found_by': found_by,
        'park_match': row.get('park_match') or park_match.GENERAL,
        'outdated': bool(state['stale'] and not state.get('partial')),
        'outdated_note': state['note'] or None,
    }
    if row.get('office'):
        office = dict(row['office'])
        if office.get('kind') == 'partner':
            office['note'] = 'Партнёрская точка, не офис таксопарка'
        item['office'] = office
    return item


def _rounded(value):
    try:
        return round(float(value), 4) if value is not None else None
    except (TypeError, ValueError):
        return None


def journal_fragments(fragments):
    """Что отдали — для журнала: без текста, только ссылки на источники."""
    return [{'rank': item['rank'], 'kind': item['kind'], 'article_id': item.get('article_id'),
             'title': item.get('title'), 'heading': item.get('heading'),
             'chunk_id': item.get('chunk_id'), 'score': item.get('score'),
             'similarity': item.get('similarity'), 'park_match': item.get('park_match')}
            for item in fragments]


def _empty(status, park=None):
    return {'status': status, 'park': _park_public(park) if park else None, 'fragments': [],
            'notes': [], 'degraded': False, 'vector_error': None, 'branches': {},
            'perimeter': None, 'today': None}


def _count(rows, kinds):
    return sum(1 for row in rows if row.get('source_kind') in kinds)


def run(cursor, *, key, question, park_id=None, park=None, limit=DEFAULT_LIMIT, embed=None,
        on_date=None):
    """Ответ на вопрос партнёра.

    status: ok | no_data | park_not_found (нет в пространствах ключа) |
    park_unavailable (есть, но в архиве). Парк ищется ПОСЛЕ того, как известны
    пространства ключа: границу держит сам запрос (resolve_park).

    on_date — «сегодня» для пометок свежести; параметр ради тестов."""
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    day = on_date or ai_currency.today()
    ctx = perimeter.service_context(cursor, otp_role=key['otp_role'],
                                    department_id=key['department_id'])
    space_ids = perimeter.spaces(cursor, ctx)
    found_park = resolve_park(cursor, spaces=space_ids, park_id=park_id, park=park)
    if found_park is None:
        return _empty('park_not_found')
    park = found_park
    if park.get('status') != 'active':
        return _empty('park_unavailable', park)
    space_id = park['space_id']
    scope = perimeter.scope(cursor, ctx, space_id)
    article_ids = scope['article_ids']

    query_vector = None
    vector_error = None
    try:
        query_vector = (embed or ai_embed.embed_query)(question)
    except Exception as error:                           # noqa: BLE001
        vector_error = str(error).splitlines()[0][:200] if str(error) else type(error).__name__
        logging.warning('knowledge_api: вектор вопроса не посчитан, ищем словами: %s', vector_error)

    found = ai_retrieve.search_hybrid(
        cursor, article_ids=article_ids, query=question, query_vector=query_vector,
        limit=limit * CANDIDATE_FACTOR, per_article=PER_ARTICLE)

    matcher = park_match.ParkMatcher(load_parks(cursor, [space_id]))
    kept = []
    dropped = 0
    for row in found['rows']:
        verdict = matcher.classify(park['id'], ' '.join(
            str(part) for part in (row.get('title'), row.get('heading_path'), row.get('text')) if part))
        if verdict not in park_match.KEEP:
            dropped += 1
            continue
        row['park_match'] = verdict
        kept.append(row)
    articles = ai_answer.usable_chunks(kept)[:limit]

    # Справочники — сборкой помощника, в том же порядке: регистрация, офисы и
    # города, карточка парка. Парк из запроса известен заранее.
    directory = assistant.directory_rows(
        cursor, ctx, question, space_id, park={'id': park['id'], 'name': park['name']},
        narrow=office_sieve(cursor, space_id, park))
    # Карточка «Парков» встаёт ПОСЛЕ сита офисов и метки от него не получает:
    # она, как и регистрация, собрана за запрошенный парк.
    for row in directory:
        row.setdefault('park_match', park_match.NAMED if row.get('source_kind') in PARK_OWN_KINDS
                       else park_match.GENERAL)
    cars = assistant.classifier_rows(cursor, question, article_ids)
    for row in cars:
        row.setdefault('park_match', park_match.GENERAL)

    rows = directory + cars + articles
    fragments = [fragment(position, row, day) for position, row in enumerate(rows, start=1)]
    notes = []
    others = matcher.others_in(park['id'], question)
    if others:
        notes.append('В вопросе назван другой таксопарк (%s); ответ ограничен парком «%s».'
                     % (', '.join(others), park['name']))
    return {
        'status': 'ok' if fragments else 'no_data',
        'park': _park_public(park),
        'fragments': fragments,
        'notes': notes,
        'degraded': found['degraded'],
        'vector_error': vector_error,
        'branches': dict(found['branches'], directory=len(directory),
                         registration=_count(directory, ('registration',)),
                         park=_count(directory, ('park',)), classifier=len(cars),
                         dropped_other_park=dropped),
        'perimeter': {'articles': len(article_ids), 'hash': scope['hash'], 'space_id': space_id},
        'today': day.isoformat(),
    }


def _park_public(park):
    return {'id': park['id'], 'slug': park.get('slug'), 'name': park.get('name')}

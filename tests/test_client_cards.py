# -*- coding: utf-8 -*-
"""«Карточка клиента» (задача #335): правила, права, тексты и клиент TEZ APP — без базы.

Запросы и сценарии на настоящем Postgres проверяет tests/test_client_cards_postgres.py.
Здесь — то, что должно быть верно при любом запросе: ключ номера, разбор ссылки на аккаунт
Тез, состояние карточки, выбор причин, алерты без спама, кто что видит, что уходит в
Telegram, разбор ответов TEZ APP — и что раздел подключён к порталу и телефону.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from client_cards import access, files, records, rules, telegram, tez, transport

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 12, 10, 0, 0)


# ─── Номер телефона ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('raw, key', [
    ('77012345678', '77012345678'), ('+7 701 234-56-78', '77012345678'), ('8 (701) 234 56 78', '77012345678'),
    ('7012345678', '77012345678'), ('sip:87012345678@pbx', '77012345678'),
    ('+998 90 123 45 67', '998901234567'), ('00998901234567', '998901234567'),
    ('89161234567', '79161234567'),
])
def test_phone_key_is_one_for_every_spelling(raw, key):
    assert rules.phone_key(raw) == key


@pytest.mark.parametrize('raw', [None, '', '105', '8501', 'anonymous', '1' * 16])
def test_internal_and_garbage_numbers_have_no_card(raw):
    assert rules.phone_key(raw) is None


def test_format_phone():
    assert rules.format_phone('77012345678') == '+7 701 234-56-78'
    assert rules.format_phone('998901234567') == '+998901234567'
    assert rules.format_phone(None) == ''


# ─── Ссылка на аккаунт Тез ───────────────────────────────────────────────────

@pytest.mark.parametrize('link, expected', [
    # Два вида ссылок, названные владельцем 12.11.2026.
    ('https://admin.tezapp.org/admin/drivers/12345', ('driver', 12345)),
    ('https://admin.tezapp.org/admin/clients/67890', ('client', 67890)),
    # Как их копируют из строки браузера на самом деле.
    ('admin.tezapp.org/admin/drivers/12345', ('driver', 12345)),
    ('  https://admin.tezapp.org/admin/drivers/12345/  ', ('driver', 12345)),
    ('https://admin.tezapp.org/admin/clients/67890/edit?tab=orders#history', ('client', 67890)),
    ('HTTP://ADMIN.TEZAPP.ORG/admin/drivers/5', ('driver', 5)),
])
def test_account_link_is_parsed(link, expected):
    assert rules.parse_account_link(link) == expected


@pytest.mark.parametrize('link', [
    '', '   ', '12345', 'drivers/12345', 'https://admin.tezapp.org/admin/drivers',
    'https://admin.tezapp.org/admin/drivers/abc', 'https://admin.tezapp.org/admin/drivers/0',
    'https://admin.tezapp.org/admin/orders/12345', 'https://admin.tezapp.org/drivers/12345',
    'https://evil.example/admin/drivers/12345', 'https://admin.tezapp.org.evil.example/admin/drivers/1',
    'https://admin.tezapp.org/admin/drivers/1 и ещё текст', 'ftp://admin.tezapp.org/admin/drivers/1',
    'https://admin.tezapp.org/admin/drivers/' + '9' * 13,
])
def test_foreign_links_are_rejected(link):
    with pytest.raises(rules.LinkError):
        rules.parse_account_link(link)


def test_account_url_round_trip():
    for kind, tez_id in (('driver', 12345), ('client', 67890)):
        assert rules.parse_account_link(rules.account_url(kind, tez_id)) == (kind, tez_id)
    assert rules.account_url('driver', 12345) == 'https://admin.tezapp.org/admin/drivers/12345'
    assert rules.account_url('robot', 1) is None and rules.account_url('driver', None) is None


# ─── Состояние карточки ──────────────────────────────────────────────────────

def _card(**fields):
    card = {'status': rules.STATUS_OPEN, 'created_at': NOW, 'answered_at': None, 'ended_at': None,
            'wrapup_sec': 60, 'account_id': None, 'topics': [], 'comment': None}
    card.update(fields)
    return card


def test_card_state_follows_its_timestamps():
    assert rules.card_state(_card(), NOW) == rules.STATE_RINGING
    assert rules.card_state(_card(answered_at=NOW), NOW + timedelta(minutes=5)) == rules.STATE_TALKING
    ended = _card(answered_at=NOW, ended_at=NOW + timedelta(minutes=5))
    assert rules.card_state(ended, NOW + timedelta(minutes=5, seconds=59)) == rules.STATE_WRAPUP
    assert rules.card_state(ended, NOW + timedelta(minutes=6)) == rules.STATE_OVERDUE
    assert rules.card_state(_card(ended_at=NOW, wrapup_sec=0, answered_at=NOW), NOW) == rules.STATE_OVERDUE
    assert rules.card_state(_card(status=rules.STATUS_DONE, ended_at=NOW), NOW) == rules.STATE_DONE
    assert rules.card_state(_card(status=rules.STATUS_VOID), NOW) == rules.STATE_VOID
    # Телефон не донёс конец звонка — карточка не числится «в разговоре» вечно.
    assert rules.card_state(_card(answered_at=NOW), NOW + timedelta(hours=5)) == rules.STATE_OVERDUE
    assert set(rules.STATE_LABELS) == {rules.STATE_RINGING, rules.STATE_TALKING, rules.STATE_WRAPUP,
                                       rules.STATE_OVERDUE, rules.STATE_DONE, rules.STATE_VOID}


def test_void_only_when_nothing_was_said_or_written():
    assert rules.void_on_end(_card())
    assert rules.void_on_end(_card(account_id=7))            # аккаунт выбрали ещё на гудках
    assert not rules.void_on_end(_card(answered_at=NOW))      # разговор был
    assert not rules.void_on_end(_card(comment='перезвонить'))
    assert not rules.void_on_end(_card(topics=[{'category_id': 1}]))


def test_completion_needs_account_then_topic():
    assert 'аккаунт' in rules.completion_error(_card())
    assert 'причину' in rules.completion_error(_card(account_id=7))
    assert rules.completion_error(_card(account_id=7, topics=[{'category_id': 1}])) is None


def test_no_account_mark_stands_for_the_account_but_not_for_the_topic():
    """«Аккаунта нет» (владелец, 12.11.2026): звонивший не зарегистрирован в Тез."""
    marked = _card(account_kind=rules.KIND_NONE)
    assert rules.has_no_account(marked)
    # Без отметки и без аккаунта карточку не сохранить — и текст говорит, как выйти.
    assert rules.NO_ACCOUNT_LABEL in rules.completion_error(_card())
    assert rules.completion_error(marked) == 'Выберите причину обращения'
    assert rules.completion_error(_card(account_kind=rules.KIND_NONE, topics=[{'category_id': 1}])) is None
    # Аккаунт выбран — отметки нет, что бы ни лежало в виде.
    assert not rules.has_no_account(_card(account_id=7, account_kind=rules.KIND_NONE))
    assert not rules.has_no_account(_card(account_kind=rules.KIND_DRIVER))
    assert not rules.has_no_account(_card())
    # Это вид карточки, а не аккаунта: по ссылке и из TEZ APP такой вид не приходит.
    assert rules.KIND_NONE not in rules.KINDS and rules.KIND_NONE not in rules.KIND_LABELS
    # Отметка — не запись оператора: недозвон с отметкой всё равно «не состоялся».
    assert rules.void_on_end(marked)


def test_summary_rows_letters_and_formulas_agree():
    """Буквы строк «Сводки» идут подряд, а формулы в подписях ссылаются на существующие строки."""
    import re

    from client_cards import report
    totals = {'calls': 10, 'done': 8, 'pending': 2, 'incoming': 7, 'outgoing': 3, 'drivers': 5, 'clients': 2,
              'no_account': 1, 'people': 9, 'avg_wrapup_sec': 41.0, 'late': 2}
    rows = report.summary_rows({'totals': totals, 'requests': {'total': 3, 'active': 1, 'avg_first_reply_sec': 600}})
    letters = [row[0] for row in rows]
    assert letters == list('АБВГДЕЖЗИКЛМНОПР')
    by_letter = {row[0]: row for row in rows}
    # Ссылки на строки: «(из Б)», «= Б ÷ А», «А = Б + В».
    refs = re.compile(r'(?:\(из |[=÷+] )([А-Я])(?![А-Яа-яё])|(?:^|\. )([А-Я]) = ')
    found = 0
    for letter, label, _value, _format, note in rows:
        for text in (label, note):
            for first, second in refs.findall(text):
                found += 1
                assert (first or second) in by_letter, (letter, text)
    assert found >= 15
    assert by_letter['И'][1:3] == ('Обращений без аккаунта (из Б)', 1)
    assert by_letter['Ж'][2] + by_letter['З'][2] + by_letter['И'][2] == by_letter['Б'][2]
    assert 'Б = Ж + З + И' in by_letter['И'][4]
    assert by_letter['М'][1] == 'Доля сохранённых позже срока = Л ÷ Б' and by_letter['Л'][2] == 2
    assert by_letter['П'][1] == 'Из них без решения (из О)' and by_letter['О'][2] == 3


TREE = {
    1: {'parent_id': None, 'is_active': True, 'has_children': True},
    2: {'parent_id': 1, 'is_active': True, 'has_children': False},
    3: {'parent_id': 1, 'is_active': False, 'has_children': False},
    4: {'parent_id': None, 'is_active': True, 'has_children': False},
    5: {'parent_id': None, 'is_active': False, 'has_children': False},
}


def test_topics_are_checked_against_the_tree():
    assert rules.normalize_topics([{'category_id': 1, 'subcategory_id': 2}, {'category_id': '4'},
                                   {'category_id': 4, 'subcategory_id': None},
                                   {'category_id': 1, 'subcategory_id': '2'}], TREE) == [(1, 2), (4, None)]
    assert rules.normalize_topics(None, TREE) == []
    for bad in ([{'category_id': 1}],                       # у категории есть подтемы
                [{'category_id': 1, 'subcategory_id': 3}],  # подтема скрыта
                [{'category_id': 4, 'subcategory_id': 2}],  # подтема чужой категории
                [{'category_id': 2}],                       # подтема вместо категории
                [{'category_id': 5}],                       # категория скрыта
                [{'category_id': 99}], [{'category_id': 'x'}], ['Заказы'], [{}]):
        with pytest.raises(ValueError):
            rules.normalize_topics(bad, TREE)
    many = {index: {'parent_id': None, 'is_active': True, 'has_children': False} for index in range(1, 9)}
    with pytest.raises(ValueError):
        rules.normalize_topics([{'category_id': index} for index in range(1, rules.TOPICS_LIMIT + 2)], many)


# Дерево из просьбы владельца (13.11.2026): основная причина → подтема 1-го уровня →
# подтема 2-го уровня. 10 «Запрос» → 11 «Проблема с заказом» → 111, 112; 10 → 12; 20 «Жалоба».
def _deep(**changes):
    tree = {
        10: {'parent_id': None, 'is_active': True, 'has_children': True, 'title': 'Запрос'},
        11: {'parent_id': 10, 'is_active': True, 'has_children': True, 'title': 'Проблема с заказом'},
        111: {'parent_id': 11, 'is_active': True, 'has_children': False, 'title': 'Не может завершить заказ'},
        112: {'parent_id': 11, 'is_active': True, 'has_children': False, 'title': 'Не может отменить заказ'},
        12: {'parent_id': 10, 'is_active': True, 'has_children': False, 'title': 'Вопрос по оплате'},
        20: {'parent_id': None, 'is_active': True, 'has_children': False, 'title': 'Жалоба'},
    }
    for node_id, fields in changes.items():
        tree[int(node_id[1:])].update(fields)
    return tree


def test_tree_paths_and_branches():
    tree = _deep()
    assert rules.category_path(tree, 111) == [10, 11, 111] and rules.category_path(tree, 20) == [20]
    assert rules.category_path(tree, 999) is None
    # Оборванная цепочка и петля — не путь: такую причину выбрать нельзя.
    assert rules.category_path({1: {'parent_id': 2}}, 1) is None
    assert rules.category_path({1: {'parent_id': 2}, 2: {'parent_id': 1}}, 1) is None
    assert rules.category_titles(tree, 111) == ['Запрос', 'Проблема с заказом', 'Не может завершить заказ']
    assert rules.category_titles(tree, 999) == ['Причина 999']
    children = rules.category_children(tree)
    assert sorted(children[None]) == [10, 20] and sorted(children[11]) == [111, 112]
    assert rules.category_subtree(tree, 10) == {10, 11, 111, 112, 12}
    assert rules.category_subtree(tree, 11, children) == {11, 111, 112} and rules.category_subtree(tree, 20) == {20}


def test_topic_is_the_deepest_level_of_its_branch():
    tree = _deep()
    assert rules.normalize_topics([{'category_id': 10, 'subcategory_id': 111}, {'category_id': 10, 'subcategory_id': 12},
                                   {'category_id': 20}], tree) == [(10, 111), (10, 12), (20, None)]
    for bad in ([{'category_id': 10}],                         # у основной причины есть подтемы
                [{'category_id': 10, 'subcategory_id': 11}],   # у подтемы есть свои подтемы
                [{'category_id': 20, 'subcategory_id': 111}],  # подтема из чужой ветки
                [{'category_id': 11, 'subcategory_id': 111}],  # ветка названа не с основной причины
                [{'category_id': 10, 'subcategory_id': 10}]):  # основная причина вместо подтемы
        with pytest.raises(ValueError):
            rules.normalize_topics(bad, tree)
    # Скрыта середина ветки — её подтемы тоже не выбрать.
    with pytest.raises(ValueError):
        rules.normalize_topics([{'category_id': 10, 'subcategory_id': 111}], _deep(n11={'is_active': False}))
    # У причины скрыты все подтемы (has_children считает видимые) — выбирают её саму.
    assert rules.normalize_topics([{'category_id': 10, 'subcategory_id': 11}],
                                  _deep(n11={'has_children': False})) == [(10, 11)]


def test_people_roll_up_the_branch_once():
    tree = _deep()
    people = rules.rollup_people([(111, 'a1'), (112, 'a1'), (112, 'p7'), (12, 'a2'), (20, 'a1')], tree)
    # Человек с двумя подтемами одной ветки считается у причины над ними один раз.
    assert {node: len(group) for node, group in people.items()} == {111: 1, 112: 2, 11: 2, 12: 1, 10: 3, 20: 1}
    assert rules.rollup_people([], tree) == {}
    # Причины нет в справочнике — считается сама по себе, ничего не роняя.
    assert rules.rollup_people([(999, 'a1')], tree) == {999: {'a1'}}


def test_topic_path_text_fits_the_column():
    assert rules.topic_path_text(['Запрос', 'Проблема с заказом', 'Не может завершить заказ']) == \
        'Запрос → Проблема с заказом → Не может завершить заказ'
    assert rules.topic_path_text(['Выплаты']) == 'Выплаты' and rules.topic_path_text([]) == ''
    # Не влезает — уходят верхние уровни: самое точное название в конце пути.
    long = ['А' * 160, 'Б' * 160, 'В' * 160]
    text = rules.topic_path_text(long)
    assert len(text) <= rules.REQUEST_TOPIC_LIMIT and text == '… → ' + 'Б' * 160 + ' → ' + 'В' * 160
    assert rules.topic_path_text(['Г' * 500], limit=40) == 'Г' * 40


def test_drivers_clients_and_third_parties_have_their_own_reasons():
    """Владелец, 13.11.2026: у водителя и клиента — собственная ветка причин, и следом:
    «в ветку Клиент, Водитель нужно добавить 3 лицо»."""
    tree = {
        1: {'parent_id': None, 'is_active': True, 'has_children': True, 'audience': 'driver'},
        2: {'parent_id': 1, 'is_active': True, 'has_children': False, 'audience': 'driver'},
        3: {'parent_id': None, 'is_active': True, 'has_children': False, 'audience': 'client'},
        4: {'parent_id': None, 'is_active': True, 'has_children': False, 'audience': 'third'},
    }
    driver, client, third = [{'category_id': 1, 'subcategory_id': 2}], [{'category_id': 3}], [{'category_id': 4}]
    for_driver, for_client = rules.account_audiences(rules.KIND_DRIVER), rules.account_audiences(rules.KIND_CLIENT)
    assert for_driver == (rules.KIND_DRIVER, rules.AUDIENCE_THIRD) and for_client == (rules.KIND_CLIENT, rules.AUDIENCE_THIRD)
    assert rules.account_audiences(rules.KIND_NONE) is None and rules.account_audiences(None) is None
    assert rules.normalize_topics(driver, tree, audiences=for_driver) == [(1, 2)]
    assert rules.normalize_topics(client, tree, audiences=for_client) == [(3, None)]
    with pytest.raises(ValueError) as error:
        rules.normalize_topics(client, tree, audiences=for_driver)
    assert str(error.value) == 'В карточке выбран водитель — причина из ветки «Клиент» сюда не подходит'
    with pytest.raises(ValueError) as error:
        rules.normalize_topics(driver + client, tree, audiences=for_client)
    assert 'выбран клиент' in str(error.value) and '«Водитель»' in str(error.value)
    # Третье лицо звонит и по чужому аккаунту — его причины подходят карточке с любым аккаунтом.
    assert rules.normalize_topics(driver + third, tree, audiences=for_driver) == [(1, 2), (4, None)]
    assert rules.normalize_topics(third, tree, audiences=for_client) == [(4, None)]
    # Аккаунта нет — кто звонит, называет оператор: подходит любая ветка.
    assert rules.normalize_topics(driver + client + third, tree) == [(1, 2), (3, None), (4, None)]

    # Какие ветки подходят карточке — по выбранному аккаунту; без аккаунта — любые.
    assert rules.card_audiences({'account_id': 7, 'account_kind': 'driver'}) == for_driver
    assert rules.card_audiences({'account_id': 8, 'account_kind': 'client'}) == for_client
    assert rules.card_audiences({'account_id': None, 'account_kind': rules.KIND_NONE}) is None
    assert rules.card_audiences({'account_id': None, 'account_kind': None}) is None and rules.card_audiences({}) is None

    # Аккаунт сменили — остаются причины его ветки и «Третьего лица».
    picked = [{'category_id': 1, 'subcategory_id': 2}, {'category_id': 3, 'subcategory_id': None},
              {'category_id': 4, 'subcategory_id': None}, {'category_id': 99, 'subcategory_id': None}]
    assert rules.topics_of_audiences(picked, tree, for_driver) == [(1, 2), (4, None)]
    assert rules.topics_of_audiences(picked, tree, for_client) == [(3, None), (4, None)]
    assert rules.topics_of_audiences(None, tree, for_client) == []

    assert [rules.normalize_audience(value) for value in ('driver', 'client', 'third')] == list(rules.AUDIENCES)
    assert rules.normalize_audience(None) == rules.KIND_DRIVER and rules.normalize_audience('') == rules.KIND_DRIVER
    for bad in ('none', 'partner', 5):
        with pytest.raises(ValueError):
            rules.normalize_audience(bad)
    assert rules.AUDIENCES == (rules.KIND_DRIVER, rules.KIND_CLIENT, rules.AUDIENCE_THIRD)
    assert [rules.AUDIENCE_LABELS[value] for value in rules.AUDIENCES] == ['Водитель', 'Клиент', 'Третье лицо']


def test_report_reasons_sheet_rows():
    from client_cards import report
    leaf = lambda title, count, **extra: dict({'title': title, 'count': count, 'own': count, 'children': []}, **extra)
    # В отчёте ветки идут вперемешку (по числу карточек) — на листе сначала водители, потом
    # клиенты, потом третьи лица.
    tree = [leaf('Нашёл чужую вещь', 2, audience='third'), leaf('Поездка', 12, audience='client'),
            {'title': 'Запрос', 'audience': 'driver', 'count': 9, 'own': 0, 'children': [
                {'title': 'Проблема с заказом', 'count': 7, 'own': 2,
                 'children': [leaf('Не может завершить заказ', 4), leaf('Не может отменить заказ', 1)]},
                leaf('Вопрос по оплате', 3)]},
            leaf('Жалоба', 1, audience='driver')]
    assert report.reason_rows(tree) == [
        ('Водитель', ['Запрос'], report.ALL_SUBTOPICS, 9),
        ('Водитель', ['Запрос', 'Проблема с заказом'], report.ALL_SUBTOPICS, 7),
        ('Водитель', ['Запрос', 'Проблема с заказом', 'Не может завершить заказ'], None, 4),
        ('Водитель', ['Запрос', 'Проблема с заказом', 'Не может отменить заказ'], None, 1),
        ('Водитель', ['Запрос', 'Проблема с заказом'], report.NO_SUBTOPIC, 2),
        ('Водитель', ['Запрос', 'Вопрос по оплате'], None, 3),
        ('Водитель', ['Жалоба'], None, 1),
        ('Клиент', ['Поездка'], None, 12),
        ('Третье лицо', ['Нашёл чужую вещь'], None, 2)]
    assert report.reason_rows([]) == []
    assert [report.reason_level_title(level) for level in (1, 2, 3)] == [
        'Основная причина', 'Подтема 1-го уровня', 'Подтема 2-го уровня']


# ─── Запись разговора ────────────────────────────────────────────────────────

def test_record_exists_only_after_a_finished_talk():
    moment = datetime(2026, 11, 12, 10, 0)
    later = moment + timedelta(minutes=5)
    assert rules.record_problem({'status': 'open', 'created_at': moment}, later) == 'Разговора не было — записи нет'
    assert rules.record_problem({'status': 'void', 'created_at': moment, 'answered_at': moment, 'ended_at': moment},
                                later) == 'Разговора не было — записи нет'
    talking = {'status': 'open', 'created_at': moment, 'answered_at': moment}
    assert rules.record_problem(talking, later) == 'Запись появится, когда звонок закончится'
    for status in ('open', 'done'):
        assert rules.record_problem({'status': status, 'created_at': moment, 'answered_at': moment,
                                     'ended_at': later}, later) is None
    # Телефон не донёс конец звонка: дольше предела разговор идти не может — запись можно искать.
    limit = moment + timedelta(hours=rules.CALL_MAX_HOURS)
    assert rules.call_end(talking, limit) is None and rules.record_problem(talking, limit) is not None
    assert rules.call_end(talking, limit + timedelta(seconds=1)) == limit
    assert rules.record_problem(talking, limit + timedelta(seconds=1)) is None
    assert rules.call_end(dict(talking, ended_at=later), later) == later


def _pbx_call(call_id, number, start, waitsec=10, billsec=120):
    return {'general_call_id': call_id, 'external_number': number, 'start_time': start, 'waitsec': waitsec,
            'billsec': billsec}


def test_card_is_matched_to_the_pbx_call_by_number_and_answer_time():
    # 12.11.2026 10:00:00 по Алматы (UTC+5) — в секундах, которыми считает АТС.
    answered = records.to_unix(datetime(2026, 11, 12, 10, 0, 0))
    assert answered == 1794459600
    find = lambda calls, phone='77012345678': (records.match_call(calls, phone=phone, answered_ts=answered) or {}).get(
        'general_call_id')
    # Ответ у АТС — начало звонка плюс ожидание; код страны АТС и портал пишут по-разному.
    assert find([_pbx_call('a', '87012345678', answered - 40, waitsec=38)]) == 'a'
    assert find([_pbx_call('a', '+7 (701) 234-56-78', answered - 5, waitsec=4)]) == 'a'
    assert find([_pbx_call('a', '7012345678', answered - 5, waitsec=4)]) == 'a'
    # Чужой номер, звонок без разговора, звонок без номера у АТС — не наш.
    assert find([_pbx_call('a', '77019999999', answered - 5)]) is None
    assert find([_pbx_call('a', '77012345678', answered - 5, billsec=0)]) is None
    assert find([_pbx_call('', '77012345678', answered - 5)]) is None
    # Тот же человек звонил оператору дважды за час — берётся разговор, ответ которого ближе.
    earlier = _pbx_call('early', '77012345678', answered - 1800, waitsec=5)
    ours = _pbx_call('ours', '77012345678', answered - 20, waitsec=15)
    later = _pbx_call('late', '77012345678', answered + 200, waitsec=10)
    assert find([earlier, ours, later]) == 'ours' and find([later, earlier]) == 'late'
    # Дальше допуска — уже другой разговор.
    assert find([_pbx_call('far', '77012345678', answered + records.MATCH_WINDOW_SEC + 1, waitsec=0)]) is None
    assert find([_pbx_call('edge', '77012345678', answered - records.MATCH_WINDOW_SEC, waitsec=0)]) == 'edge'
    # Неполный номер и пустой список.
    assert find([_pbx_call('a', '77012345678', answered)], phone='12345') is None
    assert find([]) is None and find(None) is None


class _FakePbxClient:
    def __init__(self, calls=(), link='https://pbx.example/rec.mp3', error=None):
        self.calls, self.link, self.error, self.asked = list(calls), link, error, []

    def list_calls_by_internal_number(self, sip, start_ts, stop_ts, max_wait=None):
        self.asked.append(('list', sip, start_ts, stop_ts, max_wait))
        if self.error:
            raise self.error
        return self.calls

    def get_call_record_url(self, call_id, max_wait=None):
        self.asked.append(('url', call_id, max_wait))
        if self.error:
            raise self.error
        return self.link


def test_binotel_source_finds_the_call_and_gives_the_link():
    from binotel.client import RateLimited
    started, answered, ended = (datetime(2026, 11, 12, 9, 58), datetime(2026, 11, 12, 10, 0),
                                datetime(2026, 11, 12, 10, 4))
    client = _FakePbxClient([_pbx_call(777, '87012345678', records.to_unix(answered) - 30, waitsec=28)])
    source = records.BinotelRecords(lambda: client)
    asked = dict(sip='930', phone='77012345678', started_at=started, answered_at=answered, ended_at=ended)
    assert source.find(**asked) == '777'
    # Окно запроса шире карточки: входящий у АТС начинается раньше — пока стоит в очереди.
    kind, sip, start_ts, stop_ts, max_wait = client.asked[0]
    assert (kind, sip, max_wait) == ('list', '930', records.LOOKUP_MAX_WAIT_SEC)
    assert start_ts == records.to_unix(started) - records.LOOKUP_BEFORE_SEC
    assert stop_ts == records.to_unix(ended) + records.LOOKUP_AFTER_SEC
    assert source.url('777') == 'https://pbx.example/rec.mp3' and client.asked[1] == ('url', '777', records.URL_MAX_WAIT_SEC)
    # АТС звонка не знает; запись ещё не выложена.
    assert records.BinotelRecords(lambda: _FakePbxClient()).find(**asked) is None
    assert records.BinotelRecords(lambda: _FakePbxClient(link=None)).url('777') is None

    # Очередь к АТС занята, АТС не ответила, ключей кабинета нет — понятная ошибка, а не падение.
    for error, code, status in ((RateLimited('too frequent'), 'RECORD_BUSY', 503),
                                (RuntimeError('boom'), 'RECORD_UNAVAILABLE', 502)):
        broken = records.BinotelRecords(lambda error=error: _FakePbxClient(error=error))
        for action in (lambda: broken.find(**asked), lambda: broken.url('777')):
            with pytest.raises(records.RecordError) as caught:
                action()
            assert (caught.value.code, caught.value.status) == (code, status)
    with pytest.raises(records.RecordError) as caught:
        records.BinotelRecords(lambda: None).url('777')
    assert (caught.value.code, caught.value.status) == ('RECORD_UNAVAILABLE', 503)
    # Записи подключены отделу с кодом tez (Binotel его кабинета).
    assert list(records.default_sources()) == ['tez']


# ─── Настройки ───────────────────────────────────────────────────────────────

def test_settings_are_clamped():
    assert rules.normalize_alerts(None) == rules.ALERT_DEFAULTS
    assert rules.normalize_alerts({'enabled': False, 'window_min': 5, 'min_count': 0, 'factor': 99,
                                   'cooldown_min': 'x', 'daily_limit': 100}) == {
        'enabled': False, 'window_min': 15, 'min_count': 2, 'factor': 10.0, 'cooldown_min': 120,
        'daily_limit': 24}
    assert rules.normalize_alerts({'factor': float('nan')})['factor'] == 2.0
    assert rules.normalize_wrapup(None) == rules.WRAPUP_DEFAULT_SEC
    assert rules.normalize_wrapup(0) == 0 and rules.normalize_wrapup(-5) == 0
    assert rules.normalize_wrapup(10 ** 6) == rules.WRAPUP_MAX_SEC and rules.normalize_wrapup('90') == 90


# ─── Алерты ──────────────────────────────────────────────────────────────────

SETTINGS = rules.normalize_alerts(None)


def _decide(count, baseline=0, last=None, sent_today=0, now=NOW, settings=SETTINGS):
    return rules.alert_decision(count=count, baseline=baseline, last=last, sent_today=sent_today,
                                now=now, settings=settings)


def test_threshold_needs_both_floor_and_growth():
    assert rules.alert_threshold(0, SETTINGS) == 5       # обычного нет — только нижняя планка
    assert rules.alert_threshold(1, SETTINGS) == 5
    assert rules.alert_threshold(3, SETTINGS) == 6       # вдвое больше обычного
    assert rules.alert_threshold(6.2, SETTINGS) == 13
    assert _decide(4) is None and _decide(5) == {'kind': rules.ALERT_NEW, 'threshold': 5}
    assert _decide(11, baseline=6) is None and _decide(12, baseline=6)['kind'] == rules.ALERT_NEW


def test_alert_is_not_repeated_without_a_reason():
    last = {'at': NOW - timedelta(minutes=10), 'count': 5}
    assert _decide(5, last=last) is None and _decide(7, last=last) is None
    # Стало заметно больше, но прошло мало времени — молчим; прошло полчаса — пишем.
    assert _decide(9, last=last) is None
    half_hour = {'at': NOW - timedelta(minutes=30), 'count': 5}
    assert _decide(7, last=half_hour) is None
    assert _decide(8, last=half_hour) == {'kind': rules.ALERT_GROWING, 'threshold': 5}
    # Большую волну «плюс три» не раскачивает: нужно в полтора раза.
    big = {'at': NOW - timedelta(minutes=40), 'count': 20}
    assert _decide(25, last=big) is None and _decide(30, last=big)['kind'] == rules.ALERT_GROWING
    # Пауза вышла, а рост держится — напоминание; после двух пауз тишины — новая волна.
    assert _decide(6, last={'at': NOW - timedelta(minutes=121), 'count': 5})['kind'] == rules.ALERT_STILL
    assert _decide(6, last={'at': NOW - timedelta(minutes=250), 'count': 5})['kind'] == rules.ALERT_NEW
    # Ниже порога — тишина всегда; дневной предел; выключено.
    assert _decide(4, last={'at': NOW - timedelta(days=1), 'count': 9}) is None
    assert _decide(50, sent_today=4) is None and _decide(50, sent_today=3) is not None
    assert _decide(50, settings=dict(SETTINGS, enabled=False)) is None


def _node(node_id, parent_id, count, baseline=0, last=None, sent_today=0):
    return {'id': node_id, 'parent_id': parent_id, 'count': count, 'baseline': baseline, 'last': last,
            'sent_today': sent_today}


def test_one_wave_is_one_line_in_one_message():
    # Волна по подтеме поднимает и категорию — в сообщение идёт подтема.
    picked = rules.pick_alerts([_node(1, None, 6), _node(11, 1, 6)], now=NOW, settings=SETTINGS)
    assert [(item['id'], item['kind']) for item in picked] == [(11, rules.ALERT_NEW)]
    # Рост размазан по подтемам: ни одна порога не взяла, категория взяла.
    picked = rules.pick_alerts([_node(1, None, 6), _node(11, 1, 3), _node(12, 1, 3)], now=NOW, settings=SETTINGS)
    assert [item['id'] for item in picked] == [1]
    # По подтеме идёт пауза — категория молчит тоже: это всё та же волна.
    recent = {'at': NOW - timedelta(minutes=20), 'count': 5}
    assert rules.pick_alerts([_node(1, None, 7), _node(11, 1, 6, last=recent)], now=NOW, settings=SETTINGS) == []
    # Две разные причины — обе в одном заходе, подтемы первыми.
    picked = rules.pick_alerts([_node(2, None, 9), _node(1, None, 6), _node(11, 1, 6)], now=NOW, settings=SETTINGS)
    assert [item['id'] for item in picked] == [11, 2]
    assert rules.pick_alerts([], now=NOW, settings=SETTINGS) == []


def test_wave_is_reported_at_its_deepest_level():
    """Дерево причин любой глубины: о волне пишем один раз — о самом глубоком её уровне."""
    def node(node_id, ancestors, count, **extra):
        return dict(_node(node_id, ancestors[0] if ancestors else None, count, **extra), ancestors=ancestors)

    # Волна по подтеме 2-го уровня поднимает и подтему над ней, и основную причину.
    picked = rules.pick_alerts([node(10, [], 6), node(11, [10], 6), node(111, [11, 10], 6)], now=NOW, settings=SETTINGS)
    assert [item['id'] for item in picked] == [111]
    # Рост размазан по подтемам 2-го уровня — пишем о причине над ними, но не об основной.
    picked = rules.pick_alerts([node(10, [], 6), node(11, [10], 6), node(111, [11, 10], 3), node(112, [11, 10], 3)],
                               now=NOW, settings=SETTINGS)
    assert [item['id'] for item in picked] == [11]
    # Рост размазан по двум веткам — только основная причина.
    picked = rules.pick_alerts([node(10, [], 6), node(11, [10], 3), node(111, [11, 10], 3), node(12, [10], 3)],
                               now=NOW, settings=SETTINGS)
    assert [item['id'] for item in picked] == [10]
    # По подтеме 2-го уровня идёт пауза — молчит вся цепочка над ней.
    recent = {'at': NOW - timedelta(minutes=20), 'count': 5}
    assert rules.pick_alerts([node(10, [], 7), node(11, [10], 7), node(111, [11, 10], 6, last=recent)],
                             now=NOW, settings=SETTINGS) == []
    # Соседняя ветка от этой паузы не зависит; самые глубокие — первыми.
    picked = rules.pick_alerts([node(20, [], 9), node(10, [], 7), node(11, [10], 7),
                                node(111, [11, 10], 6, last=recent), node(21, [20], 9)], now=NOW, settings=SETTINGS)
    assert [item['id'] for item in picked] == [21]


# ─── Права ───────────────────────────────────────────────────────────────────

TEZ, OP = 560, 367


def _ctx(user_id, role, department_id, *, headed=(), groups=(), status='working', departments=None):
    return {'user_id': user_id, 'name': 'Имя %s' % user_id, 'role': role, 'status': status,
            'department_id': department_id, 'headed_ids': list(headed), 'group_ids': list(groups),
            'departments': {TEZ: {'enabled': True, 'group_ids': [34]}} if departments is None else departments}


def test_who_runs_the_section_and_who_gets_the_card():
    operator = _ctx(30, 'operator', TEZ, groups=[34])
    seller = _ctx(40, 'operator', TEZ, groups=[35])
    head = _ctx(300, 'admin', TEZ, headed=[TEZ])
    supervisor = _ctx(3, 'sv', TEZ)
    super_admin = _ctx(1, 'super_admin', None)
    plain_admin = _ctx(2, 'admin', None)
    foreign_head = _ctx(9, 'admin', OP, headed=[OP])
    assert access.phone_card_enabled(operator) and not access.phone_card_enabled(seller)
    assert access.can_open_section(operator) and not access.can_open_section(seller)
    assert access.managed_department_ids(operator) == [] and access.section_department_ids(operator) == [TEZ]
    for manager in (head, supervisor, super_admin, plain_admin):
        assert access.managed_department_ids(manager) == [TEZ] and access.can_open_section(manager)
    # Назначение главой заменяет роль: админ-глава — не администратор компании.
    assert not access.is_global_admin(head) and access.is_global_admin(plain_admin)
    assert access.managed_department_ids(foreign_head) == [] and not access.can_open_section(foreign_head)
    # Руководитель проверяет карточку на себе, не числясь в группе линии; чужой админ — нет.
    assert access.phone_card_enabled(head) and access.phone_card_enabled(supervisor)
    assert not access.phone_card_enabled(super_admin)
    fired = _ctx(31, 'operator', TEZ, groups=[34], status='fired')
    assert not access.can_open_section(fired) and not access.phone_card_enabled(fired)


def test_groups_empty_means_the_whole_department_and_disabled_means_nobody():
    whole = {TEZ: {'enabled': True, 'group_ids': []}}
    assert access.phone_card_enabled(_ctx(40, 'operator', TEZ, groups=[35], departments=whole))
    off = {TEZ: {'enabled': False, 'group_ids': []}}
    operator = _ctx(30, 'operator', TEZ, groups=[34], departments=off)
    assert not access.phone_card_enabled(operator) and not access.can_open_section(operator)
    # Руководителю раздел открыт и выключенным — ему заводить причины до включения.
    assert access.can_open_section(_ctx(300, 'admin', TEZ, headed=[TEZ], departments=off))
    assert not access.can_open_section(_ctx(30, 'operator', TEZ, departments={}))
    assert access.managed_department_ids(_ctx(1, 'super_admin', None, departments={})) == []


def test_cards_and_requests_visibility():
    anna = _ctx(30, 'operator', TEZ, groups=[34])
    boris = _ctx(31, 'operator', TEZ, groups=[34])
    head = _ctx(300, 'admin', TEZ, headed=[TEZ])
    outsider = _ctx(50, 'operator', OP, departments={TEZ: {'enabled': True, 'group_ids': []},
                                                     OP: {'enabled': True, 'group_ids': []}})
    card = {'operator_id': 30, 'department_id': TEZ}
    assert access.can_edit_card(anna, card) and access.can_edit_card(head, card)
    assert access.can_view_card(boris, card) and not access.can_edit_card(boris, card)
    assert not access.can_view_card(outsider, card)
    request = {'created_by': 30, 'department_id': TEZ}
    assert access.can_view_request(anna, request) and access.can_act_on_request(head, request)
    assert not access.can_view_request(boris, request) and not access.can_view_request(outsider, request)
    manual = {'origin': 'manual', 'created_by': 30}
    assert access.can_unlink(anna, manual, TEZ) and access.can_unlink(head, manual, TEZ)
    assert not access.can_unlink(boris, manual, TEZ)
    assert not access.can_unlink(head, {'origin': 'tez', 'created_by': 30}, TEZ)
    assert access.capabilities(head) == {'manage': True, 'managed_department_ids': [TEZ], 'card': True,
                                         'card_department_id': TEZ, 'global_admin': False}


# ─── Telegram ────────────────────────────────────────────────────────────────

def test_request_message_names_the_person_and_keeps_the_tail():
    text = telegram.build_request_message(
        request_id=15, destination_title='Финансы', body='Не пришла <выплата> за 10.11',
        account={'kind': 'driver', 'tez_id': 12345, 'full_name': 'Иванов  Иван', 'city': 'Алматы'},
        phone='77012345678', topic_title='Выплаты → Не пришла выплата', author_name='Анна Оператор',
        mentions=[{'username': '@fin_lead'}, {'username': 'fin_lead'}])
    lines = text.split('\n')
    assert lines[0] == '📨 <b>Выплаты → Не пришла выплата</b>'
    assert 'Запрос №15</a> · Финансы' in lines[1] and 'view=client_cards&amp;cc_request=15' in lines[1]
    assert ('<b>Водитель:</b> Иванов Иван · <a href="https://admin.tezapp.org/admin/drivers/12345">ID 12345</a>'
            ' · Алматы') in text
    assert '<b>Телефон:</b> +7 701 234-56-78' in text
    assert 'Не пришла &lt;выплата&gt; за 10.11' in text
    assert lines[-2] == '🙍 <b>Оператор:</b> Анна Оператор'
    assert lines[-1] == '👥 <b>Ответственные:</b> @fin_lead'
    # Строки-инструкции «ответьте на это сообщение» нет — как у «Обращений».
    assert 'ответьте' not in text.lower()
    # Длинный текст режется, а оператор и теги остаются.
    long = telegram.build_request_message(request_id=1, destination_title='Отдел', body='я' * 5000,
                                          author_name='Анна', mentions=[{'username': 'lead'}])
    assert len(long) <= telegram.MESSAGE_LIMIT and long.endswith('@lead') and 'Анна' in long
    bare = telegram.build_request_message(request_id=2, destination_title='Отдел', body='Текст')
    assert bare.startswith('📨 ') and 'Запрос №2' in bare and 'Оператор' not in bare


def test_reply_and_status_texts():
    reply = telegram.build_reply_message(request_id=15, author_name='Анна', body='Уточнение <1>')
    assert reply.startswith('💬 <b>Уточнение по запросу №15</b>') and 'Уточнение &lt;1&gt;' in reply
    assert telegram.build_status_notice(request_id=15, status='resolved', actor_name='Анна') == \
        '✅ Запрос №15 — решён (Анна)'
    assert telegram.build_status_notice(request_id=15, status='cancelled') == '✅ Запрос №15 — отменён'


def test_alert_message_is_one_message_for_all_reasons():
    text = telegram.build_alert_message(department_name='Тез КЦ', window_min=60, items=[
        {'kind': rules.ALERT_NEW, 'title': 'Не приходят заказы', 'parent_title': 'Заказы', 'count': 12,
         'baseline': 3.2, 'cities': [{'city': 'Алматы', 'count': 8}, {'city': None, 'count': 4}], 'subtopics': []},
        {'kind': rules.ALERT_NEW, 'title': 'Выплаты', 'parent_title': None, 'count': 21, 'baseline': 0,
         'cities': [], 'subtopics': [{'title': 'Не пришла выплата', 'count': 15}]}])
    assert text.startswith('⚠️ <b>Рост обращений</b> · Тез КЦ')
    assert '<b>Заказы → Не приходят заказы</b>\n12 человек за последний час, обычно около 3' in text
    assert 'Города: Алматы — 8' in text and 'None' not in text
    assert '<b>Выплаты</b>\n21 человек за последний час, обычно таких обращений нет' in text
    assert 'Подтемы: Не пришла выплата — 15' in text
    assert 'view=client_cards&amp;cc_tab=report' in text
    growing = telegram.build_alert_message(department_name=None, window_min=90, items=[
        {'kind': rules.ALERT_GROWING, 'title': 'Причина', 'parent_title': None, 'count': 2, 'baseline': 1,
         'cities': [], 'subtopics': []}])
    assert growing.startswith('⚠️ <b>Обращений становится больше</b>')
    assert '2 человека за последние 90 мин, обычно около одного' in growing
    assert telegram._window_text(180) == 'за последние 3 ч'


# ─── Файлы и транспорт ───────────────────────────────────────────────────────

def test_file_kind_and_names():
    assert files.file_kind('Скрин.PNG', None) == files.KIND_PHOTO
    assert files.file_kind('запись', 'video/mp4') == files.KIND_VIDEO
    assert files.file_kind('экран.MOV', '') == files.KIND_VIDEO
    assert files.file_kind('акт.pdf', 'application/pdf') == files.KIND_DOCUMENT
    assert files.safe_name('C:\\Users\\op\\Скрин "экрана".png') == 'Скрин экрана.png'
    assert files.safe_name('../../etc/passwd') == 'passwd' and files.safe_name('') == 'file'
    assert files.is_stored('gs://bucket/path') and not files.is_stored('AgACAgIAAxkBAAI')


def test_telegram_error_carries_the_new_chat_id():
    error = transport._failure({'ok': False, 'description': 'Bad Request: group chat was upgraded',
                                'parameters': {'migrate_to_chat_id': -1003936452440}}, 400)
    assert str(error) == 'Bad Request: group chat was upgraded' and error.migrate_to == -1003936452440
    plain = transport._failure({'ok': False, 'description': 'Forbidden: bot was kicked'}, 403)
    assert plain.migrate_to is None and 'kicked' in plain
    assert transport._failure(None, 502) == 'HTTP 502'
    assert transport.result_file_id({'photo': [{'file_id': 'small'}, {'file_id': 'big'}]}) == 'big'
    assert transport.result_file_id({'video': {'file_id': 'v1'}}) == 'v1'
    assert transport.result_file_id({}) is None


# ─── TEZ APP ─────────────────────────────────────────────────────────────────

class _Response:
    def __init__(self, status_code, payload=None, text=''):
        self.status_code, self._payload, self.text = status_code, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


class _Session:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def post(self, url, data=None, headers=None, timeout=None):
        self.requests.append({'url': url, 'data': data, 'headers': headers, 'timeout': timeout})
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _config(**extra):
    config = {'enabled': True, 'base_url': 'https://api.tezapp.org', 'path': '/accounts/lookup',
              'token': 'test-token', 'admin_url': 'https://admin.tezapp.org'}
    config.update(extra)
    return config


ROW = {'type': 'driver', 'id': 12345, 'full_name': ' Иванов Иван ', 'phone': '+7 701 234 56 78', 'city': 'Алматы',
       'status': 'active', 'registered_at': '2026-11-05T09:03:11+00:00', 'last_order_at': None,
       'car': 'Chevrolet Cobalt', 'empty': ''}


def test_account_row_is_parsed_into_our_shape():
    account = tez.parse_account(ROW)
    assert (account['kind'], account['tez_id'], account['full_name'], account['phone'], account['city']) == (
        'driver', 12345, 'Иванов Иван', '77012345678', 'Алматы')
    # Время TEZ APP — в наше наивное время Алматы.
    assert account['registered_at'] == datetime(2026, 11, 5, 14, 3, 11) and account['last_order_at'] is None
    assert account['details'] == {'car': 'Chevrolet Cobalt'}
    assert tez.parse_account({'type': 'clients', 'id': '67890'})['kind'] == 'client'
    for bad in (None, {}, {'type': 'driver'}, {'type': 'park', 'id': 1}, {'type': 'driver', 'id': 'x'},
                {'type': 'driver', 'id': 0}):
        assert tez.parse_account(bad) is None


def test_lookup_by_phone_and_by_id():
    session = _Session(_Response(200, {'accounts': [ROW, {'type': 'client', 'id': 67890}, {'junk': 1}]}),
                       _Response(200, {'accounts': [ROW]}), _Response(200, {'accounts': []}))
    client = tez.TezAccounts(_config(), session=session)
    found = client.by_phone('77012345678')
    assert found['ok'] and found['source'] == tez.SOURCE_ACCOUNTS and found['driver_hint'] is None
    assert [(a['kind'], a['tez_id']) for a in found['accounts']] == [('driver', 12345), ('client', 67890)]
    request = session.requests[0]
    assert request['url'] == 'https://api.tezapp.org/accounts/lookup'
    assert request['headers']['X-Integration-Token'] == 'test-token' and 'Mozilla' in request['headers']['User-Agent']
    assert request['data'] == b'{"phone": "+77012345678"}' and request['timeout'] == (tez.CONNECT_TIMEOUT, tez.READ_TIMEOUT)
    # Тот же номер за минуту второй раз не спрашиваем; забыли — спрашиваем.
    assert client.by_phone('77012345678') is found and len(session.requests) == 1
    assert client.by_id('driver', 12345)['account']['tez_id'] == 12345
    assert session.requests[1]['data'] == b'{"type": "driver", "id": 12345}'
    missing = client.by_id('client', 5)
    assert missing['ok'] is True and missing['account'] is None


@pytest.mark.parametrize('reply, error', [
    (_Response(500), 'TEZ APP ответил ошибкой 500'), (_Response(401, text='nope'), 'TEZ APP не принял ключ портала'),
    (_Response(200, {'drivers': []}), 'TEZ APP ответил не по договору'), (_Response(200), 'TEZ APP ответил не по договору'),
    (_Response(404), 'В TEZ APP нет метода поиска аккаунтов'),
])
def test_tez_failures_are_unknown_not_empty(reply, error):
    client = tez.TezAccounts(_config(), session=_Session(reply, reply))
    found = client.by_phone('77012345678')
    assert found['ok'] is False and found['error'] == error and found['accounts'] == []
    # Отказ не запоминается: следующий звонок спрашивает заново.
    assert client.by_phone('77012345678')['ok'] is False
    assert len(client._session.requests) == 2


def test_network_error_never_raises():
    import requests

    client = tez.TezAccounts(_config(), session=_Session(requests.ConnectionError('boom')))
    assert client.by_id('driver', 1) == {'source': tez.SOURCE_ACCOUNTS, 'ok': False,
                                         'error': 'TEZ APP не отвечает', 'account': None}
    # 404 с JSON — «аккаунтов нет», а не «метода нет».
    client = tez.TezAccounts(_config(), session=_Session(_Response(404, {'error': 'not found'})))
    assert client.by_phone('77012345678')['accounts'] == [] and client.by_phone('77012345678')['ok'] is True


def test_until_the_method_exists_only_driver_dates_are_asked():
    calls = []

    def first_orders(phone):
        calls.append(phone)
        return {'account_known': True, 'registered': '2026-11-05T14:03:11+05:00', 'last_order': None}

    session = _Session()
    client = tez.TezAccounts(_config(enabled=False), session=session, first_orders_fetch=first_orders)
    assert client.source() == tez.SOURCE_FIRST_ORDERS
    found = client.by_phone('77012345678')
    assert found['ok'] and found['accounts'] == [] and session.requests == []
    assert found['driver_hint'] == {'exists': True, 'registered_at': datetime(2026, 11, 5, 14, 3, 11),
                                    'last_order_at': None}
    # Проверить ссылку нечем — аккаунт закрепится как есть, без запроса в TEZ APP.
    assert client.by_id('driver', 12345) == {'source': tez.SOURCE_FIRST_ORDERS, 'ok': False, 'error': None,
                                             'account': None}
    assert calls == ['77012345678']
    no_account = tez.TezAccounts(_config(enabled=False), first_orders_fetch=lambda phone: {
        'account_known': True, 'registered': None, 'last_order': None})
    assert no_account.by_phone('77012345678')['driver_hint'] == {'exists': False}
    unknown = tez.TezAccounts(_config(enabled=False), first_orders_fetch=lambda phone: {'account_known': False})
    assert unknown.by_phone('77012345678')['driver_hint'] is None

    def broken(phone):
        raise RuntimeError('HTTP 503')

    failed = tez.TezAccounts(_config(enabled=False), first_orders_fetch=broken).by_phone('77012345678')
    assert failed['ok'] is False and failed['error'] == 'TEZ APP не отвечает'
    off = tez.TezAccounts(_config(token=None))
    assert off.source() == tez.SOURCE_OFF and off.by_phone('77012345678')['ok'] is False


# ─── Раздел подключён к порталу и телефону ───────────────────────────────────

def _read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def test_schema_is_part_of_database_init():
    schema_py = _read('db/schema.py')
    assert 'self._init_client_cards_schema_tx(cursor)' in schema_py
    assert 'from client_cards.schema import init_client_cards_schema' in schema_py
    # Свой SAVEPOINT: сбой схемы раздела не роняет старт портала.
    body = schema_py[schema_py.index('def _init_client_cards_schema_tx'):]
    body = body[:body.index('\n    def ', 10)]
    assert 'SAVEPOINT client_cards_schema' in body and 'ROLLBACK TO SAVEPOINT client_cards_schema' in body


def test_section_is_wired_into_the_portal():
    source = _read('bot_schedule2.py')
    # Обработчик ответов из групп — третьим в цепочке, сразу за «Обращениями» и «Жалобами»,
    # до десятков обработчиков без фильтра по типу чата.
    crm = source.index('crm_bot.register(dp, db, crm_pool, types)')
    complaints = source.index('complaints_bot.register(dp, db, crm_pool, types)')
    cards = source.index("client_cards_bot.register(dp, lambda: _CLIENT_CARDS.get('service'), crm_pool, types)")
    first_plain_handler = source.index('@dp.message_handler', cards)
    assert crm < complaints < cards < first_plain_handler
    assert source.index('auth_maintenance_executor = ThreadPoolExecutor') > cards
    # Замок QR объявлен выше сборки Blueprint: он строится на уровне модуля.
    assert source.index('def _client_cards_qr_locked(') < source.index('build_client_cards_blueprint(')
    assert 'qr_locked=_client_cards_qr_locked' in source
    assert '"client_cards_access": client_cards_access' in source
    assert '"client_card": _client_cards_phone_settings(requester_id)' in source
    # Сбой раздела не оставляет оператора без регистрации на станции.
    block = source[source.index('def _client_cards_phone_settings('):]
    block = block[:block.index('\ndef ', 10)] if '\ndef ' in block[10:] else block
    assert block.count('{"enabled": False}') == 2


def test_only_tez_is_seeded_and_disabled():
    from client_cards import schema

    assert schema._SEED_DEPARTMENT_CODES == ('tez',)
    settings = next(statement for statement in schema._STATEMENTS if 'client_card_settings' in statement)
    assert re.search(r'enabled\s+BOOLEAN NOT NULL DEFAULT FALSE', settings)
    tables = [statement for statement in schema._STATEMENTS if schema._is_table(statement)]
    assert len(tables) == 10
    # Причину с обращениями нельзя удалить — только скрыть.
    topics = next(statement for statement in tables if 'client_card_topics' in statement)
    assert topics.count('ON DELETE RESTRICT') == 2

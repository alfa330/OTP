# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: PAYMENTS_TEST_PORT — порт локального Postgres.

Письма поставщикам о смене реквизитов наших компаний (просьба владельца
20.10.2026; с 27.10.2026 — «пока убери автоотправку и сделай полу ручной»: письмо
открывают в почте компании кнопкой «Отправить письмо», отправляет человек). В
tests/test_payments_requisites_mail.py видно только, что запросы написаны; что они
ЗНАЧАТ — когда письмо ждёт, что записывает «открыто в почте» и кто в письме,
«Не отправлять», галочки «Кому», что знают поставщики и что стало с письмами,
которые портал отправлял до 27.10.2026, — исполняется здесь: настоящая схема
раздела (payments.schema).

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Запуск:

    PAYMENTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_payments_requisites_mail_postgres.py

Люди и компании выдуманы.
"""
import contextlib
import os
import uuid

import pytest

from payments import directory, requisites_mail as rm, schema

PORT = os.environ.get('PAYMENTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PAYMENTS_TEST_PORT)')

ADMIN = {'id': 10, 'name': 'Админова Алия'}

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL DEFAULT 'operator',
                        status text NOT NULL DEFAULT 'working', department_id integer, supervisor_id integer,
                        telegram_id bigint, job_title text, avatar_bucket text, avatar_blob_path text);
    INSERT INTO users (id, name) VALUES (10, 'Админова Алия'), (11, 'Другой Админ');
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '10s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def _get_cursor(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


@pytest.fixture
def db():
    name = 't_payments_requisites_mail_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            for sql in schema.statements():
                cur.execute(sql)
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


ENTITY = {'name': 'ТОО «Наша компания»', 'bin': '123456789012', 'legal_address': 'Алматы, Абая 1',
          'iik': 'KZ11TEST0000000001', 'bank_name': 'АО «Альфа»', 'bik': 'ALFAKZKA', 'kbe': '17',
          'vat_payer': False, 'requisites': None}


def new_entity(cur, **fields):
    """Наша компания."""
    values = dict(ENTITY, **fields)
    cols = ', '.join(values)
    cur.execute('INSERT INTO payment_legal_entities (%s) VALUES (%s) RETURNING id'
                % (cols, ', '.join(['%s'] * len(values))), list(values.values()))
    return cur.fetchone()[0]


def supplier(cur, name, emails=(), active=True, contact=None):
    """Поставщик; адреса — строками «Почты поставщиков» (поле emails — их копия)."""
    cur.execute("INSERT INTO payment_counterparties (name, is_active, contact) VALUES (%s, %s, %s) "
                "RETURNING id", (name, active, contact))
    counterparty_id = cur.fetchone()[0]
    directory.replace_counterparty_emails(cur, counterparty_id, list(emails))
    return counterparty_id


def save(cur, entity_id, **changes):
    """Так пишет ручка справочника: «как было» под блокировкой, запись, письмо."""
    before = directory.read_legal_entity(cur, entity_id, lock=True)
    sets = ', '.join('%s = %%s' % col for col in changes)
    cur.execute('UPDATE payment_legal_entities SET %s WHERE id = %%s' % sets, list(changes.values()) + [entity_id])
    return rm.after_entity_saved(cur, entity_id, before, directory.read_legal_entity(cur, entity_id), ADMIN)


def recipients(cur, mailing_id):
    cur.execute("SELECT email, status, sent_at IS NOT NULL, counterparty_names "
                "FROM payment_requisites_mail_recipients WHERE mailing_id = %s ORDER BY email", (mailing_id,))
    return [(r[0], r[1], r[2], list(r[3])) for r in cur.fetchall()]


def history(cur, entity_id):
    return rm.history(cur, entity_id)[0]


def test_schema_runs_twice(db):
    """Миграции 27.10.2026 (кто открыл письмо, «Почта для писем») — идемпотентны: схема идёт на каждом старте."""
    with db._get_cursor() as cur:
        for sql in schema.statements():
            cur.execute(sql)
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
                    "AND ((table_name = 'payment_requisites_mailings' AND column_name LIKE 'opened%') "
                    "  OR (table_name = 'payment_legal_entities' AND column_name LIKE 'letters%')) ORDER BY 1")
        assert [row[0] for row in cur.fetchall()] == ['letters_email', 'letters_provider', 'opened_at', 'opened_by',
                                                      'opened_by_name']


def test_emails_column_defaults_and_search(db):
    with db._get_cursor() as cur:
        cur.execute("INSERT INTO payment_counterparties (name) VALUES ('ТОО «Без почты»')")
        romashka = supplier(cur, 'ТОО «Ромашка»', ['buh@romashka.kz'])
        found = {item['name']: item['emails'] for item in directory.list_counterparties(cur, include_inactive=True)}
        assert found == {'ТОО «Без почты»': [], 'ТОО «Ромашка»': ['buh@romashka.kz']}
        assert [item['id'] for item in directory.list_counterparties(cur, query='buh@rom')] == [romashka], \
            'поиск по справочнику находит и по почте'
        created, _new = directory.find_or_create_counterparty(cur, 'ТОО «Из заявки»')
        assert directory.read_counterparty(cur, created)['emails'] == []


def test_save_creates_merges_and_reverts(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        assert save(cur, entity, note='только примечание') is None, 'не реквизиты — письма нет'
        first = save(cur, entity, iik='KZ22TEST0000000002')
        assert first['status'] == 'pending' and 'ready' not in first, 'письмо ждёт кнопки, почту не спрашиваем'
        cur.execute("SELECT abs(extract(epoch FROM send_after - %s)) < 60, before_data->>'iik', after_data->>'iik' "
                    "FROM payment_requisites_mailings WHERE id = %%s" % rm.NOW_SQL, (first['id'],))
        assert cur.fetchone() == (True, 'KZ11TEST0000000001', 'KZ22TEST0000000002'), \
            'send_after — когда письмо готово: сразу, без десяти минут'
        cur.execute("UPDATE payment_requisites_mailings SET send_after = send_after - interval '5 minutes'")
        cur.execute("SELECT send_after FROM payment_requisites_mailings")
        moved = cur.fetchone()[0]
        again = save(cur, entity, note='ещё примечание', is_active=False)
        cur.execute("SELECT send_after FROM payment_requisites_mailings")
        assert again['id'] == first['id'] and cur.fetchone()[0] == moved, \
            'правка не реквизитов письмо не трогает'
        merged = save(cur, entity, bank_name='АО «Бета»')
        cur.execute("SELECT COUNT(*), MIN(send_after) > %s, MIN(after_data->>'bank_name'), MIN(before_data->>'bank_name') "
                    "FROM payment_requisites_mailings", (moved,))
        assert merged['id'] == first['id'] and cur.fetchone() == (1, True, 'АО «Бета»', 'АО «Альфа»'), \
            'вторая правка попадает в то же письмо и делает его свежим'
        assert rm.known_now(cur, entity, None)['iik'] == 'KZ11TEST0000000001', 'форма сверяет с «как было» письма'
        assert save(cur, entity, iik='KZ11TEST0000000001', bank_name='АО «Альфа»') == {'status': 'reverted'}
        cur.execute("SELECT COUNT(*) FROM payment_requisites_mailings")
        assert cur.fetchone()[0] == 0, 'вернули как было — письма нет'


def test_open_in_mail_records_who_when_and_the_addresses(db):
    """«Отправить письмо» (27.10.2026): письмо — «открыто в почте», в истории — адреса из «Скрытой копии»."""
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        x = supplier(cur, 'ТОО «Икс»', ['x@x.kz'])
        supplier(cur, 'ТОО «Игрек»', ['y@y.kz', 'shared@group.kz'])
        supplier(cur, 'ТОО «Зет»', ['shared@group.kz'], active=False)
        supplier(cur, 'ТОО «Без почты»')
        mailing = save(cur, entity, kbe='19')['id']
        rm.set_recipients(cur, entity, mailing, ADMIN, [x])
        assert rm.open_in_mail(cur, entity, mailing, {'id': 11, 'name': 'Другой Админ'},
                               [' Shared@Group.kz', 'y@y.kz', 'shared@group.kz']) == {'addresses': 2}
        cur.execute("SELECT status, opened_at IS NOT NULL, opened_by, opened_by_name, updated_by_name "
                    "FROM payment_requisites_mailings WHERE id = %s", (mailing,))
        assert cur.fetchone() == ('opened', True, 11, 'Другой Админ', 'Админова Алия'), \
            'кто открыл — отдельно; автор правки реквизитов прежний'
        assert recipients(cur, mailing) == [('shared@group.kz', 'opened', True, ['ТОО «Зет»', 'ТОО «Игрек»']),
                                            ('y@y.kz', 'opened', True, ['ТОО «Игрек»'])], \
            'общий адрес — один раз, с обоими поставщиками (скрытый — тоже)'
        item = history(cur, entity)[0]
        assert (item['status'], item['latest'], item['opened_by_name']) == ('opened', True, 'Другой Админ')
        assert item['counts'] == {'total': 2, 'opened': 2, 'sent': 0, 'failed': 0, 'removed': 0, 'superseded': 0}
        assert item['excluded_names'] == ['ТОО «Икс»'] and 'candidates' not in item
        assert rm.states(cur)[0][entity] == {'id': mailing, 'status': 'opened'}, 'метки «не отправлено» в строке нет'
        for action in (lambda: rm.open_in_mail(cur, entity, mailing, ADMIN, ['y@y.kz']),
                       lambda: rm.cancel(cur, entity, mailing, ADMIN),
                       lambda: rm.set_recipients(cur, entity, mailing, ADMIN, [])):
            with pytest.raises(rm.MailingError, match='уже открыто в почте'):
                action()
        with pytest.raises(rm.MailingError, match='не найдено'):
            rm.open_in_mail(cur, entity + 1000, mailing, ADMIN, ['y@y.kz'])


def test_open_in_mail_takes_what_the_button_put_in(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        x = supplier(cur, 'ТОО «Икс»', ['x@x.kz'])
        supplier(cur, 'ТОО «Игрек»', ['y@y.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        rm.set_recipients(cur, entity, mailing, ADMIN, [x])
        # Старая вкладка без списка — адреса отмеченных поставщиков на этот момент.
        assert rm.open_in_mail(cur, entity, mailing, ADMIN) == {'addresses': 1}
        assert recipients(cur, mailing) == [('y@y.kz', 'opened', True, ['ТОО «Игрек»'])]
        second = save(cur, entity, kbe='20')['id']
        # Адрес снятого поставщика или убранный из карточки, пока окно было открыто: в письме он был — пишем как есть.
        assert rm.open_in_mail(cur, entity, second, ADMIN, ['x@x.kz', 'gone@old.kz']) == {'addresses': 2}
        assert recipients(cur, second) == [('gone@old.kz', 'opened', True, []), ('x@x.kz', 'opened', True, ['ТОО «Икс»'])]
        third = save(cur, entity, kbe='21')['id']
        with pytest.raises(rm.MailingError, match='не похоже на адрес') as caught:
            rm.open_in_mail(cur, entity, third, ADMIN, ['y@y.kz', 'не адрес'])
        assert (caught.value.status, caught.value.code) == (400, 'PAYMENTS_EMAIL_INVALID')
        with pytest.raises(rm.MailingError, match='некому отправить'):
            rm.open_in_mail(cur, entity, third, ADMIN, [])
        with pytest.raises(rm.MailingError, match='Слишком много'):
            rm.open_in_mail(cur, entity, third, ADMIN, ['a%s@x.kz' % i for i in range(rm.MAX_LETTER_ADDRESSES + 1)])
        cur.execute("SELECT status FROM payment_requisites_mailings WHERE id = %s", (third,))
        assert cur.fetchone()[0] == 'pending', 'отказ письмо не открывает'
        assert recipients(cur, third) == []


def test_known_is_the_last_opened_letter(db):
    """Два письма открыли в почте (ИИК A→B, затем B→C) — поставщики знают C, и следующее «было» говорит C."""
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        first = save(cur, entity, iik='KZ22TEST0000000002')['id']
        rm.open_in_mail(cur, entity, first, ADMIN, ['a@alfa.kz'])
        second = save(cur, entity, iik='KZ33TEST0000000003')['id']
        cur.execute("SELECT before_data->>'iik' FROM payment_requisites_mailings WHERE id = %s", (second,))
        assert cur.fetchone()[0] == 'KZ22TEST0000000002', 'после открытого письма — новое, «было» из него'
        rm.open_in_mail(cur, entity, second, ADMIN, ['a@alfa.kz'])
        assert rm.known(cur, entity)['iik'] == 'KZ33TEST0000000003'
        third = save(cur, entity, kbe='19')['id']
        cur.execute("SELECT before_data->>'iik' FROM payment_requisites_mailings WHERE id = %s", (third,))
        assert cur.fetchone()[0] == 'KZ33TEST0000000003'
        assert [item['latest'] for item in history(cur, entity)] == [True, False, False], \
            '«Открыть в почте ещё раз» — только у последнего письма'
        assert save(cur, entity, kbe='17') == {'status': 'reverted'}, 'вернули к тому, что знают поставщики, — письма нет'


def test_iik_written_differently_is_not_a_change(db):
    """ИИК в строке записан когда-то иначе: правка одного примечания письма не рождает."""
    with db._get_cursor() as cur:
        entity = new_entity(cur, iik='kz11 test 0000000001')
        # ручка пишет ИИК верхним регистром без пробелов (_iban); БИН — цифрами (случаи — в общем JSON)
        assert save(cur, entity, iik='KZ11TEST0000000001', note='примечание') is None


def test_one_pending_letter_per_company(db):
    import psycopg2
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        save(cur, entity, kbe='19')
    with pytest.raises(psycopg2.IntegrityError):
        with db._get_cursor() as cur:
            cur.execute("INSERT INTO payment_requisites_mailings (legal_entity_id, before_data, after_data, send_after) "
                        "VALUES (%s, '{}', '{}', now())", (entity,))


def test_cancel(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        assert rm.states(cur)[0][entity] == {'id': mailing, 'status': 'pending'}
        rm.cancel(cur, entity, mailing, ADMIN)
        cur.execute("SELECT status, cancelled_by_name FROM payment_requisites_mailings WHERE id = %s", (mailing,))
        assert cur.fetchone() == ('cancelled', 'Админова Алия')
        assert entity not in rm.states(cur)[0], 'отменённое в строке компании не висит'
        assert rm.states(cur)[1] == {entity: 1}, 'а в счёт писем входит'
        with pytest.raises(rm.MailingError, match='уже отменено'):
            rm.cancel(cur, entity, mailing, ADMIN)
        with pytest.raises(rm.MailingError, match='уже отменено'):
            rm.open_in_mail(cur, entity, mailing, ADMIN, ['a@alfa.kz'])
        with pytest.raises(rm.MailingError, match='не найдено'):
            rm.cancel(cur, entity + 1, mailing, ADMIN)
        assert history(cur, entity)[0]['status'] == 'cancelled'
        again = save(cur, entity, kbe='20')
        assert again['id'] != mailing, 'после отмены следующая правка — новое письмо'
        cur.execute("SELECT before_data->>'kbe' FROM payment_requisites_mailings WHERE id = %s", (again['id'],))
        assert cur.fetchone()[0] == '17', '«как было» — то, что знают поставщики, а не отменённое'
        items = history(cur, entity)
        assert [item['id'] for item in items] == [again['id'], mailing], 'последнее письмо — сверху'
        assert rm.states(cur)[0][entity]['id'] == again['id']


def test_cancel_then_revert_sends_nothing(db):
    """Сохранили опечатку, «Не отправлять», вернули как было — письма поставщикам нет."""
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        typo = save(cur, entity, iik='KZ11TEST000000000X')['id']
        rm.cancel(cur, entity, typo, ADMIN)
        assert rm.known_now(cur, entity, directory.read_legal_entity(cur, entity))['iik'] == 'KZ11TEST0000000001'
        assert save(cur, entity, iik='KZ11TEST0000000001') == {'status': 'reverted'}
        cur.execute("SELECT status FROM payment_requisites_mailings ORDER BY id")
        assert cur.fetchall() == [('cancelled',)]


def test_cancelled_change_shows_up_in_next_letter(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        rm.cancel(cur, entity, save(cur, entity, iik='KZ22TEST0000000002')['id'], ADMIN)
        mailing = save(cur, entity, kbe='19')['id']
        item = history(cur, entity)[0]
        text = item['letter']['text']
        assert '— ИИК: было «KZ11TEST0000000001», стало «KZ22TEST0000000002»' in text, \
            'поставщик не знал нового ИИК — письмо говорит и о нём'
        assert '— КБЕ: было «17», стало «19»' in text
        assert item['id'] == mailing and item['changed'] == ['ИИК', 'КБЕ']
        assert 'KZ22TEST0000000002' in item['letter']['paste'] and '<body' not in item['letter']['paste'], \
            'в буфер — то же письмо, без страницы вокруг'


def test_company_with_letters_is_not_deleted(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        save(cur, entity, kbe='19')
        assert directory.usage(cur, 'legal_entities', entity) == [('в письмах поставщикам', 1)]


def test_letters_email_is_a_company_field(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur, letters_email='buh@company.kz', letters_provider='gmail')
        row = directory.read_legal_entity(cur, entity)
        assert (row['letters_email'], row['letters_provider']) == ('buh@company.kz', 'gmail')
        listed = {item['id']: item for item in directory.list_legal_entities(cur, include_inactive=True)}
        assert listed[entity]['letters_email'] == 'buh@company.kz'
        assert save(cur, entity, letters_email='other@company.kz', letters_provider=None) is None, \
            'почта для писем — не реквизит: письма не рождает'


def test_adopt_contact_emails_once(db):
    with db._get_cursor() as cur:
        from_contact = supplier(cur, 'ТОО «Контакт»', contact='Айдос, +7 700 000 00 00, Buh@Kontakt.kz')
        own = supplier(cur, 'ТОО «Своя почта»', ['own@x.kz'], contact='other@x.kz')
        phone = supplier(cur, 'ТОО «Телефон»', contact='+7 701 000 00 00')
        assert rm.adopt_contact_emails(cur) == 1
        cur.execute("SELECT id, emails FROM payment_counterparties ORDER BY id")
        assert {row[0]: list(row[1]) for row in cur.fetchall()} == {
            from_contact: ['buh@kontakt.kz'], own: ['own@x.kz'], phone: []}
        cur.execute("UPDATE payment_counterparties SET emails = '{}' WHERE id = %s", (from_contact,))
        assert rm.adopt_contact_emails(cur) == 0, 'второй раз не срабатывает: стёртую руками почту не возвращает'
        cur.execute("SELECT emails FROM payment_counterparties WHERE id = %s", (from_contact,))
        assert list(cur.fetchone()[0]) == []


def test_mailbox_address_becomes_letters_email_once(db):
    """27.10.2026: почта, подключённая для автоотправки, — «Почтой для писем»; сам ящик остаётся в таблице."""
    def mailbox_row(cur, entity_id, address, provider):
        cur.execute("INSERT INTO payment_entity_mailboxes (legal_entity_id, address, provider, host, port, security, login, "
                    "password_enc) VALUES (%s, %s, %s, 'smtp.example.kz', 465, 'ssl', %s, 'зашифровано')",
                    (entity_id, address, provider, address))

    with db._get_cursor() as cur:
        connected = new_entity(cur)
        own = new_entity(cur, name='ТОО «Своя почта»', letters_email='own@company.kz')
        bare = new_entity(cur, name='ТОО «Без почты»')
        mailbox_row(cur, connected, 'buh@company.kz', 'gmail')
        mailbox_row(cur, own, 'box@company.kz', None)
        assert rm.adopt_mailbox_addresses(cur) == 1
        cur.execute("SELECT id, letters_email, letters_provider FROM payment_legal_entities ORDER BY id")
        assert cur.fetchall() == [(connected, 'buh@company.kz', 'gmail'), (own, 'own@company.kz', None), (bare, None, None)], \
            'вписанную руками почту не трогаем'
        cur.execute("SELECT COUNT(*) FROM payment_entity_mailboxes")
        assert cur.fetchone()[0] == 2, '«пока убери» — ящики и доступ к ним на месте'
        cur.execute("UPDATE payment_legal_entities SET letters_email = NULL WHERE id = %s", (connected,))
        assert rm.adopt_mailbox_addresses(cur) == 0, 'второй раз не срабатывает: стёртую руками почту не возвращает'


def test_unsent_auto_letter_is_closed(db):
    """Письмо, которое портал отправлял в момент выкладки: никто его не допошлёт — «не ушло» с причиной."""
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz', 'b@alfa.kz', 'c@alfa.kz', 'd@alfa.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        cur.execute("UPDATE payment_requisites_mailings SET status = 'sending' WHERE id = %s", (mailing,))
        for email, status, retry, error in (('a@alfa.kz', 'sent', False, None), ('b@alfa.kz', 'queued', False, None),
                                            ('c@alfa.kz', 'failed', True, '421 попробуйте позже'),
                                            ('d@alfa.kz', 'failed', False, '550 No such user')):
            cur.execute("INSERT INTO payment_requisites_mail_recipients (mailing_id, email, status, next_attempt_at, "
                        "last_error) VALUES (%%s, %%s, %%s, CASE WHEN %%s THEN %s END, %%s)" % rm.NOW_SQL,
                        (mailing, email, status, retry, error))
        assert rm.close_auto_sending(cur) == 1
        cur.execute("SELECT email, status, next_attempt_at IS NULL, last_error FROM payment_requisites_mail_recipients "
                    "ORDER BY email")
        assert cur.fetchall() == [('a@alfa.kz', 'sent', True, None), ('b@alfa.kz', 'failed', True, rm.AUTO_OFF_ERROR),
                                  ('c@alfa.kz', 'failed', True, rm.AUTO_OFF_ERROR),
                                  ('d@alfa.kz', 'failed', True, '550 No such user')]
        item = history(cur, entity)[0]
        assert (item['status'], item['counts']['sent'], item['counts']['failed']) == ('done', 1, 3)
        assert rm.known(cur, entity)['kbe'] == '19', 'письмо ушло хотя бы части поставщиков — новые реквизиты им известны'
        assert rm.close_auto_sending(cur) == 0, 'на следующем старте делать нечего'


def test_journal_and_detail(db):
    """«Письма поставщикам»: все письма раздела — последние сверху, страницами; одно письмо — целиком."""
    with db._get_cursor() as cur:
        first_entity = new_entity(cur)
        second_entity = new_entity(cur, name='ТОО «Вторая»')
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        supplier(cur, 'ТОО «Бета»', ['b@beta.kz'])
        opened = save(cur, first_entity, iik='KZ22TEST0000000002')['id']
        rm.open_in_mail(cur, first_entity, opened, ADMIN, ['a@alfa.kz', 'b@beta.kz'])
        waiting = save(cur, second_entity, kbe='19')['id']
        cancelled = save(cur, first_entity, bik='BETAKZKA')['id']
        rm.cancel(cur, first_entity, cancelled, ADMIN)
        items, total = rm.journal(cur, limit=2)
        assert total == 3 and [item['id'] for item in items] == [cancelled, waiting]
        assert [item['latest'] for item in items] == [False, True], 'отменённое — не последнее письмо компании'
        older, total = rm.journal(cur, limit=2, offset=2)
        assert total == 3 and [item['id'] for item in older] == [opened]
        item = older[0]
        assert (item['legal_entity_name'], item['subject']) == ('ТОО «Наша компания»', 'Изменение реквизитов ТОО «Наша компания»')
        assert item['counts'] == {'total': 2, 'opened': 2, 'sent': 0, 'failed': 0, 'removed': 0, 'superseded': 0}
        assert (item['status'], item['latest'], item['changed'], item['opened_by_name']) == ('opened', True, ['ИИК'], 'Админова Алия')
        assert item['opened_at'] is not None and 'letter' not in item, 'в списке — без тела письма'
        assert rm.journal(cur, limit=2, offset=10) == ([], 0)
        full = rm.detail(cur, opened)
        assert [(r['email'], r['status'], r['counterparty_names']) for r in full['recipients']] == [
            ('a@alfa.kz', 'opened', ['ТОО «Альфа»']), ('b@beta.kz', 'opened', ['ТОО «Бета»'])]
        assert 'KZ22TEST0000000002' in full['letter']['html'] and full['letter']['subject'] == item['subject']
        assert set(full['letter']) == {'subject', 'text', 'html', 'paste'}
        assert full['counts'] == item['counts'] and full['latest'] is True
        pending = rm.detail(cur, waiting)
        assert pending['recipients'] == [] and [c['emails'] for c in pending['candidates']] == [['a@alfa.kz'], ['b@beta.kz']], \
            'ждущее письмо: кому — галочками'
        with pytest.raises(rm.MailingError, match='не найдено'):
            rm.detail(cur, 999999)


def test_checkmarks_change_only_while_letter_waits(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        x = supplier(cur, 'ТОО «Икс»', ['x@x.kz'])
        y = supplier(cur, 'ТОО «Игрек»', ['y@y.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        item = history(cur, entity)[0]
        assert [(c['name'], c['send']) for c in item['candidates']] == [('ТОО «Игрек»', True), ('ТОО «Икс»', True)], \
            'отмечены все поставщики с почтой'
        cur.execute("SELECT send_after, updated_by_name FROM payment_requisites_mailings WHERE id = %s", (mailing,))
        before = cur.fetchone()
        assert rm.set_recipients(cur, entity, mailing, {'id': 11, 'name': 'Другой Админ'}, [x, 999999]) == {'excluded': 1}, \
            'несуществующий поставщик не записывается'
        cur.execute("SELECT send_after, updated_by_name FROM payment_requisites_mailings WHERE id = %s", (mailing,))
        assert cur.fetchone() == before, 'галочки не меняют письмо и автора правки'
        assert rm.summary(cur, mailing)['recipients']['excluded'] == 1
        late = supplier(cur, 'ТОО «Поздний»', ['late@x.kz'])
        assert next(c for c in history(cur, entity)[0]['candidates'] if c['id'] == late)['send'], \
            'кому почту дописали, пока письмо ждало, — отмечен'
        rm.set_recipients(cur, entity, mailing, ADMIN, [])
        assert all(c['send'] for c in history(cur, entity)[0]['candidates'])
        with pytest.raises(rm.MailingError, match='не найдено'):
            rm.set_recipients(cur, entity + 1000, mailing, ADMIN, [])
        rm.set_recipients(cur, entity, mailing, ADMIN, [y])
        rm.open_in_mail(cur, entity, mailing, ADMIN, ['late@x.kz', 'x@x.kz'])
        assert history(cur, entity)[0]['excluded_names'] == ['ТОО «Игрек»']
        with pytest.raises(rm.MailingError, match='уже открыто в почте'):
            rm.set_recipients(cur, entity, mailing, ADMIN, [])


def test_supplier_email_directory_keeps_the_card_field_in_sync(db):
    """«Почта поставщиков» — строки; поле emails у поставщика — их копия в том же порядке."""
    def card(cur, counterparty_id):
        return directory.read_counterparty(cur, counterparty_id)['emails']

    with db._get_cursor() as cur:
        alfa = supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        beta = supplier(cur, 'ТОО «Бета»')
        row = directory.upsert_counterparty_email(cur, fields={'counterparty_id': alfa, 'email': 'buh@alfa.kz',
                                                              'contact_name': 'Айгерим, бухгалтер', 'note': None})
        assert card(cur, alfa) == ['a@alfa.kz', 'buh@alfa.kz'], 'новый адрес — последним'
        assert directory.find_counterparty_email(cur, alfa, 'buh@alfa.kz') == row
        assert directory.find_counterparty_email(cur, alfa, 'buh@alfa.kz', exclude_id=row) is None
        assert directory.count_counterparty_emails(cur, alfa) == 2
        directory.upsert_counterparty_email(cur, email_id=row, fields={'counterparty_id': beta, 'email': 'buh@alfa.kz',
                                                                       'contact_name': 'Айгерим', 'note': 'перешла'})
        assert (card(cur, alfa), card(cur, beta)) == (['a@alfa.kz'], ['buh@alfa.kz']), 'перенесли — у обоих копия своя'
        directory.replace_counterparty_emails(cur, beta, ['first@beta.kz', 'buh@alfa.kz'])
        listed = {(item['email'], item['counterparty_name']): item for item in directory.list_counterparty_emails(cur)}
        assert listed[('buh@alfa.kz', 'ТОО «Бета»')]['contact_name'] == 'Айгерим', \
            'карточка переписала список — контактное лицо у оставшегося адреса цело'
        assert card(cur, beta) == ['first@beta.kz', 'buh@alfa.kz']
        assert [item['email'] for item in directory.list_counterparty_emails(cur, query='айгер')] == ['buh@alfa.kz']
        assert [item['email'] for item in directory.list_counterparty_emails(cur, counterparty_id=alfa)] == ['a@alfa.kz']
        assert directory.delete_row(cur, 'counterparty_emails', row) == 1
        assert card(cur, beta) == ['first@beta.kz']
        cur.execute("UPDATE payment_counterparties SET emails = '{old@alfa.kz, a@alfa.kz}' WHERE id = %s", (alfa,))
        assert directory.adopt_email_array(cur) == 1, 'адрес, записанный прежней версией в поле, становится строкой'
        assert directory.adopt_email_array(cur) == 0
        assert 'counterparty_emails' in directory.DICTIONARIES and directory.usage(cur, 'counterparty_emails', 1) == []


def test_supplier_email_directory_shows_the_last_letter(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz', 'typo@alfa.kzz'])
        mailing = save(cur, entity, kbe='19')['id']
        assert all(item['last_letter'] is None for item in directory.list_counterparty_emails(cur)), \
            'ждущее письмо — ещё не письмо на адрес'
        rm.open_in_mail(cur, entity, mailing, ADMIN, ['a@alfa.kz'])
        state = {item['email']: item['last_letter'] for item in directory.list_counterparty_emails(cur)}
        assert state['a@alfa.kz']['status'] == 'opened' and state['a@alfa.kz']['sent_at'] is not None
        assert state['typo@alfa.kzz'] is None, 'адреса не было в письме — и писем на него не было'

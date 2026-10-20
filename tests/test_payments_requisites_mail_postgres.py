# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: PAYMENTS_TEST_PORT — порт локального Postgres.

Письма поставщикам о смене реквизитов наших компаний (просьба владельца
20.10.2026). В tests/test_payments_requisites_mail.py видно только, что запросы
написаны; что они ЗНАЧАТ — когда письмо ждёт, кто его получает, сколько раз,
что после отказа сервера, «Не отправлять» и «Отправить ещё раз», почему старое
письмо не приходит после нового, — исполняется здесь: настоящая схема раздела
(payments.schema), почтовый сервер подменён.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Запуск:

    PAYMENTS_TEST_PORT=55433 PGPASSWORD=... python -m pytest tests/test_payments_requisites_mail_postgres.py

Люди и компании выдуманы.
"""
import contextlib
import os
import uuid

import pytest

from payments import directory, mail, requisites_mail as rm, schema

PORT = os.environ.get('PAYMENTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PAYMENTS_TEST_PORT)')

ADMIN = {'id': 10, 'name': 'Админова Алия'}
# Почтовый сервер на этой же машине без шифрования — единственный случай, когда
# mail.config пускает без пароля. Сам сервер подменён (Postman).
ENV = {'PAYMENTS_SMTP_HOST': '127.0.0.1', 'PAYMENTS_SMTP_PORT': '1025', 'PAYMENTS_SMTP_SECURITY': 'none',
       'PAYMENTS_MAIL_FROM': 'buh@company.kz'}

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL DEFAULT 'operator',
                        status text NOT NULL DEFAULT 'working', department_id integer, supervisor_id integer,
                        telegram_id bigint, job_title text, avatar_bucket text, avatar_blob_path text);
    INSERT INTO users (id, name) VALUES (10, 'Админова Алия');
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
    rm._PAUSE['until'] = 0.0
    try:
        yield base
    finally:
        rm._PAUSE['until'] = 0.0
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


class Postman:
    """Почтовый сервер на месте mail.Transport. `plan` — что ответить на очередные
    письма (None — принять), `refuse_login` — не пустить в ящик, `close_after` —
    оборвать связь после стольких писем."""

    def __init__(self):
        self.letters = []
        self.plan = []
        self.refuse_login = None
        self.close_after = None
        self.closed = False
        self.deadlines = []

    def __call__(self, cfg):
        self.cfg = cfg
        self.closed = False
        return self

    def __enter__(self):
        if self.refuse_login:
            raise mail.MailError('transport', self.refuse_login)
        return self

    def __exit__(self, *_exc):
        return False

    def deadline(self, seconds):
        self.deadlines.append(seconds)

    def send(self, message):
        outcome = self.plan.pop(0) if self.plan else None
        if outcome:
            raise outcome
        sender = message['From'].addresses[0]
        self.letters.append({'to': message['To'], 'subject': message['Subject'],
                             'from': (sender.display_name, sender.addr_spec),
                             'text': message.get_body(('plain',)).get_content()})
        if self.close_after and len(self.letters) >= self.close_after:
            self.closed = True
        return '<%s@company.kz>' % len(self.letters)


ENTITY = {'name': 'ТОО «Наша компания»', 'bin': '123456789012', 'legal_address': 'Алматы, Абая 1',
          'iik': 'KZ11TEST0000000001', 'bank_name': 'АО «Альфа»', 'bik': 'ALFAKZKA', 'kbe': '17',
          'vat_payer': False, 'requisites': None}


def new_entity(cur, **fields):
    values = dict(ENTITY, **fields)
    cols = ', '.join(values)
    cur.execute('INSERT INTO payment_legal_entities (%s) VALUES (%s) RETURNING id'
                % (cols, ', '.join(['%s'] * len(values))), list(values.values()))
    return cur.fetchone()[0]


def supplier(cur, name, emails=(), active=True, contact=None):
    cur.execute("INSERT INTO payment_counterparties (name, emails, is_active, contact) VALUES (%s, %s, %s, %s) "
                "RETURNING id", (name, list(emails), active, contact))
    return cur.fetchone()[0]


def save(cur, entity_id, **changes):
    """Так пишет ручка справочника: «как было» под блокировкой, запись, письмо."""
    before = directory.read_legal_entity(cur, entity_id, lock=True)
    sets = ', '.join('%s = %%s' % col for col in changes)
    cur.execute('UPDATE payment_legal_entities SET %s WHERE id = %%s' % sets, list(changes.values()) + [entity_id])
    return rm.after_entity_saved(cur, entity_id, before, directory.read_legal_entity(cur, entity_id), ADMIN)


def make_due(cur, mailing_id=None):
    cur.execute("UPDATE payment_requisites_mailings SET send_after = send_after - interval '11 minutes'"
                + (' WHERE id = %s' if mailing_id else " WHERE status = 'pending'"), (mailing_id,) if mailing_id else None)


def recipients(cur, mailing_id):
    cur.execute("SELECT email, status, attempts, next_attempt_at IS NOT NULL, counterparty_names "
                "FROM payment_requisites_mail_recipients WHERE mailing_id = %s ORDER BY email", (mailing_id,))
    return [(r[0], r[1], r[2], r[3], list(r[4])) for r in cur.fetchall()]


def mailing_row(cur, mailing_id):
    cur.execute("SELECT status, last_error, finished_at IS NOT NULL FROM payment_requisites_mailings WHERE id = %s",
                (mailing_id,))
    return cur.fetchone()


def history(cur, entity_id):
    return rm.history(cur, entity_id)[0]


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
        assert first['status'] == 'pending' and first['ready'] is False
        cur.execute("SELECT send_after > %s + interval '9 minutes', before_data->>'iik', after_data->>'iik' "
                    "FROM payment_requisites_mailings WHERE id = %%s" % rm.NOW_SQL, (first['id'],))
        assert cur.fetchone() == (True, 'KZ11TEST0000000001', 'KZ22TEST0000000002')
        cur.execute("UPDATE payment_requisites_mailings SET send_after = send_after - interval '5 minutes'")
        cur.execute("SELECT send_after FROM payment_requisites_mailings")
        moved = cur.fetchone()[0]
        again = save(cur, entity, note='ещё примечание', is_active=False)
        cur.execute("SELECT send_after FROM payment_requisites_mailings")
        assert again['id'] == first['id'] and cur.fetchone()[0] == moved, \
            'правка не реквизитов не отодвигает письмо'
        merged = save(cur, entity, bank_name='АО «Бета»')
        cur.execute("SELECT COUNT(*), MIN(send_after) > %s, MIN(after_data->>'bank_name'), MIN(before_data->>'bank_name') "
                    "FROM payment_requisites_mailings", (moved,))
        assert merged['id'] == first['id'] and cur.fetchone() == (1, True, 'АО «Бета»', 'АО «Альфа»'), \
            'вторая правка попадает в то же письмо, отсчёт заново'
        assert rm.known_now(cur, entity, None)['iik'] == 'KZ11TEST0000000001', 'форма сверяет с «как было» письма'
        assert save(cur, entity, iik='KZ11TEST0000000001', bank_name='АО «Альфа»') == {'status': 'reverted'}
        cur.execute("SELECT COUNT(*) FROM payment_requisites_mailings")
        assert cur.fetchone()[0] == 0, 'вернули как было — письма нет'


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


def test_due_letter_goes_once_per_address(db):
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа-Сервис»', ['a@alfa.kz', 'shared@group.kz'])
        supplier(cur, 'ИП Бетов', ['shared@group.kz', 'b@betov.kz'])
        supplier(cur, 'ТОО «Скрытый»', ['hidden@x.kz'], active=False)
        supplier(cur, 'ТОО «Без почты»')
        pending = save(cur, entity, iik='KZ22TEST0000000002', vat_payer=True)
        assert pending['recipients'] == {'addresses': 4, 'suppliers': 3, 'without_email': 1,
                                         'without_names': ['ТОО «Без почты»']}
    assert rm.run(db, env=ENV, transport=postman) == {'ready': True, 'idle': True, 'finished': 0}, 'десять минут не прошли'
    assert postman.letters == []
    with db._get_cursor() as cur:
        make_due(cur)
    result = rm.run(db, env=ENV, transport=postman)
    assert (result['claimed'], result['sent'], result['failed'], result['finished']) == (1, 4, 0, 1)
    assert sorted(letter['to'] for letter in postman.letters) == ['a@alfa.kz', 'b@betov.kz', 'hidden@x.kz', 'shared@group.kz'], \
        'общий адрес двух поставщиков получает письмо один раз; скрытый — тоже поставщик («никого не пропустим»)'
    letter = postman.letters[0]
    assert letter['subject'] == 'Изменение реквизитов ТОО «Наша компания»'
    assert letter['from'] == ('ТОО «Наша компания»', 'buh@company.kz'), 'от имени компании, с ящика раздела'
    assert '— ИИК: было «KZ11TEST0000000001», стало «KZ22TEST0000000002»' in letter['text']
    assert '— НДС: было «без НДС», стало «плательщик НДС»' in letter['text']
    assert postman.deadlines and all(0 < seconds <= mail.TIMEOUT_SECONDS for seconds in postman.deadlines)
    with db._get_cursor() as cur:
        assert recipients(cur, pending['id']) == [
            ('a@alfa.kz', 'sent', 1, False, ['ТОО «Альфа-Сервис»']),
            ('b@betov.kz', 'sent', 1, False, ['ИП Бетов']),
            ('hidden@x.kz', 'sent', 1, False, ['ТОО «Скрытый»']),
            ('shared@group.kz', 'sent', 1, False, ['ИП Бетов', 'ТОО «Альфа-Сервис»']),
        ]
        assert mailing_row(cur, pending['id']) == ('done', None, True)
        latest, counts = rm.states(cur)
        assert (latest[entity]['id'], latest[entity]['status'], latest[entity]['sent'], latest[entity]['failed'],
                latest[entity]['total']) == (pending['id'], 'done', 4, 0, 4)
        assert counts == {entity: 1}
        item = history(cur, entity)[0]
        assert item['counts'] == {'total': 4, 'sent': 4, 'failed': 0, 'waiting': 0, 'removed': 0, 'superseded': 0}
        assert item['changed'] == ['ИИК', 'НДС'] and item['created_by_name'] == 'Админова Алия' and item['latest']
        # Окно «Текст письма» показывает то же, что ушло (почтовый формат дописывает перенос в конце).
        assert item['letter']['subject'] == letter['subject'] and item['letter']['text'] == letter['text'].rstrip('\n')
        assert rm.known_now(cur, entity, None)['iik'] == 'KZ22TEST0000000002', 'поставщики теперь знают новый ИИК'
    assert rm.run(db, env=ENV, transport=postman) == {'ready': True, 'idle': True, 'finished': 0}
    assert len(postman.letters) == 4, 'отправленное не уходит повторно'


def test_retries_then_gives_up(db):
    postman = Postman()
    temporary = mail.MailError('temporary', 'Связь с почтовым сервером оборвалась (TimeoutError)')
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        supplier(cur, 'ТОО «Опечатка»', ['nobody@alfa.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
    postman.plan = [temporary, mail.MailError('permanent', 'Сервер не принял адрес: 550 No such user')]
    result = rm.run(db, env=ENV, transport=postman)
    assert (result['sent'], result['failed'], result['finished']) == (0, 2, 0)
    with db._get_cursor() as cur:
        assert [(r[0], r[1], r[2], r[3]) for r in recipients(cur, mailing)] == [
            ('a@alfa.kz', 'failed', 1, True), ('nobody@alfa.kz', 'failed', 1, False)], \
            'временный отказ ждёт повтора, отказ в адресе — сразу «не ушло»'
        assert mailing_row(cur, mailing)[0] == 'sending'
        assert rm.states(cur)[0][entity]['failed'] == 1
    assert rm.run(db, env=ENV, transport=postman)['ready'] is True
    assert postman.letters == [], 'повтор — не раньше срока'
    for attempt in (2, 3):
        with db._get_cursor() as cur:
            cur.execute("UPDATE payment_requisites_mail_recipients SET next_attempt_at = next_attempt_at - interval '1 hour' "
                        "WHERE next_attempt_at IS NOT NULL")
        postman.plan = [temporary]
        rm.run(db, env=ENV, transport=postman)
        with db._get_cursor() as cur:
            cur.execute("SELECT attempts, next_attempt_at IS NOT NULL FROM payment_requisites_mail_recipients "
                        "WHERE email = 'a@alfa.kz'")
            assert cur.fetchone() == (attempt, attempt < rm.MAX_ATTEMPTS)
    with db._get_cursor() as cur:
        assert mailing_row(cur, mailing) == ('done', None, True)
        assert history(cur, entity)[0]['counts'] == {'total': 2, 'sent': 0, 'failed': 2, 'waiting': 0, 'removed': 0,
                                                     'superseded': 0}
    assert postman.letters == []


def test_login_refused_keeps_letter_pending(db):
    """Не вошли в ящик — письмо так и ждёт, адресаты не собраны: соберутся, когда ящик пустит."""
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        alfa = supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        supplier(cur, 'ТОО «Бета»', ['typo@beta.kzz'])
        mailing = save(cur, entity, bik='BETAKZKA')['id']
        make_due(cur)
    postman.refuse_login = 'Почта не приняла логин или пароль приложения (535 5.7.8)'
    clock = [1000.0]
    result = rm.run(db, env=ENV, transport=postman, clock=lambda: clock[0])
    assert result['error'] == postman.refuse_login
    with db._get_cursor() as cur:
        assert recipients(cur, mailing) == []
        assert mailing_row(cur, mailing) == ('pending', postman.refuse_login, False)
        assert rm.states(cur)[0][entity]['last_error'] == postman.refuse_login, 'в строке компании: «письмо не уходит»'
    assert rm.run(db, env=ENV, transport=postman, clock=lambda: clock[0] + 60) == {'ready': True, 'paused': True}, \
        'в ящик не стучимся каждую минуту'
    with db._get_cursor() as cur:
        cur.execute("UPDATE payment_counterparties SET emails = '{b@beta.kz}' WHERE name = 'ТОО «Бета»'")
        cur.execute("UPDATE payment_counterparties SET emails = '{a@alfa.kz, buh@alfa.kz}' WHERE id = %s", (alfa,))
    postman.refuse_login = None
    clock[0] += rm.TRANSPORT_PAUSE_SECONDS + 1
    assert rm.run(db, env=ENV, transport=postman, clock=lambda: clock[0])['sent'] == 3
    assert sorted(letter['to'] for letter in postman.letters) == ['a@alfa.kz', 'b@beta.kz', 'buh@alfa.kz'], \
        'адреса — на момент отправки: исправленный и добавленный получают, опечатка — нет'
    with db._get_cursor() as cur:
        assert mailing_row(cur, mailing) == ('done', None, True), 'дошло — ошибки ящика больше не видно'


def test_sender_refused_mid_run_gives_attempt_back(db):
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        supplier(cur, 'ТОО «Бета»', ['b@beta.kz'])
        mailing = save(cur, entity, bik='BETAKZKA')['id']
        make_due(cur)
    postman.plan = [mail.MailError('transport', 'Ящик не пускает отправителя buh@company.kz (553)')]
    result = rm.run(db, env=ENV, transport=postman)
    assert result['sent'] == 0 and 'не пускает отправителя' in result['error']
    with db._get_cursor() as cur:
        assert [(r[0], r[1], r[2]) for r in recipients(cur, mailing)] == [
            ('a@alfa.kz', 'queued', 0), ('b@beta.kz', 'queued', 0)]
        assert mailing_row(cur, mailing)[1] == 'Ящик не пускает отправителя buh@company.kz (553)'


def test_mailbox_limit_pauses_and_spares_the_rest(db):
    """«Daily user sending quota exceeded»: остальные адреса не сгорают, прогон на паузе."""
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        for index in range(4):
            supplier(cur, 'ТОО «Поставщик %s»' % index, ['buh%s@x.kz' % index])
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
    postman.plan = [None, mail.MailError('throttled', 'Сервер не принял адрес: 550 5.4.5 Daily user sending quota exceeded')]
    result = rm.run(db, env=ENV, transport=postman, clock=lambda: 5000.0)
    assert result['sent'] == 1 and 'quota' in result['error']
    assert rm._PAUSE['until'] == 5000.0 + rm.TRANSPORT_PAUSE_SECONDS
    with db._get_cursor() as cur:
        assert [(r[0], r[1], r[2], r[3]) for r in recipients(cur, mailing)] == [
            ('buh0@x.kz', 'sent', 1, False), ('buh1@x.kz', 'failed', 1, True),
            ('buh2@x.kz', 'queued', 0, False), ('buh3@x.kz', 'queued', 0, False)]
        assert 'quota' in mailing_row(cur, mailing)[1]


def test_three_refusals_in_a_row_look_like_the_mailbox(db):
    postman = Postman()
    permanent = mail.MailError('permanent', 'Сервер не принял письмо: 550 policy')
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        for index in range(5):
            supplier(cur, 'ТОО «Поставщик %s»' % index, ['buh%s@x.kz' % index])
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
    postman.plan = [permanent, permanent, permanent]
    result = rm.run(db, env=ENV, transport=postman)
    assert result['failed'] == 3 and result['error'] == rm.STREAK_ERROR
    with db._get_cursor() as cur:
        assert [(r[0], r[1], r[3]) for r in recipients(cur, mailing)] == [
            ('buh0@x.kz', 'failed', True), ('buh1@x.kz', 'failed', True), ('buh2@x.kz', 'failed', True),
            ('buh3@x.kz', 'queued', False), ('buh4@x.kz', 'queued', False)], 'трём — повтор, остальных не трогали'
        cur.execute("SELECT DISTINCT last_error FROM payment_requisites_mail_recipients WHERE status = 'failed'")
        assert cur.fetchall() == [('Сервер не принял письмо: 550 policy',)], 'ответ сервера сохранён'


def test_interrupted_send_is_retried(db):
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
    postman.plan = [RuntimeError('процесс упал посреди отправки')]
    with pytest.raises(RuntimeError):
        rm.run(db, env=ENV, transport=postman)
    with db._get_cursor() as cur:
        assert [(r[1], r[2]) for r in recipients(cur, mailing)] == [('sending', 1)]
    assert rm.run(db, env=ENV, transport=postman)['ready'] and postman.letters == [], 'свежая «отправляется» не трогается'
    with db._get_cursor() as cur:
        cur.execute("UPDATE payment_requisites_mail_recipients SET updated_at = updated_at - interval '20 minutes'")
    assert rm.run(db, env=ENV, transport=postman)['sent'] == 1
    with db._get_cursor() as cur:
        assert [(r[1], r[2]) for r in recipients(cur, mailing)] == [('sent', 2)]


def test_taken_row_is_not_sent_twice(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
        rm._claim_due(cur)
        cur.execute("SELECT id FROM payment_requisites_mail_recipients WHERE mailing_id = %s", (mailing,))
        recipient = cur.fetchone()[0]
        assert rm._take(cur, recipient) == 1
        assert rm._take(cur, recipient) is None, 'взятую другим процессом строку не берут'
        cur.execute("UPDATE payment_requisites_mail_recipients SET status = 'failed', next_attempt_at = NULL")
        assert rm._take(cur, recipient) is None, 'окончательный отказ сам не повторяется'
        cur.execute("UPDATE payment_requisites_mail_recipients SET status = 'sent'")
        assert rm._take(cur, recipient) is None


def test_finish_leaves_other_company_pending(db):
    postman = Postman()
    with db._get_cursor() as cur:
        first, second = new_entity(cur), new_entity(cur, name='ТОО «Вторая»')
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        sent = save(cur, first, kbe='19')['id']
        waiting = save(cur, second, kbe='19')['id']
        make_due(cur, sent)
    rm.run(db, env=ENV, transport=postman)
    with db._get_cursor() as cur:
        assert mailing_row(cur, sent)[0] == 'done'
        assert mailing_row(cur, waiting)[0] == 'pending', 'письмо другой компании ждёт своих десяти минут'


def test_budget_and_dropped_connection_stop_the_run(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        for index in range(4):
            supplier(cur, 'ТОО «Поставщик %s»' % index, ['buh%s@x.kz' % index])
        save(cur, entity, kbe='19')
        make_due(cur)
    # Часы читают: проверка паузы, начало прогона, затем перед каждым письмом; третье уже не влезает в бюджет.
    ticks = iter([0.0, 0.0, 1.0, 2.0, rm.RUN_BUDGET_SECONDS])
    postman = Postman()
    assert rm.run(db, env=ENV, transport=postman, clock=lambda: next(ticks, 999.0))['sent'] == 2
    postman.close_after = 1
    assert rm.run(db, env=ENV, transport=postman)['sent'] == 1, 'связь оборвалась — остальные ждут следующего прогона'
    assert rm.run(db, env=ENV, transport=Postman())['sent'] == 1


def test_cancel(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        assert rm.states(cur)[0][entity]['status'] == 'pending'
        rm.cancel(cur, entity, mailing, ADMIN)
        cur.execute("SELECT status, cancelled_by_name FROM payment_requisites_mailings WHERE id = %s", (mailing,))
        assert cur.fetchone() == ('cancelled', 'Админова Алия')
        assert entity not in rm.states(cur)[0], 'отменённое в строке компании не висит'
        assert rm.states(cur)[1] == {entity: 1}, 'а в счёт писем входит'
        with pytest.raises(rm.MailingError, match='уже отменено'):
            rm.cancel(cur, entity, mailing, ADMIN)
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
    assert rm.run(db, env=ENV, transport=Postman()) == {'ready': True, 'idle': True, 'finished': 0}


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
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        rm.cancel(cur, entity, save(cur, entity, iik='KZ22TEST0000000002')['id'], ADMIN)
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
    rm.run(db, env=ENV, transport=postman)
    text = postman.letters[0]['text']
    assert '— ИИК: было «KZ11TEST0000000001», стало «KZ22TEST0000000002»' in text, \
        'поставщик не знал нового ИИК — письмо говорит и о нём'
    assert '— КБЕ: было «17», стало «19»' in text
    with db._get_cursor() as cur:
        assert history(cur, entity)[0]['id'] == mailing and history(cur, entity)[0]['changed'] == ['ИИК', 'КБЕ']


def test_new_letter_supersedes_old_retries(db):
    """Старое письмо, ждущее повтора, не приходит поставщику после нового."""
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        first = save(cur, entity, bik='TYPOKZKA')['id']
        make_due(cur)
    postman.plan = [mail.MailError('temporary', 'Сервер не принял адрес: 451 4.7.1 greylisted')]
    rm.run(db, env=ENV, transport=postman)
    with db._get_cursor() as cur:
        assert [r[1] for r in recipients(cur, first)] == ['failed']
        second = save(cur, entity, bik='BETAKZKA')['id']
        make_due(cur, second)
        cur.execute("UPDATE payment_requisites_mail_recipients SET next_attempt_at = next_attempt_at - interval '1 hour'")
    rm.run(db, env=ENV, transport=postman)
    rm.run(db, env=ENV, transport=postman)
    assert [letter['text'].count('BETAKZKA') > 0 for letter in postman.letters] == [True], \
        'ушло только новое письмо; старое больше не повторяется'
    with db._get_cursor() as cur:
        assert [r[1] for r in recipients(cur, first)] == ['superseded']
        assert mailing_row(cur, first)[0] == 'done'
        old = [item for item in history(cur, entity) if item['id'] == first][0]
        assert old['counts']['superseded'] == 1 and not old['latest']


def test_retry_refuses_old_letter(db):
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        first = save(cur, entity, iik='KZ22TEST0000000002')['id']
        make_due(cur)
    postman.plan = [mail.MailError('permanent', 'Сервер не принял адрес: 552 Mailbox full')]
    rm.run(db, env=ENV, transport=postman)
    with db._get_cursor() as cur:
        second = save(cur, entity, iik='KZ33TEST0000000003')['id']
        make_due(cur, second)
    rm.run(db, env=ENV, transport=postman)
    with db._get_cursor() as cur:
        with pytest.raises(rm.MailingError, match='Есть письмо новее'):
            rm.retry(cur, entity, first, ADMIN)
        assert [(item['id'], item['latest']) for item in history(cur, entity)] == [(second, True), (first, False)]
    assert 'KZ33' in postman.letters[-1]['text']


def test_retry_follows_fixed_address(db):
    postman = Postman()
    permanent = mail.MailError('permanent', 'Сервер не принял адрес: 550 No such user')
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        typo = supplier(cur, 'ТОО «Опечатка»', ['buh@alfa.kzz'])
        supplier(cur, 'ТОО «Бета»', ['b@beta.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
    postman.plan = [permanent, permanent]
    rm.run(db, env=ENV, transport=postman)
    with db._get_cursor() as cur:
        assert mailing_row(cur, mailing)[0] == 'done'
        with pytest.raises(rm.MailingError, match='Письмо уже отправлено'):
            rm.cancel(cur, entity, mailing, ADMIN)
        cur.execute("UPDATE payment_counterparties SET emails = '{buh@alfa.kz}' WHERE id = %s", (typo,))
        supplier(cur, 'ТОО «Новый»', ['new@x.kz'])
        cur.execute("UPDATE payment_requisites_mailings SET updated_by_name = 'Автор Правки' WHERE id = %s", (mailing,))
        assert rm.retry(cur, entity, mailing, ADMIN) == {'queued': 2, 'removed': 1}
        cur.execute("SELECT email, status, attempts, last_error FROM payment_requisites_mail_recipients "
                    "WHERE mailing_id = %s ORDER BY email", (mailing,))
        assert cur.fetchall() == [('b@beta.kz', 'queued', 0, None), ('buh@alfa.kz', 'queued', 0, None),
                                  ('buh@alfa.kzz', 'removed', 1, 'Сервер не принял адрес: 550 No such user')], \
            'исправленный адрес получает письмо, опечатка «убран», новый поставщик повтором не цепляется'
        assert mailing_row(cur, mailing)[0] == 'sending'
        cur.execute("SELECT updated_by_name FROM payment_requisites_mailings WHERE id = %s", (mailing,))
        assert cur.fetchone()[0] == 'Автор Правки', 'повтор не переписывает автора правки'
    result = rm.run(db, env=ENV, transport=postman)
    assert (result['sent'], result['finished']) == (2, 1)
    with db._get_cursor() as cur:
        assert history(cur, entity)[0]['counts'] == {'total': 2, 'sent': 2, 'failed': 0, 'waiting': 0, 'removed': 1,
                                                     'superseded': 0}
        with pytest.raises(rm.MailingError, match='Недоставленных писем нет'):
            rm.retry(cur, entity, mailing, ADMIN)


def test_company_with_letters_is_not_deleted(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        save(cur, entity, kbe='19')
        assert directory.usage(cur, 'legal_entities', entity) == [('в письмах поставщикам', 1)]


def test_not_configured_waits(db):
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
    assert rm.run(db, env={}, transport=postman) == {'ready': False}
    with db._get_cursor() as cur:
        assert mailing_row(cur, mailing)[0] == 'pending', 'почты нет — письмо ждёт, адресатов не собирали'
        supplier(cur, 'ТОО «Поздний»', ['late@x.kz'])
    assert rm.run(db, env=ENV, transport=postman)['sent'] == 2, 'адреса — на момент отправки'


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


def test_journal_and_detail(db):
    """«Письма поставщикам»: все письма раздела — последние сверху, страницами; одно письмо — целиком."""
    postman = Postman()
    with db._get_cursor() as cur:
        first_entity = new_entity(cur)
        second_entity = new_entity(cur, name='ТОО «Вторая»')
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        supplier(cur, 'ТОО «Опечатка»', ['nobody@alfa.kz'])
        sent = save(cur, first_entity, iik='KZ22TEST0000000002')['id']
        make_due(cur)
    postman.plan = [None, mail.MailError('permanent', 'Сервер не принял адрес: 550 No such user')]
    rm.run(db, env=ENV, transport=postman)
    with db._get_cursor() as cur:
        waiting = save(cur, second_entity, kbe='19')['id']
        cancelled = save(cur, first_entity, bik='BETAKZKA')['id']
        rm.cancel(cur, first_entity, cancelled, ADMIN)
        items, total = rm.journal(cur, limit=2)
        assert total == 3 and [item['id'] for item in items] == [cancelled, waiting]
        assert [item['latest'] for item in items] == [False, True], 'отменённое — не последнее письмо компании'
        older, total = rm.journal(cur, limit=2, offset=2)
        assert total == 3 and [item['id'] for item in older] == [sent]
        item = older[0]
        assert (item['legal_entity_name'], item['subject']) == ('ТОО «Наша компания»', 'Изменение реквизитов ТОО «Наша компания»')
        assert item['counts'] == {'total': 2, 'sent': 1, 'failed': 1, 'waiting': 0, 'removed': 0, 'superseded': 0}
        assert item['latest'] is True and item['changed'] == ['ИИК'] and item['created_by_name'] == 'Админова Алия'
        assert rm.journal(cur, limit=2, offset=10) == ([], 0)
        full = rm.detail(cur, sent)
        assert [(r['email'], r['status']) for r in full['recipients']] == [('nobody@alfa.kz', 'failed'), ('a@alfa.kz', 'sent')], \
            'сначала то, что не ушло'
        assert full['recipients'][0]['last_error'] == 'Сервер не принял адрес: 550 No such user'
        assert 'KZ22TEST0000000002' in full['letter']['html'] and full['letter']['subject'] == item['subject']
        assert full['counts'] == item['counts'] and full['latest'] is True
        assert rm.detail(cur, waiting)['recipients'] == [], 'ждущее письмо: адреса соберутся в момент отправки'
        with pytest.raises(rm.MailingError, match='не найдено'):
            rm.detail(cur, 999999)


def test_known_is_the_last_letter_that_went(db):
    """Два письма ушли (ИИК A→B, затем B→C) — поставщики знают C, и следующее письмо «было» говорит C."""
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        save(cur, entity, iik='KZ22TEST0000000002')
        make_due(cur)
    rm.run(db, env=ENV, transport=postman)
    with db._get_cursor() as cur:
        save(cur, entity, iik='KZ33TEST0000000003')
        make_due(cur)
    rm.run(db, env=ENV, transport=postman)
    with db._get_cursor() as cur:
        assert rm.known(cur, entity)['iik'] == 'KZ33TEST0000000003'
        third = save(cur, entity, kbe='19')['id']
        cur.execute("SELECT before_data->>'iik' FROM payment_requisites_mailings WHERE id = %s", (third,))
        assert cur.fetchone()[0] == 'KZ33TEST0000000003'


def test_overlapping_runs_never_send_a_taken_row(db):
    """Второй процесс подключился, когда первый уже шлёт: строку, которую взял один, другой не шлёт."""
    import threading
    import time

    sent, guard, first_sent = [], threading.Lock(), threading.Event()

    class SlowPostman(Postman):
        def send(self, message):
            with guard:
                sent.append(message['To'])
            first_sent.set()
            time.sleep(0.03)
            return '<x@company.kz>'

    with db._get_cursor() as cur:
        entity = new_entity(cur)
        for index in range(15):
            supplier(cur, 'ТОО «Поставщик %02d»' % index, ['buh%02d@x.kz' % index])
        save(cur, entity, kbe='19')
        make_due(cur)
    first = threading.Thread(target=rm.run, args=(db,), kwargs={'env': ENV, 'transport': SlowPostman()})
    first.start()
    assert first_sent.wait(10)
    second = threading.Thread(target=rm.run, args=(db,), kwargs={'env': ENV, 'transport': SlowPostman()})
    second.start()
    first.join(30)
    second.join(30)
    rm.run(db, env=ENV, transport=SlowPostman())
    assert sorted(sent) == ['buh%02d@x.kz' % index for index in range(15)], 'ни дубля, ни пропуска'


def test_two_instances_send_each_address_once(db):
    """Выкладка на Render: старый и новый процесс какое-то время работают вместе, и
    планировщик каждого зовёт run(). Письмо на адрес всё равно уходит один раз."""
    import threading
    import time

    sent, guard = [], threading.Lock()

    class SlowPostman(Postman):
        def send(self, message):
            time.sleep(0.05)
            with guard:
                sent.append(message['To'])
            return '<x@company.kz>'

    with db._get_cursor() as cur:
        entity = new_entity(cur)
        for index in range(12):
            supplier(cur, 'ТОО «Поставщик %02d»' % index, ['buh%02d@x.kz' % index])
        save(cur, entity, kbe='19')
        make_due(cur)
    runs = [threading.Thread(target=rm.run, args=(db,), kwargs={'env': ENV, 'transport': SlowPostman()})
            for _ in range(2)]
    for thread in runs:
        thread.start()
    for thread in runs:
        thread.join(30)
    rm.run(db, env=ENV, transport=SlowPostman())
    assert sorted(sent) == ['buh%02d@x.kz' % index for index in range(12)], 'ни дубля, ни пропуска'
    with db._get_cursor() as cur:
        cur.execute("SELECT status, COUNT(*) FROM payment_requisites_mail_recipients GROUP BY 1")
        assert cur.fetchall() == [('sent', 12)]
        cur.execute("SELECT status FROM payment_requisites_mailings")
        assert cur.fetchall() == [('done',)]

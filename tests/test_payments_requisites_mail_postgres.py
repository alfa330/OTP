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

from payments import directory, mail, mailbox, requisites_mail as rm, schema

PORT = os.environ.get('PAYMENTS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PAYMENTS_TEST_PORT)')

ADMIN = {'id': 10, 'name': 'Админова Алия'}
# Почта компании — сервер на этой же машине без шифрования (так пускает только стенд);
# сам сервер подменён (Postman). Пароль шифруется ключом из JWT_SECRET.
os.environ.setdefault('JWT_SECRET', 'test-secret-for-mailbox')
MAILBOX = mailbox.Settings(address='buh@company.kz', provider=None, host='127.0.0.1', port=1025,
                           security='none', login='buh@company.kz', password='app-password')

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
    rm._PAUSE.clear()
    try:
        yield base
    finally:
        rm._PAUSE.clear()
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
        self.refuse_reason = 'login'
        self.close_after = None
        self.closed = False
        self.deadlines = []
        self.entered = 0

    def __call__(self, cfg):
        self.cfg = cfg
        self.closed = False
        return self

    def __enter__(self):
        self.entered += 1
        if self.refuse_login:
            raise mail.MailError('transport', self.refuse_login, reason=self.refuse_reason)
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


def new_entity(cur, mail_box=MAILBOX, **fields):
    """Наша компания; по умолчанию у неё подключена почта для писем (`mail_box=None` — нет)."""
    values = dict(ENTITY, **fields)
    cols = ', '.join(values)
    cur.execute('INSERT INTO payment_legal_entities (%s) VALUES (%s) RETURNING id'
                % (cols, ', '.join(['%s'] * len(values))), list(values.values()))
    entity_id = cur.fetchone()[0]
    if mail_box:
        mailbox.save(cur, entity_id, mail_box, ADMIN)
    return entity_id


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
        entity = new_entity(cur, mail_box=None)
        assert save(cur, entity, note='только примечание') is None, 'не реквизиты — письма нет'
        first = save(cur, entity, iik='KZ22TEST0000000002')
        assert first['status'] == 'pending' and first['ready'] is False, 'у компании нет своей почты'
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
        assert pending['ready'] is True, 'у компании своя почта'
        assert pending['recipients'] == {'addresses': 4, 'suppliers': 3, 'without_email': 1,
                                         'without_names': ['ТОО «Без почты»'], 'excluded': 0}
    assert rm.run(db, transport=postman) == {'idle': True, 'finished': 0}, 'десять минут не прошли'
    assert postman.letters == []
    with db._get_cursor() as cur:
        make_due(cur)
    result = rm.run(db, transport=postman)
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
    assert rm.run(db, transport=postman) == {'idle': True, 'finished': 0}
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
    result = rm.run(db, transport=postman)
    assert (result['sent'], result['failed'], result['finished']) == (0, 2, 0)
    with db._get_cursor() as cur:
        assert [(r[0], r[1], r[2], r[3]) for r in recipients(cur, mailing)] == [
            ('a@alfa.kz', 'failed', 1, True), ('nobody@alfa.kz', 'failed', 1, False)], \
            'временный отказ ждёт повтора, отказ в адресе — сразу «не ушло»'
        assert mailing_row(cur, mailing)[0] == 'sending'
        assert rm.states(cur)[0][entity]['failed'] == 1
    rm.run(db, transport=postman)
    assert postman.letters == [], 'повтор — не раньше срока'
    for attempt in (2, 3):
        with db._get_cursor() as cur:
            cur.execute("UPDATE payment_requisites_mail_recipients SET next_attempt_at = next_attempt_at - interval '1 hour' "
                        "WHERE next_attempt_at IS NOT NULL")
        postman.plan = [temporary]
        rm.run(db, transport=postman)
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
    """Не вошли в ящик — письмо так и ждёт, адресаты не собраны: соберутся, когда ящик пустит.
    Отказ в пароле ящик помечает: до нового пароля в него не стучимся (иначе подбор)."""
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        alfa = supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        supplier(cur, 'ТОО «Бета»', ['typo@beta.kzz'])
        mailing = save(cur, entity, bik='BETAKZKA')['id']
        make_due(cur)
    postman.refuse_login = 'Почта не приняла логин или пароль приложения (535 5.7.8)'
    clock = [1000.0]
    result = rm.run(db, transport=postman, clock=lambda: clock[0])
    assert result['error'] == postman.refuse_login
    with db._get_cursor() as cur:
        assert recipients(cur, mailing) == []
        assert mailing_row(cur, mailing) == ('pending', postman.refuse_login, False)
        assert rm.states(cur)[0][entity]['last_error'] == postman.refuse_login, 'в строке компании: «письмо не уходит»'
        assert mailbox.read(cur, entity)['problem'] == postman.refuse_login, 'окно компании просит пароль заново'
    clock[0] += rm.TRANSPORT_PAUSE_SECONDS + 1
    again = rm.run(db, transport=postman, clock=lambda: clock[0])
    assert again['without_mailbox'] == 1 and postman.entered == 1, 'и через десять минут в ящик не стучимся'
    with db._get_cursor() as cur:
        beta = directory.find_counterparty_by_name(cur, 'ТОО «Бета»')['id']
        directory.replace_counterparty_emails(cur, beta, ['b@beta.kz'])
        directory.replace_counterparty_emails(cur, alfa, ['a@alfa.kz', 'buh@alfa.kz'])
        mailbox.save(cur, entity, MAILBOX, ADMIN)      # ввели пароль заново — беда снята
    postman.refuse_login = None
    assert rm.run(db, transport=postman, clock=lambda: clock[0])['sent'] == 3
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
    postman.plan = [mail.MailError('transport', 'Ящик не пускает отправителя buh@company.kz (553)', reason='sender')]
    result = rm.run(db, transport=postman)
    assert result['sent'] == 0 and 'не пускает отправителя' in result['error']
    with db._get_cursor() as cur:
        assert [(r[0], r[1], r[2]) for r in recipients(cur, mailing)] == [
            ('a@alfa.kz', 'queued', 0), ('b@beta.kz', 'queued', 0)]
        assert mailing_row(cur, mailing)[1] == 'Ящик не пускает отправителя buh@company.kz (553)'
        assert 'не пускает отправителя' in mailbox.read(cur, entity)['problem'], 'отправителя правит человек'


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
    result = rm.run(db, transport=postman, clock=lambda: 5000.0)
    assert result['sent'] == 1 and 'quota' in result['error']
    assert rm._PAUSE[mailbox.pause_key(MAILBOX)] == 5000.0 + rm.TRANSPORT_PAUSE_SECONDS
    with db._get_cursor() as cur:
        assert mailbox.read(cur, entity)['problem'] is None, 'лимит — пауза, а не беда ящика'
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
    result = rm.run(db, transport=postman)
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
        rm.run(db, transport=postman)
    with db._get_cursor() as cur:
        assert [(r[1], r[2]) for r in recipients(cur, mailing)] == [('sending', 1)]
    rm.run(db, transport=postman)
    assert postman.letters == [], 'свежая «отправляется» не трогается'
    with db._get_cursor() as cur:
        cur.execute("UPDATE payment_requisites_mail_recipients SET updated_at = updated_at - interval '20 minutes'")
    assert rm.run(db, transport=postman)['sent'] == 1
    with db._get_cursor() as cur:
        assert [(r[1], r[2]) for r in recipients(cur, mailing)] == [('sent', 2)]


def test_taken_row_is_not_sent_twice(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
        rm._claim_due(cur, entity)
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
    rm.run(db, transport=postman)
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
    # Часы читают: начало прогона, пауза ящика, бюджет перед компанией, затем перед каждым письмом;
    # третье письмо уже не влезает в бюджет.
    ticks = iter([0.0, 0.0, 0.0, 1.0, 2.0, rm.RUN_BUDGET_SECONDS])
    postman = Postman()
    assert rm.run(db, transport=postman, clock=lambda: next(ticks, 999.0))['sent'] == 2
    postman.close_after = 1
    assert rm.run(db, transport=postman)['sent'] == 1, 'связь оборвалась — остальные ждут следующего прогона'
    assert rm.run(db, transport=Postman())['sent'] == 1


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
    assert rm.run(db, transport=Postman()) == {'idle': True, 'finished': 0}


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
    rm.run(db, transport=postman)
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
    rm.run(db, transport=postman)
    with db._get_cursor() as cur:
        assert [r[1] for r in recipients(cur, first)] == ['failed']
        second = save(cur, entity, bik='BETAKZKA')['id']
        make_due(cur, second)
        cur.execute("UPDATE payment_requisites_mail_recipients SET next_attempt_at = next_attempt_at - interval '1 hour'")
    rm.run(db, transport=postman)
    rm.run(db, transport=postman)
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
    rm.run(db, transport=postman)
    with db._get_cursor() as cur:
        second = save(cur, entity, iik='KZ33TEST0000000003')['id']
        make_due(cur, second)
    rm.run(db, transport=postman)
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
    rm.run(db, transport=postman)
    with db._get_cursor() as cur:
        assert mailing_row(cur, mailing)[0] == 'done'
        with pytest.raises(rm.MailingError, match='Письмо уже отправлено'):
            rm.cancel(cur, entity, mailing, ADMIN)
        directory.replace_counterparty_emails(cur, typo, ['buh@alfa.kz'])
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
    result = rm.run(db, transport=postman)
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


def test_company_without_mailbox_waits(db):
    postman = Postman()
    with db._get_cursor() as cur:
        entity = new_entity(cur, mail_box=None)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
    result = rm.run(db, transport=postman)
    assert (result['without_mailbox'], result['sent'], postman.entered) == (1, 0, 0)
    with db._get_cursor() as cur:
        assert mailing_row(cur, mailing)[0] == 'pending', 'почты нет — письмо ждёт, адресатов не собирали'
        supplier(cur, 'ТОО «Поздний»', ['late@x.kz'])
        mailbox.save(cur, entity, MAILBOX, ADMIN)
    assert rm.run(db, transport=postman)['sent'] == 2, 'почту подключили — уходит; адреса — на момент отправки'


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
    rm.run(db, transport=postman)
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
    rm.run(db, transport=postman)
    with db._get_cursor() as cur:
        save(cur, entity, iik='KZ33TEST0000000003')
        make_due(cur)
    rm.run(db, transport=postman)
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
    first = threading.Thread(target=rm.run, args=(db,), kwargs={'transport': SlowPostman()})
    first.start()
    assert first_sent.wait(10)
    second = threading.Thread(target=rm.run, args=(db,), kwargs={'transport': SlowPostman()})
    second.start()
    first.join(30)
    second.join(30)
    rm.run(db, transport=SlowPostman())
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
    runs = [threading.Thread(target=rm.run, args=(db,), kwargs={'transport': SlowPostman()})
            for _ in range(2)]
    for thread in runs:
        thread.start()
    for thread in runs:
        thread.join(30)
    rm.run(db, transport=SlowPostman())
    assert sorted(sent) == ['buh%02d@x.kz' % index for index in range(12)], 'ни дубля, ни пропуска'
    with db._get_cursor() as cur:
        cur.execute("SELECT status, COUNT(*) FROM payment_requisites_mail_recipients GROUP BY 1")
        assert cur.fetchall() == [('sent', 12)]
        cur.execute("SELECT status FROM payment_requisites_mailings")
        assert cur.fetchall() == [('done',)]


# ── 21.10.2026: почта у каждой компании, галочки «Кому», «Почта поставщиков» ──

ALFA_BOX = MAILBOX._replace(address='buh@alfa-co.kz', login='buh@alfa-co.kz')
BETA_BOX = MAILBOX._replace(address='buh@beta-co.kz', login='buh@beta-co.kz', host='localhost')


class PostOffice(Postman):
    """Почтовый сервер на каждый ящик: письма помнят, с какого ящика ушли."""

    def __init__(self):
        super().__init__()
        self.refuse = {}           # адрес ящика → ошибка, которую он отдаёт на любое письмо
        self.refuse_to = {}        # адрес получателя → ошибка на письмо ему

    def __call__(self, cfg):
        self.cfg = cfg
        self.closed = False
        return self

    def send(self, message):
        failure = self.refuse.get(self.cfg.sender) or self.refuse_to.get(message['To'])
        if failure:
            raise failure
        message_id = super().send(message)
        self.letters[-1]['box'] = self.cfg.sender
        return message_id


def test_each_company_sends_from_its_own_mailbox(db):
    """Решение 21.10.2026: у каждой компании своя почта; без почты — письма ждут."""
    office = PostOffice()
    with db._get_cursor() as cur:
        alfa = new_entity(cur, name='ТОО «Альфа-Ко»', mail_box=ALFA_BOX)
        beta = new_entity(cur, name='ТОО «Бета-Ко»', mail_box=BETA_BOX)
        gamma = new_entity(cur, name='ТОО «Гамма-Ко»', mail_box=None)
        supplier(cur, 'ТОО «Поставщик»', ['buh@supplier.kz'])
        for entity in (alfa, beta, gamma):
            save(cur, entity, kbe='19')
        make_due(cur)
    result = rm.run(db, transport=office)
    assert (result['sent'], result['without_mailbox']) == (2, 1)
    assert sorted((letter['box'], letter['from'][0]) for letter in office.letters) == [
        ('buh@alfa-co.kz', 'ТОО «Альфа-Ко»'), ('buh@beta-co.kz', 'ТОО «Бета-Ко»')], \
        'письмо компании — с её ящика и от её имени'
    assert all(letter['from'][1] == letter['box'] for letter in office.letters)
    with db._get_cursor() as cur:
        assert [(item['legal_entity_name'], item['status'], item['mail_ready'])
                for item in rm.journal(cur)[0]] == [('ТОО «Гамма-Ко»', 'pending', False),
                                                    ('ТОО «Бета-Ко»', 'done', True), ('ТОО «Альфа-Ко»', 'done', True)]
        assert [(row['name'], row['connected']) for row in rm.waiting_for_mailbox(cur)] == [('ТОО «Гамма-Ко»', False)]
        assert mailbox.read(cur, alfa)['checked_at'] is not None, 'ушедшее письмо подтверждает, что ящик работает'
        assert rm.summary(cur, rm.history(cur, gamma)[0][0]['id'])['ready'] is False


def test_mailbox_pause_does_not_stop_other_companies(db):
    office = PostOffice()
    with db._get_cursor() as cur:
        alfa = new_entity(cur, name='ТОО «Альфа-Ко»', mail_box=ALFA_BOX)
        beta = new_entity(cur, name='ТОО «Бета-Ко»', mail_box=BETA_BOX)
        supplier(cur, 'ТОО «Поставщик»', ['buh@supplier.kz'])
        first_alfa = save(cur, alfa, kbe='19')['id']
        save(cur, beta, kbe='19')
        make_due(cur)
    office.refuse['buh@alfa-co.kz'] = mail.MailError('throttled', 'Сервер не принял письмо: 550 5.4.5 Daily sending quota')
    result = rm.run(db, transport=office, clock=lambda: 100.0)
    assert result['sent'] == 1 and [letter['box'] for letter in office.letters] == ['buh@beta-co.kz']
    assert rm._PAUSE == {mailbox.pause_key(ALFA_BOX): 100.0 + rm.TRANSPORT_PAUSE_SECONDS}, 'пауза — только у этого ящика'
    with db._get_cursor() as cur:
        save(cur, beta, kbe='20')
        make_due(cur)
        # Повтор «Альфы» уже пора отправлять — но её ящик на паузе.
        cur.execute("UPDATE payment_requisites_mail_recipients SET next_attempt_at = next_attempt_at - interval '1 hour' "
                    "WHERE next_attempt_at IS NOT NULL")
    office.refuse.clear()
    again = rm.run(db, transport=office, clock=lambda: 200.0)
    assert (again['paused'], again['sent']) == (1, 1), 'ящик на паузе ждёт, другая компания отправляет'
    later = rm.run(db, transport=office, clock=lambda: 100.0 + rm.TRANSPORT_PAUSE_SECONDS + 1)
    assert later['sent'] == 1
    with db._get_cursor() as cur:
        assert mailing_row(cur, first_alfa)[0] == 'done'


def test_unreadable_password_waits_for_a_new_one(db, monkeypatch):
    office = PostOffice()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Поставщик»', ['buh@supplier.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        make_due(cur)
    monkeypatch.setenv('JWT_SECRET', 'другой секрет — пароль не читается')
    monkeypatch.delenv('PAYMENTS_CARD_KEY', raising=False)
    result = rm.run(db, transport=office)
    assert (result['without_mailbox'], office.entered) == (1, 0)
    with db._get_cursor() as cur:
        assert mailbox.read(cur, entity)['problem'] == mailbox.UNREADABLE
        assert mailing_row(cur, mailing)[0] == 'pending'
        mailbox.save(cur, entity, MAILBOX, ADMIN)     # ввели пароль заново — шифруется нынешним ключом
    assert rm.run(db, transport=office)['sent'] == 1


def test_recipients_are_chosen_with_checkmarks(db):
    """Решение 21.10.2026: в окне ждущего письма — галочки, по умолчанию отмечены все."""
    office = PostOffice()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        x = supplier(cur, 'ТОО «Икс»', ['x@x.kz'])
        y = supplier(cur, 'ТОО «Игрек»', ['y@y.kz', 'shared@group.kz'])
        z = supplier(cur, 'ТОО «Зет»', ['shared@group.kz'])
        supplier(cur, 'ТОО «Без почты»')
        mailing = save(cur, entity, kbe='19')['id']
        item = history(cur, entity)[0]
        assert [(c['name'], c['send']) for c in item['candidates']] == [
            ('ТОО «Зет»', True), ('ТОО «Игрек»', True), ('ТОО «Икс»', True)], 'отмечены все поставщики с почтой'
        assert rm.set_recipients(cur, entity, mailing, ADMIN, [x, y, 999999]) == {'excluded': 2}, \
            'несуществующий поставщик не записывается'
        item = history(cur, entity)[0]
        assert [(c['name'], c['send']) for c in item['candidates']] == [
            ('ТОО «Зет»', True), ('ТОО «Игрек»', False), ('ТОО «Икс»', False)]
        assert item['excluded'] == sorted([x, y])
        assert rm.summary(cur, mailing)['recipients'] == {
            'addresses': 1, 'suppliers': 1, 'without_email': 1, 'without_names': ['ТОО «Без почты»'], 'excluded': 2}
        late = supplier(cur, 'ТОО «Поздний»', ['late@x.kz'])
        assert next(c for c in history(cur, entity)[0]['candidates'] if c['id'] == late)['send'], \
            'кому почту дописали, пока письмо ждало, — отмечен'
        make_due(cur)
    office.refuse_to['late@x.kz'] = mail.MailError('permanent', 'Сервер не принял адрес: 550 mailbox full')
    rm.run(db, transport=office)
    with db._get_cursor() as cur:
        assert [(r[0], r[1], r[4]) for r in recipients(cur, mailing)] == [
            ('late@x.kz', 'failed', ['ТОО «Поздний»']), ('shared@group.kz', 'sent', ['ТОО «Зет»'])], \
            'общий адрес уходит ради неснятого поставщика; снятым — ничего'
        done = history(cur, entity)[0]
        assert done['excluded_names'] == ['ТОО «Игрек»', 'ТОО «Икс»'] and 'candidates' not in done
        with pytest.raises(rm.MailingError, match='Письмо уже отправлено'):
            rm.set_recipients(cur, entity, mailing, ADMIN, [])
        directory.replace_counterparty_emails(cur, late, ['late@late.kz'])
        directory.replace_counterparty_emails(cur, y, ['y@y.kz', 'y2@y.kz'])
        assert rm.retry(cur, entity, mailing, ADMIN) == {'queued': 1, 'removed': 1}
        cur.execute("SELECT email FROM payment_requisites_mail_recipients WHERE mailing_id = %s AND status = 'queued'",
                    (mailing,))
        assert cur.fetchall() == [('late@late.kz',)], 'повтор не цепляет снятого «Игрека»'


def test_checkmarks_change_only_while_letter_waits(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        x = supplier(cur, 'ТОО «Икс»', ['x@x.kz'])
        mailing = save(cur, entity, kbe='19')['id']
        cur.execute("SELECT send_after, updated_by_name FROM payment_requisites_mailings WHERE id = %s", (mailing,))
        before = cur.fetchone()
        rm.set_recipients(cur, entity, mailing, {'id': 11, 'name': 'Другой Админ'}, [x])
        cur.execute("SELECT send_after, updated_by_name FROM payment_requisites_mailings WHERE id = %s", (mailing,))
        assert cur.fetchone() == before, 'галочки не двигают срок и не меняют автора правки'
        rm.set_recipients(cur, entity, mailing, ADMIN, [])
        assert all(c['send'] for c in history(cur, entity)[0]['candidates'])
        with pytest.raises(rm.MailingError, match='не найдено'):
            rm.set_recipients(cur, entity + 1000, mailing, ADMIN, [])
        cur.execute("UPDATE payment_requisites_mailings SET status = 'sending' WHERE id = %s", (mailing,))
        with pytest.raises(rm.MailingError, match='уже уходит'):
            rm.set_recipients(cur, entity, mailing, ADMIN, [x])


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
    office = PostOffice()
    with db._get_cursor() as cur:
        entity = new_entity(cur)
        supplier(cur, 'ТОО «Альфа»', ['a@alfa.kz', 'typo@alfa.kzz'])
        save(cur, entity, kbe='19')
        make_due(cur)
        assert all(item['last_letter'] is None for item in directory.list_counterparty_emails(cur))
    office.refuse_to['typo@alfa.kzz'] = mail.MailError('permanent', 'Сервер не принял адрес: 550 No such user')
    rm.run(db, transport=office)
    with db._get_cursor() as cur:
        state = {item['email']: item['last_letter'] for item in directory.list_counterparty_emails(cur)}
        assert state['a@alfa.kz']['status'] == 'sent' and state['a@alfa.kz']['sent_at'] is not None
        assert state['typo@alfa.kzz']['status'] == 'failed' and '550' in state['typo@alfa.kzz']['last_error']


def test_mailbox_row_keeps_password_encrypted(db):
    with db._get_cursor() as cur:
        entity = new_entity(cur, mail_box=None)
        assert mailbox.view(mailbox.read(cur, entity)) is None and not mailbox.ready(cur, entity)
        mailbox.save(cur, entity, MAILBOX, ADMIN)
        row = mailbox.read(cur, entity)
        assert MAILBOX.password not in row['password_enc'] and mailbox.decrypt(row['password_enc']) == MAILBOX.password
        shown = mailbox.view(row)
        assert 'password_enc' not in shown and 'login' not in shown and MAILBOX.password not in str(shown)
        assert (shown['address'], shown['manual'], shown['problem']) == ('buh@company.kz', True, None)
        assert mailbox.ready(cur, entity) and mailbox.works(shown)
        mailbox.note_problem(cur, entity, 'Почта не приняла логин или пароль приложения (535)')
        assert not mailbox.ready(cur, entity) and not mailbox.works(mailbox.views(cur)[entity])
        assert mailbox.outboxes(cur, [entity]) == {}, 'ящик с бедой планировщик не трогает'
        mailbox.note_ok(cur, entity)
        assert mailbox.outboxes(cur, [entity])[entity] == MAILBOX
        assert mailbox.remove(cur, entity) == 1 and mailbox.read(cur, entity) is None
        cur.execute("INSERT INTO payment_entity_mailboxes (legal_entity_id, address, host, port, security, login, "
                    "password_enc) VALUES (%s, 'a@x.kz', 'h', 465, 'ssl', 'a@x.kz', 'x')", (entity,))
        cur.execute("DELETE FROM payment_legal_entities WHERE id = %s", (entity,))
        cur.execute("SELECT COUNT(*) FROM payment_entity_mailboxes")
        assert cur.fetchone()[0] == 0, 'компанию удалили — её почта уходит с ней'

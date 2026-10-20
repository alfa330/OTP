"""Письма поставщикам о смене реквизитов наших компаний.

Просьба владельца 20.10.2026: «добавление эмайла контрагентов поставщиков и тд,
автомат рассылка по эмайлом при изменении реквизитов». Решения владельца в тот же
день (ответы на вопросы с вариантами):
  * меняются реквизиты НАШЕЙ компании (справочник «Реквизиты компаний») — письмо
    уходит поставщикам. Правка реквизитов самого поставщика писем не вызывает;
  * получают все поставщики, у которых заполнена почта, — и скрытые тоже: «никого
    не пропустим» (у скрытого бывают незакрытые счета и акты на нашу компанию);
  * письмо уходит через SEND_DELAY_MINUTES после сохранения. Правка в эти минуты
    не рождает второе письмо — попадает в то же (уходят последние реквизиты), и
    отсчёт начинается заново; «Не отправлять» отменяет письмо; реквизиты вернули
    к тем, что знают поставщики, — письма нет вовсе;
  * отправляет корпоративный ящик по SMTP (mail.py).

Как устроено.
  * Рассылка — строка payment_requisites_mailings: «как было» и «как стало» —
    снимком тех полей, что идут в «Скопировать реквизиты» (REQUISITE_FIELDS).
    «Как было» — то, что поставщики ЗНАЮТ (known): реквизиты из последнего
    ушедшего письма, а если писем не уходило — из самого первого письма (всё
    остальное отменили) или из строки компании до правки. Поэтому опечатка,
    сохранённая и отменённая «Не отправлять», а потом исправленная, письма не
    рождает, а отменённая настоящая смена войдёт в «что изменилось» следующего
    письма — поставщик увидит всё, чего не знал. Ожидающее письмо у компании одно —
    уникальный частичный индекс.
  * Получатель — адрес, а не поставщик: письмо одинаковое для всех, и адрес,
    записанный у двух поставщиков, получает его один раз. Адреса собираются, когда
    письмо забирают в отправку, — уже после входа в ящик: почта, добавленная
    поставщику за десять минут или за дни, пока ящик не пускал, письмо получит.
  * Новое письмо компании гасит недошедшие адреса её прежних писем (superseded):
    иначе повтор старого письма мог прийти поставщику ПОСЛЕ нового, и последними у
    него оказались бы старые реквизиты. По той же причине «Отправить ещё раз» есть
    только у последнего письма компании.
  * Отправку ведёт планировщик раз в минуту (bot_schedule2:
    payments_requisites_mail_job) в общем пуле потоков бота (4 потока). Худший
    случай удержания потока — вход (до двух тайм-аутов mail.TIMEOUT_SECONDS) плюс
    RUN_BUDGET_SECONDS: перед каждым письмом тайм-аут сокета урезается до остатка
    бюджета. Не больше RUN_MAX_LETTERS писем за прогон — ящик не ловит всплеск.
    «sending» пишется и фиксируется ДО обращения к серверу: упади процесс посреди
    отправки, сторож через STUCK_MINUTES признает попытку неудачной и повторит.
  * Повторы: временный отказ (сеть, 4xx) — ещё две попытки, через 5 и 30 минут;
    сервер не принял адрес (5xx) — сразу «не ушло». Ящик упёрся в лимит или
    фильтр — прогон встаёт на паузу TRANSPORT_PAUSE_SECONDS, текущему адресу
    засчитывается попытка. Не войти в ящик — виновата настройка: попытки не
    тратятся, прогоны ждут ту же паузу, текст ошибки виден в карточке компании.
    Три отказа в адресе подряд в одном прогоне — тоже похоже на ящик: эти три
    уходят на повтор, прогон встаёт на паузу.
"""

import html as _html
import logging
import time

from psycopg2.extras import Json

from . import directory, mail
from .sqlutil import NOW_SQL, row_map

# Поля реквизитов — в том же порядке, что в «Скопировать реквизиты»
# (directory.requisites_text). Форма компании (ТОО/ИП) и примечание в письмо не
# идут и писем не вызывают.
REQUISITE_FIELDS = ('name', 'bin', 'legal_address', 'iik', 'bank_name', 'bik', 'kbe', 'vat_payer', 'requisites')
FIELD_LABELS = {
    'name': 'Наименование', 'bin': 'БИН', 'legal_address': 'Юридический адрес', 'iik': 'ИИК',
    'bank_name': 'Банк', 'bik': 'БИК', 'kbe': 'КБЕ', 'vat_payer': 'НДС', 'requisites': 'Прочие реквизиты',
}
_MULTILINE = ('legal_address', 'requisites')

SEND_DELAY_MINUTES = 10
MAX_ATTEMPTS = 3
RETRY_MINUTES = (5, 30)          # пауза после первой и второй временной неудачи
STUCK_MINUTES = 15
TRANSPORT_PAUSE_SECONDS = 600
RUN_BUDGET_SECONDS = 40
RUN_MAX_LETTERS = 20
LETTER_MIN_SECONDS = 5           # меньше остатка бюджета — письмо ждёт следующего прогона
REFUSALS_IN_A_ROW = 3
CLAIM_LIMIT = 5
HISTORY_LIMIT = 10
WITHOUT_EMAIL_NAMES = 50

STUCK_ERROR = 'Отправка прервалась — неизвестно, ушло ли письмо'
STREAK_ERROR = 'Почтовый сервер отклонил несколько адресов подряд — похоже, ящик упёрся в лимит или фильтр'
CONTACT_EMAILS_MARK = 'contact_emails_adopted'

# До какого момента не стучаться в почту после отказа ящика (на процесс).
_PAUSE = {'until': 0.0}


# ─── Снимок реквизитов и что изменилось ──────────────────────────────────────

def _norm(field, value):
    """Значение поля так, как его пишет ручка справочника: ИИК — верхним регистром
    без пробелов, БИН — цифрами. Иначе строка, записанная когда-то иначе, «менялась»
    бы при любой записи компании и уводила письмо после правки одного примечания.
    То же правило — у формы (paymentsMeta.requisitesChanged)."""
    if field == 'vat_payer':
        return bool(value)
    text = str(value or '').replace('\r', '')
    if field == 'iik':
        return ''.join(text.split()).upper()
    if field == 'bin':
        return ''.join(ch for ch in text if ch.isdigit())
    if field in _MULTILINE:
        return '\n'.join(line.strip() for line in text.split('\n')).strip()
    return text.strip()


def snapshot(entity):
    entity = entity or {}
    return {field: _norm(field, entity.get(field)) for field in REQUISITE_FIELDS}


def changed_fields(before, after):
    before, after = before or {}, after or {}
    return [field for field in REQUISITE_FIELDS if _norm(field, before.get(field)) != _norm(field, after.get(field))]


# ─── Письмо ──────────────────────────────────────────────────────────────────

def _shown(field, value):
    if field == 'vat_payer':
        return 'плательщик НДС' if value else 'без НДС'
    return _norm(field, value) or 'не указано'


def _flat(field, value):
    return '; '.join(part for part in _shown(field, value).split('\n') if part)


def letter(before, after):
    """(тема, текст, HTML) — одно письмо на всех получателей рассылки."""
    before, after = snapshot(before), snapshot(after)
    # Название идёт в тему и в имя отправителя: перенос строки в заголовке письма недопустим.
    name = ' '.join(after['name'].split()) or 'нашей компании'
    who = name + (' (БИН %s)' % after['bin'] if after['bin'] else '')
    changed = changed_fields(before, after)
    subject = 'Изменение реквизитов %s' % name
    requisites = directory.requisites_text(after)
    ask = 'Просим указывать новые реквизиты в документах для нашей компании: счетах, актах и счетах-фактурах.'

    lines = ['Здравствуйте!', '', 'Сообщаем об изменении реквизитов %s.' % who, '']
    if changed:
        lines.append('Что изменилось:')
        lines += ['— %s: было «%s», стало «%s»' % (FIELD_LABELS[field], _flat(field, before[field]),
                                                  _flat(field, after[field])) for field in changed]
        lines.append('')
    lines += ['Новые реквизиты целиком:', requisites, '', ask, '', 'С уважением,', name]
    text = '\n'.join(lines)

    esc = lambda value: _html.escape(str(value)).replace('\n', '<br>')  # noqa: E731
    # Длинный ИИК и название банка переносятся внутри своей ячейки: на телефоне таблица
    # иначе распирала письмо шире экрана (колонки — долями, ширина таблицы — по письму).
    cell = ('padding:8px 10px;border-top:1px solid #e2e8f0;vertical-align:top;'
            'overflow-wrap:anywhere;word-wrap:break-word;word-break:break-word;')
    head = 'padding:0 10px 6px;font-size:12px;color:#64748b;font-weight:600;'
    rows = ''.join(
        '<tr><td style="%scolor:#64748b">%s</td>'
        '<td style="%scolor:#64748b">%s</td><td style="%scolor:#0f172a;font-weight:600">%s</td></tr>'
        % (cell, esc(FIELD_LABELS[field]), cell, esc(_shown(field, before[field])), cell, esc(_shown(field, after[field])))
        for field in changed)
    header = ''.join('<th align="left" width="%s" style="%s">%s</th>' % (width, head, label)
                     for width, label in (('28%', 'Реквизит'), ('36%', 'Было'), ('36%', 'Стало')))
    table = ('<table role="presentation" cellpadding="0" cellspacing="0" '
             'style="width:100%;border-collapse:collapse;table-layout:fixed;font-size:14px;margin:4px 0 20px">'
             '<tr>' + header + '</tr>' + rows + '</table>') if changed else ''
    body = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>%(subject)s</title>'
        # Телефон: поля письма уже. Кто не понимает <style> (Outlook), видит письмо как на компьютере.
        '<style>@media only screen and (max-width:480px){.letter-page{padding:12px 6px!important}'
        '.letter-card{padding:20px 16px!important}}</style></head>'
        '<body class="letter-page" style="margin:0;padding:24px 12px;background:#f5f5f7;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
        'color:#0f172a;font-size:15px;line-height:1.5">'
        '<div class="letter-card" style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:16px;padding:28px 24px">'
        '<p style="margin:0 0 12px">Здравствуйте!</p>'
        '<p style="margin:0 0 16px">Сообщаем об изменении реквизитов <b>%(who)s</b>.</p>'
        '%(table)s'
        '<p style="margin:0 0 6px;font-weight:600">Новые реквизиты целиком</p>'
        '<div style="margin:0 0 20px;padding:12px 14px;background:#f8fafc;border-radius:12px;font-size:14px">'
        '%(requisites)s</div>'
        '<p style="margin:0 0 16px">%(ask)s</p>'
        '<p style="margin:0">С уважением,<br>%(name)s</p>'
        '</div></body></html>'
    ) % {'subject': esc(subject), 'who': esc(who), 'table': table, 'requisites': esc(requisites),
         'ask': esc(ask), 'name': esc(name)}
    return subject, text, body


# ─── Что знают поставщики ────────────────────────────────────────────────────

def known(cursor, entity_id):
    """Реквизиты компании, которые знают поставщики, — снимок либо None (писем не было).

    Последнее ушедшее письмо (отправляется или отправлено). Не уходило ни одно —
    «как было» самого первого письма: все письма после него отменены, а до него
    поставщики знали то, что было в строке компании."""
    cursor.execute(
        "SELECT after_data FROM payment_requisites_mailings WHERE legal_entity_id = %s "
        "AND status IN ('sending', 'done') ORDER BY id DESC LIMIT 1", (int(entity_id),))
    row = cursor.fetchone()
    if row:
        return row[0] or {}
    cursor.execute("SELECT before_data FROM payment_requisites_mailings WHERE legal_entity_id = %s "
                   "ORDER BY id LIMIT 1", (int(entity_id),))
    row = cursor.fetchone()
    return (row[0] or {}) if row else None


def known_now(cursor, entity_id, entity_row):
    """То, с чем форма сверяет правку: ожидающее письмо несёт своё «как было»."""
    cursor.execute("SELECT before_data FROM payment_requisites_mailings WHERE legal_entity_id = %s "
                   "AND status = 'pending'", (int(entity_id),))
    row = cursor.fetchone()
    if row:
        return snapshot(row[0] or {})
    base = known(cursor, entity_id)
    return snapshot(base if base is not None else entity_row)


# ─── Сохранили компанию: завести, дополнить или снять письмо ─────────────────

def after_entity_saved(cursor, entity_id, before_row, after_row, actor):
    """Вызывается в транзакции записи компании, ПОСЛЕ блокировки её строки
    (directory.read_legal_entity(..., lock=True)): две одновременные правки не
    заведут два письма. Возвращает сводку письма для ответа ручки, None — письма
    нет, {'status': 'reverted'} — реквизиты вернули к тем, что знают поставщики,
    и письма не будет."""
    after = snapshot(after_row)
    cursor.execute(
        "SELECT id, before_data, after_data FROM payment_requisites_mailings "
        "WHERE legal_entity_id = %s AND status = 'pending' FOR UPDATE", (int(entity_id),))
    row = cursor.fetchone()
    actor_id, actor_name = (actor or {}).get('id'), (actor or {}).get('name')
    if row:
        mailing_id, base, last = row[0], row[1] or {}, row[2] or {}
        if not changed_fields(base, after):
            cursor.execute("DELETE FROM payment_requisites_mailings WHERE id = %s AND status = 'pending'", (mailing_id,))
            return {'status': 'reverted'}
        if changed_fields(last, after):
            cursor.execute(
                "UPDATE payment_requisites_mailings SET after_data = %%s, "
                "send_after = %s + %%s * interval '1 minute', updated_at = %s, updated_by = %%s, "
                "updated_by_name = %%s WHERE id = %%s" % (NOW_SQL, NOW_SQL),
                (Json(after), SEND_DELAY_MINUTES, actor_id, actor_name, mailing_id))
        # Иначе правили не реквизиты (примечание, «скрыта»): письмо и срок прежние.
        return summary(cursor, mailing_id)
    if not changed_fields(snapshot(before_row), after):
        return None          # эта запись реквизитов не трогала
    base = known(cursor, entity_id)
    base = snapshot(base if base is not None else before_row)
    if not changed_fields(base, after):
        return {'status': 'reverted'}
    cursor.execute(
        "INSERT INTO payment_requisites_mailings (legal_entity_id, before_data, after_data, send_after, "
        "created_by, created_by_name, updated_by, updated_by_name) "
        "VALUES (%%s, %%s, %%s, %s + %%s * interval '1 minute', %%s, %%s, %%s, %%s) RETURNING id" % NOW_SQL,
        (int(entity_id), Json(base), Json(after), SEND_DELAY_MINUTES, actor_id, actor_name, actor_id, actor_name))
    return summary(cursor, cursor.fetchone()[0])


def summary(cursor, mailing_id):
    """Коротко о письме — для тоста после сохранения."""
    cursor.execute("SELECT id, status, send_after FROM payment_requisites_mailings WHERE id = %s", (int(mailing_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': row[0], 'status': row[1], 'send_after': row[2], 'recipients': recipients_overview(cursor),
            'ready': mail.ready()}


def recipients_overview(cursor):
    """Сколько адресов получат письмо, если отправить сейчас, и у кого почты нет."""
    cursor.execute(
        "SELECT (SELECT COUNT(DISTINCT e.email) FROM payment_counterparties cp, unnest(cp.emails) AS e(email)), "
        "       (SELECT COUNT(*) FROM payment_counterparties WHERE cardinality(emails) > 0), "
        "       (SELECT COUNT(*) FROM payment_counterparties WHERE cardinality(emails) = 0), "
        "       ARRAY(SELECT name FROM payment_counterparties WHERE cardinality(emails) = 0 "
        "             ORDER BY lower(name) LIMIT %s)", (WITHOUT_EMAIL_NAMES,))
    addresses, suppliers, without, names = cursor.fetchone()
    return {'addresses': int(addresses or 0), 'suppliers': int(suppliers or 0),
            'without_email': int(without or 0), 'without_names': list(names or [])}


# ─── Почта из поля «Контакт» ─────────────────────────────────────────────────

def adopt_contact_emails(cursor):
    """Один раз: адреса, которые раньше писали в «Контакт» (подсказка поля была
    «Имя, телефон, почта»), переходят в «Почту» — иначе такие поставщики писем не
    получат. Поставщик, у которого почта уже есть, не трогается. Повторно не
    срабатывает (отметка в payment_settings): почту, которую потом стёрли руками,
    следующий запуск из «Контакта» не вернёт. Возвращает, у скольких перенесли."""
    cursor.execute("INSERT INTO payment_settings (key, value) VALUES (%s, 'true') "
                   "ON CONFLICT (key) DO NOTHING RETURNING key", (CONTACT_EMAILS_MARK,))
    if not cursor.fetchone():
        return 0
    cursor.execute("SELECT id, contact FROM payment_counterparties "
                   "WHERE cardinality(emails) = 0 AND contact LIKE '%@%'")
    moved = 0
    for counterparty_id, contact in cursor.fetchall():
        emails = mail.find_emails(contact)
        if emails:
            cursor.execute("UPDATE payment_counterparties SET emails = %s WHERE id = %s AND cardinality(emails) = 0",
                           (emails, counterparty_id))
            moved += cursor.rowcount
    if moved:
        logging.info('Оплата счетов: почта из «Контакта» перенесена в «Почту», поставщиков: %s', moved)
    return moved


# ─── Действия администратора ─────────────────────────────────────────────────

class MailingError(Exception):
    def __init__(self, message, status=409, code='PAYMENT_MAILING_STATE'):
        super().__init__(message)
        self.status = status
        self.code = code


def _status(cursor, entity_id, mailing_id, lock=False):
    cursor.execute("SELECT status FROM payment_requisites_mailings WHERE id = %%s AND legal_entity_id = %%s%s"
                   % (' FOR UPDATE' if lock else ''), (int(mailing_id), int(entity_id)))
    row = cursor.fetchone()
    if not row:
        raise MailingError('Письмо не найдено', status=404, code='PAYMENT_MAILING_NOT_FOUND')
    return row[0]


_STATE_WORDS = {'sending': 'Письмо уже отправляется', 'done': 'Письмо уже отправлено',
                'cancelled': 'Письмо уже отменено'}


def cancel(cursor, entity_id, mailing_id, actor):
    """«Не отправлять» — пока письмо ждёт своих десяти минут."""
    status = _status(cursor, entity_id, mailing_id, lock=True)
    if status != 'pending':
        raise MailingError(_STATE_WORDS.get(status, 'Письмо уже не ждёт отправки'))
    cursor.execute(
        "UPDATE payment_requisites_mailings SET status = 'cancelled', cancelled_at = %s, updated_at = %s, "
        "cancelled_by = %%s, cancelled_by_name = %%s WHERE id = %%s" % (NOW_SQL, NOW_SQL),
        ((actor or {}).get('id'), (actor or {}).get('name'), int(mailing_id)))


def _current_addresses(cursor):
    """Адрес → (номера поставщиков, названия) по всем поставщикам с почтой."""
    cursor.execute(
        "SELECT e.email, array_agg(cp.id ORDER BY lower(cp.name), cp.id), array_agg(cp.name ORDER BY lower(cp.name), cp.id) "
        "  FROM payment_counterparties cp, unnest(cp.emails) AS e(email) GROUP BY e.email")
    return {row[0]: (list(row[1]), list(row[2])) for row in cursor.fetchall()}


def retry(cursor, entity_id, mailing_id, actor):
    """«Отправить ещё раз» — тем, до кого последнее письмо компании не дошло.

    Адрес, который так и стоит у поставщика, пробуется заново с полным запасом
    попыток. Адрес, убранный из карточки (исправили опечатку), помечается
    «убран», а письмо идёт на нынешние адреса того же поставщика. Письмо не
    последнее — отказ: у поставщика оказались бы старые реквизиты поверх новых.
    Возвращает {'queued': сколько адресов пойдёт, 'removed': сколько убрано}."""
    status = _status(cursor, entity_id, mailing_id, lock=True)
    if status != 'done':
        raise MailingError('Письмо ещё не отправлено' if status == 'pending' else _STATE_WORDS[status])
    cursor.execute("SELECT 1 FROM payment_requisites_mailings WHERE legal_entity_id = %s AND id > %s "
                   "AND status <> 'cancelled' LIMIT 1", (int(entity_id), int(mailing_id)))
    if cursor.fetchone():
        raise MailingError('Есть письмо новее — оно уже несёт новые реквизиты')
    cursor.execute(
        "SELECT id, email, counterparty_ids FROM payment_requisites_mail_recipients "
        "WHERE mailing_id = %s AND status = 'failed' FOR UPDATE", (int(mailing_id),))
    failed = cursor.fetchall()
    if not failed:
        raise MailingError('Недоставленных писем нет')
    current = _current_addresses(cursor)
    queued, removed, orphaned = 0, 0, set()
    for recipient_id, email, counterparty_ids in failed:
        if email in current:
            ids, names = current[email]
            cursor.execute(
                "UPDATE payment_requisites_mail_recipients SET status = 'queued', attempts = 0, next_attempt_at = NULL, "
                "last_error = NULL, counterparty_ids = %%s, counterparty_names = %%s, updated_at = %s WHERE id = %%s"
                % NOW_SQL, (ids, names, recipient_id))
            queued += 1
        else:
            cursor.execute("UPDATE payment_requisites_mail_recipients SET status = 'removed', updated_at = %s "
                           "WHERE id = %%s" % NOW_SQL, (recipient_id,))
            removed += 1
            orphaned.update(counterparty_ids or [])
    if orphaned:
        cursor.execute("SELECT email FROM payment_requisites_mail_recipients WHERE mailing_id = %s", (int(mailing_id),))
        present = {row[0] for row in cursor.fetchall()}
        for email, (ids, names) in sorted(current.items()):
            if email in present or not orphaned.intersection(ids):
                continue
            cursor.execute(
                "INSERT INTO payment_requisites_mail_recipients (mailing_id, email, counterparty_ids, counterparty_names) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (mailing_id, email) DO NOTHING",
                (int(mailing_id), email, ids, names))
            queued += cursor.rowcount
    if queued:
        # Автора правки реквизитов не трогаем: карточка подписывает письмо им, а не тем, кто нажал повтор.
        cursor.execute(
            "UPDATE payment_requisites_mailings SET status = 'sending', finished_at = NULL, last_error = NULL, "
            "updated_at = %s WHERE id = %%s" % NOW_SQL, (int(mailing_id),))
    return {'queued': queued, 'removed': removed}


# ─── Что показать в справочнике ──────────────────────────────────────────────

_MAILING_FIELDS = ('id', 'legal_entity_id', 'status', 'before_data', 'after_data', 'send_after', 'created_at',
                   'created_by_name', 'updated_at', 'updated_by_name', 'started_at', 'finished_at', 'cancelled_at',
                   'cancelled_by_name', 'last_error')
_RECIPIENT_FIELDS = ('mailing_id', 'email', 'counterparty_names', 'status', 'attempts', 'next_attempt_at',
                     'last_error', 'sent_at')
_ORDER = {'failed': 0, 'removed': 1, 'sending': 2, 'queued': 3, 'superseded': 4, 'sent': 5}


def _counts(recipients):
    """total — адреса, по которым письмо ушло или ещё уйдёт либо не ушло по вине
    адреса; removed (адрес убрали из карточки) и superseded (их взяло новое письмо)
    считаются отдельно."""
    counts = {'total': 0, 'sent': 0, 'failed': 0, 'waiting': 0, 'removed': 0, 'superseded': 0}
    for item in recipients:
        status = item['status']
        if status in ('removed', 'superseded'):
            counts[status] += 1
            continue
        counts['total'] += 1
        if status == 'sent':
            counts['sent'] += 1
        elif status == 'failed' and not item.get('next_attempt_at'):
            counts['failed'] += 1
        else:
            counts['waiting'] += 1
    return counts


def history(cursor, entity_id, limit=HISTORY_LIMIT):
    """Письма компании — последние сверху, с адресатами и самим письмом. Вторым
    значением — сколько писем у компании всего."""
    cursor.execute("SELECT %s, COUNT(*) OVER () FROM payment_requisites_mailings WHERE legal_entity_id = %%s "
                   "ORDER BY id DESC LIMIT %%s" % ', '.join(_MAILING_FIELDS), (int(entity_id), int(limit)))
    rows = cursor.fetchall()
    total = int(rows[0][-1]) if rows else 0
    items = [row_map(_MAILING_FIELDS, row[:-1]) for row in rows]
    by_id = {item['id']: item for item in items}
    for item in items:
        item['recipients'] = []
    if by_id:
        cursor.execute("SELECT %s FROM payment_requisites_mail_recipients WHERE mailing_id = ANY(%%s) ORDER BY email"
                       % ', '.join(_RECIPIENT_FIELDS), (list(by_id),))
        for row in cursor.fetchall():
            recipient = row_map(_RECIPIENT_FIELDS, row)
            recipient['counterparty_names'] = list(recipient['counterparty_names'] or [])
            by_id[recipient.pop('mailing_id')]['recipients'].append(recipient)
    latest = next((item['id'] for item in items if item['status'] != 'cancelled'), None)
    for item in items:
        before, after = item.pop('before_data') or {}, item.pop('after_data') or {}
        item['recipients'].sort(key=lambda r: (_ORDER.get(r['status'], 9), r['email']))
        item['counts'] = _counts(item['recipients'])
        item['changed'] = [FIELD_LABELS[field] for field in changed_fields(before, after)]
        # «Отправить ещё раз» — только у последнего неотменённого письма компании.
        item['latest'] = item['id'] == latest
        subject, text, body = letter(before, after)
        item['letter'] = {'subject': subject, 'text': text, 'html': body}
    return items, total


def states(cursor):
    """Компания → что с её последним письмом (кроме отменённых) и сколько писем у неё
    всего — для строки списка и заготовки блока писем в окне."""
    cursor.execute(
        "SELECT DISTINCT ON (m.legal_entity_id) m.legal_entity_id, m.id, m.status, m.send_after, m.last_error, "
        "       COUNT(r.id) FILTER (WHERE r.status = 'sent'), "
        "       COUNT(r.id) FILTER (WHERE r.status = 'failed' AND r.next_attempt_at IS NULL), "
        "       COUNT(r.id) FILTER (WHERE r.status NOT IN ('removed', 'superseded')) "
        "  FROM payment_requisites_mailings m "
        "  LEFT JOIN payment_requisites_mail_recipients r ON r.mailing_id = m.id "
        " WHERE m.status <> 'cancelled' "
        " GROUP BY m.id ORDER BY m.legal_entity_id, m.id DESC")
    result = {row[0]: {'id': row[1], 'status': row[2], 'send_after': row[3], 'last_error': row[4],
                       'sent': int(row[5] or 0), 'failed': int(row[6] or 0), 'total': int(row[7] or 0)}
              for row in cursor.fetchall()}
    cursor.execute("SELECT legal_entity_id, COUNT(*) FROM payment_requisites_mailings GROUP BY 1")
    counts = {row[0]: int(row[1]) for row in cursor.fetchall()}
    return result, counts


_JOURNAL_SQL = (
    "SELECT %s, le.name, "
    "       COUNT(r.id) FILTER (WHERE r.status = 'sent'), "
    "       COUNT(r.id) FILTER (WHERE r.status = 'failed' AND r.next_attempt_at IS NULL), "
    "       COUNT(r.id) FILTER (WHERE r.status IN ('queued', 'sending') "
    "                           OR (r.status = 'failed' AND r.next_attempt_at IS NOT NULL)), "
    "       COUNT(r.id) FILTER (WHERE r.status = 'removed'), "
    "       COUNT(r.id) FILTER (WHERE r.status = 'superseded'), "
    "       m.id = (SELECT max(x.id) FROM payment_requisites_mailings x "
    "                WHERE x.legal_entity_id = m.legal_entity_id AND x.status <> 'cancelled'), "
    "       COUNT(*) OVER () "
    "  FROM payment_requisites_mailings m "
    "  JOIN payment_legal_entities le ON le.id = m.legal_entity_id "
    "  LEFT JOIN payment_requisites_mail_recipients r ON r.mailing_id = m.id "
    "%%s GROUP BY m.id, le.name ORDER BY m.id DESC" % ', '.join('m.' + field for field in _MAILING_FIELDS))


def _journal_row(row):
    width = len(_MAILING_FIELDS)
    item = row_map(_MAILING_FIELDS, row[:width])
    name, sent, failed, waiting, removed, superseded, latest, total = row[width:width + 8]
    before, after = item.pop('before_data') or {}, item.pop('after_data') or {}
    item['legal_entity_name'] = name
    item['counts'] = {'total': int(sent or 0) + int(failed or 0) + int(waiting or 0), 'sent': int(sent or 0),
                      'failed': int(failed or 0), 'waiting': int(waiting or 0), 'removed': int(removed or 0),
                      'superseded': int(superseded or 0)}
    item['latest'] = bool(latest)
    item['changed'] = [FIELD_LABELS[field] for field in changed_fields(before, after)]
    return item, (before, after), int(total or 0)


def journal(cursor, limit=20, offset=0):
    """Письма поставщикам по всем компаниям — последние сверху (экран «Письма
    поставщикам»). Счётчики — запросом, без списка адресатов. (письма, всего)"""
    cursor.execute(_JOURNAL_SQL % '' + ' LIMIT %s OFFSET %s', (int(limit), int(offset)))
    items, total = [], 0
    for row in cursor.fetchall():
        item, (before, after), total = _journal_row(row)
        item['subject'] = letter(before, after)[0]
        items.append(item)
    return items, total


def detail(cursor, mailing_id):
    """Одно письмо целиком: адресаты, счётчики и само письмо (текст и HTML) — для окна письма."""
    cursor.execute(_JOURNAL_SQL % 'WHERE m.id = %s', (int(mailing_id),))
    row = cursor.fetchone()
    if not row:
        raise MailingError('Письмо не найдено', status=404, code='PAYMENT_MAILING_NOT_FOUND')
    item, (before, after), _total = _journal_row(row)
    cursor.execute("SELECT %s FROM payment_requisites_mail_recipients WHERE mailing_id = %%s ORDER BY email"
                   % ', '.join(_RECIPIENT_FIELDS), (int(mailing_id),))
    recipients = []
    for found in cursor.fetchall():
        recipient = row_map(_RECIPIENT_FIELDS, found)
        recipient.pop('mailing_id')
        recipient['counterparty_names'] = list(recipient['counterparty_names'] or [])
        recipients.append(recipient)
    recipients.sort(key=lambda r: (_ORDER.get(r['status'], 9), r['email']))
    item['recipients'] = recipients
    subject, text, body = letter(before, after)
    item['letter'] = {'subject': subject, 'text': text, 'html': body}
    return item


# ─── Отправка (планировщик) ──────────────────────────────────────────────────

_DUE_RECIPIENT = ("(r.status = 'queued' OR (r.status = 'failed' AND r.next_attempt_at IS NOT NULL "
                  "AND r.next_attempt_at <= %s))" % NOW_SQL)


def _due_mailings(cursor):
    """Письма, у которых сейчас есть работа: пришёл срок или адрес ждёт отправки."""
    cursor.execute(
        "SELECT id FROM payment_requisites_mailings WHERE status = 'pending' AND send_after <= %s "
        "UNION SELECT DISTINCT r.mailing_id FROM payment_requisites_mail_recipients r "
        "  JOIN payment_requisites_mailings m ON m.id = r.mailing_id "
        " WHERE m.status = 'sending' AND %s" % (NOW_SQL, _DUE_RECIPIENT))
    return [row[0] for row in cursor.fetchall()]


def _claim_due(cursor):
    """Пришёл срок — письмо переходит в отправку, адресаты собираются сейчас.
    Недошедшие адреса прежних писем той же компании гасятся: их возьмёт новое."""
    cursor.execute(
        "UPDATE payment_requisites_mailings SET status = 'sending', started_at = %s, updated_at = %s "
        " WHERE id IN (SELECT id FROM payment_requisites_mailings "
        "               WHERE status = 'pending' AND send_after <= %s "
        "               ORDER BY send_after LIMIT %%s FOR UPDATE SKIP LOCKED) "
        "RETURNING id, legal_entity_id" % (NOW_SQL, NOW_SQL, NOW_SQL), (CLAIM_LIMIT,))
    claimed = cursor.fetchall()
    for mailing_id, entity_id in claimed:
        cursor.execute(
            "INSERT INTO payment_requisites_mail_recipients (mailing_id, email, counterparty_ids, counterparty_names) "
            "SELECT %s, e.email, array_agg(cp.id ORDER BY lower(cp.name), cp.id), "
            "       array_agg(cp.name ORDER BY lower(cp.name), cp.id) "
            "  FROM payment_counterparties cp, unnest(cp.emails) AS e(email) GROUP BY e.email "
            "ON CONFLICT (mailing_id, email) DO NOTHING", (mailing_id,))
        cursor.execute(
            "UPDATE payment_requisites_mail_recipients r SET status = 'superseded', next_attempt_at = NULL, "
            "       updated_at = %s "
            "  FROM payment_requisites_mailings m "
            " WHERE r.mailing_id = m.id AND m.legal_entity_id = %%s AND m.id < %%s "
            "   AND r.status IN ('queued', 'failed')" % NOW_SQL, (entity_id, mailing_id))
    return [mailing_id for mailing_id, _entity in claimed]


def _reap_stuck(cursor):
    cursor.execute(
        "UPDATE payment_requisites_mail_recipients SET status = 'failed', last_error = %%s, updated_at = %s, "
        "       next_attempt_at = CASE WHEN attempts < %%s THEN %s ELSE NULL END "
        " WHERE status = 'sending' AND updated_at < %s - %%s * interval '1 minute'" % (NOW_SQL, NOW_SQL, NOW_SQL),
        (STUCK_ERROR, MAX_ATTEMPTS, STUCK_MINUTES))


def _due(cursor, limit):
    cursor.execute(
        "SELECT r.id, r.mailing_id, r.email FROM payment_requisites_mail_recipients r "
        "  JOIN payment_requisites_mailings m ON m.id = r.mailing_id "
        " WHERE m.status = 'sending' AND %s ORDER BY r.mailing_id, r.id LIMIT %%s" % _DUE_RECIPIENT, (int(limit),))
    return [{'id': row[0], 'mailing_id': row[1], 'email': row[2]} for row in cursor.fetchall()]


def _letters(cursor, mailing_ids):
    cursor.execute("SELECT id, before_data, after_data FROM payment_requisites_mailings WHERE id = ANY(%s)",
                   (list(mailing_ids),))
    result = {}
    for mailing_id, before, after in cursor.fetchall():
        subject, text, body = letter(before or {}, after or {})
        result[mailing_id] = {'subject': subject, 'text': text, 'html': body,
                              'name': ' '.join(str((after or {}).get('name') or '').split())}
    return result


def _take(cursor, recipient_id):
    """Строка «отправляется» и попытка — до обращения к серверу. None — её уже взяли."""
    cursor.execute(
        "UPDATE payment_requisites_mail_recipients SET status = 'sending', attempts = attempts + 1, "
        "       next_attempt_at = NULL, updated_at = %s WHERE id = %%s AND %s RETURNING attempts"
        % (NOW_SQL, _DUE_RECIPIENT.replace('r.', '')), (recipient_id,))
    row = cursor.fetchone()
    return row[0] if row else None


def _give_back(cursor, recipient_id):
    """Не вошли в ящик — адрес не виноват: попытку вернуть, письмо снова ждёт."""
    cursor.execute(
        "UPDATE payment_requisites_mail_recipients SET status = 'queued', attempts = GREATEST(attempts - 1, 0), "
        "       updated_at = %s WHERE id = %%s AND status = 'sending'" % NOW_SQL, (recipient_id,))


def _sent(cursor, recipient_id, mailing_id, message_id):
    cursor.execute(
        "UPDATE payment_requisites_mail_recipients SET status = 'sent', sent_at = %s, message_id = %%s, "
        "       last_error = NULL, updated_at = %s WHERE id = %%s AND status = 'sending'" % (NOW_SQL, NOW_SQL),
        (str(message_id or '')[:255] or None, recipient_id))
    cursor.execute("UPDATE payment_requisites_mailings SET last_error = NULL WHERE id = %s AND last_error IS NOT NULL",
                   (mailing_id,))


def _failed(cursor, recipient_id, attempt, error):
    """Отказ по адресу. Повтор — только у временного отказа и у лимита ящика."""
    retry = error.kind in ('temporary', 'throttled')
    retry_in = RETRY_MINUTES[min(attempt, len(RETRY_MINUTES)) - 1] if retry and attempt < MAX_ATTEMPTS else None
    cursor.execute(
        "UPDATE payment_requisites_mail_recipients SET status = 'failed', last_error = %%s, updated_at = %s, "
        "       next_attempt_at = CASE WHEN %%s::int IS NULL THEN NULL ELSE %s + %%s::int * interval '1 minute' END "
        " WHERE id = %%s AND status = 'sending'" % (NOW_SQL, NOW_SQL),
        (str(error)[:1000], retry_in, retry_in, recipient_id))


def _retry_later(cursor, recipient_id, attempt):
    """Отказ в адресе, который на деле, похоже, про ящик: адрес ждёт повтора
    (ответ сервера в last_error остаётся), если попытки ещё есть."""
    retry_in = RETRY_MINUTES[min(attempt, len(RETRY_MINUTES)) - 1] if attempt < MAX_ATTEMPTS else None
    if retry_in is None:
        return
    cursor.execute(
        "UPDATE payment_requisites_mail_recipients SET next_attempt_at = %s + %%s * interval '1 minute', "
        "       updated_at = %s WHERE id = %%s AND status = 'failed'" % (NOW_SQL, NOW_SQL), (retry_in, recipient_id))


def _finish(cursor):
    """Письмо, у которого никто не ждёт отправки или повтора, — отправлено."""
    cursor.execute(
        "UPDATE payment_requisites_mailings m SET status = 'done', finished_at = %s, updated_at = %s "
        " WHERE m.status = 'sending' AND NOT EXISTS ("
        "       SELECT 1 FROM payment_requisites_mail_recipients r WHERE r.mailing_id = m.id "
        "          AND (r.status IN ('queued', 'sending') OR (r.status = 'failed' AND r.next_attempt_at IS NOT NULL)))"
        % (NOW_SQL, NOW_SQL))
    return cursor.rowcount


def _note_error(db, mailing_ids, error):
    if not mailing_ids:
        return
    with db._get_cursor() as cursor:
        cursor.execute("UPDATE payment_requisites_mailings SET last_error = %s WHERE id = ANY(%s) "
                       "AND status IN ('pending', 'sending')", (str(error)[:1000], list(mailing_ids)))


def run(db, *, env=None, transport=None, clock=time.monotonic):
    """Один прогон планировщика: забрать письма, которым пора, и отправить, что успеем.

    Почта не подключена — ничего не трогаем: письма ждут, а когда ящик подключат,
    уйдут с адресами, какие будут у поставщиков на тот момент. Сначала вход в ящик,
    потом сбор адресов: пока ящик не пускает, ожидающее письмо остаётся ожидающим."""
    cfg = mail.config(env)
    if cfg is None:
        return {'ready': False}
    if clock() < _PAUSE['until']:
        return {'ready': True, 'paused': True}
    with db._get_cursor() as cursor:
        _reap_stuck(cursor)
        finished = _finish(cursor)
        waiting = _due_mailings(cursor)
    if not waiting:
        return {'ready': True, 'idle': True, 'finished': finished}
    result = {'ready': True, 'claimed': 0, 'sent': 0, 'failed': 0, 'finished': finished}
    started = clock()
    letters = {}
    try:
        with (transport or mail.Transport)(cfg) as smtp:
            with db._get_cursor() as cursor:
                result['claimed'] = len(_claim_due(cursor))
                due = _due(cursor, RUN_MAX_LETTERS)
                letters = _letters(cursor, {item['mailing_id'] for item in due}) if due else {}
            streak = []
            for item in due:
                left = RUN_BUDGET_SECONDS - (clock() - started)
                if left < LETTER_MIN_SECONDS or getattr(smtp, 'closed', False):
                    break   # остальным — следующий прогон
                if hasattr(smtp, 'deadline'):
                    smtp.deadline(min(mail.TIMEOUT_SECONDS, left))
                piece = letters[item['mailing_id']]
                # Письмо собирается ДО строки «отправляется»: сбой сборки не должен
                # оставлять адрес в «отправляется» до сторожа.
                message = mail.build_message(cfg, to=[item['email']], subject=piece['subject'], text=piece['text'],
                                             html=piece['html'], display_name=piece['name'])
                with db._get_cursor() as cursor:
                    attempt = _take(cursor, item['id'])
                if attempt is None:
                    continue
                try:
                    message_id = smtp.send(message)
                except mail.MailError as exc:
                    with db._get_cursor() as cursor:
                        if exc.kind == 'transport':
                            _give_back(cursor, item['id'])
                        else:
                            _failed(cursor, item['id'], attempt, exc)
                    if exc.kind in ('transport', 'throttled'):
                        raise
                    result['failed'] += 1
                    streak = streak + [(item['id'], attempt)] if exc.kind == 'permanent' else []
                    if len(streak) >= REFUSALS_IN_A_ROW:
                        # Три адреса подряд — скорее ящик, чем адреса: им повтор, прогону пауза.
                        with db._get_cursor() as cursor:
                            for recipient_id, tried in streak:
                                _retry_later(cursor, recipient_id, tried)
                        raise mail.MailError('throttled', STREAK_ERROR)
                    continue
                with db._get_cursor() as cursor:
                    _sent(cursor, item['id'], item['mailing_id'], message_id)
                result['sent'] += 1
                streak = []
    except mail.MailError as exc:
        # Ящик не пускает, упёрся в лимит или фильтр: дело в нём, а не в адресах.
        _PAUSE['until'] = clock() + TRANSPORT_PAUSE_SECONDS
        logging.warning('Оплата счетов: письма о реквизитах не отправлены — %s', exc)
        _note_error(db, set(waiting) | set(letters), exc)
        result['error'] = str(exc)
    with db._get_cursor() as cursor:
        result['finished'] += _finish(cursor)
    return result

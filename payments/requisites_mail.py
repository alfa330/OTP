"""Письма поставщикам о смене реквизитов наших компаний.

Просьба владельца 20.10.2026: «добавление эмайла контрагентов поставщиков и тд,
автомат рассылка по эмайлом при изменении реквизитов». Решения владельца в тот же
день (ответы на вопросы с вариантами):
  * меняются реквизиты НАШЕЙ компании (справочник «Реквизиты компаний») — письмо
    готовится поставщикам. Правка реквизитов самого поставщика писем не вызывает;
  * получают все поставщики, у которых заполнена почта, — и скрытые тоже: «никого
    не пропустим» (у скрытого бывают незакрытые счета и акты на нашу компанию);
  * правка, пока письмо не ушло, не рождает второе письмо — попадает в то же
    (уходят последние реквизиты); «Не отправлять» отменяет письмо; реквизиты вернули
    к тем, что знают поставщики, — письма нет вовсе.
Решения 21.10.2026 (тоже ответы на вопросы с вариантами):
  * кому уходит письмо, выбирают галочками в его окне, пока оно ждёт: по умолчанию
    отмечены все поставщики с почтой, снятые — в excluded_counterparty_ids. Хранится
    «кого не отправлять», а не «кому»: поставщик, которому почту дописали, пока
    письмо ждало, получит его, как и раньше;
  * адреса ведутся отдельным справочником «Почта поставщиков» (directory.py).
Решение 27.10.2026: «пока убери автоотправку и сделай полу ручной». Портал писем
сам не отправляет: кнопка «Отправить письмо» открывает почту компании (поле «Почта
для писем» в окне компании; Gmail — в браузере, другая — почтовой программой) с
поставщиками в «Скрытой копии» и темой, а текст кладёт в буфер — человек вставляет
его и отправляет сам. Всем отмеченным — одним письмом: в скрытой копии поставщики
не видят адреса друг друга. Отправку по расписанию с почты компании (SMTP, вход
через Google) — см. git до 27.10.2026, коммиты 216f22ff, c1c023ba, 91cac050.

Как устроено.
  * Письмо — строка payment_requisites_mailings: «как было» и «как стало» —
    снимком тех полей, что идут в «Скопировать реквизиты» (REQUISITE_FIELDS).
    «Как было» — то, что поставщики ЗНАЮТ (known): реквизиты из последнего
    ушедшего письма (открытого в почте; до 27.10.2026 — отправленного порталом), а
    если писем не уходило — из самого первого письма (всё остальное отменили) или
    из строки компании до правки. Поэтому опечатка, сохранённая и отменённая «Не
    отправлять», а потом исправленная, письма не рождает, а отменённая настоящая
    смена войдёт в «что изменилось» следующего письма — поставщик увидит всё, чего
    не знал. Ожидающее письмо у компании одно — уникальный частичный индекс.
  * «Отправить письмо» (open_in_mail): письмо переходит в «открыто в почте»
    (status = 'opened') — кто и когда; адреса, которые кнопка вставила в «Скрытую
    копию», записываются строками payment_requisites_mail_recipients — для истории
    и колонки «Последнее письмо» в «Почте поставщиков». Нажал ли человек «Отправить»
    в самой почте, портал не видит: открытое письмо считается ушедшим.
  * Получатель — адрес, а не поставщик: адрес, записанный у двух поставщиков,
    попадает в письмо один раз (и тогда, когда одного из двух сняли галочкой:
    второму письмо нужно).
"""

import html as _html
import logging

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

HISTORY_LIMIT = 10
WITHOUT_EMAIL_NAMES = 50
# Адресов в одном письме — не больше: столько поставщиков с почтой у раздела не бывает,
# а запрос с тысячами «адресов» — не от кнопки.
MAX_LETTER_ADDRESSES = 500
# Адрес из письма, отправленного порталом до 27.10.2026, который так и не ушёл.
AUTO_OFF_ERROR = 'Автоотправка выключена 27.10.2026 — на этот адрес письмо не ушло'

CONTACT_EMAILS_MARK = 'contact_emails_adopted'
MAILBOX_ADDRESS_MARK = 'mailbox_addresses_adopted'


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


def _name(after):
    # Название идёт в тему: перенос строки в заголовке письма недопустим.
    return ' '.join(after['name'].split()) or 'нашей компании'


def subject_of(after):
    return 'Изменение реквизитов %s' % _name(snapshot(after))


def _parts(before, after):
    """(тема, текст, HTML содержимого) — одно письмо на всех получателей. HTML без
    страницы вокруг: так его вставляют в письмо, а окно портала оборачивает его в
    страницу (letter)."""
    before, after = snapshot(before), snapshot(after)
    name = _name(after)
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
    content = (
        '<p style="margin:0 0 12px">Здравствуйте!</p>'
        '<p style="margin:0 0 16px">Сообщаем об изменении реквизитов <b>%(who)s</b>.</p>'
        '%(table)s'
        '<p style="margin:0 0 6px;font-weight:600">Новые реквизиты целиком</p>'
        '<div style="margin:0 0 20px;padding:12px 14px;background:#f8fafc;border-radius:12px;font-size:14px">'
        '%(requisites)s</div>'
        '<p style="margin:0 0 16px">%(ask)s</p>'
        '<p style="margin:0">С уважением,<br>%(name)s</p>'
    ) % {'who': esc(who), 'table': table, 'requisites': esc(requisites), 'ask': esc(ask), 'name': esc(name)}
    return subject, text, content


def _page(subject, content):
    """Письмо страницей — как его покажет почта поставщика (окно письма в портале)."""
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>%(subject)s</title>'
        # Телефон: поля письма уже. Кто не понимает <style> (Outlook), видит письмо как на компьютере.
        '<style>@media only screen and (max-width:480px){.letter-page{padding:12px 6px!important}'
        '.letter-card{padding:20px 16px!important}}</style></head>'
        '<body class="letter-page" style="margin:0;padding:24px 12px;background:#f5f5f7;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
        'color:#0f172a;font-size:15px;line-height:1.5">'
        '<div class="letter-card" style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:16px;padding:28px 24px">'
        '%(content)s'
        '</div></body></html>'
    ) % {'subject': _html.escape(subject), 'content': content}


def letter(before, after):
    """(тема, текст, HTML страницей) — одно письмо на всех получателей."""
    subject, text, content = _parts(before, after)
    return subject, text, _page(subject, content)


def letter_view(before, after):
    """Письмо для экрана: тема, текст, страница (окно письма) и то, что кнопка
    «Отправить письмо» кладёт в буфер (`paste`): содержимое без страницы вокруг —
    серый фон и карточка в чужом письме смотрелись бы рамкой. Цвет и размер шрифта —
    на обёртке: вставленное не должно зависеть от того, что стоит в почте по умолчанию."""
    subject, text, content = _parts(before, after)
    paste = '<div style="color:#0f172a;font-size:14px;line-height:1.5">%s</div>' % content
    return {'subject': subject, 'text': text, 'html': _page(subject, content), 'paste': paste}


# ─── Что знают поставщики ────────────────────────────────────────────────────

def known(cursor, entity_id):
    """Реквизиты компании, которые знают поставщики, — снимок либо None (писем не было).

    Последнее ушедшее письмо: открытое в почте (до 27.10.2026 — отправленное или
    отправлявшееся порталом). Не уходило ни одно — «как было» самого первого письма:
    все письма после него отменены, а до него поставщики знали то, что было в строке
    компании."""
    cursor.execute(
        "SELECT after_data FROM payment_requisites_mailings WHERE legal_entity_id = %s "
        "AND status IN ('opened', 'sending', 'done') ORDER BY id DESC LIMIT 1", (int(entity_id),))
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
    и письма не будет.

    send_after — с какого момента письмо готово (последняя правка реквизитов):
    по нему письмо стоит в журнале. Само оно ждёт кнопки «Отправить письмо»."""
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
                "UPDATE payment_requisites_mailings SET after_data = %%s, send_after = %s, updated_at = %s, "
                "updated_by = %%s, updated_by_name = %%s WHERE id = %%s" % (NOW_SQL, NOW_SQL),
                (Json(after), actor_id, actor_name, mailing_id))
        # Иначе правили не реквизиты (примечание, «скрыта»): письмо прежнее.
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
        "VALUES (%%s, %%s, %%s, %s, %%s, %%s, %%s, %%s) RETURNING id" % NOW_SQL,
        (int(entity_id), Json(base), Json(after), actor_id, actor_name, actor_id, actor_name))
    return summary(cursor, cursor.fetchone()[0])


def summary(cursor, mailing_id):
    """Коротко о письме — для тоста после сохранения."""
    cursor.execute("SELECT id, status, excluded_counterparty_ids FROM payment_requisites_mailings WHERE id = %s",
                   (int(mailing_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': row[0], 'status': row[1], 'recipients': recipients_overview(cursor, row[2])}


def recipients_overview(cursor, excluded=()):
    """Сколько адресов получат письмо, если отправить сейчас, у кого почты нет и скольких
    поставщиков с почтой сняли галочкой (`excluded`)."""
    excluded = [int(item) for item in excluded or ()]
    cursor.execute(
        "SELECT (SELECT COUNT(DISTINCT e.email) FROM payment_counterparties cp, unnest(cp.emails) AS e(email) "
        "         WHERE NOT (cp.id = ANY(%s::int[]))), "
        "       (SELECT COUNT(*) FROM payment_counterparties WHERE cardinality(emails) > 0 "
        "           AND NOT (id = ANY(%s::int[]))), "
        "       (SELECT COUNT(*) FROM payment_counterparties WHERE cardinality(emails) = 0), "
        "       ARRAY(SELECT name FROM payment_counterparties WHERE cardinality(emails) = 0 "
        "             ORDER BY lower(name) LIMIT %s), "
        "       (SELECT COUNT(*) FROM payment_counterparties WHERE cardinality(emails) > 0 AND id = ANY(%s::int[]))",
        (excluded, excluded, WITHOUT_EMAIL_NAMES, excluded))
    addresses, suppliers, without, names, skipped = cursor.fetchone()
    return {'addresses': int(addresses or 0), 'suppliers': int(suppliers or 0),
            'without_email': int(without or 0), 'without_names': list(names or []), 'excluded': int(skipped or 0)}


def candidates(cursor, excluded=()):
    """Галочки «Кому» у ждущего письма: все поставщики с почтой, отмечены — кроме снятых."""
    skipped = {int(item) for item in excluded or ()}
    cursor.execute("SELECT id, name, is_active, emails FROM payment_counterparties WHERE cardinality(emails) > 0 "
                   "ORDER BY lower(name), id")
    return [{'id': row[0], 'name': row[1], 'is_active': bool(row[2]), 'emails': list(row[3] or []),
             'send': row[0] not in skipped} for row in cursor.fetchall()]


def _names(cursor, counterparty_ids):
    ids = [int(item) for item in counterparty_ids or ()]
    if not ids:
        return []
    cursor.execute("SELECT name FROM payment_counterparties WHERE id = ANY(%s::int[]) ORDER BY lower(name)", (ids,))
    return [row[0] for row in cursor.fetchall()]


# ─── Перенос при запуске ─────────────────────────────────────────────────────

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


def adopt_mailbox_addresses(cursor):
    """Один раз (27.10.2026): адрес почты, подключённой у компании для автоотправки
    (payment_entity_mailboxes, пароль или вход через Google), становится её «Почтой для
    писем» — кнопка откроет ту же почту. Сам ящик и его доступ остаются в таблице как
    были: «пока убери» — значит, к нему могут вернуться. Повторно не срабатывает:
    адрес, который потом стёрли руками, не вернётся."""
    cursor.execute("INSERT INTO payment_settings (key, value) VALUES (%s, 'true') "
                   "ON CONFLICT (key) DO NOTHING RETURNING key", (MAILBOX_ADDRESS_MARK,))
    if not cursor.fetchone():
        return 0
    cursor.execute(
        "UPDATE payment_legal_entities le SET letters_email = mb.address, letters_provider = mb.provider "
        "  FROM payment_entity_mailboxes mb WHERE mb.legal_entity_id = le.id AND le.letters_email IS NULL")
    if cursor.rowcount:
        logging.info('Оплата счетов: почта для писем перенесена из подключённой почты, компаний: %s', cursor.rowcount)
    return cursor.rowcount


def close_auto_sending(cursor):
    """Письмо, которое портал отправлял в момент выкладки 27.10.2026, больше никто не
    допошлёт: адреса, что ждали отправки или повтора, — «не ушло» с причиной, письмо —
    отправленным. Иначе оно навсегда осталось бы «отправляется». На каждом старте;
    после первого ничего не находит."""
    cursor.execute(
        "UPDATE payment_requisites_mail_recipients SET status = 'failed', next_attempt_at = NULL, "
        "       last_error = %%s, updated_at = %s "
        " WHERE status IN ('queued', 'sending') OR (status = 'failed' AND next_attempt_at IS NOT NULL)" % NOW_SQL,
        (AUTO_OFF_ERROR,))
    cursor.execute("UPDATE payment_requisites_mailings SET status = 'done', finished_at = %s, updated_at = %s "
                   " WHERE status = 'sending'" % (NOW_SQL, NOW_SQL))
    return cursor.rowcount


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


_STATE_WORDS = {'opened': 'Письмо уже открыто в почте', 'sending': 'Письмо уже отправляется',
                'done': 'Письмо уже отправлено', 'cancelled': 'Письмо уже отменено'}


def cancel(cursor, entity_id, mailing_id, actor):
    """«Не отправлять» — пока письмо не открыли в почте."""
    status = _status(cursor, entity_id, mailing_id, lock=True)
    if status != 'pending':
        raise MailingError(_STATE_WORDS.get(status, 'Письмо уже не ждёт отправки'))
    cursor.execute(
        "UPDATE payment_requisites_mailings SET status = 'cancelled', cancelled_at = %s, updated_at = %s, "
        "cancelled_by = %%s, cancelled_by_name = %%s WHERE id = %%s" % (NOW_SQL, NOW_SQL),
        ((actor or {}).get('id'), (actor or {}).get('name'), int(mailing_id)))


def _current_addresses(cursor, excluded=()):
    """Адрес → (номера поставщиков, названия) по поставщикам с почтой, кроме снятых галочкой."""
    cursor.execute(
        "SELECT e.email, array_agg(cp.id ORDER BY lower(cp.name), cp.id), array_agg(cp.name ORDER BY lower(cp.name), cp.id) "
        "  FROM payment_counterparties cp, unnest(cp.emails) AS e(email) "
        " WHERE NOT (cp.id = ANY(%s::int[])) GROUP BY e.email", ([int(item) for item in excluded or ()],))
    return {row[0]: (list(row[1]), list(row[2])) for row in cursor.fetchall()}


def set_recipients(cursor, entity_id, mailing_id, actor, excluded):
    """Галочки «Кому» у ждущего письма: снятым поставщикам оно не уйдёт. Письмо,
    которое уже открыли в почте, не переадресовать. Автор правки реквизитов остаётся
    прежним: им подписано письмо в карточке, а не тем, кто ставил галочки."""
    status = _status(cursor, entity_id, mailing_id, lock=True)
    if status != 'pending':
        raise MailingError(_STATE_WORDS.get(status, 'Письмо уже не ждёт отправки'))
    ids = sorted({int(item) for item in excluded or ()})
    if ids:
        cursor.execute("SELECT id FROM payment_counterparties WHERE id = ANY(%s::int[]) ORDER BY id", (ids,))
        ids = [row[0] for row in cursor.fetchall()]
    cursor.execute("UPDATE payment_requisites_mailings SET excluded_counterparty_ids = %s::int[] WHERE id = %s",
                   (ids, int(mailing_id)))
    return {'excluded': len(ids)}


def open_in_mail(cursor, entity_id, mailing_id, actor, addresses=None):
    """«Отправить письмо»: письмо открыли в почте компании — отправит его человек сам
    (решение владельца 27.10.2026). Письмо считается ушедшим: следующее письмо
    компании сравнит реквизиты с ним (known).

    `addresses` — что кнопка вставила в «Скрытую копию»: в историю идут именно они, с
    отмеченными поставщиками, чьи они сейчас (адрес, который успели убрать из карточки, —
    с тем, у кого он ещё есть, или без поставщика). Нет списка (старая вкладка) — адреса
    отмеченных поставщиков на этот момент. Повторное открытие того же письма («Открыть в
    почте ещё раз») сюда не ходит: письмо уже открыто.
    Возвращает {'addresses': сколько адресов в письме}."""
    status = _status(cursor, entity_id, mailing_id, lock=True)
    if status != 'pending':
        raise MailingError(_STATE_WORDS.get(status, 'Письмо уже не ждёт отправки'))
    cursor.execute("SELECT excluded_counterparty_ids FROM payment_requisites_mailings WHERE id = %s", (int(mailing_id),))
    chosen = _current_addresses(cursor, cursor.fetchone()[0])
    if addresses is None:
        emails = sorted(chosen)
    else:
        emails = []
        for item in addresses:
            try:
                email = mail.one_email(item)
            except mail.EmailError as exc:
                raise MailingError('Адрес «%s»: %s' % (str(item)[:80], exc), status=400,
                                   code='PAYMENTS_EMAIL_INVALID') from None
            if email not in emails:
                emails.append(email)
        if len(emails) > MAX_LETTER_ADDRESSES:
            raise MailingError('Слишком много адресов для одного письма', status=400, code='PAYMENTS_EMAIL_INVALID')
        emails.sort()
    if not emails:
        raise MailingError('Письмо некому отправить: ни у одного отмеченного поставщика нет почты')
    owners = _current_addresses(cursor) if set(emails) - set(chosen) else chosen
    for email in emails:
        ids, names = chosen.get(email) or owners.get(email) or ([], [])
        cursor.execute(
            "INSERT INTO payment_requisites_mail_recipients (mailing_id, email, counterparty_ids, counterparty_names, "
            "status, sent_at, updated_at) VALUES (%%s, %%s, %%s, %%s, 'opened', %s, %s) "
            "ON CONFLICT (mailing_id, email) DO NOTHING" % (NOW_SQL, NOW_SQL),
            (int(mailing_id), email, ids, names))
    cursor.execute(
        "UPDATE payment_requisites_mailings SET status = 'opened', opened_at = %s, updated_at = %s, "
        "opened_by = %%s, opened_by_name = %%s WHERE id = %%s" % (NOW_SQL, NOW_SQL),
        ((actor or {}).get('id'), (actor or {}).get('name'), int(mailing_id)))
    return {'addresses': len(emails)}


# ─── Что показать в справочнике ──────────────────────────────────────────────

_MAILING_FIELDS = ('id', 'legal_entity_id', 'status', 'before_data', 'after_data', 'send_after', 'created_at',
                   'created_by_name', 'updated_at', 'updated_by_name', 'started_at', 'finished_at', 'cancelled_at',
                   'cancelled_by_name', 'opened_at', 'opened_by_name', 'excluded_counterparty_ids')
_RECIPIENT_FIELDS = ('mailing_id', 'email', 'counterparty_names', 'status', 'last_error', 'sent_at')
_ORDER = {'failed': 0, 'removed': 1, 'opened': 2, 'superseded': 4, 'sent': 5}


def _counts(recipients):
    """total — адреса письма: в открытом письме, ушедшие или не ушедшие по вине адреса
    (два последних — у писем, отправленных порталом до 27.10.2026); removed (адрес
    убрали из карточки) и superseded (их взяло новое письмо) считаются отдельно."""
    counts = {'total': 0, 'opened': 0, 'sent': 0, 'failed': 0, 'removed': 0, 'superseded': 0}
    for item in recipients:
        status = item['status']
        if status in ('removed', 'superseded'):
            counts[status] += 1
            continue
        counts['total'] += 1
        if status in ('opened', 'sent', 'failed'):
            counts[status] += 1
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
        # «Открыть в почте ещё раз» — только у последнего неотменённого письма компании:
        # у старого поставщик получил бы старые реквизиты поверх новых.
        item['latest'] = item['id'] == latest
        item['letter'] = letter_view(before, after)
        _choice(cursor, item)
    return items, total


def _choice(cursor, item):
    """Кому письмо: у ждущего — галочки по всем поставщикам с почтой, у ушедшего — кого сняли."""
    excluded = list(item.pop('excluded_counterparty_ids', None) or [])
    item['excluded'] = excluded
    if item['status'] == 'pending':
        item['candidates'] = candidates(cursor, excluded)
    else:
        item['excluded_names'] = _names(cursor, excluded)


def states(cursor):
    """Компания → её последнее неотменённое письмо (ждёт ли оно отправки) и сколько
    писем у неё всего — для метки в строке списка и заготовки блока писем в окне."""
    cursor.execute(
        "SELECT DISTINCT ON (legal_entity_id) legal_entity_id, id, status FROM payment_requisites_mailings "
        " WHERE status <> 'cancelled' ORDER BY legal_entity_id, id DESC")
    result = {row[0]: {'id': row[1], 'status': row[2]} for row in cursor.fetchall()}
    cursor.execute("SELECT legal_entity_id, COUNT(*) FROM payment_requisites_mailings GROUP BY 1")
    counts = {row[0]: int(row[1]) for row in cursor.fetchall()}
    return result, counts


_JOURNAL_SQL = (
    "SELECT %s, le.name, "
    "       COUNT(r.id) FILTER (WHERE r.status = 'opened'), "
    "       COUNT(r.id) FILTER (WHERE r.status = 'sent'), "
    "       COUNT(r.id) FILTER (WHERE r.status = 'failed'), "
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
    name, opened, sent, failed, removed, superseded, latest, total = row[width:width + 8]
    before, after = item.pop('before_data') or {}, item.pop('after_data') or {}
    item['legal_entity_name'] = name
    item['counts'] = {'total': int(opened or 0) + int(sent or 0) + int(failed or 0), 'opened': int(opened or 0),
                      'sent': int(sent or 0), 'failed': int(failed or 0), 'removed': int(removed or 0),
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
        item, (_before, after), total = _journal_row(row)
        item['subject'] = subject_of(after)
        item.pop('excluded_counterparty_ids', None)
        items.append(item)
    return items, total


def detail(cursor, mailing_id):
    """Одно письмо целиком: адресаты, счётчики и само письмо — для окна письма."""
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
    item['letter'] = letter_view(before, after)
    _choice(cursor, item)
    return item

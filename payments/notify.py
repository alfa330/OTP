"""Тексты уведомлений раздела «Оплата счетов»: для колокола и для Telegram. Чистые функции.

ТЗ «Закуп и оплата», п. 17. Уведомление рождается в портале (колокол), а
Telegram — «дополнительный канал оповещения о новой задаче»: отбивка с краткой
информацией и ссылкой на заявку. Согласования, комментарии, документы и смена
статуса — только в iCore: в Telegram нет ни кнопок действий, ни ответа.

Здесь только сборка текста. Кому и когда уходит сообщение — решает flow.py;
отправка живёт в bot_schedule2 (`_send_telegram_text_message`) и приходит в
Blueprint аргументом. Так тексты проверяются тестом без сети.

Событие описывается тройкой: `title` и `body` — строка колокола, `telegram` —
HTML для Telegram. Заголовок говорит, ЧТО сделать, тело — по какой заявке.
"""

import html

from . import workflow

MAX_TITLE = 160

# Что сделать исполнителю — заголовок уведомления о новой задаче.
TASK_TITLES = {
    workflow.KIND_MANAGER: 'Согласуйте закуп',
    workflow.KIND_APPROVAL: 'Утвердите закуп',
    workflow.KIND_INVOICE: 'Новый счёт на оплату',
    workflow.KIND_CARD: 'Пополните карту',
    workflow.KIND_RECEIPT: 'Приложите чек',
    workflow.KIND_RECEIVING: 'Подтвердите получение',
    workflow.KIND_ASSETS: 'Поставьте имущество на учёт',
    workflow.KIND_CLOSING: 'Проверьте закрывающие документы',
}


def _esc(text, limit=None):
    value = str(text or '')
    if limit and len(value) > limit:
        value = value[:limit - 1].rstrip() + '…'
    return html.escape(value, quote=False)


def _cut(text, limit):
    value = str(text or '').strip()
    return value if len(value) <= limit else value[:limit - 1].rstrip() + '…'


def request_link(base_url, request_id):
    base = str(base_url or '').strip().rstrip('/')
    if not base:
        return None
    return '%s/?view=payments&request=%s' % (base, int(request_id))


def reply_markup(link):
    if not link:
        return None
    return {'inline_keyboard': [[{'text': 'Открыть заявку', 'url': link}]]}


def brief(request):
    """Заявка одной строкой для колокола: «№12 · Бумага А4 · 25 000 ₸»."""
    return '№%s · %s · %s' % (int(request.get('id') or 0), _cut(request.get('expense_name') or 'Заявка', 80),
                              workflow.fmt_money(request.get('amount')))


def _headline(request):
    number = int(request.get('id') or 0)
    name = _esc(request.get('expense_name') or 'Заявка на закуп', MAX_TITLE)
    return '<b>Заявка №%s</b> · %s\n%s' % (number, workflow.fmt_money(request.get('amount')), name)


def _who(request):
    parts = []
    if request.get('initiator_name'):
        parts.append('Инициатор: %s' % _esc(request['initiator_name']))
    if request.get('counterparty_name'):
        parts.append('Поставщик: %s' % _esc(request['counterparty_name']))
    if request.get('due_on'):
        parts.append('Срок: %s' % workflow.fmt_date(request['due_on']))
    return '\n'.join(parts)


def _message(header, request, lines=()):
    parts = [header, _headline(request)]
    extra = [line for line in lines if line]
    if extra:
        parts += [''] + extra
    return '\n'.join(parts)


def task(request, subtask):
    """Новая задача исполнителю подзадачи (п. 17: «назначении новой задачи»)."""
    kind = subtask.get('kind')
    title = TASK_TITLES.get(kind) or workflow.subtask_title(kind)
    definition = workflow.subtask(kind) or {}
    return {
        'kind': 'task',
        'title': title,
        'body': brief(request),
        'telegram': _message('💳 Оплата счетов — новая задача', request, [
            '<b>%s</b>' % _esc(title),
            _esc(definition.get('brief')),
            '',
            _who(request),
        ]),
    }


def returned(request, *, by_name, comment):
    """Согласующий вернул заявку на доработку (п. 7)."""
    return {
        'kind': 'returned',
        'title': 'Заявку вернули на доработку',
        'body': '%s%s' % (brief(request), ' — %s' % _cut(comment, 200) if comment else ''),
        'telegram': _message('↩️ Оплата счетов — заявку вернули на доработку', request, [
            'Вернул: %s' % _esc(by_name or 'согласующий'),
            'Что доработать: %s' % _esc(comment, 600) if comment else '',
        ]),
    }


def clarification(request, *, reason_label, comment, by_label):
    """Бухгалтерия или финансовый отдел запросили информацию (п. 8)."""
    detail = ' — '.join(part for part in (reason_label, _cut(comment, 200)) if part)
    return {
        'kind': 'clarification',
        'title': 'Требуется уточнение',
        'body': '%s%s' % (brief(request), ' — %s' % detail if detail else ''),
        'telegram': _message('❓ Оплата счетов — требуется уточнение', request, [
            'Запросил: %s' % _esc(by_label),
            'Причина: %s' % _esc(reason_label) if reason_label else '',
            _esc(comment, 600) if comment else '',
        ]),
    }


def approved(request, *, by_name):
    return {
        'kind': 'approved',
        'title': 'Заявка согласована',
        'body': brief(request),
        'telegram': _message('✅ Оплата счетов — заявка согласована', request, [
            'Согласовал: %s' % _esc(by_name or 'утверждающий'),
        ]),
    }


def rejected(request, *, by_name, comment):
    return {
        'kind': 'rejected',
        'title': 'Заявка отклонена',
        'body': '%s%s' % (brief(request), ' — %s' % _cut(comment, 200) if comment else ''),
        'telegram': _message('⛔ Оплата счетов — заявка отклонена', request, [
            'Отклонил: %s' % _esc(by_name or 'согласующий'),
            'Причина: %s' % _esc(comment, 600) if comment else '',
        ]),
    }


def paid(request):
    line = 'Оплачено %s — %s' % (workflow.fmt_date(request.get('paid_on')),
                                 workflow.fmt_money(request.get('paid_amount')))
    return {
        'kind': 'paid',
        'title': 'Счёт оплачен',
        'body': brief(request),
        'telegram': _message('💸 Оплата счетов — счёт оплачен', request, [line]),
    }


def topped_up(request):
    line = 'Переведено %s — %s' % (workflow.fmt_date(request.get('paid_on')),
                                   workflow.fmt_money(request.get('paid_amount')))
    return {
        'kind': 'topped_up',
        'title': 'Карта пополнена',
        'body': brief(request),
        'telegram': _message('💳 Оплата счетов — карта пополнена', request, [line]),
    }


def cancelled(request, *, comment=None):
    """Заявку отменили — тому, у кого она была в работе, и инициатору."""
    return {
        'kind': 'cancelled',
        'title': 'Заявка отменена',
        'body': '%s%s' % (brief(request), ' — %s' % _cut(comment, 200) if comment else ''),
        'telegram': _message('🚫 Оплата счетов — заявка отменена', request, [
            'Причина: %s' % _esc(comment, 600) if comment else '',
        ]),
    }


def unreachable(event):
    """То же уведомление о задаче, но администратору раздела: исполнителю раздел
    не открыт, и кроме администратора задачу никто не увидит."""
    note = 'Исполнителю раздел не открыт — задачу видят только администраторы'
    return dict(event, body='%s. %s' % (event.get('body') or '', note) if event.get('body') else note,
                telegram='%s\n\n⚠️ %s.' % (event.get('telegram') or '', note))


def docs_needed(request):
    """Инициатору: приложите закрывающие документы (п. 17)."""
    return {
        'kind': 'docs_needed',
        'title': 'Приложите закрывающие документы',
        'body': brief(request),
        'telegram': _message('📎 Оплата счетов — нужны закрывающие документы', request, [
            'Приложите в заявке накладную, акт или чек. Оригиналы передайте в бухгалтерию.',
        ]),
    }


def closed(request):
    return {
        'kind': 'closed',
        'title': 'Заявка закрыта',
        'body': brief(request),
        'telegram': _message('✅ Оплата счетов — заявка закрыта', request, [
            'Все этапы пройдены, закрывающие документы получены.',
        ]),
    }


def due_soon(request, days_left):
    """Напоминание о сроке (п. 17: «приближении срока»)."""
    when = 'сегодня' if days_left <= 0 else ('завтра' if days_left == 1 else 'через %s дн.' % days_left)
    return {
        'kind': 'due_soon',
        'title': 'Срок оплаты — %s' % when,
        'body': brief(request),
        'tone': 'default',
        'telegram': _message('⏳ Оплата счетов — срок подходит', request, [
            'Срок оплаты: %s (%s)' % (workflow.fmt_date(request.get('due_on')), when),
            'Сейчас: %s' % _esc(request.get('current_assignee_name') or request.get('stage_label') or ''),
        ]),
    }


def overdue(request, days_late):
    """Срок прошёл, а заявка не оплачена (п. 17: «просрочке»)."""
    return {
        'kind': 'overdue',
        'title': 'Заявка просрочена на %s дн.' % max(1, int(days_late)),
        'body': brief(request),
        'tone': 'warning',
        'telegram': _message('⚠️ Оплата счетов — заявка просрочена', request, [
            'Срок оплаты был %s.' % workflow.fmt_date(request.get('due_on')),
            'Сейчас: %s' % _esc(request.get('current_assignee_name') or request.get('stage_label') or ''),
        ]),
    }


def generated(request, template_name):
    """Заявку по регулярному платежу создал календарь — сообщение ответственному."""
    lines = ['Платёж: %s' % _esc(template_name, MAX_TITLE)]
    if request.get('due_on'):
        lines.append('Срок оплаты: %s' % workflow.fmt_date(request['due_on']))
    lines.append('Заявка уже отправлена на согласование; вы её инициатор — счёт понадобится перед оплатой.')
    return {
        'kind': 'generated',
        'title': 'Создана заявка по регулярному платежу',
        'body': brief(request),
        'telegram': _message('🗓 Оплата счетов — создана заявка по регулярному платежу', request, lines),
    }

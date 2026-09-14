"""Тексты уведомлений раздела «Оплата счетов» для Telegram. Чистые функции.

Отправка живёт в bot_schedule2 (`_send_telegram_text_message`) и приходит в
Blueprint аргументом; здесь только сборка HTML и кнопки. Так тексты проверяются
тестом без сети, а формат один на все шаги.

Кому и когда уходит сообщение — решает routes.py: наступил шаг → тому, чей шаг
(человеку либо всем участникам роли); вернули или отклонили → инициатору.
"""

import html

from . import workflow

MAX_TITLE = 160


def _esc(text, limit=None):
    value = str(text or '')
    if limit and len(value) > limit:
        value = value[:limit - 1].rstrip() + '…'
    return html.escape(value, quote=False)


def request_link(base_url, request_id):
    base = str(base_url or '').strip().rstrip('/')
    if not base:
        return None
    return '%s/?view=payments&request=%s' % (base, int(request_id))


def reply_markup(link):
    if not link:
        return None
    return {'inline_keyboard': [[{'text': 'Открыть заявку', 'url': link}]]}


def _headline(request):
    number = int(request.get('id') or 0)
    name = _esc(request.get('expense_name') or 'Заявка на оплату', MAX_TITLE)
    amount = workflow.fmt_money(request.get('amount'))
    return '<b>Заявка №%s</b> · %s\n%s' % (number, amount, name)


def _who(request):
    parts = []
    if request.get('initiator_name'):
        parts.append('Инициатор: %s' % _esc(request['initiator_name']))
    if request.get('counterparty_name'):
        parts.append('Контрагент: %s' % _esc(request['counterparty_name']))
    return '\n'.join(parts)


def step_message(request, step_no):
    """«Наступил ваш шаг»: что за заявка, какой шаг и что на нём нужно сделать."""
    item = workflow.step(step_no) or {}
    lines = [
        '💳 Оплата счетов — ваш шаг',
        _headline(request),
        '',
        'Шаг %s из %s: <b>%s</b>' % (step_no, workflow.LAST_STEP, _esc(item.get('title'))),
    ]
    if item.get('brief'):
        lines.append(_esc(item['brief']))
    who = _who(request)
    if who:
        lines += ['', who]
    return '\n'.join(lines)


def returned_message(request, *, to_step, by_name, comment):
    lines = [
        '↩️ Оплата счетов — заявку вернули на доработку',
        _headline(request),
        '',
        'Вернул: %s' % _esc(by_name or 'ответственный'),
        'Куда: шаг %s «%s»' % (to_step, _esc(workflow.step_title(to_step))),
    ]
    if comment:
        lines += ['', 'Комментарий: %s' % _esc(comment, 600)]
    return '\n'.join(lines)


def rejected_message(request, *, by_name, comment):
    lines = [
        '⛔ Оплата счетов — заявка отклонена',
        _headline(request),
        '',
        'Отклонил: %s' % _esc(by_name or 'ответственный'),
    ]
    if comment:
        lines += ['Причина: %s' % _esc(comment, 600)]
    return '\n'.join(lines)


def blocked_message(request, reason):
    lines = [
        '⚠️ Оплата счетов — счёт ожидает действующий договор',
        _headline(request),
        '',
        _esc(reason, 700),
    ]
    return '\n'.join(lines)


def generated_message(request, template_name):
    lines = [
        '🗓 Оплата счетов — создана заявка по календарю фиксированных платежей',
        _headline(request),
        '',
        'Платёж: %s' % _esc(template_name, MAX_TITLE),
    ]
    if request.get('due_on'):
        lines.append('Срок оплаты: %s' % workflow.fmt_date(request['due_on']))
    lines.append('Заявка уже отправлена на согласование; вы её инициатор — счёт понадобится на шаге 7.')
    return '\n'.join(lines)


def done_message(request):
    return '\n'.join([
        '✅ Оплата счетов — заявка закрыта',
        _headline(request),
        '',
        'Оригиналы закрывающих документов получены бухгалтерией.',
    ])

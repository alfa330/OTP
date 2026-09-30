"""Telegram «Требуется закупка воды». Чистые функции: только текст и кнопка.

Отправка живёт в bot_schedule2 (`_send_telegram_text_message`) и приходит в
Blueprint аргументом, как у «Оплаты счетов». Сообщение уходит ПОСЛЕ коммита:
внутри транзакции остаток ещё не записан, и откат оставил бы в Telegram
тревогу о том, чего не случилось.

Когда пишем. Один раз — в момент, когда остаток ОПУСТИЛСЯ до порога закупки
(было выше, стало на пороге или ниже). Каждая следующая выдача ниже порога
молчит: иначе руководителю приходило бы по сообщению на каждого водителя, а
смысл у всех один. Поступление, поднявшее остаток выше порога, «взводит»
уведомление снова само собой — следующее падение опять будет пересечением.
"""

import html

from . import rules


def crossed_buy_threshold(before, after, buy_threshold):
    """Опустился ли остаток до порога закупки именно этой операцией."""
    buy = int(buy_threshold or 0)
    return int(before) > buy >= int(after)


def section_link(base_url):
    """Ссылка на раздел «Учёт воды» — свой пункт меню, ?view=water."""
    base = str(base_url or '').strip().rstrip('/')
    return '%s/?view=water' % base if base else None


def reply_markup(link):
    if not link:
        return None
    return {'inline_keyboard': [[{'text': 'Открыть «Учёт воды»', 'url': link}]]}


def buy_message(office, stock_after):
    """Текст уведомления. Офис — так, как он назван в учёте."""
    city = html.escape(str(office.get('city') or ''), quote=False)
    name = html.escape(str(office.get('name') or ''), quote=False)
    where = ' · '.join(part for part in (city, name) if part)
    return '\n'.join([
        '💧 <b>Требуется закупка воды</b>',
        where,
        'Осталось %s — порог закупки %s.' % (
            rules.blocks_word(int(stock_after)), rules.blocks_word(int(office.get('buy_threshold') or 0))),
    ])

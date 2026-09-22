# -*- coding: utf-8 -*-
"""ИИ пишет текст объявления OLX: промпт, разбор ответа, самопроверка.

Цепочка провайдеров переиспользуется целиком из вики (`wiki/ai/providers.py`):
там уже собраны платные и бесплатные звенья с резервированием, гашением
«мышления» и разбором отказов. Заводить вторую цепочку под раздел значило бы
второй раз проходить путь, который вика прошла за месяц отказов.

Почему ИИ сочиняет ОТДЕЛЬНЫЙ текст каждому объявлению, а не один шаблон
---------------------------------------------------------------------
Решение владельца 14.09.2026. У задачи есть требование вариативности (ТЗ, разд. 4:
«полностью идентичные тексты по всем объявлениям кабинета не допускаются»), и
сегодня оно нарушено: из 281 активного объявления 202 — байт-в-байт дубли внутри
своего кабинета. Подстановкой города в один шаблон это не лечится: у 115
заголовков запас до предела в 70 символов меньше шести знаков, а у шести он
нулевой — город туда физически не влезает. Поэтому модель получает контекст
объявления (кабинет, направление, город) и пишет под него свой текст, а предел
длины проверяется уже нашим кодом.

Самопроверка
------------
Ответ модели прогоняется через `validate.check` — те же правила, что у OLX. Если
есть блокирующие замечания, делается РОВНО ОДНА повторная попытка с перечислением
претензий. Одна, а не «пока не получится»: вторая неудача означает, что модель не
поняла задачу, и тогда честнее показать человеку черновик с замечаниями, чем
жечь токены в цикле. Заголовок при этом ещё и подрезается по границе слова —
это дешёвая механическая правка, ради которой незачем ходить в модель.
"""

import logging
import re

from . import validate

log = logging.getLogger(__name__)

# Направление объявления выводится из категории OLX, а не из настроек: категория
# приезжает вместе с объявлением и не может разойтись с реальностью.
# Числа проверены на живых данных 14.09.2026 (281 активное объявление).
DIRECTIONS = {
    1812: {
        'code': 'taxi',
        'title': 'водитель такси',
        'brief': 'набор водителей в таксопарк для работы в Яндекс Такси',
    },
    1802: {
        'code': 'courier',
        'title': 'курьер',
        'brief': 'набор курьеров в Яндекс Доставку (пешком, на велосипеде или на авто)',
    },
    1801: {
        'code': 'driver',
        'title': 'водитель',
        'brief': 'набор водителей, в том числе на грузовой транспорт и грузоперевозки',
    },
    1942: {
        'code': 'other',
        'title': 'сотрудник',
        'brief': 'набор сотрудников в таксопарк',
    },
    3031: {
        'code': 'rent',
        'title': 'аренда авто',
        'brief': 'сдача автомобилей в аренду под работу в такси (это НЕ вакансия, '
                 'а предложение арендовать машину)',
    },
}

_DEFAULT_DIRECTION = {
    'code': 'other', 'title': 'сотрудник',
    'brief': 'набор сотрудников в таксопарк',
}


def direction_for(category_id):
    try:
        return DIRECTIONS.get(int(category_id)) or _DEFAULT_DIRECTION
    except (TypeError, ValueError):
        return _DEFAULT_DIRECTION


SYSTEM_PROMPT = """\
Ты пишешь тексты объявлений для казахстанской доски OLX.kz. Заказчик — таксопарк,
объявления зовут водителей и курьеров на подключение к Яндекс Про.

Пиши как человек, который сам работал в парке: конкретно, без рекламного пафоса и
без штампов вроде «в динамично развивающуюся компанию». Не выдумывай фактов: бери
только то, что дано во вводных. Чего во вводных нет — того в тексте быть не должно.

ЖЁСТКИЕ ТРЕБОВАНИЯ ПЛОЩАДКИ (нарушение = объявление отклонят):
* заголовок от 16 до 70 символов ВКЛЮЧИТЕЛЬНО. Это предел площадки, не пожелание;
* описание от 80 до 9000 символов;
* заглавных букв не больше половины текста. Подзаголовки капсом допустимы, но
  редко — два-три на всё описание;
* НИ ОДНОГО номера телефона и ни одного адреса почты — ни в заголовке, ни в
  описании. Не пиши «звоните по номеру», просто «звоните»;
* нельзя три одинаковых знака препинания подряд («!!!», «...»);
* описание — HTML, и разрешены ТОЛЬКО теги <p>, <ul>, <li>, <strong>, <em>.
  Каждый абзац оборачивай в <p>. Никаких <br>, <div>, <h1>, style и class.

ФОРМАТ ОТВЕТА — строго такой, без пояснений до и после:
ЗАГОЛОВОК: <одна строка>
ОПИСАНИЕ:
<p>...</p><p>...</p>
"""


def _line(label, value):
    value = (value or '').strip()
    return '%s: %s\n' % (label, value) if value else ''


def build_user_prompt(advert, brief=None, instruction=None):
    """Вводные для модели: кто мы, кого зовём, в каком городе и что предлагаем."""
    direction = direction_for(advert.get('category_id'))
    city = (advert.get('city_name') or '').strip()
    company = (advert.get('company_name') or '').strip()

    parts = ['ВВОДНЫЕ ДЛЯ ЭТОГО ОБЪЯВЛЕНИЯ\n']
    parts.append(_line('Название парка', company))
    parts.append(_line('Кого зовём', direction['brief']))
    parts.append(_line('Город', city))
    if city:
        parts.append('Город должен быть виден в тексте: человек ищет работу '
                     'рядом с домом. В заголовок город ставь только если он туда '
                     'помещается по длине.\n')

    if brief:
        parts.append('\nПРЕДЛОЖЕНИЕ МЕСЯЦА (только эти факты, ничего сверх)\n')
        parts.append(_line('Оффер', brief.get('offer')))
        parts.append(_line('Бонус за подключение', brief.get('bonus')))
        parts.append(_line('Действующая акция', brief.get('promo')))
        parts.append(_line('Розыгрыш', brief.get('raffle')))
        parts.append(_line('Комиссия парка', brief.get('commission')))
        parts.append(_line('Доход', brief.get('income')))
        parts.append(_line('Что ещё упомянуть', brief.get('extra')))

    current_title = (advert.get('title') or '').strip()
    if current_title:
        parts.append('\nСЕЙЧАС В ОБЪЯВЛЕНИИ ВИСИТ ТАКОЙ ЗАГОЛОВОК (его надо '
                     'ЗАМЕНИТЬ, а не повторить):\n%s\n' % (current_title,))

    if instruction:
        parts.append('\nОТДЕЛЬНОЕ УКАЗАНИЕ РЕДАКТОРА (важнее общих правил стиля):\n')
        parts.append('%s\n' % (str(instruction).strip(),))

    parts.append('\nНапиши новый заголовок и новое описание.')
    return ''.join(parts)


_TITLE_RE = re.compile(r'ЗАГОЛОВОК\s*:\s*(.+)', re.I)
_BODY_RE = re.compile(r'ОПИСАНИЕ\s*:\s*(.*)', re.I | re.S)


def parse_answer(text):
    """Разобрать ответ модели в пару (заголовок, описание).

    Формат простой и «конвертный» — тот же приём, что у редактора вики: модель
    надёжно переносит строки-маркеры, а разбор по ним переживает и лишние
    пояснения вокруг, и потерянный перенос строки.
    """
    text = (text or '').strip()
    if not text:
        return '', ''

    title_match = _TITLE_RE.search(text)
    body_match = _BODY_RE.search(text)

    title = (title_match.group(1).strip() if title_match else '').strip(' "«»')
    body = (body_match.group(1).strip() if body_match else '').strip()

    if not body and not title_match:
        # Модель ответила без маркеров: первая строка — заголовок, остальное тело.
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if lines:
            title = lines[0].strip(' "«»')
            body = '\n'.join(lines[1:]).strip()

    if body and '<p>' not in body.lower():
        # Пришёл обычный текст — раскладываем по абзацам сами, чтобы описание
        # всегда уезжало в OLX в одном и том же виде.
        chunks = [ln.strip() for ln in re.split(r'\n{1,}', body) if ln.strip()]
        body = ''.join('<p>%s</p>' % (chunk,) for chunk in chunks)

    return title, body


def _complaints(problems):
    return '; '.join(p['message'] for p in problems)


class AiUnavailable(RuntimeError):
    """Цепочка ИИ не ответила ни одним звеном — это не про объявление, а про ИИ.

    Отдельный класс, чтобы сервис отличал «модель не поняла задачу» (текст есть,
    но с замечаниями) от «ИИ лежит»: во втором случае гнать по цепочке остальные
    тридцать объявлений пачки бессмысленно — каждое будет ждать отказа всех
    звеньев, и человек получит тридцать одинаковых ошибок через десять минут
    вместо одной сразу.
    """


def as_result(raw):
    """Привести ответ модели к словарю {text, model, provider}, какой бы формы он ни был.

    Цепочка вики (`wiki.ai.providers.generate`) возвращает КОРТЕЖ
    (текст, метаданные), а тестовые заглушки и старый код раздела — словарь или
    строку. Из-за этого расхождения раздел на проде не написал ни одного текста
    с 14.09 по 22.09.2026: кортеж уходил в `parse_answer`, тот звал `.strip()`,
    и каждое объявление падало с «'tuple' object has no attribute 'strip'»,
    хотя модель отвечала. Юнит-тесты этого не ловили — заглушки отдавали словарь.
    Поэтому форма ответа приводится к одной ЗДЕСЬ, до всякого разбора.
    """
    if raw is None:
        return {'text': '', 'model': None, 'provider': None}
    if isinstance(raw, dict):
        return {'text': raw.get('text') or '', 'model': raw.get('model'),
                'provider': raw.get('provider')}
    if isinstance(raw, (tuple, list)):
        text = raw[0] if raw else ''
        meta = raw[1] if len(raw) > 1 and isinstance(raw[1], dict) else {}
        return {'text': text or '', 'model': meta.get('model'),
                'provider': meta.get('provider')}
    return {'text': str(raw), 'model': None, 'provider': None}


def _wiki_generate_fn(chain):
    """Настоящая цепочка вики, обёрнутая под форму ответа этого модуля."""
    from wiki.ai import providers as wiki_providers

    def generate_fn(system, user, **kwargs):
        try:
            text, meta = wiki_providers.generate(system, user, chain=chain, **kwargs)
        except wiki_providers.ProviderError as exc:
            raise AiUnavailable(str(exc)) from exc
        return {'text': text, 'model': meta.get('model'),
                'provider': meta.get('provider')}

    return generate_fn


def generate_for_advert(advert, brief=None, instruction=None, generate_fn=None,
                        chain=None):
    """Сочинить текст для одного объявления.

    `generate_fn` вынесен параметром ради тестов: настоящая цепочка ходит в сеть,
    а проверять разбор ответа и самопроверку надо без неё. Что бы `generate_fn`
    ни вернул — словарь, кортеж вики или строку, — ответ проходит через
    `as_result`, так что разбор дальше видит всегда одну форму.

    Возвращает словарь с текстом и с замечаниями, которые остались ПОСЛЕ попытки
    исправления. Замечания не прячем: черновик всё равно смотрит человек, и
    честнее показать «модель не уложилась в 70 символов», чем молча обрезать
    смысл. Если ИИ не ответил вовсе — `AiUnavailable`, без черновика.
    """
    if generate_fn is None:
        generate_fn = _wiki_generate_fn(chain)

    category_id = advert.get('category_id')
    user_prompt = build_user_prompt(advert, brief=brief, instruction=instruction)

    result = as_result(generate_fn(SYSTEM_PROMPT, user_prompt))
    title, description = parse_answer(result['text'])

    problems = validate.check(title, description, category_id)
    if validate.blocking(problems):
        # Одна повторная попытка с перечислением претензий. Заголовок к этому
        # моменту ещё НЕ подрезаем: пусть модель попробует уложиться смыслом, а
        # не обрубком.
        retry_prompt = (user_prompt
                        + '\n\nПРЕДЫДУЩАЯ ПОПЫТКА НЕ ПРОШЛА ПРОВЕРКУ: %s.\n'
                          'Исправь ровно это и пришли текст заново в том же формате.'
                        % (_complaints(validate.blocking(problems)),))
        try:
            retry = as_result(generate_fn(SYSTEM_PROMPT, retry_prompt))
            retry_title, retry_description = parse_answer(retry['text'])
            if retry_title and retry_description:
                retry_problems = validate.check(retry_title, retry_description,
                                                category_id)
                if len(validate.blocking(retry_problems)) <= len(validate.blocking(problems)):
                    title, description = retry_title, retry_description
                    problems = retry_problems
                    result = retry
        except Exception:                                    # noqa: BLE001
            # Первый ответ уже есть — отказ на повторе (в том числе AiUnavailable)
            # не отменяет черновик, а оставляет его с замечаниями.
            log.exception('Объявления OLX: повторная попытка ИИ не удалась')

    # Механическая правка напоследок: длину заголовка чиним сами, по границе
    # слова. Ходить в модель ради обрезки строки незачем.
    if len(title) > validate.TITLE_MAX:
        title = validate.trim_title(title)
        problems = validate.check(title, description, category_id)

    return {
        'title': title,
        'description': description,
        'model': result['model'],
        'provider': result['provider'],
        'problems': problems,
        'ok': bool(title and description and not validate.blocking(problems)),
    }

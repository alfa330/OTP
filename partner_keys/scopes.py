# -*- coding: utf-8 -*-
"""Области доступа ключа партнёра: что именно ключ открывает. Чистая логика.

Одна область — одна дверь портала. Ручка сверяет ключ со своей областью
(auth.lookup); супер-админ в «Ключах» выбирает области при выпуске и меняет их
позже. Новая внешняя ручка = новая строка здесь и проверка в самой ручке.
"""

from collections import namedtuple

Scope = namedtuple('Scope', 'code title hint endpoints header')

KNOWLEDGE = 'knowledge'
SIGN_LINKS = 'sign_links'
CRM_EMBED = 'crm_embed'
OLX_CHATS = 'olx_chats'
HIRING_LEADS = 'hiring_leads'

SCOPES = (
    Scope(KNOWLEDGE, 'Ручка знаний',
          'Фрагменты базы знаний в правах оператора СЗоВ: партнёр присылает парк и вопрос, '
          'получает куски статей, карточку парка, офисы и комиссии. Для ИИ Syntony.',
          ('POST /api/knowledge/search', 'GET /api/knowledge/parks', 'GET /api/knowledge/ping'),
          'X-Api-Key'),
    Scope(SIGN_LINKS, 'Ссылки на подписание',
          'Ссылки eGov на подписание документов по списку ИИН — внешний вход раздела '
          '«Ссылка на подписание».',
          ('POST /api/sign_links/external/links',),
          'X-Api-Key'),
    Scope(CRM_EMBED, 'Окно «Чатов ОП» в CRM',
          'Сервер CRM получает ссылку на окно чатов для своего пользователя — вход без '
          'пароля iCORE.',
          ('POST /api/wazzup/embed/launch',),
          'Authorization: Bearer или X-Api-Key'),
    Scope(OLX_CHATS, 'Чаты OLX',
          'Чаты девяти кабинетов OLX: список чатов и журнал откликов, переписка с '
          'кандидатом, ответ ему от имени кабинета и подписка на события вебхуком '
          '(новое сообщение, наш ответ).',
          ('GET /api/olx/cabinets', 'GET /api/olx/threads', 'GET /api/olx/threads/{cabinet}/{thread}',
           'POST /api/olx/threads/{cabinet}/{thread}/messages', 'GET /api/olx/journal',
           'GET|PUT|DELETE /api/olx/webhook'),
          'X-Api-Key'),
    Scope(HIRING_LEADS, 'Отклики в воронку найма',
          'Площадки и лид-формы (Kaspi, таргет, свой сайт) присылают отклики кандидатов — '
          'ФИО, телефон, город, вакансия, кампания; отклик попадает в колонку «Отклики» '
          'раздела «Воронка найма», дубли по телефону склеиваются.',
          ('POST /api/hiring/external/responses',),
          'X-Api-Key'),
)
BY_CODE = {scope.code: scope for scope in SCOPES}
ORDER = {scope.code: index for index, scope in enumerate(SCOPES)}


def normalize(values):
    """Список кодов → проверенный список в порядке справочника. ValueError — чужой код."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    cleaned = []
    for value in values:
        code = str(value or '').strip().lower()
        if not code:
            continue
        if code not in BY_CODE:
            raise ValueError('неизвестная область доступа: %s' % code[:40])
        if code not in cleaned:
            cleaned.append(code)
    return sorted(cleaned, key=ORDER.get)


def titles(codes):
    return [BY_CODE[code].title for code in (codes or ()) if code in BY_CODE]


def catalog():
    """Справочник для интерфейса — что показать в списке областей."""
    return [{'code': scope.code, 'title': scope.title, 'hint': scope.hint,
             'endpoints': list(scope.endpoints), 'header': scope.header}
            for scope in SCOPES]

# -*- coding: utf-8 -*-
"""Справочник девяти кабинетов OLX: теги, телефоны линий, тексты ответов.

Таблица перенесена дословно из ТЗ задачи #223 (раздел 4 «Кабинеты OLX и теги» и
раздел 5 «Заготовленные сообщения»). Она же лежала в приложенном файле доступов.

Что здесь есть и чего здесь нет
-------------------------------
Здесь: код кабинета, его id в OLX, телефон линии, тег «форма», тег «звонки» и
текст автоответа. Это рабочий конфиг — по нему считается отчётность, и он должен
быть виден в коде и в диффе, а не прятаться в переменных окружения.

Здесь НЕТ логинов и паролей. Репозиторий публичный, а логины кабинетов — это
почтовые ящики на gmail; страж `tests/test_no_personal_data_in_repo.py` ловит их
отдельным правилом, и он прав. Доступы приезжают из окружения по именам
`OLX_LOGIN_<N>` / `OLX_PASSWORD_<N>` / `OLX_API_KEY_<N>`, где N — порядковый
номер кабинета в файле доступов (он же `env_index` ниже).

Девять телефонов линий в одном файле — это ровно та телефонная схема, что уже
лежит в `bot_schedule2.py`: номера компании, а не клиентов. Порог стража —
больше десяти РАЗНЫХ мобильных на файл, здесь их девять, и оба написания одного
номера (77… в теге и 87… в тексте ответа) страж считает за один.

Тег «звонки» роботом не ставится
--------------------------------
`tag_call` в таблице ТЗ есть, и он здесь тоже есть, но робот его не проставляет:
он размечает обращения из ЧАТОВ, а `call_*` по определению вешается на лид,
пришедший звонком на линию кабинета. Держим рядом, чтобы источник правды по
кабинету был один, и чтобы будущая привязка звонков не заводила вторую таблицу,
которая тут же разъедется с этой.
"""

import os

# Порядок = порядок в ТЗ и в файле доступов. Он же задаёт `env_index`.
_ROWS = (
    # code,          olx_id,       line_phone,     tag_form,            tag_call
    ('cr',           '62369008',   '77008581223', 'forma_olx_цр',      'call_цр_olx',      'цр_olx'),
    ('adal',         '325202099',  '77008581244', 'forma_olx_adal',    'call_adal_olx',    'adal_olx'),
    ('amanat',       '326525084',  '77002512629', 'forma_olx_amanat',  'call_amanat_olx',  'amanat_olx'),
    ('itaxi',        '188288847',  '77470939685', 'forma_olx_itaxi',   'call_itaxi_olx',   'itaxi_olx'),
    ('global',       '161323828',  '77008581230', 'forma_olx_global',  'call_global_olx',  'global_olx'),
    ('jana',         '419058226',  '77470939675', 'forma_olx_jana',    'call_jana_olx',    'jana_olx'),
    ('tenge',        '1210922526', '77470939672', 'forma_olx_tenge',   'call_tenge_olx',   'tenge_olx'),
    ('noltaxi',      '1238655992', '77082364569', 'forma_olx_noltaxi', 'call_noltaxi_olx', 'noltaxi_olx'),
    ('arenda',       '1243262924', '77470957682', 'forma_arenda_olx',  'call_arenda_olx',  'arenda_olx'),
)

# Текст автоответа отличается только номером, но в ТЗ он выписан по кабинетам, и
# у первого кабинета в конце стоит точка, а у остальных нет. Точку не
# «причёсываем»: текст согласован с заказчиком, и расхождение в один символ —
# не повод переписывать согласованное. Номер в тексте намеренно в виде 8XXX —
# так его набирают с мобильного, и так он написан в постановке.
_GREETING = 'Здравствуйте! По Вашему вопросу просьба позвонить по номеру 8%s'


class Cabinet(object):
    """Один кабинет OLX. Неизменяемый — это справочник, а не состояние."""

    __slots__ = ('code', 'title', 'olx_id', 'line_phone', 'tag_form', 'tag_call',
                 'env_index', 'canned_reply')

    def __init__(self, code, title, olx_id, line_phone, tag_form, tag_call,
                 env_index, canned_reply):
        self.code = code
        self.title = title
        self.olx_id = olx_id
        self.line_phone = line_phone
        self.tag_form = tag_form
        self.tag_call = tag_call
        self.env_index = env_index
        self.canned_reply = canned_reply

    def __repr__(self):
        return '<Cabinet %s olx_id=%s>' % (self.code, self.olx_id)

    # -- доступы -----------------------------------------------------------
    # Читаем окружение КАЖДЫЙ раз, а не один раз при импорте. Причина простая:
    # у четырёх кабинетов ключа приложения пока нет, он появится позже, и
    # подхватить его должен перезапуск процесса, а не пересборка образа.

    @property
    def env_login(self):
        return (os.getenv('OLX_LOGIN_%d' % self.env_index) or '').strip()

    @property
    def env_password(self):
        return (os.getenv('OLX_PASSWORD_%d' % self.env_index) or '').strip()

    @property
    def env_api_key(self):
        return (os.getenv('OLX_API_KEY_%d' % self.env_index) or '').strip()

    @property
    def env_client_id(self):
        """client_id приложения OLX.

        Отдельно от ключа: OLX выдаёт пару client_id + client_secret, а в файле
        доступов приехала только одна строка. Пока пара не собрана, кабинет
        честно считается ненастроенным (`is_configured` ниже) — робот его
        пропускает и говорит об этом в состоянии кабинетов, вместо того чтобы
        каждые полминуты биться в 401.
        """
        return (os.getenv('OLX_CLIENT_ID_%d' % self.env_index) or '').strip()

    @property
    def env_redirect_uri(self):
        """Адрес возврата ИМЕННО этого кабинета, если он свой.

        Обычно адрес один на все девять и живёт в `OLX_REDIRECT_URI`. Но заявки
        на приложения заводились в разное время и разными руками, и в части из
        них уже вписан свой адрес. Менять его там опасно: если тем же
        приложением пользуется что-то ещё, подмена сломает ЕГО экран согласия.
        Дешевле подстроиться нам — отсюда `OLX_REDIRECT_URI_<N>` с приоритетом
        над общим.
        """
        own = (os.getenv('OLX_REDIRECT_URI_%d' % self.env_index) or '').strip()
        return own or (os.getenv('OLX_REDIRECT_URI') or '').strip()

    @property
    def env_client_secret(self):
        """client_secret приложения. Если не задан — берём ключ из файла доступов."""
        explicit = (os.getenv('OLX_CLIENT_SECRET_%d' % self.env_index) or '').strip()
        return explicit or self.env_api_key

    def is_configured(self):
        """Есть ли чем ходить в API за этот кабинет."""
        return bool(self.env_client_id and self.env_client_secret)


CABINETS = tuple(
    Cabinet(code=code, title=title, olx_id=olx_id, line_phone=phone,
            tag_form=tag_form, tag_call=tag_call, env_index=index,
            canned_reply=_GREETING % phone[1:] + ('.' if code == 'cr' else ''))
    for index, (code, olx_id, phone, tag_form, tag_call, title)
    in enumerate(_ROWS, start=1)
)

BY_CODE = {c.code: c for c in CABINETS}
BY_OLX_ID = {c.olx_id: c for c in CABINETS}

# Все телефоны линий — нужны, чтобы НЕ принять собственный номер за номер
# кандидата. Ловушка реальная: робот сам пишет в чат «позвоните по номеру
# 8XXXXXXXXXX», кандидат отвечает цитатой, и в тексте обращения оказывается наш
# же номер. Сделка с телефоном собственной линии — это мусор в воронке.
LINE_PHONES = frozenset(c.line_phone for c in CABINETS)

# Теги «форма» — то, что робот реально проставляет. Список нужен разделу
# отчётности и проверке, что все теги заведены в amoCRM до первого прогона.
FORM_TAGS = tuple(c.tag_form for c in CABINETS)


def get(code_or_id):
    """Кабинет по коду ('itaxi') или по id OLX ('188288847'). None, если нет."""
    if code_or_id is None:
        return None
    key = str(code_or_id).strip()
    return BY_CODE.get(key) or BY_OLX_ID.get(key)


def configured():
    """Кабинеты, у которых собраны доступы. Остальные робот пропускает."""
    return tuple(c for c in CABINETS if c.is_configured())

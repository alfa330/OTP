"""Права раздела «Рассылки». Чистая логика: ни базы, ни Flask.

Модуль намеренно ничего не импортирует из database/flask — так его можно дёргать
в тестах напрямую (импорт database открывает пул к боевой базе; та же причина,
что в fleet_edm/access.py, oktell_guard/access.py и crm/access.py).

ПОЧЕМУ ЗДЕСЬ ИМЕННОЙ СПИСОК, А НЕ РОЛЬ И НЕ ОТДЕЛ.

Обычно периметр раздела выводится из должности: «главы отделов», «супервайзеры
СЗоВ», «роль admin». Здесь так нельзя, и вот почему. Одно нажатие кнопки
«Отправить» доставляет сообщение больше чем тысяче водителей в приложение Pro,
на экран телефона, немедленно. Отозвать его можно ровно пять минут (кабинет
даёт окно delete_limit = 300 секунд), а прочитанное не отзывается вообще. Это
не отчёт, который можно перевыпустить, и не запись в базе, которую можно
поправить: цена ошибки — репутация парка перед водителями, и она невозвратна.

Поэтому периметр назвал владелец поимённо, а не вывел из должности:
супер-админы портала и Закряева Дана Худайбергеновна (users.id = 476). У неё
роль admin и отдел СЗоВ, но открыть раздел «всем admin» или «всему СЗоВ»
означало бы раздать эту кнопку десяткам людей, которым её никто не давал, —
а список admin меняется кадровыми решениями, к рассылкам отношения не имеющими.
Именной список меняется только правкой кода, то есть решением владельца, и это
здесь достоинство, а не неудобство.

Когда периметр расширят — добавляем id в SECTION_ALLOWED_USER_IDS, а не роль
в условие.
"""

# Кому открыт раздел помимо супер-админов.
#
# 476 — Закряева Дана Худайбергеновна (роль admin, отдел СЗоВ), постановщик и
# единственный штатный отправитель по решению владельца.
SECTION_ALLOWED_USER_IDS = (476,)


def normalize_role(role) -> str:
    return str(role or '').strip().lower()


def normalize_department_code(code) -> str:
    return str(code or '').strip().lower()


def _field(user, *names):
    """Пользователь приходит и как dict (из БД), и как объект — берём первое, что
    есть. Имена дублируются в snake_case и camelCase, потому что фронт и бэкенд
    отдают разные варианты одного и того же поля."""
    if user is None:
        return None
    for name in names:
        if isinstance(user, dict):
            if name in user and user[name] not in (None, ''):
                return user[name]
        else:
            value = getattr(user, name, None)
            if value not in (None, ''):
                return value
    return None


def user_id(user):
    """Числовой идентификатор пользователя или None.

    Через int(), потому что из JSON-контекста id прилетает строкой, а сравнение
    строки с числом в кортеже разрешений тихо даёт False — то есть раздел
    закрылся бы человеку, которому он открыт, и без единой ошибки в логе.
    """
    raw = _field(user, 'id', 'user_id', 'userId')
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_department_head(user) -> bool:
    """Глава отдела — это назначение, а не роль: у человека остаётся его базовая
    роль (часто admin), но появляется отдел, которым он руководит."""
    flag = _field(user, 'is_department_head', 'isDepartmentHead')
    if flag is True:
        return True
    if isinstance(flag, str) and flag.strip().lower() in ('1', 'true', 'yes', 'да'):
        return True
    # Признак берётся ИЛИ из флага, ИЛИ из ссылки на возглавляемый отдел: разные
    # источники (БД, фронт, кэш) отдают разный набор полей, и явный False в одном
    # из них не должен перебивать заполненный код отдела в другом.
    if normalize_department_code(_field(user, 'headed_department_code', 'headedDepartmentCode')):
        return True
    head_of = _field(user, 'head_of_department_id', 'headOfDepartmentId',
                     'department_head_id', 'departmentHeadId')
    return head_of is not None


def is_global_admin(user) -> bool:
    """Глобальный админ портала = админская роль БЕЗ назначения главой отдела
    (назначение главой ЗАМЕНЯЕТ базовую роль и режет периметр отделом).

    Функция здесь есть, но ГЕЙТ РАЗДЕЛА ЕЁ НЕ ЗОВЁТ — и это осознанно. Обычных
    админов в портале десятки, кнопку «отправить тысяче водителей» им никто не
    выдавал. Держим её для случая, когда владелец решит расширить периметр: тогда
    правило пишется здесь, а не выводится заново по строке пользователя.
    """
    role = normalize_role(_field(user, 'role'))
    if role == 'super_admin':
        return True
    return role == 'admin' and not is_department_head(user)


def can_view_section(ctx) -> bool:
    """Кто открывает раздел: супер-админы и поимённо разрешённые люди.

    Роль admin сама по себе сюда НЕ пускает (см. is_global_admin и докстроку
    модуля): периметр назван, а не выведен.
    """
    if normalize_role(_field(ctx, 'role')) == 'super_admin':
        return True
    return user_id(ctx) in SECTION_ALLOWED_USER_IDS


def can_send(ctx) -> bool:
    """Кто отправляет рассылку.

    Совпадает с правом просмотра: раздел целиком про «написать и отправить»,
    уровня «смотрю чужие рассылки, но своих не делаю» здесь нет. Отдельная
    функция — чтобы гейт отправки был виден на роуте явно, а не подразумевался.
    """
    return can_view_section(ctx)


def can_manage_templates(ctx) -> bool:
    """Кто заводит и правит шаблоны. Шаблон — это заготовка текста и фильтров,
    то есть половина будущей рассылки; отдавать её более широкому кругу, чем сама
    отправка, бессмысленно."""
    return can_view_section(ctx)


def capabilities(ctx) -> dict:
    """Что показывать в интерфейсе.

    Фронт рисует кнопки по этим флагам, а не по роли: правило периметра тут
    именное, повторить его на клиенте — значит завести вторую копию списка
    сотрудников, которая разойдётся с первой при ближайшей правке.
    """
    can_view = can_view_section(ctx)
    return {
        'can_view': can_view,
        'can_send': can_send(ctx) if can_view else False,
        'can_manage_templates': can_manage_templates(ctx) if can_view else False,
    }

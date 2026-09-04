"""Эндпоинты структуры и доступов раздела «Вики».

Подключается из wiki/routes.py: там общий декоратор wiki_route, здесь —
обработчики, чтобы ни один файл не разросся до нечитаемого состояния.
"""

import re
from datetime import date

from flask import jsonify, request

from . import access as wiki_access
from . import articles as wiki_articles
from . import queries, structure
from . import schema as wiki_schema
from .schema import CAPABILITY_TITLES, SUBJECT_TYPES


def _body():
    return request.get_json(silent=True) or {}


def _day_or_none(value):
    """'2026-08-19' → та же строка, всё остальное → None.

    Дату отдаём в SQL как есть (там стоит %s::date), поэтому проверяем форму
    сами: непроверенная строка из запроса ушла бы в приведение типа и уронила
    бы эндпоинт четырёхсоткой от базы вместо внятного ответа.
    """
    text = (value or '').strip()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        return None
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean(value, limit=255):
    text = str(value or '').strip()
    return text[:limit] if text else None


def request_space(cursor, ctx):
    """Пространство, в котором сделан запрос: (space_id, ошибка).

    Нужно справочникам парков и офисов. У них, в отличие от статей, нет второй
    границы: у статьи пространство сужает уже посчитанный периметр разделов, а
    у офиса больше ничего нет — только эта колонка. Поэтому space_id не
    «уточнение выборки», а сам доступ, и проверять его обязательно ЗДЕСЬ:
    иначе сотрудник Тез КЦ получил бы справочник Таксопарков, дописав чужой id
    в строку запроса, — гард во фронте отсекает пункт меню, но не запрос.

    Откуда берём: сначала строка запроса, потом тело. Фронт присылает
    параметром при любом методе (axios params), тело оставлено для внешних
    вызовов вроде scripts/migrate_wiki_offices.py.

    Параметра нет, а пространство у человека одно — берём его: требовать
    называть единственно возможное значение значило бы ломать внешние вызовы
    ради формальности. Их несколько и параметра нет — 400, потому что молча
    выбрать первое значит показать справочник, которого не спрашивали.
    """
    # include_guest=False: справочники открыты тем, кому пространство ВЫДАНО, а
    # не всякому, кто может в него прийти. Гостя пригласили прочитать один
    # раздел — телефоны парков и адреса офисов в приглашение не входят
    # (queries.spaces_for_user).
    allowed = queries.spaces_for_user(cursor, ctx, include_guest=False)
    raw = request.args.get('space_id')
    if raw is None:
        raw = _body().get('space_id')
    space_id = _int_or_none(raw)

    if space_id is None:
        if len(allowed) == 1:
            return allowed[0], None
        if not allowed:
            return None, (jsonify({
                "error": "Вам не выдано ни одного пространства вики",
                "code": "WIKI_SPACE_REQUIRED",
            }), 403)
        return None, (jsonify({
            "error": "Укажите пространство: space_id",
            "code": "WIKI_SPACE_REQUIRED",
        }), 400)

    if space_id not in allowed:
        # 404, а не 403: существование чужого пространства — тоже сведение о
        # соседней вике, и «доступ запрещён» подтверждало бы, что оно есть.
        return None, (jsonify({
            "error": "Пространство не найдено",
            "code": "WIKI_SPACE_NOT_FOUND",
        }), 404)
    return space_id, None


def log_space(cursor, ctx):
    """Пространство, в котором сделан запрос, — ДЛЯ ЖУРНАЛА, и только.

    Не request_space: та отвечает за ДОСТУП и при двух пространствах без
    параметра отказывает 400. Здесь отказывать нельзя — импорт документа,
    черновик и разбор чужой страницы никакого пространства не проверяют, они
    просто должны попасть в правильный журнал. Нет параметра — вернём None,
    запись останется как была.

    Зачем вообще: пока статьи нет (черновик из документа, превью страницы
    Яндекс Про) или она не сохранена (правка по указанию), пространство записи
    вывести НЕ ИЗ ЧЕГО — schema.AUDIT_SPACE_SQL считает его по объекту. Такая
    запись остаётся без пространства, а «ничья» запись видна в журнале И
    «Таксопарков», И «Теза» (structure._audit_filters). Именно так журналы двух
    вик и перемешались: 25.08.2026 в журнале «Таксопарков» лежали черновики
    Теза, а 01.09.2026 — 36 разборов страниц Яндекс Про в журнале «Теза».

    Живёт здесь, а не в routes_import, где написана впервые: дверей, которые
    пишут в журнал раньше объекта, оказалось больше одной, и вторая копия этой
    функции разошлась бы с первой на первой же правке. Ровно по этой причине
    рядом лежит request_space.

    Чужой id не принимаем: пространство проверяется по выданным человеку, иначе
    запись о его действии уехала бы в чужой журнал по одному параметру строки.
    """
    raw = request.args.get('space_id')
    if raw is None:
        body = request.get_json(silent=True) or {}
        raw = body.get('space_id') if isinstance(body, dict) else None
    if raw is None:
        raw = request.form.get('space_id')
    space_id = _int_or_none(raw)
    if space_id is None:
        return None
    allowed = queries.spaces_for_user(cursor, ctx, include_guest=False)
    return space_id if space_id in allowed else None


def _slugify(value):
    """Слаг из названия: латиница/цифры/дефис. Кириллица транслитерируется.

    Полноценный порт utils/text.ts (транслитерация + 110 групп алиасов) придёт
    на этапе поиска; здесь нужен только предсказуемый ключ для URL.
    """
    table = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
        'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    }
    out = []
    for char in str(value or '').strip().lower():
        if char in table:
            out.append(table[char])
        elif char.isalnum():
            out.append(char)
        elif out and out[-1] != '-':
            out.append('-')
    return ''.join(out).strip('-')[:200] or 'section'


PERMISSION_FIELDS = ('can_read', 'can_create', 'can_edit',
                     'can_delete', 'can_publish', 'can_approve')

# Отказ по границе отдела — своими словами на каждый субъект. Общее «адресат из
# другого отдела» на роли звучало бы неправдой: у роли отдела нет вовсе, и
# человек искал бы, какой именно отдел не тот.
# Отказы про САМ РАЗДЕЛ (у адресата свои — _SUBJECT_SCOPE_ERRORS ниже). Тексты
# разные потому, что и починка разная: чужой отдел человек не исправит никак, а
# «раздел выше вашей должности» объясняет, к кому идти.
_NOT_A_GRANTOR = ("Доступ раздают супервайзер и выше", "WIKI_FORBIDDEN")
_FOREIGN_DEPARTMENT = ("Раздел относится к другому отделу", "WIKI_DEPARTMENT_SCOPE")
_SECTION_ABOVE_GRANTOR = (
    "Этот раздел стоит выше вашей должности — доступ в нём раздаёт вышестоящий "
    "руководитель",
    "WIKI_SECTION_ABOVE_GRANTOR",
)

# Отказ на структурных дверях. Форма та же, что у декоратора wiki_route
# (wiki/routes.py): фронт уже умеет читать required и показывает по нему,
# какого именно права не хватило. Заводить второй формат ради тех же дверей
# значило бы, что клиент однажды разберёт один и не разберёт другой.
_STRUCTURE_REQUIRED = {
    "error": "Недостаточно прав: нужно «%s»" % CAPABILITY_TITLES['can_manage_structure'],
    "code": "WIKI_FORBIDDEN",
    "required": "can_manage_structure",
}

# Отказ тому, у кого право на структуру ЕСТЬ, но не здесь: он вышел за
# выданную ему ветку. Текст называет причину прямо — «нет прав» на своей же
# ветке отправило бы человека искать несуществующую ошибку.
_OUTSIDE_MY_BRANCH = (
    "Этот раздел вне вашей ветки — дерево тут строит вышестоящий руководитель",
    "WIKI_SECTION_OUTSIDE_BRANCH",
)

_SUBJECT_SCOPE_ERRORS = {
    'user': 'Этот сотрудник из другого отдела',
    'group': 'Эта группа из другого отдела',
    'direction': 'Это направление из другого отдела',
    'department': 'Это чужой отдел',
    'department_head': 'Это глава чужого отдела',
    'otp_role': 'Правило на должность действует во всей компании — его выписывает директор',
    'wiki_role': 'Правило на роль в вики действует во всей компании — его выписывает директор',
}


def register(bp, wiki_route, db, log_ip):
    """Вешает обработчики на Blueprint. bp и wiki_route приходят из routes.py."""

    def _may_manage_space(ctx):
        """Заводить пространства и двигать их границу вправе только супер-админ.

        Решение владельца: пространство — это граница между отделами (и целыми
        клиентами), а не элемент структуры. Глава отдела строит РАЗДЕЛЫ внутри
        выданного ему пространства, как и раньше, но открыть пространство ещё
        одному отделу или завести новое не может: это выдача доступа к вике
        целиком, а её раздаёт один человек.

        Роль вики с can_manage_access приравнена к супер-админу здесь так же,
        как и во всём остальном разделе, — её назначают руками именно за этим.
        """
        return bool(wiki_access.normalize_role(ctx['otp_role']) == 'super_admin'
                    or (ctx['wiki_roles'] and ctx['capabilities'].get('can_manage_access')))

    def _may_manage_section_here(cursor, ctx, *, space_id):
        """Вправе ли человек трогать РАЗДЕЛ в этом пространстве.

        Способность can_manage_structure проверяет сам роут; здесь остаётся
        только граница пространства: роль вики, которой выдали правку структуры
        без мастер-ключа, не должна строить дерево в пространстве, которого ей
        не выдавали.

        Отдельной ветки «глава отдела строит у себя» тут намеренно НЕТ: с
        21.08.2026 руководитель не трогает структуру вовсе (см.
        access.capabilities_from_otp_role — у роли 'admin' способности больше
        нет). Написанная «на будущее», такая ветка была бы мёртвым правилом,
        которое оживёт не тогда, когда надо.
        """
        if _may_manage_space(ctx):
            return True
        departments = _grant_departments(ctx)
        if departments is None:
            return True
        return structure.space_open_to(cursor, space_id, departments)

    def _manage_sections(cursor, ctx):
        """Где человек вправе строить дерево. None — без границы.

        Решение владельца 27.08.2026. До него дерево правил только носитель
        способности can_manage_structure — директор и назначенный руками
        администратор вики. Коммерческому директору выписали личное правило на
        его ветку со всеми шестью правами и «вместе с подразделами», и он всё
        равно не мог завести подраздел: шесть прав правила описывают
        СОДЕРЖИМОЕ, а способностью они не становятся по построению
        (wiki/access.py: capabilities_from_grants). Право строить дерево теперь
        живёт отдельным тумблером правила — «Может заводить подразделы».

        None означает «без границы» и возвращается ТОЛЬКО носителю способности:
        у него дверь и была открыта, и сужать её эта правка не должна. Всем
        остальным возвращается множество разделов — оно же и есть граница.

        Ветка «глава отдела строит у себя» тут по-прежнему НЕ появляется:
        решение владельца 21.08.2026 («руководитель не может добавлять разделы
        и подразделы») в силе, и должность 'admin' сама по себе не открывает
        ничего. Открывает выписанное правило — то есть чужое решение, а не
        собственная должность.
        """
        if ctx['capabilities'].get('can_manage_structure'):
            return None
        return queries.manage_section_ids(cursor, ctx, ctx['subjects'])

    def _grant_ceiling(ctx):
        """До какого уровня должности человек вправе открывать разделы.

        None — не вправе вовсе. Способность can_manage_access, ПРИШЕДШАЯ ОТ РОЛИ
        ВИКИ, поднимает потолок: администратора вики назначают руками ровно для
        этого. Проверка «роль вики И способность» осталась и после того, как
        должность 'admin' перестала раздавать мастер-ключ: у роли вики он
        по-прежнему есть, и там потолок обязан подниматься.
        """
        from_wiki_role = bool(ctx['wiki_roles']) and ctx['capabilities'].get('can_manage_access')
        return wiki_access.grant_ceiling(ctx['otp_role'], is_wiki_admin=bool(from_wiki_role))

    def _grant_departments(ctx):
        """Отделы, внутри которых человек вправе настраивать доступ.

        None — без границы (коммерческий директор и администратор вики).
        Решение владельца: супервайзер и руководитель работают только со своим
        отделом, иначе СВ продаж раздавал бы доступ к веткам СЗоВ.
        """
        if wiki_access.normalize_role(ctx['otp_role']) == 'super_admin':
            return None
        if bool(ctx['wiki_roles']) and ctx['capabilities'].get('can_manage_access'):
            return None
        own = set(ctx.get('headed_department_ids') or [])
        if ctx.get('department_id'):
            own.add(ctx['department_id'])
        return sorted(own)

    def _section_grant_refusal(cursor, ctx, section_id):
        """Почему человек не вправе трогать правила ИМЕННО ЭТОГО раздела.

        None — вправе. Иначе (текст, код) для ответа: отказы разные, и путать их
        нельзя. «Раздел относится к другому отделу», сказанное про раздел своего
        же отдела, отправило бы человека искать несуществующую ошибку в ветке.

        Три проверки:

          1. потолок должности — есть ли право раздавать вообще (GRANT_CEILING);
          2. граница отдела — та ли это ветка (свой отдел, а не соседний);
          3. ВЫСОТА РАЗДЕЛА — не выше ли он самого раздающего.

        Третья появилась 25.08.2026 по требованию владельца: дерево вики
        повторяет оргструктуру, поэтому раздел руководителя лежит в ТОМ ЖЕ
        отделе, что и раздел супервайзера, — граница отдела его пропускала, а
        потолок про раздел не знает вовсе (он отвечает «кому по чину», а не
        «где»). Супервайзер открывал руководительский раздел своим операторам
        одним правилом без порога. Как меряется высота — access.py, шапка
        may_manage_section_level.

        Раздел вне любой ветки отдела остаётся за директором: «свой отдел» для
        него не определён, и пускать туда руководителя нельзя.
        """
        if _grant_ceiling(ctx) is None:
            return _NOT_A_GRANTOR
        departments = _grant_departments(ctx)
        if departments is None:
            return None
        branch = structure.section_branch_department(cursor, section_id)
        if branch is None or branch not in departments:
            return _FOREIGN_DEPARTMENT
        if not wiki_access.may_manage_section_level(
                ctx['otp_role'], structure.section_role_levels(cursor).get(section_id)):
            return _SECTION_ABOVE_GRANTOR
        return None

    # ── Дерево структуры ─────────────────────────────────────────────────
    @wiki_route('/structure')
    def wiki_structure(cursor, ctx):
        """Пространства и разделы, доступные пользователю, плюс его права на них.

        Периметр здесь ЛИЧНЫЙ (master_key=False): по этому же ответу рисуется
        дерево разделов на вкладке «Статьи», а это витрина чтения. С мастер-ключом
        флаг accessible был бы истинным у всех разделов сразу, и управляющий
        структурой видел бы в дереве чужие отделы. Раздел вне периметра остаётся
        в ответе (он нужен вкладке «Структура»), но помечен accessible=false.
        """
        subjects = ctx['subjects']
        allowed = queries.allowed_section_ids(cursor, ctx, subjects, master_key=False)
        rules_by_section = queries.section_rules_for_user(cursor, allowed, subjects,
                                                          ctx['user_id'])
        can_manage = bool(ctx['capabilities'].get('can_manage_structure')
                          or ctx['capabilities'].get('can_manage_access'))

        # Счётчик статей в дереве — по тем же статьям, что человек реально
        # откроет. Общее число остаётся отдельным полем: вкладке «Структура»
        # нужен факт «в разделе 15 статей», даже если читателю видно три.
        readable = wiki_articles.visible_article_ids(cursor, ctx, subjects, allowed,
                                                     master_key=False)
        readable_counts = structure.article_counts_by_section(cursor, readable)

        spaces = structure.list_spaces(cursor, include_archived=can_manage)
        sections = structure.list_sections(cursor, include_archived=can_manage)
        # Кому виден публичный раздел: форме нужно проставить галочки, а списку —
        # показать, что «публичный» здесь не значит «всем».
        public_departments = structure.public_departments_by_section(
            cursor, [s['id'] for s in sections])

        # Право раздавать доступ: потолок должности и граница отдела. Считаем
        # один раз на запрос, а отдел ветки — по уже загруженному дереву, а не
        # рекурсивным запросом на каждый раздел.
        ceiling = _grant_ceiling(ctx)
        grant_departments = _grant_departments(ctx)
        # Высота разделов на лестнице должностей — одной картой на весь ответ, а
        # не запросом на строку: третья граница выдачи (см. _section_grant_refusal).
        # Раздающему без границы отдела она не нужна вовсе — у мастер-ключа сняты
        # все три, и считать дерево ради ответа «да» незачем.
        role_levels = {} if grant_departments is None else structure.section_role_levels(cursor)
        # Где человек вправе строить дерево. None — без границы (носитель
        # can_manage_structure). Множество — выданные ему ветки: по нему строка
        # получает свои can_add_subsection / can_edit_section, и фронт не считает
        # границу второй раз. Ровно та же причина, что у can_grant_access ниже:
        # второй расчёт на клиенте расходится всегда в сторону «кнопку показали,
        # а API ответил 403».
        manage = _manage_sections(cursor, ctx)
        by_id = {s['id']: s for s in sections}
        branch_of = {}

        def branch_department(section_id):
            if section_id in branch_of:
                return branch_of[section_id]
            chain, current = [], section_id
            found = None
            # Ограничитель тот же, что в SQL-версии: петель сервер не допускает,
            # но зациклиться здесь — подвесить весь ответ.
            for _ in range(50):
                node = by_id.get(current)
                if not node:
                    break
                chain.append(current)
                if node['department_id']:
                    found = node['department_id']
                    break
                current = node['parent_section_id']
                if not current:
                    break
            for ident in chain:
                branch_of[ident] = found
            return found

        visible = []
        for section in sections:
            # Может ли текущий человек раздавать доступ ИМЕННО ТУТ: решает
            # сервер, а не фронт. Иначе границы (потолок, отдел ветки и высота
            # самого раздела) считались бы дважды и однажды разошлись бы — а
            # расходится это всегда в сторону «показали кнопку, а API ответил 403».
            can_grant_here = bool(
                ceiling is not None
                and (grant_departments is None
                     or (branch_department(section['id']) in grant_departments
                         and wiki_access.may_manage_section_level(
                             ctx['otp_role'], role_levels.get(section['id']))))
            )
            # Право строить дерево ИМЕННО ТУТ. Заводить подразделы — внутри
            # выданной ветки и всего, что под ней; править — сами подразделы,
            # то есть разделы, чей РОДИТЕЛЬ в этой ветке. Якорь ветки человек
            # не правит: его открыл вышестоящий, и переписывать собственную
            # границу нельзя (та же граница стоит на PATCH /sections/<id>).
            #
            # Считаем по manage, а НЕ по can_manage: can_manage — это «структура
            # ИЛИ мастер-ключ», и носитель одного лишь can_manage_access видел
            # бы кнопки, которых сервер ему не даст (двери структуры спрашивают
            # can_manage_structure). Это расхождение было и до правки; здесь оно
            # закрывается тем, что признак строки и сама дверь считают одно и
            # то же одной функцией.
            can_add_here = manage is None or section['id'] in manage
            can_edit_here = manage is None or section['parent_section_id'] in manage
            # Раздел, которым человек УПРАВЛЯЕТ, остаётся в ответе, даже если он
            # его не читает. Иначе супервайзер не увидел бы во вкладке
            # «Структура» ни одной ветки своего отдела: правило на чтение ему
            # никто не выписывал, и раздавать операторам было бы негде.
            # Витрина статей такие разделы отсеивает по accessible=false —
            # тем же способом, что и чужие разделы у администратора.
            if (section['id'] not in allowed and not can_manage
                    and not can_grant_here and not can_add_here and not can_edit_here):
                continue
            permissions = wiki_access.resolve_article_permissions(
                capabilities=ctx['capabilities'],
                section_rules=rules_by_section.get(section['id'], []),
                otp_role=ctx['otp_role'],
                is_section_owner=(section['owner_user_id'] == ctx['user_id']),
            )
            section['permissions'] = wiki_access.permissions_only(permissions)
            section['why'] = permissions['_reason']
            section['accessible'] = section['id'] in allowed
            section['readable_count'] = readable_counts.get(section['id'], 0)
            # Может ли текущий человек раздавать доступ ИМЕННО ТУТ: решает
            # сервер, а не фронт. Иначе границы (потолок, отдел ветки и высота
            # самого раздела) считались бы дважды и однажды разошлись бы — а
            # расходится это всегда в сторону «показали кнопку, а API ответил 403».
            section['can_grant_access'] = can_grant_here
            section['can_add_subsection'] = can_add_here
            section['can_edit_section'] = can_edit_here
            section['public_department_ids'] = public_departments.get(section['id'], [])
            section['context_only'] = False
            visible.append(section)

        # ── Ветка обязана остаться веткой ────────────────────────────────
        #
        # Раздел, который человек не читает и не настраивает, из ответа выпадает.
        # Пока такой раздел стоял с краю, это было незаметно; но 25.08.2026
        # вместе с высотой раздела из ответа супервайзера впервые выпал раздел
        # ПОСЕРЕДИНЕ ветки — «Руководитель группы», — и дерево на экране сложилось
        # в неправду: клиент поднимает сироту в корень пространства
        # (structureTree.js), поэтому «Супервайзер» встал рядом с «Общим
        # сотрудником», а ветка «СЗоВ» осталась пустой строкой без единого
        # подраздела. Оргструктуры, которую это дерево обязано повторять, на
        # экране не осталось вовсе.
        #
        # Поэтому предков видимых разделов возвращаем КОНТЕКСТОМ: имя и место в
        # дереве, и больше ничего. Права у такой строки пустые, accessible=false,
        # can_grant_access=false — то есть ни одной новой двери она не открывает,
        # а витрина статей отсеивает её тем же accessible, что и прежде.
        # Прятать их «ради безопасности» смысла нет: имена разделов человек и так
        # видит в справочнике отделов, а вот сломанное дерево он видеть не должен.
        shown = {section['id'] for section in visible}
        for section in list(visible):
            parent = section['parent_section_id']
            # Ограничитель тот же, что и у подъёма за отделом ветки: петель
            # сервер не допускает, но зациклиться здесь — подвесить ответ.
            for _ in range(50):
                if not parent or parent in shown:
                    break
                node = by_id.get(parent)
                if not node:
                    break
                node['permissions'] = wiki_access.permissions_only({})
                node['why'] = 'показан для структуры дерева'
                node['accessible'] = False
                node['readable_count'] = 0
                node['can_grant_access'] = False
                node['can_add_subsection'] = False
                node['can_edit_section'] = False
                node['public_department_ids'] = public_departments.get(node['id'], [])
                node['context_only'] = True
                shown.add(node['id'])
                parent = node['parent_section_id']

        # Порядок восстанавливаем по исходному списку, а не дописыванием предков
        # в хвост: соседи по ветке рисуются в том порядке, в каком приехали, и
        # предок, приклеенный последним, встал бы в дереве ниже собственных детей.
        visible = [section for section in sections if section['id'] in shown]

        # Пространства режем по ГРАНИЦЕ, а не по can_manage: способность
        # управлять структурой есть и у главы отдела, а показывать ему
        # пространство другого клиента нельзя — даже пустым, даже в списке.
        # Пустое пространство остаётся в ответе (по нему ещё нет разделов, но
        # именно в него сейчас пойдут заводить первый), чужое — нет.
        own_spaces = set(queries.spaces_for_user(cursor, ctx))
        used_spaces = {s['space_id'] for s in visible}
        return jsonify({
            "spaces": [sp for sp in spaces
                       if sp['id'] in own_spaces or sp['id'] in used_spaces],
            "sections": visible,
            "can_manage_structure": can_manage,
            # Строит ли человек дерево ХОТЯ БЫ ГДЕ-ТО. Нужно экрану, чтобы
            # решить, грузить ли справочник отделов для формы раздела: сама
            # форма открывается со строки, а справочник — один на экран.
            "can_manage_some_sections": manage is None or bool(manage),
            "can_manage_access": bool(ctx['capabilities'].get('can_manage_access')),
            # Заводить пространства и двигать их границу вправе только
            # супер-админ — конструктор спрашивает об этом сервер, а не считает
            # по роли сам.
            "can_manage_spaces": _may_manage_space(ctx),
        })

    # ── Пространства ─────────────────────────────────────────────────────
    def _feature_patch(raw):
        """Тумблеры из тела запроса: только известные ключи, только булевы.

        Храним ЛОЖНЫЕ значения вместе с истинными, а не «только выключенные»:
        разреженный объект пришлось бы дочитывать умолчанием при каждой записи,
        и первый же забытый ключ прочитался бы как «включено» после того, как
        человек его выключил.
        """
        if not isinstance(raw, dict):
            return None
        return {key: bool(raw.get(key, True)) for key in wiki_schema.SPACE_FEATURES}

    @wiki_route('/spaces', methods=('GET', 'POST'), capability='can_manage_structure')
    def wiki_spaces(cursor, ctx):
        # Список ВСЕХ пространств, включая архивные и чужие, — это карта границ
        # между отделами и клиентами, а не справочник. Отдаём его тому же, кто
        # эти границы двигает: способности can_manage_structure для чтения было
        # мало — её носит и назначенный руками администратор вики, которому
        # соседнее пространство знать неоткуда. Гейт тот же, что у POST/PATCH,
        # и он же считает признак can_manage_spaces для интерфейса.
        if not _may_manage_space(ctx):
            return jsonify({
                "error": "Пространства настраивает супер-администратор",
                "code": "WIKI_SPACE_ADMIN_ONLY",
            }), 403

        if request.method == 'GET':
            return jsonify({"items": structure.list_spaces(cursor, include_archived=True)})

        data = _body()
        name = _clean(data.get('name'))
        if not name:
            return jsonify({"error": "Укажите название пространства"}), 400

        space_id = structure.create_space(
            cursor, name=name, code=_clean(data.get('code'), 80),
            description=_clean(data.get('description'), 2000),
            icon=_clean(data.get('icon'), 64),
            department_id=_int_or_none(data.get('department_id')),
            created_by=ctx['user_id'],
            features=_feature_patch(data.get('features')) or {},
        )
        if isinstance(data.get('department_ids'), list):
            structure.set_space_departments(cursor, space_id, data['department_ids'])
        queries.log_action(cursor, actor_id=ctx['user_id'], action='space.create',
                           entity_type='space', entity_id=space_id,
                           details={'name': name,
                                    'department_ids': data.get('department_ids') or []},
                           ip_address=log_ip())
        return jsonify({"id": space_id}), 201

    @wiki_route('/spaces/<int:space_id>', methods=('PATCH', 'DELETE'),
                capability='can_manage_structure')
    def wiki_space_item(cursor, ctx, space_id):
        cursor.execute('SELECT name FROM wiki_spaces WHERE id = %s', (space_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Пространство не найдено"}), 404
        if not _may_manage_space(ctx):
            return jsonify({
                "error": "Пространства настраивает супер-администратор",
                "code": "WIKI_SPACE_ADMIN_ONLY",
            }), 403

        if request.method == 'DELETE':
            # Архивируем, а не удаляем: за пространством стоят разделы и статьи,
            # а физическое удаление сняло бы их каскадом без возможности вернуть.
            structure.update_space(cursor, space_id, {'status': 'archived'})
            queries.log_action(cursor, actor_id=ctx['user_id'], action='space.archive',
                               entity_type='space', entity_id=space_id,
                               details={'name': row[0]}, ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()
        fields = {}
        # Длины — по колонкам таблицы (wiki/schema.py): description TEXT,
        # icon VARCHAR(64), code VARCHAR(80). Общие «255» на все четыре поля
        # означали, что значок длиннее шестидесяти четырёх символов доезжал до
        # базы и валил запрос ошибкой драйвера вместо понятного отказа.
        limits = {'description': 2000, 'icon': 64, 'code': 80}
        for key in ('name', 'description', 'icon', 'code'):
            if key in data:
                fields[key] = _clean(data[key], limits.get(key, 255))
        if 'department_id' in data:
            fields['department_id'] = _int_or_none(data['department_id'])
        if data.get('status') in ('active', 'archived'):
            fields['status'] = data['status']
        if 'position' in data:
            fields['position'] = _int_or_none(data['position']) or 0
        features = _feature_patch(data.get('features'))
        if features is not None:
            fields['features'] = features

        # Список отделов — не поле таблицы, поэтому «нечего обновлять» считается
        # с его учётом: запрос, который несёт ТОЛЬКО отделы, обязан сработать.
        touched_departments = isinstance(data.get('department_ids'), list)
        if not fields and not touched_departments:
            return jsonify({"error": "Нечего обновлять"}), 400
        if fields and not structure.update_space(cursor, space_id, fields):
            return jsonify({"error": "Нечего обновлять"}), 400
        if touched_departments:
            structure.set_space_departments(cursor, space_id, data['department_ids'])
            fields['department_ids'] = sorted({int(x) for x in data['department_ids'] if x})
        queries.log_action(cursor, actor_id=ctx['user_id'], action='space.update',
                           entity_type='space', entity_id=space_id,
                           details=fields, ip_address=log_ip())
        return jsonify({"status": "ok"})

    # ── Разделы ──────────────────────────────────────────────────────────
    # Способность СНЯТА с декоратора и проверяется внутри. Причина: дверь
    # перестала быть одной — сверх носителя can_manage_structure сюда теперь
    # входит тот, кому выписали ветку тумблером «Может заводить подразделы»
    # (_manage_sections). Декоратор про раздел не знает вовсе и отказал бы
    # раньше, чем стало бы понятно, КУДА человек кладёт подраздел.
    @wiki_route('/sections', methods=('GET', 'POST'))
    def wiki_sections(cursor, ctx):
        manage = _manage_sections(cursor, ctx)
        if request.method == 'GET':
            # Плоский список всех разделов пространства — по-прежнему только
            # управляющему структурой: это справочник целиком, а не своя ветка.
            if manage is not None:
                return jsonify(_STRUCTURE_REQUIRED), 403
            return jsonify({"items": structure.list_sections(
                cursor, space_id=_int_or_none(request.args.get('space_id')),
                include_archived=True)})

        # Тому, кто дерево не строит НИГДЕ, отказываем первым делом — до
        # разбора тела и до похода за пространством. Иначе оператор получал бы
        # «Пространство не найдено» вместо «нужно управление структурой»:
        # отказ не про то, отправляющий искать несуществующую опечатку в форме.
        if manage is not None and not manage:
            return jsonify(_STRUCTURE_REQUIRED), 403

        data = _body()
        name = _clean(data.get('name'))
        space_id = _int_or_none(data.get('space_id'))
        if not name or not space_id:
            return jsonify({"error": "Укажите пространство и название раздела"}), 400

        cursor.execute("SELECT status FROM wiki_spaces WHERE id = %s", (space_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Пространство не найдено"}), 404
        if row[0] != 'active':
            return jsonify({
                "error": "Пространство в архиве — сначала верните его из архива",
                "code": "WIKI_SPACE_ARCHIVED",
            }), 400
        if not _may_manage_section_here(cursor, ctx, space_id=space_id):
            return jsonify({
                "error": "Это пространство выдано другим отделам",
                "code": "WIKI_DEPARTMENT_SCOPE",
            }), 403

        scope = data.get('visibility_scope')
        if scope not in ('public', 'restricted'):
            scope = 'restricted'

        # Отдел ветки. Из него выводится section_kind, и он же подставляется в
        # правила доступа подразделов: ветка «СЗоВ» делает раздел «Оператор»
        # внутри неё правилом «отдел СЗоВ + порог должности», а не голой ролью,
        # которая пробила бы границу отдела.
        parent_section_id = _int_or_none(data.get('parent_section_id'))
        department_id = _int_or_none(data.get('department_id'))
        owner_user_id = _int_or_none(data.get('owner_user_id'))

        # ── Тот, кому выдана ВЕТКА, а не способность ─────────────────────
        if manage is not None:
            # Раздел верхнего уровня — это новая ветка пространства, а не
            # подраздел. Право называется «может заводить подразделы» и
            # читается буквально: без родителя заводить нечего.
            if not parent_section_id or parent_section_id not in manage:
                return jsonify({"error": _OUTSIDE_MY_BRANCH[0],
                                "code": _OUTSIDE_MY_BRANCH[1]}), 403
            # Родитель обязан лежать в том же пространстве, куда кладут
            # подраздел: иначе раздел уехал бы в пространство, которого ему
            # никто не выдавал, оставшись «внутри» своей ветки по дереву.
            if structure.section_exists(cursor, parent_section_id) != space_id:
                return jsonify({
                    "error": "Родительский раздел лежит в другом пространстве",
                    "code": "WIKI_SECTION_PARENT_SPACE",
                }), 400
            # Публичный раздел виден мимо ветки — это выдача доступа всей
            # компании, а не устройство своего дерева. Владельца раздела
            # назначает тот же, кто раздаёт доступ.
            scope = 'restricted'
            owner_user_id = None
            # Ветку можно завести только на отдел, которому выдано САМО
            # пространство. Иначе внутри своей ветки заводится ветка чужого
            # клиента, и по ней начинают считаться права его сотрудников.
            if department_id and not structure.space_open_to(
                    cursor, space_id, [department_id]):
                return jsonify({
                    "error": "Этому отделу пространство не выдано",
                    "code": "WIKI_DEPARTMENT_SCOPE",
                }), 403

        taken = structure.department_branch_taken(
            cursor, space_id=space_id, parent_section_id=parent_section_id,
            department_id=department_id)
        if taken:
            return jsonify({
                "error": "Ветка этого отдела здесь уже есть — «%s»" % taken,
                "code": "WIKI_DEPARTMENT_BRANCH_TAKEN",
            }), 400

        section_id = structure.create_section(
            cursor, space_id=space_id,
            parent_section_id=parent_section_id,
            name=name, department_id=department_id,
            # Слаг обязан быть уникален в пространстве, и занять его мог даже
            # архивный раздел. Дописываем номер, как это делают статьи, — иначе
            # повтор названия падал в 500.
            slug=structure.free_section_slug(
                cursor, space_id, _clean(data.get('slug'), 200) or _slugify(name)),
            description=_clean(data.get('description'), 2000),
            icon=_clean(data.get('icon'), 64), visibility_scope=scope,
            owner_user_id=owner_user_id,
            created_by=ctx['user_id'],
        )
        if scope == 'public' and isinstance(data.get('public_department_ids'), list):
            structure.set_public_departments(cursor, section_id, data['public_department_ids'])
        queries.log_action(cursor, actor_id=ctx['user_id'], action='section.create',
                           entity_type='section', entity_id=section_id,
                           details={'name': name, 'space_id': space_id,
                                    'visibility_scope': scope,
                                    'department_id': department_id},
                           ip_address=log_ip())
        return jsonify({"id": section_id}), 201

    # Способность снята с декоратора по той же причине, что и у POST /sections:
    # дверь открыта и носителю can_manage_structure, и тому, кому выдали ветку.
    # Кому именно — зависит от раздела, а декоратор про раздел не знает.
    @wiki_route('/sections/<int:section_id>', methods=('PATCH', 'DELETE'))
    def wiki_section_item(cursor, ctx, section_id):
        # Тому, кто дерево не строит НИГДЕ, отказываем до похода за разделом:
        # иначе оператор получал бы «Раздел не найден» вместо «нужно управление
        # структурой» — отказ не про то.
        manage = _manage_sections(cursor, ctx)
        if manage is not None and not manage:
            return jsonify(_STRUCTURE_REQUIRED), 403

        cursor.execute(
            """
            SELECT s.name, sp.department_id, s.space_id, s.parent_section_id,
                   s.department_id, s.visibility_scope, s.status, s.owner_user_id
              FROM wiki_sections s JOIN wiki_spaces sp ON sp.id = s.space_id
             WHERE s.id = %s
            """,
            (section_id,),
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Раздел не найден"}), 404
        if not _may_manage_section_here(cursor, ctx, space_id=row[2]):
            return jsonify({
                "error": "Раздел лежит в пространстве, выданном другим отделам",
                "code": "WIKI_DEPARTMENT_SCOPE",
            }), 403
        section_exists_space_id = row[2]
        section_parent_id = row[3]
        section_department_id = row[4]
        section_scope = row[5]
        section_status = row[6]
        section_owner_id = row[7]

        # ── Граница того, кому выдана ВЕТКА, а не способность ────────────
        #
        # Править он вправе ПОДРАЗДЕЛЫ — то есть разделы, лежащие внутри
        # выданного ему. Сам выданный раздел не его: тумблер называется «может
        # заводить подразделы», а раздел-якорь ему открыл вышестоящий, и
        # переименовать или унести его значило бы переписать собственную
        # границу. Поэтому проверяется РОДИТЕЛЬ, а не сам раздел, — множество
        # manage уже включает и якорь, и всё поддерево под ним.
        if manage is not None and section_parent_id not in manage:
            return jsonify({"error": _OUTSIDE_MY_BRANCH[0],
                            "code": _OUTSIDE_MY_BRANCH[1]}), 403

        if request.method == 'DELETE':
            # Архив уносит раздел вместе со статьями внутри, и решение владельца
            # 27.08.2026 — «заводить и править»: снос ветки остаётся у того, кто
            # её выдал. Отказ отдельным текстом, а не общим «нет прав»: человек
            # только что переименовал этот же раздел, и «нет прав» выглядело бы
            # поломкой.
            if manage is not None:
                return jsonify({
                    "error": "Убрать раздел в архив может вышестоящий руководитель",
                    "code": "WIKI_SECTION_ARCHIVE_FORBIDDEN",
                }), 403
            structure.update_section(cursor, section_id, {'status': 'archived'})
            queries.log_action(cursor, actor_id=ctx['user_id'], action='section.archive',
                               entity_type='section', entity_id=section_id,
                               details={'name': row[0]}, ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()

        # ── Что именно вправе поменять держатель ветки ───────────────────
        #
        # Название, описание, значок, порядок и отдел ветки — да. Публичность,
        # архив, владелец и родитель — нет: первое открывает раздел мимо ветки,
        # второе уносит статьи, третье и четвёртое двигают сам раздел по дереву.
        #
        # Отказ идёт СРАВНЕНИЕМ с текущим значением, а не по наличию ключа:
        # форма шлёт весь набор полей при каждом сохранении, и запрет «по ключу»
        # отказывал бы в обычном переименовании. Ровно так уже ломался переезд
        # между пространствами — см. блок ниже.
        if manage is not None:
            requested_scope = data.get('visibility_scope')
            if requested_scope in ('public', 'restricted') and requested_scope != section_scope:
                return jsonify({
                    "error": "Публичным раздел делает вышестоящий руководитель",
                    "code": "WIKI_SECTION_PUBLIC_FORBIDDEN",
                }), 403
            if data.get('status') in ('active', 'archived') and data['status'] != section_status:
                return jsonify({
                    "error": "Убрать раздел в архив или вернуть из архива может "
                             "вышестоящий руководитель",
                    "code": "WIKI_SECTION_ARCHIVE_FORBIDDEN",
                }), 403
            if 'owner_user_id' in data and _int_or_none(
                    data['owner_user_id']) != section_owner_id:
                return jsonify({
                    "error": "Владельца раздела назначает вышестоящий руководитель",
                    "code": "WIKI_SECTION_OWNER_FORBIDDEN",
                }), 403
            if 'parent_section_id' in data and _int_or_none(
                    data['parent_section_id']) != section_parent_id:
                return jsonify({
                    "error": "Перенести раздел в другую ветку может вышестоящий "
                             "руководитель",
                    "code": "WIKI_SECTION_MOVE_FORBIDDEN",
                }), 403
            if ('department_id' in data and _int_or_none(data['department_id'])
                    and not structure.space_open_to(
                        cursor, section_exists_space_id,
                        [_int_or_none(data['department_id'])])):
                return jsonify({
                    "error": "Этому отделу пространство не выдано",
                    "code": "WIKI_DEPARTMENT_SCOPE",
                }), 403
            # Список отделов публичного раздела — часть публичности, и меняет
            # его тот же, кто её включает. Ключ просто не доезжает до
            # set_public_departments ниже.
            data.pop('public_department_ids', None)

        # ── Переезд в другое пространство ────────────────────────────────
        # Форма шлёт space_id при КАЖДОМ сохранении, поэтому переездом считается
        # только отличие от текущего: иначе правка названия всякий раз тащила бы
        # раздел в корень. Раньше ключ не читался вовсе — раздел оставался на
        # месте, а ответ был «Раздел обновлён»: молчаливый отказ, худший из
        # возможных, потому что человек уходит уверенным, что перенёс.
        target_space_id = section_exists_space_id
        moving = False
        if 'space_id' in data:
            requested = _int_or_none(data['space_id'])
            if requested and requested != section_exists_space_id:
                cursor.execute('SELECT department_id, status FROM wiki_spaces WHERE id = %s',
                               (requested,))
                target = cursor.fetchone()
                if not target:
                    return jsonify({"error": "Пространство не найдено"}), 404
                # Переезд МЕЖДУ пространствами — только супер-админу: это вынос
                # содержимого за границу, ради которой пространства и заведены.
                # Главе отдела здесь отказываем, даже если оба пространства
                # открыты его отделу: перенос статей чужому клиенту не должен
                # зависеть от того, кому ещё выдано пространство.
                if not _may_manage_space(ctx):
                    return jsonify({
                        "error": "Перенос между пространствами выполняет супер-администратор",
                        "code": "WIKI_SPACE_ADMIN_ONLY",
                    }), 403
                if target[1] != 'active':
                    return jsonify({
                        "error": "Пространство в архиве — сначала верните его из архива",
                        "code": "WIKI_SPACE_ARCHIVED",
                    }), 400
                target_space_id = requested
                moving = True

        fields = {}
        for key in ('name', 'description', 'icon'):
            if key in data:
                fields[key] = _clean(data[key], 2000 if key == 'description' else 255)
        if 'slug' in data:
            # Слаг уникален в пределах пространства — значит, свободный ищем в
            # ЦЕЛЕВОМ, а не в том, из которого раздел уезжает.
            fields['slug'] = structure.free_section_slug(
                cursor, target_space_id, _clean(data['slug'], 200) or _slugify(row[0]),
                exclude_id=section_id)
        if data.get('visibility_scope') in ('public', 'restricted'):
            fields['visibility_scope'] = data['visibility_scope']
        if data.get('status') in ('active', 'archived'):
            fields['status'] = data['status']
        if 'owner_user_id' in data:
            fields['owner_user_id'] = _int_or_none(data['owner_user_id'])
        if 'position' in data:
            fields['position'] = _int_or_none(data['position']) or 0
        if 'parent_section_id' in data:
            parent = _int_or_none(data['parent_section_id'])
            if structure.section_would_cycle(cursor, section_id, parent):
                return jsonify({
                    "error": "Раздел нельзя вложить в самого себя или в свой подраздел",
                    "code": "WIKI_SECTION_CYCLE",
                }), 400
            fields['parent_section_id'] = parent

        if moving:
            # Родитель обязан жить в целевом пространстве. Пустой ключ — переезд
            # в корень: это норма, а не ошибка, потому что дерева-источника в
            # новом пространстве нет.
            new_parent_id = fields.pop('parent_section_id', None)
            if new_parent_id and structure.section_exists(
                    cursor, new_parent_id) != target_space_id:
                return jsonify({
                    "error": "Родительский раздел лежит в другом пространстве",
                    "code": "WIKI_SECTION_PARENT_SPACE",
                }), 400
        else:
            new_parent_id = fields.get('parent_section_id', section_parent_id)

        # Отдел ветки: вид раздела выводим из него же, вторым полем не берём —
        # иначе пара «kind=department, department_id=NULL» проскочит мимо
        # уникального индекса и ветка перестанет быть веткой.
        new_department_id = (_int_or_none(data['department_id'])
                             if 'department_id' in data else section_department_id)
        if 'department_id' in data:
            fields['department_id'] = new_department_id
            fields['section_kind'] = structure.section_kind_of(new_department_id)
        # Проверяем и при переезде без смены отдела: uq_wiki_section_department
        # висит на (пространство, родитель, отдел), и ветка «ОП» могла уже быть
        # в целевом пространстве. Хватает проверки одного корня переезда —
        # у остальных разделов поддерева родитель едет вместе с ними, а значит
        # соседей за пределами поддерева у них не появляется.
        if 'department_id' in data or moving:
            taken = structure.department_branch_taken(
                cursor, space_id=target_space_id, parent_section_id=new_parent_id,
                department_id=new_department_id, exclude_id=section_id)
            if taken:
                return jsonify({
                    "error": "Ветка этого отдела здесь уже есть — «%s»" % taken,
                    "code": "WIKI_DEPARTMENT_BRANCH_TAKEN",
                }), 400

        moved = 0
        if moving:
            moved = structure.move_section_to_space(
                cursor, section_id, space_id=target_space_id,
                parent_section_id=new_parent_id)

        # Кому виден публичный раздел. Список чистится, когда раздел перестаёт
        # быть публичным: иначе он тихо переживёт выключение тумблера и
        # всплывёт при повторном включении — с отделами, которых уже никто
        # не выбирал.
        public_changed = False
        if fields.get('visibility_scope') == 'restricted':
            structure.set_public_departments(cursor, section_id, [])
            public_changed = True
        elif isinstance(data.get('public_department_ids'), list):
            structure.set_public_departments(cursor, section_id,
                                             data['public_department_ids'])
            public_changed = True

        # Переезд — сам по себе изменение: без второго условия перенос без
        # правки полей отвечал бы «Нечего обновлять» на выполненную работу.
        if (not structure.update_section(cursor, section_id, fields)
                and not moved and not public_changed):
            return jsonify({"error": "Нечего обновлять"}), 400
        if moved:
            queries.log_action(cursor, actor_id=ctx['user_id'], action='section.move',
                               entity_type='section', entity_id=section_id,
                               details={'name': row[0],
                                        'from_space_id': section_exists_space_id,
                                        'to_space_id': target_space_id,
                                        'parent_section_id': new_parent_id,
                                        'sections_moved': moved},
                               ip_address=log_ip())
        if fields:
            queries.log_action(cursor, actor_id=ctx['user_id'], action='section.update',
                               entity_type='section', entity_id=section_id,
                               details=fields, ip_address=log_ip())
        return jsonify({"status": "ok", "space_id": target_space_id,
                        "sections_moved": moved})

    # ── Правила доступа ──────────────────────────────────────────────────
    # Гейт не одной способностью, а любой из двух: справочник нужен и форме
    # раздела («отдел ветки» выбирается из него — её открывает управляющий
    # структурой), и форме правила, которую открывает раздающий доступ.
    # Роль вики может нести can_manage_access без can_manage_structure, поэтому
    # проверка идёт «или», а не сменой одной способности на другую. Отдаются
    # только названия отделов, направлений, групп и ролей вики; само
    # выписывание правил по-прежнему требует can_manage_access.
    @wiki_route('/access/subjects')
    def wiki_access_subjects(cursor, ctx):
        """Справочники для выбора субъекта правила и отдела ветки."""
        caps = ctx['capabilities']
        # Третий вход — право раздавать доступ: супервайзеру справочник нужен
        # для точечных правил (группа, направление), а способностей can_manage_*
        # у него нет. Без этого форма открывалась с пустыми списками.
        #
        # Четвёртый — держатель ветки: справочник отделов нужен форме раздела
        # («Отдел ветки»), а способности управлять структурой у него нет.
        # Проверка стоит последней и стоит запроса, поэтому считается только
        # когда первые три не сработали.
        if not (caps.get('can_manage_structure') or caps.get('can_manage_access')
                or _grant_ceiling(ctx) is not None
                or _manage_sections(cursor, ctx)):
            return jsonify({"error": "Недостаточно прав для этого действия",
                            "code": "WIKI_FORBIDDEN"}), 403
        # Справочник сужен той же границей, что и проверка на записи: свой
        # отдел, свои группы, свои направления. Роль в системе и роль вики
        # раздающему с границей не предлагаются вовсе — они адресуют людей по
        # всей компании, мимо отдела (access.may_grant_to_subject).
        departments = _grant_departments(ctx)

        # ГРАНИЦА ПРОСТРАНСТВА — вторая и независимая от границы отдела.
        #
        # Без неё в «Таксопарках» предлагался отдел «Тез КЦ»: справочник знал
        # только «чей это человек» и ничего не знал про «чьё это пространство».
        # Правило на чужой отдел всё равно ничего не открыло бы (до статей не
        # пустит _SPACE_GATE_SQL), но чужая оргструктура показывалась целиком —
        # а «Таксопарки» и «Тез» это разные компании.
        #
        # scope=all — единственное исключение, и оно с причиной: КОНСТРУКТОР
        # пространств раздаёт пространство отделам, и список всех отделов ему
        # нужен по делу. Гейт тот же, что у самого конструктора: сузить его до
        # выбранного пространства значило бы запереть дверь ключом, лежащим
        # внутри — новое пространство некому было бы выдать.
        space_departments = None
        if request.args.get('scope') == 'all':
            if not _may_manage_space(ctx):
                return jsonify({"error": "Полный справочник отделов доступен "
                                         "тому, кто настраивает пространства",
                                "code": "WIKI_FORBIDDEN"}), 403
        else:
            space_id, space_error = request_space(cursor, ctx)
            if space_error:
                return space_error
            space_departments = structure.space_department_ids(cursor, space_id)

        catalog = structure.subject_catalog(cursor, department_ids=departments,
                                            space_department_ids=space_departments)
        catalog['otp_role'] = [] if departments is not None else [
            {'id': code, 'name': label} for code, label in (
                ('super_admin', 'Супер-администратор'), ('admin', 'Администратор'),
                ('sv', 'Супервайзер'), ('supervisor', 'Супервайзер (устар.)'),
                ('trainer', 'Тренер'), ('operator', 'Оператор'), ('trainee', 'Стажёр'),
            )
        ]
        # Границу отдаём вместе со справочником: по ней форма понимает, что
        # список сужен намеренно, а не «справочник не догрузился».
        catalog['grant_departments'] = departments
        return jsonify(catalog)

    # Гейт не «can_manage_access», а лестница: право раздавать доступ само
    # ограничено должностью раздающего (wiki_access.GRANT_CEILING) и его
    # отделом. До этого выдача была всё-или-ничего: у кого мастер-ключ — тот
    # правил любой раздел, у кого нет — не видел вкладки вовсе.
    @wiki_route('/access/section-rules', methods=('GET', 'POST'))
    def wiki_section_rules(cursor, ctx):
        ceiling = _grant_ceiling(ctx)
        if ceiling is None:
            return jsonify({"error": "Доступ раздают супервайзер и выше",
                            "code": "WIKI_FORBIDDEN"}), 403

        section_id = _int_or_none(
            request.args.get('section_id') if request.method == 'GET'
            else _body().get('section_id'))
        refusal = _section_grant_refusal(cursor, ctx, section_id) if section_id else None
        if refusal:
            return jsonify({"error": refusal[0], "code": refusal[1]}), 403

        if request.method == 'GET':
            return jsonify({
                "items": structure.list_section_rules(cursor, section_id=section_id),
                # Потолок едет вместе со списком: форме нужно погасить строки
                # должностей выше него, а второй запрос ради одного числа лишний.
                "grant_ceiling": ceiling,
                # Отделы, внутри которых человек вправе адресовать правило
                # (null — без границы). Форма по ним убирает субъекты, которые
                # уводят за пределы отдела: роль в системе и роль вики.
                "grant_departments": _grant_departments(ctx),
                # Третье измерение выдачи: КАКИЕ права он вправе поставить.
                # Потолок и границу форма получает отсюда же, а про это узнавала
                # бы только по 403 на заполненной форме — то есть молчаливым
                # отказом с обратной стороны стола.
                "grantable": [key for key in PERMISSION_FIELDS
                              if ctx['role_capabilities'].get(key)],
                # Тумблер «Может заводить подразделы» — не седьмое право на
                # содержимое, а передача управления деревом. Выдаёт его тот, у
                # кого управление деревом есть В ДОЛЖНОСТИ: право, полученное
                # самим раздающим из правила, дальше не передаётся — иначе одна
                # выдача сверху расползлась бы вниз по всей ветке сама собой.
                "grantable_structure": bool(
                    ctx['role_capabilities'].get('can_manage_structure')),
            })

        data = _body()
        subject_type = data.get('subject_type')
        if not section_id or subject_type not in SUBJECT_TYPES:
            return jsonify({"error": "Укажите раздел и субъект правила"}), 400
        if structure.section_exists(cursor, section_id) is None:
            return jsonify({"error": "Раздел не найден"}), 404

        subject_id, subject_role = None, None
        if subject_type == 'otp_role':
            subject_role = str(data.get('subject_role') or '').strip()
            if subject_role not in wiki_access.ROLE_LEVELS and subject_role != 'supervisor':
                return jsonify({"error": "Неизвестная роль"}), 400
        else:
            subject_id = _int_or_none(data.get('subject_id'))
            if not subject_id:
                return jsonify({"error": "Не выбран субъект"}), 400

        # Уровень должности — второе измерение правила: «не ниже супервайзера».
        # Пустое значение (ничего не выбрано) означает «без ограничения»,
        # поэтому 0 и None здесь равнозначны и оба кладутся как NULL.
        min_role_level = _int_or_none(data.get('min_role_level'))
        if min_role_level is not None and min_role_level not in wiki_access.ROLE_LEVELS.values():
            return jsonify({"error": "Неизвестный уровень должности"}), 400

        # ── Потолок должности ────────────────────────────────────────
        # Роль адресата нужна отдельно от порога: у правила на конкретного
        # человека порог обычно пуст, и одна лишь проверка порога пропустила бы
        # «супервайзер выписывает правило на самого себя» — то есть выдачу себе
        # полного доступа к любому разделу своего отдела.
        target_role, subject_department = None, None
        if subject_type == 'user':
            # Роль и отдел одним запросом: отдел нужен проверке ниже, и второй
            # поход в users за тем же человеком был бы лишним.
            cursor.execute('SELECT role, department_id FROM users WHERE id = %s',
                           (subject_id,))
            target = cursor.fetchone()
            if not target:
                return jsonify({"error": "Сотрудник не найден"}), 404
            target_role, subject_department = target[0], target[1]
        else:
            subject_department = structure.subject_department(
                cursor, subject_type, subject_id)

        # ── Граница отдела для адресата ──────────────────────────────
        # Раздел уже проверен выше (_section_grant_refusal), но раздел — это
        # «где», а не «кому». Без этой проверки супервайзер на СВОЁМ разделе
        # выписывал правило чужому отделу, чужой группе или роли по всей
        # компании: потолок такое пропускает, потому что порог у них пуст и
        # весит как оператор.
        if not wiki_access.may_grant_to_subject(
                subject_type, grant_departments=_grant_departments(ctx),
                subject_department=subject_department):
            return jsonify({
                "error": _SUBJECT_SCOPE_ERRORS.get(
                    subject_type, "Этот адресат из другого отдела"),
                "code": "WIKI_DEPARTMENT_SCOPE",
            }), 403

        if not wiki_access.may_grant_with_ceiling(ceiling, min_role_level, target_role):
            return jsonify({
                "error": "Такой доступ выдаёт только вышестоящий руководитель",
                "code": "WIKI_GRANT_CEILING",
            }), 403

        permissions = {key: bool(data.get(key)) for key in PERMISSION_FIELDS}
        # Право без чтения бессмысленно: нельзя править то, чего не видишь.
        if any(permissions[k] for k in PERMISSION_FIELDS[1:]):
            permissions['can_read'] = True

        # ── Выписать можно только то, что умеешь сам ─────────────────
        #
        # До 21.08.2026 эта граница держалась случайно: право, выписанное сверх
        # способностей раздающего, всё равно гасло у адресата (wiki/access.py),
        # и проверять было нечего. Теперь выписанное право работает — значит
        # «супервайзер выдал оператору удаление, которого у самого супервайзера
        # нет» стало бы настоящей выдачей, причём мимо лестницы GRANT_CEILING.
        #
        # Сверяемся со способностями ДОЛЖНОСТИ, а не с итоговыми: право,
        # полученное самим раздающим из правила, дальше не передаётся. Иначе
        # одна выдача сверху делает человека раздающим то же право по всему
        # своему отделу — мимо лестницы GRANT_CEILING.
        #
        # Отказ называет право поимённо: молчаливо снять галочку и сохранить
        # правило урезанным — тот же класс отказа, от которого чинили сам
        # инцидент, только с обратной стороны стола. Форма о границе знает
        # заранее: набор доступных галочек едет в GET /access/section-rules
        # полем grantable.
        beyond = [key for key in PERMISSION_FIELDS
                  if permissions[key] and not ctx['role_capabilities'].get(key)]
        if beyond:
            return jsonify({
                "error": "Нельзя выдать право, которого нет у вас самих: %s" % ', '.join(
                    CAPABILITY_TITLES.get(key, key) for key in beyond),
                "code": "WIKI_GRANT_BEYOND_SELF",
                "required": beyond,
            }), 403

        # ── Право строить дерево внутри ветки ────────────────────────
        #
        # Проверка отдельная от блока beyond выше и стоит по той же причине, по
        # какой сам тумблер не стал седьмым правом: шесть прав — про содержимое
        # раздела, а этот — про устройство дерева, и сверяется он с
        # can_manage_structure, которого в PERMISSION_FIELDS нет вовсе.
        #
        # Сверяемся со способностями ДОЛЖНОСТИ: у коммерческого директора право
        # строить дерево приходит из правила, и передать его дальше он не
        # должен — иначе лестница выдачи разъезжается сама, без чьего-либо
        # решения.
        manage_subsections = bool(data.get('manage_subsections'))
        if manage_subsections and not ctx['role_capabilities'].get('can_manage_structure'):
            return jsonify({
                "error": "Право заводить подразделы выдаёт тот, кто управляет "
                         "структурой вики",
                "code": "WIKI_GRANT_BEYOND_SELF",
                "required": ['can_manage_structure'],
            }), 403

        rule_id = structure.upsert_section_rule(
            cursor, section_id=section_id, subject_type=subject_type,
            subject_id=subject_id, subject_role=subject_role,
            permissions=permissions,
            grant_subsections=bool(data.get('grant_subsections', True)),
            manage_subsections=manage_subsections,
            min_role_level=min_role_level,
            created_by=ctx['user_id'],
        )
        queries.log_action(cursor, actor_id=ctx['user_id'], action='rule.upsert',
                           entity_type='section', entity_id=section_id,
                           target_user_id=subject_id if subject_type == 'user' else None,
                           details={'rule_id': rule_id, 'subject_type': subject_type,
                                    'subject_id': subject_id, 'subject_role': subject_role,
                                    'min_role_level': min_role_level,
                                    'manage_subsections': manage_subsections,
                                    **permissions},
                           ip_address=log_ip())
        return jsonify({"id": rule_id}), 201

    @wiki_route('/access/section-rules/<int:rule_id>', methods=('DELETE',))
    def wiki_section_rule_item(cursor, ctx, rule_id):
        ceiling = _grant_ceiling(ctx)
        if ceiling is None:
            return jsonify({"error": "Доступ раздают супервайзер и выше",
                            "code": "WIKI_FORBIDDEN"}), 403

        # Читаем правило ДО удаления: иначе проверять границу уже не по чему,
        # а снятие чужого правила — такое же вмешательство, как выдача. Без
        # этого супервайзер отобрал бы доступ у руководителя своего отдела.
        # Заодно забираем субъект и права: журналу нужно записать, У КОГО и ЧТО
        # отобрали, а после DELETE спрашивать уже некого. Раньше в журнал шёл
        # только rule_id, и запись «Право отозвано» не говорила ничего.
        cursor.execute(
            'SELECT section_id, min_role_level, subject_type, subject_id,'
            ' subject_role, ' + ', '.join(PERMISSION_FIELDS)
            + ' FROM wiki_section_access_rules WHERE id = %s',
            (rule_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Правило не найдено"}), 404
        removed = {'rule_id': rule_id, 'subject_type': row[2], 'subject_id': row[3],
                   'subject_role': row[4], 'min_role_level': row[1]}
        removed.update(dict(zip(PERMISSION_FIELDS, row[5:])))
        refusal = _section_grant_refusal(cursor, ctx, row[0])
        if refusal:
            return jsonify({"error": refusal[0], "code": refusal[1]}), 403
        if not wiki_access.may_grant_with_ceiling(ceiling, row[1]):
            return jsonify({"error": "Это правило снимает только вышестоящий руководитель",
                            "code": "WIKI_GRANT_CEILING"}), 403
        # Та же граница, что и при выдаче: правило, адресованное чужому отделу
        # или роли по всей компании, снимает тот, кто вправе его выписать.
        # Иначе супервайзер закрывал бы соседям доступ, который открыл директор.
        if not wiki_access.may_grant_to_subject(
                row[2], grant_departments=_grant_departments(ctx),
                subject_department=structure.subject_department(cursor, row[2], row[3])):
            return jsonify({
                "error": "Это правило адресовано не вашему отделу",
                "code": "WIKI_DEPARTMENT_SCOPE",
            }), 403

        section_id = structure.delete_section_rule(cursor, rule_id)
        if section_id is None:
            return jsonify({"error": "Правило не найдено"}), 404
        queries.log_action(cursor, actor_id=ctx['user_id'], action='rule.delete',
                           entity_type='section', entity_id=section_id,
                           target_user_id=(removed['subject_id']
                                           if removed['subject_type'] == 'user' else None),
                           details=removed, ip_address=log_ip())
        return jsonify({"status": "deleted"})

    @wiki_route('/access/people')
    def wiki_access_people(cursor, ctx):
        """Сотрудники, которым текущий человек вправе выдать доступ.

        Список уже отфильтрован потолком и отделом, поэтому форма не может
        предложить того, кого сервер потом отвергнет: список и проверка
        считаются по одним и тем же правилам.
        """
        ceiling = _grant_ceiling(ctx)
        if ceiling is None:
            return jsonify({"error": "Доступ раздают супервайзер и выше",
                            "code": "WIKI_FORBIDDEN"}), 403
        # Та же вторая граница, что и у справочника субъектов: без неё список
        # «кому выдать доступ» в «Таксопарках» показывал 21 сотрудника «Тез КЦ»
        # — людей чужой компании, с именами и должностями.
        space_id, space_error = request_space(cursor, ctx)
        if space_error:
            return space_error
        return jsonify({"items": structure.grantable_people(
            cursor, max_role_level=ceiling,
            department_ids=_grant_departments(ctx),
            space_department_ids=structure.space_department_ids(cursor, space_id))})

    # ── «Почему этот человек это видит» ──────────────────────────────────
    @wiki_route('/access/effective', capability='can_manage_access')
    def wiki_access_effective(cursor, ctx):
        """Периметр указанного пользователя с объяснением по каждому разделу.

        Такого эндпоинта в оригинале нет, но при четырёх уровнях правил
        (раздел → потомки → статья → запрет) без него невозможно ответить на
        вопрос «почему Иванов видит этот регламент».

        Разделы считаются ЛИЧНЫМ периметром — ровно тем, что человек увидит в
        «Все статьи». Мастер-ключ сюда не подмешивается: иначе у носителя роли
        'admin' ответ был бы «видит все восемь разделов», хотя в его списке
        статей лежит один. Сам мастер-ключ виден в capabilities ответа.
        """
        target_id = _int_or_none(request.args.get('user_id'))
        if not target_id:
            return jsonify({"error": "Укажите user_id"}), 400

        target = queries.load_access_context(cursor, target_id)
        if not target:
            return jsonify({"error": "Пользователь не найден"}), 404

        subjects = wiki_access.collect_subjects(
            user_id=target['user_id'], otp_role=target['otp_role'],
            department_id=target['department_id'],
            headed_department_ids=target['headed_department_ids'],
            direction_id=target['direction_id'], group_ids=target['group_ids'],
            wiki_role_ids=[r.get('id') for r in target['wiki_roles']],
        )
        # Тем же расчётом, что и у самого себя: объяснение прав обязано
        # совпадать с правами. Считать способности здесь отдельно значило бы
        # завести второй источник истины — раньше он тут и стоял, и ответ
        # «почему он это видит» умалчивал о правах, выписанных правилами.
        queries.load_capabilities(cursor, target, subjects)
        allowed = queries.allowed_section_ids(cursor, target, subjects, master_key=False)
        rules_by_section = queries.section_rules_for_user(cursor, allowed, subjects,
                                                          target['user_id'])

        sections = []
        for section in structure.list_sections(cursor):
            if section['id'] not in allowed:
                continue
            permissions = wiki_access.resolve_article_permissions(
                capabilities=target['capabilities'],
                section_rules=rules_by_section.get(section['id'], []),
                otp_role=target['otp_role'],
                is_section_owner=(section['owner_user_id'] == target['user_id']),
            )
            sections.append({
                'id': section['id'], 'name': section['name'],
                'space_id': section['space_id'],
                'visibility_scope': section['visibility_scope'],
                'permissions': wiki_access.permissions_only(permissions),
                'why': permissions['_reason'],
            })

        return jsonify({
            "user_id": target['user_id'],
            "otp_role": target['otp_role'],
            "access_mode": target['access_mode'],
            "capabilities": target['capabilities'],
            # Отдельно — способности одной лишь должности: разница с итоговыми
            # и есть ответ «что человеку добавили правилами».
            "role_capabilities": target['role_capabilities'],
            "subjects": subjects,
            "sections": sections,
        })

    # ── Журнал ───────────────────────────────────────────────────────────
    #
    # Гейт стоит в ТЕЛЕ, а не параметром capability= на роуте: журнал открыт
    # «с должности СВ и выше» (решение владельца 25.08.2026), а параметр
    # проверяет способность, которой это право не выражается вовсе. Формула
    # одна — wiki_access.may_read_audit; фронт не выводит её у себя, а получает
    # готовый признак can_read_audit из /ping.
    @wiki_route('/audit')
    def wiki_audit(cursor, ctx):
        if not wiki_access.may_read_audit(
                ctx['otp_role'],
                is_wiki_admin=bool(ctx['wiki_roles'])
                and bool(ctx['capabilities'].get('can_manage_access'))):
            return jsonify({
                "error": "Журнал открыт супервайзерам и выше",
                "code": "WIKI_AUDIT_FORBIDDEN",
            }), 403

        limit = min(max(_int_or_none(request.args.get('limit')) or 100, 1), 500)
        offset = max(_int_or_none(request.args.get('offset')) or 0, 0)
        # Поиск от двух символов: по одной букве ILIKE перебирает всю таблицу
        # и всё равно возвращает почти всё.
        query = (request.args.get('q') or '').strip()[:120]
        # Пространство спрашивается так же, как у справочников: журнал у
        # каждого свой, и «покажи чужой» здесь не уточнение выборки, а доступ.
        # Отсюда и 404 на чужой id — тот же request_space, что у офисов.
        space_id, space_error = request_space(cursor, ctx)
        if space_error:
            return space_error
        filters = {
            'group': (request.args.get('group') or '').strip() or None,
            'query': query if len(query) >= 2 else None,
            'date_from': _day_or_none(request.args.get('from')),
            'date_to': _day_or_none(request.args.get('to')),
            'space_id': space_id,
        }

        payload = {"items": structure.list_audit(cursor, limit=limit, offset=offset,
                                                 **filters)}
        # Пересчитывать итоги на каждой догруженной странице незачем: фильтр
        # тот же, а COUNT по всей таблице — самая дорогая часть запроса.
        # Без общего числа «Показать ещё» не знает, когда закончиться, а чипы
        # не могут спрятать заведомо пустые группы.
        if offset == 0:
            payload["total"] = structure.count_audit(cursor, **filters)
            payload["counts"] = structure.audit_group_counts(cursor, **filters)
            # Записи вне пространств. В норме ноль, и тогда экран о них молчит.
            # Отдаём всегда: граница журнала строгая, и запись без пространства
            # не попала бы ни в один — молча потерянная запись хуже строки в
            # подвале (structure.count_audit_outside).
            payload["outside"] = structure.count_audit_outside(cursor, **filters)
        return jsonify(payload)

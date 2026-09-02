# -*- coding: utf-8 -*-
"""Раздел «Новость дня»: лестница адресатов, задержка кнопки и две двери.

Тесты чистые — ни базы, ни сети. Логика прав живёт в news/access.py именно
затем, чтобы её можно было проверять так; всё, что требует SQL, проверяется
чтением исходников (как это уже делают тесты колокола и вики).
"""

import ast
import os
import re
import unittest

from news import access as news_access
from news import schema as news_schema
from wiki import access as wiki_access

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as handle:
        return handle.read()


def _jsx_code_only(source):
    """JSX без блочных комментариев.

    Нужен тестам «этого в окне быть не должно»: объяснение, ПОЧЕМУ подписи
    больше нет, стоит прямо там же комментарием — и поиск по строке находит
    ровно его.
    """
    return re.sub(r'/\*[\s\S]*?\*/', '', source)


def _code_only(source):
    """Исходник без комментариев и docstring'ов.

    Иначе тесты «этого в модуле быть не должно» падают на СОБСТВЕННОМ
    объяснении: шапка news/routes.py прямым текстом рассказывает, почему в ней
    нет ни `wiki_enabled`, ни QR-подтверждения, — и поиск по строке находит
    ровно этот абзац.
    """
    without_blocks = re.sub(r'"""[\s\S]*?"""', '', source)
    return re.sub(r'(?m)#.*$', '', without_blocks)


class NewsLadderTests(unittest.TestCase):
    """«Опубликовать только тем, кто ниже него, но не выше» (владелец)."""

    def test_ceiling_is_the_wiki_ladder_and_not_a_second_one(self):
        """Лестница ОДНА на выдачу доступа и на новости.

        Вторая, написанная рядом, разошлась бы с первой молча: вопрос у них
        буквально один — «кого этот человек вправе адресовать». Проверяем не
        совпадение чисел (числа можно скопировать), а совпадение ответов на
        всех известных должностях сразу.
        """
        for role in list(wiki_access.ROLE_LEVELS) + ['supervisor', 'superadmin', '', None]:
            self.assertEqual(news_access.publish_ceiling(role),
                             wiki_access.grant_ceiling(role), role)

    def test_supervisor_reaches_only_operators(self):
        self.assertEqual(news_access.publish_ceiling('sv'),
                         wiki_access.ROLE_LEVELS['operator'])

    def test_trainer_and_operator_do_not_publish(self):
        # У них нет потолка вовсе — и по этому же признаку прячется вкладка.
        self.assertIsNone(news_access.publish_ceiling('trainer'))
        self.assertIsNone(news_access.publish_ceiling('operator'))
        self.assertIsNone(news_access.publish_ceiling('trainee'))

    def test_wiki_admin_role_lifts_the_ceiling(self):
        self.assertIsNone(news_access.publish_ceiling('operator'))
        self.assertEqual(news_access.publish_ceiling('operator', is_wiki_admin=True),
                         wiki_access.ROLE_LEVELS['super_admin'])

    def test_department_boundary_repeats_the_wiki_rule(self):
        self.assertEqual(news_access.publish_departments('sv', department_id=3), [3])
        self.assertEqual(
            news_access.publish_departments('admin', department_id=3,
                                            headed_department_ids=[7]),
            [3, 7])
        # Без границы — только директор и администратор вики.
        self.assertIsNone(news_access.publish_departments('super_admin', department_id=3))
        self.assertIsNone(news_access.publish_departments('sv', department_id=3,
                                                          is_wiki_admin=True))


class NewsAudienceRefusalTests(unittest.TestCase):
    """Проверка набора адресатов — та же, что потом стоит в роуте."""

    SV = dict(ceiling=wiki_access.ROLE_LEVELS['operator'], departments=[3])

    def test_own_department_passes(self):
        self.assertIsNone(news_access.audience_refusal(
            [{'subject_type': 'department', 'subject_id': 3}],
            subject_departments={('department', 3): 3}, **self.SV))

    def test_foreign_department_refused(self):
        refusal = news_access.audience_refusal(
            [{'subject_type': 'department', 'subject_id': 9}],
            subject_departments={('department', 9): 9}, **self.SV)
        self.assertIn('другому отделу', refusal or '')

    def test_role_subject_closed_for_bounded_author(self):
        """Правило на должность адресует людей ПО ВСЕЙ КОМПАНИИ.

        Супервайзеру одного отдела «всем операторам» — это и есть рассылка,
        которой быть не должно, поэтому отдельный вид адресата ему закрыт
        целиком (wiki/access.py: COMPANY_WIDE_SUBJECTS).
        """
        refusal = news_access.audience_refusal(
            [{'subject_type': 'otp_role', 'subject_role': 'operator'}],
            subject_departments={}, **self.SV)
        self.assertIn('по всей компании', refusal or '')

    def test_director_may_address_a_role(self):
        self.assertIsNone(news_access.audience_refusal(
            [{'subject_type': 'otp_role', 'subject_role': 'sv'}],
            ceiling=wiki_access.ROLE_LEVELS['super_admin'], departments=None,
            subject_departments={}))

    def test_person_above_the_ceiling_refused(self):
        """Именной адресат проверяется ДОЛЖНОСТЬЮ, а не только отделом.

        Без этой проверки супервайзер выписал бы новость на руководителя
        своего же отдела — граница отдела такого адресата пропускает.
        """
        refusal = news_access.audience_refusal(
            [{'subject_type': 'user', 'subject_id': 42}],
            subject_departments={('user', 42): 3},
            target_roles={42: 'admin'}, **self.SV)
        self.assertIn('ниже вас по должности', refusal or '')

    def test_person_below_the_ceiling_passes(self):
        self.assertIsNone(news_access.audience_refusal(
            [{'subject_type': 'user', 'subject_id': 42}],
            subject_departments={('user', 42): 3},
            target_roles={42: 'operator'}, **self.SV))

    def test_empty_audience_refused(self):
        """Новость без адресатов — публикация в никуда, а не «всем»."""
        self.assertIsNotNone(news_access.audience_refusal(
            [], subject_departments={}, **self.SV))

    def test_non_publisher_refused_before_anything_else(self):
        self.assertIsNotNone(news_access.audience_refusal(
            [{'subject_type': 'department', 'subject_id': 3}],
            ceiling=None, departments=[3], subject_departments={('department', 3): 3}))

    def test_min_level_above_the_ceiling_refused(self):
        """Порог снизу выше потолка сверху = адресатов ноль.

        Молчаливая публикация в пустоту хуже отказа: автор уверен, что смену
        предупредил.
        """
        refusal = news_access.audience_refusal(
            [{'subject_type': 'department', 'subject_id': 3,
              'min_role_level': wiki_access.ROLE_LEVELS['admin']}],
            subject_departments={('department', 3): 3}, **self.SV)
        self.assertIn('Порог должности', refusal or '')


class NewsViewerRulesTests(unittest.TestCase):
    """Кто под новость подпадает — сторона зрителя."""

    def test_supervisor_spelling_is_ranked_as_sv(self):
        """'supervisor' в ROLE_LEVELS нет, её уровень 0.

        Ноль проходит ЛЮБОЙ потолок сверху — то есть носитель такого написания
        получал бы новости, адресованные операторам. Ровно то, что правило
        «только тем, кто ниже» запрещает.
        """
        self.assertEqual(news_access.effective_role_level('supervisor'),
                         wiki_access.ROLE_LEVELS['sv'])
        self.assertEqual(news_access.viewer_roles('supervisor'), ['sv'])

    def test_no_downward_expansion_of_roles(self):
        """Новость идёт ВНИЗ, поэтому раскрытия ролей вниз здесь быть не должно.

        В вике expand_otp_roles отвечает на обратный вопрос («что человеку
        открыто») и раздаёт руководителю все роли ниже. Возьми мы её сюда —
        каждая новость операторам стала бы новостью руководителя.
        """
        self.assertEqual(news_access.viewer_roles('admin'), ['admin'])
        self.assertNotIn('operator', news_access.viewer_roles('admin'))
        self.assertIn('operator', wiki_access.expand_otp_roles('admin'))

    def test_audience_params_never_pass_empty_arrays(self):
        """`= ANY('{}')` не ошибка, но и не совпадение, а NULL сравнивать нельзя."""
        params = news_access.audience_params(
            {'department': [], 'direction': [], 'group': []}, 7, 'operator')
        self.assertEqual(params['departments'], [-1])
        self.assertEqual(params['directions'], [-1])
        self.assertEqual(params['groups'], [-1])

    def test_one_template_serves_both_the_window_and_the_report(self):
        """Журнал обязан считать адресатов теми же правилами, что и выдача окна.

        Разъедься они — «прочитали 12 из 30» считалось бы не по тем тридцати,
        кому окно показывали, и журнал перестал бы отвечать на вопрос, ради
        которого он существует.
        """
        source = _read('news', 'access.py')
        self.assertEqual(source.count('AUDIENCE_MATCH_TEMPLATE = """'), 1)
        for form in (news_access.AUDIENCE_MATCH_FOR_VIEWER,
                     news_access.AUDIENCE_MATCH_FOR_REPORT):
            self.assertIn('audience_max_role_level', form)
            self.assertIn("r.subject_type = 'department'", form)
            self.assertIn("r.subject_type = 'user'", form)
            self.assertIn('min_role_level', form)


class NewsDelayTests(unittest.TestCase):
    def test_delay_is_clamped_not_rejected(self):
        self.assertEqual(news_access.normalize_delay('30'), 30)
        self.assertEqual(news_access.normalize_delay(-5), 0)
        self.assertEqual(news_access.normalize_delay(10 ** 6),
                         news_schema.MAX_CONFIRM_DELAY_SECONDS)
        self.assertEqual(news_access.normalize_delay('быстро'),
                         news_schema.DEFAULT_CONFIRM_DELAY_SECONDS)

    def test_ceiling_keeps_the_portal_usable(self):
        """Потолок задержки — не придирка, а защита от опечатки.

        «1000» вместо «10» заперло бы весь портал у всего отдела до конца смены.
        """
        self.assertLessEqual(news_schema.MAX_CONFIRM_DELAY_SECONDS, 600)

    def test_optional_news_is_not_held_by_the_gate(self):
        """У необязательной новости кнопки «Прочитал» нет — её закрывают крестиком.

        Задержка при этом остаётся в записи: новость могли создать обязательной
        и снять обязательность позже. Гейт по ней означал бы окно, которое не
        закрывается ВООБЩЕ: ждать секунды пользователю нечем, кнопки нет.
        """
        source = _read('news', 'queries.py')
        confirm = source[source.index('def confirm_read('):]
        confirm = confirm[:confirm.index('\n# ─')]
        self.assertIn('if not is_mandatory:', confirm)
        # И выход из этой ветки — до проверки остатка.
        self.assertLess(confirm.index('if not is_mandatory:'),
                        confirm.index("return 'too_early'"))

    def test_audience_ceiling_belongs_to_whoever_set_the_audience(self):
        """Потолок переписывается вместе с набором адресатов.

        Иначе директор, поправивший адресатов у чужой опубликованной новости,
        унаследовал бы супервайзерский потолок 10 — его правка молча не дошла
        бы ни до кого выше оператора.
        """
        queries_src = _read('news', 'queries.py')
        set_audience = queries_src[queries_src.index('def set_audience('):]
        set_audience = set_audience[:set_audience.index('\n\ndef ')]
        self.assertIn('audience_max_role_level', set_audience)
        routes = _code_only(_read('news', 'routes.py'))
        self.assertEqual(routes.count("audience_max_role_level=ctx['ceiling']"), 4)

    def test_server_decides_the_gate(self):
        """Задержку проверяет СЕРВЕР, а не таймер в браузере.

        Без серверной проверки подтверждение уходило бы из консоли мгновенно, и
        вся механика держалась бы на честном слове клиента — та же ошибка, от
        которой лечили гейт «дочитал до конца» в обязательном ознакомлении.
        """
        source = _read('news', 'queries.py')
        confirm = source[source.index('def confirm_read('):]
        self.assertIn("'too_early'", confirm)
        self.assertIn('shown_at', confirm)


class NewsDoorsTests(unittest.TestCase):
    """Две двери раздела: читать может каждый, писать — супервайзер и выше."""

    def test_reading_routes_do_not_stand_behind_the_wiki_gates(self):
        """Окно обязано доехать до того, у кого вики нет.

        Роуты вики отвечают 403 по тумблеру `departments.wiki_enabled` и по
        QR-подтверждению сессии оператора. Появись выдача новости там — она не
        дошла бы ровно до тех, ради кого пишется.
        """
        source = _code_only(_read('news', 'routes.py'))
        self.assertNotIn('sensitive_access_granted', source)
        self.assertNotIn('wiki_enabled', source)
        self.assertNotIn('WIKI_DEPARTMENT_DISABLED', source)

        # И обратно: роутов новостей нет внутри блюпринта вики — там обе двери
        # стоят на каждом роуте разом, декоратором.
        for name in os.listdir(os.path.join(ROOT, 'wiki')):
            if not name.startswith('routes'):
                continue
            self.assertNotIn("'/news", _code_only(_read('wiki', name)), name)

    def test_publisher_routes_are_marked_at_the_declaration(self):
        """У каждой двери видно, про чтение она или про выпуск.

        Читающих ровно две — выдача окна и отметка о прочтении. Всё остальное
        обязано нести publisher=True: забытый флаг открыл бы оператору чужой
        журнал прочтений.
        """
        tree = ast.parse(_read('news', 'routes.py'))
        open_routes = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if not (isinstance(decorator, ast.Call)
                        and getattr(decorator.func, 'id', '') == 'news_route'):
                    continue
                publisher = any(kw.arg == 'publisher'
                                and getattr(kw.value, 'value', False) is True
                                for kw in decorator.keywords)
                if not publisher:
                    open_routes.append(decorator.args[0].value)
        self.assertEqual(sorted(open_routes), ['/<int:post_id>/read', '/access', '/pending'])

    def test_access_route_is_open_but_says_nothing_to_outsiders(self):
        """/access открыт всем НАМЕРЕННО и обязан молчать не-редактору.

        Вкладку он не открывает (её решает потолок в ответе), а отдельный 403
        на нём сделал бы красную строку в консоли у каждого оператора, зашедшего
        в вики. Но справочники отделов и людей в таком ответе лежать не должны.
        """
        source = _read('news', 'routes.py')
        handler = source[source.index('def news_access_info('):]
        handler = handler[:handler.index('\n    @news_route')]
        # Ветка отказа кончается там, где начинается настоящий ответ.
        empty = handler[handler.index("if ctx['ceiling'] is None:"):]
        empty = empty[:empty.index('return jsonify({\n            "can_publish": True')]
        self.assertIn('"can_publish": False', empty)
        self.assertIn('"people": []', empty)
        self.assertIn('"subjects": {}', empty)
        # А справочники людей и отделов собираются только ПОСЛЕ этой ветки.
        self.assertNotIn('targetable_people', empty)
        self.assertNotIn('subject_catalog', empty)


class NewsPerimeterTests(unittest.TestCase):
    """Две дыры, найденные разбором перед деплоем. Обе — про точечные пути."""

    def test_card_and_report_repeat_the_list_perimeter(self):
        """Периметр списка обязан повторяться на карточке и в журнале.

        Список редактора звучит так: своё плюс чужое своего отдела, но только
        от авторов НЕ ВЫШЕ себя (news/queries.py: list_posts). Точечные пути
        этого не повторяли — и супервайзер, не видя черновик своего
        руководителя в списке, открывал его прямым обращением по id ВМЕСТЕ с
        журналом «кто прочитал».
        """
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('def _may_read_post(', routes)
        # Обе точечные двери ходят через него, и ни одна — через прежнюю
        # проверку «только отдел».
        self.assertEqual(routes.count('if not _may_read_post(ctx, post):'), 3)
        self.assertNotIn("post.get('author_department_id') not in (", routes)
        # Меряется должностью автора, а не только отделом.
        self.assertIn("effective_role_level(post.get('author_role'))", routes)

    def test_confirming_a_read_is_bounded_by_audience_and_status(self):
        """«Прочитал» принимался по ЛЮБОМУ id, включая чужой черновик.

        Роут стоит на голой аутентификации (так требует постановка), поэтому
        без проверки любой сотрудник перебором id заранее «прочитывал» ещё не
        выпущенное объявление — и когда его публиковали, окно у этого человека
        не показывалось уже никогда, а в журнале он стоял подтвердившим.
        """
        source = _read('news', 'queries.py')
        confirm = source[source.index('def confirm_read('):]
        confirm = confirm[:confirm.index('\n# ─')]
        self.assertIn("p.status = 'published'", confirm)
        self.assertIn('AUDIENCE_MATCH_FOR_VIEWER', confirm)

    def test_a_manager_above_the_author_can_take_the_window_down(self):
        """Обязательному окну нужен тормоз не только у автора.

        Ошибочное объявление супервайзера иначе снимается только им самим — а
        он бывает на смене, в отпуске или уже не работает.
        """
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('def _may_take_down(', routes)
        self.assertIn('if not _may_take_down(ctx, post):', routes)
        # Право снять шире права править, но не наоборот: чужой текст не правят.
        take_down = routes[routes.index('def _may_take_down('):]
        take_down = take_down[:take_down.index('\n    def ')]
        self.assertIn('_may_read_post(ctx, post)', take_down)
        self.assertIn('>', take_down)

    def test_a_published_news_can_never_be_deleted(self):
        """Журнал прочтений — доказательство, и стирается он вместе с новостью.

        Проверки одного лишь текущего статуса мало: снятая перестаёт быть
        'published', и «снять → удалить» уносило бы журнал в два нажатия.
        """
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn("if post['published_at']:", routes)
        self.assertNotIn("if post['status'] == 'published':", routes)

    def test_reading_routes_do_not_pay_for_publishing_rights(self):
        """/pending дёргает каждый вошедший и каждая вкладка на каждый тычок.

        Потолок публикации стоит двух лишних обращений к базе и отвечает на
        вопрос, которого чтение не задаёт. Пул на портал — 40 соединений, и
        его уже делит SSE аукциона.
        """
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('if publisher or rights:', routes)
        self.assertIn("@news_route('/access', rights=True)", routes)
        # Контекст читателя роль вики не спрашивает.
        queries_src = _code_only(_read('news', 'queries.py'))
        context = queries_src[queries_src.index('def load_viewer_context('):]
        context = context[:context.index('def is_wiki_admin(')]
        self.assertNotIn('is_wiki_admin(cursor', context)

    def test_the_window_does_not_chase_a_newcomer_with_the_whole_archive(self):
        """Адресат считается по ТЕКУЩЕМУ профилю, а не по составу на день выпуска.

        Без горизонта вышедший на работу человек попадает под правило «отдел
        СЗоВ» и получает подряд все обязательные окна, накопленные за год.
        Журнал при этом не обрезается — горизонт снимает только показ.
        """
        source = _read('news', 'queries.py')
        self.assertIn('SHOW_HORIZON_DAYS', source)
        pending = source[source.index('def pending_for_user('):]
        pending = pending[:pending.index('def mark_shown(')]
        self.assertIn('@HORIZON@', pending)
        self.assertIn('p.expires_at IS NOT NULL', pending)


class NewsFrontendTests(unittest.TestCase):
    """Интерфейсные решения, которые нельзя проверить сборкой.

    Читаем .jsx текстом — приём раздела: собранный бандл не отвечает на вопрос
    «а не закрывается ли обязательное окно крестиком».
    """

    MODAL = os.path.join('src', 'components', 'news', 'NewsOfDayModal.jsx')

    def test_mandatory_window_has_no_way_out_but_the_button(self):
        source = _read(self.MODAL)
        # Крестик и Esc — только у необязательной новости.
        self.assertIn('if (!current || current.is_mandatory) return;', source)
        self.assertIn("current.is_mandatory ? (", source)
        # Клика по фону нет вовсе: у подложки не должно быть обработчика.
        backdrop = source[source.index('className="fixed inset-0'):]
        backdrop = backdrop[:backdrop.index('>')]
        self.assertNotIn('onMouseDown', backdrop)
        self.assertNotIn('onClick', backdrop)

    def test_the_window_shows_the_news_and_nothing_about_its_author(self):
        """Сотруднику показывают объявление, а не карточку автора.

        Решение владельца 01.09.2026: подпись «кто и когда опубликовал» и
        строка с замком «окно нельзя закрыть» из окна убраны. Замок объяснял
        словами то, что и так показано устройством окна, а автор с датой
        отвлекали от текста. И то и другое осталось у редактора — в списке и
        в журнале, где по ним действительно работают.
        """
        source = _jsx_code_only(_read(self.MODAL))
        for gone in ('author_name', 'author_role', 'author_department',
                     'published_at', 'initialsOf', 'Окно нельзя закрыть'):
            self.assertNotIn(gone, source, gone)
        # Шапка и заголовок остаются.
        self.assertIn('Новость дня', source)
        self.assertIn('обязательно к прочтению', source)
        self.assertIn('news-of-day-title', source)

    def test_the_hot_query_stopped_collecting_what_nobody_shows(self):
        """Два LEFT JOIN на запросе, который дёргает каждый вошедший.

        Убрав автора из окна, надо было убрать его и из выдачи — иначе
        соединения с users и departments остались бы платой ни за что.
        """
        source = _read('news', 'queries.py')
        pending = source[source.index('def pending_for_user('):]
        pending = pending[:pending.index('def mark_shown(')]
        self.assertNotIn('LEFT JOIN users', pending)
        self.assertNotIn('LEFT JOIN departments', pending)
        self.assertNotIn('author_name', pending)

    def test_window_opens_no_second_sse_channel(self):
        """Слотов на портал ровно BELL_STREAM_LIMIT, каждый — нить waitress.

        Свой канал у окна срезал бы ёмкость реалтайма вдвое, поэтому окно
        едет на тычке канала колокола. Фонового опроса тоже нет — его в
        проекте уже выпиливали из колокола.
        """
        source = _read(self.MODAL)
        self.assertNotIn('EventSource', source)
        self.assertNotIn('setInterval', source)
        self.assertIn('subscribeNewsPoke', source)

    def test_poke_reaches_the_window_past_the_memoized_sidebar(self):
        """Тычок идёт ПОДПИСКОЙ МОДУЛЯ, а не пропом — и это не вкусовщина.

        Окно смонтировано внутри `sidebarTree = useMemo(...)` в App.jsx. Значение
        из состояния App, отданное сюда пропом, замерзало бы на первом рендере:
        в списке зависимостей того useMemo под сорок значений, и новое в нём
        забыть проще, чем вспомнить. Отказ молчаливый — окно просто перестаёт
        всплывать у открытой вкладки.
        """
        app = _read('src', 'App.jsx')
        # Колоколу отдаётся стабильная функция модуля, а не колбэк из состояния.
        self.assertIn('onStreamPoke={emitNewsPoke}', app)
        # И никакого счётчика тычков в состоянии App: он и есть та ловушка.
        self.assertNotIn('newsPokeNonce', app)
        # Окно подписывается само.
        self.assertIn('subscribeNewsPoke', _read(self.MODAL))

    def test_window_does_not_hammer_pending_on_every_bell_event(self):
        """Тычок канала широковещателен и приходит на ЛЮБОЕ событие колокола.

        Плюс возврат во вкладку поднимает focus и visibilitychange сразу.
        Без гарда один переход между вкладками стоил бы двух-трёх запросов,
        а /pending ещё и пишет отметку о показе.
        """
        source = _read(self.MODAL)
        self.assertIn('lastLoadRef', source)
        # Первый запрос при входе обязан уйти мимо гарда.
        self.assertIn('load(true)', source)

    def test_bell_hands_the_poke_over_without_restarting_its_stream(self):
        """Колбэк тычка держится в ref, а не в зависимостях эффекта канала.

        Новая функция из App на каждом её рендере рвала бы живой SSE-поток и
        занимала слот заново — ровно та ловушка, что уже ловилась на
        нестабильном showToast.
        """
        source = _read('src', 'components', 'notifications', 'NotificationsBell.jsx')
        self.assertIn('const streamPokeRef = useRef(onStreamPoke);', source)
        self.assertIn('streamPokeRef.current?.()', source)

    def test_app_mounts_the_window_outside_the_wiki(self):
        source = _read('src', 'App.jsx')
        self.assertIn('NewsOfDayModal', source)
        # Окно не должно оказаться внутри раздела: тогда его увидели бы только
        # те, у кого вики есть, — то есть не те, ради кого оно сделано.
        wiki_view = _read('src', 'components', 'wiki', 'WikiView.jsx')
        self.assertNotIn('NewsOfDayModal', wiki_view)

    def test_news_tab_is_hidden_from_those_who_cannot_publish(self):
        source = _read('src', 'components', 'wiki', 'WikiView.jsx')
        self.assertIn('const canPublishNews = state?.grant_ceiling != null;', source)
        self.assertIn('show: features.news && canPublishNews', source)

    def test_row_actions_come_from_the_server(self):
        """Что можно с новостью, решает сервер и присылает признаком.

        Коллега того же уровня видит чужое объявление своего отдела, но правит
        его только автор. Вторая формула во фронте дала бы пункт меню, на
        который сервер отвечает 403, — молчаливый отказ, от которого этот
        проект уже лечили в каталоге вики.
        """
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn("post['can_edit'] = _may_edit(ctx, post)", routes)
        tab = _read('src', 'components', 'wiki', 'WikiNews.jsx')
        self.assertIn('post.can_edit', tab)
        # Своей формулы «автор ли я» во фронте быть не должно.
        self.assertNotIn('author_id ===', tab)

    def test_role_titles_live_in_one_place(self):
        """Подпись должности человек видит и в окне, и в журнале редактора."""
        for path in (self.MODAL, os.path.join('src', 'components', 'wiki', 'WikiNews.jsx')):
            self.assertIn('newsShared', _read(path), path)
        shared = _read('src', 'components', 'news', 'newsShared.js')
        self.assertEqual(len(re.findall(r'ROLE_TITLES = \{', shared)), 1)


class NewsPhotoTests(unittest.TestCase):
    """Фотографии объявления: до 10 кадров, WebP, карусель у читателя.

    Дополнение владельца к задаче #252 (02.09.2026): «сделать так что бы можно
    было прикреплять фотографии с конвертацией в WebP. До 10 картинок и что бы
    была Корусель у того кто будет смотреть».
    """

    MODAL = os.path.join('src', 'components', 'news', 'NewsOfDayModal.jsx')
    GALLERY = os.path.join('src', 'components', 'news', 'NewsGallery.jsx')
    TAB = os.path.join('src', 'components', 'wiki', 'WikiNews.jsx')
    CSS = os.path.join('src', 'components', 'news', 'news-modal.css')

    def test_photos_hang_below_the_post_they_belong_to(self):
        """Таблица кадров объявлена ПОСЛЕ news_posts, иначе FK уронит миграцию.

        Тем же порядком уже роняли инициализацию вики (шапка wiki/schema.py).
        """
        source = _read('news', 'schema.py')
        self.assertLess(source.index('CREATE TABLE IF NOT EXISTS news_posts'),
                        source.index('CREATE TABLE IF NOT EXISTS news_photos'))
        ddl = source[source.index('CREATE TABLE IF NOT EXISTS news_photos'):]
        self.assertIn('REFERENCES news_posts(id) ON DELETE CASCADE', ddl)
        self.assertIn('idx_news_photos_post', source)
        # Частичный: привязанных кадров в индексе уборки быть не должно.
        self.assertIn('WHERE news_id IS NULL', source)
        self.assertEqual(news_schema.MAX_PHOTOS_PER_POST, 10)

    def test_the_picture_does_not_go_through_a_door_of_ours(self):
        """Ни одной двери вики на пути кадра — ради этого раздел и выносили.

        /api/wiki/file/<id> стоит за тумблером departments.wiki_enabled и за
        QR-подтверждением сессии: оператор без вики получил бы 403 на каждый
        кадр. Своего роута отдачи у нас тоже нет — тег <img> не шлёт
        заголовков, и такой роут пришлось бы авторизовать кукой, которую
        мобильный браузер кросс-сайтом не приложит.
        """
        routes = _code_only(_read('news', 'routes.py'))
        photos = _code_only(_read('news', 'photos.py'))
        for forbidden in ('/api/wiki/file', 'wiki.storage', 'store_file',
                          'wiki_files', 'redirect('):
            self.assertNotIn(forbidden, routes, forbidden)
            self.assertNotIn(forbidden, photos, forbidden)
        # Роута отдачи файла нет вовсе: наружу уходит подписанный адрес GCS.
        self.assertNotIn("news_route('/file", routes)
        self.assertNotIn("news_route('/photos/<uuid:photo_id>')", routes)

    def test_the_converter_is_one_for_the_whole_project(self):
        """WebP считается одним кодом на весь проект — своего не заводим."""
        photos = _read('news', 'photos.py')
        self.assertIn('from wiki import images as wiki_images', photos)
        self.assertIn('wiki_images.to_webp', photos)
        # Клиентская половина — тоже готовая, из «Посылок».
        self.assertIn("from '../parcels/parcelPhoto'", _read(self.TAB))
        # Своего конвертера в пакете новостей быть не должно.
        for name in ('NewsGallery.jsx', 'newsShared.js', 'NewsOfDayModal.jsx'):
            self.assertNotIn('createImageBitmap',
                             _read('src', 'components', 'news', name), name)

    def test_an_unreadable_file_is_refused_not_stored(self):
        """Файл, который не открылся картинкой, В БАКЕТ НЕ ЛОЖИТСЯ.

        Здесь мы расходимся с вики намеренно: там непереведённый файл кладётся
        как принесли (он вложение статьи), а тут единственный смысл строки —
        показать кадр. Заодно это ЕДИНСТВЕННАЯ настоящая проверка «а картинка
        ли это»: content_type пишет клиент, и запрос с заголовком image/jpeg и
        телом PDF иначе сделал бы раздел файлохостингом.
        """
        photos = _read('news', 'photos.py')
        block = photos[photos.index('converted = wiki_images.to_webp'):]
        block = block[:block.index('data, kind, width, height = converted')]
        self.assertIn('raise PhotoError', block)
        self.assertIn('NEWS_PHOTO_UNREADABLE', block)

    def test_the_limit_is_the_same_number_on_both_sides(self):
        """Десять кадров и двадцать мегабайт — одинаково на сервере и в форме.

        Правило, живущее только во фронте, держится до первого запроса мимо него.
        """
        self.assertEqual(news_schema.MAX_PHOTOS_PER_POST, 10)
        client = _read('src', 'components', 'parcels', 'parcelPhoto.js')
        self.assertIn('PHOTO_MAX_COUNT = 10', client)
        self.assertIn('PHOTO_MAX_BYTES = 20 * 1024 * 1024', client)
        self.assertIn('MAX_BYTES = 20 * 1024 * 1024', _read('news', 'photos.py'))

    def test_the_hot_query_pays_nothing_for_photos(self):
        """/pending не дорожает: кадры — скалярный агрегат, а не джойн.

        Джойн размножил бы строку новости на число кадров вместе с телом
        объявления в каждой копии и, что хуже, отдал бы LIMIT 20 КАРТИНКАМ:
        две новости по десять кадров съели бы всю очередь, и третье объявление
        молча не доехало бы до окна.
        """
        source = _read('news', 'queries.py')
        block = source[source.index('def pending_for_user('):]
        block = block[:block.index('\ndef mark_shown(')]
        self.assertIn('json_agg', block)
        self.assertIn('LIMIT %(max_photos)s', block)
        self.assertIn('LIMIT 20', block)
        self.assertNotIn('JOIN news_photos', block)
        # Ровно один execute: второго обращения к базе за кадрами нет.
        self.assertEqual(block.count('cursor.execute('), 1)
        # Прежние стражи горячего запроса остаются в силе.
        for forbidden in ('LEFT JOIN users', 'LEFT JOIN departments', 'author_name'):
            self.assertNotIn(forbidden, block, forbidden)

    def test_bucket_and_blob_path_never_leave_through_the_card(self):
        """Путь в бакете наружу не уходит НИ ОДНИМ из семи путей get_post.

        get_post зовут семь мест, из них журнал и удаление свой результат не
        отдают. Положи мы bucket/blob_path в общий словарь — завели бы семь
        возможностей уронить его в jsonify.
        """
        source = _read('news', 'queries.py')
        card = source[source.index('def get_post('):]
        card = card[:card.index('\ndef audience_rules(')]
        self.assertNotIn('news_photos', card)
        self.assertNotIn('blob_path', card)
        report = source[source.index('def read_report('):]
        report = report[:report.index('\ndef audience_size(')]
        self.assertNotIn('blob_path', report)

    def test_only_the_signer_shapes_the_outgoing_photo(self):
        """Наружу кадр собирает ровно один код — sign_urls, по белому списку."""
        routes = _code_only(_read('news', 'routes.py'))
        # Везде, где кадры кладутся в ответ, они проходят через подпись.
        self.assertIn('news_photos.sign_urls(', routes)
        self.assertNotIn('_lms_signed_url', routes)
        photos = _read('news', 'photos.py')
        out = photos[photos.index('def sign_urls('):]
        # Белый список ключей: bucket и blob_path в него не входят.
        self.assertIn("'id': str(row.get('id'))", out)
        collected = re.search(r"out\.append\(\{(.*?)\}\)", out, re.S).group(1)
        self.assertNotIn('bucket', collected)
        self.assertNotIn('blob_path', collected)

    def test_signatures_are_cached_byte_for_byte(self):
        """Кэш подписей — не оптимизация, а условие работы карусели.

        Подпись v4 кладёт в адрес момент подписания, поэтому каждый новый вызов
        даёт ДРУГУЮ строку. Без кэша ответ /pending на каждый тычок канала
        колокола приносил бы новые адреса, <img> считал бы кадры новыми, и
        лента отматывалась бы на первый кадр, пока человек смотрит пятый.
        """
        photos = _read('news', 'photos.py')
        self.assertIn('_SIGNED = {}', photos)
        self.assertIn('_RESIGN_BEFORE', photos)
        self.assertIn('private, max-age=', photos)

    def test_photo_routes_do_not_hold_a_pool_slot_through_the_network(self):
        """Пережатие и заливка идут ВНЕ курсора: слотов сорок на весь портал.

        news_route держит курсор вокруг обработчика (в отличие от посылок),
        поэтому кадру на 12 мегапикселей нужен defer_cursor — иначе слот занят
        на секунды. Заодно только так можно снести блоб ПОСЛЕ фиксации.
        """
        routes = _read('news', 'routes.py')
        self.assertIn('defer_cursor=False', routes)      # объявление в декораторе
        add = routes[routes.index('def news_photo_add('):]
        add = add[:add.index('def news_photo_drop(')]
        self.assertIn('defer_cursor=True',
                      routes[:routes.index('def news_photo_add(')].rsplit('@news_route', 1)[-1]
                      + '@news_route')
        # Пережатие и заливка не должны стоять внутри блока with курсора.
        for line in add.splitlines():
            if 'news_photos.prepare(' in line or 'news_photos.upload(' in line:
                self.assertLess(len(line) - len(line.lstrip()), 16, line)
        drop = routes[routes.index('def news_photo_drop('):]
        # Блобы сносятся после выхода из with — на нулевом отступе тела функции.
        self.assertIn('\n        news_photos.drop_blobs(gcs, refs)', drop)

    def test_photo_routes_are_bounded_by_the_right_to_edit(self):
        """Кадр правит тот, кто правит текст: фотография — часть объявления."""
        routes = _code_only(_read('news', 'routes.py'))
        drop = routes[routes.index('def news_photo_drop('):]
        self.assertIn('_may_edit(ctx, post)', drop)
        # Периметр чтения не расширился: через него по-прежнему ходят три двери.
        self.assertEqual(routes.count('if not _may_read_post(ctx, post):'), 3)

    def test_someone_elses_photo_cannot_be_adopted(self):
        """Идентификатор кадра НЕ должен быть ключом доступа.

        Без условия по владельцу правкой своей новости можно было бы
        «усыновить» чужую фотографию, подставив её id в массив photos.
        """
        source = _read('news', 'queries.py')
        block = source[source.index('def set_photos('):]
        self.assertIn('f.news_id = %(post)s', block)
        self.assertIn('f.uploaded_by = %(me)s', block)

    def test_orphan_blobs_are_swept_after_the_commit(self):
        """Строки удаляет SQL, байты — вызывающий, и только после фиксации."""
        source = _read('news', 'queries.py')
        sweep = source[source.index('def sweep_loose_photos('):]
        sweep = sweep[:sweep.index('\ndef set_photos(')]
        self.assertIn('RETURNING bucket, blob_path', sweep)
        # Сама функция блобы НЕ сносит — она их только возвращает.
        self.assertNotIn('drop_blobs', sweep)
        # Аварийная ветка загрузки убирает уже залитый блоб.
        routes = _read('news', 'routes.py')
        add = routes[routes.index('def news_photo_add('):]
        add = add[:add.index('def news_photo_drop(')]
        self.assertIn('news_photos.drop_blobs(gcs, [(bucket, blob_path)])', add)

    def test_photos_are_attached_before_the_news_goes_out(self):
        """Кадры привязываются РАНЬШЕ публикации и в той же транзакции.

        Иначе между «новость есть» и «кадры прикреплены» открывается окно, в
        котором объявление уже всплыло у отдела без фотографий, — а второй раз
        оно не всплывёт никогда: очередь /pending человек получает один раз.
        """
        routes = _read('news', 'routes.py')
        create = routes[routes.index('def news_post_create('):]
        create = create[:create.index('def news_post_update(')]
        self.assertLess(create.index('_set_photos_refusal('),
                        create.index('queries.publish_post('))

    def test_a_missing_photo_table_does_not_break_the_portal(self):
        """Нет таблицы кадров — «фотографий нет», а не «раздел разворачивается».

        Порядок деплоя: код выдачи приезжает раньше, чем DDL отработает на
        старте, и подзапрос по несуществующей таблице ответил бы пятисоткой
        КАЖДОМУ вошедшему в портал — на самом горячем роуте.
        """
        self.assertIn('def photos_ready(', _read('news', 'schema.py'))
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('_photos_ready(cursor)', routes)
        # Готовность кадров НЕ подмешана в готовность всего раздела.
        schema_src = _read('news', 'schema.py')
        ready = schema_src[schema_src.index('def schema_is_ready('):
                           schema_src.index('def photos_ready(')]
        self.assertNotIn('news_photos', ready)
        # Выдача умеет обойтись без таблицы.
        self.assertIn('with_photos=False', _read('news', 'queries.py'))

    def test_the_window_mounts_the_gallery_without_react_owning_it(self):
        """Ленту строит DOM API, а не JSX: mountGallery перекладывает узлы.

        React, владеющий кадрами, стал бы диффать дерево, которого не строил, и
        упал бы NotFoundError на размонтировании — причём на ВТОРОЙ новости в
        очереди, а не у разработчика с одной. Уронив обязательное окно, он
        закрыл бы человеку вход в портал.
        """
        # Без блочных комментариев: объяснение, почему здесь НЕ attachGallery,
        # стоит там же в шапке — и поиск по строке нашёл бы ровно его.
        gallery = _jsx_code_only(_read(self.GALLERY))
        self.assertIn("import('../wiki/gallery')", gallery)
        # Именно mountGallery: attachGallery не оборачивает кадры в слайды, и
        # правило flex: 0 0 100% не применилось бы — листать стало бы нечего.
        self.assertIn('mountGallery(strip, document)', gallery)
        self.assertNotIn('attachGallery', gallery)
        self.assertIn("document.createElement('img')", gallery)
        self.assertNotIn('dangerouslySetInnerHTML', gallery)

    def test_every_class_the_gallery_builds_is_drawn_for_the_news(self):
        """Единственный сторож дубля стилей.

        Карусель переиспользуется целиком, а её CSS в вики весь начинается с
        предка .wiki-prose и берёт цвета из переменных .wiki-scope (который
        означает ещё и zoom). Поэтому правила скопированы в news-modal.css — и
        набор классов обязан совпадать с тем, что реально строит gallery.js.
        """
        built = set(re.findall(r'wiki-gallery(?:__[a-z-]+|--[a-z]+)?',
                               _read('src', 'components', 'wiki', 'gallery.js')))
        css = _read(self.CSS)
        missing = sorted(name for name in built if name not in css)
        self.assertEqual(missing, [], 'не нарисованы в news-modal.css: %s' % missing)

    def test_a_frame_without_a_signature_does_not_break_the_window(self):
        """Кадр без адреса не показываем, протухший — перезапрашиваем."""
        gallery = _read(self.GALLERY)
        self.assertIn('photo.url', gallery)
        self.assertIn('onBroken', gallery)
        modal = _read(self.MODAL)
        self.assertIn('onBroken={() => load(true)}', modal)
        # Кадр без подписи сервер выбрасывает ещё на выдаче.
        photos = _read('news', 'photos.py')
        out = photos[photos.index('def sign_urls('):]
        self.assertIn('if not url:', out)

    def test_the_window_still_shows_nothing_about_the_author(self):
        """Карусель не вернула в окно того, что владелец просил убрать.

        Проверка идёт по JSX без блочных комментариев: объяснение, почему
        подписи нет, стоит там же комментарием.
        """
        modal = _jsx_code_only(_read(self.MODAL))
        for forbidden in ('author_name', 'author_role', 'author_department',
                          'initialsOf', 'published_at', 'publishedLabel'):
            self.assertNotIn(forbidden, modal, forbidden)
        # Подложка окна по-прежнему одна: лайтбокс живёт в другом файле и
        # рисуется порталом, иначе он сдвинул бы срез стража подложки.
        self.assertEqual(modal.count('className="fixed inset-0'), 1)
        self.assertNotIn('IosLightbox', modal)

    def test_the_queue_head_is_refreshed_not_frozen(self):
        """Перезапрос обязан обновлять открытую новость, а не только хвост.

        Раньше голова бралась из СТАРОГО состояния, а её свежая копия
        выбрасывалась из остатка: подписи адресов конечны, и вкладка, открытая
        утром, к вечеру показывала бы пустые рамки до конца дня.
        """
        modal = _jsx_code_only(_read(self.MODAL))
        merge = modal[modal.index('setQueue((prev) => {'):]
        merge = merge[:merge.index('});')]
        self.assertIn('items.find((item) => item.id === head.id)', merge)

    def test_the_form_explains_itself_through_the_hint(self):
        """Пояснения формы ушли под «i» — дополнение владельца 02.09.2026."""
        tab = _read(self.TAB)
        self.assertGreaterEqual(tab.count('<IosHint'), 5)
        code = _jsx_code_only(tab)
        # Серых строк, которые пересказывали то, что видно на экране, нет.
        for gone in ('Пусто — пока не подтвердят',
                     'Окно нельзя закрыть, отметка попадёт в журнал',
                     'Окно закрывается крестиком, журнал не ведётся',
                     '{delayLabel(delay)}',
                     'все сотрудники этой должности'):
            self.assertNotIn(gone, code, gone)

    def test_the_audience_boundary_stays_in_plain_sight(self):
        """Граница адресатов под «i» НЕ уезжает, и это не забывчивость.

        Остальные пояснения объясняют то, что человек видит на экране. Это —
        границу, которой на экране НЕТ: потолок режет уже выбранный отдел на
        выдаче, и автор публикует «отделу», а журнал показывает знаменатель
        меньше его состава. Прочитать это надо ДО «Опубликовать», а подсказку
        за «i» читают после.
        """
        code = _jsx_code_only(_read(self.TAB))
        card = code[code.index('Из выбранного новость увидят') - 2000:]
        card = card[:card.index('ниже вас по должности') + 200]
        self.assertIn('ниже вас по должности', card)
        self.assertNotIn('<IosHint', card)

    def test_the_form_does_not_publish_while_photos_are_still_flying(self):
        """Сохранить, пока кадры едут, — значит выпустить объявление без них."""
        tab = _read(self.TAB)
        self.assertIn('photos.some((photo) => photo.busy)', tab)
        self.assertIn("photos: photos.filter((photo) => photo.id)", tab)


if __name__ == '__main__':
    unittest.main()

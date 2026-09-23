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
        # Шесть мест: две записи адресатов (создание и правка), два выпуска
        # (создание с публикацией и кнопка «Опубликовать» — оба через _launch,
        # который взводит запланированный запуск тем же потолком) и два
        # расчёта круга адресатов, проверка SIP-номеров и предварительный
        # расчёт рассылки. Возьми любой из них другой потолок — предупреждение
        # и расчёт были бы про других людей, чем сама публикация.
        self.assertEqual(routes.count("audience_max_role_level=ctx['ceiling']"), 6)

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

        Читающих ровно столько: выдача окна и отметка о прочтении, лента
        «мои новости» с карточкой и прохождение теста и тренажёра (задача #342 —
        вкладка «Новости» открыта всем, решение владельца 17.09.2026). Всё
        остальное обязано нести publisher=True: забытый флаг открыл бы оператору
        чужой журнал прочтений.
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
        self.assertEqual(sorted(open_routes), [
            '/<int:post_id>/quiz', '/<int:post_id>/read', '/<int:post_id>/trainer',
            '/access', '/feed', '/feed/<int:post_id>', '/pending'])

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
        # ВСЕ точечные двери ходят через него — карточка, журнал, его выгрузка
        # в Excel — и ни одна не через прежнюю проверку «только отдел».
        self.assertEqual(routes.count('if not _may_read_post(ctx, post):'), 4)
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
        self.assertIn('viewer_match(with_space)', confirm)

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

    def test_a_news_on_show_is_taken_down_before_it_is_deleted(self):
        """Решение владельца 21.09.2026: «в архиве должна быть кнопка удалить,
        чтобы можно было удалить новость навсегда, журнал его тоже удалится кто
        прочитал».

        Порог остался ровно один и он про ПОКАЗ, а не про прошлое: объявление,
        висящее у людей на экране прямо сейчас, сначала снимают — иначе
        обязательное окно исчезло бы у читающего его человека в середине текста.
        """
        routes = _code_only(_read('news', 'routes.py'))
        delete = routes[routes.index('def news_post_delete('):
                        routes.index('def news_quiz_draft(')]
        self.assertIn("if post['status'] == 'published':", delete)
        self.assertNotIn("if post['published_at']:", delete)

    def test_deleting_a_news_takes_its_photos_out_of_the_bucket(self):
        """Каскад живёт в базе, а картинка — в бакете: без отдельного снятия от
        каждой удалённой новости оставалось бы до десяти файлов навсегда,
        сборщика сирот в проекте нет. И сносятся они ПОСЛЕ фиксации."""
        routes = _code_only(_read('news', 'routes.py'))
        delete = routes[routes.index('def news_post_delete('):
                        routes.index('def news_quiz_draft(')]
        self.assertIn('with_photos=_photos_ready(cursor)', delete)
        self.assertLess(delete.index('queries.delete_post('),
                        delete.index('news_photos.drop_blobs('))
        self.assertIn("methods=('DELETE',), publisher=True,\n                defer_cursor=True",
                      _read('news', 'routes.py'))
        drop = _code_only(_read('news', 'queries.py'))
        drop = drop[drop.index('def delete_post('):]
        drop = drop[:drop.index('def ', 10)]
        self.assertIn('DELETE FROM news_photos WHERE news_id = %s RETURNING bucket, blob_path',
                      drop)
        self.assertLess(drop.index('news_photos'), drop.index('DELETE FROM news_posts'))

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

    def test_news_tab_is_open_to_every_reader(self):
        """Вкладка «Новости» — всем (решение владельца 17.09.2026, задача #342).

        Читателю — «только сами новости, которые ему были предназначены»,
        редактору — ещё и управление. Право правки — прежний потолок
        публикации, второго признака рядом с ним нет.
        """
        source = _read('src', 'components', 'wiki', 'WikiView.jsx')
        self.assertIn('const canPublishNews = state?.grant_ceiling != null;', source)
        self.assertIn("{ key: 'news', label: 'Новости', icon: Megaphone,\n          show: features.news },",
                      source)
        tab = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        # Читатель получает ленту, а не отказ «публикуют супервайзер и выше».
        self.assertIn('if (!canPublish) {', tab)
        self.assertIn('return <NewsFeed apiBaseUrl={apiBaseUrl} headers={headers} '
                      'spaceId={spaceId} />;', tab)
        self.assertNotIn('Новости публикуют супервайзер и выше', tab)
        # Список редактора читателю не запрашивается — иначе 403 на каждом заходе.
        self.assertIn("if (!canPublish || bucket === 'mine')", tab)

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

    def test_deleting_a_released_news_is_asked_out_loud(self):
        """Вместе с новостью пропадает журнал «Кто прочитал» — про такое
        спрашивают вслух. У черновика спрашивать нечего: его никто не видел."""
        tab = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        self.assertIn("if (action === 'delete' && post.published_at", tab)
        self.assertIn('window.confirm(', tab)
        self.assertIn('Вернуть его будет нельзя', tab)
        # Кнопка «Удалить» есть у всего, что не на показе, — то есть и в архиве.
        self.assertIn("post.can_edit && post.status !== 'published'", tab)


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
        # До следующей двери: дальше идут читающие роуты, и они про другое.
        drop = drop[:drop.index('@news_route(')]
        self.assertIn('_may_edit(ctx, post)', drop)
        # Право ЧИТАТЬ кадру не подходит: через _may_read_post ходят только
        # читающие точечные двери (карточка, журнал, выгрузка журнала), и
        # правка кадра среди них не появилась.
        self.assertNotIn('_may_read_post', drop)

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
                        create.index('_launch('))

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


class NewsFormQuizTests(unittest.TestCase):
    """Тест в форме новости: «Добавить вопросы» или «Составить ИИ» (15.09.2026)."""

    GOOD = ('ТЕСТ:\n1. Сколько стоит аренда в сутки?\n- 3000 ₸\n+ 5000 ₸\n- 7000 ₸\n'
            '2. Где оформляется аренда?\n+ В офисе\n- В приложении\n- По телефону')
    BODY = ('<p>Аренда — <strong>5000 ₸</strong> в сутки.</p>'
            '<ul><li><p>Оформляется в офисе.</p></li></ul>')

    @staticmethod
    def _generate(*replies):
        calls = []

        def generate(system, user, **kwargs):
            calls.append((system, user))
            return replies[min(len(calls), len(replies)) - 1], {'model': 'stub'}
        return generate, calls

    def test_news_text_keeps_sentences_whole_and_lists_once(self):
        from wiki.ai import knowledge
        self.assertEqual(knowledge.news_text(self.BODY),
                         'Аренда — 5000 ₸ в сутки.\nОформляется в офисе.')

    def test_quiz_is_drafted_from_the_news_text(self):
        from wiki.ai import knowledge
        generate, calls = self._generate(self.GOOD)
        result = knowledge.draft_quiz(title='Аренда', body=self.BODY, generate_fn=generate)
        self.assertEqual(len(calls), 1)
        self.assertIn('5000 ₸ в сутки', calls[0][1])
        self.assertEqual([item['correct'] for item in result['quiz']], [1, 0])
        self.assertEqual(result['warnings'], [])

    def test_reply_without_the_marker_is_still_a_quiz(self):
        from wiki.ai import knowledge
        generate, _calls = self._generate(self.GOOD.replace('ТЕСТ:\n', ''))
        self.assertEqual(len(knowledge.draft_quiz(title='', body=self.BODY,
                                                  generate_fn=generate)['quiz']), 2)

    def test_broken_reply_is_retried_once_then_handed_over(self):
        from wiki.ai import knowledge
        generate, calls = self._generate('Не могу составить тест.')
        result = knowledge.draft_quiz(title='', body=self.BODY, generate_fn=generate)
        self.assertEqual(len(calls), 2)
        self.assertIn('Предыдущий ответ не принят', calls[1][1])
        self.assertTrue(result['warnings'])

    def test_invented_number_in_the_right_answer_is_flagged(self):
        from wiki.ai import knowledge
        generate, _calls = self._generate(self.GOOD.replace('+ 5000 ₸', '+ 9000 ₸'))
        result = knowledge.draft_quiz(title='', body=self.BODY, generate_fn=generate)
        self.assertTrue(any('9000' in warning for warning in result['warnings']), result)

    def test_create_checks_the_quiz_before_writing_and_attaches_it_before_publishing(self):
        routes = _read('news', 'routes.py')
        create = routes[routes.index('def news_post_create('):routes.index('def news_post_update(')]
        self.assertLess(create.index('_quiz_from_request('), create.index('queries.create_post('))
        self.assertLess(create.index('queries.set_quiz('), create.index('_launch('))
        # Обязательность навязывает только ОБЯЗАТЕЛЬНОЕ прохождение (#342).
        self.assertIn('or news_access.must_pass(', create)

    def test_quiz_of_a_published_news_is_locked(self):
        routes = _read('news', 'routes.py')
        update = routes[routes.index('def news_post_update('):routes.index('def news_post_publish(')]
        self.assertIn('NEWS_QUIZ_LOCKED', update)
        self.assertLess(update.index('NEWS_QUIZ_LOCKED'), update.index('queries.update_post('))

    def test_ai_draft_does_not_hold_a_pool_slot(self):
        routes = _read('news', 'routes.py')
        self.assertIn("@news_route('/quiz/draft', methods=('POST',), publisher=True, "
                      "defer_cursor=True)", routes)
        body = routes[routes.index('def news_quiz_draft('):]
        body = body[:body.index('@news_route(')]
        self.assertIn('ProviderError', body)
        self.assertNotIn('_get_cursor', body)

    def test_form_offers_manual_and_ai_quiz_with_one_shared_editor(self):
        form = _read('src', 'components', 'wiki', 'WikiNews.jsx')
        questions = _read('src', 'components', 'wiki', 'WikiQuestions.jsx')
        for text in ('Составить ИИ', 'Добавить вопросы', '/api/news/quiz/draft',
                     '...(quizLocked ? {} : {'):
            self.assertIn(text, form)
        for source in (form, questions):
            self.assertIn("import NewsQuizEditor from '../news/NewsQuizEditor'", source)
        self.assertNotIn('function QuizEditor', questions)


class NewsPassTests(unittest.TestCase):
    """Задача #342: тренажёр в новости и необязательный тест.

    «При создании новости должна быть возможность прикрепить тренажёр или тест…
    Если тест обязательный, оператор не должен иметь возможности закрыть
    новость, не пройдя тест. Если необязательный — может ознакомиться без него».
    """

    def test_trainer_key_is_checked_by_shape_only(self):
        """Сценарии живут в коде фронта — сервер знает только форму ключа."""
        self.assertEqual(news_access.normalize_trainer_key('yandex-pro-edo-provider'),
                         ('yandex-pro-edo-provider', None))
        self.assertEqual(news_access.normalize_trainer_key('  '), (None, None))
        self.assertEqual(news_access.normalize_trainer_key(None), (None, None))
        for bad in ('Yandex', 'a b', '../x', '-lead', 'x' * 65, '<script>'):
            key, problem = news_access.normalize_trainer_key(bad)
            self.assertIsNone(key, bad)
            self.assertTrue(problem, bad)

    def test_optional_pass_never_holds_the_confirmation(self):
        self.assertEqual(news_access.outstanding_passes(
            pass_required=False, has_quiz=True, has_trainer=True,
            quiz_passed=False, trainer_passed=False), [])
        self.assertFalse(news_access.must_pass(pass_required=False, has_quiz=True,
                                               has_trainer=True))

    def test_required_pass_lists_what_is_left_trainer_first(self):
        left = news_access.outstanding_passes(
            pass_required=True, has_quiz=True, has_trainer=True,
            quiz_passed=False, trainer_passed=False)
        self.assertEqual(left, ['trainer', 'quiz'])
        self.assertEqual(news_access.outstanding_passes(
            pass_required=True, has_quiz=True, has_trainer=True,
            quiz_passed=False, trainer_passed=True), ['quiz'])
        self.assertEqual(news_access.outstanding_passes(
            pass_required=True, has_quiz=True, has_trainer=False,
            quiz_passed=True, trainer_passed=False), [])

    def test_nothing_to_pass_means_nothing_required(self):
        """pass_required по умолчанию TRUE и у новости без теста ничего не значит."""
        self.assertFalse(news_access.must_pass(pass_required=True, has_quiz=False,
                                               has_trainer=False))

    def test_old_quizzes_stay_required(self):
        """Тесты, выпущенные до задачи, были обязательными — умолчание ДА."""
        source = _read('news', 'schema.py')
        self.assertIn('ADD COLUMN IF NOT EXISTS pass_required BOOLEAN NOT NULL DEFAULT TRUE',
                      source)
        for table, column in news_schema.PASS_COLUMNS:
            self.assertIn('ALTER TABLE %s ADD COLUMN IF NOT EXISTS %s' % (table, column), source)
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn("current.get('pass_required', True)", routes)

    def test_missing_columns_do_not_break_the_portal(self):
        """Колонки #342 не подмешаны в готовность раздела и читаются по флагу."""
        schema_src = _read('news', 'schema.py')
        ready = schema_src[schema_src.index('def schema_is_ready('):
                           schema_src.index('def photos_ready(')]
        self.assertNotIn('pass_required', ready)
        self.assertIn('def pass_ready(', schema_src)
        queries_src = _read('news', 'queries.py')
        pending = queries_src[queries_src.index('def pending_for_user('):
                              queries_src.index('def mark_shown(')]
        self.assertIn('_pass_columns_sql(with_pass)', pending)
        self.assertNotIn('p.pass_required', pending)
        # По-прежнему одно обращение к базе на горячем роуте.
        self.assertEqual(pending.count('cursor.execute('), 1)

    def test_confirmation_waits_for_a_required_trainer(self):
        source = _read('news', 'queries.py')
        confirm = source[source.index('def confirm_read('):]
        confirm = confirm[:confirm.index('\n# ─')]
        self.assertIn('news_access.must_pass(', confirm)
        self.assertLess(confirm.index('news_access.must_pass('),
                        confirm.index('if not is_mandatory:'))
        self.assertLess(confirm.index("return 'trainer_pending'"),
                        confirm.rindex('SET confirmed_at'))
        self.assertLess(confirm.index("'too_early'"), confirm.index("'trainer_pending'"))
        self.assertIn('NEWS_TRAINER_PENDING', _read('news', 'routes.py'))

    def test_reader_doors_are_bounded_by_audience_and_status(self):
        """/quiz, /trainer и лента стоят на голой аутентификации — как /read.

        Без периметра перебором id можно было бы прочитать чужой черновик или
        «пройти» ещё не выпущенный тест.
        """
        source = _read('news', 'queries.py')
        for name, end in (('def _viewer_post(', 'def _mark_pass('),
                          ('def feed_for_user(', 'def _plain_preview('),
                          ('def feed_post(', '# ─')):
            block = source[source.index(name):]
            block = block[:block.index(end)]
            self.assertIn("p.status = 'published'", block, name)
            # viewer_match(...) — тот же шаблон адресата, только с границей
            # пространства или без неё (news/access.py).
            self.assertIn('viewer_match(with_space)', block, name)
            # Свою новость автор не получает — ни окном, ни в ленте.
            self.assertIn('p.author_id IS DISTINCT FROM %(user_id)s', block, name)
            self.assertNotIn('correct_index', block, name)
        for name in ('def pass_quiz(', 'def mark_trainer_passed('):
            block = source[source.index(name):]
            block = block[:block.index('\ndef ', 10)]
            self.assertIn('_viewer_post(', block, name)

    def test_the_wrong_questions_are_never_named_to_the_client(self):
        """Правило владельца (21.09.2026): один неверный вариант сбрасывает весь
        тест, и «завершён» он, только когда все ответы выбраны верно. Назови
        сервер вопрос с ошибкой — и ответ подбирался бы переключением одного
        варианта: сеть у браузера открыта любому."""
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('NEWS_QUIZ_WRONG', routes)
        self.assertNotIn('"wrong"', routes)
        self.assertNotIn("'wrong'", routes)

    def test_a_pass_mark_is_never_moved_by_a_retry(self):
        source = _read('news', 'queries.py')
        mark = source[source.index('def _mark_pass('):source.index('def pass_quiz(')]
        self.assertIn('ON CONFLICT (news_id, user_id)', mark)
        self.assertIn('COALESCE(news_reads.{col}, EXCLUDED.{col})', mark)

    def test_published_news_keeps_its_trainer_and_requirement(self):
        routes = _read('news', 'routes.py')
        update = routes[routes.index('def news_post_update('):routes.index('def news_post_publish(')]
        self.assertIn('NEWS_PASS_LOCKED', update)
        self.assertLess(update.index('NEWS_PASS_LOCKED'), update.index('queries.update_post('))
        create = routes[routes.index('def news_post_create('):routes.index('def news_post_update(')]
        self.assertLess(create.index('queries.set_passes('), create.index('_launch('))

    def test_report_counts_passes_over_the_same_people(self):
        """«Прошли 9» и «подтвердили 12 из 30» считаются по ОДНИМ людям.

        Считает их одна чистая функция (access.report_summary), и знаменатель у
        всех счётчиков один — нынешние адресаты. Раньше это была арифметика
        внутри роута; с выгрузкой в Excel у неё стало два читателя, и разойтись
        им нельзя.
        """
        summary = _function_code(_read('news', 'access.py'), 'report_summary')
        self.assertIn("addressed = [row for row in rows if row.get('in_audience')]", summary)
        for key in ('confirmed', 'quiz_passed', 'trainer_passed', 'quiz_failed'):
            self.assertIn("for row in addressed", summary)
            self.assertIn("'%s':" % key, summary)
        # Экран и файл зовут её одну — своей арифметики у роутов нет.
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('news_access.report_summary(', routes)
        self.assertNotIn("for row in addressed", routes)


class NewsPassFrontendTests(unittest.TestCase):
    """Задача #342 во фронте: окно, лента и форма."""

    MODAL = os.path.join('src', 'components', 'news', 'NewsOfDayModal.jsx')
    PASSES = os.path.join('src', 'components', 'news', 'NewsPasses.jsx')
    FEED = os.path.join('src', 'components', 'news', 'NewsFeed.jsx')
    TAB = os.path.join('src', 'components', 'wiki', 'WikiNews.jsx')

    def test_window_waits_for_a_required_trainer_but_not_an_optional_one(self):
        modal = _jsx_code_only(_read(self.MODAL))
        self.assertIn("const mustPass = !!current?.pass_required && (quiz.length > 0 || !!current?.trainer_key);",
                      modal)
        self.assertIn('!trainerLeft && (!quizLeft || quizAnswered)', modal)
        self.assertIn("'Пройдите тренажёр'", modal)
        self.assertIn('NEWS_TRAINER_PENDING', modal)
        # «Проверить» — только у необязательного: обязательный сверяет подтверждение.
        self.assertIn('checkable={!mustPass}', modal)

    def test_escape_belongs_to_the_trainer_while_it_is_open(self):
        """Слушатели окна и урока срабатывают на одно нажатие Esc — без проверки
        закрылись бы оба слоя, вместе с необязательной новостью."""
        modal = _jsx_code_only(_read(self.MODAL))
        self.assertIn('if (!current || current.is_mandatory || trainerOpen) return undefined;', modal)
        passes = _read(self.PASSES)
        # «Назад» на телефоне снимает урок: своя запись в стеке поверх записи окна.
        self.assertIn('useScreenBackGesture(isMobileShell && open, onClose);', passes)

    def test_trainer_stands_above_the_window(self):
        passes = _read(self.PASSES)
        self.assertIn('layer="top"', _read(self.MODAL))
        self.assertIn('layer={layer}', passes)
        css = _read('src', 'components', 'wiki', 'trainers', 'trainer.css')
        self.assertIn('.wt-overlay--top { z-index: 130; }', css)
        player = _read('src', 'components', 'wiki', 'trainers', 'TrainerPlayer.jsx')
        self.assertIn('onFinishedRef.current?.();', player)

    def test_trainer_and_registry_are_not_in_the_main_chunk(self):
        """Окно смонтировано в корне портала — сценарии едут только к тем, кому
        тренажёр прикреплён."""
        passes = _jsx_code_only(_read(self.PASSES))
        self.assertIn("lazy(() => import('../wiki/trainers/TrainerPlayer'))", passes)
        self.assertIn("import('../wiki/trainers/registry')", passes)
        self.assertNotIn("from '../wiki/trainers/registry'", passes)
        self.assertNotIn('trainers/', _jsx_code_only(_read(self.MODAL)))

    def test_one_block_serves_the_window_and_the_feed(self):
        for path in (self.MODAL, self.FEED):
            self.assertIn("import NewsPasses, { useQuizAttempt } from './NewsPasses';",
                          _read(path), path)
        passes = _jsx_code_only(_read(self.PASSES))
        self.assertIn('/api/news/${post.id}/quiz', passes)
        self.assertIn('/api/news/${post.id}/trainer', passes)

    def test_a_wrong_answer_resets_the_whole_quiz(self):
        """Правило владельца (21.09.2026) дословно: «если один вариант не
        правилен, ответы сбрасываются и выходит уведомление о том что ответы не
        правильные и попробовать заново, тест будет завершен если он все ответы
        выберет корректно»."""
        passes = _jsx_code_only(_read(self.PASSES))
        # Попытка сбрасывается ЦЕЛИКОМ; итог (сколько верных из скольких) при
        # этом запоминается — он нужен мягкому порогу (ТЗ #300, п.4).
        self.assertIn('setAnswers({});', passes)
        self.assertIn('setFailed(true);', passes)
        self.assertIn('Ответы неверные — выбор сброшен, пройдите тест заново', passes)
        self.assertIn('attempt.fail(e.response.data);', passes)
        # Пометки «вот этот вопрос неверный» не осталось: сервер её и не шлёт.
        for forbidden in ('Неверно — перечитайте новость', 'wrong', 'rose-50 text-rose-900'):
            self.assertNotIn(forbidden, passes, forbidden)

    def test_the_attempt_is_one_for_the_window_and_the_feed(self):
        """Правило «неверно — начинай заново» одно на оба места: своя копия в
        окне и в ленте разъехалась бы, а журнал прохождений у редактора один."""
        passes = _jsx_code_only(_read(self.PASSES))
        self.assertIn('export function useQuizAttempt(postId)', passes)
        for path in (self.MODAL, self.FEED):
            code = _jsx_code_only(_read(path))
            self.assertIn("import NewsPasses, { useQuizAttempt } from './NewsPasses';", code, path)
            self.assertIn('attempt={attempt}', code, path)
            # Своего состояния ответов у места больше нет.
            self.assertNotIn('setAnswers(', code, path)

    def test_feed_shows_nothing_about_the_author(self):
        """Как и окно: сотрудник читает объявление, а не карточку автора."""
        feed = _jsx_code_only(_read(self.FEED))
        for forbidden in ('author_name', 'author_role', 'author_department', 'initialsOf'):
            self.assertNotIn(forbidden, feed, forbidden)

    def test_form_sends_trainer_and_requirement_under_the_quiz_lock(self):
        tab = _jsx_code_only(_read(self.TAB))
        lock = tab[tab.index('...(quizLocked ? {} : {'):]
        lock = lock[:lock.index('}),')]
        self.assertIn('trainer_key: trainerKey', lock)
        # Обязательность прохождения считается из ТИПА новости (ТЗ #300, п.5):
        # у информационной и критичной её решает тип, у важной — автор.
        self.assertIn('pass_required: passEffective', lock)
        self.assertIn('is_mandatory: mandatory || mustPass', tab)
        self.assertIn('const passEffective = rule.passRequired === null', tab)
        # Список тренажёров — из реестра, а не своим перечнем.
        self.assertIn("import { TRAINER_CARDS } from './trainers/registry';", _read(self.TAB))


class NewsSpaceTests(unittest.TestCase):
    """Пространство новости (решение владельца 18.09.2026).

    Дословно: «сделай чтобы по пространствам новости таксопарки и тез не
    смешивались». Граница третья — рядом с потолком должности и границей
    отдела, — и держится она в четырёх местах сразу: в шаблоне адресата, в
    списке редактора, в справочниках формы и в проверке набора адресатов.
    """

    SUPER = dict(ceiling=wiki_access.ROLE_LEVELS['super_admin'], departments=None)

    def test_the_boundary_stands_in_both_halves_of_one_template(self):
        """Окно и журнал считают адресатов ОДНИМ правилом — вместе с границей.

        Разъедься они, «подтвердили 12 из 30» считалось бы не по тем тридцати,
        кому объявление показали, — и журнал перестал бы отвечать на вопрос,
        ради которого существует.
        """
        source = _read('news', 'access.py')
        self.assertEqual(source.count('SPACE_MATCH_TEMPLATE = """'), 1)
        for form in (news_access.AUDIENCE_MATCH_FOR_VIEWER_IN_SPACE,
                     news_access.AUDIENCE_MATCH_FOR_REPORT_IN_SPACE):
            self.assertIn('wiki_space_departments', form)
            self.assertIn('p.space_id', form)
        # И ровно эти два выбирает селектор — второго способа собрать условие
        # нет: разойдясь, они дали бы окно без границы, а журнал с ней.
        self.assertIs(news_access.viewer_match(True),
                      news_access.AUDIENCE_MATCH_FOR_VIEWER_IN_SPACE)
        self.assertIs(news_access.report_match(True),
                      news_access.AUDIENCE_MATCH_FOR_REPORT_IN_SPACE)
        self.assertIs(news_access.viewer_match(False),
                      news_access.AUDIENCE_MATCH_FOR_VIEWER)
        self.assertIs(news_access.report_match(False),
                      news_access.AUDIENCE_MATCH_FOR_REPORT)

    def test_a_news_without_a_space_is_not_lost(self):
        """Ничья новость видна отовсюду, а не ниоткуда.

        Так лежат объявления, выпущенные до этой границы. Спрячь мы их — у
        обязательного окна не осталось бы ни одного редактора, который вправе
        его снять, и оно висело бы у людей навсегда.
        """
        self.assertIn('p.space_id IS NULL', news_access.SPACE_MATCH_TEMPLATE)
        queries = _read('news', 'queries.py')
        space_filter = queries[queries.index('def _space_filter('):]
        space_filter = space_filter[:space_filter.index('\ndef ', 10)]
        self.assertIn('p.space_id IS NULL', space_filter)
        self.assertIn('%(space)s::int IS NULL', space_filter)

    def test_a_space_without_departments_narrows_nothing(self):
        """Пространство, не назвавшее отделов, ничего про своих людей не сказало.

        То же соглашение, что у вики (wiki/queries.py: _SPACE_GATE_SQL): иначе
        полунастроенное пространство отрезало бы своих же читателей от окна.

        И справочник формы обязан читать пустоту ТАК ЖЕ: иначе в новом
        пространстве он не предложил бы ни одного адресата, а новость оттуда
        всё равно дошла бы до всех.
        """
        self.assertIn('NOT EXISTS', news_access.SPACE_MATCH_TEMPLATE)
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('queries.space_departments(cursor, space_id) or None', routes)

    def test_a_subject_from_another_space_is_refused(self):
        """Форма сужена справочником, но правило живёт и на сервере.

        Правило, живущее только во фронте, держится до первого запроса мимо
        него — а здесь мимо него уезжает объявление чужой компании.
        """
        refusal = news_access.audience_refusal(
            [{'subject_type': 'department', 'subject_id': 560}],
            subject_departments={('department', 560): 560},
            space_departments=[1, 367], **self.SUPER)
        self.assertIn('другого пространства', refusal or '')
        # Свой отдел того же пространства проходит.
        self.assertIsNone(news_access.audience_refusal(
            [{'subject_type': 'department', 'subject_id': 367}],
            subject_departments={('department', 367): 367},
            space_departments=[1, 367], **self.SUPER))

    def test_a_role_is_not_cut_by_the_space_but_by_the_window(self):
        """Должность адресует людей по всей компании — её режет показ, а не форма.

        Запрети мы её в пространстве, у директора пропало бы «всем операторам»
        вовсе. Сужает такую новость сама граница показа: из «Тез» она дойдёт до
        операторов Тез КЦ, из «Таксопарков» — до операторов Таксопарков.
        """
        self.assertIsNone(news_access.audience_refusal(
            [{'subject_type': 'otp_role', 'subject_role': 'operator'}],
            subject_departments={}, space_departments=[560], **self.SUPER))

    def test_catalogs_add_the_two_boundaries_instead_of_choosing_one(self):
        """«Чей это человек» и «чьей компании вика» — разные вопросы.

        Складывает их вика, одной функцией (narrow_to_space). Вторая, своя,
        однажды разошлась бы с первой молча.
        """
        queries = _read('news', 'queries.py')
        catalog = queries[queries.index('def subject_catalog('):]
        catalog = catalog[:catalog.index('\ndef ', 10)]
        self.assertIn('wiki_structure.narrow_to_space(department_ids, space_department_ids)',
                      catalog)
        people = queries[queries.index('def targetable_people('):]
        people = people[:people.index('\ndef ', 10)]
        self.assertIn('space_department_ids=space_department_ids', people)

    def test_the_foreign_table_never_reaches_a_query_unguarded(self):
        """wiki_space_departments приносит ЧУЖОЙ пакет.

        Сорвись миграция вики — упоминание несуществующей таблицы валит запрос
        на разборе, то есть окно новости пропало бы у всех вошедших в портал.
        Ради этого пакет news/ и вынесен из wiki/, поэтому каждая выборка
        спрашивает защёлку, а не таблицу.
        """
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('def _space_ready(cursor):', routes)
        # Ни одна выборка не берёт границу мимо защёлки.
        for call in ('with_space=_space_ready(cursor)',
                     'space_id=_request_space(cursor)'):
            self.assertIn(call, routes)
        self.assertNotIn('with_space=True', routes)
        schema = _read('news', 'schema.py')
        ready = schema[schema.index('def space_ready('):]
        ready = ready[:ready.index('\ndef ', 10)]
        self.assertIn("to_regclass('public.wiki_space_departments')", ready)
        self.assertIn("column_name = 'space_id'", ready)

    def test_an_orphan_news_finds_its_space_by_the_author_department(self):
        """Новости, выпущенные до колонки, разбираются отделом автора.

        Другого следа, из какой вики выпущено старое объявление, в базе нет, и
        он честный: список редактора и так стоял на отделе автора.
        """
        schema = _read('news', 'schema.py')
        backfill = schema[schema.index('def backfill_space_ids('):]
        backfill = backfill[:backfill.index('\ndef ', 10)]
        self.assertIn('p.author_department_id', backfill)
        self.assertIn('WHERE p.space_id IS NULL', backfill)
        self.assertIn('if not space_ready(cursor):', backfill)
        # Бэкфилл живёт под своим SAVEPOINT'ом: он читает таблицу вики, и её
        # сбой не должен уносить развёрнутые таблицы раздела.
        db = _read('database.py')
        self.assertIn('SAVEPOINT news_spaces_backfill', db)

    def test_a_question_becomes_the_news_of_its_own_space(self):
        """Новость из «Вопросов операторов» (#321) — вики отдела адресата.

        Вопрос задал оператор конкретной компании, и объявление с ответом
        принадлежит её вике: иначе оно осталось бы ничьим и встало в список
        обеих.
        """
        source = _code_only(_read('wiki', 'routes_questions.py'))
        self.assertIn("space_of_department(cursor, item['department_id'])", source)

    def test_the_space_of_a_news_never_changes_by_an_edit(self):
        """Переезд объявления в соседнюю вику — это другой круг адресатов.

        У опубликованной новости он уже показан людям и посчитан в журнале,
        поэтому правка пространство не трогает, а проверяет адресатов по
        ПРОСТРАНСТВУ САМОЙ НОВОСТИ, а не по вкладке, из которой пришёл запрос.
        """
        routes = _code_only(_read('news', 'routes.py'))
        self.assertEqual(routes.count("_audience_refusal(cursor, ctx, rules, space_id=post.get('space_id'))"), 2)
        self.assertIn('space_id=space_id, with_space=_space_ready(cursor),', routes)
        update = routes[routes.index('def news_post_update('):]
        update = update[:update.index('\n    @news_route')]
        self.assertNotIn('space_id=%', update)

    def test_the_tab_asks_for_the_space_it_is_open_in(self):
        """Список, справочники и лента — все три спрашивают пространство.

        Забудь любой из них — и вкладка в «Тез» показывала бы таксопарковые
        объявления, предлагала бы чужие отделы или выдавала бы человеку ленту
        соседней компании.
        """
        tab = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        self.assertIn('params: { status: bucket, space_id: spaceId }', tab)
        self.assertIn('params: { space_id: spaceId }', tab)
        self.assertIn('{ ...payload, space_id: spaceId }', tab)
        # Пространство в зависимостях загрузчиков: переключили вику — список и
        # справочники перечитываются, а не остаются от соседней.
        self.assertIn('[apiBaseUrl, headers, bucket, canPublish, spaceId]', tab)
        self.assertIn('[apiBaseUrl, headers, spaceId]', tab)
        feed = _jsx_code_only(_read('src', 'components', 'news', 'NewsFeed.jsx'))
        self.assertIn('space_id: spaceId', feed)
        self.assertIn('[apiBaseUrl, headers, spaceId]', feed)
        view = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiView.jsx'))
        news_tab = view[view.index('<WikiNews'):]
        news_tab = news_tab[:news_tab.index('/>')]
        self.assertIn('spaceId={activeSpace?.id || null}', news_tab)

    def test_the_window_outside_the_wiki_asks_for_no_space(self):
        """Окно стоит вне вики, и пространства у него нет.

        Человеку показывают всё, что адресовано ЕМУ; границу здесь держит сама
        новость, а не вкладка. Появись у окна параметр пространства — он был бы
        взят с потолка, потому что спрашивать его в корне портала не у кого.
        """
        modal = _jsx_code_only(_read('src', 'components', 'news', 'NewsOfDayModal.jsx'))
        self.assertNotIn('space_id', modal)
        self.assertNotIn('spaceId', modal)


class NewsChannelTests(unittest.TestCase):
    """Куда отправлено объявление (решение владельца 18.09.2026).

    Дословно: «в редакторе новости добавить „отправить в iCORE или Oktell“, по
    умолчанию в iCORE; в Тез пока будет только вариант с iCORE, и выбора там
    делать не нужно».

    Канал — не украшение списка: он решает, КТО и ГДЕ увидит объявление. Одно и
    то же окно в двух местах означало бы два подтверждения одной новости и два
    прохождения одного теста, а журнал «Кто прочитал» у неё один.
    """

    def test_the_default_is_the_portal(self):
        """Портал видят все, программа стоит у части операторов. Незнакомое
        значение — тоже портал: форма старого бандла канала не присылает."""
        self.assertEqual(news_access.DEFAULT_CHANNEL, 'icore')
        self.assertEqual(news_access.CHANNELS[0], 'icore')
        self.assertEqual(news_access.normalize_channel(None), 'icore')
        self.assertEqual(news_access.normalize_channel('телеграм'), 'icore')
        self.assertEqual(news_access.normalize_channel('OKTELL'), 'oktell')
        # Умолчание подменяется: у правки это прежний канал карточки, иначе
        # сохранение заголовка переносило бы объявление в другое окно.
        self.assertEqual(news_access.normalize_channel(None, default='oktell'), 'oktell')

    def test_oktell_is_offered_only_where_the_program_stands(self):
        """В «Тез» программы нет — и выбора там нет тоже: переключатель с
        единственной кнопкой это не выбор, а лишний вопрос на экране."""
        self.assertEqual(news_access.channels_for_departments(['szov', 'op']),
                         ['icore', 'oktell'])
        self.assertEqual(news_access.channels_for_departments(['tez']), ['icore'])
        # Пространство без отделов ничего про своих людей не сказало.
        self.assertEqual(news_access.channels_for_departments([]), ['icore'])
        self.assertEqual(news_access.channels_for_departments(None), ['icore'])

    def test_the_perimeter_is_taken_from_the_program_not_copied(self):
        """Периметр «Ограничителя Перезвона» — один на портал. Вторая копия
        кода отдела разъехалась бы молча, и форма предлагала бы Oktell там, где
        программы нет, — объявление не увидел бы никто."""
        from oktell_guard import access as guard_access
        self.assertEqual(news_access.OKTELL_DEPARTMENT_CODE,
                         guard_access.SECTION_DEPARTMENT_CODE)
        source = _code_only(_read('news', 'access.py'))
        self.assertIn('from oktell_guard.access import SECTION_DEPARTMENT_CODE',
                      _read('news', 'access.py'))
        self.assertNotIn("'szov'", source)

    def test_each_window_asks_for_its_own_channel(self):
        """Портал спрашивает своё, программа — своё. Спроси кто-нибудь «всё»,
        человек подтвердил бы одну новость дважды, в двух окнах."""
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('channel=(news_access.DEFAULT_CHANNEL if _channel_ready(cursor)'
                      ' else None)', routes)
        guard = _code_only(_read('oktell_guard', 'routes.py'))
        self.assertIn("channel=('oktell'", guard)

    def test_the_queue_is_not_filtered_by_itself(self):
        """Канал выбирает СПРАШИВАЮЩИЙ. Поставь мы умолчание в самой выдаче —
        объявление для АТС молча уехало бы в портал у всякого, кто забыл
        назвать канал."""
        queries_src = _read('news', 'queries.py')
        signature = queries_src[queries_src.index('def pending_for_user('):]
        signature = signature[:signature.index(')')]
        self.assertIn('channel=None', signature)
        body = queries_src[queries_src.index('def _channel_filter('):]
        body = body[:body.index('\ndef ', 10)]
        self.assertIn('if not channel:', body)
        self.assertIn('AND p.channel = %(channel)s', body)

    def test_the_server_checks_the_channel_itself(self):
        """Правило, живущее только во фронте, держится до первого запроса мимо
        него: форма в «Тез» про Oktell не спрашивает, но запрос туда дойти
        может."""
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('NEWS_CHANNEL_UNAVAILABLE', routes)
        self.assertIn('channel not in _channels_for_space(cursor, space_id)', routes)
        # И то же самое считается по отделам ПРОСТРАНСТВА, а не по своим.
        self.assertIn('news_access.channels_for_departments(', routes)

    def test_a_published_news_does_not_change_its_channel(self):
        """Объявление уже показано людям там, куда его отправили, и часть
        отдела подтвердила его в том окне. Нужен другой канал — публикуется
        новая новость; тем же замком заперты тест и обязательность."""
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('NEWS_CHANNEL_LOCKED', routes)
        self.assertIn("post['published_at'] and channel != (post.get('channel')", routes)

    def test_the_column_can_be_missing(self):
        """Деплой, на котором код приехал, а DDL ещё не отработал, обязан
        работать как вчера — отправкой в портал, а не пятисоткой каждому
        вошедшему."""
        schema_src = _read('news', 'schema.py')
        self.assertIn("channel VARCHAR(16) NOT NULL DEFAULT 'icore'", schema_src)
        self.assertTrue(hasattr(news_schema, 'channel_ready'))
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('with_channel=_channel_ready(cursor)', routes)
        self.assertIn('NEWS_CHANNEL_NOT_READY', routes)

    def test_the_form_asks_only_where_there_is_a_choice(self):
        """«В Тез выбора делать не нужно»: строки нет вовсе, а не серая
        кнопка, на которую нельзя нажать."""
        form = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        self.assertIn('channelOptions.length > 1 && (', form)
        self.assertIn("access?.channels || [NEWS_CHANNELS[0].value]", form)
        self.assertIn('channels.includes(item.value)', form)
        # Значения переключателя — те же, что знает сервер.
        values = re.findall(r"\{ value: '(\w+)', label: '[^']+' \}", form)
        self.assertEqual([v for v in values if v in news_access.CHANNELS],
                         list(news_access.CHANNELS))

    def test_the_form_sends_the_channel_and_locks_it_after_publishing(self):
        form = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        self.assertIn('            channel,', form)
        self.assertIn("setChannel(post?.channel || NEWS_CHANNELS[0].value)", form)
        self.assertIn('const channelLocked = quizLocked;', form)
        # У выпущенной новости канал не листается: сервер его всё равно не примет.
        self.assertIn('disabled={channelLocked}', form)


class NewsExpiryTests(unittest.TestCase):
    """«Действует до» — поле, на котором 18.09.2026 молча сгорели четыре
    объявления подряд (#7–#10): выпущенные вечером, они истекли за 22 часа до
    собственной публикации и не показались никому и нигде. Искали причину в
    агенте, в канале доставки и в правах — а она была в дате."""

    @staticmethod
    def _timestamp_parser():
        """Достаём _timestamp_or_none из замыкания фабрики и выполняем как есть.

        Ходить ради него в базу и поднимать Flask незачем: функция чистая, а
        читать её глазами — ровно то, из-за чего дефект и дожил до прода.
        """
        from datetime import datetime as real_datetime

        tree = ast.parse(_read('news', 'routes.py'))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == '_timestamp_or_none':
                namespace = {'datetime': real_datetime}
                exec(compile(ast.Module([node], []), '<timestamp>', 'exec'), namespace)
                return namespace['_timestamp_or_none']
        raise AssertionError('_timestamp_or_none не найден в news/routes.py')

    def test_a_bare_date_means_the_whole_day_not_its_first_second(self):
        """Поле `datetime-local`, в котором тронули только дату, отдаёт 00:00 —
        то есть НАЧАЛО дня. «Действует до 18.09» человек читает как «весь 18-е»."""
        parse = self._timestamp_parser()
        self.assertEqual(str(parse('2026-09-18T00:00')), '2026-09-18 23:59:59')
        self.assertEqual(str(parse('2026-09-18')), '2026-09-18 23:59:59')

    def test_a_time_typed_on_purpose_is_left_alone(self):
        parse = self._timestamp_parser()
        self.assertEqual(str(parse('2026-09-18T15:30')), '2026-09-18 15:30:00')
        self.assertEqual(str(parse('2026-09-18T00:01')), '2026-09-18 00:01:00')
        self.assertIsNone(parse(''))
        self.assertIsNone(parse('не дата'))

    @staticmethod
    def _expiry_parser():
        """_expiry_refusal из замыкания фабрики — тем же приёмом, что выше."""
        from datetime import datetime as real_datetime

        tree = ast.parse(_read('news', 'routes.py'))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == '_expiry_refusal':
                namespace = {'datetime': real_datetime}
                exec(compile(ast.Module([node], []), '<expiry>', 'exec'), namespace)
                return namespace['_expiry_refusal']
        raise AssertionError('_expiry_refusal не найден в news/routes.py')

    def test_the_check_survives_what_the_card_actually_gives_it(self):
        """Срок приходит СТРОКОЙ, и проверка обязана это пережить.

        Оба места, откуда её зовут, берут значение у queries.get_post, а тот
        отдаёт карточку — готовый JSON, где время сериализовано. Сравнение
        строки с datetime роняло публикацию КАЖДОГО объявления с заполненным
        «действует до»: 500 и «внутренняя ошибка» вместо выпуска. Поймано на
        проде 18.09.2026, через полчаса после выкладки.
        """
        from datetime import datetime, timedelta

        self.assertIn("'expires_at': row[7].isoformat() if row[7] else None",
                      _read('news', 'queries.py'))
        refusal = self._expiry_parser()
        future = (datetime.now() + timedelta(days=3)).replace(microsecond=0)
        past = (datetime.now() - timedelta(days=1)).replace(microsecond=0)
        # Так значение и приезжает — строкой из карточки.
        self.assertIsNone(refusal(future.isoformat(), True))
        self.assertIn('истёк', refusal(past.isoformat(), True))
        # И объектом, если однажды позовут до сериализации.
        self.assertIsNone(refusal(future, True))
        self.assertIn('истёк', refusal(past, True))
        # Пустое поле и черновик — не отказ.
        self.assertIsNone(refusal(None, True))
        self.assertIsNone(refusal('', True))
        self.assertIsNone(refusal(past.isoformat(), False))
        # Непонятная дата не должна запирать выпуск: срок проверяет и выдача.
        self.assertIsNone(refusal('не дата', True))

    def test_publishing_something_already_expired_is_refused(self):
        """Просроченное объявление не видит НИКТО и НИГДЕ, а в списке оно стоит
        «опубликовано». Тишина неотличима от поломки — отказываем словами."""
        source = _read('news', 'routes.py')
        self.assertIn('def _expiry_refusal(', source)
        self.assertIn('NEWS_EXPIRED', source)
        # Обе двери публикации: и создание с publish=true, и отдельная кнопка.
        self.assertEqual(source.count('"code": "NEWS_EXPIRED"'), 2)
        body = source.split('def _expiry_refusal(', 1)[1].split('\n    def ', 1)[0]
        # Пустое поле — «показывать без срока», а не отказ. Пустая СТРОКА тоже:
        # карточка отдаёт срок сериализованным, и её пустота выглядит так.
        self.assertIn("if not publishing or expires_at in (None, ''):", body)

    def test_a_draft_may_keep_any_date(self):
        """Отказ только на публикации: черновику дата в прошлом не мешает —
        автор как раз и сохраняет его, чтобы её поправить."""
        source = _read('news', 'routes.py')
        body = source.split('def _expiry_refusal(', 1)[1].split('\n    def ', 1)[0]
        self.assertIn('not publishing', body)


class NewsOktellSipWarningTests(unittest.TestCase):
    """Предупреждение «у кого нет SIP-номера» (решение владельца 18.09.2026).

    Дословно: «если в разделе настройки SIP не имеется данных Oktell хотя бы у
    одного человека из отмеченных для отправки новости — уведомление, рядом с
    переключателем кнопка предупреждения, при нажатии плавный переход и видно,
    у кого именно нет номера и как его ввести».

    Смысл: объявление канала Oktell рисует программа поверх клиента АТС. Кто в
    АТС не заведён, тот его не увидит — и узнать об этом надо ДО публикации, а
    не через неделю по жалобе.
    """

    def test_the_audience_is_not_counted_a_second_way(self):
        """Правила адресата не переписаны: предупреждение и показ обязаны
        считать одних и тех же людей. Несохранённые правила формы подставлены
        CTE поверх таблиц — имя CTE перекрывает таблицу внутри запроса."""
        source = _read('news', 'queries.py')
        body = source[source.index('def audience_sip_check('):]
        body = body[:body.index('\ndef ', 10)]
        self.assertIn('WITH news_posts AS (', body)
        self.assertIn('news_audience_rules AS (', body)
        self.assertIn('jsonb_to_recordset(%(rules)s::jsonb)', body)
        self.assertIn('report_match(with_space)', body)
        # Своего правила «кому уйдёт» здесь нет ни одного.
        self.assertNotIn("subject_type = 'department'", body)

    def test_the_check_is_about_the_sip_number(self):
        """«Данные Oktell» у человека — это его SIP-номер в разделе
        «Настройки SIP»: без номера он в АТС не работает."""
        source = _read('news', 'queries.py')
        body = source[source.index('def audience_sip_check('):]
        body = body[:body.index('\ndef ', 10)]
        self.assertIn("NULLIF(btrim(COALESCE(u.sip_number, '')), '') IS NOT NULL", body)

    def test_the_route_keeps_the_same_perimeter(self):
        """Иначе ручка стала бы способом перебрать чужих людей по id: правила
        приходят из формы, а форма — это ещё не право их адресовать."""
        routes = _code_only(_read('news', 'routes.py'))
        body = routes[routes.index('def news_audience_oktell('):]
        body = body[:body.index('@news_route', 10)]
        self.assertIn('_audience_refusal(cursor, ctx, rules, space_id=space_id)', body)
        self.assertIn('audience_max_role_level=ctx[\'ceiling\']', body)
        # Пустой набор адресатов — не ошибка, а «спрашивать не о ком».
        self.assertIn('if not rules:', body)

    def test_the_list_is_capped_but_the_count_is_not(self):
        """В панели читают первые имена, а решение принимают по числу: двести
        строк в ней — это не ответ, а новый вопрос."""
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('missing[:SIP_MISSING_LIMIT]', routes)
        self.assertIn('"missing_count": len(missing)', routes)

    def test_the_form_asks_only_for_oktell(self):
        """В портале SIP-номер ни при чём. Запрос «на всякий случай» на каждый
        щелчок по справочнику адресата был бы обращением к базе ради ответа,
        который никто не спросит."""
        form = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        self.assertIn("if (!open || channel !== 'oktell' || !audienceKey)", form)
        self.assertIn('/api/news/audience/oktell', form)
        # Пауза и отмена прошлого ответа: адресатов набирают по одному.
        self.assertIn('}, 400);', form)
        self.assertIn('if (alive) setSipCheck', form)

    def test_the_warning_button_stands_next_to_the_switch(self):
        """Предупреждение относится к одной кнопке «Oktell»: отдельная красная
        строка внизу карточки читалась бы как ошибка всей формы."""
        form = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        row = form[form.index('label="Куда отправить"'):]
        row = row[:row.index('</PickerRow>')]
        self.assertIn('badge={sipMissing > 0 ? (', row)
        self.assertIn('aria-label="Кто не увидит объявление в Oktell"', row)
        self.assertIn('aria-expanded={sipOpen}', row)
        # Листалка значений — тот самый «переключатель», рядом с которым стоит
        # предупреждение (решение владельца 21.09.2026: выбирать пролистыванием).
        self.assertIn('options={channelOptions}', row)

    def test_the_panel_opens_smoothly_and_without_a_second_modal(self):
        """Вторая модалка поверх формы — это два окна об одной новости.
        Плавность даёт сетка 0fr → 1fr: высота считается по содержимому, и три
        имени раскрываются так же ровно, как тридцать."""
        form = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        row = form[form.index('label="Куда отправить"'):]
        row = row[:row.index('</PickerRow>')]
        self.assertIn('grid transition-all duration-300 ease-out', row)
        self.assertIn('grid-rows-[1fr]', row)
        self.assertIn('grid-rows-[0fr]', row)
        self.assertNotIn('IosModal', row)

    def test_the_form_is_one_window_with_screens_inside(self):
        """Решение владельца 21.09.2026: «модалка слишком перегружена… убрать
        двойную модалку».

        Открытым на главном экране остаётся только то, ради чего новость и
        пишут, — заголовок, текст и кадры. Остальное свёрнуто в строки со
        значением справа, а второй уровень открывается ТУТ ЖЕ, сдвигом, и
        возвращается шевроном: окно поверх окна — это два окна об одной
        новости, и «закрыть» у них означает разное.
        """
        code = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        form = code[code.index('function NewsForm('):code.index('function NewsReport(')]
        # Одно окно на всю форму, ни одного вложенного.
        self.assertEqual(form.count('<IosModal'), 1)
        # Выбор адресата и тренажёра — экраны, а не окна.
        for name in ('function AudienceScreen(', 'function TrainerScreen('):
            self.assertIn(name, code, name)
        for gone in ('function AudiencePicker(', 'function TrainerPicker(',
                     'pickerOpen', 'trainerPickerOpen'):
            self.assertNotIn(gone, code, gone)
        # Шеврон «назад» ведёт на предыдущий уровень, а не наружу.
        self.assertIn("onBack={screen === 'main' ? null : back}", form)
        self.assertIn("setScreen(screen === 'trainer' ? 'passes' : 'main')", form)
        # Кнопки формы — только на главном экране.
        self.assertIn("footer={screen !== 'main' ? null : (", form)
        # Каждая настройка — строка со значением справа.
        for label in ('label="Тип"', 'label="Кому"', 'label="Куда отправить"',
                      'label="Тест и тренажёр"', 'label="Публикация и показ"'):
            self.assertIn(label, form, label)
        self.assertIn('function SettingRow(', code)

    def test_short_lists_are_flipped_in_place_not_on_a_screen(self):
        """Решение владельца 21.09.2026: «тип новости и кнопку куда отправить
        сделать как в айос со стрелками, чтобы там же просто пролистывать и
        выбрать, то есть это как галерея фоток».

        У типа три значения, у канала два — отдельный экран ради них был бы
        шагом туда и обратно. Как в галерее, листалка останавливается на краях:
        «Информационная» после «Критичной» означала бы список без начала и
        конца.
        """
        code = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        form = code[code.index('function NewsForm('):code.index('function NewsReport(')]
        self.assertIn('function PickerRow(', code)
        # Обе короткие настройки — листалки, и своих экранов у них больше нет.
        self.assertEqual(form.count('<PickerRow'), 2)
        for gone in ("push('kind')", "push('channel')", "screen === 'kind'",
                     "screen === 'channel'"):
            self.assertNotIn(gone, form, gone)
        picker = code[code.index('function PickerRow('):code.index('function SettingRow(')]
        # Края не заворачиваются по кругу.
        self.assertIn('if (disabled || next < 0 || next >= options.length) return;', picker)
        # Пальцем листается тем же движением, что кадры в галерее.
        self.assertIn('onTouchStart', picker)
        self.assertIn('onTouchEnd', picker)
        # Значение приезжает с той стороны, в которую листают.
        self.assertIn("forward ? 'animate-push-in' : 'animate-pop-in'", picker)

    def test_one_person_gets_the_instruction_not_a_list(self):
        """«Если один сотрудник — просто инструкция, как добавить номер»:
        перечень из одной строки под фразой «1 из 24» повторял бы сам себя."""
        form = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        self.assertIn('const sipOnly = sipMissing === 1 ?', form)
        self.assertIn('{sipMissing > 1 && (', form)
        self.assertIn('Как добавить номер', form)
        self.assertIn('«Настройки SIP»', form)
        self.assertIn('«SIP-номер»', form)


if __name__ == '__main__':
    unittest.main()


def _function(source, name):
    """Тело функции от её `def` до следующей на том же уровне."""
    start = source.index('def %s(' % name)
    rest = source[start:]
    end = rest.find('\ndef ')
    return rest if end < 0 else rest[:end]


def _function_code(source, name):
    """То же, но БЕЗ строки документации.

    _code_only здесь не годится: он снимает любые тройные кавычки, а SQL в этом
    модуле живёт как раз в них — и проверять было бы нечего.
    """
    body = _function(source, name)
    if '"""' in body:
        body = body.split('"""', 2)[-1]
    return body


class NewsKindTests(unittest.TestCase):
    """ТЗ #300, п.5: тип новости вместо двух несвязанных тумблеров.

    Два тумблера («обязательно к прочтению» и «пройти обязательно») отвечали на
    один вопрос порознь и позволяли собрать бессмысленное — необязательную
    новость с обязательным тестом — и опасное: критичное изменение, которое
    закрывают крестиком.
    """

    def test_the_table_from_the_spec_is_the_rule(self):
        self.assertEqual(news_schema.NEWS_KINDS, ('info', 'important', 'critical'))
        # Информационная: окно закрывается крестиком, прохождение не держит.
        self.assertEqual(news_access.kind_flags('info', pass_required=True), (False, False))
        # Важная: ознакомление обязательное, прохождение «настраиваемое».
        self.assertEqual(news_access.kind_flags('important', pass_required=False), (True, False))
        self.assertEqual(news_access.kind_flags('important', pass_required=True), (True, True))
        # Критичная: и то, и другое — обязательно, что бы ни прислала форма.
        self.assertEqual(news_access.kind_flags('critical', pass_required=False), (True, True))

    def test_critical_without_a_quiz_is_refused(self):
        """Критичная без теста — это важная, названная критичной.

        Блокировать работу «до успешного прохождения» ей нечем, а молча понизить
        тип нельзя: автор выпустил бы объявление не тем, каким собрал.
        """
        self.assertIsNone(news_access.kind_refusal('critical', has_quiz=True))
        self.assertIsNone(news_access.kind_refusal('important', has_quiz=False))
        self.assertIn('тест', news_access.kind_refusal('critical', has_quiz=False))

    def test_an_unknown_kind_behaves_like_before_the_task(self):
        """Форма старого бандла типа не присылает вовсе."""
        self.assertEqual(news_access.normalize_kind(None), 'important')
        self.assertEqual(news_access.normalize_kind('важная'), 'important')

    def test_derived_kind_repeats_the_backfill_word_for_word(self):
        """Правило «поведение → тип» записано дважды: в DDL и в питоне.

        Бэкфилл проставляет тип старым строкам, kind_of отвечает за строки,
        приехавшие без колонки. Разъедься они — одна и та же новость называлась
        бы в списке по-разному до и после рестарта.
        """
        self.assertEqual(news_access.kind_of(is_mandatory=False, pass_required=True,
                                             has_quiz=True), 'info')
        self.assertEqual(news_access.kind_of(is_mandatory=True, pass_required=True,
                                             has_quiz=True), 'critical')
        self.assertEqual(news_access.kind_of(is_mandatory=True, pass_required=False,
                                             has_quiz=True), 'important')
        self.assertEqual(news_access.kind_of(is_mandatory=True, pass_required=True,
                                             has_quiz=False), 'important')
        backfill = [st for st in news_schema._STATEMENTS if 'SET kind = CASE' in st]
        self.assertEqual(len(backfill), 1)
        self.assertIn("WHEN NOT is_mandatory THEN 'info'", backfill[0])
        self.assertIn("THEN 'critical'", backfill[0])
        self.assertIn("ELSE 'important'", backfill[0])
        # Трогает только строки без типа: иначе он переписывал бы выбор автора
        # на каждом старте процесса.
        self.assertIn('WHERE kind IS NULL', backfill[0])

    def test_the_kind_of_a_published_news_is_locked(self):
        routes = _read('news', 'routes.py')
        update = routes[routes.index('def news_post_update('):
                        routes.index('def news_post_publish(')]
        self.assertIn('NEWS_KIND_LOCKED', update)
        self.assertLess(update.index('NEWS_KIND_LOCKED'), update.index('queries.update_post('))

    def test_the_form_has_no_second_place_for_mandatory(self):
        """Тумблер «обязательно к прочтению» из формы убран целиком.

        Оставь мы его рядом с типом — на один вопрос было бы два ответа, и они
        разошлись бы в первый же день.
        """
        form = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        self.assertNotIn('setMandatory', form)
        self.assertNotIn('Обязательно к прочтению', form)
        for label in ('Информационная', 'Важная', 'Критичная'):
            self.assertIn(label, form)

    def test_the_form_repeats_the_server_rule(self):
        """KIND_RULES формы и KIND_RULES сервера обязаны совпадать.

        Разойдись они — форма показывала бы новость не такой, какой её
        записывает сервер, и молча.
        """
        form = _read('src', 'components', 'wiki', 'WikiNews.jsx')
        block = form[form.index('const KIND_RULES = {'):]
        block = block[:block.index('};')]
        found = re.findall(
            r'(\w+): \{ mandatory: (true|false), passRequired: (true|false|null), '
            r'quizRequired: (true|false) \}', block)
        self.assertEqual({item[0] for item in found}, set(news_access.KIND_RULES))
        words = {'true': True, 'false': False, 'null': None}
        for kind, mandatory, pass_required, quiz_required in found:
            rule = news_access.KIND_RULES[kind]
            self.assertEqual(words[mandatory], rule['mandatory'], kind)
            self.assertEqual(words[pass_required], rule['pass_required'], kind)
            self.assertEqual(words[quiz_required], rule['quiz_required'], kind)


class NewsPassScoreTests(unittest.TestCase):
    """ТЗ #300, п.4: проходной результат теста."""

    KEY = [(1, 0), (2, 1), (3, 0)]
    PERFECT = {'1': 0, '2': 1, '3': 0}
    ONE_WRONG = {'1': 0, '2': 1, '3': 1}

    def test_a_hundred_percent_is_exactly_the_old_rule(self):
        """Умолчание не меняет поведение ни одной прежней новости."""
        self.assertEqual(news_schema.DEFAULT_PASS_SCORE_PERCENT, 100)
        self.assertTrue(news_access.quiz_result(self.KEY, self.PERFECT, 100)['passed'])
        self.assertFalse(news_access.quiz_result(self.KEY, self.ONE_WRONG, 100)['passed'])

    def test_a_softer_threshold_is_counted_in_questions(self):
        result = news_access.quiz_result(self.KEY, self.ONE_WRONG, 60)
        self.assertTrue(result['passed'])
        self.assertEqual((result['correct'], result['total'], result['needed']), (2, 3, 2))
        # Округление вверх: 80% от пяти вопросов — четыре, а не три с хвостом.
        self.assertEqual(news_access.needed_correct(5, 80), 4)
        self.assertEqual(news_access.needed_correct(3, 60), 2)

    def test_a_test_that_passes_an_empty_form_is_impossible(self):
        self.assertEqual(news_access.normalize_pass_score(0), news_schema.MIN_PASS_SCORE_PERCENT)
        self.assertEqual(news_access.normalize_pass_score(500), 100)
        self.assertEqual(news_access.normalize_pass_score('нет'), 100)
        self.assertEqual(news_access.needed_correct(10, 1), 1)
        self.assertEqual(news_access.needed_correct(0, 100), 0)

    def test_the_threshold_of_a_published_news_is_locked(self):
        """Часть отдела уже сдала тест по прежнему порогу."""
        routes = _read('news', 'routes.py')
        update = routes[routes.index('def news_post_update('):
                        routes.index('def news_post_publish(')]
        self.assertIn('NEWS_SCORE_LOCKED', update)
        self.assertLess(update.index('NEWS_SCORE_LOCKED'), update.index('queries.update_post('))

    def test_the_refusal_names_the_score_but_never_the_questions(self):
        """Сколько верных — говорим, какие именно неверны — нет.

        Подсветка вопроса вернула бы подбор ответа переключением одного
        варианта (решение владельца 21.09.2026), а «есть неверные ответы» при
        мягком пороге не объясняет, почему тест не засчитан.
        """
        routes = _read('news', 'routes.py')
        refusal = _function(routes.replace('\n    def ', '\ndef '), '_quiz_refusal')
        self.assertIn('needed', refusal)
        self.assertIn('correct', refusal)
        self.assertNotIn("'wrong'", refusal)
        self.assertNotIn('"wrong"', refusal)
        # И ровно один сборщик на оба места: окно портала и лента.
        self.assertEqual(routes.count('_quiz_refusal(detail)'), 2)

    def test_the_window_shows_the_score(self):
        passes = _jsx_code_only(_read('src', 'components', 'news', 'NewsPasses.jsx'))
        self.assertIn('attempt.score', passes)
        self.assertIn('attempt.fail(e.response.data)', passes)
        modal = _jsx_code_only(_read('src', 'components', 'news', 'NewsOfDayModal.jsx'))
        self.assertIn('attempt.fail(e.response.data)', modal)


class NewsScheduleTests(unittest.TestCase):
    """ТЗ #300, п.8: отложенный запуск и растяжка публикации по волнам."""

    @staticmethod
    def _start():
        from datetime import datetime
        return datetime(2026, 9, 22, 9, 0)

    def test_the_example_from_the_spec(self):
        """«120 операторов, период 2 часа, интервал 10 минут → 12 волн по 10»."""
        from collections import Counter
        plan = news_access.plan_waves(user_ids=list(range(120)), start_at=self._start(),
                                      spread_minutes=120, wave_interval_minutes=10)
        sizes = Counter(wave for _user, wave, _at in plan)
        self.assertEqual(len(sizes), 12)
        self.assertEqual(set(sizes.values()), {10})
        preview = news_access.spread_preview(recipients=120, start_at=self._start(),
                                             spread_minutes=120, wave_interval_minutes=10)
        self.assertEqual((preview['waves'], preview['per_wave']), (12, 10))
        # Последняя волна включается ВНУТРИ периода, а не за ним.
        self.assertEqual(preview['ends_at'].hour, 10)
        self.assertEqual(preview['ends_at'].minute, 50)

    def test_nobody_lands_in_two_waves(self):
        """ТЗ п.8.3 дословно: «не должен попадать более чем в одну волну».

        В базе это первичный ключ (news_id, user_id), здесь — сам расчёт.
        """
        plan = news_access.plan_waves(user_ids=list(range(125)), start_at=self._start(),
                                      spread_minutes=120, wave_interval_minutes=10)
        self.assertEqual(len(plan), 125)
        self.assertEqual(len({user for user, _wave, _at in plan}), 125)
        waves = news_schema._STATEMENTS
        self.assertTrue(any('PRIMARY KEY (news_id, user_id)' in st and 'news_waves' in st
                            for st in waves))

    def test_waves_are_even_and_never_leave_a_stub(self):
        from collections import Counter
        sizes = Counter(wave for _u, wave, _at in news_access.plan_waves(
            user_ids=list(range(125)), start_at=self._start(),
            spread_minutes=120, wave_interval_minutes=10))
        self.assertEqual(sorted(set(sizes.values())), [10, 11])

    def test_refusals_say_what_to_fix(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 9, 22, 9, 0)
        later = now + timedelta(hours=1)
        earlier = now - timedelta(hours=1)
        check = news_access.schedule_refusal
        # Черновик не проверяем вовсе: автор собирает объявление заранее.
        self.assertIsNone(check(mode='later', scheduled_at=None, spread_minutes=None,
                                wave_interval_minutes=None, publishing=False, now=now))
        self.assertIn('дату и время', check(mode='later', scheduled_at=None,
                                            spread_minutes=None, wave_interval_minutes=None,
                                            publishing=True, now=now))
        self.assertIn('прошло', check(mode='later', scheduled_at=earlier, spread_minutes=None,
                                      wave_interval_minutes=None, publishing=True, now=now))
        self.assertIsNone(check(mode='later', scheduled_at=later, spread_minutes=None,
                                wave_interval_minutes=None, publishing=True, now=now))
        # Растяжка без времени начала — это «сразу», и она законна.
        self.assertIsNone(check(mode='spread', scheduled_at=None, spread_minutes=120,
                                wave_interval_minutes=10, publishing=True, now=now))
        self.assertIn('Период', check(mode='spread', scheduled_at=None, spread_minutes=1,
                                      wave_interval_minutes=1, publishing=True, now=now))
        self.assertIn('Интервал', check(mode='spread', scheduled_at=None, spread_minutes=120,
                                        wave_interval_minutes=1, publishing=True, now=now))
        self.assertIn('длиннее', check(mode='spread', scheduled_at=None, spread_minutes=10,
                                       wave_interval_minutes=30, publishing=True, now=now))

    def test_scheduled_is_a_draft_with_a_time_and_not_a_new_status(self):
        """Своего статуса у запланированной нет — и это решение.

        CHECK на status объявлен внутри CREATE TABLE, и расширить его
        идемпотентно нечем: ALTER TABLE ADD CONSTRAINT без IF NOT EXISTS упал бы
        на втором старте и утащил бы за собой весь SAVEPOINT схемы.
        """
        self.assertEqual(news_schema.STATUSES, ('draft', 'published', 'archived'))
        self.assertEqual(news_access.post_state('draft', None), 'draft')
        self.assertEqual(news_access.post_state('draft', '2026-09-22T09:00:00'), 'scheduled')
        self.assertEqual(news_access.post_state('published', '2026-09-22T09:00:00'), 'published')
        schema = _read('news', 'schema.py')
        self.assertNotIn("'scheduled'", schema[:schema.index('PASS_COLUMNS')])

    def test_a_draft_with_a_time_is_not_armed(self):
        """Дефект, пойманный прогоном на живой базе.

        Автор выбрал «Отложить», поставил время и нажал «В черновики». Черновик
        обязан ПОМНИТЬ время (иначе, вернувшись завтра, автор нашёл бы пустое
        поле), но выпускать его по этому времени нельзя: «в черновики» и
        означает «никуда не отправлять». Разводит это отдельный признак
        scheduled_armed, а не наличие даты.
        """
        self.assertEqual(news_access.post_state('draft', '2026-09-22T09:00', False), 'draft')
        self.assertEqual(news_access.post_state('draft', '2026-09-22T09:00', True), 'scheduled')
        source = _read('news', 'queries.py')
        # Взводит только публикация…
        self.assertIn('scheduled_armed = TRUE', _function_code(source, 'schedule_post'))
        # …и спрашивают признак, а не дату, обе двери крона.
        self.assertIn('AND scheduled_armed', _function_code(source, 'due_scheduled_posts'))
        self.assertIn('AND scheduled_armed', _function_code(source, 'publish_scheduled'))
        # Поля формы взвод не ставят никогда.
        from news import queries as news_queries
        self.assertNotIn('scheduled_armed', news_queries._PLAN_FIELDS)
        self.assertNotIn('scheduled_armed', news_queries._PLAN_INSERT_COLUMNS)

    def test_switching_the_launch_back_to_now_disarms_it(self):
        """Иначе крон выпустил бы новость, которую уже отвязали от расписания."""
        from news import queries as news_queries
        self.assertIn('scheduled_armed = CASE WHEN %(plan_scheduled_at)s IS NULL',
                      news_queries._PLAN_UPDATE_SET)
        # И выпуск снимает взвод: снятая с показа и выпущенная заново не должна
        # пойти по старому расписанию второй раз.
        self.assertIn('scheduled_armed = FALSE',
                      _function_code(_read('news', 'queries.py'), 'publish_scheduled'))

    def test_a_scheduled_news_gets_its_own_actions_in_the_list(self):
        """«Опубликовать» у запланированной не значит ничего: сервер прочёл бы
        её же расписание и взвёл запуск заново."""
        form = _read('src', 'components', 'wiki', 'WikiNews.jsx')
        self.assertIn("post.can_edit && post.state === 'scheduled'", form)
        self.assertIn('Выпустить сейчас', form)
        self.assertIn('Отменить запуск', form)
        self.assertIn("post.can_edit && post.state === 'draft'", form)

    def test_the_wave_gate_stands_in_every_viewer_query(self):
        """Окно, подтверждение, лента и карточка ленты — все четыре двери.

        Забытая дверь означала бы, что объявление, отложенное для окна, видно
        списком, а тест по нему проходится заранее.
        """
        source = _read('news', 'queries.py')
        for name in ('pending_for_user', 'confirm_read', '_viewer_post',
                     'feed_for_user', 'feed_post'):
            self.assertIn('_wave_gate(with_plan)', _function(source, name), name)

    def test_the_report_shows_everyone_including_the_waiting_waves(self):
        """Журнал волной НЕ режется: «кому ушла новость» — про весь круг."""
        report = _function_code(_read('news', 'queries.py'), 'read_report')
        self.assertNotIn('_wave_gate(', report)
        for field in ('wave_no', 'planned_at', 'activated_at'):
            self.assertIn(field, report)

    def test_showing_depends_on_the_plan_and_not_on_the_cron(self):
        """Крон может опоздать — объявление обязано открыться вовремя."""
        gate = _function_code(_read('news', 'queries.py'), '_wave_gate')
        self.assertIn('planned_at', gate)
        self.assertNotIn('activated_at', gate)

    def test_waves_are_built_before_the_news_goes_out(self):
        """Объявление, всплывшее раньше своего расписания, второй раз не всплывёт."""
        routes = _read('news', 'routes.py').replace('\n    def ', '\ndef ')
        launch = _function(routes, '_launch')
        self.assertLess(launch.index('_build_waves('), launch.index('queries.publish_post('))
        # И то же самое в кроне — второй точке выпуска.
        job = _function(_read('bot_schedule2.py'), 'publish_scheduled_news_job')
        self.assertLess(job.index('set_waves('), job.index('publish_scheduled('))

    def test_the_cron_job_is_registered_and_never_touches_request(self):
        """Напоминания о задачах уже падали в кроне на `request` вне контекста."""
        bot = _read('bot_schedule2.py')
        self.assertIn("id='news_publish_scheduler'", bot)
        self.assertIn('run_news_scheduler_async', bot)
        job = _function(bot, 'publish_scheduled_news_job')
        self.assertNotIn('request', job)
        self.assertIn('plan_ready', job)

    def test_the_oktell_window_gets_the_same_schedule(self):
        """Расписание одно на оба окна.

        Иначе объявление, отложенное для портала, вышло бы поверх клиента АТС
        немедленно — и человек подтвердил бы его раньше своей волны.
        """
        agent = _read('oktell_guard', 'routes.py')
        # Три двери агента: выдача окна, подтверждение и вопрос «держит ли ещё»
        # (п.16) — все с одной границей волны.
        self.assertEqual(agent.count('with_plan=_news_plan_ready(cursor, news_plan_ready)'), 3)

    def test_the_form_asks_the_server_for_the_calculation(self):
        """ТЗ п.8.4: расчёт перед запуском считает тот же код, что и выпуск."""
        form = _read('src', 'components', 'wiki', 'WikiNews.jsx')
        self.assertIn('/api/news/audience/preview', form)
        self.assertIn("publishMode === 'spread'", form)
        routes = _read('news', 'routes.py')
        preview = routes[routes.index('def news_audience_preview('):]
        preview = preview[:preview.index('@news_route')]
        self.assertIn('news_access.spread_preview(', preview)
        self.assertIn('_audience_refusal(', preview)


class NewsAttemptsTests(unittest.TestCase):
    """ТЗ #300, п.4.2: «количество попыток обязательно фиксировать в системе»."""

    def test_every_attempt_is_recorded_including_the_successful_one(self):
        """Удачная попытка — тоже попытка: «сдал с третьего раза» отличает
        понятную инструкцию от непонятной, и без этой строки ответа нет."""
        source = _read('news', 'queries.py')
        confirm = _function_code(source, 'confirm_read')
        self.assertIn('record_attempt(', confirm)
        # Запись СТРОГО до ветки отказа — иначе в журнал попадали бы только
        # проваленные попытки.
        self.assertLess(confirm.index('record_attempt('),
                        confirm.index("return 'quiz_wrong'"))
        self.assertIn('record_attempt(', _function_code(source, 'pass_quiz'))

    def test_the_number_is_counted_by_the_insert_itself(self):
        """Отдельный SELECT ради счётчика открыл бы окно между «посчитали» и
        «записали», и две попытки подряд легли бы под одним номером."""
        record = _function_code(_read('news', 'queries.py'), 'record_attempt')
        self.assertIn('COALESCE(MAX(attempt_no), 0) + 1', record)
        self.assertNotIn('fetchone()[0] + 1', record)

    def test_answers_are_cleaned_before_they_are_stored(self):
        """Тело запроса приходит от клиента: складывать его как есть значило бы
        хранить чужой JSON под видом ответов."""
        key = [(1, 0), (2, 1)]
        self.assertEqual(news_access.clean_answers(key, {'1': 0, '9': 3, '2': '1'}),
                         {'1': 0, '2': 1})
        # bool — не «вариант 1», и неотвеченный вопрос в словарь не попадает.
        self.assertEqual(news_access.clean_answers(key, {'1': True, '2': None}), {})
        self.assertEqual(news_access.clean_answers(key, None), {})

    def test_the_table_has_its_own_latch(self):
        """Запись попытки стоит на ПОДТВЕРЖДЕНИИ новости: ссылка на
        несуществующую таблицу заперла бы смену всем, кому пришло обязательное
        объявление."""
        self.assertIn('def attempts_ready(', _read('news', 'schema.py'))
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('_attempts_ready(cursor)', routes)
        queries = _read('news', 'queries.py')
        self.assertIn('if with_attempts:', _function_code(queries, 'confirm_read'))

    def test_the_oktell_window_records_attempts_too(self):
        """Тест из окна АТС — такая же попытка: иначе «сдал с третьего раза»
        считалось бы по половине операторов."""
        agent = _read('oktell_guard', 'routes.py')
        self.assertIn('with_attempts=_news_attempts_ready(cursor, news_attempts_ready)',
                      agent)


class NewsReportTests(unittest.TestCase):
    """ТЗ #300, п.11–14: статусы, сводка руководителю, аналитика и выгрузка."""

    @staticmethod
    def _row(**kwargs):
        row = {'in_audience': True, 'shown_at': None, 'confirmed_at': None,
               'quiz_passed_at': None, 'trainer_passed_at': None, 'attempts': 0,
               'worked_after': False}
        row.update(kwargs)
        return row

    def test_statuses_cover_the_list_from_the_spec(self):
        status = news_access.person_status
        self.assertEqual(status(has_quiz=True, shown_at='t', confirmed_at='t',
                                quiz_passed_at='t', attempts=1), 'passed')
        self.assertEqual(status(has_quiz=False, shown_at='t', confirmed_at='t',
                                quiz_passed_at=None), 'done')
        self.assertEqual(status(has_quiz=True, shown_at='t', confirmed_at=None,
                                quiz_passed_at=None, attempts=3), 'retrying')
        self.assertEqual(status(has_quiz=True, shown_at='t', confirmed_at=None,
                                quiz_passed_at=None, attempts=1), 'failed')
        self.assertEqual(status(has_quiz=True, shown_at='t', confirmed_at=None,
                                quiz_passed_at=None), 'pending')
        self.assertEqual(status(has_quiz=True, shown_at=None, confirmed_at=None,
                                quiz_passed_at=None, attendance_known=True,
                                worked_after=False), 'absent')
        self.assertEqual(set(news_access.PERSON_STATUS_LABELS), set(news_access.PERSON_STATUSES))

    def test_absence_is_never_claimed_without_data(self):
        """«Не выходил на смену» говорим, только когда источник посещаемости по
        этому кругу вообще отвечает: обвинять в прогуле по отсутствию данных
        нельзя. Молчит источник — человек просто не открывал объявление."""
        blind = news_access.person_status(has_quiz=False, shown_at=None, confirmed_at=None,
                                          quiz_passed_at=None, attendance_known=False,
                                          worked_after=False)
        self.assertEqual(blind, 'not_seen')
        # Знание ПЕРСОНАЛЬНОЕ: часы ведут не во всех отделах (у фронт-офисов,
        # маркетинга, бухгалтерии и HR в daily_hours нет ни строки) и не
        # каждому человеку внутри отдела.
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn("attendance_known=row['attendance_tracked']", routes)
        hours = _function_code(_read('news', 'queries.py'), 'read_report')
        self.assertIn('worked_before', hours)
        self.assertIn("'attendance_tracked': bool(", hours)

    def test_the_summary_counts_only_the_current_audience(self):
        rows = [self._row(confirmed_at='t', quiz_passed_at='t', attempts=1, status='passed'),
                self._row(confirmed_at='t', attempts=2, status='retrying'),
                self._row(status='not_seen'),
                self._row(in_audience=False, confirmed_at='t', quiz_passed_at='t',
                          attempts=1, status='passed')]
        summary = news_access.report_summary(rows, has_quiz=True)
        self.assertEqual(summary['assigned'], 3)
        self.assertEqual(summary['confirmed'], 2)
        self.assertEqual(summary['not_confirmed'], 1)
        self.assertEqual(summary['quiz_passed'], 1)
        # «Не прошли» — те, кто ПРОБОВАЛ и не сдал, а не все несдавшие: человек,
        # который до теста ещё не дошёл, его не заваливал.
        self.assertEqual(summary['quiz_failed'], 1)
        self.assertEqual(summary['confirmed_outside'], 1)
        self.assertEqual(summary['needs_attention'], 2)
        # Процент — по тесту, когда он есть: 1 из 3.
        self.assertEqual(summary['percent'], 33)
        # Среднее — по тем, кто отвечал: (1 + 2) / 2, а не / 3.
        self.assertEqual(summary['avg_attempts'], 1.5)

    def test_the_percent_follows_confirmations_when_there_is_no_quiz(self):
        rows = [self._row(confirmed_at='t', status='done'), self._row(status='not_seen')]
        self.assertEqual(news_access.report_summary(rows, has_quiz=False)['percent'], 50)

    def test_the_front_repeats_the_server_labels(self):
        """Файл и экран обязаны называть одно состояние одним словом."""
        form = _read('src', 'components', 'wiki', 'WikiNews.jsx')
        block = form[form.index('const STATUS_LABELS = {'):]
        block = block[:block.index('};')]
        found = dict(re.findall(r"(\w+): '([^']+)'", block))
        self.assertEqual(found, news_access.PERSON_STATUS_LABELS)
        # И должности: их печатает выгрузка, а на экране даёт newsShared.
        shared = _read('src', 'components', 'news', 'newsShared.js')
        titles = shared[shared.index('export const ROLE_TITLES = {'):]
        titles = titles[:titles.index('};')]
        self.assertEqual(dict(re.findall(r"(\w+): '([^']+)'", titles)),
                         news_access.ROLE_TITLES)

    def test_mistakes_count_people_and_not_attempts(self):
        """«Вопрос №3 — ошиблись 38% сотрудников»: один человек, трижды
        ошибшийся в одном вопросе, — это один человек, а не три ошибки."""
        source = _function_code(_read('news', 'queries.py'), 'question_mistakes')
        self.assertIn('COUNT(DISTINCT a.user_id)', source)
        self.assertIn('COUNT(DISTINCT user_id)', source)
        self.assertIn('wrong_ids @> to_jsonb(z.id)', source)

    def test_the_export_repeats_the_journal_and_never_counts_its_own(self):
        """Файл, расходящийся с экраном, хуже отсутствующего файла."""
        routes = _code_only(_read('news', 'routes.py'))
        self.assertIn('def _report_data(', routes)
        # Два читателя у одного сборщика: экран и файл.
        self.assertEqual(routes.count('= _report_data(cursor, post)'), 2)
        export = routes[routes.index('def news_post_report_export('):]
        export = export[:export.index('@news_route(')]
        self.assertIn('report_xlsx.build(post, rows)', export)
        # Курсор закрыт ДО сборки книги: openpyxl не держит слот пула.
        self.assertIn("defer_cursor=True", _read('news', 'routes.py'))
        self.assertLess(export.index('_get_cursor'), export.index('report_xlsx.build('))

    def test_the_export_has_the_minimum_columns_from_the_spec(self):
        from news import report_xlsx
        titles = [title for _key, title, _width in report_xlsx.COLUMNS]
        for required in ('ФИО', 'ID сотрудника', 'Подразделение', 'Дата публикации',
                         'Дата ознакомления', 'Результат теста', 'Попыток', 'Статус'):
            self.assertIn(required, titles)
        # Подпись статуса берётся у сервера, а не пишется в файле второй раз.
        source = _read('news', 'report_xlsx.py')
        self.assertIn('news_access.PERSON_STATUS_LABELS', source)
        self.assertNotIn('Успешно пройден', source)


class NewsTakeDownTests(unittest.TestCase):
    """ТЗ #300, п.16: «блокировка сотрудников снимается; сама публикация
    сохраняется в истории; фиксируется, кто и когда отменил публикацию»."""

    def test_who_and_when_are_stored_and_survive_the_person(self):
        schema = _read('news', 'schema.py')
        self.assertIn('ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP', schema)
        # Уволенного удалить можно, а время снятия от этого не пропадёт.
        self.assertIn('archived_by INTEGER REFERENCES users(id) ON DELETE SET NULL', schema)
        self.assertIn('def takedown_ready(', schema)

    def test_only_what_is_on_air_is_taken_down_and_only_once(self):
        """Условие «на показе» — В САМОМ UPDATE: двое, нажавшие одновременно,
        иначе оба получили бы «сняли», и в истории остался бы второй."""
        take_down = _function_code(_read('news', 'queries.py'), 'take_down')
        self.assertIn("AND status = 'published'", take_down)
        self.assertIn('archived_by = %(by)s', take_down)
        routes = _code_only(_read('news', 'routes.py'))
        archive = routes[routes.index('def news_post_archive('):]
        archive = archive[:archive.index('@news_route(')]
        self.assertIn("by=ctx['user_id']", archive)
        self.assertIn('NEWS_NOT_ON_AIR', archive)
        self.assertNotIn("set_status(cursor, post_id=post_id, status='archived')", archive)

    def test_republishing_keeps_the_history_of_the_takedown(self):
        """Ошибочно запущенную, снятую и выпущенную заново новость журнал обязан
        и дальше объяснять: кто её останавливал и когда."""
        source = _read('news', 'queries.py')
        for name in ('publish_post', 'publish_scheduled'):
            self.assertNotIn('archived_by', _function_code(source, name), name)

    def test_the_journal_stays_open_after_the_takedown(self):
        form = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        self.assertIn('post.published_at && hasJournal(post)', form)
        self.assertNotIn("post.status === 'published' && hasJournal(post)", form)
        # Снятую по ошибке можно выпустить снова.
        self.assertIn("post.can_edit && post.status === 'archived'", form)
        self.assertIn('Опубликовать снова', form)

    def test_taking_down_asks_first_and_never_in_a_second_window(self):
        """Снятие убирает окно у всего круга разом — через подтверждение. И не
        отдельным окном: на телефоне любое окно здесь целый экран."""
        form = _jsx_code_only(_read('src', 'components', 'wiki', 'WikiNews.jsx'))
        self.assertIn("onSelect: () => setTakingDown(post.id)", form)
        self.assertNotIn("onSelect: () => act(post, 'archive')", form)
        strip = form[form.index('{takingDown === post.id && ('):]
        strip = strip[:strip.index("onClick={() => act(post, 'archive')}")]
        self.assertIn('Снять с показа?', strip)
        self.assertNotIn('IosModal', strip)

    def test_the_label_names_who_without_guessing_gender(self):
        """«снята … · Зарина Алиева», а не «снял/сняла»: по имени пол не угадывают."""
        form = _read('src', 'components', 'wiki', 'WikiNews.jsx')
        label = form[form.index('const takedownLabel = '):]
        label = label[:label.index('\n};')]
        self.assertIn('снята', label)
        self.assertNotIn('снял ', label)
        self.assertNotIn('сняла', label)
        # Автор уже первым в строке — имя второй раз не пишем.
        self.assertIn('post.archived_by !== post.author_id', label)

    def test_an_open_portal_window_lets_go_of_a_withdrawn_news(self):
        """У обязательной новости нет крестика: окно, которого больше нет на
        сервере, запирало портал до перезагрузки страницы."""
        modal = _jsx_code_only(_read('src', 'components', 'news', 'NewsOfDayModal.jsx'))
        self.assertIn('if (!fresh) return items;', modal)
        self.assertNotIn('fresh || head', modal)
        self.assertIn('e?.response?.status === 404', modal)

    def test_the_oktell_window_lets_the_operator_back_on_the_line(self):
        """Окно поверх клиента АТС, пока открыто, сервер не спрашивало вовсе:
        снятое объявление держало оператора в «Тренинге» до конца смены."""
        agent = _read('oktell_recall_guard', 'agent.py')
        self.assertIn('def news_state(self, news_id', agent)
        self.assertIn('def release_news(reason', agent)
        # Один выход на «подтвердил» и «сняли»: разбор нажатия сам статус не
        # возвращает — это делает только release_news, и забыть его в одной из
        # веток больше нельзя.
        press = agent[agent.index('    def handle_news_press('):]
        press = press[:press.index('    def release_news(')]
        self.assertNotIn('set_operator_status', press)
        release = agent[agent.index('    def release_news('):]
        release = release[:release.index('\n    def ', 10)]
        self.assertIn('set_operator_status', release)
        self.assertIn('release_news("подтверждено")', agent)
        self.assertIn('elif verdict.get("status") == 404:', agent)
        # «Не знаем» (нет связи) окно не закрывает: закрывает только явное «нет».
        self.assertIn('link.news_state(active_news.get("id")) is False', agent)
        # Правка едет только новой сборкой — версия поднята.
        self.assertNotIn('VERSION = "1.0.31"', agent)

    def test_the_state_door_has_no_side_effects(self):
        """/news ставит отметку «показали», а спрашивать «держит ли ещё» можно
        сколько угодно — отметку ставить здесь не за что."""
        routes = _code_only(_read('oktell_guard', 'routes.py'))
        state = routes[routes.index('def oktell_guard_agent_news_state('):]
        state = state[:state.index('@agent_route(')]
        self.assertIn('news_still_required(', state)
        self.assertNotIn('mark_shown', state)

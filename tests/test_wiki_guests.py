# -*- coding: utf-8 -*-
"""Гостевой доступ вики: лестница, срок и стражи над двумя раздвоениями.

Файл держит три разных обещания, данных в докстрингах боевого кода. Каждое из
них — это место, где механика ломается МОЛЧА, и обычным тестом такое не ловится:
поломка выглядит как рабочая система, просто с другими правами.

1. can_grant_guest НЕ ПРАВО НА СОДЕРЖИМОЕ. Шесть прав из PERMISSION_COLUMNS
   превращаются в способности (access.capabilities_from_grants), а любая
   способность сверх чтения открывает справочники «Парки» и «Офисы»
   (access.has_write_capability). Добавь кто-нибудь седьмой элемент в кортеж —
   и тумблер «пусть выдаёт гостевой доступ» молча раздаст правку телефонов
   парков. На это ссылаются три докстринга (wiki/schema.py, wiki/structure.py,
   wiki/guests.py), и до этого файла единственной защитой был комментарий.

2. ЛЕСТНИЦА «НИЖЕ СЕБЯ». Решение владельца 25.08.2026, и оно НЕ совпадает с
   GRANT_CEILING: та таблица перепрыгивает тренера у супервайзера, а тут сказано
   «ниже себя» без исключений. Совпади две лестницы случайно в одной точке —
   кто-нибудь заменит одну другой, и правило разойдётся с решением беззвучно.

3. ДВА РАЗДВОЕНИЯ, оба сознательные и оба обязаны сходиться:
   * гостевая CTE описана дважды — в периметре чтения (queries) и отдельным
     запросом про одного человека (guests). Разойдись условия — человек увидит
     в баннере срок по разделу, которого в периметре уже нет;
   * отдел ветки считается дважды — поштучно (structure.section_branch_department)
     и набором (structure.branch_department_map). Разойдись они — граница отдела
     у формы выдачи и у проверки на записи станут разными.

Базы файл не требует НИГДЕ, кроме последнего класса: он сверяет два вычислителя
отдела ветки на боевом дереве разделов и сам скипается, если базы нет.
"""

import re
import unittest
from datetime import datetime, timedelta

from wiki import guests, queries, structure
from wiki.access import (
    ROLE_LEVELS,
    capabilities_from_grants,
    has_write_capability,
    may_grant_guest_to,
    role_level_of,
)
from wiki.schema import (
    CAPABILITY_COLUMNS,
    GUEST_GRANT_COLUMN,
    MAX_GUEST_DAYS,
    PERMISSION_COLUMNS,
)

# «Сейчас» для арифметики срока. Фиксированное, а не now(): тест про календарь,
# который проходит только до полуночи, — это не тест.
NOW = datetime(2026, 8, 25, 14, 37, 12)


class GuestGrantColumnIsolationTest(unittest.TestCase):
    """Страж №1: право выдавать не должно стать правом на содержимое."""

    def test_column_name_is_the_one_the_guards_watch(self):
        """Имя проверяем первым: иначе стражи ниже сторожат несуществующее.

        Приём из tests/test_wiki_directory_space.py — константу переименуют, а
        assertNotIn по новому имени продолжит проходить, ничего не проверяя.
        """
        self.assertEqual('can_grant_guest', GUEST_GRANT_COLUMN)

    def test_not_a_permission(self):
        self.assertNotIn(GUEST_GRANT_COLUMN, PERMISSION_COLUMNS)

    def test_not_a_capability(self):
        self.assertNotIn(GUEST_GRANT_COLUMN, CAPABILITY_COLUMNS)

    def test_does_not_become_a_write_capability(self):
        """Главное следствие: тумблер не открывает справочники «Парки»/«Офисы».

        Гейт этих справочников — has_write_capability, «есть ли хоть одна
        способность сверх чтения». Способности поднимаются из ПРАВИЛ, которые
        человеку выписали (capabilities_from_grants), и попади сюда тумблер —
        правка телефонов парков досталась бы каждому, кому разрешили звать
        гостей.
        """
        caps = capabilities_from_grants({GUEST_GRANT_COLUMN: True})
        self.assertFalse(has_write_capability(caps))
        self.assertFalse(any(caps.values()))

    def test_permission_columns_stay_six(self):
        """Ровно шесть прав. Седьмое обязано сначала сломать этот тест."""
        self.assertEqual(
            ('can_read', 'can_create', 'can_edit',
             'can_delete', 'can_publish', 'can_approve'),
            PERMISSION_COLUMNS)


class GuestLadderTest(unittest.TestCase):
    """Страж №2: «ниже себя по оргструктуре» — строго ниже."""

    def test_supervisor_grants_below_including_trainer(self):
        """СВ выдаёт и тренеру — здесь лестница РАСХОДИТСЯ с GRANT_CEILING.

        В GRANT_CEILING у супервайзера потолок 10, и тренер (20) пропущен
        намеренно: так владелец сформулировал ПРО ПРАВИЛА разделов. Про гостевой
        доступ сказано иначе — «ниже себя», без исключений. Тест закрепляет
        именно расхождение: подмени кто-нибудь одну лестницу другой, разница
        исчезнет молча.
        """
        self.assertTrue(may_grant_guest_to('sv', 'trainer'))
        self.assertTrue(may_grant_guest_to('sv', 'operator'))
        self.assertTrue(may_grant_guest_to('sv', 'trainee'))

    def test_nobody_grants_to_their_own_level(self):
        """Своему уровню — нет. Гостевой доступ не заменяет правило раздела."""
        for role in ('sv', 'admin', 'trainer', 'super_admin'):
            with self.subTest(role=role):
                self.assertFalse(may_grant_guest_to(role, role))

    def test_nobody_grants_upwards(self):
        self.assertFalse(may_grant_guest_to('operator', 'sv'))
        self.assertFalse(may_grant_guest_to('trainer', 'admin'))
        self.assertFalse(may_grant_guest_to('sv', 'admin'))

    def test_operator_grants_to_nobody(self):
        """Ниже оператора никого нет — и стажёр ему не «ниже», а вровень."""
        for target in ROLE_LEVELS:
            with self.subTest(target=target):
                self.assertFalse(may_grant_guest_to('operator', target))

    def test_unknown_target_role_is_refused(self):
        """Опечатка в должности — отказ, а не «ноль меньше любого уровня».

        role_level_of отдаёт незнакомой роли ноль, и без явной проверки условие
        «уровень цели меньше моего» пропустило бы кого угодно.
        """
        self.assertEqual(0, role_level_of('нет такой должности'))
        self.assertFalse(may_grant_guest_to('admin', 'нет такой должности'))
        self.assertFalse(may_grant_guest_to('admin', ''))
        self.assertFalse(may_grant_guest_to('admin', None))

    def test_unknown_actor_role_grants_to_nobody(self):
        self.assertFalse(may_grant_guest_to('нет такой должности', 'operator'))

    def test_master_key_has_no_ladder(self):
        """unbounded снимает лестницу целиком — как и границу отдела.

        Носитель роли вики «Администратор» бывает оператором по должности, и
        лестница закрыла бы ему выдачу вовсе, хотя роль назначают ровно за тем,
        чтобы человек раздавал доступ.
        """
        self.assertTrue(may_grant_guest_to('operator', 'super_admin', unbounded=True))

    def test_supervisor_alias_is_normalized(self):
        """'supervisor' — историческое написание 'sv', и уровень у него нулевой.

        В ROLE_LEVELS его нет (см. шапку wiki/access.py), поэтому носитель такой
        роли по лестнице не выдаёт никому. Это не описка теста, а фиксация
        существующего края: у правил разделов алиас разворачивается отдельной
        веткой (expand_otp_roles), здесь такой ветки нет.
        """
        self.assertNotIn('supervisor', ROLE_LEVELS)
        self.assertFalse(may_grant_guest_to('supervisor', 'operator'))


class GuestExpiryTest(unittest.TestCase):
    """Срок: конец дня, потолок в днях и внятный отказ вместо трассировки."""

    def test_preset_lands_on_the_end_of_the_last_day(self):
        """«7 дней» — это весь седьмой день, а не момент плюс 168 часов.

        Человек читает «доступ до 1 сентября» и понимает весь день первого;
        выдача, истекающая в 14:37, выглядит как сбой, а не как срок.
        """
        got = guests.resolve_expiry(NOW, days=7)
        self.assertEqual(datetime(2026, 9, 1, 23, 59, 59), got)

    def test_date_and_preset_agree(self):
        """Обе двери формы приводят к одному выражению.

        Иначе «14 дней» и «дата через 14 дней» означали бы разное, и потолок
        зависел бы от того, какой кнопкой человек воспользовался.
        """
        self.assertEqual(guests.resolve_expiry(NOW, days=MAX_GUEST_DAYS),
                         guests.resolve_expiry(NOW, until='2026-09-08'))

    def test_cap_is_the_owner_decision(self):
        self.assertEqual(14, MAX_GUEST_DAYS)
        with self.assertRaises(ValueError) as ctx:
            guests.resolve_expiry(NOW, days=MAX_GUEST_DAYS + 1)
        self.assertIn('14', str(ctx.exception))

    def test_cap_applies_to_the_date_door_too(self):
        with self.assertRaises(ValueError):
            guests.resolve_expiry(NOW, until='2026-09-09')

    def test_today_is_allowed_and_lasts_till_midnight(self):
        """Сегодняшняя дата — законный срок «до конца дня»."""
        self.assertEqual(datetime(2026, 8, 25, 23, 59, 59),
                         guests.resolve_expiry(NOW, until='2026-08-25'))

    def test_past_date_is_refused(self):
        with self.assertRaises(ValueError):
            guests.resolve_expiry(NOW, until='2026-08-24')

    def test_zero_and_negative_days_are_refused(self):
        for days in (0, -1):
            with self.subTest(days=days):
                with self.assertRaises(ValueError):
                    guests.resolve_expiry(NOW, days=days)

    def test_both_doors_at_once_is_refused(self):
        """Умолчание пришлось бы выбрать, а тихий выбор — это чужой срок."""
        with self.assertRaises(ValueError):
            guests.resolve_expiry(NOW, days=7, until='2026-09-01')

    def test_neither_door_is_refused(self):
        with self.assertRaises(ValueError):
            guests.resolve_expiry(NOW)

    def test_messages_are_for_humans(self):
        """Текст отказа показывают в форме — трассировке там не место."""
        for kwargs in ({'days': 'много'}, {'until': '01.09.2026'}, {}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError) as ctx:
                    guests.resolve_expiry(NOW, **kwargs)
                message = str(ctx.exception)
                self.assertTrue(message and message[0].isupper(), message)
                self.assertNotIn('literal', message)


class GuestStatusTest(unittest.TestCase):
    """Состояние выдачи и обратный отсчёт — как их читает человек."""

    def test_revoked_beats_expiry(self):
        """Отзыв — событие, срок — просто дата. Событие сильнее.

        Отозванная вчера выдача с завтрашним сроком остаётся отозванной, а не
        «истекает завтра»: иначе список предложит её продлить.
        """
        row = {'revoked_at': NOW - timedelta(days=1),
               'expires_at': NOW + timedelta(days=1)}
        self.assertEqual('revoked', guests.grant_status(row, NOW))

    def test_expired_and_active(self):
        self.assertEqual('expired', guests.grant_status(
            {'revoked_at': None, 'expires_at': NOW - timedelta(seconds=1)}, NOW))
        self.assertEqual('active', guests.grant_status(
            {'revoked_at': None, 'expires_at': NOW + timedelta(seconds=1)}, NOW))

    def test_days_left_counts_calendar_days(self):
        """«Осталось 0 дней» рядом с «до 25.08» значит «сегодня последний».

        По календарю, а не по часам: выдача до конца сегодняшнего дня — это ноль
        оставшихся дней, и ровно это человек видит в календаре.
        """
        self.assertEqual(0, guests.days_left(datetime(2026, 8, 25, 23, 59, 59), NOW))
        self.assertEqual(1, guests.days_left(datetime(2026, 8, 26, 0, 0, 1), NOW))
        self.assertEqual(14, guests.days_left(
            guests.resolve_expiry(NOW, days=14), NOW))

    def test_days_left_goes_negative_after_expiry(self):
        self.assertEqual(-1, guests.days_left(datetime(2026, 8, 24, 23, 59, 59), NOW))

    def test_no_expiry_is_not_a_crash(self):
        self.assertIsNone(guests.days_left(None, NOW))


def _normalize(sql):
    """SQL без пробельного шума — чтобы сверять условия, а не отступы."""
    return re.sub(r'\s+', ' ', sql).strip()


class GuestSqlAgreementTest(unittest.TestCase):
    """Страж №3: два описания гостевой выдачи обязаны говорить одно и то же.

    Базы не требует — сверяются ТЕКСТЫ запросов. Именно на раздвоении условий
    доступа исходная вика и сломалась (см. шапку wiki/perimeter.py), а здесь
    раздвоение сознательное: в периметре чтения условие вклеено в два больших
    запроса, а в guests.py нужен самостоятельный запрос про одного человека.
    """

    GUEST_SQL = (queries._GUEST_SECTIONS_CTE,
                 guests._MY_GRANTS_SQL,
                 guests._ARTICLE_GRANT_SQL)

    def test_every_guest_query_filters_revocation_and_expiry(self):
        """Отозванная и истёкшая выдача обязана отсекаться ВЕЗДЕ.

        Забудь одно из двух в одном из запросов — и человек увидит в баннере
        срок по разделу, которого в периметре уже нет.
        """
        for sql in self.GUEST_SQL:
            with self.subTest(sql=_normalize(sql)[:60]):
                text = _normalize(sql)
                self.assertIn('revoked_at IS NULL', text)
                self.assertIn(
                    "expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')",
                    text)

    def test_expiry_is_compared_in_almaty_everywhere(self):
        """Наивная колонка сравнивается с наивным алматинским «сейчас».

        Голый CURRENT_TIMESTAMP сдвинул бы срок на часы, и выдача истекала бы
        раньше обещанного у части запросов, а не у всех сразу — то есть
        расхождение было бы плавающим.
        """
        for sql in self.GUEST_SQL:
            with self.subTest(sql=_normalize(sql)[:60]):
                text = _normalize(sql)
                self.assertNotIn('expires_at > CURRENT_TIMESTAMP ', text + ' ')

    def test_subsection_expansion_uses_the_column(self):
        """Раскрытие на подразделы читается из include_subsections, а не из «всегда»."""
        for sql in (queries._GUEST_SECTIONS_CTE, guests._ARTICLE_GRANT_SQL):
            with self.subTest(sql=_normalize(sql)[:60]):
                self.assertIn('include_subsections', _normalize(sql))

    def test_space_gate_lets_the_guest_through_by_name(self):
        """Исключение в границе пространства — только через гостевые CTE.

        Проверяем текстом: щель обязана опираться на guest_seed/guest_tree, то
        есть на ИМЕННУЮ выдачу этому человеку, а не на какое-нибудь общее
        послабление отделу.
        """
        gate = _normalize(queries._SPACE_GATE_SQL)
        self.assertIn('SELECT id FROM guest_seed UNION SELECT id FROM guest_tree', gate)
        cte = _normalize(queries._GUEST_SECTIONS_CTE)
        self.assertIn('g.user_id = %(user_id)s', cte)

    def test_grant_right_requires_read(self):
        """Раздавать раздел, которого сам не видишь, нельзя."""
        text = _normalize(guests._GRANTABLE_SECTIONS_SQL)
        self.assertIn('r.can_grant_guest', text)
        self.assertIn('r.can_read', text)
        text = _normalize(guests._MAY_GRANT_ANYWHERE_SQL)
        self.assertIn('r.can_grant_guest', text)
        self.assertIn('r.can_read', text)

    def test_shareable_articles_offer_only_what_a_guest_can_open(self):
        """Форма не предлагает черновик и строгий режим.

        Оба открылись бы 200-м ответом и не открылись бы у получателя:
        статус-условие и обход строгого режима в articles._VISIBLE_ARTICLES_SQL
        гостевой ветки не знают.
        """
        text = _normalize(guests._SHAREABLE_ARTICLES_SQL)
        self.assertIn("a.status = 'published'", text)
        self.assertIn('NOT a.strict_mode', text)

    def test_extension_does_not_rewrite_the_granter(self):
        """Повторная выдача не переписывает granted_by.

        На этом поле держится право отозвать выданное собой даже после того, как
        право на разделе сняли (routes_guests._may_touch). Перепиши его продление
        — и первый выдавший потерял бы возможность отозвать свою же выдачу
        вообще ничем, а в журнале авторство оказалось бы затёрто.

        Читаем ИСХОДНИК ветки UPDATE: проверить это на объекте нечем — функция
        ходит в базу, а страж нужен именно на текст запроса.
        """
        import inspect

        source = inspect.getsource(guests.create_grant)
        update = re.search(r'UPDATE wiki_guest_access.*?RETURNING id', source, re.S)
        self.assertIsNotNone(update, 'ветка продления исчезла из create_grant')
        self.assertNotIn('granted_by', _normalize(update.group(0)))


class BranchDepartmentAgreementTest(unittest.TestCase):
    """Страж №3б: поштучный и наборный вычислители отдела ветки — заодно.

    Единственный класс файла, которому нужна база: оба вычислителя ходят прямо
    в wiki_sections, и подменить таблицу заглушкой, как в периметровых тестах,
    нельзя — там SQL склеивается из констант, а здесь живёт внутри функций.
    Сверяем на боевом дереве, только на чтение.
    """

    @classmethod
    def setUpClass(cls):
        from tests import prod_db
        reason = prod_db.skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        cls.prod_db = prod_db
        cls.conn = prod_db.connection()

    def test_both_implementations_return_the_same_department(self):
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT id FROM wiki_sections WHERE status = 'active' ORDER BY id")
            section_ids = [row[0] for row in cursor.fetchall()]
            self.assertTrue(section_ids, 'в базе нет активных разделов')

            bulk = structure.branch_department_map(cursor)
            for section_id in section_ids:
                one = structure.section_branch_department(cursor, section_id)
                self.assertEqual(one, bulk.get(section_id),
                                 'разошлись на разделе %s' % section_id)
        finally:
            self.prod_db.rollback()
            cursor.close()


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""Гостевой доступ вики: лестница, срок и стражи над двумя раздвоениями.

Файл держит три разных обещания, данных в докстрингах боевого кода. Каждое из
них — это место, где механика ломается МОЛЧА, и обычным тестом такое не ловится:
поломка выглядит как рабочая система, просто с другими правами.

1. ЛЕСТНИЦА ВЫДАЧИ. Владелец 25.08.2026 перечислил роли поимённо: директор
   выдаёт всем, руководитель — супервайзерам и операторам, супервайзер —
   операторам, тренер и оператор не выдают вовсе. Таблица, а не арифметика «на
   ступень ниже»: у супервайзера ступенька перепрыгивает тренера. Формула,
   которая «почти совпадает», разошлась бы с решением молча.

2. СВОИ ПОДЧИНЁННЫЕ. Второе измерение того же права — отдел: «если СВ из СЗоВ,
   то он и видит операторов из СЗоВ». Потолок отвечает «кому по чину», отдел —
   «чьим людям», и одно из другого не выводится.

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
from pathlib import Path

from wiki import guests, queries, structure
from wiki.access import (
    ROLE_LEVELS,
    guest_grant_ceiling,
    may_grant_guest_in_department,
    may_grant_guest_to,
    role_level_of,
)
from wiki.schema import MAX_GUEST_DAYS

# «Сейчас» для арифметики срока. Фиксированное, а не now(): тест про календарь,
# который проходит только до полуночи, — это не тест.
NOW = datetime(2026, 8, 25, 14, 37, 12)


class GuestLadderTest(unittest.TestCase):
    """Страж №1: лестница выдачи — ровно та, что назвал владелец."""

    def test_director_grants_to_everyone(self):
        """«Коммерческий директор может всем» — включая других директоров.

        Наверху лестницы правило «никто не выдаёт своему уровню» лишено смысла:
        над директором никого нет, эскалировать некуда.
        """
        for target in ROLE_LEVELS:
            with self.subTest(target=target):
                self.assertTrue(may_grant_guest_to('super_admin', target))

    def test_head_grants_to_supervisors_and_operators(self):
        """«Руководитель — супервайзерам и операторам», но не другим руководителям."""
        self.assertTrue(may_grant_guest_to('admin', 'sv'))
        self.assertTrue(may_grant_guest_to('admin', 'operator'))
        self.assertTrue(may_grant_guest_to('admin', 'trainee'))
        self.assertFalse(may_grant_guest_to('admin', 'admin'))
        self.assertFalse(may_grant_guest_to('admin', 'super_admin'))

    def test_supervisor_grants_to_operators_only(self):
        """«Супервайзеры могут выдавать доступы операторам» — и только им.

        Тренер (20) под потолок супервайзера (10) НЕ проходит, и это не описка:
        та же ступенька перепрыгнута в GRANT_CEILING у правил разделов.
        """
        self.assertTrue(may_grant_guest_to('sv', 'operator'))
        self.assertTrue(may_grant_guest_to('sv', 'trainee'))
        self.assertFalse(may_grant_guest_to('sv', 'trainer'))
        self.assertFalse(may_grant_guest_to('sv', 'sv'))
        self.assertFalse(may_grant_guest_to('sv', 'admin'))

    def test_trainer_and_operator_do_not_grant_at_all(self):
        """Ниже супервайзера права нет вовсе — и раздела они не видят."""
        for role in ('trainer', 'operator', 'trainee'):
            with self.subTest(role=role):
                self.assertIsNone(guest_grant_ceiling(role))
                for target in ROLE_LEVELS:
                    self.assertFalse(may_grant_guest_to(role, target))

    def test_ceiling_answers_both_questions_at_once(self):
        """Один и тот же признак: «вижу ли раздел» и «кому вправе выдать».

        Раздел «Гостевой доступ» виден супервайзеру и выше — это ровно те, у
        кого потолок не None. Считать видимость вторым способом значило бы
        завести второй источник истины об одном и том же.
        """
        visible = {role for role in ROLE_LEVELS
                   if guest_grant_ceiling(role) is not None}
        self.assertEqual({'super_admin', 'admin', 'sv'}, visible)

    def test_unknown_target_role_is_refused(self):
        """Опечатка в должности — отказ, а не «ноль меньше любого потолка».

        role_level_of отдаёт незнакомой роли ноль, и без явной проверки условие
        «уровень цели не выше потолка» пропустило бы кого угодно.
        """
        self.assertEqual(0, role_level_of('нет такой должности'))
        for value in ('нет такой должности', '', None):
            with self.subTest(value=value):
                self.assertFalse(may_grant_guest_to('admin', value))

    def test_unknown_actor_role_grants_to_nobody(self):
        self.assertIsNone(guest_grant_ceiling('нет такой должности'))
        self.assertFalse(may_grant_guest_to('нет такой должности', 'operator'))

    def test_wiki_admin_role_lifts_the_ceiling(self):
        """Роль вики «Администратор» поднимает потолок независимо от должности.

        Её назначают руками ровно затем, чтобы человек раздавал доступ, а
        должность у него бывает любая — по лестнице оператор не выдал бы никому.
        """
        self.assertIsNone(guest_grant_ceiling('operator'))
        self.assertEqual(ROLE_LEVELS['super_admin'],
                         guest_grant_ceiling('operator', is_wiki_admin=True))
        self.assertTrue(may_grant_guest_to('operator', 'admin', is_wiki_admin=True))

    def test_supervisor_alias_matches_sv(self):
        """'supervisor' — историческое написание 'sv', и выдаёт он так же.

        В ROLE_LEVELS его нет (см. шапку wiki/access.py), поэтому в таблице он
        прописан отдельной строкой — иначе носители этой роли не выдавали бы
        вовсе, хотя это те же супервайзеры.
        """
        self.assertNotIn('supervisor', ROLE_LEVELS)
        self.assertEqual(guest_grant_ceiling('sv'), guest_grant_ceiling('supervisor'))
        self.assertTrue(may_grant_guest_to('supervisor', 'operator'))
        self.assertFalse(may_grant_guest_to('supervisor', 'trainer'))


class GuestDepartmentTest(unittest.TestCase):
    """Страж №2: свои подчинённые — не только по чину, но и по отделу."""

    def test_own_department_passes(self):
        self.assertTrue(may_grant_guest_in_department(1, [1, 367]))

    def test_foreign_department_is_refused(self):
        """«СВ из СЗоВ видит операторов из СЗоВ» — и только их."""
        self.assertFalse(may_grant_guest_in_department(367, [1]))

    def test_no_department_is_refused(self):
        """Отдела нет — «неизвестно чей», а не «ничей». То же, что в may_grant_to_subject."""
        for value in (None, '', 'нет'):
            with self.subTest(value=value):
                self.assertFalse(may_grant_guest_in_department(value, [1]))

    def test_director_has_no_department_border(self):
        """None означает «без границы»: директору сказано «может всем»."""
        self.assertTrue(may_grant_guest_in_department(367, None))
        self.assertTrue(may_grant_guest_in_department(None, None))

    def test_department_and_ladder_are_independent(self):
        """Два измерения, и одно не выводится из другого.

        Свой отдел не поднимает потолок должности, а высокий потолок не открывает
        чужой отдел: обе проверки стоят на выдаче по очереди.
        """
        self.assertTrue(may_grant_guest_in_department(1, [1]))
        self.assertFalse(may_grant_guest_to('sv', 'sv'))


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

    def test_today_is_a_valid_preset(self):
        """Ноль дней — «сегодня», и это законный срок.

        Он здесь ради часа: «сегодня до 18:00» — то, зачем гостевой доступ чаще
        всего и зовут. Без нуля ближайший пресет — завтрашний день.
        """
        self.assertEqual(datetime(2026, 8, 25, 23, 59, 59),
                         guests.resolve_expiry(NOW, days=0))

    def test_negative_days_are_refused(self):
        with self.assertRaises(ValueError):
            guests.resolve_expiry(NOW, days=-1)

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


class GuestHourTest(unittest.TestCase):
    """Час: «до 18:00» вместо «до конца дня» (решение владельца 25.08.2026)."""

    def test_named_hour_replaces_the_end_of_day(self):
        self.assertEqual(datetime(2026, 8, 25, 18, 0),
                         guests.resolve_expiry(NOW, days=0, at_time='18:00'))
        self.assertEqual(datetime(2026, 8, 26, 9, 0),
                         guests.resolve_expiry(NOW, days=1, at_time='09:00'))
        self.assertEqual(datetime(2026, 9, 1, 18, 30),
                         guests.resolve_expiry(NOW, until='2026-09-01', at_time='18:30'))

    def test_no_hour_still_means_the_end_of_day(self):
        """Час необязателен, и без него ничего не меняется."""
        for value in (None, '', '   '):
            with self.subTest(value=value):
                self.assertEqual(guests.resolve_expiry(NOW, days=3),
                                 guests.resolve_expiry(NOW, days=3, at_time=value))

    def test_hour_in_the_past_is_refused(self):
        """«До 09:00», набранное в 14:37, — выдача, истёкшая в момент создания.

        Строка в списке есть, доступа нет: тот самый молчаливый отказ, от
        которого этот раздел лечили шесть раз.
        """
        with self.assertRaises(ValueError) as ctx:
            guests.resolve_expiry(NOW, days=0, at_time='09:00')
        self.assertIn('прошло', str(ctx.exception))

    def test_same_hour_tomorrow_is_fine(self):
        """Тот же час, но завтра, — уже будущее и потому законен."""
        self.assertEqual(datetime(2026, 8, 26, 9, 0),
                         guests.resolve_expiry(NOW, days=1, at_time='09:00'))

    def test_broken_hour_is_refused_with_a_human_message(self):
        for value in ('25:00', '18:70', 'вечером', '18-00'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    guests.resolve_expiry(NOW, days=1, at_time=value)
                self.assertIn('ЧЧ:ММ', str(ctx.exception))

    def test_hour_does_not_lift_the_day_cap(self):
        """Час уточняет ДЕНЬ, а не обходит потолок в четырнадцать дней."""
        with self.assertRaises(ValueError):
            guests.resolve_expiry(NOW, days=MAX_GUEST_DAYS + 1, at_time='18:00')
        with self.assertRaises(ValueError):
            guests.resolve_expiry(NOW, until='2026-09-09', at_time='18:00')

    def test_seconds_are_zero(self):
        """«До 18:00» — ровно 18:00. Лишние секунды в списке читаются как
        чужая точность, которой человек не задавал."""
        self.assertEqual(0, guests.resolve_expiry(NOW, days=0, at_time='18:00').second)


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

    def test_candidates_ask_both_questions(self):
        """Справочник получателей сужен И потолком, И отделом.

        Забудь одно из двух — и форма предложит того, кого сервер отвергнет:
        молчаливый отказ с обратной стороны стола, от которого этот раздел
        лечили дважды.
        """
        text = _normalize(guests._CANDIDATES_SQL)
        self.assertIn('BETWEEN 1 AND %(ceiling)s', text)
        self.assertIn('u.department_id = ANY(%(depts)s::int[])', text)
        # Уволенных и уволившихся в списке быть не должно.
        self.assertIn("u.status = 'working'", text)
        # И себя самого тоже.
        self.assertIn('u.id <> %(actor)s', text)

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

        Файл читаем с диска и режем по именам функций, а НЕ через
        inspect.getsource: тот берёт строки по номерам, запомненным при импорте,
        и стоит поправить модуль во время долгого прогона — отдаёт чужой кусок.
        Ровно так этот тест и покраснел на ровном месте 25.08.2026.
        """
        text = (Path(guests.__file__).read_text(encoding='utf-8')
                .split('def create_grant(')[1].split('\ndef ')[0])
        update = re.search(r'UPDATE wiki_guest_access.*?RETURNING id', text, re.S)
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

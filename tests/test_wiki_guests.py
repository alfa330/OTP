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
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

from wiki import guests, queries, structure
from wiki.access import (
    ROLE_LEVELS,
    guest_grant_ceiling,
    may_grant_guest_in_department,
    may_grant_guest_permanent,
    may_grant_guest_space,
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


class GuestSpaceRightTest(unittest.TestCase):
    """Третий объект выдачи — пространство целиком (владелец, 11.09.2026).

    Право на него НЕ лестница и не мастер-ключ: владельцу был задан прямой
    вопрос «кто получает это право», и выбран самый узкий из предложенных
    ответов — роль OTP super_admin. Администратор вики, которому весь остальной
    раздел выдаёт мастер-ключ, пространства не раздаёт.

    Страж нужен именно потому, что соблазн «приравнять как везде» здесь
    постоянный: рядом, в routes_structure._may_manage_space, роль вики как раз
    приравнена — и это ДРУГОЕ решение про другое действие.
    """

    def test_only_super_admin_grants_a_space(self):
        self.assertTrue(may_grant_guest_space('super_admin'))
        for role in ('admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee'):
            with self.subTest(role=role):
                self.assertFalse(may_grant_guest_space(role))

    def test_unknown_and_empty_roles_grant_nothing(self):
        # Незнакомая должность не должна проходить «по умолчанию»: опечатка в
        # поле role здесь означала бы выдачу чужой вики целиком.
        for role in (None, '', 'директор', 'wiki_admin', 'манагер'):
            with self.subTest(role=role):
                self.assertFalse(may_grant_guest_space(role))

    def test_space_right_is_not_the_ladder(self):
        """Потолок должности и право на пространство — разные вопросы.

        У руководителя потолок есть (он выдаёт супервайзерам и операторам), а
        пространство он не раздаёт. Свяжи их — и следующее послабление потолка
        молча раздало бы пространства.
        """
        self.assertIsNotNone(guest_grant_ceiling('admin'))
        self.assertFalse(may_grant_guest_space('admin'))
        # И наоборот: мастер-ключ роли вики поднимает потолок до максимума, но
        # права на пространство не даёт — функция про него вовсе не спрашивает.
        self.assertEqual(guest_grant_ceiling('trainer', is_wiki_admin=True),
                         ROLE_LEVELS['super_admin'])
        self.assertFalse(may_grant_guest_space('trainer'))


class GuestKindTest(unittest.TestCase):
    """Вид выдачи считается ОДНОЙ функцией на весь раздел.

    Список, баннер и /ping отвечают на вопрос «что выдано» одинаково только
    пока отвечает одна функция. Разложенный по трём местам тернарник — это ровно
    тот способ, каким в этой вике уже расходились два вычислителя одного и того
    же (см. шапку wiki/articles.py).
    """

    def test_three_kinds(self):
        self.assertEqual(guests.grant_kind({'section_id': 7}), 'section')
        self.assertEqual(guests.grant_kind({'article_id': 3}), 'article')
        self.assertEqual(guests.grant_kind({'space_id': 2}), 'space')

    def test_article_wins_over_space_and_section(self):
        # Ограничение wiki_guest_access_one_object держит ровно одну непустую
        # колонку, но порядок ветвей всё равно обязан быть определённым: строка
        # из старой базы или из ручного INSERT не должна читаться по-разному в
        # списке и в баннере.
        self.assertEqual(
            guests.grant_kind({'article_id': 3, 'space_id': 2, 'section_id': 7}),
            'article')
        self.assertEqual(guests.grant_kind({'space_id': 2, 'section_id': 7}), 'space')

    def test_empty_row_is_a_section(self):
        # Пустая строка ограничением запрещена; если она всё же встретилась,
        # «Удалённый объект» в подписи честнее, чем вид выдачи, которого нет.
        self.assertEqual(guests.grant_kind({}), 'section')


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

    def test_every_guest_branch_filters_revocation_and_expiry(self):
        """Не «условие встречается», а «оно есть в КАЖДОЙ ветке».

        Проверка выше (assertIn) проходит, даже если условие стоит в одной ветке
        из двух: с 11.09.2026 у гостевой CTE их две — выдача раздела и выдача
        пространства. Забудь отсечение во второй — и отозванная выдача целого
        пространства продолжала бы открывать чужую вику, а в списке значилась бы
        отозванной. Считаем обращения к таблице и сверяем с числом отсечений.
        """
        for sql in self.GUEST_SQL:
            with self.subTest(sql=_normalize(sql)[:60]):
                text = _normalize(sql)
                reads = text.count('FROM wiki_guest_access')
                self.assertGreater(reads, 0)
                self.assertEqual(text.count('revoked_at IS NULL'), reads)
                self.assertEqual(
                    text.count(
                        "expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"),
                    reads)

    def test_guest_perimeter_knows_the_space_grant(self):
        """Выдача пространства втекает в тот же guest_seed, что и раздел.

        Отдельным CTE она получила бы свою щель в границе пространства
        (_SPACE_GATE_SQL смотрит ровно на guest_seed/guest_tree), своё отсечение
        и своё попадание в оба режима периметра — то есть четыре места, где
        забытая правка открывает гостю дерево без статей или наоборот.
        """
        cte = _normalize(queries._GUEST_SECTIONS_CTE)
        self.assertIn('g.space_id IS NOT NULL', cte)
        self.assertIn('JOIN wiki_sections s ON s.space_id = g.space_id', cte)
        # Архив пространства гостю не открывается: он убран из оборота.
        self.assertIn("s.status = 'active'", cte)
        # Раскрывать в этой ветке нечего — она и так перечисляет все разделы.
        self.assertIn('FALSE AS deep', cte)

    def test_article_expiry_covers_the_space_grant(self):
        """Статья, открытая выдачей ПРОСТРАНСТВА, обязана иметь срок.

        Самый злой из молчаливых отказов раздела: статья в витрине есть (её
        раздел приехал в периметр гостевой веткой), а открыть нельзя —
        guest_allows_read приходит из этого запроса, и без ветки про
        пространство resolve_article_permissions вернёт can_read=False на
        статье, которую человеку только что показали списком.
        """
        text = _normalize(guests._ARTICLE_GRANT_SQL)
        self.assertIn('g.space_id IS NOT NULL', text)
        self.assertIn('JOIN wiki_sections s ON s.space_id = g.space_id', text)

    def test_switcher_shows_the_granted_space(self):
        """Пространство выдачи ищется тремя способами сразу.

        Прямо названное (выдача пространства), пространство выданного раздела и
        пространство разделов выданной статьи. Забудь первое — и человек с
        выданной вики не нашёл бы её в переключателе: разделы посчитаны, а
        прийти к ним некуда.
        """
        source = (Path(queries.__file__).read_text(encoding='utf-8')
                  .split('def spaces_for_user(')[1].split('\ndef ')[0])
        self.assertIn('COALESCE(g.space_id, gs.space_id, gass.space_id)',
                      _normalize(source))

    def test_create_grant_tells_three_objects_apart(self):
        """Повторная выдача продлевает выдачу ТОГО ЖЕ объекта.

        Условие поиска прежней строки обязано сравнивать все три колонки: без
        space_id выдача пространства нашла бы выдачу раздела того же человека
        (у неё section_id заполнен, а article_id пуст — и «пусто = пусто»
        сошлось бы) и молча продлила бы не то.
        """
        text = (Path(guests.__file__).read_text(encoding='utf-8')
                .split('def create_grant(')[1].split('\ndef ')[0])
        update = re.search(r'UPDATE wiki_guest_access.*?RETURNING id', text, re.S)
        self.assertIsNotNone(update)
        for column in ('section_id', 'article_id', 'space_id'):
            with self.subTest(column=column):
                self.assertIn('%s IS NOT DISTINCT FROM' % column,
                              _normalize(update.group(0)))
        insert = re.search(r'INSERT INTO wiki_guest_access.*?RETURNING id', text, re.S)
        self.assertIsNotNone(insert)
        self.assertIn('space_id', _normalize(insert.group(0)))

    def test_grant_list_tells_the_granted_space_from_the_host_space(self):
        """Две разные колонки пространства в одном ответе — и их нельзя путать.

        space_id — «где лежит выданное» (у выдачи раздела он тоже заполнен),
        granted_space_id — «выдано само пространство». Считай вид выдачи по
        первому — и каждая выдача раздела прочиталась бы как выдача всей вики.
        """
        self.assertIn('granted_space_id', guests._GRANT_KEYS)
        self.assertIn('space_id', guests._GRANT_KEYS)
        text = (Path(guests.__file__).read_text(encoding='utf-8')
                .split('def list_grants(')[1].split('\ndef ')[0])
        self.assertIn("item['granted_space_id']", text)
        # Выдача пространства не проваливается сквозь фильтр по пространству:
        # её делают ИЗ своей вики, и выдавший обязан увидеть её в списке.
        self.assertIn('g.space_id IS NOT NULL', _normalize(guests._GRANT_LIST_SQL))

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


class _RowsCursor:
    """Курсор, отдающий заранее заданные строки: SQL здесь не проверяется —
    проверяется, во что функция превращает ответ базы."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return list(self.rows)


class GuestPermanentTest(unittest.TestCase):
    """Бессрочная выдача (решение владельца 09.11.2026, глава ООЗ, 476).

    Первые две поставлены в базе; с 25.11.2026 её выдаёт формой супер-админ
    (GuestPermanentRightTest и GuestPermanentRoutesTest ниже). Читающие запросы
    её не различают — для них это просто дальний срок, — поэтому стражи здесь
    на два других места: что срок «никогда» не уходит человеку датой 31.12.9999
    и что ни повторная выдача, ни продление не превращают «насовсем» обратно в
    «до такого-то числа».
    """

    PERMANENT = guests.PERMANENT_EXPIRES_AT

    def test_the_mark_and_the_driver_infinity_are_both_permanent(self):
        """'infinity', записанный руками, драйвер отдаёт как datetime.max —
        это тоже бессрочно; обычный и пустой срок — нет."""
        self.assertTrue(guests.is_permanent(self.PERMANENT))
        self.assertTrue(guests.is_permanent(datetime.max))
        self.assertFalse(guests.is_permanent(NOW + timedelta(days=MAX_GUEST_DAYS)))
        self.assertFalse(guests.is_permanent(datetime(9998, 12, 31, 23, 59, 59)))
        self.assertFalse(guests.is_permanent(None))

    def test_sql_floor_and_python_agree(self):
        """Порог в SQL и признак в питоне — одна граница: первый день 9999 года."""
        floor = guests._PERMANENT_FLOOR
        self.assertTrue(guests.is_permanent(floor))
        self.assertFalse(guests.is_permanent(floor - timedelta(seconds=1)))
        self.assertLessEqual(floor, self.PERMANENT)

    def test_days_and_dates_never_reach_permanent(self):
        """Потолок дней на месте: днями и датой дальше 14 дней не уйти — ни
        датой 31.12.9999, ни через resolve_term без признака «бессрочно»."""
        self.assertFalse(guests.is_permanent(
            guests.resolve_expiry(NOW, days=MAX_GUEST_DAYS)))
        with self.assertRaises(ValueError):
            guests.resolve_expiry(NOW, until='9999-12-31')
        with self.assertRaises(ValueError):
            guests.resolve_term(NOW, until='9999-12-31')

    def test_permanent_is_active_until_revoked(self):
        self.assertEqual('active', guests.grant_status(
            {'revoked_at': None, 'expires_at': self.PERMANENT}, NOW))
        self.assertEqual('revoked', guests.grant_status(
            {'revoked_at': NOW, 'expires_at': self.PERMANENT}, NOW))

    def test_no_countdown_for_permanent(self):
        """«Осталось 2 914 000 дней» — правда, но шум."""
        self.assertIsNone(guests.days_left(self.PERMANENT, NOW))
        self.assertIsNone(guests.days_left(datetime.max, NOW))

    def test_payload_never_carries_the_mark(self):
        self.assertEqual(guests.expiry_payload(self.PERMANENT, NOW),
                         {'expires_at': None, 'days_left': None, 'permanent': True})
        until = guests.resolve_expiry(NOW, days=3)
        self.assertEqual(guests.expiry_payload(until, NOW),
                         {'expires_at': '2026-08-28T23:59:59', 'days_left': 3,
                          'permanent': False})

    def test_banner_feed_marks_permanent(self):
        """/ping: бессрочная выдача в списке есть, но без даты — по признаку
        баннер молчит о ней (guestAccess.js: bannerText)."""
        created = datetime(2026, 11, 9, 11, 23, 31)
        cursor = _RowsCursor([
            (8, None, None, 11, False, self.PERMANENT, None, created,
             None, None, None, 'Таксопарки'),
            (5, 37, None, None, True, datetime(2026, 8, 27, 23, 59, 59), None, created,
             'Регламент', None, None, None),
        ])
        items = {item['id']: item for item in guests.my_active_grants(cursor, 476, now=NOW)}
        self.assertTrue(items[8]['permanent'])
        self.assertIsNone(items[8]['expires_at'])
        self.assertIsNone(items[8]['days_left'])
        self.assertEqual('space', items[8]['kind'])
        self.assertFalse(items[5]['permanent'])
        self.assertEqual('2026-08-27T23:59:59', items[5]['expires_at'])
        self.assertEqual(2, items[5]['days_left'])

    def test_grant_list_marks_permanent(self):
        """Список выдач: «бессрочно» и статус «действует», даты нет."""
        row = dict.fromkeys(guests._GRANT_KEYS)
        row.update(id=8, user_id=476, user_name='Сотрудник', expires_at=self.PERMANENT,
                   created_at=datetime(2026, 11, 9, 11, 23, 31), granted_space_id=11,
                   space_id=11, space_name='Таксопарки', include_subsections=False)
        cursor = _RowsCursor([tuple(row[key] for key in guests._GRANT_KEYS)])
        [item] = guests.list_grants(cursor, actor_id=448, section_ids=(),
                                    unbounded=True, now=NOW)
        self.assertEqual('active', item['status'])
        self.assertTrue(item['permanent'])
        self.assertIsNone(item['expires_at'])
        self.assertIsNone(item['days_left'])
        self.assertEqual('space', item['kind'])

    def _function_source(self, module, name):
        return (Path(module.__file__).read_text(encoding='utf-8')
                .split('def %s(' % name)[1].split('\ndef ')[0])

    def test_regrant_does_not_shorten_a_permanent_grant(self):
        """Повторная выдача продлевает ДО «сейчас + N дней» — бессрочную она
        укоротила бы. Ветка UPDATE обязана её обходить."""
        text = self._function_source(guests, 'create_grant')
        update = re.search(r'UPDATE wiki_guest_access.*?RETURNING id', text, re.S)
        self.assertIn('AND expires_at < %(permanent)s', _normalize(update.group(0)))
        self.assertIn("'permanent': _PERMANENT_FLOOR", text)

    def test_extension_does_not_shorten_a_permanent_grant(self):
        text = _normalize(self._function_source(guests, 'extend_grant'))
        self.assertIn('WHERE id = %s AND expires_at < %s', text)
        self.assertIn('_PERMANENT_FLOOR', text)

    def test_permanent_lookup_tells_three_objects_apart(self):
        """Поиск бессрочной выдачи — тем же объектом, что и create_grant."""
        text = _normalize(self._function_source(guests, 'permanent_grant_id'))
        for column in ('section_id', 'article_id', 'space_id'):
            with self.subTest(column=column):
                self.assertIn('%s IS NOT DISTINCT FROM' % column, text)
        self.assertIn('revoked_at IS NULL', text)
        self.assertIn('expires_at >= %(permanent)s', text)

    def test_routes_refuse_before_writing(self):
        """Обе двери отвечают словами ДО записи: выдача — 409 до create_grant,
        продление — 400 до extend_grant, и 409 вместо журнала, если продление
        проиграло гонку бессрочной выдаче (GuestPermanentRoutesTest)."""
        from wiki import routes_guests
        source = Path(routes_guests.__file__).read_text(encoding='utf-8')
        create = source.split("def wiki_guests_create(")[1].split('\n    def ')[0]
        self.assertLess(create.index('wiki_guests.permanent_grant_id('),
                        create.index('wiki_guests.create_grant('))
        change = source.split("def wiki_guests_change(")[1]
        self.assertLess(change.index("wiki_guests.is_permanent(grant['expires_at'])"),
                        change.index('wiki_guests.extend_grant('))
        self.assertEqual(3, source.count('"WIKI_GUEST_PERMANENT"'))

    def test_article_answer_uses_the_shared_payload(self):
        """Статья берёт срок той же функцией, что баннер и список."""
        from wiki import routes_articles
        source = Path(routes_articles.__file__).read_text(encoding='utf-8')
        self.assertIn('wiki_guests.expiry_payload(guest_until,', source)


class GuestPermanentRightTest(unittest.TestCase):
    """«Бессрочно» в форме — супер-админ, любой объект (владелец, 25.11.2026).

    Вопрос был задан прямо — «кому сделать „Бессрочно“ рядом с „14 дней“», — и
    из трёх ответов (супер-админ на любой объект / супер-админ только на
    пространство / все, кто выдаёт) выбран первый. Руководителю, супервайзеру и
    администратору вики остаётся потолок в 14 дней.
    """

    def test_only_super_admin_grants_permanent(self):
        self.assertTrue(may_grant_guest_permanent('super_admin'))
        self.assertTrue(may_grant_guest_permanent(' Super_Admin '))
        for role in ('admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee',
                     None, '', 'директор', 'wiki_admin'):
            with self.subTest(role=role):
                self.assertFalse(may_grant_guest_permanent(role))

    def test_permanent_is_not_the_ladder(self):
        """Потолок по чину и срок — разные вопросы: мастер-ключ роли вики
        поднимает потолок до директорского, а «Бессрочно» не даёт."""
        self.assertEqual(guest_grant_ceiling('admin', is_wiki_admin=True),
                         ROLE_LEVELS['super_admin'])
        self.assertFalse(may_grant_guest_permanent('admin'))


class GuestTermTest(unittest.TestCase):
    """resolve_term: «бессрочно» ИЛИ день с часом — ровно один ответ."""

    def test_permanent_is_the_mark(self):
        until = guests.resolve_term(NOW, permanent=True)
        self.assertEqual(guests.PERMANENT_EXPIRES_AT, until)
        self.assertTrue(guests.is_permanent(until))

    def test_permanent_with_a_day_or_an_hour_is_refused(self):
        """«Бессрочно до 18:00» — два ответа на один вопрос."""
        for extra in ({'days': 3}, {'days': 0}, {'until': '2026-08-27'},
                      {'at_time': '18:00'}):
            with self.subTest(**extra):
                with self.assertRaises(ValueError) as caught:
                    guests.resolve_term(NOW, permanent=True, **extra)
                self.assertIn('Бессрочно', str(caught.exception))

    def test_empty_fields_do_not_count(self):
        """Пустая строка — «не назвал»: так форма шлёт пустое поле."""
        self.assertEqual(guests.PERMANENT_EXPIRES_AT, guests.resolve_term(
            NOW, permanent=True, days=None, until='', at_time=' '))

    def test_without_permanent_it_is_resolve_expiry(self):
        self.assertEqual(guests.resolve_expiry(NOW, days=3),
                         guests.resolve_term(NOW, days=3))
        self.assertEqual(guests.resolve_expiry(NOW, days=0, at_time='18:00'),
                         guests.resolve_term(NOW, days=0, at_time='18:00', permanent=False))
        with self.assertRaises(ValueError):
            guests.resolve_term(NOW)
        with self.assertRaises(ValueError):
            guests.resolve_term(NOW, days=MAX_GUEST_DAYS + 1)


try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None


class _GrantCursor:
    """Курсор для ручек выдачи: отвечает только на запрос о получателе.

    Всё остальное, что ходит в базу, подменено на уровне функций модулей — здесь
    проверяются решения ручек (право, срок, ответ, журнал), а не SQL.
    """

    def __init__(self, target, events):
        self.target = target
        self.calls = []
        self.events = events

    def execute(self, sql, params=None):
        flat = ' '.join(sql.split())
        self.calls.append((flat, params))
        if 'pg_advisory_xact_lock' in flat:
            self.events.append(('lock', params))

    def fetchone(self):
        sql = self.calls[-1][0] if self.calls else ''
        if 'FROM users u WHERE u.id' in sql:
            return self.target
        return None

    def fetchall(self):
        return []


@unittest.skipIf(Flask is None, 'flask не установлен')
class GuestPermanentRoutesTest(unittest.TestCase):
    """Настоящие ручки /guests с подменённым слоем данных.

    Стражи на двери, а не на функции права: функцию легко держать верной и
    забыть позвать — ровно так в этом разделе уже ломалось молча (см. шапку
    wiki/access.py).
    """

    # Получатель: работающий оператор чужого отдела. Супер-админу граница отдела
    # не мешает, а руководителю отказ «по сроку» должен прийти РАНЬШЕ, чем по
    # отделу, — поэтому отдел у получателя и руководителя один.
    TARGET = ('operator', 'Сотрудник', 'working', 7, False)

    def build(self, role='super_admin', *, wiki_roles=(), permanent_id=None,
              existing=None, grant=None, extend_hits=True):
        from wiki import routes_guests  # noqa: F401 — роуты регистрирует blueprint
        from wiki.routes import build_wiki_blueprint

        self.created, self.extended, self.logged, self.events = [], [], [], []
        context = {
            'user_id': 448, 'otp_role': role, 'department_id': 7,
            'direction_id': None, 'headed_department_ids': [], 'group_ids': [],
            'wiki_roles': list(wiki_roles), 'access_mode': 'auto',
        }
        cursor = _GrantCursor(self.TARGET, self.events)

        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def _create(_cursor, **kwargs):
            self.created.append(kwargs)
            self.events.append(('create', None))
            # existing — у человека уже есть ВРЕМЕННАЯ выдача того же объекта:
            # create_grant её продлевает и отвечает created=False.
            return 55, existing is None

        def _extend(_cursor, grant_id, expires_at):
            self.extended.append((grant_id, expires_at))
            # extend_hits=False — строку не тронули: пока шёл запрос, выдачу
            # уже сделали бессрочной (условие «expires_at < порога» в базе).
            return extend_hits

        def _permanent_check(_cursor, **kwargs):
            self.events.append(('permanent_check', None))
            return permanent_id

        patches = [
            (queries, 'load_access_context', lambda _c, _u: dict(context)),
            (queries, 'granted_rule_rights', lambda _c, _s, _u: ({}, [])),
            (queries, 'log_action', lambda _c, **kw: self.logged.append(kw)),
            (guests, 'shareable_section_ids',
             lambda _c, _ctx, _s, departments=None: None if departments is None else {3}),
            (guests, 'permanent_grant_id', _permanent_check),
            (guests, 'create_grant', _create),
            (guests, 'extend_grant', _extend),
            (guests, 'get_grant', lambda _c, _id: dict(grant) if grant else None),
            (guests, 'article_section_ids', lambda _c, _id: {3}),
            (structure, 'space_is_active', lambda _c, _id: True),
            (structure, 'section_exists', lambda _c, _id: 3),
        ]
        for module, name, replacement in patches:
            original = getattr(module, name)
            setattr(module, name, replacement)
            self.addCleanup(setattr, module, name, original)

        app = Flask(__name__)
        app.register_blueprint(build_wiki_blueprint(
            db=db, require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=lambda _user_id, cursor=None: True,
            client_ip=lambda: '127.0.0.1',
            gcs={'signed_url': lambda *a, **k: 'https://x'},
            session_id_provider=lambda: 'None',
        ))
        app.config['TESTING'] = True
        return app.test_client()

    TEMPORARY = {'id': 21, 'user_id': 476, 'section_id': 3, 'article_id': None,
                 'space_id': None, 'granted_by': 448,
                 'expires_at': datetime(2030, 1, 5, 23, 59, 59), 'revoked_at': None,
                 'include_subsections': True, 'reason': None, 'kind': 'section'}

    # ── Признак для формы ────────────────────────────────────────────────

    def test_list_tells_the_form_who_may_grant_permanent(self):
        for role, wiki_roles, expected in (
                ('super_admin', (), True),
                ('admin', (), False),
                ('sv', (), False),
                # Мастер-ключ роли вики: потолок по чину снят, срок — нет.
                ('admin', ({'id': 5, 'code': 'wiki_admin', 'can_read': True,
                            'can_manage_access': True},), False)):
            with self.subTest(role=role, wiki_admin=bool(wiki_roles)):
                with mock.patch.object(guests, 'list_grants',
                                                lambda *a, **k: []):
                    client = self.build(role, wiki_roles=wiki_roles)
                    response = client.get('/api/wiki/guests')
                self.assertEqual(200, response.status_code, response.get_json())
                self.assertIs(expected, response.get_json()['can_grant_permanent'])
                self.assertEqual(MAX_GUEST_DAYS, response.get_json()['max_days'])

    def test_list_flag_is_not_the_space_right(self):
        """Признак формы считается своим правом, а не правом на пространство:
        расширь однажды второе — кнопка «Бессрочно» не должна появиться следом."""
        with mock.patch('wiki.access.may_grant_guest_space', lambda _r: True), \
                mock.patch.object(guests, 'list_grants', lambda *a, **k: []):
            data = self.build('admin').get('/api/wiki/guests').get_json()
        self.assertIs(True, data['can_grant_space'])
        self.assertIs(False, data['can_grant_permanent'])

    # ── Выдача ───────────────────────────────────────────────────────────

    def test_super_admin_grants_any_object_permanently(self):
        """Пространство, раздел и статья — «любой объект» дословно."""
        for body, entity in (({'space_id': 11}, 'space'),
                             ({'section_id': 3}, 'section'),
                             ({'article_id': 7}, 'article')):
            with self.subTest(entity=entity):
                client = self.build('super_admin')
                response = client.post('/api/wiki/guests', json=dict(
                    body, user_id=476, permanent=True))
                self.assertEqual(200, response.status_code, response.get_json())
                data = response.get_json()
                self.assertIs(True, data['permanent'])
                self.assertIsNone(data['expires_at'])
                self.assertTrue(data['created'])
                [call] = self.created
                self.assertEqual(guests.PERMANENT_EXPIRES_AT, call['expires_at'])
                [record] = [r for r in self.logged if r['action'] == 'guest.grant']
                self.assertEqual(entity, record['entity_type'])
                self.assertIs(True, record['details']['permanent'])
                self.assertIsNone(record['details']['expires_at'])

    def test_regrant_turns_a_temporary_grant_permanent(self):
        """Повторная выдача продлевает прежнюю — «бессрочно» делает её бессрочной,
        и журнал называет это продлением, а тост — «стал бессрочным»."""
        client = self.build('super_admin', existing=True)
        response = client.post('/api/wiki/guests', json={
            'section_id': 3, 'user_id': 476, 'permanent': True})
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertFalse(response.get_json()['created'])
        self.assertIs(True, response.get_json()['permanent'])
        [record] = self.logged
        self.assertEqual('guest.extend', record['action'])
        self.assertIs(True, record['details']['permanent'])

    def test_others_may_not_grant_permanent(self):
        """Руководителю и супервайзеру — 403 словами, и ДО записи. Даже если
        право на пространство однажды расширят: «бессрочно» — своё решение."""
        for role in ('admin', 'sv'):
            with self.subTest(role=role):
                with mock.patch(
                        'wiki.access.may_grant_guest_space', lambda _r: True):
                    client = self.build(role)
                    response = client.post('/api/wiki/guests', json={
                        'section_id': 3, 'user_id': 476, 'permanent': True})
                self.assertEqual(403, response.status_code, response.get_json())
                self.assertEqual('WIKI_GUEST_PERMANENT_RIGHT',
                                 response.get_json()['code'])
                self.assertEqual([], self.created)
                self.assertEqual([], self.logged)

    def test_right_is_checked_before_the_term(self):
        """«Бессрочно» вместе с днём от руководителя — отказ ПО ПРАВУ (403), а не
        «уберите день» (400): чинить ему нечего, кнопки у него и нет."""
        client = self.build('admin')
        response = client.post('/api/wiki/guests', json={
            'section_id': 3, 'user_id': 476, 'permanent': True, 'days': 7})
        self.assertEqual(403, response.status_code, response.get_json())
        self.assertEqual('WIKI_GUEST_PERMANENT_RIGHT', response.get_json()['code'])

    def test_others_keep_the_two_week_ceiling(self):
        client = self.build('admin')
        response = client.post('/api/wiki/guests', json={
            'section_id': 3, 'user_id': 476, 'days': MAX_GUEST_DAYS + 1})
        self.assertEqual(400, response.status_code)
        self.assertEqual([], self.created)

    def test_permanent_with_a_day_is_refused(self):
        client = self.build('super_admin')
        response = client.post('/api/wiki/guests', json={
            'section_id': 3, 'user_id': 476, 'permanent': True, 'days': 7})
        self.assertEqual(400, response.status_code)
        self.assertIn('Бессрочно', response.get_json()['error'])
        self.assertEqual([], self.created)

    def test_only_boolean_true_means_permanent(self):
        """Строка "true" из чужого клиента — не «да»: срока нет, и это 400, а не
        бессрочная выдача."""
        client = self.build('super_admin')
        response = client.post('/api/wiki/guests', json={
            'section_id': 3, 'user_id': 476, 'permanent': 'true'})
        self.assertEqual(400, response.status_code)
        self.assertEqual([], self.created)

    def test_grant_is_serialized_per_person_and_object(self):
        """Проверка «нет ли бессрочной» и запись — под замком на пару «человек +
        объект»: иначе две одновременные выдачи заводили бы две строки."""
        client = self.build('super_admin')
        response = client.post('/api/wiki/guests', json={
            'space_id': 12, 'user_id': 476, 'permanent': True})
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual([('lock', ('wiki_guest:476:space:12',)),
                          ('permanent_check', None), ('create', None)], self.events)

    def test_lock_key_tells_people_and_objects_apart(self):
        keys = []
        for kwargs in ({'section_id': 3}, {'article_id': 3}, {'space_id': 3}):
            cursor = _GrantCursor(None, [])
            guests.lock_grant_object(cursor, user_id=476, **kwargs)
            [(_kind, params)] = cursor.events
            keys.append(params[0])
        cursor = _GrantCursor(None, [])
        guests.lock_grant_object(cursor, user_id=477, section_id=3)
        keys.append(cursor.events[0][1][0])
        self.assertEqual(['wiki_guest:476:section:3', 'wiki_guest:476:article:3',
                          'wiki_guest:476:space:3', 'wiki_guest:477:section:3'], keys)

    def test_second_permanent_grant_is_refused_in_words(self):
        client = self.build('super_admin', permanent_id=9)
        response = client.post('/api/wiki/guests', json={
            'space_id': 12, 'user_id': 476, 'permanent': True})
        self.assertEqual(409, response.status_code)
        self.assertEqual('WIKI_GUEST_PERMANENT', response.get_json()['code'])
        self.assertEqual([], self.created)

    def test_temporary_grant_still_answers_with_a_date(self):
        client = self.build('super_admin')
        response = client.post('/api/wiki/guests', json={
            'section_id': 3, 'user_id': 476, 'days': 3})
        self.assertEqual(200, response.status_code, response.get_json())
        data = response.get_json()
        self.assertIs(False, data['permanent'])
        self.assertTrue(data['expires_at'].endswith('T23:59:59'))
        [call] = self.created
        self.assertFalse(guests.is_permanent(call['expires_at']))
        self.assertIs(False, self.logged[0]['details']['permanent'])
        self.assertEqual(data['expires_at'], self.logged[0]['details']['expires_at'])

    # ── Продление ────────────────────────────────────────────────────────

    def test_super_admin_makes_a_temporary_grant_permanent(self):
        client = self.build('super_admin', grant=self.TEMPORARY)
        response = client.patch('/api/wiki/guests/21', json={'permanent': True})
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual({'status': 'extended', 'expires_at': None, 'permanent': True},
                         response.get_json())
        self.assertEqual([(21, guests.PERMANENT_EXPIRES_AT)], self.extended)
        [record] = self.logged
        self.assertEqual('guest.extend', record['action'])
        self.assertIs(True, record['details']['permanent'])
        self.assertIsNone(record['details']['expires_at'])

    def test_extension_that_lost_the_race_tells_the_truth(self):
        """Пока шёл запрос «продлить на 3 дня», выдачу сделали бессрочной:
        extend_grant строку не тронул. Не «продлён до …» и не запись в журнал."""
        client = self.build('super_admin', grant=self.TEMPORARY, extend_hits=False)
        response = client.patch('/api/wiki/guests/21', json={'days': 3})
        self.assertEqual(409, response.status_code, response.get_json())
        self.assertEqual('WIKI_GUEST_PERMANENT', response.get_json()['code'])
        self.assertEqual([], self.logged)

    def test_others_may_not_extend_to_permanent(self):
        """Своя выдача руководителя (granted_by = он) — трогать её он вправе,
        а сделать бессрочной — нет."""
        client = self.build('admin', grant=self.TEMPORARY)
        response = client.patch('/api/wiki/guests/21', json={'permanent': True})
        self.assertEqual(403, response.status_code, response.get_json())
        self.assertEqual('WIKI_GUEST_PERMANENT_RIGHT', response.get_json()['code'])
        self.assertEqual([], self.extended)

    def test_permanent_grant_is_still_not_extended(self):
        grant = dict(self.TEMPORARY, expires_at=guests.PERMANENT_EXPIRES_AT)
        client = self.build('super_admin', grant=grant)
        response = client.patch('/api/wiki/guests/21', json={'permanent': True})
        self.assertEqual(400, response.status_code)
        self.assertEqual('WIKI_GUEST_PERMANENT', response.get_json()['code'])
        self.assertEqual([], self.extended)

    def test_plain_extension_still_works(self):
        client = self.build('admin', grant=self.TEMPORARY)
        response = client.patch('/api/wiki/guests/21', json={'days': 3})
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertIs(False, response.get_json()['permanent'])
        [(grant_id, until)] = self.extended
        self.assertEqual(21, grant_id)
        self.assertFalse(guests.is_permanent(until))


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

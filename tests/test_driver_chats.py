# -*- coding: utf-8 -*-
"""Раздел «Чаты водителей» (задача #271): права, номер, чаты, журнал.

Что здесь сторожится и почему именно это:

* **Периметр.** Раздел даёт рядовому оператору переписку ЛЮБОГО водителя по
  номеру телефона — это самое широкое право, какое портал выдавал операторам.
  Каждая строка матрицы прав проверяется отдельно, включая два исключения
  внутри самого СЗоВ (чат-менеджер и тренер), которых нет у соседних разделов.
* **Номер телефона.** Оператор вводит его как привык, а вендор хранит как
  прислали. Промах нормализации выглядит как «водителя нет», а не как ошибка
  формата, и ищут его потом не там.
* **Подписи.** Словари видов события живут в двух местах (питон не читает js).
  Разойдись они — человек увидел бы в выгрузке не то слово, что на экране.
* **Формулировки журнала.** «Открыл переписку», а не «Сделал скриншот»: снимок
  экрана системе не виден в принципе, и называть одно другим в документе, по
  которому разбирают утечку, нельзя.

QR-гейт этого раздела проверяется отдельно, в общем
tests/test_sensitive_section_qr_gate.py — там же, где гейты вики, обращений и
посылок: правило одно на портал, и разъехаться копии не должны.
"""

import json
import re
import sys
from contextlib import contextmanager
from unittest import mock
from unittest.mock import MagicMock
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None

from driver_chats import access, chat2desk, queries, report, schema  # noqa: E402
from driver_chats.routes import build_driver_chats_blueprint  # noqa: E402

APP_JSX = ROOT / 'src' / 'App.jsx'
JOURNAL_META = ROOT / 'src' / 'components' / 'driver_chats' / 'journalMeta.js'
VIEW_JSX = ROOT / 'src' / 'components' / 'driver_chats' / 'DriverChatsView.jsx'


def ctx(role='operator', department_code='szov', direction_model=None, headed=()):
    return {
        'user_id': 10,
        'name': 'Тест',
        'role': role,
        'department_id': 1,
        'department_code': department_code,
        'direction_model': direction_model,
        'headed_department_ids': list(headed),
        'headed_department_codes': [department_code] if headed else [],
    }


class RolloutTests(unittest.TestCase):
    """Стадия выката. Флаг снят 08.09.2026 — раздел открыт отделу.

    Владелец: «доступ для линии/основы СЗоВ, супервайзерам и руководителя Ару
    Омаровой, и так же будет виден суперадминам». Флаг оставлен в коде, чтобы
    закрыть раздел обратно одной строкой; правила периметра проверены классами
    ниже и от флага не зависят.
    """

    def test_flag_is_off_and_both_twins_agree(self):
        """Двойники обязаны стоять в одном положении. Разъедься они — либо пункт
        меню ведёт в 403 (фронт открыт, сервер закрыт), либо раздел доступен
        прямым адресом, но его не видно в меню."""
        self.assertFalse(access.ROLLOUT_SUPER_ADMIN_ONLY)
        source = APP_JSX.read_text(encoding='utf-8-sig')
        self.assertIn('const DRIVER_CHATS_ROLLOUT_SUPER_ADMIN_ONLY = false;', source)

    def test_the_department_is_inside_right_now(self):
        """Без всяких моков: то, что увидит живой человек сегодня."""
        self.assertTrue(access.can_open_section(ctx()))                    # оператор «Основы»
        self.assertTrue(access.can_open_section(ctx(role='sv')))           # супервайзер
        self.assertTrue(access.can_open_section(                            # Ару — глава СЗоВ
            ctx(role='admin', department_code='szov', headed=('szov',))))
        self.assertTrue(access.can_open_section(ctx(role='super_admin', department_code='')))
        self.assertFalse(access.can_open_section(ctx(direction_model='chat_manager')))
        self.assertFalse(access.can_open_section(ctx(role='trainer')))
        self.assertFalse(access.can_open_section(ctx(department_code='op')))

    def test_only_super_admin_passes_while_the_flag_is_on(self):
        with mock.patch.object(access, 'ROLLOUT_SUPER_ADMIN_ONLY', True):
            self.assertTrue(access.can_open_section(
                ctx(role='super_admin', department_code='')))
            for person in (ctx(), ctx(role='trainee'), ctx(role='sv'),
                           ctx(role='sv', direction_model='chat_manager'),
                           ctx(role='admin', department_code=''),
                           ctx(role='admin', department_code='szov', headed=('szov',))):
                with self.subTest(role=person['role']):
                    self.assertFalse(access.can_open_section(person))

    def test_removing_the_flag_restores_the_perimeter(self):
        with mock.patch.object(access, 'ROLLOUT_SUPER_ADMIN_ONLY', False):
            self.assertTrue(access.can_open_section(ctx()))
            self.assertTrue(access.can_open_section(ctx(role='sv')))
            self.assertFalse(access.can_open_section(ctx(direction_model='chat_manager')))


class SectionPerimeterTests(unittest.TestCase):
    """Кого пускать в раздел ПО ПЕРИМЕТРУ, без учёта стадии выката. Постановка:
    «доступен в отделе СЗоВ», «чат-менеджерам он виден не будет»."""

    def test_szov_line_staff_is_allowed(self):
        for role in ('operator', 'trainee', 'sv'):
            with self.subTest(role=role):
                self.assertTrue(access.section_perimeter_allows(ctx(role=role)))

    def test_rank_and_file_chat_manager_is_excluded_even_inside_szov(self):
        """Главный случай: по отделу он проходит, отсекает его направление.

        Раздел существует, чтобы оператор линии передал переписку ЕМУ; сами эти
        диалоги он видит у себя в Chat2Desk целиком и без посредника.
        """
        for role in ('operator', 'trainee'):
            with self.subTest(role=role):
                self.assertFalse(access.section_perimeter_allows(
                    ctx(role=role, direction_model='chat_manager')))

    def test_supervisor_of_chat_managers_does_pass(self):
        """Решение владельца 08.09.2026: доступ супервайзерам он назвал по РОЛИ.

        Исключение по направлению — про рядовых: им раздел открыл бы чужие
        диалоги вместо своих рабочих. У супервайзера работа как раз в чужих
        чатах, он их разбирает, и журнал ведётся в том числе для него.
        """
        person = ctx(role='sv', direction_model='chat_manager')
        self.assertTrue(access.section_perimeter_allows(person))
        self.assertTrue(access.can_view_journal(person))

    def test_chat_manager_model_outside_szov_is_not_the_same_people(self):
        """Модель `chat_manager` живёт в СЗоВ. Появись она в другом отделе — это
        другие люди с другой работой, и отсекать их этой проверкой нельзя;
        их и так не пустит граница отдела."""
        self.assertFalse(access.is_chat_manager(
            ctx(department_code='op', direction_model='chat_manager')))

    def test_trainer_is_excluded(self):
        """То же решение, что закрыло тренеру «Обращения» и «Посылки»."""
        self.assertFalse(access.section_perimeter_allows(ctx(role='trainer')))

    def test_other_departments_are_excluded(self):
        for code in ('op', 'tez', 'front_office', 'accounting', 'hr', ''):
            with self.subTest(code=code):
                self.assertFalse(access.section_perimeter_allows(ctx(department_code=code)))

    def test_super_admin_and_szov_head_are_allowed(self):
        self.assertTrue(access.section_perimeter_allows(ctx(role='super_admin', department_code='')))
        self.assertTrue(access.section_perimeter_allows(
            ctx(role='admin', department_code='szov', headed=('szov',))))

    def test_portal_admin_is_not_let_in_here(self):
        """В соседних разделах роль `admin` без своего отдела означает «видит
        всё». Здесь — нет: 08.09.2026 владелец сузил круг до «линия/основа,
        супервайзеры, Ару, суперадмины», а пятеро таких админов числятся в СЗоВ
        и получали бы вместе с разделом ещё и журнал о работе чужого отдела.

        Глава СЗоВ при этом проходит: назначение главой заменяет базовую роль.
        """
        for code in ('', 'szov', 'op'):
            with self.subTest(department_code=code):
                person = ctx(role='admin', department_code=code)
                self.assertFalse(access.section_perimeter_allows(person))
                self.assertFalse(access.can_view_journal(person))

    def test_head_of_another_department_is_not_a_global_admin(self):
        """Назначение главой ЗАМЕНЯЕТ базовую роль — действующая семантика
        портала. Иначе глава Бухгалтерии читал бы переписку водителей."""
        self.assertFalse(access.section_perimeter_allows(
            ctx(role='admin', department_code='accounting', headed=('accounting',))))

    def test_unknown_role_falls_to_the_closed_side(self):
        """Незнакомая роль сводится к оператору: закрыто и с QR — правильная
        сторона ошибки."""
        person = ctx(role='невиданная_роль')
        self.assertTrue(access.section_perimeter_allows(person))  # он всё ещё в СЗоВ
        self.assertTrue(access.requires_sensitive_qr(person))   # но подтверждает доступ


class JournalPerimeterTests(unittest.TestCase):
    """Кто видит журнал. Постановка: аккаунт Ару Омаровой, супервайзеры СЗоВ и
    суперадмины.

    Флаг выката здесь снят: пока он стоит, журнал доступен только супер-админу
    (как и весь раздел), а проверять надо правило, которое заработает после
    открытия раздела отделу.
    """

    def setUp(self):
        patcher = mock.patch.object(access, 'ROLLOUT_SUPER_ADMIN_ONLY', False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_journal_is_closed_to_supervisors_while_the_rollout_flag_is_on(self):
        """Сейчас, на обкатке, журнал видит только супер-админ — включая Ару."""
        with mock.patch.object(access, 'ROLLOUT_SUPER_ADMIN_ONLY', True):
            self.assertFalse(access.can_view_journal(ctx(role='sv')))
            self.assertTrue(access.can_view_journal(
                ctx(role='super_admin', department_code='')))

    def test_szov_supervisors_and_super_admins(self):
        self.assertTrue(access.can_view_journal(ctx(role='sv')))
        self.assertTrue(access.can_view_journal(ctx(role='super_admin', department_code='')))

    def test_szov_head_sees_the_journal_about_his_own_department(self):
        self.assertTrue(access.can_view_journal(
            ctx(role='admin', department_code='szov', headed=('szov',))))

    def test_operator_never_sees_the_journal(self):
        """Журнал ведётся ради контроля за оператором; «посмотреть, что про меня
        записано» — другой продукт."""
        for role in ('operator', 'trainee'):
            with self.subTest(role=role):
                self.assertFalse(access.can_view_journal(ctx(role=role)))

    def test_supervisor_of_another_department_sees_nothing(self):
        self.assertFalse(access.can_view_journal(ctx(role='sv', department_code='op')))

    def test_chat_manager_supervisor_sees_the_journal_too(self):
        """Она супервайзер СЗоВ, а журнал владелец дал супервайзерам (08.09.2026).

        До этого дня она не проходила даже в раздел: исключение по направлению
        считалось раньше роли.
        """
        self.assertTrue(access.can_view_journal(
            ctx(role='sv', direction_model='chat_manager')))

    def test_the_journal_gate_never_bypasses_the_section_gate(self):
        """Тот, кого не пустили в раздел, не видит и журнал."""
        for person in (ctx(role='sv', department_code='op'),
                       ctx(role='admin', department_code='szov'),
                       ctx(role='trainer')):
            with self.subTest(role=person['role'], dept=person['department_code']):
                self.assertFalse(access.can_open_section(person))
                self.assertFalse(access.can_view_journal(person))

    def test_journal_rule_has_no_hardcoded_person(self):
        """Ару Омарова проходит как глава СЗоВ, а не по своему id.

        Именной список ломается ровно в тот день, когда человек меняется, и
        ломается молча. Здесь он сломался бы сразу: у Ару в базе ДВЕ учётки —
        id 205 («Омарова Ару», роль sv, статус fired, ни одной сессии) и рабочая
        id 1 («Omarova Aru», роль admin), записанная главой СЗоВ. Любой из этих
        id в коде выдал бы доступ не тому человеку, а правило «глава этого
        отдела или его супервайзер» покрывает её как есть.

        Проверяем не имя (имя в пояснении как раз полезно), а МЕХАНИКУ: правило
        не имеет права смотреть ни на id человека, ни на равенство числу.
        """
        source = (ROOT / 'driver_chats' / 'access.py').read_text(encoding='utf-8')
        rules = source.split('def normalize_role')[1]
        self.assertNotRegex(rules, r'==\s*\d+\b',
                            'сравнение с числом в правилах доступа — это хардкод человека')
        self.assertNotIn("get('user_id')", rules,
                         'правило доступа не должно зависеть от id человека')


class QrPolicyTests(unittest.TestCase):
    def test_operator_and_trainee_are_gated(self):
        """Стажёр закрыт СОЗНАТЕЛЬНО — в отличие от «Посылок» и вики, где он
        проходит молча (parcels/access.py: сравнение ровно с 'operator')."""
        self.assertTrue(access.requires_sensitive_qr(ctx(role='operator')))
        self.assertTrue(access.requires_sensitive_qr(ctx(role='trainee')))

    def test_supervisor_admin_and_head_are_not_gated(self):
        for person in (ctx(role='sv'), ctx(role='super_admin', department_code=''),
                       ctx(role='admin', department_code=''),
                       ctx(role='operator', headed=('szov',))):
            with self.subTest(role=person['role'], headed=person['headed_department_codes']):
                self.assertFalse(access.requires_sensitive_qr(person))


class PhoneTests(unittest.TestCase):
    """Номер водителя. Оператор вводит как привык, вендор хранит как прислали."""

    def test_common_forms_normalize_to_one(self):
        for raw in ('77071234567', '+7 707 123 45 67', '8(707)123-45-67',
                    '87071234567', '7071234567', ' +7-707-123-45-67 '):
            with self.subTest(raw=raw):
                self.assertEqual(chat2desk.normalize_phone(raw), '77071234567')

    def test_foreign_numbers_are_found_too(self):
        """Поиск не только по Казахстану: в c2d_requests 1148 записей с другим
        кодом страны — Россия (79…), Узбекистан (998…), Киргизия (996…),
        Таджикистан (992…), Турция, Великобритания. Водитель с российской симкой
        — обычное дело, и «введите казахстанский номер» ему не ответ
        (замечание владельца 04.09.2026)."""
        for raw, expected in (
            ('79161234567', '79161234567'),      # Россия
            ('998901234567', '998901234567'),    # Узбекистан
            ('996555123456', '996555123456'),    # Киргизия
            ('992937654321', '992937654321'),    # Таджикистан
            ('447911123456', '447911123456'),    # Великобритания
            ('+90 532 123 45 67', '905321234567'),  # Турция, как её пишут
        ):
            with self.subTest(raw=raw):
                self.assertEqual(chat2desk.normalize_phone(raw), expected)

    def test_international_dialling_prefix_is_stripped(self):
        """«00» вместо «+» — то, как номер диктуют «чтобы набрать».

        Без этой поправки узбекский номер уходил в поиск четырнадцатизначным и
        не находился нигде: ни точным совпадением, ни хвостом.
        """
        self.assertEqual(chat2desk.normalize_phone('00998901234567'), '998901234567')
        self.assertEqual(chat2desk.normalize_phone('00 90 531 729 83 61'), '905317298361')
        # Внутренняя форма после снятия префикса тоже приводится к «7…».
        self.assertEqual(chat2desk.normalize_phone('0087071234567'), '77071234567')
        # А вот у номера, который без «00» стал бы короче десяти цифр, префикс
        # снимать нельзя — это не префикс, а часть чего-то другого.
        self.assertIsNone(chat2desk.normalize_phone('001234567'))

    def test_tail_is_the_same_in_every_way_of_writing(self):
        """Хвост — то, чем номер опознаётся, как бы его ни записали."""
        for raw in ('87071234567', '+7 707 123 45 67', '77071234567', '7071234567'):
            with self.subTest(raw=raw):
                self.assertEqual(chat2desk.phone_tail(raw), '071234567')
        for raw in ('+90 531 729 83 61', '905317298361', '00905317298361'):
            with self.subTest(raw=raw):
                self.assertEqual(chat2desk.phone_tail(raw), '317298361')

    def test_tail_is_nine_digits_and_not_invented_from_junk(self):
        """Девять — не на глаз, а два измерения сразу.

        Сверху: на всей базе (16 421 хвост) девятизначный не совпал ни у одной
        пары разных водителей, а восьмизначный совпал дважды.
        Снизу: девять — ровно длина национального номера Узбекистана, Киргизии
        и Таджикистана, и водитель диктует его без кода страны.
        """
        self.assertEqual(chat2desk.PHONE_TAIL_DIGITS, 9)
        self.assertEqual(len(chat2desk.phone_tail('998901234567')), 9)
        for raw in ('', None, 'мусор', '12345678', '1' * 16,
                    '[wa_dialog] KZ.1026155950418911'):
            with self.subTest(raw=raw):
                self.assertIsNone(chat2desk.phone_tail(raw))

    def test_nine_digit_national_number_is_searchable_but_not_a_full_number(self):
        """Узбекский «90 123 45 67» — это ХВОСТ, а не номер целиком.

        Номером его считать нельзя: у вендора номер лежит с кодом страны, и
        точный поиск по девяти цифрам не нашёл бы ничего. Зато по хвосту
        водитель находится — ровно ради этого разделены два пути.
        """
        for raw in ('90 123 45 67', '901234567', '555510048', '920105581'):
            with self.subTest(raw=raw):
                self.assertIsNone(chat2desk.normalize_phone(raw))
                self.assertIsNotNone(chat2desk.phone_tail(raw))
        # Ввод, из которого не собирается даже хвост, раздел по-прежнему
        # отвергает до всякой базы.
        self.assertIsNone(chat2desk.phone_tail('1234'))

    def test_garbage_is_rejected_rather_than_guessed(self):
        """Границы — десять цифр (номер без кода страны) и пятнадцать (потолок
        E.164). Этого хватает, чтобы отсечь идентификаторы WhatsApp, которые
        вендор кладёт в то же поле (244 строки вида «[wa…» длиной 30-32)."""
        for raw in ('', None, 'abc', '123', '123456789', '1' * 16,
                    '[wa_dialog] KZ.1026155950418911'):  # так они и лежат в базе
            with self.subTest(raw=raw):
                self.assertIsNone(chat2desk.normalize_phone(raw))

    def test_foreign_number_keeps_its_own_variants(self):
        """У номера без «7» впереди нет казахстанской формы с «8» — не выдумываем
        её, иначе поиск уйдёт к вендору за несуществующей записью."""
        self.assertEqual(chat2desk.phone_variants('998901234567'),
                         ['998901234567', '+998901234567'])

    def test_variants_cover_how_the_vendor_stores_it(self):
        """В c2d_requests телефон лежит без приведения: 11, 12, 14, 15 знаков."""
        variants = chat2desk.phone_variants('87071234567')
        self.assertIn('77071234567', variants)
        self.assertIn('87071234567', variants)
        self.assertIn('+77071234567', variants)
        self.assertIn('7071234567', variants)

    def test_variants_normalize_their_own_input(self):
        """Функция обязана быть безопасной сама по себе.

        Дай ей «8707…» — и без нормализации внутри она вернёт варианты от
        «8707…», промахнувшись мимо номера, который лежит в базе. Промах при
        этом выглядит как «водителя нет», а не как ошибка формата.
        """
        self.assertEqual(chat2desk.phone_variants('8 707 123 45 67'),
                         chat2desk.phone_variants('77071234567'))
        self.assertEqual(chat2desk.phone_variants('мусор'), [])


class PhoneTailLookupTests(unittest.TestCase):
    """Поиск водителя по ХВОСТУ номера — то, чем находится иностранный номер.

    Точные варианты (`phone_variants`) перебирают то, как номер пишут У НАС:
    с «8», с «+», без кода страны. Достроить турецкий «531 729 83 61» до
    «90 531 729 83 61» они не могут — кодов стран две сотни, — а хвост у номера
    один и тот же в любой записи (владелец 08.09.2026: «могут быть и номера из
    других стран, исправь это»).
    """

    class _Cursor:
        def __init__(self, rows):
            self.rows = rows
            self.params = None

        def execute(self, _sql, params=None):
            self.params = params

        def fetchall(self):
            return self.rows

    def test_one_driver_behind_the_tail_is_returned_with_his_stored_number(self):
        """Телефон отдаётся В ТОЙ записи, в какой лежит у нас: под ней потом
        сохраняется кеш и пишется журнал, а повторный поиск того же водителя
        попадает уже в точный путь."""
        cursor = self._Cursor([(124223666, '905317298361')])
        self.assertEqual(queries.local_client_by_tail(cursor, '317298361'),
                         (124223666, '905317298361'))
        self.assertEqual(cursor.params, {'tail': '317298361'})

    def test_nothing_found_is_not_an_error(self):
        self.assertIsNone(queries.local_client_by_tail(self._Cursor([]), '317298361'))
        self.assertIsNone(queries.local_client_by_tail(self._Cursor([]), None))

    def test_two_drivers_behind_one_tail_stop_the_search(self):
        """Такого на живой базе нет ни разу, но если случится — показать
        переписку соседа нельзя. Раздел обязан спросить номер целиком."""
        cursor = self._Cursor([(1, '905317298361'), (2, '77071234567')])
        self.assertEqual(queries.local_client_by_tail(cursor, '317298361'),
                         ('ambiguous', None))

    def test_messenger_identifiers_are_excluded_from_the_lookup(self):
        """В той же колонке вендор держит идентификаторы мессенджеров на 14-15
        знаков (852 строки). У них тоже есть девять последних цифр, и без
        отсечки поиск выдал бы за водителя чужой диалог."""
        self.assertIn("client_phone ~ '^[+]?[0-9]{10,15}$'",
                      queries._LOCAL_CLIENT_BY_TAIL_SQL)

    def test_the_query_and_the_index_speak_the_same_expression(self):
        """Разойдись выражения хоть пробелом — планировщик индекс не возьмёт и
        уйдёт в полный проход по 86 тыс. строк (0,2 с против единиц мс), причём
        молча: запрос останется правильным."""
        expression = r"right(regexp_replace(client_phone, '\D', '', 'g'), 9)"
        self.assertIn(expression, queries._LOCAL_CLIENT_BY_TAIL_SQL)
        ddl = '\n'.join(schema.DDL)
        self.assertIn('idx_c2d_requests_phone_tail', ddl)
        self.assertIn(expression, ddl)
        # Длина хвоста в SQL — та же, что в питоне: девять.
        self.assertIn(', %d)' % chat2desk.PHONE_TAIL_DIGITS,
                      queries._LOCAL_CLIENT_BY_TAIL_SQL)

    def test_the_index_is_idempotent_like_the_rest_of_the_schema(self):
        """Схема раздела разворачивается на КАЖДОМ старте."""
        ddl = '\n'.join(schema.DDL)
        self.assertIn('CREATE INDEX IF NOT EXISTS idx_c2d_requests_phone_tail', ddl)


class ForeignPhoneSearchTests(unittest.TestCase):
    """Ручка поиска на иностранном номере — целиком, до ответа."""

    @classmethod
    def setUpClass(cls):
        cls.routes = (ROOT / 'driver_chats' / 'routes.py').read_text(encoding='utf-8')
        cls.search = cls.routes.split('def driver_chats_search')[1].split(
            'def driver_chats_open')[0]

    def test_the_tail_is_tried_only_after_the_exact_records(self):
        """Порядок значим: точное совпадение дешевле и однозначнее, а хвост —
        запасной ход. Поменяй местами — и раздел начал бы находить водителя по
        девяти цифрам там, где есть точная запись."""
        self.assertLess(self.search.index('local_client_id'),
                        self.search.index('local_client_by_tail'))
        self.assertLess(self.search.index('cached_client_id'),
                        self.search.index('local_client_by_tail'))

    def test_the_tail_is_tried_before_the_vendor(self):
        """Свой запрос по индексу — единицы миллисекунд, вызов вендора — сотни
        мс И квота, общая с ночным синком метрик отдела."""
        self.assertLess(self.search.index('local_client_by_tail'),
                        self.search.index('chat2desk.find_client'))

    def test_the_number_switches_to_the_form_stored_in_our_base(self):
        """Иначе кеш и журнал легли бы под «531 729 83 61», а следующий поиск
        того же водителя снова пошёл бы через хвост."""
        self.assertIn('phone = chat2desk.normalize_phone(stored_phone) or phone',
                      self.search)

    def test_ambiguous_tail_answers_with_its_own_code(self):
        self.assertIn('PHONE_AMBIGUOUS', self.search)
        self.assertIn('Введите номер целиком, с кодом страны', self.search)

    def test_ambiguous_answer_does_not_open_a_fourth_cursor(self):
        """Строка журнала пишется тем же курсором, которым нашли неоднозначность:
        каждый курсор — место в общем пуле, где четыре держит цикл бота."""
        self.assertEqual(self.search.count('db._get_cursor()'), 3)

    def test_the_refusal_shows_a_foreign_example_too(self):
        """Отказ «непохоже на номер» — единственное место, где раздел объясняет
        формат. Три казахстанских примера в нём читались как «только КЗ»."""
        refusal = self.search.split('BAD_PHONE')[0]
        self.assertIn('79161234567', refusal)
        self.assertIn('+998 90 123 45 67', refusal)

    def test_the_screen_says_foreign_numbers_are_welcome(self):
        """Человек читает подсказку на экране, а не код: пока в ней стояли одни
        казахстанские примеры, раздел выглядел «только для КЗ»."""
        view = VIEW_JSX.read_text(encoding='utf-8')
        steps = view.split('const SEARCH_STEPS = [')[1].split('];')[0]
        self.assertIn('иностранный', steps)
        self.assertIn('998', steps)


class WindowTests(unittest.TestCase):
    def test_window_is_yesterday_and_today(self):
        """«История чатов за последние 2 дня» — вчера и сегодня, а не двое
        суток назад: водитель звонит про то, что писал вчера или утром."""
        today = date(2026, 9, 3)
        self.assertEqual(chat2desk.window_bounds(today=today),
                         (date(2026, 9, 2), date(2026, 9, 3)))

    def test_window_never_collapses(self):
        today = date(2026, 9, 3)
        start, end = chat2desk.window_bounds(days=1, today=today)
        self.assertEqual((start, end), (today, today))


def msg(mid, kind, created, text='', request_id=1, dialog_id=7, **extra):
    item = {'id': mid, 'type': kind, 'created': created, 'text': text,
            'requestId': request_id, 'dialogId': dialog_id,
            'photo': None, 'video': None, 'audio': None, 'pdf': None,
            'attachments': []}
    item.update(extra)
    return item


class ChatGroupingTests(unittest.TestCase):
    def test_two_days_of_one_park_are_one_chat(self):
        """Главное требование владельца 04.09.2026: «один чат один таксопарк, но
        с историей в два последних дня».

        Раньше эти же три сообщения давали ДВА чата — по числу обращений, — и
        один разговор с одним парком выглядел как два разных.
        """
        chats = chat2desk.group_chats([
            msg(1, 'from_client', '2026-09-02T10:00:00', 'первое',
                request_id=100, channelId=2137),
            msg(2, 'to_client', '2026-09-02T10:05:00', 'ответ',
                request_id=100, channelId=2137),
            msg(3, 'from_client', '2026-09-03T09:00:00', 'сегодня',
                request_id=200, channelId=2137),
        ])
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]['channel_id'], 2137)
        self.assertEqual(chats[0]['messages_count'], 3)
        self.assertEqual(chats[0]['incoming_count'], 2)
        self.assertEqual(chats[0]['outgoing_count'], 1)
        self.assertEqual(chats[0]['request_ids'], [200, 100],
                         'все обращения окна остаются справочно, свежие сверху')

    def test_different_parks_stay_different_chats(self):
        """«Один чат один таксопарк» — значит два парка это два чата, и свежий
        сверху. Так пишут 2,3 % водителей окна."""
        chats = chat2desk.group_chats([
            msg(1, 'from_client', '2026-09-02T10:00:00', 'в первый парк',
                request_id=100, channelId=2137),
            msg(2, 'from_client', '2026-09-03T09:00:00', 'во второй парк',
                request_id=200, channelId=3624),
        ])
        self.assertEqual([c['channel_id'] for c in chats], [3624, 2137])

    def test_reference_request_is_the_last_live_one(self):
        """Самое свежее сообщение окна часто и есть автоопрос после закрытия
        чата. Наследовать ЕГО номер значило бы подписать живой разговор
        служебной заявкой — и в журнале, и в выгрузке."""
        chats = chat2desk.group_chats([
            msg(1, 'from_client', '2026-09-03T10:00:00', 'помогите',
                request_id=100, channelId=2137),
            msg(2, None, '2026-09-03T12:00:00', 'Оцените работу оператора',
                request_id=999, channelId=2137),
        ])
        self.assertEqual(chats[0]['request_id'], 100)
        self.assertIn(999, chats[0]['request_ids'])

    def test_messages_without_request_fall_back_to_dialog(self):
        """Автоответы и системные строки приходят до открытия обращения — без
        фолбэка они бы просто пропали из списка."""
        chats = chat2desk.group_chats([
            msg(1, 'autoreply', '2026-09-03T08:00:00', 'здравствуйте',
                request_id=None, dialog_id=55),
        ])
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]['dialog_id'], 55)

    def test_service_chat_is_marked_not_dropped(self):
        """Автоопрос «оцените работу оператора» — половина строк в c2d_requests.

        Прячем за тумблером, а не выбрасываем: иногда спрашивают именно про
        оценку, которую водитель поставил.
        """
        chats = chat2desk.group_chats([
            msg(1, 'autoreply', '2026-09-02T22:05:00', 'Оцените работу', request_id=300),
            msg(2, 'system', '2026-09-02T22:06:00', 'Chat closed', request_id=300),
        ])
        self.assertTrue(chats[0]['is_service'])

    def test_channel_travels_with_the_chat(self):
        """Таксопарк берётся из САМОГО сообщения: в ночном срезе заявок канал
        есть только за вчера, а оператору чаще нужен сегодняшний чат — и парк у
        него не показывался вовсе."""
        chats = chat2desk.group_chats([
            msg(1, 'from_client', '2026-09-03T10:00:00', 'привет',
                request_id=900, channelId=2137),
        ])
        self.assertEqual(chats[0]['channel_id'], 2137)

    def test_channel_is_taken_from_the_first_message_that_has_one(self):
        """Системная строка канал иногда не несёт — парк не должен теряться."""
        chats = chat2desk.group_chats([
            msg(1, 'system', '2026-09-03T09:00:00', 'открыт', request_id=901, channelId=None),
            msg(2, 'from_client', '2026-09-03T09:01:00', 'привет', request_id=901, channelId=3624),
        ])
        self.assertEqual(chats[0]['channel_id'], 3624)

    def test_live_chat_is_not_service(self):
        chats = chat2desk.group_chats([
            msg(1, 'from_client', '2026-09-03T10:00:00', 'помогите', request_id=400),
        ])
        self.assertFalse(chats[0]['is_service'])

    def test_authors_are_collected_per_chat(self):
        """За двое суток водителю могли отвечать разные чат-менеджеры —
        подписывать все ответы одним именем нельзя."""
        chats = chat2desk.group_chats([
            msg(1, 'to_client', '2026-09-03T10:00:00', 'раз', request_id=500, author='Алишер'),
            msg(2, 'to_client', '2026-09-03T11:00:00', 'два', request_id=500, author='Бехруз'),
        ])
        self.assertEqual(chats[0]['authors'], ['Алишер', 'Бехруз'])

    def test_preview_takes_the_last_meaningful_text(self):
        chats = chat2desk.group_chats([
            msg(1, 'from_client', '2026-09-03T10:00:00', 'первое', request_id=600),
            msg(2, 'from_client', '2026-09-03T11:00:00', 'последнее', request_id=600),
            msg(3, 'system', '2026-09-03T11:01:00', '', request_id=600),
        ])
        self.assertEqual(chats[0]['preview'], 'последнее')

    def test_preview_skips_the_technical_closing_line(self):
        """Иначе в списке у каждого закрытого чата стоит «Chat closed…», и
        оператор не видит, о чём был разговор (замечено на живом стенде)."""
        chats = chat2desk.group_chats([
            msg(1, 'from_client', '2026-09-03T10:00:00', 'не приходят заказы', request_id=700),
            msg(2, 'system', '2026-09-03T12:00:00',
                'Chat closed. Reason — chat inactivity timeout.', request_id=700),
        ])
        self.assertEqual(chats[0]['preview'], 'не приходят заказы')

    def test_service_only_chat_still_shows_something(self):
        """Живых реплик нет вовсе — лучше служебная строка, чем пустая карточка."""
        chats = chat2desk.group_chats([
            msg(1, 'autoreply', '2026-09-03T10:00:00', 'Оцените работу', request_id=800),
        ])
        self.assertEqual(chats[0]['preview'], 'Оцените работу')


class NormalizeMessageTests(unittest.TestCase):
    def test_author_is_added_only_where_it_makes_sense(self):
        """У реплики клиента автор — сам водитель, подписывать её нечем."""
        names = {42: 'Алишер Зиноллаев'}
        out = chat2desk.normalize_message(
            {'id': 1, 'type': 'to_client', 'operator_id': 42, 'text': 'ответ'}, names)
        self.assertEqual(out['author'], 'Алишер Зиноллаев')
        incoming = chat2desk.normalize_message(
            {'id': 2, 'type': 'from_client', 'text': 'вопрос'}, names)
        self.assertNotIn('author', incoming)

    def test_internal_comment_keeps_its_type(self):
        """Заметка оператора — НЕ реплика клиента. Ветка «всё, что не to_client
        → клиент» однажды уже выдавала заметки за сообщения водителя."""
        out = chat2desk.normalize_message({'id': 3, 'type': 'comment', 'text': 'обед'}, {})
        self.assertEqual(out['type'], 'comment')

    def test_relative_media_path_becomes_a_full_url(self):
        out = chat2desk.normalize_message(
            {'id': 4, 'type': 'from_client', 'photo': 'companies/company_1/a.jpg'}, {})
        self.assertTrue(out['photo'].startswith('https://'))
        self.assertTrue(out['photo'].endswith('companies/company_1/a.jpg'))

    def test_absolute_media_url_is_left_alone(self):
        out = chat2desk.normalize_message(
            {'id': 5, 'type': 'from_client', 'photo': 'https://example.test/a.jpg'}, {})
        self.assertEqual(out['photo'], 'https://example.test/a.jpg')

    def test_utc_is_converted_to_almaty(self):
        """Вендор отдаёт UTC словом, а не оффсетом."""
        out = chat2desk.normalize_message(
            {'id': 6, 'type': 'from_client', 'created': '2026-09-03T10:00:00 UTC'}, {})
        self.assertEqual(out['created'], '2026-09-03T15:00:00')


class HandoffTextTests(unittest.TestCase):
    """Кнопка «Передан». Главное требование постановки — зафиксировать, КТО её
    нажал."""

    def test_operator_name_goes_into_the_text(self):
        """Учёток Chat2Desk у линейных операторов нет: без operator_id вендор
        припишет заметку чат-менеджеру, который вёл диалог (проверено живьём
        03.09.2026). Значит имя обязано быть в самом тексте."""
        text = chat2desk.build_handoff_text('Хайрихан Шерзад')
        self.assertIn('Хайрихан Шерзад', text)

    def test_note_is_appended(self):
        text = chat2desk.build_handoff_text('Оператор', 'уточнить статус заказа')
        self.assertIn('Оператор', text)
        self.assertIn('уточнить статус заказа', text)

    def test_form_is_short(self):
        """Требование владельца 03.09.2026: «комментарий от оператора такого-то и
        его сообщение». Длинная преамбула про скриншот занимала в ленте
        Chat2Desk две строки и повторяла очевидное."""
        text = chat2desk.build_handoff_text('Иванов И.', 'нужен статус заказа')
        self.assertEqual(text, 'Комментарий от оператора Иванов И.: нужен статус заказа')
        self.assertNotIn('Скриншот переписки передан', text)

    def test_nameless_author_still_says_something(self):
        self.assertIn('оператор', chat2desk.build_handoff_text('').lower())

    def test_text_is_capped(self):
        """Отозвать или отредактировать заметку через API нельзя — метода
        удаления у вендора нет. Ограничение стоит на входе."""
        text = chat2desk.build_handoff_text('Оператор', 'я' * 5000)
        self.assertLessEqual(len(text), chat2desk.MAX_COMMENT_LENGTH)


class ServiceMessageTests(unittest.TestCase):
    """Сообщения без типа. Склейка делает этот дефект видимым."""

    def test_empty_type_becomes_service_not_a_driver_reply(self):
        """Вендор шлёт автоопрос «Оператордың жұмысын қалай бағалайсыз?» с
        type: null (проверено живьём 03.09.2026). У пустого типа в ленте нет
        своей ветки, и ChatThread рисует его белым пузырём СЛЕВА — то есть
        выдаёт вопрос парка за слова водителя. Пока чаты резались по обращению,
        опрос жил отдельной карточкой; после склейки он внутри живой ленты, и
        оператор унёс бы его на скриншоте чат-менеджеру.
        """
        for raw in (None, '', '   '):
            with self.subTest(raw=raw):
                out = chat2desk.normalize_message({'id': 1, 'type': raw, 'text': 'Оцените'}, {})
                self.assertEqual(out['type'], 'autoreply')

    def test_known_types_are_untouched(self):
        for kind in ('from_client', 'to_client', 'system', 'comment', 'autoreply'):
            with self.subTest(kind=kind):
                out = chat2desk.normalize_message({'id': 1, 'type': kind}, {})
                self.assertEqual(out['type'], kind)

    def test_survey_does_not_count_as_a_live_reply(self):
        """Чат из одного автоопроса остаётся служебным — иначе он выглядел бы
        как живой разговор с парком."""
        chats = chat2desk.group_chats([
            msg(1, None, '2026-09-03T12:00:00', 'Оцените работу', request_id=1, channelId=2137),
        ])
        self.assertTrue(chats[0]['is_service'])


class ClientNameTests(unittest.TestCase):
    def test_unknown_is_not_a_name(self):
        """Вендор отдаёт «unknown» буквальной строкой, когда имени нет, и в
        шапке чата это выглядело как имя водителя (видно на живом прогоне
        04.09.2026). Пустое имя честнее: раздел подставит телефон."""
        for raw in ('unknown', 'UNKNOWN', ' Unknown ', 'no name', '', None, '   '):
            with self.subTest(raw=raw):
                self.assertIsNone(chat2desk.clean_client_name(raw))

    def test_real_name_survives(self):
        self.assertEqual(chat2desk.clean_client_name('  Асхат  '), 'Асхат')

    def test_header_falls_back_to_the_phone(self):
        source = VIEW_JSX.read_text(encoding='utf-8')
        self.assertIn('{driverName || formatPhone(phone)}', source)


class ChatKeyTwinTests(unittest.TestCase):
    """Ключ чата живёт на бэке и во фронте. Разъедутся — «передан» встанет на
    чужом чате, а журнал получит повторные записи."""

    def test_backend_key_prefers_channel(self):
        self.assertEqual(chat2desk.chat_key(2137, 999), 'c2137')
        self.assertEqual(chat2desk.chat_key(None, 999), 'd999')
        self.assertEqual(chat2desk.chat_key(None, None), 'x0')

    def test_frontend_twin_has_the_same_order(self):
        source = VIEW_JSX.read_text(encoding='utf-8')
        body = source.split('function chatKey(chat)')[1].split('\n}')[0]
        self.assertIn('channel_id', body)
        self.assertLess(body.index('channel_id'), body.index('dialog_id'),
                        'канал проверяется раньше диалога — как на бэкенде')
        self.assertIn("'x0'", body)


class JournalAddressTests(unittest.TestCase):
    """После склейки адрес чата — парк, а не обращение."""

    def test_schema_has_channel_id(self):
        joined = '\n'.join(schema.DDL)
        self.assertIn('channel_id', joined)

    def test_channel_column_is_added_by_alter_not_create(self):
        """Таблица уже живёт на проде: CREATE TABLE IF NOT EXISTS её не тронет,
        и колонка молча не появилась бы."""
        alters = [s for s in schema.DDL if s.strip().upper().startswith('ALTER TABLE')]
        self.assertTrue(any('channel_id' in s for s in alters))

    def test_open_event_carries_no_request_id(self):
        """У просмотра одного обращения больше не существует — лучше пустая
        колонка, чем произвольный номер."""
        source = (ROOT / 'driver_chats' / 'routes.py').read_text(encoding='utf-8')
        body = source.split('def driver_chats_open')[1].split('def ')[0]
        self.assertIn('channel_id', body)
        self.assertNotIn("request_id=_int_or_none(data.get('request_id'))", body)

    def test_handoff_takes_request_id_only_from_the_vendor(self):
        source = (ROOT / 'driver_chats' / 'routes.py').read_text(encoding='utf-8')
        body = source.split('def driver_chats_handoff')[1].split('def ')[0]
        self.assertIn("request_id=sent.get('request_id')", body)
        self.assertNotIn("or _int_or_none(data.get('request_id'))", body)


class ThreadScrollTests(unittest.TestCase):
    """Двухсуточная лента должна открываться на свежем, а не на самом старом."""

    @classmethod
    def setUpClass(cls):
        cls.thread = (ROOT / 'src' / 'components' / 'c2d_eval' / 'ChatThread.jsx'
                      ).read_text(encoding='utf-8')
        cls.view = VIEW_JSX.read_text(encoding='utf-8')

    def test_prop_exists_and_defaults_to_old_behaviour(self):
        """Лента используется в пяти местах — менять их молча нельзя."""
        self.assertIn("initialScroll = 'start'", self.thread)

    def test_section_opens_at_the_end(self):
        self.assertIn('initialScroll="end"', self.view)


class HandoffFlowTests(unittest.TestCase):
    """Порядок действий кнопки «Передан» — то, что ломается молча."""

    def test_cache_is_dropped_right_after_sending(self):
        """Заметка уже в чате у вендора, а наш снимок ей на пять минут старше.

        Без сброса оператор жмёт «Найти» и НЕ видит собственный комментарий —
        выглядит как «кнопка не сработала», и он жмёт её второй раз. Отозвать
        лишнюю заметку через API нельзя (метода DELETE у вендора нет), поэтому
        сброс кеша обязателен, а не желателен. Замечено на живом прогоне
        03.09.2026.
        """
        source = (ROOT / 'driver_chats' / 'routes.py').read_text(encoding='utf-8')
        handoff = source.split('def driver_chats_handoff')[1].split('def ')[0]
        self.assertIn('drop_cached_messages', handoff)
        self.assertLess(handoff.index('send_internal_comment'),
                        handoff.index('drop_cached_messages'),
                        'кеш сбрасывается ПОСЛЕ успешной отправки, а не до')


class RefreshTests(unittest.TestCase):
    """Кнопка «Обновить»: она обязана ходить к вендору, а не отдавать тот же кеш.

    Дефект, ради которого кнопка появилась (замечен владельцем 07.09.2026):
    отправив внутренний комментарий, оператор не видел его в ленте, потому что
    единственным способом перечитать переписку был повторный поиск, а тот
    молча возвращал пятиминутный кеш. Если обновление снова начнёт читать кеш,
    кнопка станет украшением — и человек нажмёт «Передан» второй раз, а отозвать
    заметку через API вендора нельзя.
    """

    class _Cursor:
        """Курсор ровно настолько, насколько его использует cached_messages."""

        def __init__(self, row):
            self.row = row

        def execute(self, *args, **kwargs):
            return None

        def fetchone(self):
            return self.row

    @classmethod
    def setUpClass(cls):
        cls.routes = (ROOT / 'driver_chats' / 'routes.py').read_text(encoding='utf-8')
        cls.search = cls.routes.split('def driver_chats_search')[1].split(
            'def driver_chats_open')[0]
        cls.view = VIEW_JSX.read_text(encoding='utf-8')
        cls.load = cls.view.split('const load = useCallback')[1].split(
            'const runSearch = useCallback')[0]

    # ── Сервер ───────────────────────────────────────────────────────────────

    def test_cache_is_read_only_when_the_request_is_not_a_refresh(self):
        """Единственное чтение кеша обязано стоять под флагом обновления."""
        lines = self.search.splitlines()
        reads = [i for i, line in enumerate(lines) if 'cached_messages' in line]
        self.assertEqual(len(reads), 1,
                         'чтение кеша в поиске одно — иначе флаг обойдут мимо')
        guard = next(line for line in reversed(lines[:reads[0]])
                     if line.strip().startswith('if '))
        self.assertIn('not force_fresh', guard,
                      'кеш читается ТОЛЬКО когда это не «Обновить»')

    def test_refresh_flag_is_read_from_the_query(self):
        self.assertIn("request.args.get('refresh')", self.search)

    def test_refresh_still_costs_a_search_from_the_daily_limit(self):
        """Обновление — обращение к вендору, и лимит защищает именно его.

        Потолок стоит не ради денег: исчерпав месячную квоту Chat2Desk, встают
        табло СЗоВ и зарплатные метрики чат-менеджеров. Кнопка, ходящая мимо
        счётчика, обошла бы эту защиту.
        """
        self.assertIn('DAILY_LIMIT_REACHED', self.search)
        self.assertIn("'search'", self.search, 'обновление пишется в журнал доступа')

    def test_response_carries_the_time_of_the_snapshot(self):
        """Подпись «обновлено в HH:MM» берётся с сервера, а не с часов браузера:
        на кеше они расходятся на его возраст."""
        self.assertIn("'fetched_at':", self.search)

    # ── Контракт кеша ────────────────────────────────────────────────────────

    def test_cache_always_answers_with_a_pair(self):
        """Один голый `return None` на любом из выходов — и распаковка на
        вызывающей стороне упала бы или связала сообщения с пустым временем."""
        window_from = date(2026, 9, 6)
        window_to = date(2026, 9, 7)
        fresh = queries.now_almaty()
        cases = {
            'кеша нет': None,
            'без времени': (['m'], None, window_from, window_to),
            'окно уехало': (['m'], fresh, date(2026, 9, 5), window_to),
            'протух': (['m'], fresh - timedelta(seconds=600), window_from, window_to),
        }
        for label, row in cases.items():
            with self.subTest(label):
                got = queries.cached_messages(self._Cursor(row), 1,
                                              window_from, window_to, 300)
                self.assertEqual(got, (None, None))

        got = queries.cached_messages(
            self._Cursor((['m'], fresh, window_from, window_to)), 1,
            window_from, window_to, 300)
        self.assertEqual(got, (['m'], fresh))

    # ── Экран ────────────────────────────────────────────────────────────────

    def test_refresh_asks_the_server_to_skip_the_cache(self):
        self.assertIn("refresh=1", self.load,
                      'без флага обновление вернуло бы тот же самый снимок')

    def test_refresh_keeps_the_handoff_marks(self):
        """«Передан» — отметка о необратимом действии. Стереть её обновлением
        значит предложить человеку сделать это же второй раз."""
        self.assertEqual(self.load.count('setHandedOff'), 1,
                         'отметки сбрасывает только новый поиск')
        self.assertLess(self.load.index('} else {'), self.load.index('setHandedOff'),
                        'сброс отметок стоит в ветке поиска, а не обновления')

    def test_failed_refresh_does_not_wipe_the_open_chat(self):
        """Упавшая сеть не имеет права уносить с экрана уже прочитанную ленту."""
        guards = 0
        for chunk in self.load.split('if (refresh) {')[1:]:
            head = chunk.split('}')[0]
            if 'toast(' not in head:
                continue
            guards += 1
            self.assertIn('return;', head,
                          'ошибка обновления уходит в тост и выходит, '
                          'не доводя до setResult(emptyResult)')
        self.assertEqual(guards, 2,
                         'таких выходов ровно два — отказ сервера и упавшая сеть')

    def test_refresh_is_one_button_inside_the_chat(self):
        """Кнопка обновления ОДНА и стоит в шапке переписки.

        Сначала она жила в строке поиска — над списком парков, — и владелец
        сказал, что оттуда она читается как «перечитать список»: человек в этот
        момент смотрит в саму переписку. Кнопку добавили и туда, а две одинаковые
        кнопки стали читаться как разные действия («эта обновляет список, эта —
        чат»). 07.09.2026 владелец попросил свести их в одну общую: запрос к
        вендору один на всего водителя, и делить его не на что.
        """
        panel = self.view.split('const ChatPanel = ')[1].split('const HandoffModal')[0]
        self.assertIn('onRefresh', panel)
        self.assertIn('RefreshCw', panel)
        search_stage = self.view.split('const SearchStage = ')[1].split(
            '// ── Список чатов и панель')[0]
        self.assertNotIn('onRefresh', search_stage,
                         'второй кнопки в строке поиска быть не должно')

    def test_fresh_message_is_not_left_below_the_fold(self):
        """Обновление обязано ПОКАЗАТЬ новое сообщение, а не дописать его под
        сгибом двухсуточной ленты: снимок пересобирается на новом чате, а лента
        на смене снимка прижимается к низу."""
        snap = self.view.split('const snapshot = useMemo(')[1].split(');')[0]
        self.assertIn('[activeChat]', snap,
                      'снимок пересобирается, когда чат приехал заново')
        thread = (ROOT / 'src' / 'components' / 'c2d_eval' / 'ChatThread.jsx'
                  ).read_text(encoding='utf-8')
        deps = thread.split('}, [snapshot')[1].split(']')[0]
        self.assertIn('initialScroll', deps,
                      'прижатие к низу пересчитывается на новом снимке')

    def test_button_exists_and_is_not_a_timer(self):
        """Автообновление жгло бы дневной лимит в фоне у каждого открытого
        экрана — обновляет человек, кнопкой."""
        self.assertIn("'Обновить'", self.view)
        self.assertNotIn('setInterval', self.view)


class SearchLatencyTests(unittest.TestCase):
    """Скорость поиска и обновления. Замеры на проде 07.09.2026.

    Владелец: «почему обновляется не моментально». Раздел ждал в четырёх местах,
    и ни одно из них не было видно по коду:

    * поиск клиента по телефону шёл обратным проходом по индексу дня через ВСЕ
      86 052 строки c2d_requests — 22 мс;
    * справочник парков собирался Seq Scan-ом по той же таблице ради 14 строк —
      45 мс, на КАЖДОМ запросе;
    * до вендора и после него бралось пять отдельных курсоров, а в общем пуле
      соединений четыре места постоянно держит цикл запросов бота;
    * каждый вызов Chat2Desk открывал новое TLS-соединение — 117 мс против
      58 мс по живому.

    Всё это дёшево вернуть обратно случайной правкой, поэтому сторожим.
    """

    @classmethod
    def setUpClass(cls):
        cls.routes = (ROOT / 'driver_chats' / 'routes.py').read_text(encoding='utf-8')
        cls.search = cls.routes.split('def driver_chats_search')[1].split(
            'def driver_chats_open')[0]
        cls.queries = (ROOT / 'driver_chats' / 'queries.py').read_text(encoding='utf-8')
        cls.vendor = (ROOT / 'driver_chats' / 'chat2desk.py').read_text(encoding='utf-8')

    def test_phone_lookup_has_an_index(self):
        """Телефон — разрез раздела, и без индекса это полный проход таблицы."""
        database = (ROOT / 'database.py').read_text(encoding='utf-8')
        self.assertIn('idx_c2d_requests_client_phone', database)
        self.assertIn('ON c2d_requests(client_phone)', database)
        self.assertIn('idx_dch_cache_phone', '\n'.join(schema.DDL),
                      'по телефону спрашивают и собственную память раздела')

    def test_client_is_looked_up_in_our_own_memory_first(self):
        """Ночной синк c2d_requests не знает того, кто написал впервые сегодня,
        и поиск по нему уходил к вендору перебирать записи номера — до трёх
        вызовов. Свою же пару телефон-клиент мы узнали ещё на первом поиске."""
        self.assertIn('cached_client_id', self.search)
        self.assertLess(self.search.index('cached_client_id'),
                        self.search.index('local_client_id'),
                        'своя память раньше ночного среза')
        self.assertLess(self.search.index('local_client_id'),
                        self.search.index('find_client'),
                        'ночной срез раньше вендора')

    def test_handoff_keeps_the_phone_to_client_memory(self):
        """«Передан» состаривает снимок, но не выбрасывает строку: вместе с ней
        ушла бы и пара телефон-клиент, и следующее же обновление — то самое,
        ради которого кнопку жмут, — снова искало бы клиента у вендора."""
        drop = self.queries.split('def drop_cached_messages')[1].split('\ndef ')[0]
        # Смотрим на КОД, а не на пояснение: слово DELETE стоит и в тексте
        # docstring, объясняющем, почему его здесь нет.
        code = drop.split('"""')[2]
        self.assertNotIn('DELETE', code.upper(), 'строку не удаляем — состариваем')
        self.assertIn('UPDATE dch_message_cache', code)

    def test_parks_dictionary_is_not_rebuilt_on_every_request(self):
        """Агрегат по всей таблице ради 14 строк. Индекса под него нет и быть не
        может — только кеш в процессе."""
        names = self.queries.split('def channel_names')[1].split('\ndef ')[0]
        self.assertIn('_CHANNELS_CACHE', names)
        self.assertIn('_CHANNELS_TTL', names)

    def test_vendor_calls_reuse_one_connection(self):
        """Рукопожатие TLS стоит столько же, сколько сам запрос."""
        self.assertIn('_SESSION = requests.Session()', self.vendor)
        self.assertIn('_SESSION.request(', self.vendor)
        self.assertNotIn('requests.request(', self.vendor,
                         'холодный вызов мимо сессии возвращает потерянные 60 мс')

    def test_database_is_visited_twice_not_five_times(self):
        """Каждый курсор — заявка на место в общем пуле, где четыре из них
        держит цикл запросов бота; ожидание слота человек ждёт наравне с
        запросом. Третий курсор в коде — ветка «номера нет», она к обычному
        пути отношения не имеет."""
        self.assertLessEqual(self.search.count('db._get_cursor()'), 3)
        not_found = self.search.split("'not_found': True")[0]
        self.assertEqual(not_found.count('db._get_cursor()'), 2,
                         'до ответа «номера нет» — общий курсор и курсор журнала')

    def test_no_vendor_call_while_holding_a_connection(self):
        """Держать место в пуле, пока идёт сетевой запрос, — занимать его на
        порядок дольше самой работы с базой."""
        for chunk in self.search.split('with db._get_cursor() as cursor:')[1:]:
            # тело блока — строки с отступом глубже, чем у самого with
            body = []
            for line in chunk.splitlines()[1:]:
                if line.strip() and not line.startswith('            '):
                    break
                body.append(line)
            body = '\n'.join(body)
            for call in ('fetch_window_messages', 'find_client', 'operator_names',
                         'chat2desk.channel_names'):
                self.assertNotIn(call, body,
                                 f'{call} ходит в сеть — не под курсором')


class VendorLagTests(unittest.TestCase):
    """Заметка «Передан» видна СРАЗУ, хотя вендор показывает её через минуту.

    Замер на живом чате 07.09.2026 (журнал раздела, client 64098578):

        10:05:23  handoff, message_id 666212283 — вендор принял заметку
        10:05:25…10:05:56  двадцать нажатий «Обновить», каждое ходило к вендору
                           и получало ленту из 10 сообщений — БЕЗ заметки
        10:06:19  сообщений 11 — заметка появилась

    То есть 56 секунд. При этом created у заметки — 10:05:22, момент отправки:
    у вендора она была всё это время, он просто не отдавал её в /v1/messages.
    Ускорить это нельзя, ждать — нечего: id заметки вендор возвращает сразу,
    текст собираем мы. Показываем свою копию, а доехавшая вытесняет её по id
    (проверено на трёх заметках: message_id из ответа = id в ленте).
    """

    @classmethod
    def setUpClass(cls):
        cls.routes = (ROOT / 'driver_chats' / 'routes.py').read_text(encoding='utf-8')
        cls.view = VIEW_JSX.read_text(encoding='utf-8')

    def test_merge_drops_the_copy_the_vendor_already_returned(self):
        vendor = [{'id': 1, 'created': '2026-09-07T10:00:00'},
                  {'id': 666212283, 'created': '2026-09-07T10:05:22'}]
        mine = [chat2desk.pending_comment_message(
            message_id=666212283, text='Передан оператором Тест',
            created='2026-09-07T10:05:22', channel_id=2137)]
        merged = chat2desk.merge_pending_comments(vendor, mine)
        self.assertEqual([m['id'] for m in merged], [1, 666212283],
                         'своя копия не должна задваивать вендорскую')

    def test_merge_adds_the_note_the_vendor_still_hides(self):
        vendor = [{'id': 1, 'created': '2026-09-07T10:00:00'}]
        mine = [chat2desk.pending_comment_message(
            message_id=666212283, text='Передан оператором Тест',
            created='2026-09-07T10:05:22', channel_id=2137)]
        merged = chat2desk.merge_pending_comments(vendor, mine)
        self.assertEqual([m['id'] for m in merged], [1, 666212283])
        self.assertEqual(merged[-1]['type'], 'comment',
                         'заметка рисуется по центру ленты, как внутренняя')

    def test_merged_note_keeps_the_order_of_the_thread(self):
        """Лента идёт по времени: заметка, отправленная раньше последнего
        сообщения водителя, не имеет права прыгнуть в конец."""
        vendor = [{'id': 1, 'created': '2026-09-07T10:00:00'},
                  {'id': 2, 'created': '2026-09-07T10:09:00'}]
        mine = [chat2desk.pending_comment_message(
            message_id=9, text='x', created='2026-09-07T10:05:00')]
        self.assertEqual([m['id'] for m in
                          chat2desk.merge_pending_comments(vendor, mine)], [1, 9, 2])

    def test_note_carries_no_invented_author(self):
        """У вендорской копии автор свой — учётная запись API. Подставить сюда
        оператора портала значило бы через минуту молча заменить одно имя
        другим; имя передавшего и так стоит в тексте заметки."""
        note = chat2desk.pending_comment_message(
            message_id=1, text='Передан оператором Тест', created='2026-09-07T10:00:00')
        self.assertIsNone(note['author'])
        self.assertEqual(note['type'], 'comment')

    def test_handoff_hands_the_ready_message_back(self):
        """Без этого экран ждал бы вендора целую минуту."""
        handoff = self.routes.split('def driver_chats_handoff')[1].split('\n    # ──')[0]
        self.assertIn('pending_comment_message', handoff)
        self.assertIn("'message':", handoff)

    def test_search_backfills_notes_the_vendor_still_hides(self):
        """Обновление и перезагрузка страницы в ту же минуту тоже обязаны
        показывать заметку — иначе она мигнёт и пропадёт."""
        search = self.routes.split('def driver_chats_search')[1].split(
            'def driver_chats_open')[0]
        self.assertIn('pending_handoff_notes', search)
        self.assertIn('merge_pending_comments', search)
        self.assertLess(search.index('merge_pending_comments'),
                        search.index('group_chats'),
                        'склейка ДО разбора по паркам, иначе заметка мимо чата')
        self.assertEqual(search.count('db._get_cursor()'), 3,
                         'заметки читаются уже открытым курсором, без четвёртого')

    def test_screen_shows_the_note_without_waiting(self):
        self.assertIn('pendingNotes', self.view)
        handoff = self.view.split('const sendHandoff')[1].split('const snapshot')[0]
        self.assertIn('setPendingNotes', handoff)
        self.assertNotIn('Нажмите «Обновить»', self.view,
                         'подсказка про кнопку больше не нужна — заметка уже в ленте')

    def test_note_is_pinned_to_the_driver_not_just_the_park(self):
        """Адрес заметки — парк, а парк у водителей общий. Без привязки к
        клиенту заметка, отправленная одному, показалась бы в чате следующего
        найденного по тому же парку — и уехала бы туда на скриншоте."""
        merge = self.view.split('const chats = useMemo(')[1].split('}, [')[0]
        self.assertIn('item.clientId === result.clientId', merge)
        self.assertIn('noteBelongsTo(item.message, chat)', merge)

    def test_counter_agrees_with_what_the_thread_shows(self):
        """Заметка в ленте есть, а под ней «4 сообщ.» — и человек поверит
        счётчику. Вендор свою копию в счёт включает (в живом замере 10 -> 11),
        значит и наша обязана."""
        merge = self.view.split('const chats = useMemo(')[1].split('}, [')[0]
        self.assertIn('messages_count:', merge)
        self.assertIn('last_at:', merge)


class LabelTwinTests(unittest.TestCase):
    """Словари видов события живут в двух местах: питон не читает js."""

    def test_kind_labels_match_between_python_and_javascript(self):
        source = JOURNAL_META.read_text(encoding='utf-8')
        block = re.search(r'export const KIND_LABELS = \{(.*?)\};', source, re.S)
        self.assertIsNotNone(block, 'KIND_LABELS не найден в journalMeta.js')
        pairs = dict(re.findall(r"(\w+):\s*'([^']*)'", block.group(1)))
        self.assertEqual(pairs, report.KIND_LABELS)

    def test_role_labels_match_between_python_and_javascript(self):
        source = JOURNAL_META.read_text(encoding='utf-8')
        block = re.search(r'export const ROLE_LABELS = \{(.*?)\};', source, re.S)
        self.assertIsNotNone(block)
        pairs = dict(re.findall(r"(\w+):\s*'([^']*)'", block.group(1)))
        self.assertEqual(pairs, report.ROLE_LABELS)

    def test_every_event_kind_has_a_label(self):
        for kind in schema.EVENT_KINDS:
            self.assertIn(kind, report.KIND_LABELS)

    def test_journal_never_claims_to_see_a_screenshot(self):
        """Система видит открытие чата, а не нажатие Cmd+Shift+4.

        Назвать одно другим в журнале, по которому потом разбирают утечку,
        значит соврать в документе. Формулировку правил владелец не менял —
        меняешь её, меняй и это правило осознанно.
        """
        self.assertEqual(report.KIND_LABELS['open'], 'Открыл переписку')
        for label in report.KIND_LABELS.values():
            self.assertNotIn('скрин', label.lower())

    def test_export_file_name_matches_the_frontend_twin(self):
        """Content-Disposition до фронта не доходит — имя собирается с двух
        сторон и обязано совпадать."""
        source = JOURNAL_META.read_text(encoding='utf-8')
        self.assertIn('Журнал чатов водителей.xlsx', source)
        self.assertEqual(report.export_file_name(None, None),
                         'Журнал чатов водителей.xlsx')
        self.assertEqual(report.export_file_name(date(2026, 9, 3), date(2026, 9, 3)),
                         'Журнал чатов водителей 03.09.2026.xlsx')


class ReportTests(unittest.TestCase):
    def test_workbook_builds_with_context_sheet_first(self):
        rows = [{
            'id': 1, 'kind': 'handoff', 'user_id': 5, 'user_name': 'Оператор',
            'user_role': 'operator', 'phone': '77071234567', 'client_id': 1,
            'dialog_id': 2, 'request_id': 3, 'channel_name': 'Техподдержка',
            'comment_text': 'передал', 'c2d_message_id': 9, 'messages_count': 13,
            'ip_address': '10.0.0.1', 'created_at': '2026-09-03T18:05:00',
        }]
        stream, count = report.build_workbook(
            rows, period_from=date(2026, 9, 1), period_to=date(2026, 9, 3),
            generated_by='СВ Тест')
        self.assertEqual(count, 1)
        payload = stream.getvalue()
        self.assertTrue(payload.startswith(b'PK'), 'это должен быть xlsx')
        self.assertGreater(len(payload), 2000)

    def test_empty_journal_still_builds_a_file(self):
        stream, count = report.build_workbook([])
        self.assertEqual(count, 0)
        self.assertTrue(stream.getvalue().startswith(b'PK'))

    def test_illegal_characters_do_not_break_the_book(self):
        """Управляющие символы прилетают из текста чата — openpyxl их не берёт."""
        rows = [{'id': 1, 'kind': 'open', 'user_name': 'Оператор\x07',
                 'comment_text': 'текст\x00чата', 'created_at': '2026-09-03T10:00:00'}]
        stream, count = report.build_workbook(rows)
        self.assertEqual(count, 1)
        self.assertTrue(stream.getvalue().startswith(b'PK'))


class SchemaTests(unittest.TestCase):
    def test_ddl_is_idempotent_by_construction(self):
        for statement in schema.DDL:
            self.assertIn('IF NOT EXISTS', statement)

    def test_journal_keeps_all_three_addresses_of_a_chat(self):
        """«В какой чат он был направлен» — у вендора это три разных ключа."""
        ddl = schema.DDL[0]
        for column in ('client_id', 'dialog_id', 'request_id'):
            self.assertIn(column, ddl)

    def test_journal_keeps_a_snapshot_of_the_person(self):
        """Человек меняет отдел и увольняется; журнал отвечает «кто это сделал
        ТОГДА»."""
        ddl = schema.DDL[0]
        for column in ('user_name', 'user_role', 'department_id'):
            self.assertIn(column, ddl)

    def test_cache_is_not_the_shared_snapshot_table(self):
        """У c2d_chat_snapshots upsert ПЕРЕЗАПИСЫВАЕТ messages, а на них держатся
        цитаты супервайзера в уже выставленных оценках. Просмотр чата оператором
        не имеет права затирать чужие данные."""
        joined = '\n'.join(schema.DDL)
        self.assertIn('dch_message_cache', joined)
        self.assertNotIn('c2d_chat_snapshots', joined)


class FrontendAccessTests(unittest.TestCase):
    """Пункт меню, отрисовка раздела и гард видимости — три РАЗНЫХ места.

    Постоянная ловушка портала: предикат доступа возвращает true, бэкенд отдаёт
    данные, раздел открывается прямым адресом — но пункта в меню нет, и снаружи
    это выглядит как «доступ не выдаётся». Так уже было с «Ботом опозданий».
    """

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JSX.read_text(encoding='utf-8-sig')

    def test_menu_item_is_declared_exactly_once(self):
        self.assertEqual(
            self.source.count("handleSidebarViewNavigation(e, 'driver_chats')"), 1,
            'пункт «Чаты водителей» должен стоять один раз, в общей части меню')

    def test_menu_item_is_gated_by_the_section_predicate(self):
        self.assertIn('{canAccessDriverChatsSection && (', self.source)
        self.assertIn(
            'const canAccessDriverChatsSection = canAccessDriverChatsSectionForUser(user);',
            self.source)

    def test_view_is_rendered_and_wrapped_into_the_qr_gate(self):
        self.assertIn('view === "driver_chats" && canAccessDriverChatsSection', self.source)
        self.assertIn('sectionTitle="Чаты водителей"', self.source)

    def test_visibility_guard_lets_the_section_through(self):
        self.assertIn("if (view === 'driver_chats' && canAccessDriverChatsSection) return;",
                      self.source)

    def test_qr_status_is_requested_before_the_section_is_drawn(self):
        """Иначе замок мигнёт тому, кто доступ уже подтвердил."""
        self.assertIn("|| view === 'driver_chats'", self.source)

    def test_chat_manager_is_hidden_in_the_menu_too(self):
        """Бэкенд его не пустит, но пункт меню, ведущий в 403, — это шум."""
        self.assertIn("DRIVER_CHATS_EXCLUDED_DIRECTION_MODEL = 'chat_manager'", self.source)
        self.assertIn('isDriverChatsChatManager(userLike)', self.source)

    def test_profile_carries_the_direction_model(self):
        """Без этого поля фронт не отличит чат-менеджера от оператора линии."""
        bot = (ROOT / 'bot_schedule2.py').read_text(encoding='utf-8')
        self.assertIn('"direction_model": direction_model,', bot)


class ViewContractTests(unittest.TestCase):
    """Решения интерфейса, которые легко потерять при доработке."""

    @classmethod
    def setUpClass(cls):
        cls.source = VIEW_JSX.read_text(encoding='utf-8')

    def test_thread_is_reused_not_reimplemented(self):
        """Второй ленты переписки в проекте быть не должно."""
        self.assertIn("import ChatThread from '../c2d_eval/ChatThread'", self.source)

    def test_search_is_an_explicit_action(self):
        """Поиск «по мере ввода» жёг бы месячный лимит вендора, общий с ночным
        синком метрик отдела."""
        self.assertNotIn('debounce', self.source.lower())
        self.assertIn("event.key === 'Enter'", self.source)

    def test_handoff_warns_that_it_cannot_be_recalled(self):
        """Метода удаления сообщения у вендора нет — человек обязан знать это
        ДО нажатия, а не после."""
        self.assertIn('Отозвать', self.source)

    def test_thread_is_shown_whole(self):
        """Переписка показывается ЦЕЛИКОМ, без фильтра служебных сообщений.

        Тумблер «скрыть служебные» и подсказка про снимок экрана (⌘⇧4) убраны по
        решению владельца 07.09.2026: полосу под лентой они занимали на каждом
        чате, а нужны были один раз. Служебные строки — меню парка и автоопрос
        «оцените работу оператора» — теперь просто часть ленты, ровно так их
        видит и чат-менеджер у себя.

        Формулировка правила поменялась осознанно: раньше здесь стояло
        «интерфейс обязан сказать, чем делать снимок». Меняешь обратно — меняй
        и это правило, а не тест под код.
        """
        self.assertNotIn('hideService', self.source,
                         'фильтр служебных сообщений в разделе не нужен')
        self.assertNotIn('IosToggle', self.source)
        self.assertNotIn('⌘⇧4', self.source)

    def test_taxi_park_is_shown_on_every_chat(self):
        """По одному номеру приходят чаты разных парков. Без названия оператор
        не понимает, чей это чат, — на это указал владелец 03.09.2026."""
        # Два места показа: строка списка слева и шапка открытого чата.
        self.assertIn("chat.channel_name || 'Парк не определён'", self.source,
                      'парк стоит в шапке чата')
        self.assertEqual(self.source.count("'Парк не определён'"), 2,
                         'парк показывается в списке чатов и в шапке открытого чата')
        # Список чатов слева вернули по просьбе владельца 04.09.2026: строка
        # списка теперь ровно одна на парк, а не на обращение.
        self.assertIn('const ChatList', self.source)


class ThreadNoteRenderingTests(unittest.TestCase):
    """Внутренний комментарий в ленте — ПО ЦЕНТРУ, как в самом Chat2Desk.

    Лента общая с «Журналом оценок» и «Моими оценками», поэтому решение
    сторожится тестом: у правого края заметка читалась как ответ оператора,
    хотя это служебная пометка для коллег (требование владельца 03.09.2026).
    """

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'src' / 'components' / 'c2d_eval' / 'ChatThread.jsx'
                      ).read_text(encoding='utf-8')

    def test_note_is_centered(self):
        self.assertIn("note ? 'justify-center'", self.source)

    def test_note_has_no_bubble_tail(self):
        """Хвост пузыря показывает сторону разговора, а у заметки стороны нет."""
        block = re.search(r"const bubbleClass = (.*?);\n", self.source, re.S)
        self.assertIsNotNone(block)
        note_branch = re.search(r"note\s*\?\s*'([^']*)'", block.group(1))
        self.assertIsNotNone(note_branch)
        self.assertNotIn('rounded-br-md', note_branch.group(1))


class ExportPeriodTwinTests(unittest.TestCase):
    """Потолок периода выгрузки живёт на двух сторонах и обязан совпадать.

    Разъедься они — «Подтвердить» осталось бы живым на периоде, который сервер
    отвергнет: человек ждал бы файл и получил отказ (тот же контракт, что у
    выгрузки «Посылок»).
    """

    @classmethod
    def setUpClass(cls):
        cls.meta = JOURNAL_META.read_text(encoding='utf-8')
        cls.routes = (ROOT / 'driver_chats' / 'routes.py').read_text(encoding='utf-8')

    def test_cap_is_the_same_number_on_both_sides(self):
        found = re.search(r'EXPORT_MAX_DAYS\s*=\s*(\d+)', self.meta)
        self.assertIsNotNone(found, 'EXPORT_MAX_DAYS пропал из journalMeta.js')
        self.assertEqual(int(found.group(1)), report.EXPORT_MAX_DAYS)
        self.assertEqual(report.EXPORT_MAX_DAYS, 30,
                         'владелец просил ровно 30 суток (08.09.2026)')

    def test_days_are_declined_the_same_way_on_both_sides(self):
        """Экран и отказ сервера склоняют число одинаково — иначе в подсказке
        «31 день», а в ошибке «31 дней», и это видит один и тот же человек."""
        js = self.meta.split('export const pluralDays = (count) => {')[1].split('};')[0]
        # Обе стороны знают про 11–14 (там всегда «дней», хотя цифра кончается
        # на 1–4) — самая частая ошибка в таких помощниках.
        self.assertIn('11', js)
        self.assertIn('14', js)
        for count, word in ((1, '1 день'), (2, '2 дня'), (5, '5 дней'),
                            (11, '11 дней'), (14, '14 дней'), (21, '21 день'),
                            (31, '31 день'), (32, '32 дня'), (45, '45 дней')):
            with self.subTest(count=count):
                self.assertEqual(report.plural_days(count), word)

    def test_period_is_counted_inclusive_on_both_sides(self):
        """Обе границы включительно: «с 1 по 1» — одни сутки, а не ноль.
        Забытое «+1» на одной стороне даёт расхождение ровно в сутки."""
        self.assertIn('export const rangeDays', self.meta)
        self.assertIn('86400000) + 1', self.meta, 'фронт перестал считать включительно')
        self.assertIn('(date_to - date_from).days + 1', self.routes,
                      'сервер перестал считать включительно')


@unittest.skipIf(Flask is None, 'flask не установлен')
class JournalExportRouteTests(unittest.TestCase):
    """Период выгрузки на СЕРВЕРЕ: он обязателен и не длиннее месяца.

    Граница именно здесь, а не в пикере: кнопку в интерфейсе можно обойти, ручку
    зовут напрямую. Просьба владельца 08.09.2026 — «корректная выгрузка с
    кастомным пикером, макс выгрузка на 30 дней».
    """

    def build(self, context=None, rows=()):
        context = context or {'user_id': 7, 'name': 'Супервайзер СЗоВ', 'role': 'sv',
                              'department_id': 1, 'department_code': 'szov',
                              'direction_model': None,
                              'headed_department_ids': [], 'headed_department_codes': []}
        captured = {}

        def _journal_all(_cursor, filters, cap=0):
            captured.update(filters)
            captured['cap'] = cap
            return list(rows)

        cursor = MagicMock()
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def _patch(module, name, value):
            original = getattr(module, name)
            setattr(module, name, value)
            self.addCleanup(setattr, module, name, original)

        _patch(queries, 'load_access_context', lambda _c, _uid: dict(context))
        _patch(queries, 'journal_all', _journal_all)

        app = Flask(__name__)
        app.register_blueprint(build_driver_chats_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=lambda _uid: True))
        return app.test_client(), captured

    def test_without_a_period_the_file_is_refused(self):
        """Пустой период раньше означал «весь журнал за год» — книга собиралась
        в памяти инстанса и уезжала человеку целиком."""
        client, captured = self.build()
        response = client.get('/api/driver_chats/journal/export')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'DRIVER_CHATS_PERIOD_REQUIRED')
        self.assertEqual(captured, {}, 'в базу не ходили')

    def test_half_a_period_is_not_enough_either(self):
        client, _ = self.build()
        for query in ('date_from=2026-09-01', 'date_to=2026-09-01'):
            with self.subTest(query=query):
                response = client.get('/api/driver_chats/journal/export?' + query)
                self.assertEqual(response.status_code, 400)

    def test_the_cap_itself_passes(self):
        """Ровно потолок — это ещё можно: границы включительно, и забытое «+1»
        молча выпускало бы период на сутки длиннее объявленного."""
        client, captured = self.build()
        response = client.get(
            '/api/driver_chats/journal/export?date_from=2026-08-10&date_to=2026-09-08')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured['raw_from'], date(2026, 8, 10))
        self.assertEqual(captured['raw_to'], date(2026, 9, 8))
        self.assertEqual((captured['raw_to'] - captured['raw_from']).days + 1,
                         report.EXPORT_MAX_DAYS)

    def test_a_day_over_the_cap_is_refused_with_the_number_spoken(self):
        """Отказ называет ЗАПРОШЕННУЮ длину и склоняет её: «слишком длинно» без
        числа заставляет человека считать самому."""
        client, captured = self.build()
        response = client.get(
            '/api/driver_chats/journal/export?date_from=2026-08-09&date_to=2026-09-08')
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload['code'], 'DRIVER_CHATS_PERIOD_TOO_LONG')
        self.assertIn('31 день', payload['error'])
        self.assertNotIn('31 суток', payload['error'])
        self.assertIn('%d суток' % report.EXPORT_MAX_DAYS, payload['error'])
        self.assertEqual(captured, {})

    def test_the_refusal_declines_the_number_for_any_length(self):
        client, _ = self.build()
        for date_to, expected in (('2026-09-09', '32 дня'),
                                  ('2026-09-22', '45 дней'),
                                  ('2026-10-08', '61 день')):
            with self.subTest(date_to=date_to):
                response = client.get(
                    '/api/driver_chats/journal/export?date_from=2026-08-09&date_to=' + date_to)
                self.assertIn(expected, response.get_json()['error'])

    def test_reversed_period_is_turned_around_not_refused(self):
        """«С 8-го по 1-е» — описка, а не попытка сломать выгрузку."""
        client, captured = self.build()
        response = client.get(
            '/api/driver_chats/journal/export?date_from=2026-09-08&date_to=2026-09-01')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured['raw_from'], date(2026, 9, 1))
        self.assertEqual(captured['raw_to'], date(2026, 9, 8))

    def test_the_upper_bound_covers_the_whole_last_day(self):
        """Верхняя граница — начало СЛЕДУЮЩИХ суток, иначе «по 8 сентября»
        молча теряет всё, что было в этот день после полуночи."""
        client, captured = self.build()
        client.get('/api/driver_chats/journal/export?date_from=2026-09-08&date_to=2026-09-08')
        self.assertEqual(captured['date_from'], datetime(2026, 9, 8, 0, 0))
        self.assertEqual(captured['date_to'], datetime(2026, 9, 9, 0, 0))

    def test_the_rest_of_the_filters_still_travel_into_the_file(self):
        """Пикер задаёт только рамку. Действие, сотрудник и телефон приезжают с
        экрана — иначе файл не совпал бы с тем, что человек видел."""
        client, captured = self.build()
        client.get('/api/driver_chats/journal/export?date_from=2026-09-01&date_to=2026-09-08'
                   '&kinds=handoff&user_id=42&phone=%2B7%20707%20123%2045%2067')
        self.assertEqual(captured['kinds'], ['handoff'])
        self.assertEqual(captured['user_id'], 42)
        # Нормализация вендора приводит казахстанский номер к виду 7XXXXXXXXXX,
        # а «восьмёрку» человеку рисует уже экран (formatPhone).
        self.assertEqual(captured['phone'], '77071234567')

    def test_the_file_is_named_by_the_chosen_period(self):
        client, _ = self.build()
        response = client.get(
            '/api/driver_chats/journal/export?date_from=2026-09-01&date_to=2026-09-08')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            report.export_file_name(date(2026, 9, 1), date(2026, 9, 8)),
            'Журнал чатов водителей 01.09.2026 — 08.09.2026.xlsx')

    def test_an_operator_still_gets_nothing(self):
        """Гейт журнала стоит раньше разбора периода: оператору незачем узнавать
        про потолок того, чего ему не покажут."""
        client, captured = self.build(context={
            'user_id': 8, 'name': 'Оператор', 'role': 'operator', 'department_id': 1,
            'department_code': 'szov', 'direction_model': None,
            'headed_department_ids': [], 'headed_department_codes': []})
        response = client.get(
            '/api/driver_chats/journal/export?date_from=2026-09-01&date_to=2026-09-08')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()['code'], 'DRIVER_CHATS_JOURNAL_CLOSED')
        self.assertEqual(captured, {})


@unittest.skipIf(Flask is None, 'flask не установлен')
class SearchRouteForeignPhoneTests(unittest.TestCase):
    """Ручка поиска целиком: что происходит с иностранным номером.

    Владелец 08.09.2026: «человек может искать только по кз номерам, могут быть
    и номера из других стран, исправь это». Здесь проверяется весь путь, а не
    отдельные функции: где раздел спрашивает свою базу, где — вендора (тот стоит
    квоты, общей с ночным синком метрик отдела), и что он отвечает человеку.
    """

    def build(self, *, exact=None, by_tail=None):
        context = {'user_id': 7, 'name': 'Оператор СЗоВ', 'role': 'sv',
                   'department_id': 1, 'department_code': 'szov',
                   'direction_model': None,
                   'headed_department_ids': [], 'headed_department_codes': []}
        seen = {'tail': [], 'variants': [], 'vendor': [], 'logged': []}

        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)          # поисков за сегодня — ноль
        db = MagicMock()

        @contextmanager
        def _get_cursor():
            yield cursor

        db._get_cursor = _get_cursor

        def _patch(module, name, value):
            original = getattr(module, name)
            setattr(module, name, value)
            self.addCleanup(setattr, module, name, original)

        def _local(_c, variants):
            seen['variants'].append(list(variants))
            return exact

        def _tail(_c, tail):
            seen['tail'].append(tail)
            return by_tail

        def _find_client(phone):
            seen['vendor'].append(phone)
            return None

        def _log(_c, _ctx, kind, **kwargs):
            seen['logged'].append((kind, kwargs.get('phone')))

        _patch(queries, 'load_access_context', lambda _c, _uid: dict(context))
        _patch(queries, 'cached_client_id', lambda _c, _phone: None)
        _patch(queries, 'local_client_id', _local)
        _patch(queries, 'local_client_by_tail', _tail)
        _patch(queries, 'pending_handoff_notes', lambda *_a, **_k: [])
        _patch(queries, 'cached_messages', lambda *_a, **_k: (None, None))
        _patch(queries, 'store_messages', lambda *_a, **_k: None)
        _patch(queries, 'request_meta', lambda *_a, **_k: {})
        _patch(queries, 'channel_names', lambda *_a, **_k: {})
        _patch(queries, 'log_event', _log)
        _patch(chat2desk, 'find_client', _find_client)
        _patch(chat2desk, 'fetch_window_messages', lambda *_a, **_k: ([], 0))
        _patch(chat2desk, 'operator_names', lambda *_a, **_k: {})
        _patch(chat2desk, 'channel_names', lambda *_a, **_k: {})

        app = Flask(__name__)
        app.register_blueprint(build_driver_chats_blueprint(
            db=db,
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (context['user_id'], None, None),
            sensitive_access_granted=lambda _uid: True))
        return app.test_client(), seen

    def test_foreign_number_without_the_country_code_is_found_by_its_tail(self):
        """Турецкий номер, продиктованный как «531 729 83 61». Точного
        совпадения нет и быть не может — в базе он лежит с кодом «90»."""
        client, seen = self.build(by_tail=(124223666, '905317298361'))
        response = client.get('/api/driver_chats/search?phone=531+729+83+61')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(seen['tail'], ['317298361'])
        self.assertEqual(payload['client_id'], 124223666)
        # Дальше живём под ТОЙ записью номера, в какой он лежит у нас: под ней
        # ляжет кеш и строка журнала, а следующий поиск попадёт в точный путь.
        self.assertEqual(payload['phone'], '905317298361')
        self.assertEqual(seen['logged'], [('search', '905317298361')])
        self.assertEqual(seen['vendor'], [], 'вендора не спрашивали — нашли у себя')

    def test_nine_digit_national_number_reaches_the_tail_instead_of_a_refusal(self):
        """Узбекский, киргизский и таджикский номера девятизначные. Раньше на
        них раздел отвечал «непохоже на номер телефона» — то есть отказывал
        целой стране."""
        client, seen = self.build(by_tail=(124421666, '996555510048'))
        response = client.get('/api/driver_chats/search?phone=555510048')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen['tail'], ['555510048'])
        self.assertEqual(response.get_json()['phone'], '996555510048')
        self.assertEqual(seen['variants'], [],
                         'точное совпадение по девяти цифрам не спрашивают')

    def test_nine_digits_that_found_nobody_are_not_asked_of_the_vendor(self):
        """Фильтр `phone` у вендора ТОЧНЫЙ: девять цифр без кода страны он не
        найдёт, а вызов стоит квоты, общей с ночным синком метрик отдела."""
        client, seen = self.build(by_tail=None)
        response = client.get('/api/driver_chats/search?phone=555510048')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['not_found'])
        self.assertEqual(seen['vendor'], [])

    def test_a_full_number_that_found_nobody_still_goes_to_the_vendor(self):
        """Номер целиком вендор поискать может — там живут те, кто написал
        впервые сегодня и в ночной срез ещё не попал."""
        client, seen = self.build(by_tail=None)
        response = client.get('/api/driver_chats/search?phone=998901234567')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen['vendor'], ['998901234567'])

    def test_the_tail_is_skipped_when_the_exact_record_answered(self):
        """Хвост — запасной ход. Точное совпадение и дешевле, и однозначнее."""
        client, seen = self.build(exact=555001)
        response = client.get('/api/driver_chats/search?phone=87071234567')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['client_id'], 555001)
        self.assertEqual(seen['tail'], [], 'до хвоста дело не дошло')

    def test_two_drivers_behind_one_tail_stop_with_a_clear_answer(self):
        """Показать переписку соседа нельзя, ответить «не найдено» — соврать."""
        client, seen = self.build(by_tail=('ambiguous', None))
        response = client.get('/api/driver_chats/search?phone=531+729+83+61')
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload['code'], 'PHONE_AMBIGUOUS')
        self.assertIn('с кодом страны', payload['error'])
        self.assertEqual(seen['vendor'], [])
        # В журнал ложится то, что человек ввёл (после нормализации), а не
        # хвост: по нему потом и разбирают, кого искали.
        self.assertEqual(seen['logged'], [('search', '5317298361')],
                         'искал — значит в журнале')

    def test_real_junk_is_still_refused_before_any_database(self):
        client, seen = self.build()
        for raw in ('1234', 'abc', ''):
            with self.subTest(raw=raw):
                response = client.get('/api/driver_chats/search?phone=' + raw)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()['code'], 'BAD_PHONE')
        self.assertEqual(seen['tail'], [])
        self.assertEqual(seen['vendor'], [])


class SearchScreenTests(unittest.TestCase):
    """Экран поиска, переделанный 08.09.2026 по просьбе владельца:

    «поиск поставить по середине сверху, объяснение как что работает, при
    нажатии небольшая анимация — строка поиска увеличивается; номер и
    поиск/enter — находится чат, поиск плавно уходит наверх и появляется чат,
    справа чаты по таксопаркам».
    """

    @classmethod
    def setUpClass(cls):
        cls.source = VIEW_JSX.read_text(encoding='utf-8')

    def test_search_is_one_node_in_both_states(self):
        """«Уход наверх» сделан сворачиванием на ТОМ ЖЕ поле, а не подменой
        блока: подмена размонтировала бы поле вместе с фокусом и кареткой, и
        следующий номер человек начинал бы с щелчка мышью."""
        self.assertEqual(self.source.count('<SearchStage'), 1,
                         'поисковая строка рисуется один раз, на оба состояния')
        stage = self.source.split('const SearchStage = ')[1].split(
            '// ── Список чатов и панель')[0]
        self.assertIn('compact', stage, 'у строки два состояния, а не два компонента')

    def test_the_explanation_stands_above_the_field(self):
        """Три шага — это и есть просьба «сверху объяснение, как что работает».
        Формулировка честная: снимок делает человек средствами системы."""
        self.assertIn('SEARCH_STEPS', self.source)
        steps = self.source.split('const SEARCH_STEPS = [')[1].split('];')[0]
        self.assertEqual(steps.count('title:'), 3)
        self.assertIn('Enter', steps, 'шаг про поиск называет клавишу')
        self.assertIn('Передан', steps, 'шаг про передачу называет кнопку')
        self.assertNotIn('скриншот', steps.lower(),
                         'снимок экрана делает ОС, разделу он не виден')

    def test_the_field_grows_on_focus(self):
        """Та самая «небольшая анимация»: строка растёт, когда в неё встают."""
        stage = self.source.split('const SearchStage = ')[1].split(
            '// ── Список чатов и панель')[0]
        self.assertIn('setFocused(true)', stage)
        self.assertIn('setFocused(false)', stage)
        self.assertIn('focused ?', stage)

    def test_motion_is_muted_for_those_who_asked(self):
        """Системная настройка «меньше движения» — не пожелание: раздел
        открывают по многу раз за смену."""
        stage = self.source.split('const SearchStage = ')[1].split(
            '// ── Список чатов и панель')[0]
        self.assertIn('motion-reduce:transition-none', stage)

    def test_parks_are_on_the_left_of_the_thread(self):
        """Владелец 08.09.2026 сначала попросил список парков справа, посмотрел
        и в тот же день вернул налево. Порядок теперь один на все ширины —
        список первый, — поэтому классов `order` в разметке быть не должно: они
        означали бы, что где-то он всё-таки второй."""
        grid = self.source.split('{hasChats && (')[1].split('</div>\n                    )}')[0]
        self.assertLess(grid.index('<ChatList'), grid.index('<ChatPanel'),
                        'список парков идёт первым — парк выбирают раньше, чем читают')
        self.assertIn('lg:grid-cols-[308px_minmax(0,1fr)]', grid,
                      'узкая колонка списка стоит первой и в сетке')
        self.assertNotIn('order-', grid)

    def test_the_explanation_hides_once_a_chat_is_found(self):
        """Найденный чат — главное на экране, объяснение своё дело сделало."""
        self.assertIn('const hasChats = chats.length > 0;', self.source)
        stage = self.source.split('const SearchStage = ')[1].split(
            '// ── Список чатов и панель')[0]
        self.assertIn('compact ? \'max-h-0', stage)


class JournalScreenTests(unittest.TestCase):
    """Экран журнала, переделанный 08.09.2026: «сделать более понятным и
    удобным и корректная выгрузка с кастомным пикером»."""

    @classmethod
    def setUpClass(cls):
        cls.source = VIEW_JSX.read_text(encoding='utf-8')
        cls.journal = cls.source.split('// ── Журнал ─')[1]

    def test_no_system_pickers_and_selects_are_left(self):
        """Системные `<select>` и `<input type="date">` рисует ОС: внутри
        интерфейса в стиле macOS это деталь из другой программы. Эталон —
        выгрузка табло СЗоВ (src/components/ui/DateRangePicker.jsx).

        Смотрим на КОД без комментариев: сами эти слова в пояснении рядом с
        решением как раз уместны, и запрещать их — значит запрещать объяснять.
        """
        code = re.sub(r'/\*.*?\*/', '', self.source, flags=re.S)
        code = re.sub(r'^\s*//.*$', '', code, flags=re.M)
        self.assertNotIn('<select', code)
        self.assertNotIn('type="date"', code)
        self.assertIn('IosDateRangePicker', self.journal)
        self.assertIn('CustomSelect', self.journal)
        self.assertIn('variant="ios"', self.journal)

    def test_export_opens_the_picker_instead_of_downloading(self):
        """Нажатие «Выгрузить» раскрывает период, а не качает файл: период у
        файла обязателен."""
        block = self.journal.split('ref={exportRef}')[1].split('</div>')[0]
        self.assertIn('setExportOpen', block)
        self.assertNotIn('download(', block.split('exportOpen && (')[0],
                         'кнопка сама файл не собирает')
        self.assertIn('IosDateRangeCalendar', self.journal)
        self.assertIn('Подтвердить', self.journal)

    def test_the_picker_closes_by_click_outside_and_escape(self):
        """Требование эталонного пикера. Слушаем mousedown, а не click: иначе
        прокрутка колесом внутри панели считается внешней и гасит её."""
        self.assertIn("document.addEventListener('mousedown'", self.journal)
        self.assertIn("event.key === 'Escape'", self.journal)

    def test_confirm_is_dead_past_the_cap(self):
        """Потолок держит сервер, но узнать о нём человек должен ДО ожидания."""
        self.assertIn('EXPORT_MAX_DAYS', self.journal)
        self.assertIn('exportTooLong', self.journal)
        self.assertIn('disabled={!exportDays || exportTooLong}', self.journal)

    def test_export_has_its_own_period_but_the_screen_filters(self):
        """Рамку задаёт пикер, остальной отбор — экран. Строка запроса
        собирается заново: подмена двух ключей в общем объекте разъехалась бы с
        именем файла на первой правке."""
        download = self.journal.split('const download = useCallback')[1].split(
            '}, [apiBaseUrl')[0]
        self.assertIn("search.set('date_from', from)", download)
        self.assertIn("search.set('date_to', to)", download)
        self.assertIn('exportFileName(from, to)', download)
        self.assertIn("search.set('kinds'", download)
        self.assertIn("search.set('user_id'", download)

    def test_phone_filter_costs_one_request_not_eleven(self):
        """Телефон применяется по Enter и по уходу из поля. Запрос на каждую
        набранную цифру — это одиннадцать обращений к журналу на один номер."""
        self.assertIn('phoneDraft', self.journal)
        self.assertIn('onBlur={applyPhone}', self.journal)
        self.assertIn("if (event.key === 'Enter') applyPhone();", self.journal)

    def test_the_date_lives_in_a_day_separator_not_in_every_row(self):
        """За неделю дата повторялась в пятидесяти строках подряд. В строке
        осталось время, день вынесен в разделитель."""
        self.assertIn('formatDayFull', self.journal)
        self.assertIn('dayKeyOf', self.journal)
        row = self.journal.split('const JournalRow = ')[1].split('const Stat = ')[0]
        self.assertIn('formatTime(item.created_at)', row)
        self.assertNotIn('formatDateTime', row)

    def test_the_pager_is_the_shared_one(self):
        """Своих «Назад/Вперёд» в портале быть не должно — у общего пагинатора
        есть ещё и «1–50 из 179», которого не хватало."""
        self.assertIn('<IosPager', self.journal)
        self.assertNotIn('Вперёд', self.journal)


if __name__ == '__main__':
    unittest.main()

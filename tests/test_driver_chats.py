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
from unittest import mock
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver_chats import access, chat2desk, report, schema  # noqa: E402

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
    """Стадия выката: пока флаг включён, раздел виден ТОЛЬКО супер-админу.

    Решение владельца 03.09.2026 на время обкатки. Периметр при этом описан
    целиком и проверен классами ниже — открыть раздел отделу значит снять флаг,
    а не восстанавливать правила по памяти.
    """

    def test_flag_is_on_right_now(self):
        """Если флаг снимут, этот тест напомнит поправить и фронт: там свой
        двойник, и разъехавшись, они дадут пункт меню, ведущий в 403."""
        self.assertTrue(access.ROLLOUT_SUPER_ADMIN_ONLY)
        source = APP_JSX.read_text(encoding='utf-8-sig')
        self.assertIn('const DRIVER_CHATS_ROLLOUT_SUPER_ADMIN_ONLY = true;', source)

    def test_only_super_admin_passes_while_the_flag_is_on(self):
        with mock.patch.object(access, 'ROLLOUT_SUPER_ADMIN_ONLY', True):
            self.assertTrue(access.can_open_section(
                ctx(role='super_admin', department_code='')))
            for person in (ctx(), ctx(role='trainee'), ctx(role='sv'),
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

    def test_chat_manager_is_excluded_even_inside_szov(self):
        """Главный случай: по отделу он проходит, отсекает его направление.

        Раздел существует, чтобы оператор линии передал переписку ЕМУ; сами эти
        диалоги он видит у себя в Chat2Desk целиком и без посредника.
        """
        for role in ('operator', 'trainee', 'sv'):
            with self.subTest(role=role):
                self.assertFalse(access.section_perimeter_allows(
                    ctx(role=role, direction_model='chat_manager')))

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

    def test_global_admin_and_szov_head_are_allowed(self):
        self.assertTrue(access.section_perimeter_allows(ctx(role='super_admin', department_code='')))
        self.assertTrue(access.section_perimeter_allows(ctx(role='admin', department_code='')))
        self.assertTrue(access.section_perimeter_allows(
            ctx(role='admin', department_code='szov', headed=('szov',))))

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

    def test_chat_manager_supervisor_is_still_out(self):
        """Гейт журнала не должен обходить гейт раздела."""
        self.assertFalse(access.can_view_journal(
            ctx(role='sv', direction_model='chat_manager')))

    def test_journal_rule_has_no_hardcoded_person(self):
        """Ару Омарова проходит как СВ СЗоВ, а не по своему id.

        Именной список ломается ровно в тот день, когда человек меняется, и
        ломается молча. Правило «СВ этого отдела» переживает и замену, и
        появление второго руководителя.
        """
        source = (ROOT / 'driver_chats' / 'access.py').read_text(encoding='utf-8')
        self.assertNotRegex(source, r'==\s*205\b')
        self.assertNotIn('Омаров', source.replace('Ару Омарова', ''))


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

    def test_screenshot_hint_is_present(self):
        """Снимок делает человек — интерфейс обязан сказать, чем именно."""
        self.assertIn('⌘⇧4', self.source)

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


if __name__ == '__main__':
    unittest.main()

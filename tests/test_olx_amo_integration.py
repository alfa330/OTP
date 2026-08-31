# -*- coding: utf-8 -*-
"""Робот «Лиды OLX» (задача #223): периметр, схема, запись в amoCRM, ветвление.

Тест держит четыре границы, каждую из которых легко потерять при рефакторинге и
ни одну — заметить по симптомам:

  * ПЕРИМЕТР. Раздел показывает телефоны кандидатов и переписку девяти
    корпоративных кабинетов. Открыт он админам и главам «Маркетинга»/«ОП»;
    подключать кабинеты (то есть выдавать доступ к переписке) — только
    глобальному админу.

  * ДЕДУПЛИКАЦИЯ. Уникальный индекс по (кабинет, номер, дата) — единственное,
    что спасает от дублей при параллельном опросе. Его условие `WHERE` тоже
    существенно: без него повторная попытка после сбоя стала бы запрещена.

  * ЗАПИСЬ В amoCRM. Идентификаторы сняты с боевого аккаунта. Тег обязан ехать
    ПО ID: в справочнике лежат близкие мусорные двойники, и передача по имени
    завела бы очередной.

  * ВЕТВЛЕНИЕ. Три исхода разбора обращения — номер есть, номер кривой, номера
    нет — ведут к трём разным действиям, и перепутать их значит либо потерять
    лид, либо ответить кандидату вместо создания сделки.

В базу тест не ходит: подключение к боевому Postgres из «модульных» тестов —
известная ловушка проекта, поэтому DDL и SQL проверяются по тексту, а amoCRM и
OLX подменяются заглушками.
"""

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from olx_amo import access, amo_writer, cabinets, phones, schema, service  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP_JSX = ROOT / 'src' / 'App.jsx'
BOT = ROOT / 'bot_schedule2.py'
DATABASE = ROOT / 'database.py'


def ctx(role='operator', user_id=10, department_code=None,
        headed=(), headed_codes=()):
    """Портрет сотрудника. По умолчанию — рядовой без отдела."""
    return {
        'user_id': user_id,
        'name': 'Тест',
        'role': role,
        'department_id': None,
        'department_code': department_code,
        'headed_department_ids': list(headed),
        'headed_department_codes': list(headed_codes),
    }


# ─────────────────────────────────────────────────────────────────────────────

class AccessTests(unittest.TestCase):
    def test_global_admin_sees_everything(self):
        for role in ('admin', 'super_admin'):
            who = ctx(role=role)
            self.assertTrue(access.can_view(who), role)
            self.assertTrue(access.can_manage_cabinets(who), role)

    def test_marketing_and_sales_heads_see_the_journal(self):
        for code in ('marketing', 'op'):
            who = ctx(role='sv', headed=(1,), headed_codes=(code,))
            self.assertTrue(access.can_view(who), code)
            # Но НЕ подключают кабинеты: это выдача доступа к переписке.
            self.assertFalse(access.can_manage_cabinets(who), code)

    def test_other_department_heads_are_out(self):
        for code in ('szov', 'tez', 'front_office', 'accounting', 'hr'):
            who = ctx(role='sv', headed=(2,), headed_codes=(code,))
            self.assertFalse(access.can_view(who), code)

    def test_department_head_with_admin_role_is_not_a_global_admin(self):
        """Назначение главой ЗАМЕНЯЕТ базовую роль и режет периметр отделом."""
        head = ctx(role='admin', headed=(3,), headed_codes=('szov',))
        self.assertFalse(access.can_view(head))
        self.assertFalse(access.can_manage_cabinets(head))

    def test_plain_employees_and_trainers_are_out(self):
        for role in ('operator', 'trainee', 'trainer', 'sv'):
            self.assertFalse(access.can_view(ctx(role=role)), role)

    def test_capabilities_shape_is_stable(self):
        self.assertEqual({'can_view', 'can_manage_cabinets'},
                         set(access.capabilities(ctx(role='admin')).keys()))


class SchemaTests(unittest.TestCase):
    """DDL проверяем по тексту: в боевую базу «модульный» тест не ходит."""

    def setUp(self):
        self.ddl = '\n'.join(schema._STATEMENTS)

    def test_all_four_tables_are_declared(self):
        for table in ('olx_accounts', 'olx_threads', 'olx_journal', 'olx_poll_runs'):
            self.assertIn('CREATE TABLE IF NOT EXISTS %s' % table, self.ddl, table)

    def test_everything_is_idempotent(self):
        """Схема разворачивается при КАЖДОМ старте приложения."""
        for statement in schema._STATEMENTS:
            head = statement.strip().upper()
            if head.startswith('CREATE TABLE'):
                self.assertIn('IF NOT EXISTS', head)
            elif head.startswith('CREATE'):
                self.assertIn('IF NOT EXISTS', head)

    def test_tables_come_before_indexes(self):
        """Индекс по столбцу, объявленному ниже, уронил бы весь разворот схемы.

        В списке DDL таблица и её индексы стоят рядом — так читается диффом. За
        порядок отвечает сам разворот: он проходит список дважды, сначала беря
        только таблицы. Проверяем именно его, а не порядок в списке.
        """
        applied = []

        class _Cursor(object):
            def execute(self, statement):
                applied.append('table' if schema._is_table(statement) else 'index')

        schema.init_olx_amo_schema(_Cursor())
        self.assertNotIn('table', applied[applied.index('index'):],
                         'таблицы обязаны развернуться до индексов')

    def test_dedupe_index_exists_and_is_scoped(self):
        """Уникальный индекс — единственная защита от дублей при гонке."""
        index = next(s for s in schema._STATEMENTS if 'idx_olx_journal_dedupe' in s)
        self.assertIn('UNIQUE', index)
        self.assertIn('cabinet_code, phone_normalized, (message_at::date)', index)
        # Условие обязательно: без него повтор после сбоя стал бы запрещён.
        self.assertIn("WHERE result IN ('lead_created', 'manual_review')", index)
        self.assertIn('phone_normalized IS NOT NULL', index)

    def test_journal_keeps_both_phone_forms_and_both_times(self):
        """Раздел 7 ТЗ требует номер до и после и обе отметки времени."""
        journal = next(s for s in schema._STATEMENTS
                       if 'CREATE TABLE IF NOT EXISTS olx_journal' in s)
        for column in ('phone_raw', 'phone_normalized', 'message_at',
                       'lead_created_at', 'latency_ms', 'error_text', 'tag'):
            self.assertIn(column, journal, column)

    def test_result_vocabulary_covers_all_branches(self):
        self.assertEqual(
            {'lead_created', 'duplicate', 'manual_review', 'canned_reply',
             'skipped', 'error'},
            set(schema.JOURNAL_RESULTS))


class AmoWriterTests(unittest.TestCase):
    def test_ids_match_the_live_account(self):
        """Сняты чтением боевого аккаунта igroupkz 31.08.2026."""
        self.assertEqual(5524684, amo_writer.PIPELINE_ID)       # «Отдел продаж»
        self.assertEqual(48846277, amo_writer.STATUS_ID)        # «Новая заявка»
        self.assertEqual(8303491, amo_writer.RESPONSIBLE_USER_ID)  # «Администратор»
        self.assertEqual(892223, amo_writer.CONTACT_PHONE_FIELD_ID)
        self.assertEqual(1277159, amo_writer.CONTACT_PHONE_WORK_ENUM_ID)

    def test_every_cabinet_has_a_tag(self):
        self.assertEqual({c.code for c in cabinets.CABINETS}, set(amo_writer.TAG_IDS))

    def test_lead_carries_stage_tag_and_responsible(self):
        lead = amo_writer.build_lead('77757025144', 'itaxi')
        self.assertEqual('77757025144', lead['name'])
        self.assertEqual(5524684, lead['pipeline_id'])
        self.assertEqual(48846277, lead['status_id'])
        self.assertEqual(8303491, lead['responsible_user_id'])

    def test_tag_travels_by_id_not_by_name(self):
        """В справочнике amoCRM лежат мусорные двойники вроде 'forma_ olx_цр'."""
        tags = amo_writer.build_lead('77757025144', 'cr')['_embedded']['tags']
        self.assertEqual(1, len(tags))
        self.assertEqual(717413, tags[0]['id'])
        self.assertEqual('forma_olx_цр', tags[0]['name'])

    def test_phone_goes_into_the_work_slot_of_the_phone_field(self):
        """«Раб. тел.» — это enum WORK внутри поля «Телефон», а не своё поле."""
        contact = amo_writer.build_lead('77757025144', 'jana')['_embedded']['contacts'][0]
        field = contact['custom_fields_values'][0]
        self.assertEqual(892223, field['field_id'])
        self.assertEqual('77757025144', field['values'][0]['value'])
        self.assertEqual(1277159, field['values'][0]['enum_id'])

    def test_no_metadata_block(self):
        """В аккаунте включено «Неразобранное»: с metadata сделка уехала бы туда."""
        lead = amo_writer.build_lead('77757025144', 'global')
        self.assertNotIn('metadata', lead['_embedded'])

    def test_unknown_cabinet_is_refused_not_silently_untagged(self):
        """ТЗ запрещает сделку без тега кабинета — источник стал бы неизвестен."""
        with self.assertRaises(amo_writer.AmoWriteError):
            amo_writer.build_lead('77757025144', 'нет такого кабинета')


class MessageBranchingTests(unittest.TestCase):
    """Три исхода разбора обращения ведут к трём разным действиям."""

    @staticmethod
    def msg(text=None, phone=None):
        payload = {'id': '1', 'type': 'received', 'text': text or ''}
        if phone:
            payload['phone'] = phone
        return payload

    def test_number_in_text_is_found(self):
        found, raw, scan = service.extract_phone(
            self.msg('Здравствуйте, мой номер 8 775 702 51 44'))
        self.assertEqual('77757025144', found)
        self.assertFalse(scan.needs_manual_review)

    def test_dedicated_phone_field_wins(self):
        """В категориях «Работа» OLX кладёт номер отдельным полем."""
        found, raw, _ = service.extract_phone(
            self.msg('Здравствуйте!', phone='+7 775 702 51 44'))
        self.assertEqual('77757025144', found)
        self.assertEqual('+7 775 702 51 44', raw)

    def test_broken_number_goes_to_manual_review_not_to_a_reply(self):
        found, raw, scan = service.extract_phone(self.msg('мой номер +996 555 123456'))
        self.assertIsNone(found)
        self.assertTrue(scan.rejected)
        self.assertTrue(scan.needs_manual_review)

    def test_message_without_a_number_asks_for_a_canned_reply(self):
        found, raw, scan = service.extract_phone(self.msg('А какие условия работы?'))
        self.assertIsNone(found)
        self.assertFalse(scan.rejected)
        self.assertFalse(scan.needs_manual_review)

    def test_our_own_line_quoted_back_is_not_a_lead(self):
        """Кандидат процитировал наш автоответ — сделка с нашим номером мусор."""
        found, _, scan = service.extract_phone(
            self.msg('Здравствуйте! По Вашему вопросу просьба позвонить по номеру 87008581223'))
        self.assertIsNone(found)
        self.assertEqual(['77008581223'], scan.own_lines)
        self.assertFalse(scan.needs_manual_review)

    def test_our_own_message_is_not_an_appeal(self):
        from olx_amo.olx_client import message_is_incoming
        self.assertFalse(message_is_incoming({'type': 'sent', 'text': 'привет'}))
        self.assertTrue(message_is_incoming({'type': 'received', 'text': 'привет'}))


class CabinetConfigTests(unittest.TestCase):
    def test_cabinet_is_configured_only_with_a_full_credential_pair(self):
        """Один ключ без client_id — это не доступ, а половина доступа."""
        cab = cabinets.BY_CODE['itaxi']
        keys = ('OLX_CLIENT_ID_%d' % cab.env_index,
                'OLX_CLIENT_SECRET_%d' % cab.env_index,
                'OLX_API_KEY_%d' % cab.env_index)
        saved = {k: os.environ.get(k) for k in keys}
        try:
            for key in keys:
                os.environ.pop(key, None)
            self.assertFalse(cab.is_configured())

            os.environ[keys[2]] = 'x' * 48          # только ключ из файла доступов
            self.assertFalse(cab.is_configured(), 'ключа без client_id недостаточно')

            os.environ[keys[0]] = '200123'
            self.assertTrue(cab.is_configured(), 'client_id + секрет — этого хватает')
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_each_cabinet_may_keep_its_own_return_address(self):
        """Заявки заводились в разное время, и адрес возврата в них разный.

        Менять уже вписанный адрес опасно: если тем же приложением пользуется
        что-то ещё, подмена сломает ЕГО экран согласия. Поэтому подстраиваемся
        мы: у кабинета может быть свой адрес, и он главнее общего.
        """
        cab = cabinets.BY_CODE['adal']
        keys = ('OLX_REDIRECT_URI', 'OLX_REDIRECT_URI_%d' % cab.env_index)
        saved = {k: os.environ.get(k) for k in keys}
        try:
            for key in keys:
                os.environ.pop(key, None)
            self.assertEqual('', cab.env_redirect_uri)

            os.environ[keys[0]] = 'https://portal/общий'
            self.assertEqual('https://portal/общий', cab.env_redirect_uri)

            os.environ[keys[1]] = 'https://portal/только-adal'
            self.assertEqual('https://portal/только-adal', cab.env_redirect_uri,
                             'адрес кабинета обязан быть главнее общего')
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_no_cabinet_credentials_leak_into_the_repository(self):
        """Репозиторий публичный: логины кабинетов — почтовые ящики на gmail."""
        source = (ROOT / 'olx_amo' / 'cabinets.py').read_text(encoding='utf-8')
        self.assertNotIn('@gmail', source)
        self.assertNotIn('OLX_PASSWORD_1=', source)


class WiringTests(unittest.TestCase):
    """Раздел, который не подключён, отличается от сломанного только логом."""

    @classmethod
    def setUpClass(cls):
        cls.app = APP_JSX.read_text(encoding='utf-8-sig')
        cls.bot = BOT.read_text(encoding='utf-8')
        cls.database = DATABASE.read_text(encoding='utf-8')

    def test_schema_is_deployed_on_startup(self):
        self.assertIn('self._init_olx_amo_schema_tx(cursor)', self.database)
        self.assertIn('def _init_olx_amo_schema_tx', self.database)
        # SAVEPOINT: весь _init_db идёт одной транзакцией, и падение схемы
        # раздела не должно ронять инициализацию всей базы.
        self.assertIn('SAVEPOINT olx_amo_schema', self.database)

    def test_blueprint_is_registered(self):
        self.assertIn('from olx_amo.routes import build_olx_amo_blueprint', self.bot)
        self.assertIn('build_olx_amo_blueprint(', self.bot)

    def test_poll_job_runs_twice_a_minute(self):
        """ТЗ: опрос не реже раза в 30 секунд, накопительная выгрузка не годится."""
        self.assertIn("id='olx_amo_poll'", self.bot)
        self.assertIn("CronTrigger(second='0,30'", self.bot)
        self.assertIn("id='olx_amo_retry'", self.bot)

    def test_poll_job_cannot_overlap_itself(self):
        """Два одновременных обхода читали бы те же чаты и удваивали сделки."""
        block = self.bot[self.bot.index("id='olx_amo_poll'"):]
        block = block[:block.index(')')]
        self.assertIn('max_instances=1', block)
        self.assertIn('coalesce=True', block)

    def test_robot_has_its_own_thread_pool(self):
        """В общем пуле бота четыре места на всё приложение."""
        self.assertIn("olx_amo_pool = ThreadPoolExecutor(", self.bot)
        self.assertIn("thread_name_prefix='olx-amo'", self.bot)

    def test_menu_item_is_declared_exactly_once(self):
        """Два вхождения означали бы дубль по ролевым ветвям — они разъедутся."""
        self.assertEqual(1, self.app.count("handleSidebarViewNavigation(e, 'olx_leads')"))

    def test_menu_item_is_gated_by_the_section_predicate(self):
        self.assertIn('{canAccessOlxLeadsSection && (', self.app)
        self.assertIn('const canAccessOlxLeadsSection = canAccessOlxLeadsForUser(user);',
                      self.app)

    def test_view_is_rendered(self):
        self.assertIn('view === "olx_leads" && canAccessOlxLeadsSection', self.app)
        self.assertIn("import('./components/olx/OlxLeadsView')", self.app)

    def test_visibility_guard_lets_the_section_through(self):
        """Без этой строки гард отдела выкинул бы главу «Маркетинга» обратно."""
        self.assertIn("if (view === 'olx_leads' && canAccessOlxLeadsSection) return;",
                      self.app)

    def test_journal_can_be_exported_for_an_arbitrary_period(self):
        """Раздел 7 ТЗ: «Журнал доступен для выгрузки за произвольный период»."""
        routes = (ROOT / 'olx_amo' / 'routes.py').read_text(encoding='utf-8')
        self.assertIn("@section_route('/journal/export')", routes)
        self.assertIn('xlsxwriter', routes)
        # Номер — текстом: иначе Excel съедает его как число.
        self.assertIn('write_string(line, 5', routes)

        queries_src = (ROOT / 'olx_amo' / 'queries.py').read_text(encoding='utf-8')
        self.assertIn('def journal_for_export(', queries_src)

        view = (ROOT / 'src' / 'components' / 'olx' / 'OlxLeadsView.jsx').read_text(
            encoding='utf-8')
        self.assertIn('journal/export', view)
        # Ссылкой файл не забрать: она не несёт заголовок авторизации.
        self.assertIn("responseType: 'blob'", view)

    def test_alerts_are_sent_on_state_change_only(self):
        """Раздел 7 ТЗ требует уведомлений, но повтор каждые 5 минут — это шум."""
        service_src = (ROOT / 'olx_amo' / 'service.py').read_text(encoding='utf-8')
        self.assertIn('def collect_alerts(', service_src)
        self.assertIn('if previous == state:', service_src,
                      'уведомление должно уходить только на СМЕНЕ состояния')
        # Четыре повода ровно из ТЗ.
        for state in ('needs_auth', 'stale', 'amo_failing', 'silent'):
            self.assertIn("'%s'" % state, service_src, state)

        self.assertIn("id='olx_amo_alerts'", self.bot)
        # Адресаты выбираются в разделе из групп, где уже есть бот, а не задаются
        # переменной окружения: список меняют живые люди чаще, чем выкатывается
        # релиз. Подробнее — AlertChatsTests в test_olx_amo_service.py.
        self.assertIn('list_alert_chats', self.bot)

        schema_src = (ROOT / 'olx_amo' / 'schema.py').read_text(encoding='utf-8')
        self.assertIn('CREATE TABLE IF NOT EXISTS olx_alerts', schema_src)

    def test_pager_is_fed_one_based_pages(self):
        """IosPager считает страницы С ЕДИНИЦЫ, а состояние раздела — с нуля.

        Состояние с нуля, потому что из него умножением получается offset. Если
        отдать пейджеру то же число без сдвига, на первой странице не
        подсветится ни одна кнопка, а нажатие «1» перелистнёт на вторую порцию
        и первые пятьдесят обращений станут невидимы.
        """
        view = (ROOT / 'src' / 'components' / 'olx' / 'OlxLeadsView.jsx').read_text(
            encoding='utf-8')
        self.assertIn('page={page + 1}', view)
        self.assertIn('onPage={(number) => onPage(number - 1)}', view)

    def test_badge_tones_exist_in_the_design_system(self):
        """Неизвестный тон бейджа молча падает в серый — ошибка перестала бы гореть."""
        ios = (ROOT / 'src' / 'components' / 'ui' / 'ios.jsx').read_text(encoding='utf-8')
        palette = set(re.findall(r"^\s{4}(\w+):\s*'bg-", ios, re.M))
        view = (ROOT / 'src' / 'components' / 'olx' / 'OlxLeadsView.jsx').read_text(
            encoding='utf-8')
        used = set(re.findall(r"^\s+\w+:\s*'(\w+)',\s*$", view, re.M))
        # Берём только те значения, что похожи на тона (остальное — подписи).
        tones = {t for t in used if t in palette or t in ('rose', 'orange', 'yellow')}
        self.assertTrue(tones, 'не нашли ни одного тона — проверьте разбор')
        self.assertTrue(tones <= palette,
                        'тонов нет в палитре ios.jsx: %s' % sorted(tones - palette))

    def test_frontend_and_backend_agree_on_the_departments(self):
        """Две копии одного правила обязаны совпадать буквально."""
        self.assertIn("const OLX_LEADS_HEAD_DEPARTMENT_CODES = new Set(['op', 'marketing']);",
                      self.app)
        self.assertEqual(('op', 'marketing'), access.SECTION_HEAD_DEPARTMENT_CODES)


if __name__ == '__main__':
    unittest.main()

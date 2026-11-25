# -*- coding: utf-8 -*-
"""Раздел «Воронка найма» (#404): правила, права, разбор файла, HeadHunter, отчёты, проводка.

Без базы: процесс на схеме раздела проверяет tests/test_hiring_postgres.py.
"""

import io
import re
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook

from hiring import access, hh, reports, rules, upload
from tests import source_cache

ROOT = Path(__file__).resolve().parents[1]
BOT_PY = source_cache.monolith_flat_path()
DATABASE_PY = source_cache.database_flat_path()
APP_JSX = ROOT / 'src' / 'App.jsx'
SOURCES_PY = ROOT / 'notifications' / 'sources.py'
BELL_JSX = ROOT / 'src' / 'components' / 'notifications' / 'NotificationsBell.jsx'
SCOPES_PY = ROOT / 'partner_keys' / 'scopes.py'


def ctx(**overrides):
    base = {'user_id': 7, 'name': 'Рекрутер', 'role': 'operator', 'status': 'working', 'department_id': 1,
            'department_code': 'op', 'headed_ids': [], 'headed_codes': [], 'grant': None, 'trainer_group_ids': [],
            'vacancy_department_ids': []}
    base.update(overrides)
    return base


class PhoneTests(unittest.TestCase):
    def test_normalizes_to_plus7(self):
        for raw in ('8 705 123 45 67', '+7 (705) 123-45-67', '77051234567', '7051234567', '  8-705-1234567 '):
            self.assertEqual(rules.normalize_phone(raw), '+77051234567', raw)

    def test_rejects_garbage(self):
        for raw in ('', None, '123', '+1 650 555 1234', '705123456', 'abc'):
            self.assertIsNone(rules.normalize_phone(raw), raw)

    def test_pretty_and_whatsapp(self):
        self.assertEqual(rules.pretty_phone('+77051234567'), '+7 705 123 45 67')
        self.assertEqual(rules.whatsapp_link('87051234567'), 'https://web.whatsapp.com/send?phone=77051234567')
        self.assertEqual(rules.phone_key('8 705 123 45 67'), '7051234567')


class StageTests(unittest.TestCase):
    def test_ten_columns_in_tz_order(self):
        # Десять колонок ТЗ 4.1 и одиннадцатая — архив «Не отвечает звонок/чат» (владелец 20.11.2026).
        self.assertEqual(rules.STAGE_CODES, ('contacts', 'responses', 'in_work', 'invited_office', 'invited_training',
                                             'refused', 'on_training', 'on_line', 'dropped', 'hired', 'no_response'))
        self.assertEqual(rules.STAGE_LABELS['no_response'], 'Не отвечает звонок/чат')
        self.assertEqual(rules.TARGETS_FROM['no_response'], ['in_work', 'refused'])
        self.assertNotIn('no_response', rules.ASSIGNED_STAGES)          # без задач и без возврата в пул через месяц
        self.assertIn('no_response', rules.RELEASABLE_STAGES)
        self.assertNotIn('no_response', [dst for (_src, dst) in rules.TRANSITIONS])   # туда — только сторож

    def test_ten_days_is_a_category_next_to_seven(self):
        # «Отработал 10 дней» (SZOV-10) — категория тренера, а не этап: колонка доски и строка воронки сразу за
        # «Отработал 7 дней», карточка же остаётся в «7 днях» — поэтому в этапах и переходах её нет.
        self.assertNotIn('hired_10', rules.STAGE_CODES)
        self.assertEqual([code for code, _ in rules.BOARD_COLUMNS],
                         ['contacts', 'responses', 'in_work', 'invited_office', 'invited_training', 'refused', 'on_training',
                          'on_line', 'dropped', 'hired', 'hired_10', 'no_response'])
        self.assertEqual(dict(rules.BOARD_COLUMNS)['hired_10'], 'Отработал 10 дней')
        self.assertEqual(rules.FUNNEL_STAGES[-2:], ('hired', 'hired_10'))
        self.assertEqual(rules.FUNNEL_LABELS['hired_10'], 'Отработал 10 дней')
        self.assertNotIn('hired_10', [dst for (_src, dst) in rules.TRANSITIONS])
        # «Отработал 7 дней» руками тоже не ставится — только система по сменам.
        self.assertNotIn('hired', [dst for (_src, dst) in rules.TRANSITIONS])

    def test_whatsapp_repeat_only_after_72_hours(self):
        # Владелец 23.11.2026: новая карточка «повторно» по сообщению WhatsApp — только через 72 часа после закрытия.
        from datetime import datetime as dt, timedelta as td
        closed = dt(2026, 11, 20, 12, 35)
        self.assertEqual(rules.WA_REPEAT_AFTER_HOURS, 72)
        self.assertFalse(rules.wa_repeat_allowed(closed, closed + td(hours=2)))
        self.assertFalse(rules.wa_repeat_allowed(closed, closed + td(hours=72) - td(seconds=1)))
        self.assertTrue(rules.wa_repeat_allowed(closed, closed + td(hours=72)))
        self.assertTrue(rules.wa_repeat_allowed(None, closed))           # закрытия не было — как раньше

    def test_shift_credits_one_decision_for_seven_and_ten(self):
        """Логика SZOV-10 буквально: 7 смен — «7 дней»; 10 — обе; 8 — рекрутеру 7, тренеру ничего; недобор до 10 не
        отклоняет «7 дней»; раз засчитанное не повторяется."""
        credits = rules.shift_credits
        self.assertEqual(credits('on_line', None, 6), [])
        self.assertEqual(credits('on_line', None, 7), ['hired'])
        self.assertEqual(credits('on_line', None, 8), ['hired'])
        self.assertEqual(credits('on_line', None, 9), ['hired'])
        self.assertEqual(credits('on_line', None, 10), ['hired', 'hired_10'])     # обе за один пересчёт
        self.assertEqual(credits('on_line', None, 12), ['hired', 'hired_10'])
        self.assertEqual(credits('hired', None, 8), [])                          # 7 уже засчитано, до 10 не дошёл
        self.assertEqual(credits('hired', None, 10), ['hired_10'])
        self.assertEqual(credits('hired', '2026-11-23 12:12', 11), [])           # 10 уже засчитано
        for stage in ('on_training', 'dropped', 'refused', 'in_work', None):
            self.assertEqual(credits(stage, None, 15), [], stage)                # только линия и «7 дней»
        self.assertEqual((rules.SHIFTS_TO_HIRE, rules.SHIFTS_FOR_TRAINER), (7, 10))

    def test_substages_of_tz_4_1(self):
        self.assertEqual([c for c, _ in rules.SUBSTAGES['in_work']], ['dial', 'no_answer', 'whatsapp'])
        self.assertEqual([c for c, _ in rules.SUBSTAGES['invited_office']], ['expected', 'came', 'no_show'])
        self.assertEqual([c for c, _ in rules.SUBSTAGES['refused']], ['ours', 'candidate'])
        self.assertEqual([c for c, _ in rules.SUBSTAGES['dropped']], ['self', 'we'])
        self.assertTrue(rules.substage_allowed('in_work', None))
        self.assertFalse(rules.substage_allowed('refused', None))
        self.assertFalse(rules.substage_allowed('in_work', 'came'))

    def test_targets_follow_tz_4_2(self):
        self.assertEqual(rules.TARGETS_FROM['in_work'], ['invited_office', 'invited_training', 'refused'])
        self.assertEqual(rules.TARGETS_FROM['invited_office'], ['invited_training', 'refused'])
        self.assertEqual(rules.TARGETS_FROM['on_line'], ['dropped'])   # «Отработал 7 дней» — сам, по сменам
        self.assertEqual(rules.TARGETS_FROM['refused'], ['in_work'])
        self.assertNotIn('hired', rules.TARGETS_FROM)

    def test_reasons_seed_is_the_tz_lists(self):
        self.assertEqual(len(rules.SEED_REASONS['refusal_ours']), 10)
        self.assertEqual(len(rules.SEED_REASONS['refusal_candidate']), 9)
        self.assertEqual(len(rules.SEED_REASONS['drop_self']), 6)
        self.assertEqual(len(rules.SEED_REASONS['drop_we']), 5)
        self.assertIn(rules.REASON_NO_SHOW_OFFICE, rules.SEED_REASONS['refusal_candidate'])
        self.assertIn(rules.REASON_NO_CONTACT, rules.SEED_REASONS['refusal_candidate'])
        for names in rules.SEED_REASONS.values():
            self.assertEqual(names[-1], 'Другое')

    def test_sources_seed(self):
        codes = [code for code, *_ in rules.SEED_SOURCES]
        self.assertEqual(codes[:6], ['hh', 'kaspi', 'olx', 'target', 'manual', 'referral'])
        self.assertEqual({kind for _c, _n, kind, _p in rules.SEED_SOURCES}, {'responses', 'contacts'})

    def test_source_colors_match_the_interface(self):
        # Владелец 20.11.2026: каналы слиты с источниками, цвет — у источника; стартовые цвета — его слова о каналах
        # 09.11.2026: Таргет — синий, hh — красный, Каспи — розовый.
        self.assertEqual(rules.SEED_SOURCE_COLORS, {'target': 'blue', 'hh': 'red', 'kaspi': 'pink'})
        self.assertTrue(set(rules.SEED_SOURCE_COLORS.values()) <= set(rules.SOURCE_COLORS))
        self.assertTrue(set(rules.SEED_SOURCE_COLORS) <= {code for code, *_ in rules.SEED_SOURCES})
        # «Таргет» ставится сам по коду источника (владелец 09.11.2026).
        self.assertEqual(rules.SOURCE_TARGET, 'target')
        # Цвет проверяет сервер, рисует интерфейс — коды палитры одни и те же и в том же порядке.
        meta = (ROOT / 'src' / 'components' / 'hiring' / 'hiringMeta.js').read_text(encoding='utf-8')
        block = re.search(r'export const SOURCE_COLORS = \{(.*?)\n\};', meta, re.S).group(1)
        self.assertEqual(tuple(re.findall(r'^\s+(\w+): \{ label:', block, re.M)), rules.SOURCE_COLORS)

    def test_response_group_total_is_the_count_not_the_portion(self):
        """Число у группы «Откликов» — сколько всего (с отборами и периодом), а не сколько карточек пришло порцией; карточки
        группы — номерами, без копий; порядок — справочника, «Без источника» — последней, выключенный источник — с именем."""
        from hiring import routes
        cards = [{'id': 1, 'source_id': 4}, {'id': 2, 'source_id': 4}, {'id': 3, 'source_id': None}, {'id': 5, 'source_id': 7}]
        sources = [{'id': 1, 'name': 'HeadHunter', 'color': 'red', 'is_active': True},
                   {'id': 7, 'name': 'Старый', 'color': 'teal', 'is_active': False},
                   {'id': 4, 'name': 'Таргет', 'color': 'blue', 'is_active': True}]
        groups = routes.response_groups(cards, {None: 1, 4: 198, 7: 1}, sources)
        self.assertEqual([(g['name'], g['color'], g['total'], g['card_ids']) for g in groups],
                         [('Старый', 'teal', 1, [5]), ('Таргет', 'blue', 198, [1, 2]), ('Без источника', None, 1, [3])])
        self.assertEqual(routes._source_ids_arg('4, 10,abc,none,99999999999,-1'), ([4, 10], True))
        self.assertEqual(routes._source_ids_arg('-'), ([], False))

    def test_invited_columns_by_day(self):
        """Владелец 24.11.2026: «Приглашен в офис» и «Приглашен на обучение» — по дням «начиная от сегодня», «прошлые дни
        должны уходить вниз». День — дата визита и старт группы; сегодня и дальше по порядку, ниже прошедшие от вчерашнего,
        без даты — последней; число у дня — сколько всего, а не порция; карточки — номерами."""
        from datetime import date
        from hiring import routes
        today = date(2026, 11, 24)
        self.assertEqual(rules.DATED_STAGES, ('invited_office', 'invited_training'))
        self.assertEqual(rules.board_day({'stage': 'invited_office', 'office_visit_at': datetime(2026, 11, 25, 14, 0)}),
                         date(2026, 11, 25))
        self.assertEqual(rules.board_day({'stage': 'invited_training', 'group_start_date': date(2026, 11, 30),
                                          'office_visit_at': datetime(2026, 11, 20, 10, 0)}), date(2026, 11, 30))
        self.assertIsNone(rules.board_day({'stage': 'invited_office', 'office_visit_at': None}))
        self.assertIsNone(rules.board_day({'stage': 'invited_training', 'group_start_date': None}))
        self.assertIsNone(rules.board_day({'stage': 'in_work', 'office_visit_at': datetime(2026, 11, 25, 14, 0)}))
        days = [date(2026, 11, 20), None, date(2026, 11, 26), today, date(2026, 11, 23), date(2026, 11, 25)]
        self.assertEqual(rules.day_order(days, today), [today, date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 23),
                                                        date(2026, 11, 20), None])
        self.assertEqual(rules.day_order([], today), [])
        cards = [{'id': 1, 'stage': 'invited_office', 'office_visit_at': datetime(2026, 11, 24, 9, 30)},
                 {'id': 2, 'stage': 'invited_office', 'office_visit_at': datetime(2026, 11, 23, 16, 0)},
                 {'id': 3, 'stage': 'invited_office', 'office_visit_at': datetime(2026, 11, 24, 15, 0)}]
        groups = routes.day_groups(cards, {date(2026, 11, 23): 1, today: 2, date(2026, 11, 27): 4, None: 1}, today)
        self.assertEqual([(g['day'], g['total'], g['card_ids']) for g in groups],
                         [('2026-11-24', 2, [1, 3]), ('2026-11-27', 4, []), ('2026-11-23', 1, [2]), (None, 1, [])])
        self.assertEqual(routes._days_arg('2026-11-24, 2026-02-30,abc,none,2026-11-25'),
                         ([date(2026, 11, 24), date(2026, 11, 25)], True))
        self.assertEqual(routes._days_arg('-'), ([], False))
        self.assertEqual(routes._days_arg(None), ([], False))
        # Перенесённое обучение (24.11.2026) — в колонке по своей дате кандидата, а не по старту группы.
        self.assertEqual(rules.board_day({'stage': 'invited_training', 'group_start_date': date(2026, 11, 25),
                                          'training_date': date(2026, 11, 27)}), date(2026, 11, 27))

    def test_reschedule_reasons_and_feed_line(self):
        """Владелец 24.11.2026: причины переноса — «форс-мажор, поменялись планы, учёба, другое с комментом»; строка ленты
        говорит, с какой даты на какую и почему."""
        from datetime import date
        self.assertEqual(rules.RESCHEDULE_REASONS, (('force_majeure', 'Форс-мажор'), ('plans', 'Поменялись планы'),
                                                    ('study', 'Учёба'), ('other', 'Другое')))
        self.assertEqual(rules.RESCHEDULE_NEEDS_COMMENT, ('other',))
        self.assertEqual(rules.reschedule_text('invited_office', datetime(2026, 11, 25, 10, 0), datetime(2026, 11, 27, 14, 30), 'plans'),
                         'Визит перенесён: 25.11 10:00 → 27.11 14:30 · Поменялись планы')
        self.assertEqual(rules.reschedule_text('invited_training', date(2026, 11, 23), date(2026, 11, 25), 'other', 'Сессия'),
                         'Обучение перенесено: 23.11 → 25.11 · Другое\nСессия')
        self.assertEqual(rules.reschedule_text('invited_office', None, datetime(2026, 11, 27, 9, 0), 'study'),
                         'Визит перенесён: — → 27.11 09:00 · Учёба')
        # Подписи ленты на экране — те же коды причин, что у сервера (экран берёт их из меты раздела).
        meta = (ROOT / 'src' / 'components' / 'hiring' / 'hiringMeta.js').read_text(encoding='utf-8')
        self.assertIn("export const SYSTEM_TOUCH_LABELS = { %s: 'Перенос даты' };" % rules.TOUCH_RESCHEDULED, meta)

    def test_channels_are_gone_from_code(self):
        """Понятия «Канал» больше нет (владелец 20.11.2026): ни ручек, ни меток в карточке, ни раздела настроек."""
        ui = ROOT / 'src' / 'components' / 'hiring'
        for path in [*ui.glob('*.jsx'), *ui.glob('*.js'), *(ROOT / 'hiring').glob('*.py')]:
            text = path.read_text(encoding='utf-8')
            if path.name == 'schema.py':      # таблица и колонка остаются следом слияния — только там
                continue
            self.assertNotRegex(text, r'channel_id|channel_name|channel_color|ChannelPill|/channels|list_channels', path.name)


class ChatsWindowInterfaceTests(unittest.TestCase):
    """23.11.2026: в «Чатах» — отбор кандидатов по статусам, и окно на весь экран без внешнего контейнера со своей
    прокруткой страницы. Поведение отбора проверяет tests/test_hiring_postgres.py (wa_chats с stages)."""

    @classmethod
    def setUpClass(cls):
        ui = ROOT / 'src' / 'components' / 'hiring'
        cls.view = (ui / 'HiringView.jsx').read_text(encoding='utf-8')
        cls.chats = (ui / 'ChatsTab.jsx').read_text(encoding='utf-8')
        cls.css = (ui / 'hiringChats.css').read_text(encoding='utf-8')

    def test_status_filter_goes_to_the_list_request(self):
        self.assertIn("stages: stages.length ? stages.join(',') : undefined", self.chats)
        self.assertIn('options={stageOptions}', self.chats)
        self.assertIn('placeholder="Все статусы"', self.chats)
        # Счётчик «Ждут ответа» на вкладке раздела — по всем чатам: отбор по статусам его не трогает.
        self.assertIn("if (params.mode === 'all' && !params.q && !params.stages) onWaiting?.(", self.chats)
        self.assertIn("{active === 'chats' && <ChatsTab {...shared}", self.view)   # boot — в shared

    def test_full_screen_only_for_chats_and_only_on_computer(self):
        self.assertIn("boot && current === 'chats' ? ' hiring-screen-chats' : ''", self.view)
        self.assertIn('body:not(.mobile-shell) .main-content:has(> .hiring-screen-chats) {', self.css)
        rules_text = self.css[self.css.index('body:not(.mobile-shell) .main-content'):]
        for prop in ('padding: 0;', 'overflow: hidden;', 'height: 100vh;'):
            self.assertIn(prop, rules_text[:rules_text.index('}')])
        # Телефон — как было: своя высота окна под оболочкой.
        self.assertIn('body.mobile-shell .hiring-chats {', self.css)
        self.assertNotIn('body.mobile-shell .main-content', self.css)


class GroupsAndBoardInterfaceTests(unittest.TestCase):
    """Владелец 18.11.2026: тренер ведёт группы с вместимостью, рекрутер при приглашении видит свободные места;
    полоса прокрутки под доской тонкая до наведения, «Взять в работу» — маленькая кнопка в углу карточки."""

    @classmethod
    def setUpClass(cls):
        ui = ROOT / 'src' / 'components' / 'hiring'
        cls.view = (ui / 'HiringView.jsx').read_text(encoding='utf-8')
        cls.board = (ui / 'BoardTab.jsx').read_text(encoding='utf-8')
        cls.sheet = (ui / 'CardSheet.jsx').read_text(encoding='utf-8')
        cls.settings = (ui / 'SettingsTab.jsx').read_text(encoding='utf-8')
        cls.groups = (ui / 'GroupsTab.jsx').read_text(encoding='utf-8')
        cls.meta = (ui / 'hiringMeta.js').read_text(encoding='utf-8')
        cls.styles = (ROOT / 'src' / 'styles.css').read_text(encoding='utf-8')

    def test_capacity_limit_is_one_for_server_and_interface(self):
        self.assertIn('export const GROUP_CAPACITY_MAX = %d;' % rules.GROUP_CAPACITY_MAX, self.meta)

    def test_groups_tab_for_trainer_and_lead(self):
        self.assertIn("import GroupsTab from './GroupsTab';", self.view)
        self.assertIn("{active === 'groups' && <GroupsTab {...shared} />}", self.view)
        # Набор и порядок вкладок — hiringTabs (поведение проверяет tests/hiring_meta.test.mjs).
        self.assertIn('const tabs = useMemo(() => hiringTabs(caps), [caps]);', self.view)
        # Группы — одним местом: из «Настроек» они ушли во вкладку.
        self.assertNotIn("value: 'groups'", self.settings)
        self.assertNotIn('GroupsSection', self.settings)
        # Тренера и отдел группе назначает руководитель — поля только у него (сервер проверяет то же); тело запроса —
        # groupRequestBody, шаг степпера — stepCapacity (оба проверены node-тестами).
        self.assertIn('{lead && (', self.groups)
        self.assertIn("api.get('/people/search')", self.groups)
        self.assertIn('if (!lead) return undefined;', self.groups)
        self.assertIn('const body = groupRequestBody(form, { lead });', self.groups)
        self.assertEqual(self.groups.count('onChange(stepCapacity(value, '), 2)
        self.assertIn('onClick={() => onOpen(group)}', self.groups)
        # «Новая группа» — только тем, кто вправе заводить группы; места перечитываются при изменении карточек.
        self.assertIn('{caps.create_groups && (', self.groups)
        self.assertIn('useEffect(() => { load(); }, [load, version]);', self.groups)
        self.assertIn("if (codeOf(error) === 'HIRING_CAPACITY_BELOW_TAKEN') onStale(group?.id);", self.groups)

    def test_card_is_read_only_for_reader(self):
        # Тренер в чужой карточке только читает: поля комментария нет (сервер его и не примет). «Обновить из статусов»
        # нет ни у кого: смены и обе категории система считает сама (SZOV-10).
        self.assertIn('const readOnly = Boolean(data?.read_only);', self.sheet)
        self.assertEqual(self.sheet.count('{!readOnly && ('), 1)
        self.assertNotIn('shifts/sync', self.sheet)
        self.assertNotIn('Обновить из статусов', self.sheet)
        self.assertNotIn("setMove('hired')", self.sheet)
        self.assertIn('placeholder="Комментарий в ленту"', self.sheet[self.sheet.index('{!readOnly && ('):])
        # Рекрутер в отборе доски — и тренеру: он читает всю воронку.
        self.assertIn('{(caps.lead || caps.read_all) && (', self.board)

    def test_recruiter_sees_free_seats_when_inviting(self):
        self.assertIn("api.get('/groups', { pick: 1 })", self.sheet)
        self.assertIn("if (!open || target !== 'invited_training') return;", self.sheet)
        self.assertIn("<GroupPicker groups={pickGroups} value={form.group_id} onChange={set('group_id')} />", self.sheet)
        self.assertIn('disabled={seats.full}', self.sheet)
        self.assertIn("if (codeOf(error) === 'HIRING_GROUP_FULL') {", self.sheet)
        self.assertIn('setPickGroups((prev) => markGroupFull(prev, payload.group_id));', self.sheet)
        self.assertIn("setForm((f) => ({ ...f, group_id: '' }));", self.sheet)
        # Места не загрузились — честная пометка и «Повторить»; на телефоне список без своей прокрутки.
        self.assertIn('{pickStale && (', self.sheet)
        self.assertIn('sm:max-h-[272px] sm:overflow-y-auto', self.sheet)
        self.assertNotIn("boot.dictionaries?.groups || []).filter((g) => g.status === 'active');\n    const kind", self.sheet)

    def test_board_and_card_wiring_for_sources(self):
        """Связки интерфейса правки 20.11.2026 (каналы → источники, «Отклики» группами); период — общий (24.11.2026)."""
        # Доска перезагружается по периоду, раскрытым группам и «Показать ещё»; на доске — ответ на последний запрос.
        self.assertIn("mine: scope === 'mine', query: debounced, filters, period, expanded, open: openGroups, openDays, expandedDays,",
                      self.board)
        self.assertIn('}, [api, scope, debounced, filters, period, expanded, openGroups, openDays, expandedDays, toast]);', self.board)
        self.assertIn('if (seq !== requestSeq.current) return;\n                setColumns(data.columns);', self.board)
        # Группы свёрнуты по умолчанию: раскрытые — список в браузере, карточки группы — по номерам из колонки.
        self.assertIn("const [openGroups, setOpenGroups] = useState(() => readList(OPEN_GROUPS_KEY).map(String));", self.board)
        self.assertIn('open={openGroups.includes(groupKey(group))} cards={cards}', self.board)
        self.assertIn('const cards = groupCards(group, cardsById);', self.board)
        self.assertIn('onExpand={() => onExpandGroup(group)}', self.board)
        self.assertIn('const expandGroup = (group) => setExpanded((prev) => withExpanded(prev, group));', self.board)
        # Внутри групп плашки источника нет — он в шапке группы.
        self.assertIn('{cards.map((card) => tile(card, { showSource: false, inGroup: true }))}', self.board)
        self.assertIn("{...(column.stage === 'responses' ? {", self.board)
        # Период — один на все колонки, в панели рядом с «Фильтрами» (владелец 24.11.2026), а не в шапке «Откликов».
        toolbar = self.board[self.board.index('<FiltersToggle open={filtersOpen}'):self.board.index('<FiltersRow open={filtersOpen}')]
        self.assertIn('<IosDateRangePicker portal from={period.from} to={period.to}', toolbar)
        self.assertNotIn('ResponsesPeriod', self.board)
        self.assertNotIn('onPeriod', self.board)
        # Карточка меняет источник полем source_id, у завершённой — только руководитель; окно источника — sourceRequestBody.
        self.assertIn('await api.patch(`/cards/${card.id}`, { source_id: sourceId });', self.sheet)
        self.assertIn('canEdit={isOwnerOrLead && (!card.is_closed || Boolean(caps.lead))}', self.sheet)
        self.assertIn('const body = sourceRequestBody(form, editing);', self.settings)
        self.assertNotIn("value: 'channels'", self.settings)

    def test_board_invited_days_wiring(self):
        """Владелец 24.11.2026: «Приглашен в офис / на обучение» по дням — раскрытые дни помнит браузер, они уходят с
        запросом доски; подписи — по дню сервера; перед прошедшими — «Прошедшие дни»; в дне визита — время визита."""
        self.assertIn("const OPEN_DAYS_KEY = 'hiring.board.openDays';", self.board)
        self.assertIn('const [openDays, setOpenDays] = useState(readOpenDays);', self.board)
        self.assertIn('if (data.today) setToday(data.today);', self.board)
        self.assertIn('} : DATED_STAGES.includes(column.stage) ? {', self.board)
        self.assertIn(': dated ? column.days.map(dayGroup) :', self.board)
        self.assertIn('{past && !isPastDay(column.days[index - 1]?.day, today) && (', self.board)
        self.assertIn('Прошедшие дни', self.board)
        self.assertIn("visit: column.stage === 'invited_office' ? visitTime(card) : ''", self.board)
        # «Показаны N из M» — только у колонок без групп: у дней свёрнутые группы — не «недогрузка».
        self.assertIn('{!grouped && !dated && column.total > column.cards.length && (', self.board)

    def test_reschedule_wiring(self):
        """Владелец 24.11.2026: дату визита и обучения переносят с причиной — кнопкой в карточке и значком на карточке доски
        (она открывается сразу с окном переноса); правка карточки дату визита больше не меняет."""
        self.assertIn("await api.post(`/cards/${card.id}/reschedule`, body);", self.sheet)
        self.assertIn('const problem = rescheduleProblem(form, office, reasons, current);', self.sheet)
        self.assertIn('{reschedulable && (\n                                            <button type="button" onClick={() => setRescheduling(true)}',
                      self.sheet)
        self.assertIn('<RescheduleDialog open={rescheduling}', self.sheet)
        self.assertIn("if (target === 'on_training') initial.date = trainingDay(card) || todayIso();", self.sheet)
        edit = self.sheet[self.sheet.index('const EditDialog = '):self.sheet.index('const ReasonChoice = ')]
        self.assertNotIn('office_visit_at', edit)
        self.assertIn('const reschedule = (card) => onOpenCard(card.id, { reschedule: true });', self.board)
        self.assertIn('reschedulable={canMoveDate(card)}', self.board)
        self.assertIn('setOpenReschedule(Boolean(options?.reschedule));', self.view)
        self.assertIn('initialReschedule={openReschedule}', self.view)
        # Разбор 24.11.2026: опоздавший ответ по прежней карточке отбрасывается, а перенос и звонок с доски ждут данных именно
        # открытой карточки — иначе они ушли бы в чужую.
        self.assertIn('if (shownCardId.current !== cardId) return;', self.sheet)
        self.assertIn('const loadedHere = Boolean(data?.card) && Number(data.card.id) === Number(cardId);', self.sheet)
        self.assertIn('if (!initialReschedule || !loadedHere) return;', self.sheet)
        self.assertIn('if (initialCall && loadedHere) { onCallConsumed?.(); startCall(); }', self.sheet)

    def test_chat_handled_button_under_last_candidate_message(self):
        """Владелец 20.11.2026: «Ответ не нужен» — снизу под последним сообщением кандидата, как в «Чатах ОП», а не в
        шапке чата."""
        chats = (ROOT / 'src' / 'components' / 'hiring' / 'ChatsTab.jsx').read_text(encoding='utf-8')
        self.assertEqual(chats.count('Ответ не нужен\n'), 1)
        self.assertIn('{data.can_handle && row.id === lastIncoming && (', chats)
        self.assertIn('const lastIncoming = useMemo(() => lastIncomingId(rows), [rows]);', chats)
        header = chats[chats.index('<div className="flex flex-wrap items-center gap-2">'):chats.index('<div className="relative min-h-0 flex-1">')]
        self.assertNotIn('handled', header)

    def test_no_response_archive_column_and_back_to_work(self):
        """Владелец 20.11.2026: «Не отвечает звонок/чат» — последняя колонка, «по умолчанию завёрнутый и серый»; из карточки —
        снова в работу закреплённым рекрутером, без повторной активации."""
        self.assertIn("const ARCHIVE_OPEN_KEY = 'hiring.board.openArchive';", self.board)
        self.assertIn('collapsed={columnCollapsed(column.stage, collapsed, openedArchive)}', self.board)
        self.assertIn("archive ? 'bg-slate-100/50' : 'bg-slate-100/80'", self.board)
        self.assertIn("archive ? 'opacity-75 transition-opacity hover:opacity-100' : ''", self.board)
        self.assertIn("const backToWork = target === 'in_work' && card?.stage === ARCHIVE_STAGE;", self.sheet)
        self.assertIn("if (target === 'in_work' && !backToWork) { payload.reactivate = true;", self.sheet)
        self.assertIn("{target === 'in_work' && !backToWork && (", self.sheet)
        self.assertIn("return card.stage === ARCHIVE_STAGE ? isOwnerOrLead : actor.includes('lead');", self.sheet)

    def test_card_whatsapp_opens_portal_chat(self):
        """Владелец 20.11.2026: «при нажатии на ватсап … полноценный чат, мб просто переход на чаты» — кнопка карточки и
        значок рабочего стола открывают окно «Чаты» с чатом карточки, а не WhatsApp Web; касание пишет отправка."""
        ui = ROOT / 'src' / 'components' / 'hiring'
        desk = (ui / 'DeskTab.jsx').read_text(encoding='utf-8')
        chats = (ui / 'ChatsTab.jsx').read_text(encoding='utf-8')
        for text in (self.sheet, desk):
            self.assertNotIn('window.open(card.whatsapp_link', text)
            self.assertNotIn("{ kind: 'whatsapp' }", text)
        self.assertIn("const whatsapp = () => { if (card?.id) onOpenChat?.(card.id); };", self.sheet)
        self.assertIn("...(whatsappCount && !onOpenChat ? [{ value: 'whatsapp'", self.sheet)
        self.assertIn('const whatsapp = onOpenChat ? (card) => onOpenChat(card.id) : null;', desk)
        self.assertIn("const onOpenChat = tabs.includes('chats') ? openChat : undefined;", self.view)
        self.assertIn('focus={chatFocus} onFocusConsumed={focusConsumed}', self.view)
        self.assertIn('onOpenChat={onOpenChat}', self.view)
        self.assertIn('api.post(`/cards/${focus.cardId}/chat`)', chats)
        self.assertIn('.then(({ data }) => { if (data?.key) setSelectedKey(data.key); })', chats)

    def test_chats_live_channel_wiring(self):
        """Владелец 20.11.2026: «чаты работают не так быстро и не в реальном времени» — окно держит живой канал
        (/api/hiring/wa/stream), по тычку перечитывает список и открытый чат; опрос — страховка, реже при живом канале."""
        chats = (ROOT / 'src' / 'components' / 'hiring' / 'ChatsTab.jsx').read_text(encoding='utf-8')
        self.assertIn("import { runSseStream } from '../common/sseClient';", chats)
        self.assertIn("url: api.streamUrl('/wa/stream'),", chats)
        self.assertIn("onEvent: (event, payload) => { if (event === 'chats') bump(payload); },", chats)
        self.assertIn('if (now && !wasLive) bump({ all: 1 });', chats)
        self.assertIn("const onVisibility = () => (visible() ? open() : close());", chats)
        self.assertEqual(chats.count('liveRef.current ? LIVE_POLL_MS :'), 2)
        self.assertIn('if (!pulseTouches(pulse, cardId) || pulseTimer.current) return;', chats)
        self.assertIn('live={live} pulse={pulse}', chats)
        self.assertIn('streamUrl: url,', self.view)
        self.assertIn("headers: () => (headersRef.current ? headersRef.current() : {}),", self.view)

    def test_board_scrollbar_and_take_button(self):
        self.assertIn('className="board-scroll -mx-3 overflow-x-auto', self.board)
        block = self.styles[self.styles.index('.board-scroll::-webkit-scrollbar {'):]
        block = block[:block.index('@supports not selector(::-webkit-scrollbar)')]
        self.assertNotIn('scrollbar-width', block)      # иначе Chrome рисует штатную полосу ~11 px
        self.assertIn('height: 13px;', block)
        self.assertIn('border: 5px solid transparent;', block)   # 13 − 2×5 = 3 px до наведения
        self.assertIn('.board-scroll::-webkit-scrollbar-thumb:hover', block)
        self.assertIn('border-width: 2px;', block)               # 13 − 2×2 = 9 px под курсором
        # «Темнее над доской» Chromium не перерисовывает и залипает — правила нет ни в одной теме.
        theme = (ROOT / 'src' / 'theme-dark.css').read_text(encoding='utf-8')
        self.assertNotIn('.board-scroll:hover::-webkit-scrollbar-thumb {', self.styles + theme)
        # Кнопка пула — маленькая, в нижней строке, только у карточек пула и только тому, кто берёт в работу; щелчок по
        # ней карточку не открывает, Enter на ней — тоже.
        self.assertIn("const TAKE_BTN = 'hiring-take-btn';", self.board)
        self.assertIn('{canTake && inPool ? (', self.board)
        self.assertIn('<button type="button" onClick={(event) => { event.stopPropagation(); onTake(card); }}\n'
                      '                            className={TAKE_BTN}>', self.board)
        self.assertIn("if (event.key === 'Enter' && event.target === event.currentTarget) onOpen(card.id);", self.board)
        self.assertNotIn('absolute inset-x-3 bottom-3', self.board)
        self.assertNotIn('h-8 w-full items-center justify-center rounded-xl bg-blue-600', self.board)
        # С мышью кнопка проявляется с задержкой и до этого невидима и щелчков не берёт (visibility), на сенсорном видна всегда.
        take = self.styles[self.styles.index('.hiring-take-btn {'):]
        take = take[:take.index('\n}\n', take.index('.group:focus-within .hiring-take-btn')) + 3]
        self.assertIn('@media (hover: hover) {\n    .hiring-take-btn {\n        visibility: hidden;\n        opacity: 0;', take)
        self.assertIn('.group:hover .hiring-take-btn,\n    .group:focus-within .hiring-take-btn {\n        visibility: visible;', take)
        self.assertIn('opacity 0.12s ease 0.15s, visibility 0s linear 0.15s;', take)
        self.assertIn('height: 24px;', take)
        self.assertIn('html[data-otp-theme="dark"] .hiring-take-btn:hover {', theme)


class TransitionTests(unittest.TestCase):
    def check(self, card, target, kinds, has_touch=True, payload=None):
        return rules.check_transition(card, target, set(kinds), has_touch=has_touch, payload=payload or {})

    def test_take_requires_free_card(self):
        rule = self.check({'stage': 'contacts', 'recruiter_id': None}, 'in_work', ['recruiter'])
        self.assertTrue(rule.get('take'))
        with self.assertRaises(rules.TransitionError) as caught:
            self.check({'stage': 'contacts', 'recruiter_id': 5}, 'in_work', ['recruiter'])
        self.assertEqual(caught.exception.code, 'HIRING_ALREADY_TAKEN')

    def test_in_work_needs_a_touch_and_fields(self):
        card = {'stage': 'in_work', 'recruiter_id': 7}
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'invited_office', ['owner'], has_touch=False)
        self.assertEqual(caught.exception.code, 'HIRING_NO_TOUCH')
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'invited_office', ['owner'])
        self.assertEqual(caught.exception.code, 'HIRING_NEED_OFFICE_DATE')
        self.check(card, 'invited_office', ['owner'], payload={'office_visit_at': '2026-11-01T10:00'})
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'refused', ['owner'], payload={'initiator': 'ours'})
        self.assertEqual(caught.exception.code, 'HIRING_NEED_REASON')

    def test_refusal_needs_no_touch_but_still_a_reason(self):
        """Владелец 24.11.2026: отказ — без обязательного звонка или WhatsApp перед ним, причины обязательны."""
        card = {'stage': 'in_work', 'recruiter_id': 7}
        self.check(card, 'refused', ['owner'], has_touch=False, payload={'initiator': 'candidate', 'reason_id': 3})
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'refused', ['owner'], has_touch=False, payload={'reason_id': 3})
        self.assertEqual(caught.exception.code, 'HIRING_NEED_INITIATOR')
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'refused', ['owner'], has_touch=False, payload={'initiator': 'ours'})
        self.assertEqual(caught.exception.code, 'HIRING_NEED_REASON')
        # Приглашения связь по-прежнему требуют.
        for target, payload in (('invited_office', {'office_visit_at': '2026-11-01T10:00'}), ('invited_training', {'group_id': 1})):
            with self.assertRaises(rules.TransitionError) as caught:
                self.check(card, target, ['owner'], has_touch=False, payload=payload)
            self.assertEqual(caught.exception.code, 'HIRING_NO_TOUCH')
        self.assertEqual([pair for pair, rule in rules.TRANSITIONS.items() if pair[1] == 'refused' and rule.get('needs_touch')], [])

    def test_office_to_training_only_when_came(self):
        card = {'stage': 'invited_office', 'recruiter_id': 7, 'substage': 'no_show'}
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'invited_training', ['owner'], payload={'group_id': 1})
        self.assertEqual(caught.exception.code, 'HIRING_NOT_CAME')
        self.check(dict(card, substage='came'), 'invited_training', ['owner'], payload={'group_id': 1})
        # Не пришёл — отказ с причиной можно оформить из любого подстатуса.
        self.check(card, 'refused', ['owner'], payload={'initiator': 'candidate', 'reason_id': 3})

    def test_who_may_do_what(self):
        # «Отработал 7 дней» не переход ни для кого — ни для руководителя отдела с семью сменами (SZOV-10).
        for kinds in (['dept_head'], ['owner', 'lead'], ['dept_head', 'owner', 'lead', 'trainer']):
            with self.assertRaises(rules.TransitionError) as caught:
                self.check({'stage': 'on_line'}, 'hired', kinds, payload={'shifts_done': 12})
            self.assertEqual(caught.exception.code, 'HIRING_BAD_TRANSITION')
        with self.assertRaises(rules.TransitionError) as caught:
            self.check({'stage': 'on_line'}, 'dropped', ['trainer'], payload={'initiator': 'self', 'reason_id': 1, 'day': 2})
        self.assertEqual(caught.exception.code, 'HIRING_FORBIDDEN')
        self.assertIn('руководитель отдела', caught.exception.message)
        self.check({'stage': 'on_line'}, 'dropped', ['dept_head'], payload={'initiator': 'self', 'reason_id': 1, 'day': 2})
        self.check({'stage': 'invited_training'}, 'on_training', ['trainer'])
        with self.assertRaises(rules.TransitionError):
            self.check({'stage': 'refused'}, 'in_work', ['owner'])
        self.check({'stage': 'dropped'}, 'in_work', ['lead'])

    def test_drop_needs_initiator_reason_day(self):
        card = {'stage': 'on_training'}
        with self.assertRaises(rules.TransitionError) as caught:
            self.check(card, 'dropped', ['trainer'], payload={'initiator': 'self', 'reason_id': 1})
        self.assertEqual(caught.exception.code, 'HIRING_NEED_DAY')
        self.check(card, 'dropped', ['trainer'], payload={'initiator': 'we', 'reason_id': 1, 'day': 3})
        self.assertEqual(rules.drop_stage_for('on_line'), 'line')
        self.assertEqual(rules.drop_stage_for('on_training'), 'training')

    def test_group_seats(self):
        # Место занимает приглашённый и не отпавший: отказ до обучения и отсев место освобождают.
        self.assertEqual(rules.SEAT_STAGES, ('invited_training', 'on_training', 'on_line', 'hired'))
        self.assertEqual(rules.seats_free(12, 8), 4)
        self.assertEqual(rules.seats_free(3, 3), 0)
        self.assertEqual(rules.seats_free(3, 5), 0)
        self.assertEqual(rules.seats_free(10, None), 10)
        self.assertIsNone(rules.seats_free(None, 4))    # группа до 18.11.2026: вместимость не задана
        # До приглашения на обучение карточка — работа рекрутера: тренер её не видит, даже если она помнит группу.
        self.assertEqual(rules.TRAINER_HIDDEN_STAGES, ('contacts', 'responses', 'in_work', 'invited_office', 'no_response'))
        self.assertFalse(set(rules.TRAINER_HIDDEN_STAGES) & set(rules.SEAT_STAGES))

    def test_parse_capacity(self):
        for raw, expected in ((12, 12), ('12', 12), (' 7 ', 7), (12.0, 12), (1, 1), (500, 500)):
            self.assertEqual(rules.parse_capacity(raw), expected, raw)
        for raw in (None, '', 0, -3, 501, '1e3', 'abc', 2.5, True, False, [], {}):
            self.assertIsNone(rules.parse_capacity(raw), raw)

    def test_actor_kinds(self):
        caps = {'lead': False, 'recruiter': True, 'headed_ids': [5], 'super_admin': False}
        card = {'recruiter_id': 7, 'group_id': 3, 'department_id': 5}
        self.assertEqual(rules.actor_kinds(caps, card, 7, trainer_group_ids=[3]), {'recruiter', 'owner', 'trainer', 'dept_head'})
        self.assertEqual(rules.actor_kinds(caps, card, 8, trainer_group_ids=[]), {'recruiter', 'dept_head'})
        # Выдачу сняли — «владельцем» своей старой карточки человек больше не считается.
        self.assertEqual(rules.actor_kinds({'lead': False, 'recruiter': False, 'headed_ids': []}, card, 7, trainer_group_ids=[3]), {'trainer'})
        self.assertIn('lead', rules.actor_kinds({'lead': True, 'headed_ids': []}, card, 8))
        self.assertIn('dept_head', rules.actor_kinds({'super_admin': True, 'headed_ids': []}, card, 8))


class AccessTests(unittest.TestCase):
    def test_lead_circle(self):
        self.assertTrue(access.is_lead(ctx(role='super_admin')))
        self.assertTrue(access.is_lead(ctx(headed_codes=['hr'])))
        self.assertTrue(access.is_lead(ctx(department_code='hr', role='hr_manager')))
        self.assertTrue(access.is_lead(ctx(grant='lead')))
        self.assertFalse(access.is_lead(ctx(grant='staff')))

    def test_recruiters_and_remote_phones(self):
        remote = ctx(grant='remote')
        self.assertTrue(access.can_open_section(remote))
        self.assertTrue(access.is_remote(remote))
        self.assertFalse(access.sees_phone(remote, {'recruiter_id': None}))
        self.assertFalse(access.sees_phone(remote, {'recruiter_id': 8}))
        self.assertTrue(access.sees_phone(remote, {'recruiter_id': 7}))
        self.assertTrue(access.sees_phone(ctx(grant='staff'), {'recruiter_id': None}))
        self.assertFalse(access.can_upload(remote))
        self.assertFalse(access.can_manage(ctx(grant='staff')))

    def test_trainer_and_dept_head(self):
        # Тренеру раздел открыт ролью, и без группы (владелец 18.11.2026): группы он заводит сам.
        self.assertTrue(access.can_open_section(ctx(role='trainer')))
        self.assertTrue(access.can_open_section(ctx(role=' Trainer ')))
        self.assertTrue(access.can_open_section(ctx(role='trainer', trainer_group_ids=[4])))
        self.assertFalse(access.can_open_section(ctx(role='trainer', status='fired')))
        # Тренер группы без роли «тренер» — тоже тренер, пока группа действует.
        self.assertTrue(access.is_trainer(ctx(role='operator', trainer_group_ids=[4])))
        self.assertFalse(access.is_trainer(ctx(role='trainee')))
        self.assertFalse(access.can_open_section(ctx(role='admin', headed_ids=[9], vacancy_department_ids=[3])))
        self.assertTrue(access.can_open_section(ctx(role='admin', headed_ids=[9], vacancy_department_ids=[9, 3])))
        self.assertFalse(access.can_see_pool(ctx(role='trainer', trainer_group_ids=[4])))
        trainer = access.capabilities(ctx(role='trainer'))
        self.assertTrue(trainer['trainer'] and trainer['groups'])
        self.assertFalse(trainer['lead'] or trainer['manage'] or trainer['recruiter'] or trainer['pool'] or trainer['take'])
        self.assertEqual(trainer['trainer_group_ids'], [])
        self.assertEqual(access.scope_label(ctx(role='trainer')), 'Тренер')

    def test_who_edits_groups(self):
        trainer = ctx(role='trainer')
        self.assertTrue(access.can_edit_group(trainer, {'trainer_id': 7}))
        self.assertFalse(access.can_edit_group(trainer, {'trainer_id': 8}))
        self.assertFalse(access.can_edit_group(trainer, {'trainer_id': None}))
        self.assertTrue(access.can_edit_group(ctx(grant='lead'), {'trainer_id': 8}))
        self.assertTrue(access.can_edit_group(ctx(role='super_admin'), {'trainer_id': None}))
        # Рекрутер и руководитель отдела групп не ведут; свою группу правит её тренер, кем бы он ни был по роли.
        self.assertFalse(access.can_manage_groups(ctx(grant='staff')))
        self.assertFalse(access.can_edit_group(ctx(grant='staff'), {'trainer_id': 8}))
        self.assertTrue(access.can_edit_group(ctx(grant='staff'), {'trainer_id': 7}))
        self.assertFalse(access.can_manage_groups(ctx(role='admin', headed_ids=[9], vacancy_department_ids=[9])))
        self.assertTrue(access.capabilities(ctx(grant='lead'))['groups'])

    def test_trainer_reads_the_whole_funnel(self):
        # Владелец 19.11.2026: тренеру — все кандидаты для чтения. Только роли «тренер»: назначенный тренер группы
        # без роли, рекрутер и глава отдела читают, как раньше, свой круг.
        self.assertTrue(access.can_read_all(ctx(role='trainer')))
        self.assertTrue(access.capabilities(ctx(role=' Trainer '))['read_all'])
        for other in (ctx(role='operator', trainer_group_ids=[4]), ctx(grant='staff'), ctx(grant='lead'),
                      ctx(role='admin', headed_ids=[9], vacancy_department_ids=[9])):
            self.assertFalse(access.capabilities(other)['read_all'], other)
        # Читать — не действовать: брать в работу, добавлять отклики и вести справочники тренер не может.
        caps = access.capabilities(ctx(role='trainer'))
        self.assertFalse(caps['take'] or caps['pool'] or caps['add_response'] or caps['manage'] or caps['recruiter'])

    def test_who_creates_groups(self):
        # Новые группы — руководитель и роль «тренер». Тренер группы без роли (его назначил руководитель) свою
        # группу ведёт, но новыми группами продлевать себе раздел не может (разбор 18.11.2026).
        self.assertTrue(access.can_create_groups(ctx(role='trainer')))
        self.assertTrue(access.can_create_groups(ctx(grant='lead')))
        self.assertTrue(access.can_create_groups(ctx(role='super_admin')))
        appointed = ctx(role='operator', trainer_group_ids=[4])
        self.assertTrue(access.can_manage_groups(appointed) and not access.can_create_groups(appointed))
        self.assertFalse(access.can_create_groups(ctx(grant='staff')))
        caps = access.capabilities(appointed)
        self.assertTrue(caps['groups'] and not caps['create_groups'])
        self.assertTrue(access.capabilities(ctx(role='trainer'))['create_groups'])

    def test_fired_sees_nothing(self):
        self.assertFalse(access.can_open_section(ctx(role='super_admin', status='fired')))
        self.assertFalse(access.can_open_section(ctx(grant='staff', status='dismissal')))

    def test_stranger(self):
        self.assertFalse(access.can_open_section(ctx()))
        caps = access.capabilities(ctx(grant='staff'))
        self.assertTrue(caps['recruiter'] and caps['take'] and caps['add_response'])
        self.assertFalse(caps['lead'] or caps['manage'] or caps['reports'])
        self.assertEqual(access.scope_label(ctx(grant='remote')), 'Удалённый рекрутер')


class UploadTests(unittest.TestCase):
    def test_csv_with_headers_and_duplicates(self):
        raw = 'ФИО;Телефон;Город;Комментарий\nИванов Иван;8 705 123 45 67;Алматы;ок\nПетров;+7 701 000 00 00;;\nСидоров;77051234567;Астана;повтор\nБез номера;;;\n'.encode('cp1251')
        contacts, errors, total = upload.parse(raw, 'база.csv')
        self.assertEqual(total, 4)
        self.assertEqual([c['phone'] for c in contacts], ['+77051234567', '+77010000000'])
        self.assertEqual(contacts[0]['full_name'], 'Иванов Иван')
        self.assertEqual(contacts[0]['city'], 'Алматы')
        self.assertEqual([e['error'] for e in errors], ['Повтор номера в файле', 'Пустой или неразборный номер'])
        self.assertEqual([e['row'] for e in errors], [4, 5])

    def test_xlsx_without_headers_uses_tz_order(self):
        book = Workbook()
        sheet = book.active
        sheet.append(['Асель', 7051112233, 'Шымкент', None])
        sheet.append([None, None, None, None])
        sheet.append(['Данияр', '8 (777) 555 66 77', '', 'вечером'])
        stream = io.BytesIO()
        book.save(stream)
        contacts, errors, total = upload.parse(stream.getvalue(), 'контакты.xlsx')
        self.assertEqual(total, 2)
        self.assertEqual(errors, [])
        self.assertEqual(contacts[0], {'row': 1, 'full_name': 'Асель', 'phone': '+77051112233', 'city': 'Шымкент', 'comment': None})
        self.assertEqual(contacts[1]['phone'], '+77775556677')
        self.assertEqual(contacts[1]['comment'], 'вечером')

    def test_wrong_format(self):
        with self.assertRaises(upload.UploadError):
            upload.parse(b'x', 'файл.pdf')


class HHTests(unittest.TestCase):
    """Отклики hh.kz от моста на шлюзе (hiring/hh.py): разбор темы, настройки руководителя, вид для «Настроек»."""

    RAW = {'topic_id': '5735665687', 'vacancy_id': '139445254', 'vacancy_name': '  Супервайзер   контакт-центра ',
           'responded_at': '2026-11-23T12:30:00.370925+03:00', 'last_name': 'Иванов', 'first_name': 'Иван',
           'middle_name': '', 'phone': '+7 (701) 234-56-78', 'city': 'Алматы',
           'resume_hash': '9F2c4a1b0d3e5f6a7b8c9d0e1f2a3b4c5d6e7f'}

    def test_clean_item(self):
        item = hh.clean_item(self.RAW)
        self.assertEqual(item['topic_id'], 5735665687)
        self.assertEqual(item['vacancy_id'], '139445254')
        self.assertEqual(item['vacancy_name'], 'Супервайзер контакт-центра')
        # Время hh — московское с поясом; у портала — наивное время Алматы (+2 часа).
        self.assertEqual(item['responded_at'], datetime(2026, 11, 23, 14, 30, 0, 370925))
        self.assertEqual(item['full_name'], 'Иванов Иван')
        self.assertEqual(rules.normalize_phone(item['phone']), '+77012345678')
        self.assertEqual(item['resume_url'], 'https://hh.kz/resume/9f2c4a1b0d3e5f6a7b8c9d0e1f2a3b4c5d6e7f'
                                             '?vacancyId=139445254&t=5735665687')

    def test_clean_resume(self):
        """Резюме от моста (владелец 23.11.2026: «все резюме и письмо человека который обратился отобразились сразу в
        карточке»): подписи вместо кодов hh, длины в потолках, лишних ключей (контактов) нет."""
        raw = {'title': ' Оператор  call-центра ', 'salary': {'amount': 250000, 'currency': 'KZT'}, 'age': 27,
               'gender': 'female', 'area': 'Алматы', 'metro': 'Алатау', 'citizenship': ['Казахстан'],
               'relocation': 'no_relocation', 'business_trips': 'never', 'travel_time': 'any',
               'employment': ['full', 'part'], 'schedule': ['shift', 'новый_код'], 'formats': ['ON_SITE'],
               'experience_total': {'years': 0, 'months': 70},
               'experience': [{'company': 'ТОО Ромашка', 'area': 'Алматы', 'position': 'Оператор', 'start': '2020-01-01',
                               'end': None, 'years': 5, 'months': 10, 'description': 'Звонки\r\n\r\n\r\nЧаты'},
                              {'company': None, 'position': None}],
               'education_level': 'higher', 'education': [{'name': 'КазНУ', 'year': 2019}], 'courses': [{'name': ''}],
               'languages': [{'title': 'Русский', 'degree': 'l1'}, {'title': 'Английский', 'degree': 'b1'}],
               'key_skills': ['Продажи', 'CRM'], 'about': 'я' * 7000, 'driver_license': ['B'], 'medical_book': True,
               'email': 'candidate@example.com', 'phone': '+7 701 000 00 00'}
        resume = hh.clean_resume(raw)
        self.assertEqual(resume['title'], 'Оператор call-центра')
        self.assertEqual(resume['salary'], {'amount': 250000, 'currency': 'KZT', 'sign': '₸'})
        self.assertEqual((resume['age'], resume['gender'], resume['area'], resume['metro'], resume['citizenship']),
                         (27, 'женщина', 'Алматы', 'Алатау', ['Казахстан']))
        self.assertEqual((resume['relocation'], resume['business_trips'], resume['travel_time']),
                         ('не готов к переезду', 'не готов к командировкам', 'не имеет значения'))
        self.assertEqual(resume['employment'], ['полная занятость', 'частичная занятость'])
        self.assertEqual(resume['schedule'], ['сменный график', 'новый_код'])         # незнакомый код hh — как есть
        self.assertEqual(resume['formats'], ['на месте работодателя'])
        self.assertEqual(resume['experience_total'], {'years': 5, 'months': 10})        # 70 месяцев — 5 лет 10 мес.
        self.assertEqual(resume['experience'], [{'company': 'ТОО Ромашка', 'area': 'Алматы', 'position': 'Оператор',
                                                 'start': '2020-01-01', 'end': None, 'span': {'years': 5, 'months': 10},
                                                 'description': 'Звонки\n\nЧаты'}])
        self.assertEqual((resume['education_level'], resume['education'], resume['courses']),
                         ('высшее', [{'name': 'КазНУ', 'organization': None, 'result': None, 'year': 2019}], []))
        self.assertEqual(resume['languages'], [{'title': 'Русский', 'level': 'родной'},
                                               {'title': 'Английский', 'level': 'B1 — средний'}])
        self.assertEqual((resume['key_skills'], len(resume['about']), resume['driver_license'], resume['medical_book']),
                         (['Продажи', 'CRM'], 6000, ['B'], True))
        self.assertFalse({'email', 'phone'} & set(resume))                               # контактов в резюме нет
        self.assertIsNone(hh.clean_resume(None))
        self.assertIsNone(hh.clean_resume({'email': 'x@example.com'}))                   # одни контакты — резюме нет
        item = hh.clean_item(dict(self.RAW, resume=raw, letter='  Здравствуйте!\r\n\r\n\r\nХочу к вам.  '))
        self.assertEqual((item['letter'], item['resume']['title']), ('Здравствуйте!\n\nХочу к вам.', 'Оператор call-центра'))
        self.assertEqual((hh.clean_item(self.RAW)['resume'], hh.clean_item(self.RAW)['letter']), (None, None))

    def test_clean_item_refuses_what_it_cannot_place(self):
        for broken in ({}, {'topic_id': 'x1', 'vacancy_id': '1', 'responded_at': '2026-11-23T10:00:00+03:00'},
                       dict(self.RAW, vacancy_id=None), dict(self.RAW, responded_at='вчера'), 'тема'):
            self.assertIsNone(hh.clean_item(broken))
        # Хеш резюме — только шестнадцатеричный: иначе ссылки нет, отклик всё равно принимается.
        self.assertIsNone(hh.clean_item(dict(self.RAW, resume_hash='../../evil'))['resume_url'])
        self.assertIsNone(hh.resume_url('', '1', '2'))

    def test_parse_moment(self):
        self.assertEqual(hh.parse_moment('2026-11-23T09:28:27+0300'), datetime(2026, 11, 23, 11, 28, 27))
        self.assertEqual(hh.parse_moment('2026-11-23T09:28:27Z'), datetime(2026, 11, 23, 14, 28, 27))
        self.assertEqual(hh.parse_moment('2026-11-23T12:08:00'), datetime(2026, 11, 23, 12, 8))
        self.assertIsNone(hh.parse_moment(None))
        self.assertIsNone(hh.parse_moment('завтра'))

    def test_session_and_vacancies(self):
        self.assertEqual(hh.clean_session({'session': 'ok'}), ('ok', None))
        self.assertEqual(hh.clean_session({'session': 'login_required', 'detail': ' редирект  на вход '}),
                         ('login_required', 'редирект на вход'))
        self.assertEqual(hh.clean_session({'session': 'что-то'})[0], 'error')
        self.assertIsNone(hh.clean_vacancies(None))
        self.assertEqual(hh.clean_vacancies([{'id': '1', 'name': 'A', 'area': 'Алматы', 'area_id': 160, 'responses': 5},
                                             {'id': '1', 'name': 'дубль'}, {'id': 'x', 'name': 'B'},
                                             {'id': '2', 'name': ''},
                                             {'id': '3', 'name': 'C', 'area_id': '1' * 12, 'responses': -1}]),
                         [{'id': '1', 'name': 'A', 'area': 'Алматы', 'area_id': 160, 'responses': 5},
                          {'id': '3', 'name': 'C', 'area': None, 'area_id': None, 'responses': None}])

    def test_settings_rules(self):
        settings = {'since': '2026-11-23T12:08:00',
                    'vacancies': {'139508734': {'on': False}, '139079696': {'on': True, 'vacancy_id': 2}}}
        self.assertEqual(hh.since(settings), datetime(2026, 11, 23, 12, 8))
        self.assertEqual(hh.since({}), rules.HH_SINCE_DEFAULT)
        self.assertEqual(hh.vacancy_rule(settings, '139508734'), {'on': False, 'vacancy_id': None, 'since': None})
        self.assertEqual(hh.vacancy_rule(settings, 139079696), {'on': True, 'vacancy_id': 2, 'since': None})
        # Новая вакансия hh — переносится, без вакансии iCORE (владелец 23.11.2026).
        self.assertEqual(hh.vacancy_rule(settings, '1'), {'on': True, 'vacancy_id': None, 'since': None})
        self.assertEqual(hh.off_ids(settings), ['139508734'])
        # Решения владельца 23.11.2026: только новые с этой минуты, все вакансии, кроме «Младший маркетолог».
        self.assertEqual(rules.HH_SINCE_DEFAULT, datetime(2026, 11, 23, 12, 8))
        self.assertEqual(rules.HH_OFF_DEFAULT, ('139508734',))
        # «Добавь в настройки возможность выбрать какие вакансии отслеживать»: вакансию включили позже — её отклики с
        # минуты включения (то же «только новые с этой минуты», что у всего моста).
        later = {'since': '2026-11-23T12:08:00', 'vacancies': {'1': {'on': True, 'since': '2026-11-24T10:15:00'},
                                                               '2': {'on': True, 'since': '2026-11-01T00:00:00'},
                                                               '3': {'on': False, 'since': '2026-11-24T10:15:00'}}}
        self.assertEqual(hh.vacancy_rule(later, '1')['since'], datetime(2026, 11, 24, 10, 15))
        self.assertIsNone(hh.vacancy_rule(later, '2')['since'])              # раньше общей отсечки — действует общая
        self.assertIsNone(hh.vacancy_rule(later, '3')['since'])              # выключена — не в счёт
        self.assertEqual(hh.vacancy_since(later, '1'), datetime(2026, 11, 24, 10, 15))
        self.assertEqual(hh.vacancy_since(later, '2'), datetime(2026, 11, 23, 12, 8))
        self.assertEqual(hh.vacancy_since(later, '9'), datetime(2026, 11, 23, 12, 8))   # новая вакансия — общая
        self.assertEqual(hh.since_by_vacancy(later), {'1': datetime(2026, 11, 24, 10, 15)})
        merged = hh.merge_vacancy_settings(
            {'1': {'on': False}, '2': {'on': True, 'since': '2026-11-24T10:15:00'}, '3': {'on': True}},
            {'1': {'on': True}, '2': {'on': True, 'vacancy_id': 7}, '3': {'on': False}, '4': {'on': True}},
            datetime(2026, 11, 25, 9, 30))
        self.assertEqual(merged, {'1': {'on': True, 'since': '2026-11-25T09:30:00'},          # включили — с этой минуты
                                  '2': {'on': True, 'vacancy_id': 7, 'since': '2026-11-24T10:15:00'},   # момент цел
                                  '3': {'on': False},                                          # выключили — момента нет
                                  '4': {'on': True}})                                          # новая — общая отсечка
        self.assertEqual(hh.merge_vacancy_settings(None, {'1': {'on': True}}, datetime(2026, 11, 25)), {'1': {'on': True}})

    def test_bridge_pace(self):
        """Владелец 23.11.2026: «максимально безопасно… чтобы не получить банов», затем «добавь случайный тайминг в
        опросе лидов а ночью от 45 мин до 2 часов» — пауза случайная: днём 2–5 минут, ночью 45 минут – 2 часа, но не
        дальше начала дня; «не на связи» — не раньше полутора пауз с пятью минутами запаса (и не раньше 20 минут)."""
        low, high = SimpleNamespace(randint=lambda a, _b: a), SimpleNamespace(randint=lambda _a, b: b)
        self.assertEqual([rules.hh_poll_seconds(datetime(2026, 11, 23, 8, 0), low),
                          rules.hh_poll_seconds(datetime(2026, 11, 23, 20, 59), high)], [120, 300])
        self.assertEqual([rules.hh_poll_seconds(datetime(2026, 11, 23, 21, 0), r) for r in (low, high)], [2700, 7200])
        self.assertEqual(rules.hh_poll_seconds(datetime(2026, 11, 24, 5, 0), low), 2700)
        # Под утро ночная пауза не перескакивает начало дня: первый опрос — в 8:02–8:05, а не к девяти-десяти.
        self.assertEqual(rules.hh_poll_seconds(datetime(2026, 11, 24, 7, 0), high), 3600 + 300)
        self.assertEqual(rules.hh_poll_seconds(datetime(2026, 11, 24, 7, 30), low), 1800 + 120)
        for _ in range(300):
            self.assertTrue(120 <= rules.hh_poll_seconds(datetime(2026, 11, 23, 13, 0)) <= 300)
            self.assertTrue(2700 <= rules.hh_poll_seconds(datetime(2026, 11, 24, 0, 30)) <= 7200)
        self.assertGreater(len({rules.hh_poll_seconds(datetime(2026, 11, 23, 13, 0)) for _ in range(50)}), 1)
        self.assertEqual(rules.hh_silent_after(300).total_seconds(), 1200)
        self.assertEqual(rules.hh_silent_after(7200).total_seconds(), 11100)
        self.assertEqual(rules.hh_silent_after(None).total_seconds(), 1200)
        self.assertEqual(rules.hh_silent_after('мусор').total_seconds(), 1200)
        # Свой отступ моста при беде с hh (сторож ждёт не раньше него): секунды, мусор — ноль.
        self.assertEqual(hh.clean_pause({'pause_sec': 1800.0}), 1800)
        self.assertEqual(hh.clean_pause({'pause_sec': 10 ** 9}), hh.PAUSE_MAX_SECONDS)
        for broken in (None, {}, {'pause_sec': None}, {'pause_sec': True}, {'pause_sec': 'полчаса'},
                       {'pause_sec': -5}, {'pause_sec': float('nan')}, {'pause_sec': float('inf')}):
            self.assertEqual(hh.clean_pause(broken), 0)

    def test_clean_vacancy_settings(self):
        self.assertEqual(hh.clean_vacancy_settings({'1': {'on': False}, '2': {'on': True, 'vacancy_id': '7'},
                                                    '3': {'vacancy_id': None}}, {7}),
                         {'1': {'on': False}, '2': {'on': True, 'vacancy_id': 7}, '3': {'on': True}})
        for broken in (None, [], {'x': {'on': True}}, {'1': 'on'}, {'1': {'vacancy_id': 'abc'}}, {'1': {'vacancy_id': 8}}):
            with self.assertRaises(ValueError):
                hh.clean_vacancy_settings(broken, {7})

    def test_view(self):
        now = datetime(2026, 11, 23, 13, 0)
        row = {'settings': {'vacancies': {'2': {'on': False}}},
               'cursor': {'bridge': {'seen_at': '2026-11-23T12:59:00', 'session': 'ok', 'version': 'hh/1.0.0'},
                          'account': {'manager': 'Руслан', 'employer': 'Ресурс Плюс KZ'},
                          'vacancies': [{'id': '1', 'name': 'Оператор'}, {'id': '2', 'name': 'Маркетолог'}]}}
        view = hh.view(row, now)
        self.assertEqual(view['since'], '2026-11-23T12:08')
        self.assertFalse(view['silent'])
        self.assertEqual([(v['id'], v['on']) for v in view['vacancies']], [('1', True), ('2', False)])
        self.assertTrue(hh.view(row, datetime(2026, 11, 23, 13, 30))['silent'])
        self.assertEqual(hh.view(None, now)['vacancies'], [])


class CallsTests(unittest.TestCase):
    """Звонки из телефонии Binotel (hiring/calls.py): разбор вебхука и истории, исход, запись."""

    WEBHOOK = {
        'callDetails[generalCallID]': '4411', 'callDetails[callType]': '1', 'callDetails[internalNumber]': '8501',
        'callDetails[externalNumber]': '77051234567', 'callDetails[disposition]': 'ANSWER', 'callDetails[billsec]': '187',
        'callDetails[waitsec]': '12', 'callDetails[startTime]': '1793350000', 'callDetails[recordingStatus]': '1',
        'callDetails[companyID]': '77', 'requestType': 'apiCallCompleted',
    }

    def test_result_mapping_matches_remote_call_center(self):
        from hiring import calls
        self.assertEqual(calls.map_result('ANSWER'), 'answered')
        self.assertEqual(calls.map_result('vm-success'), 'answered')
        self.assertEqual(calls.map_result('BUSY'), 'busy')
        self.assertEqual(calls.map_result('NOANSWER'), 'no_answer')
        self.assertEqual(calls.map_result('CANCEL'), 'no_answer')
        self.assertEqual(calls.map_result('CONGESTION'), 'other')
        for pending in ('', 'ONLINE', 'CALLING', 'RINGING'):
            self.assertEqual(calls.map_result(pending), '')

    def test_webhook_fields_become_a_call(self):
        from hiring import calls
        call = calls.from_webhook(self.WEBHOOK)
        self.assertEqual(call['general_call_id'], '4411')
        self.assertEqual(call['internal_number'], '8501')
        self.assertEqual(call['external_phone'], '+77051234567')
        self.assertEqual(call['direction'], 'out')
        self.assertEqual((call['billsec'], call['waitsec']), (187, 12))
        self.assertEqual(call['started_at'], datetime(2026, 10, 30, 13, 46, 40))   # unix UTC → Алматы (+5)
        self.assertTrue(calls.talked(call))
        self.assertEqual(calls.touch_result(call), 'dial')
        self.assertEqual(calls.touch_body(call), 'Исходящий звонок · Дозвонились · 3:07')
        self.assertEqual(calls.recording_state(call), 'available')
        incoming = calls.from_webhook(dict(self.WEBHOOK, **{'callDetails[callType]': '0', 'callDetails[disposition]': 'NOANSWER',
                                                              'callDetails[billsec]': '0'}))
        self.assertEqual(incoming['direction'], 'in')
        self.assertEqual(calls.touch_result(incoming), 'no_answer')
        self.assertEqual(calls.touch_body(incoming), 'Входящий звонок · Не поднял трубку')
        self.assertEqual(calls.recording_state(incoming), 'none')
        self.assertIsNone(calls.from_webhook({'callDetails[generalCallID]': '1'}))          # без линии
        self.assertIsNone(calls.from_webhook({'generalCallID': '', 'internalNumber': '8501'}))

    def test_history_row_becomes_the_same_call(self):
        from hiring import calls
        call = calls.from_history({'general_call_id': '4411', 'call_type': 1, 'internal_number': '8501',
                                   'external_number': '87051234567', 'disposition': 'ANSWER', 'billsec': 61, 'waitsec': 4,
                                   'start_time': 1793350000, 'recording_status': ''})
        self.assertEqual(call['external_phone'], '+77051234567')
        self.assertEqual(call['started_at'], datetime(2026, 10, 30, 13, 46, 40))
        self.assertEqual(calls.format_duration(61), '1:01')
        self.assertIsNone(calls.from_history({'general_call_id': '', 'internal_number': '8501'}))


class ReportFilterTests(unittest.TestCase):
    def test_month_and_cohort_bounds(self):
        filters = reports.parse_filters({'month': '2026-10', 'recruiters': '3,5,x', 'sources': '2'})
        self.assertEqual(filters['cohort_from'], datetime(2026, 10, 1))
        self.assertEqual(filters['cohort_to'], datetime(2026, 11, 1))
        self.assertEqual(filters['recruiters'], [3, 5])
        self.assertEqual(filters['sources'], [2])
        self.assertIsNone(filters['stage'])

    def test_period_overrides_month_and_stage_filter(self):
        filters = reports.parse_filters({'from': '2026-09-15', 'to': '2026-09-20', 'stage': 'on_line', 'stage_from': '2026-09-01'})
        self.assertIsNone(filters['month'])
        self.assertEqual(filters['cohort_to'], datetime(2026, 9, 21))
        self.assertEqual(filters['stage'], 'on_line')
        self.assertEqual(filters['stage_from'], datetime(2026, 9, 1))
        self.assertIsNone(filters['stage_to'])

    def test_recruiter_is_forced_for_non_lead(self):
        filters = reports.parse_filters({'recruiters': '1,2'}, forced_recruiter_id=9)
        self.assertEqual(filters['recruiters'], [9])

    def test_bad_month_falls_back_to_current(self):
        filters = reports.parse_filters({'month': 'oops'})
        self.assertEqual(filters['month'], rules.month_of(rules.now_almaty()))

    def test_rates(self):
        self.assertEqual(rules.through_rate(1, 3), 33.3)
        self.assertIsNone(rules.through_rate(1, 0))
        self.assertEqual(rules.month_bounds('2026-12'), (datetime(2026, 12, 1), datetime(2027, 1, 1)))


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = BOT_PY.read_text(encoding='utf-8')
        cls.app = APP_JSX.read_text(encoding='utf-8')
        cls.database = DATABASE_PY.read_text(encoding='utf-8')
        cls.sources = SOURCES_PY.read_text(encoding='utf-8')
        cls.bell = BELL_JSX.read_text(encoding='utf-8')
        cls.scopes = SCOPES_PY.read_text(encoding='utf-8')

    def test_schema_blueprint_flag_and_scheduler(self):
        self.assertIn('self._init_hiring_schema_tx(cursor)', self.database)
        self.assertIn("TG_TABLE_NAME = 'hiring_notifications'", self.database)
        self.assertIn("('trg_bell_hiring_notifications_insert', 'hiring_notifications', 'AFTER INSERT', ''),", self.database)
        self.assertIn('build_hiring_blueprint(', self.bot)
        self.assertIn('build_hiring_external_blueprint(', self.bot)
        self.assertIn('"hiring_access": hiring_access,', self.bot)
        # Линии Binotel «Удаленного КЦ» рекрутерам — сервис раздела по требованию (появляется ниже по файлу).
        self.assertEqual(self.bot.count('dial_list=lambda: _dial_list_service,'), 2)   # Blueprint и сервис
        # Звонки из телефонии: наблюдатель вебхука Binotel и добор истории линий рекрутеров.
        self.assertIn("_dial_list_service.call_observers.append(_HIRING['service'].on_binotel_call)", self.bot)
        self.assertIn("('calls_sync', CronTrigger(", self.bot)
        self.assertIn("if name == 'calls_sync':", self.bot)
        self.assertIn("('shifts_sync', CronTrigger(", self.bot)
        self.assertIn("if name == 'shifts_sync':", self.bot)
        # «Не отвечает звонок/чат» (владелец 20.11.2026): сторож раз в 10 минут.
        self.assertIn("('no_response', CronTrigger(minute='3,13,23,33,43,53'", self.bot)
        self.assertIn("if name == 'no_response':\n            return service.archive_silent()", self.bot)
        # Отклики hh.kz приносит мост на шлюзе по подписи моста; задача hh — его сторож (владелец 23.11.2026) и балл ИИ
        # резюме из базы hh, которым его ещё не поставили (владелец 25.11.2026).
        self.assertIn('app.register_blueprint(build_hiring_hh_agent_blueprint(', self.bot)
        self.assertIn("if name == 'hh':\n", self.bot)
        hh_job = self.bot.split("if name == 'hh':\n", 1)[1].split('        if name ==', 1)[0]
        self.assertIn('alert = service.hh_watchdog()', hh_job)
        self.assertIn('service.hh_base_sweep()', hh_job)
        self.assertIn('return alert', hh_job)
        self.assertNotIn('pull_hh', self.bot)
        for name in ('olx', 'hh', 'next_contacts', 'auto_return', 'overdue'):
            self.assertIn("('%s', CronTrigger(" % name, self.bot)
        self.assertIn("id='hiring_%s' % _hiring_name", self.bot)

    def test_bell_source(self):
        self.assertIn("'hiring': ('hiring',),", self.sources)
        self.assertIn("'hiring': hiring,", self.sources)
        self.assertIn("'hiring'})", self.sources)
        self.assertIn("hiring: { label: 'Воронка найма'", self.bell)

    def test_partner_scope(self):
        self.assertIn("HIRING_LEADS = 'hiring_leads'", self.scopes)
        self.assertIn("'POST /api/hiring/external/responses'", self.scopes)

    def test_menu_guards_and_view(self):
        self.assertIn("const HiringView = lazyWithRetry(() => import('./components/hiring/HiringView'));", self.app)
        self.assertIn("userLike?.hiring_access === true", self.app)
        self.assertIn("(requestedViewFromUrl !== 'hiring' || canAccessHiringSection) &&", self.app)
        self.assertIn("if (view === 'hiring' && !canAccessHiringSection) {", self.app)
        self.assertIn("if (view === 'hiring' && canAccessHiringSection) return;", self.app)
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'hiring')"), 1)
        self.assertEqual(self.app.count('<span className="sidebar-text">Воронка найма</span>'), 1)
        self.assertIn("{view === 'hiring' && canAccessHiringSection && (", self.app)
        self.assertIn('|| canAccessHiringSection\n', self.app)
        self.assertEqual(self.app.count("if (nextView !== 'hiring') url.searchParams.delete('card');"), 2)
        self.assertIn("if (nextView === 'hiring' && Number(target)) {", self.app)
        self.assertIn("if (view !== 'hiring') setHiringFocusRequest(null);", self.app)
        trainer_block = self.app[self.app.index('const TRAINER_ALLOWED_VIEWS'):self.app.index(']);', self.app.index('const TRAINER_ALLOWED_VIEWS'))]
        self.assertIn("'hiring',", trainer_block)


class HhBaseTests(unittest.TestCase):
    """Поиск по базе резюме hh.kz (владелец 25.11.2026): настройка поиска вакансии, запрос к hh, расписание, разбор
    найденного, контакты, текст резюме для ИИ и ответ ИИ."""

    def test_profile_and_keywords(self):
        from hiring import hh_base
        self.assertEqual(hh_base.clean_keywords('оператор call-центра\n"оператор колл-центра", (менеджер)!; Оператор CALL-центра'),
                         ['оператор call-центра', 'оператор колл-центра', 'менеджер'])
        self.assertEqual(len(hh_base.clean_keywords(['слово %d' % n for n in range(30)])), hh_base.KEYWORDS_MAX)
        self.assertEqual(hh_base.clean_profile({'enabled': False}), {'enabled': False, 'portrait': None, 'keywords': []})
        with self.assertRaises(hh_base.ProfileError):
            hh_base.clean_profile({'enabled': True, 'portrait': 'Портрет', 'keywords': []})
        with self.assertRaises(hh_base.ProfileError):
            hh_base.clean_profile({'enabled': True, 'portrait': '  ', 'keywords': ['оператор']})
        profile = hh_base.clean_profile({'enabled': True, 'portrait': ' Портрет\r\n\r\n\r\nсотрудника ', 'keywords': ['оператор']})
        self.assertEqual(profile['portrait'], 'Портрет\n\nсотрудника')
        self.assertIs(hh_base.clean_profile({'enabled': 'true', 'keywords': []})['enabled'], False)   # только true
        self.assertEqual(hh_base.search_text(['оператор call-центра', 'оператор', 'специалист контакт-центра']),
                         '"оператор call-центра" OR оператор OR "специалист контакт-центра"')

    def test_search_task_and_schedule(self):
        from hiring import hh_base
        noon = datetime(2026, 11, 25, 12, 0)
        profile = {'enabled': True, 'keywords': ['оператор'], 'last_run_at': None, 'area_id': 188}
        first = hh_base.search_task(profile, noon)
        self.assertEqual((first['params']['area'], first['params']['search_period'], first['params']['order_by'],
                          first['pages'], first['stop_before'], first['reason']), ('188', '30', 'relevance', 5, None, 'auto'))
        self.assertEqual(hh_base.search_task(dict(profile, area_id=None), noon)['params']['area'], '160')
        later = hh_base.search_task(dict(profile, last_run_at=datetime(2026, 11, 25, 9, 0)), noon, manual=True)
        self.assertEqual((later['params']['search_period'], later['params']['order_by'], later['pages'], later['stop_before'],
                          later['reason']), ('3', 'publication_time', 3, '2026-11-24T09:00:00+05:00', 'manual'))
        self.assertTrue(hh_base.search_due(profile, datetime(2026, 11, 25, 23, 0)))           # первый — в любое время
        ran = dict(profile, last_run_at=datetime(2026, 11, 25, 9, 0))
        self.assertFalse(hh_base.search_due(ran, datetime(2026, 11, 25, 11, 59)))
        self.assertTrue(hh_base.search_due(ran, datetime(2026, 11, 25, 12, 0)))
        self.assertFalse(hh_base.search_due(ran, datetime(2026, 11, 25, 19, 0)))              # вечером — нет
        self.assertFalse(hh_base.search_due(ran, datetime(2026, 11, 26, 8, 59)))
        self.assertTrue(hh_base.search_due(ran, datetime(2026, 11, 26, 9, 0)))
        self.assertFalse(hh_base.search_due(dict(profile, enabled=False), noon))
        self.assertFalse(hh_base.search_due(dict(profile, keywords=[]), noon))
        self.assertTrue(hh_base.run_now_allowed({}, noon))
        self.assertFalse(hh_base.run_now_allowed({'requested_at': datetime(2026, 11, 25, 11, 55)}, noon))
        self.assertTrue(hh_base.run_now_allowed({'last_run_at': datetime(2026, 11, 25, 11, 50)}, noon))
        self.assertEqual(hh_base.contacts_next_check(noon, noon), datetime(2026, 11, 25, 12, 1))
        self.assertEqual(hh_base.contacts_next_check(noon, datetime(2026, 11, 25, 12, 2)), datetime(2026, 11, 25, 12, 3))
        self.assertEqual(hh_base.contacts_next_check(noon, datetime(2026, 11, 25, 12, 9, 59)), datetime(2026, 11, 25, 12, 10))
        self.assertIsNone(hh_base.contacts_next_check(noon, datetime(2026, 11, 25, 12, 10)))

    def test_found_and_contacts(self):
        from hiring import hh_base
        self.assertIsNone(hh_base.clean_found({'hash': 'не хеш'}))
        found = hh_base.clean_found({
            'hash': 'AB12CD3400112233445566778899AABBCCDDEE', 'resume_id': '900000001', 'title': '  Оператор   call-центра ',
            'age': 26, 'gender': 'female', 'area': 'Алматы', 'salary': {'amount': 200000, 'currency': 'KZT'},
            'experience_months': 38, 'experience': [{'company': 'ТОО Ромашка', 'position': 'Продавец', 'start': '2023-09-01'}, 'мусор'],
            'skills': ['Продажи', '', 5], 'job_status': 'active_search', 'online': 1, 'access': 'openned',
            'last_activity_at': '2026-11-25T09:30:00.500+03:00', 'updated_at': '2026-11-24T20:00:00+00:00',
            'topic': {'vacancy_id': 138923446, 'topic_id': 5000000777, 'type': 'INVITATION_BY_EMPLOYER',
                      'at': '2026-11-25T09:40:00.100+03:00'},
            'phone': '+77070000000', 'first_name': 'Нельзя'})
        self.assertEqual(found['hash'], 'ab12cd3400112233445566778899aabbccddee')
        self.assertEqual(found['resume_id'], 900000001)
        self.assertEqual(found['snippet'], {
            'title': 'Оператор call-центра', 'age': 26, 'gender': 'женщина', 'area': 'Алматы',
            'salary': {'amount': 200000, 'currency': 'KZT', 'sign': '₸'}, 'experience_months': 38,
            'experience': [{'company': 'ТОО Ромашка', 'position': 'Продавец', 'start': '2023-09-01', 'end': None}],
            'skills': ['Продажи', '5'], 'job_status': 'active_search', 'online': False})
        self.assertIsNone(found['access'])                       # неизвестное состояние контактов — не выдумываем
        self.assertEqual(found['hh_updated_at'], datetime(2026, 11, 25, 1, 0))
        self.assertEqual(found['last_activity_at'], datetime(2026, 11, 25, 11, 30, 0, 500000))
        self.assertEqual(found['topic'], {'vacancy_id': '138923446', 'topic_id': '5000000777',
                                          'type': 'INVITATION_BY_EMPLOYER', 'at': '2026-11-25T11:40'})
        self.assertNotIn('phone', str(found))
        self.assertIsNone(hh_base.clean_contacts({}))
        self.assertEqual(hh_base.clean_contacts({'last_name': 'Иванова', 'first_name': 'Анна', 'phones': ['+998 90 1', '8 (707) 123-45-67'],
                                                 'email': 'anna@example.kz'}),
                         {'full_name': 'Иванова Анна', 'phone': '+77071234567', 'email': 'anna@example.kz',
                          'phone_raw': '+998 90 1'})
        self.assertIsNone(hh_base.clean_contacts({'first_name': 'Анна', 'phones': ['+998 90 123 45 67']})['phone'])

    def test_candidate_text_score_and_card_entries(self):
        from hiring import hh, hh_base
        snippet = hh_base.clean_found({'hash': 'b' * 38, 'title': 'Оператор', 'age': 21, 'gender': 'male',
                                       'salary': {'amount': 250000, 'currency': 'KZT'}, 'experience_months': 0,
                                       'skills': ['Excel'], 'job_status': 'looking_for_offers'})['snippet']
        text = hh_base.candidate_text(snippet)
        self.assertIn('Желаемая должность: Оператор', text)
        self.assertIn('О кандидате: 21 лет, мужчина, ожидает 250 000 ₸', text)
        self.assertIn('Общий стаж: нет', text)
        self.assertIn('Статус на hh: Рассматривает предложения', text)
        self.assertIn('краткая карточка', text)
        full = hh.clean_resume({'title': 'Оператор КЦ', 'languages': [{'title': 'Казахский', 'degree': 'l1'}],
                                'experience_total': {'years': 1, 'months': 2}, 'about': 'Люблю людей',
                                'experience': [{'company': 'ТОО', 'position': 'Оператор', 'start': '2024-01-01',
                                                'description': 'Звонки\n\nЧаты'}]})
        text = hh_base.candidate_text(snippet, full)
        self.assertIn('Языки: Казахский (родной)', text)
        self.assertIn('Общий стаж: 1 год 2 мес.', text)
        self.assertIn('- Оператор, ТОО (2024-01-01 — сейчас): Звонки Чаты', text)
        self.assertNotIn('краткая карточка', text)
        self.assertEqual(hh_base.clean_score({'score': '87.6', 'verdict': ' Хороший ', 'pros': ['a', '', 'b', 'c', 'd'],
                                              'cons': None}),
                         {'score': 88, 'verdict': 'Хороший', 'pros': ['a', 'b', 'c'], 'cons': []})
        for bad in ({'score': 101}, {'score': -1}, {'score': 'x'}, None):
            self.assertIsNone(hh_base.clean_score(bad))
        self.assertEqual([hh_base.experience_text(m) for m in (1, 12, 26, 61, 0)],
                         ['1 мес.', '1 год', '2 года 2 мес.', '5 лет 1 мес.', None])
        entries = hh_base.card_entries([{'resume_hash': 'c' * 38, 'hh_vacancy_id': 1, 'vacancy_name': 'Оператор',
                                         'status_at': datetime(2026, 11, 25, 12, 0), 'snippet': snippet, 'resume': None,
                                         'score': 80, 'score_data': {'verdict': 'Ок'}}])
        self.assertEqual((entries[0]['resume']['title'], entries[0]['resume']['key_skills'], entries[0]['resume_url'],
                          entries[0]['verdict']), ('Оператор', ['Excel'], 'https://hh.kz/resume/' + 'c' * 38, 'Ок'))
        self.assertTrue(hh_base.bridge_ready('hh/1.4.0/3.12.3'))
        self.assertTrue(hh_base.bridge_ready('hh/2.0.1'))
        self.assertFalse(hh_base.bridge_ready('hh/1.3.0/3.12.3'))
        self.assertFalse(hh_base.bridge_ready(None))

    def test_ai_keywords_and_scores(self):
        import importlib
        import sys
        import types
        from unittest import mock
        from hiring import hh_ai
        # ai_feedback.service тянет database (time.tzset — только Linux, плюс пул к базе): заглушка, как в test_dress_code_ai.
        if 'ai_feedback.service' not in sys.modules:
            stub = types.ModuleType('database')
            stub.db = mock.MagicMock()
            stub.IT_TICKET_CATALOG = {}
            with mock.patch.dict(sys.modules, {'database': stub}):
                module = importlib.import_module('ai_feedback.service')
            sys.modules['ai_feedback.service'] = module
            import ai_feedback
            ai_feedback.service = module
        ai = sys.modules['ai_feedback.service']
        endpoint = mock.patch.object(ai, 'gemini_endpoint', return_value=('https://vertex.test/model', {}))
        endpoint.start()
        self.addCleanup(endpoint.stop)

        def post_with(answer):
            calls = []

            def post(url, headers, payload):
                calls.append(payload)
                return 200, {'candidates': [{'content': {'parts': [{'text': answer}]}}],
                             'usageMetadata': {'promptTokenCount': 120, 'candidatesTokenCount': 30}}
            return post, calls

        post, calls = post_with('{"keywords": ["оператор call-центра", "OR", "\\"оператор колл-центра\\"", "оператор call-центра"]}')
        self.assertEqual(hh_ai.suggest_keywords('Оператор', 'Грамотная речь, опыт с клиентами', post=post),
                         ['оператор call-центра', 'оператор колл-центра'])   # «OR» — не фраза
        self.assertIn('Грамотная речь', calls[0]['contents'][0]['parts'][0]['text'])
        with self.assertRaises(hh_ai.HhAiError):
            hh_ai.suggest_keywords('Оператор', 'кратко', post=post)
        post, calls = post_with('{"results": [{"id": "1", "score": 91, "verdict": "Подходит"},'
                                ' {"id": "2", "score": 300, "verdict": "мусор"}, {"id": "чужой", "score": 50, "verdict": "x"}]}')
        results, meta = hh_ai.score_candidates('Оператор', 'Портрет', [('1', 'резюме 1'), ('2', 'резюме 2')], post=post)
        self.assertEqual(list(results), ['1'])
        self.assertEqual(results['1']['score'], 91)
        self.assertEqual((meta['tokens_in'], meta['tokens_out']), (120, 30))
        self.assertIn('Кандидат id=2', calls[0]['contents'][0]['parts'][0]['text'])


if __name__ == '__main__':
    unittest.main()

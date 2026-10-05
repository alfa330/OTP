# -*- coding: utf-8 -*-
"""Фронт и бэкенд раздела «Ограничитель Перезвона» должны сходиться по правам.

Этого теста не было, и именно поэтому расхождение прожило до 31.08.2026: фронт
выдавал пункт меню супервайзерам СЗоВ (переиспользованный предикат табло), а
бэкенд отвечал им «Раздел вам не открыт». Человек такое видит раньше нас.

Здесь же проверяются точки подключения раздела в App.jsx: пункт меню в двух
ветвях сайдбара, гард видимости и открытие по адресу. Образец — класс
SzovWallboardWiringTests в tests/test_szov_wallboard.py.

С 05.10.2026 у раздела вторая часть — отдел продаж (автоофлайн iCORE Phone,
OktellGuardPhonePanel.jsx). Глава и СВ ОП в раздел теперь пускаются — каждый
в свою часть; какую именно, решает бэкенд (access.visible_department_codes).
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oktell_guard import access, phone  # noqa: E402


class OktellGuardWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8-sig')
        cls.view = (
            ROOT / 'src' / 'components' / 'oktell_guard' / 'OktellGuardView.jsx'
        ).read_text(encoding='utf-8-sig')
        cls.phone_panel = (
            ROOT / 'src' / 'components' / 'oktell_guard' / 'OktellGuardPhonePanel.jsx'
        ).read_text(encoding='utf-8-sig')
        cls.phone_helpers = (
            ROOT / 'src' / 'components' / 'oktell_guard' / 'oktellGuardPhone.js'
        ).read_text(encoding='utf-8-sig')

    def gate(self):
        """Тело фронтового предиката — по нему сверяем круг с бэкендом."""
        return self.app.split('const canAccessOktellGuardForUser')[1].split('};')[0]

    def test_department_code_matches_the_backend(self):
        self.assertIn("const OKTELL_GUARD_DEPARTMENT_CODE = '%s';" % access.SECTION_DEPARTMENT_CODE,
                      self.app)

    def test_phone_department_code_matches_the_backend(self):
        """Код части ОП — отдельной константой, а не заменой первой: на ней
        держатся и пункт меню (App.jsx), и выбор панели (oktellGuardPhone.js)."""
        self.assertIn("const OKTELL_GUARD_PHONE_DEPARTMENT_CODE = '%s';" % phone.PHONE_DEPARTMENT_CODE,
                      self.app)
        self.assertIn("export const PHONE_DEPARTMENT_CODE = '%s';" % phone.PHONE_DEPARTMENT_CODE,
                      self.phone_helpers)
        self.assertEqual(access.PHONE_DEPARTMENT_CODE, phone.PHONE_DEPARTMENT_CODE)

    def test_frontend_gate_admits_the_same_three_circles(self):
        """Админы, главы СЗоВ и ОП, СВ СЗоВ и ОП — те же ветки, что в access.py."""
        gate = self.gate()
        self.assertIn("if (role === 'super_admin') return true;", gate)
        # Тот же намеренный вырез, что у табло: глава чужого отдела не админ.
        self.assertIn("if (role === 'admin' && !isDepartmentHead(userLike)) return true;", gate)
        self.assertIn('isOktellGuardDepartmentHead(userLike)', gate)
        self.assertIn('isSupervisorRole(role)', gate)
        self.assertIn('=== OKTELL_GUARD_DEPARTMENT_CODE', gate)
        # СВ продаж: и по коду, и по id отдела — у части профилей есть только одно.
        self.assertIn('=== OKTELL_GUARD_PHONE_DEPARTMENT_CODE', gate)
        self.assertIn('=== AI_QA_OP_DEPARTMENT_ID', gate)
        # Глава — любого из двух отделов раздела.
        head = self.app.split('const isOktellGuardDepartmentHead')[1].split(');')[0]
        self.assertIn('OKTELL_GUARD_DEPARTMENT_CODES.has(code)', head)
        codes = self.app.split('const OKTELL_GUARD_DEPARTMENT_CODES = new Set([')[1].split(']);')[0]
        self.assertIn('OKTELL_GUARD_DEPARTMENT_CODE,', codes)
        self.assertIn('OKTELL_GUARD_PHONE_DEPARTMENT_CODE,', codes)

    def test_backend_admits_the_same_three_circles(self):
        """Вторая половина той же сверки — уже на реальной логике бэкенда."""
        szov = {'department_code': 'szov'}
        self.assertTrue(access.can_view_section(dict(szov, role='super_admin')))
        self.assertTrue(access.can_view_section({'role': 'admin', 'department_code': ''}))
        self.assertTrue(access.can_view_section(dict(szov, role='admin', is_department_head=True)))
        self.assertTrue(access.can_view_section(dict(szov, role='sv')))
        # С 05.10.2026 ОП в разделе есть — но только своей частью.
        op = {'department_code': 'op'}
        self.assertTrue(access.can_view_section(dict(op, role='sv')))
        self.assertTrue(access.can_view_section(dict(op, role='admin', is_department_head=True)))
        self.assertEqual(access.visible_department_codes(dict(op, role='sv')), ['op'])
        self.assertEqual(access.visible_department_codes(dict(szov, role='sv')), ['szov'])
        self.assertEqual(access.visible_department_codes({'role': 'super_admin'}), ['szov', 'op'])
        # Чужой отдел по-прежнему мимо: и фронт, и бэкенд пускают только эти два.
        self.assertFalse(access.can_view_section({'role': 'sv', 'department_code': 'tez'}))
        # Правит правило ОП глава ОП, а не СВ; Oktell ему не открывается.
        self.assertTrue(access.can_manage_phone_settings(dict(op, role='admin', is_department_head=True)))
        self.assertFalse(access.can_manage_phone_settings(dict(op, role='sv')))
        self.assertFalse(access.can_manage_settings(dict(op, role='admin', is_department_head=True)))

    def test_gate_is_not_borrowed_from_the_wallboard(self):
        """Раздел жил на предикате табло, и это было молчаливой связкой: сузят
        табло — ограничитель потеряет тех же людей, никто не заметит."""
        self.assertIn('const canAccessOktellGuard = canAccessOktellGuardForUser(user);', self.app)
        self.assertNotIn('const canAccessOktellGuard = canAccessSzovWallboardForUser(user);', self.app)

    def test_sidebar_item_present_in_both_branches(self):
        """Пункт виден и админам, и главе/СВ — а это две независимые разметки
        сайдбара, поэтому <li> обязан встречаться дважды. У «Посылок» тест
        требует обратного (ровно одно вхождение) — там пункт объявлен в общей
        части, здесь ветви разные, и одно вхождение означало бы, что половина
        допущенных раздел в меню не увидит."""
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'oktell_guard')"), 2)
        self.assertEqual(
            self.app.count('<span className="sidebar-text">Ограничитель «Перезвона»</span>'), 2)

    def test_view_is_reachable_by_url_and_not_bounced(self):
        """Ctrl-клик по пункту меню открывает ?view=oktell_guard — без строки в
        canOpenRequestedView новая вкладка уезжала в раздел по умолчанию."""
        self.assertIn("(requestedViewFromUrl !== 'oktell_guard' || canAccessOktellGuard)", self.app)
        # allowlist отдела не должен уводить с раздела: у него свой предикат.
        self.assertIn("if (view === 'oktell_guard' && canAccessOktellGuard) return;", self.app)
        # Спрятанный пункт меню доступом не является — сам раздел тоже за гейтом.
        self.assertIn('view === "oktell_guard" && canAccessOktellGuard', self.app)

    def test_read_only_screen_has_no_dead_ends(self):
        """У СВ can_manage=false, и интерфейс обязан быть последовательным: ни
        одного живого правящего контрола и ни одной галочки, ведущей в тупик."""
        self.assertEqual(self.view.count('disabled={!canManage}'), 6)
        # Единственные входы в массовую правку и загрузку версии — под canManage.
        self.assertEqual(self.view.count('setBulkOpen(true)'), 1)
        self.assertEqual(self.view.count('setUploadOpen(true)'), 1)
        # ...и оба стоят под гейтом, а не рядом с ним.
        self.assertIsNotNone(
            re.search(r'\{canManage && \(\s*<button\s+type="button"\s+onClick=\{\(\) => setBulkOpen\(true\)\}',
                      self.view),
            'кнопка массовой правки должна быть под canManage')
        self.assertIn('right={canManage ? (', self.view)
        # Галочки строк тоже спрятаны: выделять нечего, действие только одно.
        checkbox = re.search(r'\{canManage && \(\s*<input\s*type="checkbox"', self.view)
        self.assertIsNotNone(checkbox, 'галочка строки должна быть под canManage')

    # --- «Скачать Oktell»: пункта в меню больше нет ------------------------
    #
    # С 07.09 по 22.09.2026 пункт стоял у каждого оператора СЗоВ, и его гейт жил
    # в двух местах — во фронте и в access.can_download_agent. 22.09.2026
    # владелец пункт убрал: им не пользуются, агент ставится на машины
    # централизованно (MSI через групповую политику), а не скачиванием из меню.
    # Ручка /download осталась: в неё ходит кнопка «Скачать агента» в самом
    # разделе. Проверяем обе половины: в меню нет ни пункта, ни его обвязки, а
    # ручка и её круг — на месте.

    def test_oktell_download_item_is_gone_from_the_menu(self):
        """Убрано целиком, а не спрятано: без гейта, обработчика и строки в
        карте отделов. Мёртвый гейт читался бы как действующее правило, а
        строка карты без пункта валит sidebar_department_filter.test.mjs."""
        self.assertNotIn('Скачать Oktell', self.app)
        for token in ('downloadOktellAgent', 'canDownloadOktellAgent',
                      'OKTELL_AGENT_USER_ROLES', 'download_oktell',
                      '/api/oktell_guard/download'):
            self.assertNotIn(token, self.app, token)

    def test_download_handle_keeps_its_own_predicate(self):
        """Ручка /download и после снятия пункта стоит на can_download_agent:
        круг не сужали (почему — в access.py), оператор СЗоВ по прямому
        запросу файл получает, раздела при этом не видит."""
        routes = (ROOT / 'oktell_guard' / 'routes.py').read_text(encoding='utf-8')
        self.assertIn("@section_route('/download', gate=access.can_download_agent)", routes)
        operator = {'role': 'operator', 'department_code': 'szov'}
        self.assertTrue(access.can_download_agent(operator))
        self.assertFalse(access.can_view_section(operator))

    def test_section_keeps_its_own_download_button(self):
        """Файл по-прежнему качают из раздела — СВ и глава ставят агента себе и
        раздают его. Качается в этом же окне (startFileDownload), а не новой
        вкладкой — см. tests/test_file_download_same_window.py."""
        self.assertIn("const data = await request('/download');", self.view)
        self.assertIn('startFileDownload(data?.url)', self.view)

    def test_icore_phone_item_is_named_by_the_program(self):
        """Пункт один и назван именем программы: безымянный «Скачать телефон»
        читался бы как общий для всех отделов, а у СЗоВ телефон — Oktell."""
        self.assertEqual(
            self.app.count('<span className="sidebar-text">Скачать iCore Phone</span>'), 1)
        self.assertNotIn('<span className="sidebar-text">Скачать телефон</span>', self.app)

    def test_read_only_screen_explains_itself(self):
        """Погашенное поле без объяснения читается как поломка."""
        self.assertIn('Раздел открыт вам на просмотр.', self.view)
        self.assertIn('Раздел открыт вам на просмотр.', self.phone_panel)

    # --- Два отдела: переключатель и часть ОП -------------------------------

    def test_every_request_names_its_department(self):
        """Без ?department= сервер отдаёт первую открытую часть. Для загрузки
        раздела это и нужно (глава ОП сразу попадает в свою), а правка и отчёт
        СЗоВ обязаны называть отдел сами: иначе у админа после переключения
        они уехали бы не туда."""
        self.assertIn("request(`/settings${query}`)", self.view)
        self.assertIn("request(`/employees${query}`)", self.view)
        self.assertIn("request(`/report?${departmentQuery(OKTELL_DEPARTMENT_CODE)}", self.view)
        self.assertIn("request(`/settings?${departmentQuery(OKTELL_DEPARTMENT_CODE)}`, {", self.view)
        self.assertIn("request(`/employees/bulk?${departmentQuery(OKTELL_DEPARTMENT_CODE)}`, {",
                      self.view)
        for path in ('/employees?${DEPARTMENT}', '/settings?${DEPARTMENT}',
                     '/report?${DEPARTMENT}', '/phone/settings?${DEPARTMENT}'):
            self.assertIn(path, self.phone_panel, path)
        # У ОП нет ни персональных правил, ни агента, ни версий программы.
        for token in ('/employees/bulk', "'/download'", '/release', 'setBulkOpen', 'setUploadOpen'):
            self.assertNotIn(token, self.phone_panel, token)

    def test_department_switch_is_shown_only_when_there_is_a_choice(self):
        """У главы и СВ переключатель был бы одной неактивной кнопкой."""
        self.assertIn('const departmentSwitch = departments.length > 1 ? (', self.view)
        self.assertIn('ariaLabel="Отдел"', self.view)
        self.assertIn('if (department === PHONE_DEPARTMENT_CODE) {', self.view)

    def test_phone_panel_read_only_has_no_live_controls(self):
        """СВ ОП читает правило, но не правит: все четыре контрола правила
        (тумблер, пороги, предупреждение, группы) гаснут по can_manage."""
        self.assertEqual(self.phone_panel.count('disabled={!canManage || saving}'), 4)
        self.assertEqual(self.phone_panel.count('patchRule({'), 4)
        # Уход из поля «Предупреждать за» сам ничего не сохраняет: решение —
        # в phoneWarnBlurDecision (сохранять только настоящую правку). PUT на
        # каждый blur гасил контролы под щелчком по соседнему и переписывал
        # автора правила.
        self.assertIn('onBlur={onWarnBlur}', self.phone_panel)
        self.assertIn('phoneWarnBlurDecision(event.target.value, savedWarnRef.current, threshold)',
                      self.phone_panel)
        self.assertNotIn('onBlur={(event) => patchRule(', self.phone_panel)
        # Отметку «сохранено» двигает только ответ сервера, не набор в поле.
        self.assertEqual(self.phone_panel.count('savedWarnRef.current = '), 1)
        self.assertEqual(self.phone_panel.count('applyServerRule(data.phone_settings);'), 2)

    def test_phone_report_drops_stale_responses(self):
        """Каждая промежуточная дата в поле — свой запрос; медленный ответ
        за старый период не должен лечь под новые даты (ни строками, ни
        ошибкой)."""
        self.assertIn('const seq = ++reportSeq.current;', self.phone_panel)
        self.assertEqual(self.phone_panel.count('if (seq !== reportSeq.current) return;'), 2)
        # Как считает таймер — прямо у порога: «5 минут» без этого читаются
        # как «5 минут в смене», а не «5 минут в «Исходе» без звонка».
        self.assertIn('Таймер считает только в «Исходе» и обнуляется только звонком; '
                      'в других статусах замирает', self.phone_panel)

    def test_both_parts_keep_stable_callbacks(self):
        """showToast новый на каждый рендер App; в зависимостях загрузки он
        перезапускал её без конца. Обёртки — в обеих частях раздела."""
        self.assertIn('const toast = useStableCallback(showToast);', self.view)
        self.assertIn('const headerFactory = useStableCallback(withAccessTokenHeader);', self.view)
        self.assertIn('const toast = useStableCallback(toastProp);', self.phone_panel)
        self.assertIn('const request = useStableCallback(requestProp);', self.phone_panel)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

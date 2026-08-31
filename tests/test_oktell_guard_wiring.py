# -*- coding: utf-8 -*-
"""Фронт и бэкенд раздела «Ограничитель Перезвона» должны сходиться по правам.

Этого теста не было, и именно поэтому расхождение прожило до 31.08.2026: фронт
выдавал пункт меню супервайзерам СЗоВ (переиспользованный предикат табло), а
бэкенд отвечал им «Раздел вам не открыт». Человек такое видит раньше нас.

Здесь же проверяются точки подключения раздела в App.jsx: пункт меню в двух
ветвях сайдбара, гард видимости и открытие по адресу. Образец — класс
SzovWallboardWiringTests в tests/test_szov_wallboard.py.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from oktell_guard import access  # noqa: E402


class OktellGuardWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8-sig')
        cls.view = (
            ROOT / 'src' / 'components' / 'oktell_guard' / 'OktellGuardView.jsx'
        ).read_text(encoding='utf-8-sig')

    def gate(self):
        """Тело фронтового предиката — по нему сверяем круг с бэкендом."""
        return self.app.split('const canAccessOktellGuardForUser')[1].split('};')[0]

    def test_department_code_matches_the_backend(self):
        self.assertIn("const OKTELL_GUARD_DEPARTMENT_CODE = '%s';" % access.SECTION_DEPARTMENT_CODE,
                      self.app)

    def test_frontend_gate_admits_the_same_three_circles(self):
        """Админы, глава СЗоВ, СВ СЗоВ — те же три ветки, что в access.py."""
        gate = self.gate()
        self.assertIn("if (role === 'super_admin') return true;", gate)
        # Тот же намеренный вырез, что у табло: глава чужого отдела не админ.
        self.assertIn("if (role === 'admin' && !isDepartmentHead(userLike)) return true;", gate)
        self.assertIn('isOktellGuardDepartmentHead(userLike)', gate)
        self.assertIn('isSupervisorRole(role)', gate)
        self.assertIn('=== OKTELL_GUARD_DEPARTMENT_CODE', gate)

    def test_backend_admits_the_same_three_circles(self):
        """Вторая половина той же сверки — уже на реальной логике бэкенда."""
        szov = {'department_code': 'szov'}
        self.assertTrue(access.can_view_section(dict(szov, role='super_admin')))
        self.assertTrue(access.can_view_section({'role': 'admin', 'department_code': ''}))
        self.assertTrue(access.can_view_section(dict(szov, role='admin', is_department_head=True)))
        self.assertTrue(access.can_view_section(dict(szov, role='sv')))
        self.assertFalse(access.can_view_section({'role': 'sv', 'department_code': 'op'}))

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

    def test_read_only_screen_explains_itself(self):
        """Погашенное поле без объяснения читается как поломка."""
        self.assertIn('Раздел открыт вам на просмотр.', self.view)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

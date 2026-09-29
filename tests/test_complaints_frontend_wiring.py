# -*- coding: utf-8 -*-
"""Раздел «Жалобы» в src/App.jsx: пункт меню, доступ, гарды и диплинк.

Правило одного раздела живёт в нескольких местах (предикат меню, гард вида,
обязательная граница на сервере), и расходятся они молча: пункт есть, а сервер
отвечает 403 — или СВ отдела со своим allowlist выбрасывает из раздела сразу
после входа. Тест держит все места рядом с их серверными двойниками.
"""

import re
import unittest
from pathlib import Path

from complaints import access, catalog, telegram

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src' / 'App.jsx').read_text(encoding='utf-8-sig')


class ComplaintsWiringTest(unittest.TestCase):
    def test_menu_item_is_declared_once_in_the_common_tail(self):
        """Как «Обращения»: один раз в общей части меню — круг раздела не
        совпадает ни с одной ролевой веткой (sidebar-structure-is-test-locked)."""
        self.assertEqual(APP.count("handleSidebarViewNavigation(e, 'complaints')"), 1)
        self.assertEqual(APP.count('<span className="sidebar-text">Жалобы</span>'), 1)
        self.assertIn('{canAccessComplaintsSection && (\n', APP.replace('\r\n', '\n'))

    def test_menu_predicate_mirrors_the_server_gate(self):
        match = re.search(r"const COMPLAINTS_HANDLER_DEPARTMENT_CODES = \[(.*?)\];", APP, re.S)
        self.assertIsNotNone(match)
        codes = tuple(re.findall(r"'([a-z_]+)'", match.group(1)))
        self.assertEqual(set(codes), set(catalog.HANDLER_DEPARTMENT_CODES))

    def test_operator_has_no_menu_item(self):
        """Раздел — для тех, кто разбирает (access.can_open_section). Оператор
        СЗоВ заводит жалобу в «Обращениях» (решение владельца 29.09.2026), и
        правила «весь отдел СЗоВ» в предикате меню быть не должно."""
        body = APP.split('const canAccessComplaintsSectionForUser = (userLike) => {')[1].split('\n};')[0]
        self.assertNotIn('COMPLAINTS_INTAKE_DEPARTMENT_CODE', body)
        self.assertIn('isSupervisorRole(role)', body)
        self.assertFalse(access.can_open_section({'user_id': 10, 'role': 'operator',
                                                  'department_code': access.INTAKE_DEPARTMENT_CODE}))

    def test_author_lands_in_crm(self):
        """Ответ по своей жалобе — в «Обращениях»: колокол даёт цель
        «complaint:<id>», старая ссылка ?view=complaints переводит туда же."""
        self.assertIn("/^complaint:(\\d+)$/.exec(String(target || ''))", APP)
        self.assertIn("if (requestedViewFromUrl === 'complaints' && canAccessCrmSection) {", APP)
        self.assertIn('const setter = canAccessComplaintsSection ? setComplaintsFocusRequest', APP)

    def test_view_guard_keeps_supervisors_of_allowlisted_departments(self):
        """У ОП, Теза и фронт-офисов есть allowlist разделов, и без своего
        предиката гард выбросил бы их СВ из «Жалоб» сразу после входа."""
        guard = "if (view === 'complaints' && canAccessComplaintsSection) return;"
        self.assertIn(guard, APP)
        self.assertLess(APP.index(guard), APP.index('if (departmentAllowsView(user, view)) return;'))

    def test_screen_is_behind_the_predicate_and_the_qr_gate(self):
        self.assertIn("{view === 'complaints' && canAccessComplaintsSection && (sensitiveSectionsLocked ? (",
                      APP)
        self.assertIn("import('./components/complaints/ComplaintsView')", APP)
        self.assertIn("if (view === 'complaints' || view === 'crm_tickets' || view === 'wiki'", APP)

    def test_trainer_takes_complaints_in_crm(self):
        """Тренер СЗоВ жалобы принимает, но не разбирает: раздела «Жалобы» в
        его списке нет, а «Обращения», где жалобы заводят, есть."""
        block = re.search(r'const TRAINER_ALLOWED_VIEWS = Object\.freeze\(\[(.*?)\]\);', APP, re.S)
        views = re.findall(r"'([a-z_]+)'", block.group(1))
        self.assertNotIn('complaints', views)
        self.assertIn('crm_tickets', views)

    def test_complaints_are_created_in_the_crm_wizard(self):
        """Заводят жалобу в мастере «Обращений», а не в разделе «Жалобы»."""
        wizard = (ROOT / 'src' / 'components' / 'crm' / 'TicketWizard.jsx').read_text(encoding='utf-8')
        self.assertIn('<ComplaintFields draft={complaintDraft} meta={complaints} />', wizard)
        # Среди тематик — одна строка «Жалоба»; типы с иконками — своим экраном
        # (просьба владельца 29.09.2026), «Назад» с полей ведёт к типам.
        self.assertEqual(wizard.count('<PickRow title="Жалоба" ready'), 1)
        self.assertIn("icon={TARGET_ICONS[item.code] || AlertTriangle}", wizard)
        self.assertIn("{phase === 'complaint_target' && (", wizard)
        self.assertIn('onClick={backToComplaintTargets}', wizard)
        view = (ROOT / 'src' / 'components' / 'complaints' / 'ComplaintsView.jsx').read_text(encoding='utf-8')
        self.assertNotIn('Новая жалоба', view)
        crm = (ROOT / 'src' / 'components' / 'crm' / 'CrmTicketsView.jsx').read_text(encoding='utf-8')
        # Своя жалоба в ленте — в формате обращения: пузыри и панель данных.
        self.assertIn('<ComplaintThreadCard', crm)
        self.assertIn('attachmentUrl={`${apiBaseUrl}/api/complaints/complaints/', crm)
        self.assertIn("complaints={complaintsEnabled ? complaintsMeta : null}", crm)

    def test_deeplink_parameter_matches_the_bot_link(self):
        link = telegram.complaint_link(7)
        self.assertIn('view=complaints&complaint_id=7', link)
        self.assertIn("const COMPLAINT_ID_QUERY_PARAM = 'complaint_id';", APP)
        self.assertIn("if (nextView === 'complaints' && Number(target)) {", APP)

    def test_menu_icon_token_is_registered(self):
        icons = (ROOT / 'src' / 'components' / 'common' / 'FaIcon.jsx').read_text(encoding='utf-8-sig')
        self.assertIn("'fa-message-exclamation': 'MessageSquareWarning'", icons)
        self.assertIn('fa-message-exclamation', APP)


if __name__ == '__main__':
    unittest.main()

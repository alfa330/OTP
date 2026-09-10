# -*- coding: utf-8 -*-
"""Раздел «QR доступ»: сканер, который спрашивает про ЧЕЛОВЕКА.

Постановка владельца 10.09.2026: «зашёл в раздел — сканируешь — выходит
уведомление, какому человеку ты хочешь открыть доступ, подтверждение либо
отмена». До этого раздел спрашивал про СТРОКУ: первым на экране стояло поле
«Токен / строка из QR», а в окне подтверждения печаталась подпись токена —
узнать по ней, кого пускаешь, было нельзя. Имя приходило с сервера уже ПОСЛЕ
того, как доступ открыт.

Отсюда ручка предпросмотра. Здесь сторожатся три вещи, которые ломаются молча:

  * предпросмотр и подтверждение считают периметр ОДНОЙ функцией. Разъехавшись,
    они дают либо «Открыть доступ» с последующим отказом, либо — что хуже —
    карточку с именем и фотографией сотрудника чужого отдела, которого
    подтверждающий видеть не должен;
  * предпросмотр НИЧЕГО не пишет: это ответ на вопрос «кому?», а не выдача;
  * разметка сканера в интерфейсе одна. В App.jsx она лежала ДВУМЯ копиями —
    своя у администраторов, своя у глав отделов с тренерами, — и правка в
    одной из них оставляла половину людей на прежнем экране.
"""

import ast
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import source_cache  # noqa: E402
from wiki.access import QR_GATED_ROLES  # noqa: E402

BOT_PATH = ROOT / 'bot_schedule2.py'
APP_PATH = ROOT / 'src' / 'App.jsx'
VIEW_PATH = ROOT / 'src' / 'components' / 'qr' / 'QrAccessView.jsx'

SZOV, OP = 1, 367


def _bot_module():
    source = BOT_PATH.read_text(encoding='utf-8-sig')
    return source, source_cache.parse(source)


def _function_body(source, module, name):
    """Тело функции БЕЗ докстринга.

    Целиком брать нельзя: докстринги здесь сами называют и общий резолвер, и
    функцию периметра, и страж «своей копии нет» проходил бы за счёт
    объяснения — ровно так пробивали соседние стражи (см. GrantAccessGatedRolesTests
    в test_admin_sessions_section.py).
    """
    node = next(n for n in module.body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    body = node.body[1:] if ast.get_docstring(node) else node.body
    return '\n'.join(ast.get_source_segment(source, stmt) for stmt in body)


class _FakeDb:
    """База ровно в объёме, который читает резолвер."""

    def __init__(self, users, departments, sessions, headed=None):
        self.users = users
        self.departments = departments
        self.sessions = sessions
        self.headed = headed or {}
        self.writes = []

    def get_user(self, id=None):
        return self.users.get(id)

    def get_headed_departments_for_user(self, user_id):
        return [{'id': dep} for dep in self.headed.get(user_id, [])]

    def get_user_department_id(self, user_id):
        return self.departments.get(user_id)

    def get_user_session(self, session_id=None, user_id=None):
        return self.sessions.get((session_id, user_id))

    def set_session_sensitive_access(self, **kwargs):
        self.writes.append(kwargs)
        return True


def _user(user_id, name, role, supervisor_id=None):
    """Кортеж users в том виде, в каком его отдаёт Database.get_user."""
    row = [None] * 20
    row[0], row[2], row[3] = user_id, name, role
    row[4] = 'СЗоВ'
    row[6] = supervisor_id
    row[7] = f'login{user_id}'
    return tuple(row)


class _Resolver:
    """Резолвер из монолита с подменённой базой.

    Импортировать bot_schedule2 нельзя — он на старте поднимает пул к боевой
    базе, поэтому функции берутся через ast (общий приём набора).
    """

    NAMES = ('_normalize_user_role', '_get_role_level', '_has_min_role',
             '_is_privileged_role', '_sensitive_access_approval_error',
             '_normalize_sensitive_qr_token', '_resolve_sensitive_qr_target')

    def __init__(self, db, claims=None, token_error=None):
        source, module = _bot_module()
        namespace = {
            'db': db,
            'logging': __import__('logging'),
            'SENSITIVE_QR_GATED_ROLES': frozenset(QR_GATED_ROLES),
            'urlparse': __import__('urllib.parse', fromlist=['urlparse']).urlparse,
            'parse_qs': __import__('urllib.parse', fromlist=['parse_qs']).parse_qs,
        }
        for name in ('ROLE_HIERARCHY', 'SENSITIVE_QR_TOKEN_MESSAGES',
                     'SENSITIVE_QR_TOKEN_FALLBACK_MESSAGE'):
            node = next(n for n in module.body
                        if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == name)
            exec(ast.get_source_segment(source, node), namespace)

        def _decode(token):
            if token_error:
                raise ValueError(token_error)
            return claims

        namespace['_decode_sensitive_qr_token'] = _decode
        for name in self.NAMES:
            node = next(n for n in module.body
                        if isinstance(n, ast.FunctionDef) and n.name == name)
            exec(textwrap.dedent(ast.get_source_segment(source, node)), namespace)
        self.call = namespace['_resolve_sensitive_qr_target']


class ResolvePerimeterTests(unittest.TestCase):
    """Кого показывает предпросмотр — тот же круг, что открывает подтверждение."""

    def _stand(self, approver_role='sv', approver_dept=SZOV, operator_dept=SZOV,
               operator_role='operator', revoked=False, headed=None):
        claims = {'session_id': 'sess-1', 'user_id': 20,
                  'expires_at': __import__('datetime').datetime(2026, 9, 10)}
        db = _FakeDb(
            users={10: _user(10, 'Подтверждающий', approver_role),
                   20: _user(20, 'Сотрудник', operator_role, supervisor_id=None)},
            departments={10: approver_dept, 20: operator_dept},
            sessions={('sess-1', 20): {'revoked_at': '2026-09-10' if revoked else None}},
            headed=headed,
        )
        return db, _Resolver(db, claims=claims).call

    def test_own_department_is_resolved(self):
        db, resolve = self._stand()
        context, error = resolve(10, 'OTP-SENSITIVE:whatever')
        self.assertIsNone(error)
        self.assertEqual(context['operator'][2], 'Сотрудник')
        self.assertEqual(db.writes, [], 'предпросмотр не имеет права ничего писать')

    def test_foreign_department_leaks_no_name(self):
        _db, resolve = self._stand(operator_dept=OP)
        context, error = resolve(10, 'OTP-SENSITIVE:whatever')
        self.assertIsNone(context, 'имя сотрудника чужого отдела не отдаём вовсе')
        self.assertEqual(error[1], 403)

    def test_revoked_session_is_refused(self):
        _db, resolve = self._stand(revoked=True)
        context, error = resolve(10, 'OTP-SENSITIVE:whatever')
        self.assertIsNone(context)
        self.assertEqual(error[1], 410)

    def test_role_outside_the_gate_is_refused(self):
        _db, resolve = self._stand(operator_role='sv')
        context, error = resolve(10, 'OTP-SENSITIVE:whatever')
        self.assertIsNone(context)
        self.assertEqual(error[1], 400)

    def test_stranger_cannot_even_look(self):
        db, _resolve = self._stand()
        db.users[10] = _user(10, 'Оператор', 'operator')
        resolve = _Resolver(db, claims={'session_id': 'sess-1', 'user_id': 20}).call
        context, error = resolve(10, 'OTP-SENSITIVE:whatever')
        self.assertIsNone(context)
        self.assertEqual(error[1], 403)

    def test_broken_token_speaks_russian(self):
        """Сообщение читает человек на экране подтверждения, а не разработчик."""
        db = _FakeDb(users={10: _user(10, 'Подтверждающий', 'admin')},
                     departments={}, sessions={})
        resolve = _Resolver(db, token_error='QR token expired').call
        context, error = resolve(10, 'что-то не то')
        self.assertIsNone(context)
        self.assertEqual(error[1], 400)
        self.assertNotIn('QR token', error[0])
        self.assertIn('истёк', error[0])

    def test_empty_code_is_refused_before_decoding(self):
        db = _FakeDb(users={10: _user(10, 'Подтверждающий', 'admin')},
                     departments={}, sessions={})
        context, error = _Resolver(db, token_error='не должно дойти').call(10, '   ')
        self.assertIsNone(context)
        self.assertEqual(error[1], 400)


class SharedResolverGuardTests(unittest.TestCase):
    """У предпросмотра и подтверждения НЕТ своих копий проверок."""

    OWN_COPIES = ('_decode_sensitive_qr_token', '_sensitive_access_approval_error',
                  'SENSITIVE_QR_GATED_ROLES', 'OTP-SENSITIVE')

    def _bodies(self):
        source, module = _bot_module()
        return {name: _function_body(source, module, name)
                for name in ('preview_sensitive_access_qr', 'approve_sensitive_access')}

    def test_both_routes_go_through_one_resolver(self):
        for name, body in self._bodies().items():
            self.assertIn('_resolve_sensitive_qr_target', body, name)
            for copied in self.OWN_COPIES:
                self.assertNotIn(copied, body, f'{name}: своя копия {copied}')

    def test_preview_writes_nothing(self):
        source, module = _bot_module()
        for name in ('preview_sensitive_access_qr', '_resolve_sensitive_qr_target'):
            self.assertNotIn('set_session_sensitive_access',
                             _function_body(source, module, name),
                             f'{name}: предпросмотр не выдаёт доступ')

    def test_the_guard_would_catch_a_local_copy(self):
        """Страж проверяется подделкой: молча зелёный страж хуже, чем никакой."""
        fake = textwrap.dedent('''
            def preview_sensitive_access_qr():
                """Общий резолвер: _resolve_sensitive_qr_target."""
                claims = _decode_sensitive_qr_token(token)
                return claims
        ''')
        module = ast.parse(fake)
        body = _function_body(fake, module, 'preview_sensitive_access_qr')
        self.assertNotIn('_resolve_sensitive_qr_target', body,
                         'докстринг не должен считаться за вызов')
        self.assertIn('_decode_sensitive_qr_token', body)


class SectionMarkupTests(unittest.TestCase):
    """Экран раздела — один компонент, а не две копии в монолите."""

    def test_app_has_no_scanner_markup_of_its_own(self):
        app = APP_PATH.read_text(encoding='utf-8-sig')
        for leftover in ('BarcodeDetector', 'qrScannerRunning', 'qrApproveInput',
                         'startQrScanner', 'getUserMedia'):
            self.assertNotIn(leftover, app, f'вёрстка сканера вернулась в App.jsx: {leftover}')

    def test_section_is_wired_into_both_menu_branches(self):
        """Меню раздела в App.jsx две ветки: администраторы и главы отделов.

        Компонент обязан стоять в обеих: подключённый в одну, он открывается
        второй половине людей только прямым адресом.
        """
        app = APP_PATH.read_text(encoding='utf-8-sig')
        self.assertEqual(app.count('<QrAccessView'), 2, 'раздел подключён не в обе ветки меню')
        self.assertEqual(app.count("{view === 'qr_access' && ("), 2)

    def test_confirmation_names_the_person(self):
        """Подтверждают человека. Строки токена на экране быть не должно."""
        view = VIEW_PATH.read_text(encoding='utf-8-sig')
        self.assertIn('operator_name', view)
        self.assertIn('avatar_url', view)
        self.assertNotIn('pendingTokenRef.current}', view,
                         'токен на экране ничего не говорит о том, кого пускают')

    def test_returning_does_not_switch_the_camera_on_by_itself(self):
        """Кто выключил камеру, тому её обратно не включают.

        Ловля кода тоже гасит камеру, поэтому отличить «выключил сам» от
        «выключилось на время вопроса» по одному признаку «сейчас не сканирует»
        нельзя. Возврат с экрана подтверждения смотрит на НАМЕРЕНИЕ; кнопка со
        словом «сканировать» включает всегда — её затем и нажимают.
        """
        view = VIEW_PATH.read_text(encoding='utf-8-sig')
        resume = view.split('const resumeScanning', 1)[1].split('}, [startScanner]);', 1)[0]
        again = view.split('const scanAgain', 1)[1].split('}, [startScanner]);', 1)[0]
        self.assertIn('if (cameraWantedRef.current) startScanner();', resume)
        self.assertNotIn('cameraWantedRef', again)
        self.assertIn('startScanner();', again)

    def test_camera_stops_when_the_section_is_left(self):
        """Камеру гасит размонтирование самого раздела.

        Прежний экран держал её выключение эффектом в App.jsx по смене view;
        уехав в компонент, разметка забрала это с собой — иначе камера осталась
        бы гореть на всём портале.
        """
        view = VIEW_PATH.read_text(encoding='utf-8-sig')
        module_tail = view.split('mountedRef.current = false;', 1)
        self.assertEqual(len(module_tail), 2, 'нет пометки размонтирования')
        self.assertIn('stopScanner();', module_tail[1].split('}, [stopScanner]);', 1)[0])


if __name__ == '__main__':
    unittest.main()

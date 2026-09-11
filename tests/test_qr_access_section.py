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
CSS_PATH = ROOT / 'src' / 'components' / 'qr' / 'qr-access.css'

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
                     'SENSITIVE_QR_TOKEN_FALLBACK_MESSAGE',
                     'SENSITIVE_QR_PREFIX', 'SENSITIVE_QR_LEGACY_PREFIX'):
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
                  'SENSITIVE_QR_GATED_ROLES', 'SENSITIVE_QR_PREFIX')

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

    def test_column_is_centred_against_the_window(self):
        """Колонка раздела стоит по центру ЭКРАНА, а не области контента.

        Слева от контента живёт сайдбар (80 px свёрнутый, 300 px развёрнутый),
        и одного `mx-auto` мало: на 1440 px колонка отставала от левого края
        окна на 424 px, а от правого — на 344, то есть заметно жалась вправо;
        с развёрнутым сайдбаром разрыв доходил до 534 против 234. То же боком
        на телефоне, где навигацию держит бар у боковой грани. Поле с
        ПРОТИВОПОЛОЖНОЙ стороны ровно возвращает середину окна.

        Поле обязано жить на ОТДЕЛЬНОЙ обёртке: у самой колонки свои `px-4` из
        утилит, и правило из файла раздела спорило бы с ними при равном весе,
        проигрывая (утилиты в бандле ниже) — см. [[component-css-loses-to-tailwind]].
        """
        view = VIEW_PATH.read_text(encoding='utf-8-sig')
        css = CSS_PATH.read_text(encoding='utf-8-sig')

        self.assertIn('<div className="qr-access-stage">', view)
        stage_line = next(l for l in view.splitlines() if 'qr-access-stage' in l)
        self.assertNotIn('px-', stage_line, 'поля колонки и компенсация — разные элементы')

        rule = css.split('.qr-access-stage {', 1)[1].split('}', 1)[0]
        self.assertIn('padding-right: var(--app-sidebar-offset', rule)
        # Сайдбар сворачивается с переходом — колонка обязана ехать вместе с ним.
        self.assertIn('transition: padding-right', rule)
        for side, prop in (('left', 'padding-right'), ('right', 'padding-left')):
            block = css.split(f'body[data-tabbar-side="{side}"] .qr-access-stage {{', 1)[1].split('}', 1)[0]
            self.assertIn(f'{prop}: var(--mtb-thickness', block,
                          f'бар у грани {side}: поле ставится не с той стороны')

    def test_scan_frame_is_centred_without_engine_guesswork(self):
        """Рамка сканирования ставится сама, без сетки и пропорции.

        Первый вариант (grid place-items-center + height в процентах +
        aspect-ratio) в Chrome вставал ровно по центру, а на живом iPhone
        уезжал к правому краю кадра: слева оставалось ~150 pt, справа — 5
        (владелец прислал снимок 10.09.2026). Правил портала, которые бы его
        двигали, нет — расходится сама связка: высота в процентах от дорожки,
        ширина из пропорции, место в дорожке сетки. Здесь ни одного из этих
        звеньев не осталось, поэтому сторожим именно их отсутствие.
        """
        view = VIEW_PATH.read_text(encoding='utf-8-sig')
        window = view.split('className="qr-window', 1)[1].split('>', 1)[0]
        self.view_transform = lambda: view.split('const frameTransform', 1)[1].split(';', 1)[0]

        self.assertIn("left: '50%'", window)
        self.assertIn("top: '50%'", window)
        # Перенос на полразмера с 11.09.2026 живёт в frameTransform: к нему
        # ДОБАВЛЯЕТСЯ перелёт на найденный код. Что он там остался и что его не
        # отменяют кадры анимации — сторожит ScanFrameHuntTests.
        self.assertIn('transform: frameTransform', window)
        self.assertIn("? `translate(-50%, -50%) translate(", self.view_transform())
        # Сторона — ОДНО значение на ширину и на нижнее поле: проценты у обоих
        # считаются от ширины родителя, поэтому стороны равны без пропорции.
        self.assertIn('width: frameSide', window)
        self.assertIn('paddingBottom: frameSide', window)
        self.assertNotIn('aspectRatio', window)
        # Рамка лежит в кадре напрямую, без промежуточной сетки-центровщика
        # (place-items-center у кружков с иконками к делу не относится).
        self.assertIn('className="qr-window pointer-events-none absolute', view)
        self.assertNotIn('grid place-items-center">\n', view.split('{scanning && (', 1)[1][:400])

    # Страж бегущего луча («едет через top, а не translateY») убран вместе с
    # самим лучом: владелец снял синюю полоску 11.09.2026. Ответ на вопрос «жива
    # ли камера» перешёл к подрагиванию рамки — его сторожит ScanFrameHuntTests.

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


class ScanFrameHuntTests(unittest.TestCase):
    """Рамка ищет код сама и садится на него (постановка владельца 11.09.2026).

    До этого рамка стояла посреди кадра неподвижно, а поймав код — просто
    исчезала вместе с камерой: сменялся экран, и по нему нельзя было сказать,
    ЧТО поймалось. Теперь она подрагивает, пока ищет, а найдя — перелетает на
    место кода в кадре, зеленеет и держит его долю секунды.

    Сторожатся три вещи, каждая из которых ломается молча — картинка остаётся
    правдоподобной, а рамка показывает не туда.
    """

    def setUp(self):
        self.view = VIEW_PATH.read_text(encoding='utf-8-sig')
        self.css = CSS_PATH.read_text(encoding='utf-8-sig')

    def _function(self, name, end='\n};\n'):
        return self.view.split(f'const {name} = ', 1)[1].split(end, 1)[0]

    def test_hunting_keeps_the_frame_centred(self):
        """Каждый кадр анимации несёт перенос на полразмера.

        Анимация в каскаде стоит ВЫШЕ inline-стиля, а место рамки задано именно
        им (left/top 50 % + translate(-50%, -50%)). Кадр, написанный без
        переноса, отменяет центровку на всё время анимации: рамка встаёт левым
        верхним углом в середину кадра и дёргается уже оттуда. Никакой ошибки
        при этом не видно — просто «сканер почему-то смещён».
        """
        frames = self.css.split('@keyframes qr-hunt {', 1)[1].split('\n}', 1)[0]
        moves = [line for line in frames.splitlines() if 'transform:' in line]
        self.assertGreaterEqual(len(moves), 4, 'рамка должна ходить, а не стоять')
        for line in moves:
            self.assertIn('translate(-50%, -50%)', line, f'кадр без центровки: {line.strip()}')

        binding = self.css.split('.qr-window[data-qr-state="hunting"] {', 1)[1].split('}', 1)[0]
        self.assertIn('animation: qr-hunt', binding)
        self.assertIn("data-qr-state={locked ? 'locked' : 'hunting'}", self.view)

    def test_landing_is_added_to_the_centring_and_not_instead_of_it(self):
        """Перелёт — ДОБАВОЧНЫЙ сдвиг, а не замена переноса.

        Размер рамке меняют width/padding-bottom, а не scale: затемнение вокруг
        рисует растянутая тень самой рамки, и scale сжал бы вместе с ней разгон
        тени — на мелком коде дальние углы кадра остались бы незатемнёнными.
        """
        transform = self.view.split('const frameTransform = locked', 1)[1].split(';', 1)[0]
        self.assertIn('translate(-50%, -50%) translate(', transform)
        self.assertNotIn('scale(', transform, 'размер рамки — не через scale')

        side = self.view.split('const frameSide = ', 1)[1].split(';', 1)[0]
        self.assertIn('lock.side', side)
        self.assertIn('px', side)

    def test_frame_lands_where_the_code_is(self):
        """Пересчёт «код в кадре → место на экране» идёт по object-cover.

        Картинка растянута по БОЛЬШЕМУ из двух отношений, лишнее срезано поровну
        с двух сторон. Взяв меньшее (object-contain), рамка садится мимо кода
        ровно на величину среза — на телефоне это половина ширины кадра, и
        выглядит это как «сканер показывает не туда», а не как ошибка расчёта.
        """
        body = self._function('lockOnBox')
        self.assertIn('const scale = Math.max(hostW / frameW, hostH / frameH);', body)
        self.assertIn('object-cover', self.view, 'кадр перестал быть object-cover')
        # Рамка не вылезает за кадр: затемнение рисует её же тень.
        self.assertIn('clamp(cropX', body)
        self.assertIn('clamp(cropY', body)

        # jsQR отдаёт углы, BarcodeDetector — прямоугольник; читаем оба.
        corners = self._function('boxOfJsQr')
        for name in ('topLeftCorner', 'topRightCorner', 'bottomRightCorner', 'bottomLeftCorner'):
            self.assertIn(name, corners)
        self.assertIn('hit?.boundingBox', self._function('boxOfDetected'))

    def test_the_code_is_caught_once_and_only_through_the_frame(self):
        """Разбор кадра зовёт sightCode, а не catchCode напрямую.

        Иначе карточка выезжает мгновенно, и посадка рамки не видна вовсе —
        ровно то поведение, ради ухода от которого всё и сделано. Сторож
        «ловим один раз» при этом обязан стоять В sightCode: без него каждый
        следующий тик интервала находил бы ту же бумажку заново и слал бы ещё
        один запрос предпросмотра.
        """
        start = self.view.split('const startScanner = useCallback', 1)[1]
        start = start.split('const toggleTorch', 1)[0]
        self.assertIn('sightCode(String(', start)
        self.assertNotIn('catchCode(String(', start)

        sight = self.view.split('const sightCode = useCallback', 1)[1].split('}, [catchCode]);', 1)[0]
        self.assertIn('if (caughtRef.current) return;', sight)
        self.assertIn('caughtRef.current = true;', sight)
        self.assertIn('LOCK_HOLD_MS', sight)

    def test_switching_the_camera_off_cancels_the_pending_card(self):
        """Выключил камеру, пока рамка садилась, — карточка не выезжает.

        Между «поймал» и вопросом «открыть доступ?» есть доли секунды. Не гаси
        сканер этот отсчёт, нажатие «выключить камеру» кончалось бы выехавшей
        поверх карточкой на чужой код — человек её не просил и уже не понимает,
        откуда она.
        """
        stop = self.view.split('const stopScanner = useCallback', 1)[1].split('}, []);', 1)[0]
        self.assertIn('clearTimeout(lockTimerRef.current)', stop)
        self.assertIn('lockTimerRef.current = null;', stop)


class ShortQrCodeTests(unittest.TestCase):
    """Код доступа короткий — и обязан таким остаться.

    Постановка владельца 11.09.2026: «укороти код, чтобы сканировалось с
    расстояния побольше». Длина строки в QR — не косметика: 235 знаков это QR
    версии 11 (61×61 модулей), 57 знаков — версии 3 (29×29). На экране телефона
    модуль во втором случае вдвое крупнее, и камера берёт код с двух метров
    вместо двадцати сантиметров (замерено на стенде: 44 px в кадре против 100).

    Ломается это молча: лишнее поле в теле кода, подпись целиком вместо
    усечённой, строчные буквы в приставке — всё это оставляет рабочий QR,
    который просто перестаёт читаться с расстояния. Поэтому здесь сторожится
    сама ДЛИНА и алфавит.
    """

    #: Знаки, которые QR умеет паковать «буквенно-цифровым» режимом — 5.5 бита
    #: вместо 8. Один знак вне набора переводит в побайтовый режим ВСЮ строку.
    QR_ALNUM = set('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:')
    #: Ёмкость QR версии 3 при уровне коррекции M. Следующая версия — это плюс
    #: четыре модуля стороны, то есть минус расстояние.
    QR_V3_M_CAPACITY = 61

    NAMES = ('_base64url_encode', '_base64url_decode', '_sensitive_qr_signature',
             '_build_sensitive_qr_token', '_decode_sensitive_qr_token',
             '_decode_legacy_sensitive_qr_token', '_normalize_sensitive_qr_token')

    def setUp(self):
        import base64 as base64_module
        import hashlib as hashlib_module
        import hmac as hmac_module
        import json as json_module
        import uuid as uuid_module
        from datetime import datetime, timedelta, timezone
        from urllib.parse import urlparse, parse_qs

        source, module = _bot_module()
        self.ns = {
            'base64': base64_module, 'hmac': hmac_module, 'hashlib': hashlib_module,
            'json': json_module, 'uuid': uuid_module, 'datetime': datetime,
            'timedelta': timedelta, 'timezone': timezone,
            'urlparse': urlparse, 'parse_qs': parse_qs,
            'SENSITIVE_QR_SECRET': 'secret-for-the-test',
            'SENSITIVE_QR_TTL_SECONDS': 300,
        }
        for name in ('SENSITIVE_QR_PREFIX', 'SENSITIVE_QR_LEGACY_PREFIX',
                     'SENSITIVE_QR_BODY_BYTES', 'SENSITIVE_QR_SIGNATURE_BYTES',
                     'SENSITIVE_QR_TOKEN_MESSAGES'):
            node = next(n for n in module.body
                        if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == name)
            exec(ast.get_source_segment(source, node), self.ns)
        for name in self.NAMES:
            node = next(n for n in module.body
                        if isinstance(n, ast.FunctionDef) and n.name == name)
            exec(textwrap.dedent(ast.get_source_segment(source, node)), self.ns)

        self.sid = '6f1d8c0a-2b4e-49f8-93a1-7e5c2d8b4a61'
        self.token, self.expires_at = self.ns['_build_sensitive_qr_token'](self.sid, 4127)
        self.payload = self.ns['SENSITIVE_QR_PREFIX'] + self.token

    def test_the_code_fits_the_smallest_practical_qr(self):
        self.assertLessEqual(
            len(self.payload), self.QR_V3_M_CAPACITY,
            f'код на {len(self.payload)} знаков не влезает в QR версии 3 — '
            'модули мельче, расстояние срабатывания меньше')
        self.assertTrue(
            set(self.payload) <= self.QR_ALNUM,
            'в коде есть знак вне буквенно-цифрового режима QR: строчная буква '
            'или дефис переводят в побайтовый режим ВСЮ строку')
        # Длина постоянна: тело фиксированное, base32 не зависит от значений.
        other, _ = self.ns['_build_sensitive_qr_token'](self.sid, 1)
        self.assertEqual(len(other), len(self.token))

    def test_the_code_reads_back_as_it_was_issued(self):
        claims = self.ns['_decode_sensitive_qr_token'](self.token)
        self.assertEqual(claims['session_id'], self.sid)
        self.assertEqual(claims['user_id'], 4127)
        self.assertLess(abs((claims['expires_at'] - self.expires_at).total_seconds()), 1)

    def test_the_code_is_read_the_same_way_it_is_shown(self):
        """С приставкой, ссылкой и строчными буквами — тот же ответ."""
        normalize = self.ns['_normalize_sensitive_qr_token']
        decode = self.ns['_decode_sensitive_qr_token']
        for name, raw in (
            ('со своей приставкой', self.payload),
            ('голым токеном', self.token),
            ('строчными буквами', self.token.lower()),
            ('ссылкой с кодом', f'https://portal.example/qr?token={self.token}'),
        ):
            with self.subTest(name):
                self.assertEqual(decode(normalize(raw))['session_id'], self.sid)

    def test_a_touched_code_is_refused(self):
        """Подпись усечена до 8 байт — проверяем, что она всё ещё подпись."""
        decode = self.ns['_decode_sensitive_qr_token']
        swapped = 'A' if self.token[10] != 'A' else 'B'
        for name, broken in (
            ('подменён знак', self.token[:10] + swapped + self.token[11:]),
            ('обрезан', self.token[:-2]),
            ('чужая строка', 'HELLOWORLD'),
            ('пусто', ''),
        ):
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    decode(broken)

    def test_an_expired_code_is_refused(self):
        self.ns['SENSITIVE_QR_TTL_SECONDS'] = -10
        stale, _ = self.ns['_build_sensitive_qr_token'](self.sid, 4127)
        with self.assertRaises(ValueError) as caught:
            self.ns['_decode_sensitive_qr_token'](stale)
        self.assertIn('expired', str(caught.exception))
        # Человеку на экране показывают перевод ЭТОГО текста. Разъехавшись с
        # ключом, он молча превращается в «Это не QR-код доступа портала», и
        # супервайзер вместо «попросите обновить QR» видит «код не наш».
        self.assertIn(str(caught.exception), self.ns['SENSITIVE_QR_TOKEN_MESSAGES'])

    def test_codes_issued_before_the_change_still_open_the_door(self):
        """Выкладка меняет процесс, а выданные коды живут ещё пять минут.

        Ветку старого разбора можно убрать следующей выкладкой — но не этой:
        иначе у всех, кто открыл окно с кодом за минуту до неё, подтверждение
        кончится «Это не QR-код доступа портала».
        """
        import hashlib as hashlib_module
        import hmac as hmac_module
        import json as json_module
        from datetime import datetime, timedelta, timezone

        body = json_module.dumps({
            'sid': self.sid, 'uid': 4127, 'nonce': 'a' * 32,
            'exp': int((datetime.now(timezone.utc) + timedelta(seconds=300)).timestamp()),
        }, separators=(',', ':'), sort_keys=True).encode('utf-8')
        payload_b64 = self.ns['_base64url_encode'](body)
        signature = hmac_module.new(self.ns['SENSITIVE_QR_SECRET'].encode('utf-8'),
                                    payload_b64.encode('utf-8'), hashlib_module.sha256).hexdigest()
        legacy = f'{self.ns["SENSITIVE_QR_LEGACY_PREFIX"]}{payload_b64}.{signature}'

        claims = self.ns['_decode_sensitive_qr_token'](
            self.ns['_normalize_sensitive_qr_token'](legacy))
        self.assertEqual(claims['user_id'], 4127)
        # И заодно: прежний код был вчетверо длиннее — ради этого всё и затеяно.
        self.assertGreater(len(legacy), 4 * len(self.payload))


class ScannerReachTests(unittest.TestCase):
    """Сканер должен видеть код с расстояния, а не в упор.

    Второе слагаемое той же постановки. Без требований к кадру браузер отдаёт
    то, что считает нужным (у Chrome это 640×480), и код на чужом экране
    занимает в кадре десятки пикселей: на стенде прежний код читался с 0,28 м,
    нынешний в кадре 1920 — с 1,9 м. Требование разрешения выглядит «лишней
    строчкой в настройках камеры» и снимается первым же упрощением.
    """

    def setUp(self):
        self.view = VIEW_PATH.read_text(encoding='utf-8-sig')
        self.app = APP_PATH.read_text(encoding='utf-8-sig')

    def test_the_scanner_asks_for_a_big_frame(self):
        block = self.view.split('const CAMERA_CONSTRAINTS = {', 1)[1].split('};', 1)[0]
        self.assertIn("facingMode: { ideal: 'environment' }", block)
        for side, least in (('width', 1280), ('height', 720)):
            value = block.split(f'{side}: {{ ideal: ', 1)[1].split(' }', 1)[0]
            self.assertGreaterEqual(int(value), least, f'{side} кадра просят мельче прежнего')
        # Требования должны и доезжать до камеры.
        self.assertIn('video: CAMERA_CONSTRAINTS', self.view)

    def test_the_employee_code_is_drawn_by_the_portal_itself(self):
        """Картинку кода рисуем у себя, а не заказываем на стороне.

        Здесь стоял api.qrserver.com, и туда уходил ДЕЙСТВУЮЩИЙ код доступа —
        вместе с тем, что без интернета сотруднику нечего показать. Библиотека
        грузится по требованию: окно с кодом открывает меньшинство.
        """
        self.assertNotIn('qrserver.com/v1/create-qr-code', self.app,
                         'код доступа снова уходит постороннему сервису')
        self.assertIn("import('qrcode')", self.app)
        self.assertIn("errorCorrectionLevel: 'M'", self.app)
        self.assertIn('margin: 2', self.app)


if __name__ == '__main__':
    unittest.main()

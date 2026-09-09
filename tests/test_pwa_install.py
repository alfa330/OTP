# -*- coding: utf-8 -*-
"""Страж установки портала на телефон (PWA).

Портал ставится на домашний экран средствами браузера: манифест
`public/manifest.webmanifest`, сервис-воркер `public/sw.js`, метки в index.html
и панель с предложением. Особенность этой связки в том, что ЛОМАЕТСЯ ОНА МОЛЧА:
браузер не жалуется ни на один неверный путь, ни на пропавшую метку — он просто
перестаёт предлагать установку, и узнать об этом можно только с телефона в
руках. Поэтому проверяется здесь то, что нельзя увидеть на глаз.

Четыре границы, за которыми правка перестаёт быть косметической.

  * ПУТИ. Фронт живёт на GitHub Pages в подкаталоге /OTP/, а на своём домене
    жил бы в корне. Внутри манифеста поэтому всё относительное (start_url,
    scope, иконки): абсолютный `/` увёл бы установленное приложение на корень
    домена, то есть на чужую страницу. В index.html — наоборот, от корня:
    относительный путь документ по адресу /OTP/wiki/... считал бы от своего
    каталога, а Vite подставляет base только в пути от корня.

  * ДОКУМЕНТ ИЗ СЕТИ. Портал уже наступал на устаревшую сборку (Pages иногда не
    доезжает, у людей остаётся старый бандл — для этого написан
    src/staleBundleRecovery.js). Воркер, отдающий index.html из кэша, сделал бы
    такое залипание постоянным и непробиваемым деплоем. Из кэша разрешено
    отдавать без вопросов ровно одно: сборочные ассеты с хэшем в имени.

  * БЕЗОПАСНЫЕ ЗОНЫ. env(safe-area-inset-*) в CSS работает только в паре с
    viewport-fit=cover в мете viewport. Без неё правила выглядят рабочими и не
    делают ничего: кнопка меню остаётся под часами телефона.

  * НЕПРОЗРАЧНАЯ ИКОНКА. Прозрачность в плитке домашнего экрана iOS заливает
    чёрным, и фирменный знак превращается в чёрный квадрат.

Интерфейсные решения проверяются чтением .jsx текстом — приём набора: React в
тестах не поднимается, а решение, записанное в разметке, обязано пережить
рефакторинг.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'public' / 'manifest.webmanifest'
SERVICE_WORKER = ROOT / 'public' / 'sw.js'
OFFLINE_PAGE = ROOT / 'public' / 'offline.html'
INDEX = ROOT / 'index.html'
MAIN = ROOT / 'src' / 'main.jsx'
STYLES = ROOT / 'src' / 'styles.css'
PWA_UTIL = ROOT / 'src' / 'utils' / 'pwa.js'
PROMPT = ROOT / 'src' / 'components' / 'common' / 'InstallAppPrompt.jsx'
MENU_ITEM = ROOT / 'src' / 'components' / 'common' / 'InstallAppMenuItem.jsx'
GUIDE = ROOT / 'src' / 'components' / 'common' / 'InstallGuideSheet.jsx'
APP_JSX = ROOT / 'src' / 'App.jsx'


def read(path):
    return path.read_text(encoding='utf-8-sig')


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(read(MANIFEST))

    def test_paths_are_relative_to_the_manifest(self):
        """Один манифест обязан работать и в корне, и в /OTP/ на Pages."""
        paths = [self.manifest['start_url'], self.manifest['scope']]
        paths += [icon['src'] for icon in self.manifest['icons']]
        for path in paths:
            self.assertFalse(
                path.startswith('/') or '://' in path,
                'Путь %r в манифесте абсолютный: установленное приложение '
                'открывало бы корень домена, а не портал' % path,
            )

    def test_declares_standalone_launch(self):
        """Без display: standalone значок открывает обычную вкладку."""
        self.assertEqual('standalone', self.manifest['display'])
        self.assertIn('name', self.manifest)
        self.assertIn('short_name', self.manifest)

    def test_has_both_sizes_chrome_requires(self):
        """Chrome предлагает установку, только имея 192 и 512."""
        sizes = {icon['sizes'] for icon in self.manifest['icons']}
        self.assertIn('192x192', sizes)
        self.assertIn('512x512', sizes)

    def test_icons_are_maskable(self):
        """Android обрезает иконку под форму темы; без maskable — белая рамка."""
        purposes = ' '.join(icon.get('purpose', '') for icon in self.manifest['icons'])
        self.assertIn('maskable', purposes)

    def test_icon_files_exist_and_match_declared_size(self):
        """Иконка не того размера молча выкидывается из списка кандидатов."""
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover — Pillow есть в requirements
            self.skipTest('Pillow недоступен')
        for icon in self.manifest['icons']:
            path = (MANIFEST.parent / icon['src']).resolve()
            self.assertTrue(path.exists(), 'Нет файла иконки: %s' % icon['src'])
            with Image.open(path) as image:
                declared = tuple(int(part) for part in icon['sizes'].split('x'))
                self.assertEqual(declared, image.size, 'Размер %s не совпал с заявленным' % icon['src'])
                self.assertNotIn(
                    'A', image.getbands(),
                    'Иконка %s с прозрачностью: на домашнем экране iOS прозрачное '
                    'заливается чёрным' % icon['src'],
                )

    def test_apple_touch_icon_exists(self):
        """iPhone берёт плитку не из манифеста, а из apple-touch-icon."""
        path = ROOT / 'public' / 'icons' / 'apple-touch-icon.png'
        self.assertTrue(path.exists())


class IndexHeadTests(unittest.TestCase):
    def setUp(self):
        self.html = read(INDEX)

    def test_manifest_link_is_root_absolute(self):
        """Vite подставляет base только в пути от корня."""
        self.assertIn('rel="manifest"', self.html)
        match = re.search(r'<link rel="manifest" href="([^"]+)"', self.html)
        self.assertIsNotNone(match, 'Пропала ссылка на манифест')
        self.assertTrue(
            match.group(1).startswith('/'),
            'Путь к манифесту относительный: со страницы /OTP/wiki/... он не найдётся',
        )

    def test_viewport_covers_the_display(self):
        """Без viewport-fit=cover весь блок safe-area в styles.css — пустой."""
        match = re.search(r'<meta name="viewport" content="([^"]+)"', self.html)
        self.assertIsNotNone(match)
        self.assertIn('viewport-fit=cover', match.group(1))

    def test_ios_launch_meta_present(self):
        """iOS запускает во весь экран по меткам, а не по манифесту."""
        self.assertIn('name="apple-mobile-web-app-capable"', self.html)
        self.assertIn('name="apple-mobile-web-app-title"', self.html)
        self.assertIn('rel="apple-touch-icon"', self.html)

    def test_theme_color_meta_present(self):
        """Строку состояния красит эта мета; её же подменяет тёмный режим."""
        self.assertIn('name="theme-color"', self.html)


class ServiceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.code = read(SERVICE_WORKER)

    def _function_body(self, name):
        start = self.code.index('const %s = ' % name)
        end = self.code.index('\n};', start)
        return self.code[start:end]

    def test_document_is_taken_from_the_network_first(self):
        """Кэш для документа — запасной выход, иначе старая сборка залипнет."""
        body = self._function_body('handleNavigation')
        network = body.index('fetchWithTimeout(')
        cache = body.index('caches.match(')
        self.assertLess(
            network, cache,
            'В handleNavigation кэш опрашивается раньше сети — так устаревшая '
            'сборка становится вечной',
        )

    def test_only_hashed_assets_are_cache_first(self):
        """Из кэша без вопросов — только файлы с хэшем в имени."""
        body = self._function_body('isBuildAsset')
        self.assertIn("'assets/'", body)

    def test_static_cache_is_a_closed_list(self):
        """«Всё своё происхождение» — это и прокси тайлов карты тоже.

        На локальном стенде Flask раздаёт с одного адреса фронт, API и
        /map/tile/... — тайлы вытеснили бы из конечного кэша сам портал.
        Синтаксис регулярного выражения здесь общий у JS и Python, поэтому
        проверяем не текст правила, а его поведение."""
        pattern = re.search(r'const PUBLIC_ASSET_RE = /(.+)/;', self.code).group(1)
        matcher = re.compile(pattern)
        self.assertIn('if (isPublicAsset(url))', self.code)
        self.assertTrue(matcher.search('/OTP/icons/icon-192.png'))
        self.assertTrue(matcher.search('/OTP/favicon.ico'))
        self.assertFalse(matcher.search('/map/tile/12/3/4.png'))
        self.assertFalse(matcher.search('/OTP/api/schedule'))

    def test_api_and_foreign_origins_never_cached(self):
        """Кэшированный ответ API — это показанные не те данные."""
        self.assertIn('url.origin !== self.location.origin', self.code)
        self.assertIn("SCOPE_PATH + 'api/'", self.code)

    def test_mutating_requests_pass_through(self):
        self.assertIn("request.method !== 'GET'", self.code)

    def test_ranged_requests_pass_through(self):
        """Записи разговоров тянутся кусками: кэш ломает перемотку."""
        self.assertIn("request.headers.has('range')", self.code)

    def test_scope_is_taken_from_registration(self):
        """Жёсткий '/' сломал бы воркер на Pages, где портал живёт в /OTP/."""
        self.assertIn('new URL(self.registration.scope).pathname', self.code)

    def test_new_version_takes_over_immediately(self):
        self.assertIn('skipWaiting()', self.code)
        self.assertIn('clients.claim()', self.code)

    def test_old_caches_are_dropped_on_activate(self):
        """Без уборки кэш растёт с каждой публикацией до конца жизни телефона."""
        self.assertIn('caches.delete(name)', self.code)
        self.assertIn('ASSET_CACHE_LIMIT', self.code)

    def test_offline_page_is_precached(self):
        """Страница «нет связи» нужна ровно тогда, когда её уже не скачать."""
        self.assertTrue(OFFLINE_PAGE.exists())
        self.assertIn('OFFLINE_URL', self.code)

    def test_offline_page_has_no_external_assets(self):
        """В офлайне не загрузится ничего, чего нет в самой странице.

        Ищем именно ЗАГРУЗКИ: адрес пространства имён SVG
        (http://www.w3.org/2000/svg) браузер никуда не запрашивает, и запрет на
        любое вхождение «http» ловил бы его ложно."""
        html = read(OFFLINE_PAGE)
        loads = re.findall(r'(?:src|href)="(?!#)([^"]+)"', html)
        loads += re.findall(r'url\(([^)]+)\)', html)
        loads += re.findall(r'@import\s+[\'"]([^\'"]+)', html)
        self.assertEqual([], loads, 'Заглушка тянет внешние файлы: %s' % loads)


class RuntimeWiringTests(unittest.TestCase):
    def test_worker_registered_only_in_build(self):
        """На разработке воркер отдавал бы кэш вместо свежей сборки Vite."""
        main = read(MAIN)
        self.assertIn('startPwaRuntime(', main)
        self.assertIn('withServiceWorker: import.meta.env.PROD', main)

    def test_worker_scope_follows_build_base(self):
        util = read(PWA_UTIL)
        self.assertIn("scope + 'sw.js'", util)
        self.assertIn('{ scope }', util)

    def test_runtime_starts_before_render(self):
        """`beforeinstallprompt` приходит раньше, чем появится дерево React."""
        main = read(MAIN)
        self.assertLess(
            main.index('startPwaRuntime('),
            main.index('createRoot('),
            'Подписка ставится после отрисовки — событие установки будет пропущено',
        )

    def test_chrome_mini_bar_is_suppressed(self):
        """Без preventDefault Chrome покажет свою полосу поверх нашей панели."""
        util = read(PWA_UTIL)
        self.assertIn('event.preventDefault()', util)

    def test_deferred_event_is_used_once(self):
        """Повторный prompt() по тому же событию падает с InvalidStateError."""
        util = read(PWA_UTIL)
        prompt_body = util[util.index('export const promptInstall'):]
        self.assertLess(
            prompt_body.index('deferredPrompt = null'),
            prompt_body.index('event.prompt()'),
            'Ссылка на событие снимается после вызова — вторая кнопка упадёт',
        )


class InstallPromptTests(unittest.TestCase):
    def setUp(self):
        self.jsx = read(PROMPT)

    def test_panel_is_a_sibling_of_the_assistant_orb(self):
        """Внутри main-content position: fixed считался бы от чужого предка."""
        app = read(APP_JSX)
        self.assertIn('<InstallAppPrompt />', app)
        # Панелей две: на экране входа (он уходит ранним return выше) и в самом
        # портале. Соседство с шариком проверяется у ВТОРОЙ.
        self.assertLess(
            app.index('<AssistantOrb'),
            app.rindex('<InstallAppPrompt />'),
            'Панель уехала из соседей шарика — проверьте, что она не внутри '
            'разделов с overflow-hidden и zoom',
        )

    def test_layer_is_between_the_orb_and_the_modals(self):
        """Полноэкранные окна обязаны накрывать предложение, шарик — нет."""
        match = re.search(r'z-\[(\d+)\]', self.jsx)
        self.assertIsNotNone(match, 'У панели пропал слой')
        layer = int(match.group(1))
        self.assertGreater(layer, 84, 'Панель ушла под шарик помощника')
        self.assertLess(layer, 100, 'Панель накрыла бы «Новость дня» и карточку задачи')

    def test_panel_waits_before_showing(self):
        """Панель в первую секунду закрывают не читая."""
        self.assertIn('APPEAR_DELAY_MS', self.jsx)
        delay = int(re.search(r'APPEAR_DELAY_MS = (\d+)', self.jsx).group(1))
        self.assertGreaterEqual(delay, 5000)

    def test_install_button_only_where_it_can_open_something(self):
        """Кнопки установки на iOS не существует: обещать её нельзя.

        Панель рисует «Установить» ровно при `canInstallHere`, и это условие —
        главное во всём компоненте: кнопка, которая на iPhone не открывает
        ничего, читается как сломанный портал."""
        self.assertIn(
            "const canInstallHere = install.platform !== 'ios' && install.canPrompt;",
            self.jsx,
        )
        # Якорь — сама кнопка (по обработчику), а не слово «Установить»: оно
        # встречается и в комментариях, объясняющих, почему её нет на iPhone.
        button = self.jsx.index('onClick={handleInstall}\n                                disabled={pending}')
        guard = self.jsx.index('{canInstallHere && (')
        self.assertLess(guard, button, 'Кнопка «Установить» вышла из-под условия')

    def test_panel_shows_the_steps_and_the_details_button(self):
        """Панель читается за две секунды: шаги строкой и «Подробнее»."""
        self.assertIn('Подробнее', self.jsx)
        self.assertIn('Поделиться', self.jsx)
        self.assertIn('на главный экран', self.jsx)

    def test_panel_reports_its_height_to_the_page(self):
        """Экран входа поднимает форму на высоту панели, а не на константу."""
        self.assertIn("OFFER_HEIGHT_VAR = '--install-offer-height'", self.jsx)
        self.assertIn('ResizeObserver', self.jsx)

    def test_dismiss_is_a_snooze(self):
        """«Позже» — отсрочка на две недели, а не отказ навсегда."""
        self.assertIn('snoozeInstallOffer', self.jsx)
        util = read(PWA_UTIL)
        self.assertIn('INSTALL_SNOOZE_MS = 14 * 24 * 60 * 60 * 1000', util)

    def test_menu_item_subscribes_itself(self):
        """Пункт живёт в мемоизированном дереве сайдбара: проп бы замёрз."""
        menu = read(MENU_ITEM)
        self.assertIn('subscribeToInstallState', menu)
        self.assertIn('Установить приложение', menu)
        self.assertIn('<InstallAppMenuItem', read(APP_JSX))


class InstallGuideTests(unittest.TestCase):
    def setUp(self):
        self.jsx = read(GUIDE)

    def test_both_systems_are_switchable(self):
        """Инструкцию открывают, чтобы объяснить коллеге с другим телефоном."""
        self.assertIn("'ios'", self.jsx)
        self.assertIn("'android'", self.jsx)
        self.assertIn('iPhone', self.jsx)
        self.assertIn('Android', self.jsx)

    def test_ios_warns_that_only_safari_can_install(self):
        """В Chrome и внутри Telegram пункта «На экран „Домой“» нет вовсе."""
        self.assertIn('Safari', self.jsx)

    def test_ios_warns_about_the_hidden_menu_item(self):
        """Пункт лежит ниже видимой части меню — без этого шага люди сдаются."""
        self.assertIn('Пролистайте', self.jsx)

    def test_android_step_matches_the_button_on_screen(self):
        """«Нажмите „Установить“ ниже» под пустым местом — худшая инструкция."""
        self.assertIn('const androidSteps = (hasInstallButton) =>', self.jsx)
        self.assertIn('три точки', self.jsx)

    def test_guide_layer_is_above_the_offer_panel(self):
        """Инструкция открывается ИЗ панели и обязана лечь поверх неё."""
        layers = [int(value) for value in re.findall(r'z-\[(\d+)\]', self.jsx)]
        self.assertTrue(layers, 'У инструкции пропал слой')
        panel_layer = int(re.search(r'z-\[(\d+)\]', read(PROMPT)).group(1))
        self.assertGreater(min(layers), panel_layer)
        self.assertLess(max(layers), 100, 'Инструкция накрыла бы полноэкранные окна')


class AuthScreenTests(unittest.TestCase):
    def test_login_screen_offers_installation_too(self):
        """С домашнего экрана человек попадает сразу сюда."""
        app = read(APP_JSX)
        self.assertEqual(2, app.count('<InstallAppPrompt />'),
                         'Панель должна стоять и на экране входа, и в портале')

    def test_login_screen_lifts_above_the_panel(self):
        """Иначе панель накрывает кнопку «Войти» — форма центрована."""
        css = read(STYLES)
        self.assertIn('body.has-install-offer .auth-screen', css)
        self.assertIn('var(--install-offer-height', css)

    def test_auth_screens_keep_away_from_the_edges_and_the_notch(self):
        css = read(STYLES)
        block = css[css.index('.auth-screen {'):]
        block = block[:block.index('}')]
        for side in ('top', 'right', 'bottom', 'left'):
            self.assertIn('env(safe-area-inset-%s)' % side, block)

    def test_login_inputs_are_mobile_safe(self):
        """Автозаглавная буква и автозамена ломают ввод логина на телефоне."""
        app = read(APP_JSX)
        login_input = app[app.index('placeholder="Логин"'):]
        login_input = login_input[:login_input.index('/>')]
        self.assertIn('autoCapitalize="none"', login_input)
        self.assertIn('autoCorrect="off"', login_input)


class SafeAreaTests(unittest.TestCase):
    def setUp(self):
        self.css = read(STYLES)

    def test_safe_area_rules_are_locked_to_standalone(self):
        """Во вкладке те же отступы стали бы двойными: там панели браузера."""
        block = self.css[self.css.index('@media (display-mode: standalone)'):]
        block = block[:block.index('\n    }\n\n')]
        self.assertIn('env(safe-area-inset-top)', block)
        self.assertIn('env(safe-area-inset-bottom)', block)
        self.assertIn('.hamburger-btn', block)

    def test_bottom_inset_is_not_on_body(self):
        """padding-bottom на body добавился бы к 100vh — вечная полоса прокрутки."""
        block = self.css[self.css.index('@media (display-mode: standalone)'):]
        block = block[:block.index('\n    }\n\n')]
        body_rule = re.search(r'\bbody\s*\{[^}]*padding-bottom', block)
        self.assertIsNone(body_rule, 'Нижний отступ повешен на body')


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""Страж моторики мобильной оболочки: как на телефоне движется всё остальное.

Просьба владельца 11.09.2026: «переходы и все анимации проверь, сделай их
плавными вместо таких резких, например при свайпе назад или при кнопке, сделай
все в стиле ios/macos. Применить только к мобильной версии».

ПОЧЕМУ ЭТО СТЕРЕЖЁТ PYTHON, А НЕ ГЛАЗ. Моторика не видна в диффе и не видна на
рабочем месте: за широким монитором оболочка выключена целиком, а на телефоне
пропавший уход экрана выглядит не как ошибка, а как «ну, так работает». Числа
же тут связаны попарно через границу языков — длительность ухода живёт
одновременно в CSS, в React-примитиве и в наблюдателе за DOM, и разойтись им
достаточно один раз.

Границы, за которыми правка перестаёт быть косметической.

  * ПОРЯДОК ПОДКЛЮЧЕНИЯ. mobile-motion.css перекрывает длительности и кривые
    оболочки при РАВНОМ весе селектора. Уехав выше mobile-shell.css в графе
    импортов, он замолкает весь и молча: стили соберутся, ошибок не будет,
    просто движение останется прежним.

  * ОДНА ДЛИТЕЛЬНОСТЬ УХОДА НА ТРИ МЕСТА. Разметку на время ухода держат
    SCREEN_LEAVE_MS (ios.jsx) и SCREEN_EXIT_MS (mobileScreenExit.js), а едет
    экран по анимации из стилей. Число меньше — движение обрубается на
    середине, больше — на экране застывает уже закрытое окно.

  * СОДЕРЖИМОЕ РАЗДЕЛА НЕЛЬЗЯ ДВИГАТЬ. Окна портала свёрстаны внутри разделов
    (position: fixed внутри .main-content), а transform у предка делает его
    точкой отсчёта для fixed-потомков. Любой параллакс, сдвигающий
    .main-content, утащил бы за собой сами окна — и растянул бы их на всю
    высоту страницы, потому что на телефоне прокрутка страничная.

  * ПРОВОЖАЕМ ТОЛЬКО КОРЕНЬ ЭКРАНА. Наблюдатель возвращает на место снятый
    узел; вернув вместо корня окна кусок раздела, он показал бы на треть
    секунды двойник половины страницы.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MOTION_CSS = (ROOT / 'src' / 'components' / 'common' / 'mobile-motion.css').read_text(encoding='utf-8')
SHELL_CSS = (ROOT / 'src' / 'components' / 'common' / 'mobile-shell.css').read_text(encoding='utf-8')
TAB_BAR = (ROOT / 'src' / 'components' / 'common' / 'MobileTabBar.jsx').read_text(encoding='utf-8')
SCREEN_EXIT = (ROOT / 'src' / 'utils' / 'mobileScreenExit.js').read_text(encoding='utf-8')
IOS = (ROOT / 'src' / 'components' / 'ui' / 'ios.jsx').read_text(encoding='utf-8')


def split_selectors(selector):
    """Селекторы списка по запятым — не трогая запятые внутри :is(...)."""
    parts, depth, current = [], 0, ''
    for ch in selector:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(current.strip())
            current = ''
            continue
        current += ch
    parts.append(current.strip())
    return [p for p in parts if p]


def rules(source):
    """Селекторы всех правил файла — без @media, комментариев и keyframes."""
    text = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    text = re.sub(r'@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}', '', text, flags=re.S)
    out = []
    for chunk in re.finditer(r'([^{}]+)\{([^{}]*)\}', text):
        selector = chunk.group(1).strip()
        if selector.startswith('@') or not selector:
            continue
        out.append((selector, chunk.group(2)))
    return out


class MotionLayerTests(unittest.TestCase):
    """Слой заперт на телефоне и подключён после вёрстки оболочки."""

    def test_every_rule_is_locked_to_the_shell(self):
        # Настольный вид не должен измениться ни на кадр: владелец просил
        # «применить только к мобильной версии» — это его формулировка, и она
        # повторяется в задачах про оболочку с 10.09.2026.
        for selector, _ in rules(MOTION_CSS):
            for part in split_selectors(selector):
                self.assertTrue(
                    part.startswith('body.mobile-shell'),
                    f'правило моторики не заперто на оболочке: {part}',
                )

    def test_motion_is_imported_after_the_shell(self):
        shell_at = TAB_BAR.index("import './mobile-shell.css'")
        motion_at = TAB_BAR.index("import './mobile-motion.css'")
        self.assertLess(shell_at, motion_at, 'моторика обязана идти после вёрстки оболочки')

    def test_curves_and_durations_have_names(self):
        # Токены объявлены один раз и дальше используются только по имени:
        # разъехавшиеся числа в разных концах файла и дают «вразнобой».
        for token in ('--ios-move', '--ios-soft', '--ios-exit', '--ios-press',
                      '--ios-screen-in', '--ios-screen-out', '--ios-sheet-in',
                      '--ios-sheet-out', '--ios-view', '--ios-press-up', '--ios-press-down'):
            self.assertIn(f'{token}:', MOTION_CSS, f'нет токена {token}')

        body = MOTION_CSS[MOTION_CSS.index('--ios-press-down'):]
        self.assertNotIn('cubic-bezier', body, 'кривая записана числом мимо токенов')

    def test_reduced_motion_keeps_the_answer_to_a_tap(self):
        block = MOTION_CSS[MOTION_CSS.index('@media (prefers-reduced-motion: reduce)'):]
        # Отменяются поездки…
        self.assertIn('.otp-modal-root', block)
        self.assertIn('.mobile-view-switch', block)
        self.assertIn('.animate-dropdown', block)
        # …а отклик на нажатие остаётся: человек просил убрать движение, а не
        # лишить интерфейс ответа.
        self.assertNotIn('opacity: 1', block)


class ScreenDepthTests(unittest.TestCase):
    """Глубина у экрана есть, а содержимое раздела при этом не двигается."""

    def test_section_under_the_screen_is_never_moved(self):
        # Ловушка, из-за которой параллакса здесь нет вовсе: fixed-потомок
        # внутри сдвинутого предка считается ОТ ПРЕДКА.
        for selector, body in rules(MOTION_CSS):
            if '.main-content' not in selector:
                continue
            self.assertNotIn('transform:', body, f'содержимое раздела сдвигать нельзя: {selector}')
        # И причина записана рядом с решением, а не только здесь.
        self.assertIn('ПАРАЛЛАКС', MOTION_CSS)

    def test_dim_lives_under_the_screen_and_leaves_with_it(self):
        self.assertIn('body.mobile-shell::before', MOTION_CSS)
        self.assertIn('body.mobile-shell:has(.otp-modal-root:not(.is-leaving))::before', MOTION_CSS)
        # Слой обязан быть ровно под экраном: у экрана 120 (mobile-shell.css).
        dim = MOTION_CSS[MOTION_CSS.index('body.mobile-shell::before'):]
        self.assertIn('z-index: 119', dim)
        self.assertIn('z-index: 120', SHELL_CSS)

    def test_screen_moves_once(self):
        # Полотно окна не имеет права ехать вместе с экраном: два движения в
        # одном кадре — это и есть дёрганость (та же причина, по которой
        # animation сняли у .otp-modal-card 11.09.2026).
        panel = MOTION_CSS[MOTION_CSS.index('.otp-modal-root .otp-modal-panel'):]
        self.assertIn('animation: none !important', panel.split('}')[0])
        self.assertIn('animation: none !important', SHELL_CSS[SHELL_CSS.index('.otp-modal-card'):])


class ScreenExitTests(unittest.TestCase):
    """Уход экрана: один на всех, и его длительность — одно число."""

    def test_leave_duration_is_the_same_number_everywhere(self):
        js = int(re.search(r'SCREEN_LEAVE_MS = (\d+)', IOS).group(1))
        exit_ms = int(re.search(r'SCREEN_EXIT_MS = (\d+)', SCREEN_EXIT).group(1))
        css = re.search(r'--ios-screen-out:\s*([\d.]+)s', MOTION_CSS).group(1)
        self.assertEqual(js, exit_ms)
        self.assertEqual(js / 1000, float(css))
        self.assertIn('animation: otp-screen-out var(--ios-screen-out)', MOTION_CSS)

    def test_only_the_screen_root_is_sent_off(self):
        # Вернуть на место кусок раздела вместо корня окна — показать двойник
        # половины страницы.
        self.assertIn('if (!isScreenRoot(node)) continue;', SCREEN_EXIT)
        self.assertIn("classList?.contains(ROOT_CLASS)", SCREEN_EXIT)

    def test_windows_with_their_own_exit_are_left_alone(self):
        # IosModal и SimpleModal уходят сами, с уже поставленной меткой.
        self.assertIn('if (node.classList.contains(LEAVING_CLASS)) continue;', SCREEN_EXIT)
        self.assertIn('is-leaving', IOS)

    def test_recreated_screen_is_not_sent_off(self):
        # React пересоздал узел (смена ключа) — старый уехал бы поверх нового.
        self.assertIn('replaced', SCREEN_EXIT)
        self.assertIn('if (replaced?.has(record.target)) continue;', SCREEN_EXIT)

    def test_leaving_screen_catches_nothing(self):
        self.assertIn("setAttribute('inert'", SCREEN_EXIT)
        self.assertIn("setAttribute('aria-hidden', 'true')", SCREEN_EXIT)
        leaving = MOTION_CSS[MOTION_CSS.index('.otp-modal-root.is-leaving'):]
        self.assertIn('pointer-events: none', leaving)

    def test_ghost_is_always_removed(self):
        self.assertIn('SCREEN_EXIT_MS + CLEANUP_SLACK_MS', SCREEN_EXIT)
        self.assertIn('node.remove()', SCREEN_EXIT)
        # Выключение оболочки снимает и наблюдателя, и незавершённые таймеры.
        self.assertIn('clearTimeout(timer)', SCREEN_EXIT)

    def test_observer_is_mobile_only(self):
        self.assertIn('startMobileScreenExit', TAB_BAR)
        hook = TAB_BAR[TAB_BAR.index('startMobileScreenExit(document)') - 400:]
        self.assertIn('if (!state.shell', hook)
        # И внутри — вторая проверка: класс мог сняться между кадрами.
        self.assertIn("classList.contains('mobile-shell')", SCREEN_EXIT)

    def test_calm_setting_skips_the_send_off(self):
        self.assertIn('prefers-reduced-motion: reduce', SCREEN_EXIT)
        self.assertIn('if (calm?.matches) return;', SCREEN_EXIT)


class PressTests(unittest.TestCase):
    """Отклик на нажатие: вниз мгновенно, вверх мягко."""

    def test_press_has_two_durations(self):
        self.assertIn('--ios-press-down: 0.09s', MOTION_CSS)
        self.assertIn('--ios-press-up: 0.32s', MOTION_CSS)
        down = MOTION_CSS.index('transition-duration: var(--ios-press-down)')
        up = MOTION_CSS.index('transition-duration: var(--ios-press-up)')
        self.assertLess(up, down, 'быстрое нажатие обязано стоять ПОСЛЕ мягкого возврата')

    def test_answer_replaces_the_grey_box(self):
        # Серый прямоугольник браузера снят — значит свой отклик обязателен,
        # иначе нажатие остаётся вовсе без ответа.
        self.assertIn('-webkit-tap-highlight-color: transparent', MOTION_CSS)
        self.assertIn('opacity: 0.58', MOTION_CSS)

    def test_disabled_controls_do_not_answer(self):
        press = MOTION_CSS[MOTION_CSS.index('opacity: 0.58') - 1200:MOTION_CSS.index('opacity: 0.58')]
        self.assertIn(':not(:disabled)', press)
        self.assertIn(':not([aria-disabled="true"])', press)

    def test_places_with_their_own_answer_are_left_alone(self):
        press = MOTION_CSS[MOTION_CSS.index('opacity: 0.58') - 1200:MOTION_CSS.index('opacity: 0.58')]
        # Экраны учётной записи и кадрирование красят строку сами.
        self.assertIn('mobile-account__', press)
        self.assertIn('mobile-crop__', press)
        # Бар разделов в область правила не входит вовсе.
        self.assertNotIn('.mtb-item', press)

    def test_authors_own_timings_are_respected(self):
        # duration-* в разметке — осознанный выбор автора места (полоса
        # прогресса, задержанная подсказка); переписывать его отсюда нельзя.
        self.assertIn(':not([class*="duration-"])', MOTION_CSS)
        # И набор свойств у тех, кто уже описал переход утилитой Tailwind.
        self.assertIn(':not([class*="transition"])', MOTION_CSS)


class ViewSwitchTests(unittest.TestCase):
    """Переключение раздела — растворение, а не появление из пустоты."""

    def test_switch_dissolves_instead_of_flying_up(self):
        self.assertIn('animation: ios-view-in var(--ios-view)', MOTION_CSS)
        frames = MOTION_CSS[MOTION_CSS.index('@keyframes ios-view-in'):]
        frames = frames[:frames.index('}\n')]
        self.assertIn('opacity: 0.4', frames, 'начало не с нуля: с нуля это снова «появилось из пустоты»')
        self.assertNotIn('translate', frames, 'поездка содержимого раздела запрещена')

    def test_dropdowns_open_like_a_phone(self):
        # Общая утилита — сжатие по вертикали: на всю ширину экрана оно
        # выглядит гармошкой, и буквы внутри едут вместе с ней.
        self.assertIn('animation: ios-drop-in', MOTION_CSS)
        self.assertIn('transform-origin: top center', MOTION_CSS)
        drop = MOTION_CSS[MOTION_CSS.index('@keyframes ios-drop-in'):]
        drop = drop[:drop.index('}\n')]
        self.assertNotIn('scaleY', drop, 'гармошка по вертикали — ровно то, что здесь заменяется')


if __name__ == '__main__':
    unittest.main()

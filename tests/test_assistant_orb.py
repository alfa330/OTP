# -*- coding: utf-8 -*-
"""Плавающий помощник: шарик поверх портала и мини-чат из него.

База знаний лежит в разделе «Вики», а вопросы к ней возникают в других разделах.
Шарик даёт того же помощника, не уводя человека с экрана, — и ровно поэтому он
приносит с собой два новых класса ошибок, которых у вкладки не было.

ПЕРВЫЙ — ПРОСТРАНСТВО. Вкладка присылала то пространство, которое сама же и
открыла, и сервер ему верил. Шарик берёт последний выбор из localStorage: этот
выбор мог устареть, доступ к тому пространству могли отозвать, а тумблер
«Помощник» в нём — выключить. Отсюда `effective_space`, и главное в нём —
что непонятное значение заменяется ПЕРВЫМ ДОСТУПНЫМ, а не None: None означает
«отвечать по объединению всех пространств», и человек, спросив про «Тез»,
получил бы абзац из «Таксопарков» без всякого признака, что база знаний другая.

ВТОРОЙ — СЛОЙ. Виджет висит поверх ВСЕГО портала, и разъехаться с чужими
модалками ему нечем, кроме z-index. Слой 84 выбран так, чтобы всё
полноэкранное перекрывало шарик само, без реестра «открыт оверлей»; проверяется
он здесь, потому что поднять его на 120 «чтобы было видно» — правка на одну
цифру, а стоит она перекрытого окна «Новость дня».

Интерфейсные решения проверяются чтением .jsx текстом — приём набора: React в
тестах не поднимается, а решение, записанное в разметке, обязано пережить
рефакторинг.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wiki import routes_ai  # noqa: E402

APP = ROOT / 'src' / 'App.jsx'
ORB = ROOT / 'src' / 'components' / 'assistant' / 'AssistantOrb.jsx'
PANEL = ROOT / 'src' / 'components' / 'assistant' / 'AssistantPanel.jsx'
THREAD = ROOT / 'src' / 'components' / 'assistant' / 'assistantThread.jsx'
CSS = ROOT / 'src' / 'components' / 'assistant' / 'assistant-orb.css'
DARK_SCRIPT = ROOT / 'scripts' / 'build_dark_theme.py'


class FakeCursor:
    """Курсор, который ничего не делает: запросы уводятся заглушками."""


def _ctx():
    return {'user_id': 1, 'capabilities': {}, 'otp_role': 'operator'}


class EffectiveSpaceTests(unittest.TestCase):
    """Какое пространство помощник выберет на самом деле."""

    def setUp(self):
        self._spaces_for_user = routes_ai.queries.spaces_for_user
        self._list_spaces = routes_ai.structure.list_spaces

    def tearDown(self):
        routes_ai.queries.spaces_for_user = self._spaces_for_user
        routes_ai.structure.list_spaces = self._list_spaces

    def _arrange(self, allowed, spaces):
        routes_ai.queries.spaces_for_user = lambda cursor, ctx, **kw: allowed
        routes_ai.structure.list_spaces = lambda cursor, **kw: spaces

    def test_доступное_пространство_остаётся_как_просили(self):
        self._arrange([7, 9], [
            {'id': 7, 'name': 'Тез', 'features': {}},
            {'id': 9, 'name': 'Таксопарки', 'features': {}},
        ])
        self.assertEqual(routes_ai.effective_space(FakeCursor(), _ctx(), 9), 9)

    def test_чужое_пространство_заменяется_первым_а_не_объединением(self):
        """Главная проверка файла.

        Запомненный в браузере номер может указывать на пространство, доступ к
        которому отозвали. Соблазн «не понял — не сужаю» даёт None, а None в
        perimeter.read_perimeter означает «искать по ВСЕМ пространствам сразу»:
        ответ соберётся из двух баз знаний, и по нему не будет видно, что часть
        абзацев из чужой вики.
        """
        self._arrange([7], [
            {'id': 7, 'name': 'Тез', 'features': {}},
            {'id': 9, 'name': 'Чужая вика', 'features': {}},
        ])
        self.assertEqual(routes_ai.effective_space(FakeCursor(), _ctx(), 9), 7)
        self.assertIsNotNone(routes_ai.effective_space(FakeCursor(), _ctx(), 9))

    def test_пространство_без_помощника_не_предлагается(self):
        """Тумблер features.assistant — решение владельца, а не украшение.

        У клиентской вики (Тез КЦ) помощник выключен намеренно. Пока он жил во
        вкладке, решение держалось витриной; шарик приходит в пространство не
        через вкладку и вернул бы выключенного помощника через окно.
        """
        self._arrange([7, 9], [
            {'id': 7, 'name': 'Тез КЦ', 'features': {'assistant': False}},
            {'id': 9, 'name': 'Таксопарки', 'features': {}},
        ])
        self.assertEqual(routes_ai.effective_space(FakeCursor(), _ctx(), 7), 9)
        names = [sp['name'] for sp in routes_ai.assistant_spaces(FakeCursor(), _ctx())]
        self.assertEqual(names, ['Таксопарки'])

    def test_без_пространств_сужать_нечем(self):
        self._arrange([], [{'id': 7, 'name': 'Тез', 'features': {}}])
        self.assertIsNone(routes_ai.effective_space(FakeCursor(), _ctx(), 7))

    def test_все_ручки_чата_идут_через_проверку(self):
        """Ни одна ручка не должна брать space_id сырым.

        Достаточно забыть один вызов, чтобы у этой ручки вернулось прежнее
        поведение «не сужать вовсе» — и она одна начнёт отвечать по объединению
        пространств, пока остальные отвечают по одному.
        """
        source = (ROOT / 'wiki' / 'routes_ai.py').read_text(encoding='utf-8')
        body = source.split('def register(', 1)[1]
        for raw in re.finditer(r'_space_id\(\)', body):
            line_start = body.rfind('\n', 0, raw.start()) + 1
            line = body[line_start:body.index('\n', raw.start())]
            self.assertIn('effective_space', line,
                          f'сырой _space_id() в ручке: {line.strip()}')


class MountTests(unittest.TestCase):
    """Как шарик встроен в оболочку портала."""

    def setUp(self):
        self.app = APP.read_text(encoding='utf-8')
        self.orb = ORB.read_text(encoding='utf-8')

    def test_шарик_сиблинг_а_не_потомок_области_контента(self):
        """Внутри main-content на части разделов стоит overflow-hidden, а у вики
        на этом поддереве действует zoom (.wiki-scope) — position: fixed считался
        бы от масштабированного предка, и координаты поехали бы."""
        self.assertIn('<AssistantOrb', self.app)
        orb_at = self.app.index('<AssistantOrb')
        toast_at = self.app.index('<ToastContainer')
        self.assertLess(orb_at, toast_at, 'шарик обязан стоять до тостов')
        # ToastContainer — сиблинг корневого div; значит и шарик тоже.
        self.assertGreater(orb_at, self.app.index('main-content'))

    def test_шарика_нет_там_где_он_второй_вход_или_чужой_экран(self):
        suppressed = re.search(r'SUPPRESSED_VIEWS = new Set\(\[([^\]]*)\]\)', self.orb)
        self.assertIsNotNone(suppressed, 'список погашенных разделов исчез')
        views = suppressed.group(1)
        # Вики: там уже есть вкладка «Помощник» и строка «Спросить Помощника».
        self.assertIn("'wiki'", views)
        # Журнал оценок — iframe со своей сборкой; LMS — свой каркас.
        self.assertIn("'call_evaluation'", views)
        self.assertIn("'lms'", views)

    def test_слой_ниже_полноэкранных_режимов(self):
        """84 держит шарик под всем, что занимает экран целиком.

        Занятые полки, которые нельзя трогать: 85 — модалка «Ивентов», 90 —
        лист ios.jsx, 95 — тренажёры вики, 110 — карточка задачи, 120 —
        «Новость дня» и обращения IT, 130 — виджет закреплённой задачи,
        135/140 — полноэкранные режимы, 9999 — тосты.
        """
        layers = [int(v) for v in re.findall(r'zIndex: (\d+)', self.orb)]
        self.assertTrue(layers, 'у шарика пропал слой')
        for layer in layers:
            self.assertLess(layer, 85,
                            f'слой {layer} перекроет полноэкранный режим или модалку')
            self.assertGreater(layer, 80, f'слой {layer} уйдёт под док переписки')

    def test_панель_ленивая_а_шарик_нет(self):
        """Шарик на первом экране у всех — он обязан быть в основном коде.
        Мини-чат тянет markdown с DOMPurify, и его платит только тот, кто открыл."""
        self.assertRegex(self.orb, r'lazy\(\(\) => import\(.\./AssistantPanel')
        self.assertNotIn('lazy', self.app[self.app.index('import AssistantOrb'):]
                         .split('\n')[0])

    def test_замок_qr_не_снят_а_показан(self):
        """Оператор без подтверждённой сессии видит шарик и замок с кнопкой.

        Снять QR-гейт было бы проще всего, но это расширение прав: гейт закрывает
        доступ к тексту статей. Решение владельца — оставить замок и показать,
        как его открыть.
        """
        panel = PANEL.read_text(encoding='utf-8')
        self.assertIn('SENSITIVE_ACCESS_REQUIRED', panel)
        self.assertIn('onRequestQr', panel)
        self.assertIn('locked={sensitiveSectionsLocked}', self.app)


class VisualTests(unittest.TestCase):
    """Рисунок пузыря: что именно нельзя потерять при правке стилей."""

    def setUp(self):
        self.css = CSS.read_text(encoding='utf-8')

    def test_нет_contain_который_обрежет_ореол(self):
        """`contain: paint` и `contain: size` клипают потомков по границе бокса.

        Ореол задан тенью на слое во всю величину шарика и выходит за круг —
        под paint-containment он исчезнет, а вместе с ним и мягкое свечение,
        ради которого пузырь читается пузырём, а не кружком.
        """
        for forbidden in ('contain: paint', 'contain: size', 'contain: strict',
                          'contain: layout paint'):
            self.assertNotIn(forbidden, self.css)

    def test_движение_только_на_transform(self):
        """Анимировать filter, box-shadow или background-position значит гонять
        перерисовку в основном потоке — на виджете, который висит всегда."""
        for block in re.findall(r'@keyframes[^{]+\{(.*?)\n\}', self.css, re.S):
            for prop in re.findall(r'\n\s+([a-z-]+):', block):
                self.assertEqual(prop, 'transform',
                                 f'в keyframes анимируется {prop}, а не transform')

    def test_тёмная_тема_гасит_белое_ядро_и_блик(self):
        """На тёмном молочное ядро даёт серое пятно, а белый блик — царапину.
        Пузырь в темноте держит светящаяся кромка, и только она."""
        dark = self.css[self.css.index('html[data-otp-theme="dark"]'):]
        self.assertIn('.aorb__spec { background: none; }', dark)
        self.assertIn('.aorb__rim', dark)

    def test_уважает_настройку_меньше_движения(self):
        self.assertIn('@media (prefers-reduced-motion: reduce)', self.css)

    def test_вкладка_в_фоне_ставит_анимацию_на_паузу_а_не_сбрасывает(self):
        """`animation: none` дёрнул бы пузырь в нулевую фазу при возврате на
        вкладку; пауза продолжает движение с того же места."""
        idle = self.css[self.css.index('.aorb-idle'):]
        rule = idle[:idle.index('}')]
        self.assertIn('animation-play-state: paused', rule)
        self.assertNotIn('animation: none', rule)

    def test_стили_шарика_не_попали_в_палитру_тёмной_темы(self):
        """Скрипт тёмной темы подменяет светлые стопы графитом. Пастельная
        радуга под такой подменой превращается в грязь, а рисовать её всё равно
        должен сам компонент — он объявляет оба состояния рядом."""
        script = DARK_SCRIPT.read_text(encoding='utf-8')
        palette = script[script.index('PALETTE_SOURCES = ('):]
        palette = palette[:palette.index(')\n')]
        self.assertNotIn('assistant', palette)


class SharedThreadTests(unittest.TestCase):
    """Лента ответов — одна на вкладку и на мини-чат."""

    def test_оговорка_про_архив_живёт_в_единственном_месте(self):
        """Формулировка обязана совпадать с серверной (wiki/ai/answer.py).

        Разъехавшись по двум файлам, живой ответ и он же из истории начали бы
        читаться по-разному — а это ровно тот случай, ради которого оговорка и
        появилась после разбора 27.08.2026.
        """
        phrase = 'Часть ответа взята из архивных материалов'
        hits = [p for p in (ROOT / 'src').rglob('*.jsx')
                if phrase in p.read_text(encoding='utf-8')]
        self.assertEqual([p.name for p in hits], ['assistantThread.jsx'],
                         'появилась вторая копия оговорки про архив')

    def test_узкая_панель_не_прячет_источники(self):
        """compact убирает служебные подписи, но не обвязку ответа: источники,
        оговорку про архив и приписку об ознакомлении. Помощник отвечает по
        регламентам, которые оператор пересказывает водителю."""
        thread = THREAD.read_text(encoding='utf-8')
        compact_block = thread[thread.index('export const AssistantMessage'):]
        self.assertIn('Источники', compact_block)
        self.assertIn('ackTitles', compact_block)
        self.assertIn('STALE_CAVEATS', compact_block)
        # Спрятать под compact разрешено только время и модель.
        self.assertIn('compact ? null :', compact_block)


if __name__ == '__main__':
    unittest.main()

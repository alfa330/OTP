# -*- coding: utf-8 -*-
"""Оценка ответа помощника: «Помогло» и «Нет» обязаны отвечать на нажатие.

Ряд кнопок живёт в одном месте на два экрана — вкладка «Помощник» вики и
мини-чат шарика (src/components/assistant/assistantThread.jsx). Проверки здесь
про то, что видно только в исходнике, и почти каждая — про МОЛЧАЛИВЫЙ отказ:
кнопка нажимается, запрос уходит, оценка сохраняется, а на экране не меняется
ничего. Ни сборка, ни рендер-тест такого не ловят.

Как это выглядело 07.09.2026. Выбранной оценке дописывали `text-emerald-600`
рядом с `iosBtnGhost`, а тот несёт `text-slate-500`. Утилиты цвета Tailwind
идут в собранном CSS ПО АЛФАВИТУ, и «slate» стоит почти в конце: замер на
собранном бандле дал emerald 95 049, rose 99 314, slate-500 100 672 байт.
Специфичность у всех одинаковая, поэтому выигрывало последнее правило — серый.
Пользователь видел кнопку, которая «не нажимается», хотя голос уже лежал в
базе. Лечится только `!` (важность) либо отказом ghost от своего цвета.

Тест читает .jsx текстом — как страж каталога вики
(test_wiki_catalog.CatalogScreenSourceTest). Исходник берётся дважды: целиком
там, где проверяется видимая строка, и без комментариев там, где проверяется
код, — иначе объяснение ловушки в шапке файла само же и проходило бы за код.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = ROOT / 'src' / 'components'

# Цвета, которые в собранном CSS Tailwind стоят РАНЬШЕ slate и потому проигрывают
# `text-slate-500` из iosBtnGhost. Список — по алфавиту палитры, до «slate».
LOSES_TO_SLATE = ('amber', 'blue', 'cyan', 'emerald', 'fuchsia', 'green',
                  'indigo', 'lime', 'orange', 'pink', 'purple', 'red', 'rose')


def read(*parts):
    return (SRC.joinpath(*parts)).read_text(encoding='utf-8')


def strip_comments(text):
    without_blocks = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return '\n'.join(line for line in without_blocks.splitlines()
                     if not line.lstrip().startswith('//'))


class FeedbackButtonsSourceTest(unittest.TestCase):
    """Ряд «Помогло / Нет» — единственный ответ помощнику от человека."""

    @classmethod
    def setUpClass(cls):
        cls.thread = read('assistant', 'assistantThread.jsx')
        cls.thread_code = strip_comments(cls.thread)
        cls.ios = read('ui', 'ios.jsx')
        cls.ios_code = strip_comments(cls.ios)

    def test_ghost_button_still_carries_its_own_colour(self):
        """Предпосылка ловушки: iosBtnGhost задаёт цвет текста сам.

        Всё, что ниже, проверяет обход именно этого. Уберут цвет из ghost —
        обход станет лишним, и тест должен об этом сказать первым, а не
        оставить в коде важность, которая уже ничего не решает.
        """
        ghost = re.search(r'iosBtnGhost\s*=\s*\n?\s*[\'"](.+?)[\'"]',
                          self.ios_code, flags=re.S)
        self.assertIsNotNone(ghost, 'iosBtnGhost больше не строка-константа')
        self.assertTrue('text-slate-500' in ghost.group(1),
                        'iosBtnGhost больше не красит текст — важность у оценки '
                        'стала не нужна, обход пора убрать')

    def test_chosen_vote_wins_the_cascade(self):
        """Цвет выбранной оценки — только с `!`.

        Без важности класс дописывается, но не действует: `.text-slate-500`
        лежит в собранном CSS позже. Кнопка остаётся серой, и нажатие выглядит
        как отказ.
        """
        active = re.search(r'const VOTE_ON = \{(.+?)\};', self.thread_code, flags=re.S)
        self.assertIsNotNone(active, 'классы выбранной оценки больше не собраны в одном месте')
        block = active.group(1)
        for colour in ('emerald', 'rose'):
            self.assertRegex(block, r'!text-%s-\d{3}' % colour,
                             'оценка красится %s без важности — цвет проиграет '
                             'text-slate-500 из iosBtnGhost' % colour)

    def test_hover_tint_wins_too(self):
        """Подложку под курсором перебивает та же строка.

        `hover:bg-slate-100` из iosBtnGhost старше по порядку, чем
        `hover:bg-emerald-100`: наведя курсор на выбранную оценку, человек
        видел бы, как она сереет обратно.
        """
        active = re.search(r'const VOTE_ON = \{(.+?)\};', self.thread_code, flags=re.S)
        block = active.group(1)
        for colour in ('emerald', 'rose'):
            self.assertRegex(block, r'hover:!bg-%s-\d{3}' % colour,
                             'подложка %s под курсором проиграет hover:bg-slate-100' % colour)

    def test_the_choice_is_not_told_by_colour_alone(self):
        """Разницу серого и зелёного на подписи в 13 px видно не каждому.

        Поэтому у выбранной оценки есть ещё и подложка. Это не украшение:
        она и есть тот ответ на нажатие, которого не было.
        """
        active = re.search(r'const VOTE_ON = \{(.+?)\};', self.thread_code, flags=re.S)
        block = active.group(1)
        for colour in ('emerald', 'rose'):
            self.assertRegex(block, r'(?<!hover:)(?<!!)bg-%s-\d{2,3}' % colour,
                             'у выбранной оценки %s нет спокойной подложки' % colour)

    def test_buttons_say_which_one_is_chosen(self):
        """Состояние объявлено, а не только нарисовано."""
        self.assertEqual(self.thread_code.count('aria-pressed='), 2,
                         'обе кнопки оценки обязаны объявлять своё состояние')

    def test_no_bare_accent_next_to_the_ghost_button(self):
        """Голый акцентный цвет рядом с iosBtnGhost в этом файле запрещён.

        Ровно так ловушка и выглядела: класс на месте, эффекта нет.
        """
        for line in self.thread_code.splitlines():
            if 'iosBtnGhost' not in line:
                continue
            for colour in LOSES_TO_SLATE:
                self.assertNotRegex(
                    line, r'(?<!!)\btext-%s-\d{2,3}' % colour,
                    'цвет text-%s-* дописан к iosBtnGhost без важности: '
                    'он проиграет text-slate-500' % colour)


class FeedbackRollbackSourceTest(unittest.TestCase):
    """Отметка на экране обязана совпадать с тем, что легло в базу.

    Оценка красит кнопку сразу, не дожидаясь сервера, — так и надо. Но пока
    цвет не менялся, отказ сервера был незаметен; теперь крашеная кнопка при
    неудачном запросе врала бы. Ленту держат ДВА владельца (вкладка вики и хук
    шарика), и откат нужен обоим — поправишь одного, второй промолчит.
    """

    @classmethod
    def setUpClass(cls):
        cls.tab = strip_comments(read('wiki', 'WikiAssistant.jsx'))
        cls.hook = strip_comments(read('assistant', 'useAssistantChat.js'))
        cls.panel = strip_comments(read('assistant', 'AssistantPanel.jsx'))
        cls.thread = strip_comments(read('assistant', 'assistantThread.jsx'))

    def test_the_button_hands_over_the_previous_vote(self):
        """Прежнее значение знает только кнопка — она его и отдаёт."""
        self.assertEqual(self.thread.count('message.feedback ?? null'), 2,
                         'кнопки не передают прежнюю оценку, откатывать будет нечего')

    def test_both_owners_take_the_previous_vote(self):
        for name, code in (('WikiAssistant.jsx', self.tab),
                           ('useAssistantChat.js', self.hook)):
            self.assertTrue(
                re.search(r'sendFeedback = useCallback\(\(messageId, value, previous', code),
                '%s не принимает прежнюю оценку' % name)

    def test_both_owners_roll_the_vote_back(self):
        for name, code in (('WikiAssistant.jsx', self.tab),
                           ('useAssistantChat.js', self.hook)):
            body = re.search(r'sendFeedback = useCallback\((.+?)\n    \}, \[', code, flags=re.S)
            self.assertIsNotNone(body, '%s: не нашёл тело sendFeedback' % name)
            self.assertTrue('mark(previous)' in body.group(1),
                            '%s не возвращает прежнюю оценку при отказе сервера' % name)

    def test_the_orb_panel_passes_it_through(self):
        """Панель шарика — посредник: потеряет третий аргумент, откат обнулится."""
        self.assertTrue('chat.sendFeedback(messageId, value, previous)' in self.panel,
                        'AssistantPanel не прокидывает прежнюю оценку в хук')

    def test_a_failed_vote_still_tells_the_person(self):
        """Откат молча — это второй молчаливый отказ вместо первого."""
        for name, code in (('WikiAssistant.jsx', self.tab),
                           ('AssistantPanel.jsx', self.panel)):
            self.assertTrue('Не удалось сохранить оценку' in code,
                            '%s не говорит о неудавшейся оценке' % name)


class FeedbackVisibilitySourceTest(unittest.TestCase):
    """Кому кнопки показываются вообще."""

    @classmethod
    def setUpClass(cls):
        cls.thread = strip_comments(read('assistant', 'assistantThread.jsx'))

    def test_no_vote_on_a_reply_the_server_has_not_seen(self):
        """У своей реплики id локальный: оценивать нечего, сервер её не знает."""
        self.assertTrue("!String(message.id).startsWith('local-')" in self.thread,
                        'ряд оценки показался бы у ещё не сохранённой реплики')


if __name__ == '__main__':
    unittest.main()

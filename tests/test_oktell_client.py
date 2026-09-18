# -*- coding: utf-8 -*-
"""Клиент Oktell: решения, которые ломаются молча.

Прогонять сам Electron в наборе тестов нельзя — он тянет окно и сотню мегабайт,
поэтому здесь сторожа по исходникам, как у остальных .jsx в этом наборе. Живой
прогон делает `npm run selftest` внутри oktell_client: он поднимает окно и
ходит в заглушку.

Сторожим ровно то, за что уже заплачено ошибкой на стенде или что отвалится
беззвучно: момент установки обёртки сокета, сокрытие вида АТС, серверную
проверку подтверждения и правила, которые обязаны пускать оператора работать,
даже когда у нас что-то не вышло.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "oktell_client"

MAIN = (CLIENT / "main.js").read_text(encoding="utf-8")
PRELOAD_PAGE = (CLIENT / "preload-oktell.js").read_text(encoding="utf-8")
RENDERER = (CLIENT / "renderer" / "app.js").read_text(encoding="utf-8")
STUB = (CLIENT / "dev_harness" / "stub_server.py").read_text(encoding="utf-8")


class ClientLayoutTests(unittest.TestCase):
    def test_the_package_points_at_the_main_process(self):
        package = json.loads((CLIENT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual("main.js", package["main"])
        self.assertIn("selftest", package["scripts"])

    def test_the_real_config_is_not_committed(self):
        """Адреса портала и АТС у каждой машины свои, а репозиторий публичный."""
        ignored = (CLIENT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("config.json", ignored)
        self.assertTrue((CLIENT / "config.example.json").exists())
        self.assertFalse((CLIENT / "config.json").exists()
                         and "config.json" not in ignored)


class PageHookTests(unittest.TestCase):
    """Обёртка сокета обязана стоять раньше скриптов страницы."""

    def test_the_hook_is_a_preload_and_not_a_late_injection(self):
        """Первая версия вставляла скрипт на did-finish-load и не поймала ни
        одного кадра: страница поднимает свой веб-сокет ещё при разборе
        разметки. Отказ молчаливый — статус просто никогда не приходит, и
        объявление показывается «когда угодно»."""
        self.assertIn("preload: path.join(__dirname, 'preload-oktell.js')", MAIN)
        self.assertNotIn("did-finish-load', () => injectOktellScript", MAIN)
        self.assertNotIn("executeJavaScript", MAIN)

    def test_the_page_view_gives_up_isolation_but_not_node(self):
        """Из изолированного мира чужой window.WebSocket не подменить. Node при
        этом странице не достаётся — иначе чужая страница получила бы наш
        процесс целиком."""
        view = MAIN.split("oktellView = new WebContentsView(", 1)[1].split("});", 1)[0]
        self.assertIn("contextIsolation: false", view)
        self.assertIn("sandbox: false", view)
        self.assertIn("nodeIntegration: false", view)

    def test_the_account_is_ready_before_the_view_is_built(self):
        """preload забирает учётку синхронно на своём старте — то есть раньше,
        чем openOktell вернёт управление."""
        opened = MAIN.split("function openOktell(account) {", 1)[1].split("\n}", 1)[0]
        self.assertIn("if (account) pendingAccount = account;", opened)
        self.assertIn("event.returnValue", MAIN)
        self.assertIn("sendSync('oktell:options')", PRELOAD_PAGE)

    def test_frames_are_read_but_never_altered(self):
        """Обёртка только слушает: подмена содержимого кадров сломала бы АТС."""
        self.assertIn("socket.addEventListener('message'", PRELOAD_PAGE)
        self.assertNotIn("socket.send =", PRELOAD_PAGE)


class BlockingTests(unittest.TestCase):
    """Пока объявление на экране, до АТС не добраться."""

    def test_hiding_the_view_also_collapses_it(self):
        """Второй рубеж: если setVisible окажется пустышкой на чужой платформе,
        вид нулевого размера всё равно не примет ни клика, ни клавиши."""
        body = MAIN.split("function showOktell(visible) {", 1)[1].split("\n}", 1)[0]
        self.assertIn("setVisible(oktellVisible)", body)
        self.assertIn("setBounds({ x: 0, y: 0, width: 0, height: 0 })", body)

    def test_resizing_the_window_does_not_reveal_a_hidden_view(self):
        """Обычный resize вернул бы спрятанный вид прямо под объявление."""
        layout = MAIN.split("function layoutOktell() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("if (!oktellVisible) return;", layout)

    def test_the_window_has_no_way_to_be_dismissed(self):
        """У обязательного объявления нет ни крестика, ни Esc — закрыть его
        может только подтверждение, принятое сервером."""
        markup = (CLIENT / "renderer" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("news-close", markup)
        self.assertNotIn("Escape", RENDERER)


class ServerDecidesTests(unittest.TestCase):
    """Выдержку и тест решает сервер, клиент только рисует."""

    def test_the_client_never_measures_the_delay_itself(self):
        """Часы на машине оператора свои, и «сколько прошло» считать нечем."""
        self.assertIn("remaining_seconds", RENDERER)
        self.assertIn("NEWS_TOO_EARLY", RENDERER)
        self.assertNotIn("Date.now() - shown", RENDERER)

    def test_wrong_answers_are_not_highlighted(self):
        """Сервер называет неверные вопросы, но подсказка превратила бы тест в
        перебор вариантов."""
        self.assertIn("NEWS_QUIZ_WRONG", RENDERER)
        self.assertNotIn("result.wrong", RENDERER)

    def test_the_icore_password_is_not_kept(self):
        """Пароль от учётки iCORE нигде не оседает.

        В форме его стирают сразу после входа, в состоянии процесса держат
        токены, а не пару логин-пароль, и на диск клиент не пишет вовсе —
        поэтому «запомнить меня» здесь нечем и подобрать нечего.
        """
        self.assertIn("passwordInput.value = ''", RENDERER)
        self.assertNotIn("localStorage", RENDERER)
        stored = MAIN.split("    auth = {", 1)[1].split("};", 1)[0]
        self.assertIn("token:", stored)
        self.assertIn("refreshToken:", stored)
        self.assertNotIn("password", stored)
        # Конфиг только читаем: писать в него значило бы завести файл с паролем.
        self.assertIn("fs.readFileSync", MAIN)
        self.assertNotIn("writeFileSync", MAIN)


class FailOpenTests(unittest.TestCase):
    """Наши неудачи не имеют права запирать оператора."""

    def test_an_unknown_state_eventually_shows_the_news(self):
        """Иначе переименованное вендором поле означало бы, что объявления не
        приходят вообще — и никто об этом не узнает."""
        rule = MAIN.split("function canShowNews() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("operatorState.busy", rule)
        self.assertIn("unknown_state_grace_seconds", rule)

    def test_a_missing_cabinet_still_opens_the_pbx(self):
        """409 значит «учётку ещё не завели» — оператор введёт логин руками."""
        # Режем до СЛЕДУЮЩЕГО обработчика: внутри этого есть свои `});`,
        # и наивная нарезка обрывала бы его на первой же строке.
        handler = MAIN.split("ipcMain.handle('auth:login'", 1)[1].split("ipcMain.handle(", 1)[0]
        self.assertIn("cabinet_error", handler)
        self.assertIn("openOktell(cabinet)", handler)
        # Открываем АТС и запускаем опрос ДАЖЕ без учётки: 409 — это «ещё не
        # завели», а не «этому человеку сюда нельзя».
        self.assertIn("startNews()", handler)

    def test_the_end_of_a_call_wakes_the_queue(self):
        """Ждать следующего опроса после разговора — минута простоя окна."""
        listener = MAIN.split("ipcMain.on('oktell:state'", 1)[1].split("});", 1)[0]
        self.assertIn("wasBusy && !busy", listener)
        self.assertIn("pollNews()", listener)


class StubTests(unittest.TestCase):
    """Заглушка обязана отказывать так же, как боевой сервер."""

    def test_it_refuses_early_confirmations_and_wrong_answers(self):
        self.assertIn("NEWS_TOO_EARLY", STUB)
        self.assertIn("NEWS_QUIZ_WRONG", STUB)

    def test_it_speaks_the_real_status_frame(self):
        """Кадр тот же, что у настоящей АТС, иначе обёртка проверялась бы на
        выдуманном формате."""
        self.assertIn("getuserstateresult", STUB)
        self.assertIn("userstatestr", STUB)


if __name__ == "__main__":
    unittest.main()

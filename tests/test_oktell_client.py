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
MARKUP = (CLIENT / "renderer" / "index.html").read_text(encoding="utf-8")
UPDATE = (CLIENT / "update.js").read_text(encoding="utf-8")
BOT = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
APP = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
SECTION = (ROOT / "src" / "components" / "oktell_client"
           / "OktellClientTestView.jsx").read_text(encoding="utf-8")
DATABASE = (ROOT / "database.py").read_text(encoding="utf-8-sig")


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
        # executeJavaScript в файле есть — но только в витрине, где он нажимает
        # кнопку ради показа заказчику. В странице АТС им не пользуемся вовсе.
        for chunk in MAIN.split("executeJavaScript")[:-1]:
            self.assertIn("function showcase()", chunk,
                          "executeJavaScript вне витрины — значит хук снова поздний")

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

    def test_only_an_optional_news_can_be_dismissed(self):
        """Крестик есть, но только у необязательного объявления.

        У обязательного выхода нет вовсе: ни крестика, ни Esc — закрыть его
        может только подтверждение, принятое сервером.
        """
        self.assertIn('id="news-close"', MARKUP)
        self.assertIn("newsClose.hidden = !!item.is_mandatory", RENDERER)
        self.assertIn("if (!current || current.is_mandatory) return;", RENDERER)
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


class LineReleaseTests(unittest.TestCase):
    """Оператора снимают с линии ДО показа и возвращают после."""

    def test_the_status_goes_out_before_the_window(self):
        """Между окном и сменой статуса не должно быть щели: АТС успевает
        направить звонок, а окно АТС уже закрыто — звонок пропадёт."""
        poll = MAIN.split("async function pollNews() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(poll.index("await setTraining(true)"), poll.index("send('news:show'"))
        self.assertLess(poll.index("lockScreen(true)"), poll.index("send('news:show'"))

    def test_only_a_mandatory_news_touches_the_line(self):
        """Необязательное объявление — «к сведению». Уводить ради него человека
        с линии значило бы терять звонки на ровном месте."""
        poll = MAIN.split("async function pollNews() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("if (item.is_mandatory) {", poll)
        block = poll.split("if (item.is_mandatory) {", 1)[1].split("    }", 1)[0]
        self.assertIn("setTraining(true)", block)
        self.assertIn("lockScreen(true)", block)

    def test_the_status_is_restored_only_if_we_took_it(self):
        """У оператора, который к моменту объявления уже был на перерыве, статус
        не наш: «вернуть на линию» подняло бы его с обеда."""
        close = MAIN.split("async function closeNews() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("if (lineReleased) await setTraining(false);", close)

    def test_the_command_is_repeated_while_the_window_is_up(self):
        """wp_setuserstate не залипает — проверено вживую 17.08.2026, оператор
        возвращается на линию сам за минуту-полторы."""
        self.assertIn("function startReapply()", MAIN)
        self.assertIn("reapply_seconds", MAIN)
        show = MAIN.split("async function pollNews() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("startReapply()", show)
        close = MAIN.split("async function closeNews() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("stopReapply()", close)

    def test_the_frame_is_configurable_and_not_wired_into_the_logic(self):
        """Протокол веб-клиента вендор нигде не описывает: уточнили — поправили
        строку в конфиге, а не пересобрали программу."""
        self.assertIn("socket_frames", MAIN)
        self.assertIn("lunchreasonid: 3", MAIN)
        command = MAIN.split("function pageStatusCommand(kind) {", 1)[1].split("\n}", 1)[0]
        self.assertIn("config.status?.socket_frames", command)

    def test_there_are_two_ways_and_the_second_is_the_documented_one(self):
        """Кадр быстрее, но протокол — догадка. Серверная команда АТС
        документирована и проверена вживую, поэтому она запасной путь, а не
        основной: она не держится."""
        body = MAIN.split("async function setTraining(on) {", 1)[1].split("\n}", 1)[0]
        self.assertLess(body.index("pageStatusCommand"), body.index("httpStatusCommand"))
        self.assertIn("wp_setuserstate", MAIN)
        self.assertIn("oncallcenter", MAIN)

    def test_the_page_answers_even_when_it_failed(self):
        """Молчание страницы главный процесс прочитал бы как успех и не стал бы
        снимать оператора с линии вторым способом."""
        self.assertIn("oktell:status-result", PRELOAD_PAGE)
        handler = PRELOAD_PAGE.split("ipcRenderer.on('oktell:status'", 1)[1]
        self.assertIn("answer(false", handler)
        self.assertIn("'страница не ответила'", MAIN)


class ScreenLockTests(unittest.TestCase):
    """Пока объявление на экране, до рабочего стола не добраться."""

    def test_the_window_takes_the_whole_screen_and_keeps_it(self):
        lock = MAIN.split("function lockScreen(on) {", 1)[1].split("\n}", 1)[0]
        self.assertIn("setKiosk(true)", lock)
        self.assertIn("setAlwaysOnTop(true, 'screen-saver')", lock)
        self.assertIn("setSkipTaskbar(true)", lock)
        self.assertIn("setClosable(false)", lock)
        self.assertIn("setMinimizable(false)", lock)

    def test_losing_focus_takes_it_back(self):
        """Иначе Alt+Tab уводит оператора на рабочий стол, и объявление
        превращается в окно, которое просто висит сзади."""
        self.assertIn("win.on('blur', keepFocus)", MAIN)
        keep = MAIN.split("function keepFocus() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("if (!locked", keep)
        self.assertIn("win.focus()", keep)

    def test_the_lock_is_released_on_the_way_back(self):
        close = MAIN.split("async function closeNews() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("lockScreen(false)", close)


class TwoStepWindowTests(unittest.TestCase):
    """Сначала текст, потом тест — так требует постановка."""

    def test_the_quiz_is_hidden_while_the_text_is_open(self):
        step = RENDERER.split("function goStep(next) {", 1)[1].split("\n}", 1)[0]
        self.assertIn("newsQuiz.hidden = reading", step)
        self.assertIn("newsBody.hidden = !reading", step)

    def test_the_first_button_opens_the_quiz_and_confirms_nothing(self):
        """Отправлять подтверждение с пустыми ответами значило бы получить отказ
        сервера на ровном месте."""
        self.assertIn("if (step === 'read' && hasQuiz()) {", RENDERER)

    def test_the_delay_gates_the_reading_step(self):
        """Выдержка про то, что материал открыли и не пролистали, а не про
        скорость ответов."""
        update = RENDERER.split("function updateConfirm() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("if (step === 'read')", update)
        self.assertIn("remaining > 0", update)

    def test_a_wrong_answer_sends_the_operator_back_to_the_text(self):
        """Постановка: «сотрудник автоматически возвращается к материалу»."""
        wrong = RENDERER.split("NEWS_QUIZ_WRONG", 1)[1].split("} else", 1)[0]
        self.assertIn("goStep('read')", wrong)
        self.assertIn("answers = {}", wrong)


class ReleaseEndpointTests(unittest.TestCase):
    """Раздача клиента: манифест, файл, публикация."""

    def test_all_four_routes_exist(self):
        for rule in ('version', 'download', 'releases', 'publish'):
            self.assertIn(f"@app.route('/api/oktell_client/{rule}'", BOT)

    def test_the_manifest_is_public(self):
        """Закрывать нельзя: клиент сверяет версию и до входа оператора, а
        истёкшая сессия не должна запирать парк машин на старой версии."""
        head = BOT.split("@app.route('/api/oktell_client/version'", 1)[1].split("def ", 1)[0]
        self.assertNotIn("@require_api_key", head)
        body = BOT.split("def oktell_client_version_endpoint", 1)[1].split("@app.route", 1)[0]
        # Ссылки на файл в манифесте нет — её выдаёт отдельная ручка.
        self.assertNotIn("signed_url", body)
        self.assertIn("'no-store'", body)

    def test_the_download_is_gated_on_the_server(self):
        """Спрятанная кнопка ничего не ограничивает: подписанную ссылку можно
        было бы запросить напрямую."""
        head = BOT.split("@app.route('/api/oktell_client/download'", 1)[1].split("def ", 1)[0]
        self.assertIn("@require_api_key", head)
        body = BOT.split("def oktell_client_download_endpoint", 1)[1].split("@app.route", 1)[0]
        self.assertIn("_can_download_oktell_client", body)
        self.assertIn("403", body)

    def test_the_department_is_resolved_by_code(self):
        """id отдела засеян, а не задан: на другой базе он другой."""
        self.assertIn("OKTELL_CLIENT_DEPARTMENT_CODES = ('szov',)", BOT)
        resolver = BOT.split("def _oktell_client_department_ids():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("db.get_departments()", resolver)
        # Неудачный запрос не кэшируем: иначе одна плохая минута закрыла бы
        # скачивание всему отделу на десять.
        self.assertIn("return tuple(cached['ids'] or ())", resolver)

    def test_publishing_needs_its_own_token_compared_in_constant_time(self):
        """Токен сборки даёт право положить файл, который парк машин поставит
        сам. Обычное сравнение утекает его по времени."""
        body = BOT.split("def oktell_client_publish_endpoint", 1)[1].split("@app.route", 1)[0]
        self.assertIn("OKTELL_CLIENT_PUBLISH_TOKEN_ENV", body)
        self.assertIn("hmac.compare_digest", body)
        self.assertIn("401", body)

    def test_the_hash_is_computed_by_the_server(self):
        """Присланному клиентом хэшу верить нельзя: именно он решает, поставит
        программа файл или отбросит."""
        body = BOT.split("def oktell_client_publish_endpoint", 1)[1].split("@app.route", 1)[0]
        self.assertIn("hashlib.sha256(data).hexdigest()", body)
        # Для готового объекта в бакете хэш обязателен и проверяется по длине.
        self.assertIn("len(digest) != 64", body)

    def test_only_one_release_can_be_current(self):
        """«Две текущие» означали бы, что часть машин уедет не туда: клиент
        берёт релиз без ORDER BY."""
        self.assertIn("oktell_client_releases_current_idx", DATABASE)
        self.assertIn("WHERE is_current", DATABASE)
        self.assertIn("_init_oktell_client_schema_tx(cursor)", DATABASE)

    def test_the_manifest_cache_is_dropped_on_publish(self):
        """Иначе новая версия доедет до парка через минуту после публикации."""
        body = BOT.split("def oktell_client_publish_endpoint", 1)[1].split("@app.route", 1)[0]
        self.assertIn("_oktell_client_version_cache.pop('current', None)", body)


class UpdaterTests(unittest.TestCase):
    """Автообновление клиента: чем оно отличается от соседских."""

    def test_versions_are_compared_as_numbers(self):
        """Строкой '0.10.0' меньше '0.9.0', и парк застрял бы на девятой сборке."""
        self.assertIn("function isNewer(", UPDATE)
        self.assertIn("parseInt(part, 10)", UPDATE)

    def test_a_file_with_a_wrong_hash_is_deleted(self):
        """Подписи у нас нет, и хэш — единственная проверка подлинности файла."""
        body = UPDATE.split("async function download(", 1)[1].split("\n}", 1)[0]
        self.assertIn("actual.toLowerCase() !== String(manifest.sha256", body)
        self.assertIn("unlinkSync(file)", body)
        self.assertIn("return null", body)

    def test_installing_waits_for_a_real_pause(self):
        """Перезапуск посреди разговора оборвёт звонок, а посреди объявления —
        собьёт чтение и оставит человека снятым с линии."""
        install = UPDATE.split("function install() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("options.isIdle()", install)
        self.assertIn("return false", install)
        rule = MAIN.split("function updateIsSafeNow() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("!operatorState.busy", rule)
        self.assertIn("!newsShown", rule)

    def test_a_mandatory_release_does_not_break_a_call_either(self):
        """Обязательность ускоряет установку, но права оборвать звонок не даёт:
        отдельной ветки «ставить немедленно» в install() нет."""
        install = UPDATE.split("function install() {", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("mandatory", install)

    def test_the_pause_after_a_call_and_after_the_news_is_used(self):
        """Следующей паузы может не быть до конца смены."""
        listener = MAIN.split("ipcMain.on('oktell:state'", 1)[1].split("});", 1)[0]
        self.assertIn("updater.onIdle()", listener)
        close = MAIN.split("async function closeNews() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("updater.onIdle()", close)

    def test_the_startup_path_installs_before_work_begins(self):
        """На старте оператор ещё не работает — обрывать нечего."""
        self.assertIn("await updater.onStartup()", MAIN)
        startup = UPDATE.split("async function onStartup() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("install()", startup)

    def test_the_download_link_is_asked_for_only_after_login(self):
        """До входа ссылку не выдадут — ручка за авторизацией."""
        fetcher = UPDATE.split("async function fetchDownloadUrl() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("if (!token) return null;", fetcher)
        self.assertIn("updater.check();", MAIN)


class TestSectionTests(unittest.TestCase):
    """Раздел «Тест iCORE/Oktell» — единственное место, откуда берут программу."""

    def test_the_item_is_in_both_menu_branches(self):
        """Меню ветвится по ролям и по оболочке: пункт, добавленный в одну
        ветку, открывается только по адресу, и человек его не находит."""
        self.assertEqual(2, APP.count("handleSidebarViewNavigation(e, 'icore_oktell_test')"))
        # Подпись считаем только в самих пунктах: третье вхождение — комментарий
        # у предиката, и привязываться к нему числом значило бы ломать тест от
        # каждой правки формулировки.
        self.assertEqual(2, APP.count('sidebar-text">Тест iCORE/Oktell'))

    def test_the_section_opens_by_url_too(self):
        """Ctrl-клик по пункту меню — штатный путь, он ведёт на ?view=…, и без
        этой строки новая вкладка молча уезжает в раздел по умолчанию."""
        self.assertIn("requestedViewFromUrl !== 'icore_oktell_test'", APP)
        self.assertIn("if (view === 'icore_oktell_test'", APP)

    def test_it_is_a_szov_pilot(self):
        self.assertIn("icore_oktell_test: ['szov']", APP)

    def test_there_is_no_second_download_button(self):
        """Программа одна, и берут её из раздела: вторая кнопка в общем меню —
        это два названия одного и того же, то есть шум."""
        self.assertNotIn('Скачать клиент Oktell', APP)
        self.assertNotIn('downloadOktellClient', APP)

    def test_the_section_asks_the_public_manifest_not_the_admin_history(self):
        """Раздел смотрят и операторы, а история релизов открыта админам."""
        self.assertIn('/api/oktell_client/version', SECTION)
        self.assertNotIn('/api/oktell_client/releases', SECTION)

    def test_the_download_link_is_taken_fresh_on_click(self):
        """Подпись живёт час: держать ссылку в разметке нельзя."""
        download = SECTION.split("const download = async () => {", 1)[1].split("\n    };", 1)[0]
        self.assertIn('/api/oktell_client/download', download)
        self.assertIn('window.open(data.url', download)


if __name__ == "__main__":
    unittest.main()

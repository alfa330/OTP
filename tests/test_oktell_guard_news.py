# -*- coding: utf-8 -*-
"""Надстройка над ограничителем: вход по учётке iCORE и обязательные объявления.

Почему это внутри «Ограничителя Перезвона», а не отдельной программой: у него
уже есть всё нужное — хук в странице Oktell, живые сокеты клиента, плашка
поверх окна, своя раздача с автообновлением и личный токен, по которому сервер
знает, кто за машиной, ещё ДО входа в АТС. Отдельное приложение означало бы
второй канал раздачи, вторую программу на машине и браузер внутри неё.

Сторожим то, что ломается молча: порядок «сначала снять с линии, потом
показать», возврат статуса только если мы его и забрали, и то, что правила
подтверждения не продублированы, а взяты у раздела «Новости».
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / "oktell_recall_guard" / "agent.py"
ROUTES = (ROOT / "oktell_guard" / "routes.py").read_text(encoding="utf-8")


def _load_agent():
    spec = importlib.util.spec_from_file_location("oktell_guard_news_agent", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agent = pytest.importorskip("requests") and _load_agent()
AGENT_SOURCE = AGENT_PATH.read_text(encoding="utf-8")


class TestCabinetFromServer:
    """Учётку кабинета отдаёт сервер, оператор её не вводит и не знает."""

    def test_it_is_served_only_to_a_known_agent(self):
        """Общий токен сборки не говорит, кто за машиной: отдавать по нему
        чужой пароль от АТС нельзя."""
        body = ROUTES.split("def oktell_guard_agent_config", 1)[1].split("@agent_route", 1)[0]
        assert "agent_owner(cursor)" in body
        assert "db.get_oktell_account(owner['user_id']) if owner else None" in body

    def test_an_account_without_a_password_is_not_sent(self):
        """Пустая пара выглядела бы как «логин без пароля», и агент пытался бы
        войти ею — вместо этого ключа просто нет."""
        body = ROUTES.split("def oktell_guard_agent_config", 1)[1].split("@agent_route", 1)[0]
        assert "if cabinet and cabinet.get('cabinet_password'):" in body

    def test_the_agent_forgets_it_when_the_server_stops_sending_it(self):
        """Отозвали доступ — значит и в памяти агента учётки остаться не должно,
        поэтому она перезаписывается, а не сливается с прежней."""
        cfg = agent.apply_server_config(
            {"cabinet": {"login": "6612", "password": "old"}, "server_url": "https://x"},
            {"oktell_url": "https://oktell/"},
        )
        assert cfg["cabinet"] == {}

    def test_it_reaches_the_agent_config(self):
        cfg = agent.apply_server_config(
            {"server_url": "https://x"},
            {"oktell_url": "https://oktell/", "cabinet": {"login": "6612", "password": "s"}},
        )
        assert cfg["cabinet"]["login"] == "6612"


class TestAutologin:
    """Форму входа заполняет агент — тем же способом, что и разлогин."""

    def test_native_setter_and_events(self):
        """Простое присваивание value не будит слушателей Angular, и форма
        отправила бы пустые поля, которые на экране выглядят заполненными."""
        js = agent.build_autologin_js("6612", "secret")
        assert "HTMLInputElement.prototype, 'value'" in js
        assert "new Event('input'" in js and "new Event('change'" in js

    def test_hidden_inputs_are_skipped(self):
        """У формы входа бывает второй, невидимый набор полей: заполнение его
        выглядит как «ничего не произошло»."""
        assert "offsetParent !== null" in agent.build_autologin_js("a", "b")

    def test_it_does_not_retry_in_a_tight_loop(self):
        """Форма могла не успеть перерисоваться — второй заход подряд только
        мешает и множит попытки входа."""
        assert "__oktellGuardAutologinAt" in agent.build_autologin_js("a", "b")

    def test_nothing_happens_without_credentials(self):
        js = agent.build_autologin_js("", "")
        assert "if (!creds.login || !creds.password)" in js


class TestOperatorState:
    """«Тренинг» уходит в тот же сокет, который слушает ограничитель."""

    def test_the_status_is_changed_by_the_clients_own_method(self):
        """Кадр руками собирать НЕЛЬЗЯ. Свой (`{"onlunch":true,"lunchreasonid":3}`)
        АТС молча игнорировала, и оператор оставался на линии всё чтение.
        Настоящий кадр, снятый с провода, куда шире:

            ["setuserstate",{"userlogin":"6554","userstateid":2,"oncallcenter":true,
                             "onredirect":false,"lunchreasonid":3,"qid":"0.365…"}]

        Формат — внутреннее дело вендора, а метод лежит в той же странице, что и
        кнопка статуса, по которой жмёт оператор."""
        js = agent.build_set_status_js("break", 3)
        assert "app.oktell" in js
        assert "setStatus" in js
        assert "new WebSocket" not in js
        assert not hasattr(agent, "build_set_state_js"), "кадр руками больше не собираем"

    def test_the_operator_goes_back_where_he_was(self):
        """Возвращать в «Готов» нельзя: того, кто был без телефона или на обеде,
        мы бы поставили на линию сами. И возвращать надо С ПРИЧИНОЙ: обед,
        тренинг и тех.причина — всё это `break`, один статус их не различает, и
        человек вернулся бы с объявления на чужую причину перерыва."""
        assert "getCurrentBreakReason" in agent.build_read_status_js()
        loop = AGENT_SOURCE.split("def run_agent", 1)[1]
        assert 'status_before.get("status")' in loop
        assert 'status_before.get("reason")' in loop

    def test_a_call_is_not_taken_for_an_applied_status(self):
        """`setUserStatus` возвращает undefined ВСЕГДА — по вызову судить о
        результате нельзя. 21.09.2026 агент считал успехом сам факт вызова:
        объявление показалось человеку, оставшемуся на линии, и в логе не было
        ни строчки. Теперь статус перечитывается, пока АТС его не применит."""
        body = AGENT_SOURCE.split("def set_operator_status", 1)[1].split("    def ", 1)[0]
        assert "self.operator_status()" in body
        assert "АТС не применила статус" in body

    def test_the_training_reason_is_three(self):
        """Справочник подпричин Oktell: 1 Тех.причина, 2 Перезвон, 3 Тренинг,
        4 Перерыв. Проверено живьём: getLunchReasons() отдаёт ровно эту
        четвёрку."""
        loop = AGENT_SOURCE.split("def run_agent", 1)[1]
        assert 'cfg.get("training_reason_id", 3)' in loop

    def test_the_hook_knows_when_a_call_is_running(self):
        """Объявление обязано дождаться конца разговора, а знать об этом
        мгновенно может только страница."""
        js = agent.build_hook_js({})
        assert "inCall: false" in js
        assert "rule.inCall = true;" in js
        assert "rule.inCall = false;" in js
        assert "inCall: !!rule.inCall" in agent.build_hook_health_js(["k"])


class TestNewsWindow:
    """Окно объявления рисуется в странице — как плашка предупреждения."""

    def _item(self):
        return {
            "id": 7, "title": "Правила", "body": "<p>текст</p>", "remaining_seconds": 5,
            "quiz": [{"id": 1, "prompt": "Куда звонить?", "options": ["Туда", "Сюда"]}],
        }

    def test_the_text_comes_first_and_the_quiz_after(self):
        """Постановка: «после нажатия „Ознакомлен“ сотрудник переходит к
        тестированию»."""
        js = agent.build_news_js(self._item())
        assert "state.step === 'read' && questions.length" in js
        assert "state.step = 'quiz'" in js

    def test_the_delay_is_taken_from_the_server(self):
        """Часы на машине оператора свои: считать «сколько прошло» нечем."""
        js = agent.build_news_js(self._item())
        assert '"remaining_seconds": 5' in js or "'remaining_seconds': 5" in js

    def test_there_is_no_way_to_close_it(self):
        """Объявление обязательное: «свернуть, потом прочитаю» у него нет."""
        js = agent.build_news_js(self._item())
        assert "закрыть" not in js.lower()
        assert "Esc" not in js

    def test_the_verdict_comes_from_the_server_not_from_the_page(self):
        """Верных ответов у страницы нет, и права ходить на наш адрес у неё
        тоже: нажатие забирает агент и решает сервер."""
        js = agent.build_news_js(self._item())
        assert "state.result = { id: data.id, answers: state.answers }" in js
        assert "fetch(" not in js
        assert "state.result" in agent.build_news_result_js()

    def test_a_wrong_answer_keeps_the_answers_and_marks_the_question(self):
        """Дефект 1.0.16: из-за одного неверного ответа окно стирало ВСЕ отметки
        и возвращало к тексту — человек отвечал заново на весь тест и даже не
        знал, где ошибся. Ответы остаются, помечается только неверный вопрос."""
        js = agent.build_news_js(self._item())
        assert "NEWS_QUIZ_WRONG" in js
        # Сброс ответов и возврат к тексту — ровно то, чего быть не должно.
        assert "state.answers = {}; drawQuiz(); state.step = 'read';" not in js
        assert "state.wrong = (payload.wrong || []).slice();" in js
        assert "Неверно — перечитайте новость и выберите другой вариант" in js

    def test_the_marked_question_is_the_one_the_server_named(self):
        """Своего мнения о верности у окна нет: красит те вопросы, которые
        назвал сервер, и только выбранный в них вариант — подкрасить остальные
        значило бы подсказать."""
        js = agent.build_news_js(self._item())
        assert "function missed(id)" in js
        assert "var chosen = state.answers[item.id] === optionIndex;" in js
        assert "bad && chosen" in js

    def test_the_choice_survives_a_repaint(self):
        """Пометка приходит после ответа сервера, и перерисовка не должна
        снимать уже сделанный выбор."""
        js = agent.build_news_js(self._item())
        assert "radio.checked = state.answers[item.id] === optionIndex;" in js

    def test_fixing_the_answer_clears_the_mark(self):
        """Красный на варианте, который человек только что сменил, говорил бы
        неправду — как и отказ сервера про прошлый ответ."""
        js = agent.build_news_js(self._item())
        body = js.split("radio.addEventListener('change'", 1)[1].split("var text =", 1)[0]
        assert "state.wrong = state.wrong.filter(" in body
        assert "note.textContent = '';" in body


class TestLoopOrder:
    """Порядок действий в цикле — то, что ломается молча."""

    def _loop(self):
        return AGENT_SOURCE.split("def run_agent", 1)[1]

    def test_the_line_is_released_before_the_window_is_shown(self):
        """Иначе между окном и сменой статуса есть щель, в которую АТС успевает
        направить звонок — а окно АТС уже закрыто объявлением."""
        loop = self._loop()
        assert loop.index('status_before = browser.set_operator_status(') < \
               loop.index("if news_overlay.show(item):")

    def test_nothing_is_shown_during_a_call(self):
        loop = self._loop()
        assert 'in_call = bool(rule_state.get("inCall"))' in loop
        assert "if state.browser.get(\"session\") and not in_call:" in loop

    def test_the_status_is_restored_only_if_we_took_it(self):
        """У того, кто к моменту объявления уже был на перерыве, статус не наш:
        «вернуть на линию» подняло бы его с обеда."""
        loop = self._loop()
        assert loop.count("if training_set:") >= 1
        assert "training_set = False" in loop

    def test_a_failed_show_gives_the_line_back(self):
        """Сняли с линии, а окно не нарисовалось — оператор остался бы без
        звонков и без объявления."""
        loop = self._loop()
        assert "elif training_set:" in loop

    def test_the_queue_can_be_longer_than_one(self):
        loop = self._loop()
        assert "next_news_check = time.time()   # очередь может быть длиннее одного" in loop


class TestServerRules:
    """Правила показа и подтверждения берутся у раздела «Новости»."""

    def test_only_mandatory_news_reaches_the_agent(self):
        """Необязательное — «к сведению»: снимать ради него с линии и закрывать
        клиент АТС значило бы терять звонки на ровном месте."""
        body = ROUTES.split("def oktell_guard_agent_news", 1)[1].split("@agent_route", 1)[0]
        assert "if item.get('is_mandatory')" in body

    def test_only_what_was_sent_to_oktell_reaches_the_agent(self):
        """Канал объявления (решение владельца 18.09.2026): автор выбирает в
        форме, куда его отправить. Отправленное в портал агент показывать не
        должен — человек подтвердил бы одну новость дважды, в двух окнах, а
        журнал «Кто прочитал» у неё один."""
        body = ROUTES.split("def oktell_guard_agent_news", 1)[1].split("@agent_route", 1)[0]
        assert "channel=('oktell'" in body
        # Нет колонки — агент работает как до задачи, а не падает.
        assert "_news_channel_ready(cursor, news_channel_ready)" in body
        assert "else None)" in body

    def test_only_the_shown_one_is_marked(self):
        """Отметка «показали» — это точка отсчёта задержки кнопки и запись в
        журнал: поставить её всей очереди значит написать «открыл» про то, чего
        человек не видел."""
        body = ROUTES.split("def oktell_guard_agent_news", 1)[1].split("@agent_route", 1)[0]
        assert "mark_shown(cursor, news_ids=[item['id']]" in body

    def test_the_confirmation_is_not_a_second_copy_of_the_rules(self):
        """Выдержку, ответы и право подтверждать решает та же функция, что и на
        сайте: две копии правила про документооборот разъезжаются."""
        body = ROUTES.split("def oktell_guard_agent_news_read", 1)[1].split("@agent_route", 1)[0]
        assert "news_queries.confirm_read(" in body
        assert "NEWS_TOO_EARLY" in body and "NEWS_QUIZ_WRONG" in body

    def test_the_wrong_questions_are_named(self):
        """Без id вопросов окно агента умеет сказать только «где-то неверно»,
        и человек переотвечает весь тест из-за одного вопроса. Портал их
        отдаёт (news/routes.py) — агенту нужны те же."""
        body = ROUTES.split("def oktell_guard_agent_news_read", 1)[1].split("@agent_route", 1)[0]
        assert '"wrong": detail' in body

    def test_an_unknown_agent_gets_nothing(self):
        for name in ("oktell_guard_agent_news", "oktell_guard_agent_news_read"):
            body = ROUTES.split(f"def {name}", 1)[1].split("@agent_route", 1)[0]
            assert "agent_owner(cursor)" in body
            assert "if not owner:" in body

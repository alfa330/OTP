# -*- coding: utf-8 -*-
"""Временная ставка оператора: действует в расчёте и аукционе, но не в деньгах.

Решение владельца 29.08.2026: график на следующую неделю строится по ставкам,
которых в карточках людей ещё нет. Ставку на период задают в «Деталях расчёта»,
и она обязана дойти РОВНО до двух мест — расчёта ресурсов и аукциона смен.

Главный страж здесь — отрицательный: подмена не должна протечь в учёт часов и
зарплату. Там своя помесячная `work_hours.rate`, и если подменённое значение
попадёт в заморозку месяца, оно станет в истории неотличимо от настоящей ставки.
"""
import ast
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE = os.path.join(REPO_ROOT, "database.py")
SERVICE = os.path.join(REPO_ROOT, "resource_fte_service.py")
CHAT = os.path.join(REPO_ROOT, "resource_fte", "chat.py")
BACKEND = os.path.join(REPO_ROOT, "bot_schedule2.py")
VIEW = os.path.join(REPO_ROOT, "src", "components", "resources", "ResourceFteView.jsx")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _sql_literals(path):
    """Все SQL-строки файла, как их увидит Python (разбором AST, не регуляркой)."""
    out = []
    for node in ast.walk(ast.parse(_read(path))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            out.append(node.args[0].value)
    return out


def _function_source(path, name):
    """Исходник одной функции/метода — чтобы сторожить место, а не весь файл."""
    src = _read(path)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} не найдена в {os.path.basename(path)}")


class RateOverrideSchemaTests(unittest.TestCase):
    def test_table_index_and_function_are_created_by_code(self):
        """Схему в этом проекте меняют только кодом — ручных ALTER на проде нет."""
        ddl = " ".join(" ".join(s.split()) for s in _sql_literals(DATABASE))
        self.assertIn("CREATE TABLE IF NOT EXISTS operator_rate_overrides", ddl)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS uq_operator_rate_overrides_period", ddl)
        self.assertIn("CREATE OR REPLACE FUNCTION operator_effective_rate", ddl)

    def test_window_and_rate_are_guarded_by_check_constraints(self):
        """Базу нельзя оставить без защиты: витрина не единственный вход."""
        ddl = " ".join(" ".join(s.split()) for s in _sql_literals(DATABASE))
        table = ddl[ddl.index("CREATE TABLE IF NOT EXISTS operator_rate_overrides"):]
        table = table[:table.index(");") + 2]
        self.assertIn("CHECK (rate IN (1.00, 0.75, 0.50))", table,
                      "ставка вне набора должна отклоняться базой")
        self.assertIn("CHECK (valid_to >= valid_from)", table,
                      "перевёрнутое окно должно отклоняться базой")

    def test_override_falls_back_to_the_card_rate(self):
        """Функция обязана возвращаться к users.rate за границей окна.

        Иначе «далее уже с их реальной ставки» пришлось бы делать руками, и об
        этом бы забыли — срок подмены нигде больше не поддержан.
        """
        ddl = " ".join(" ".join(s.split()) for s in _sql_literals(DATABASE))
        body = ddl[ddl.index("CREATE OR REPLACE FUNCTION operator_effective_rate"):]
        body = body[:body.index("$$ LANGUAGE sql STABLE")]
        self.assertIn("BETWEEN o.valid_from AND o.valid_to", body)
        self.assertIn("SELECT u.rate FROM users u WHERE u.id = p_operator_id", body)


class RateOverrideReachesCalcAndAuctionTests(unittest.TestCase):
    def test_resource_availability_reads_the_effective_rate(self):
        """Обе ветки доступности — и агрегат, и «Детали расчёта»."""
        source = _function_source(SERVICE, "_period_operator_availability_tx")
        self.assertEqual(
            source.count("operator_effective_rate(u.id, (SELECT MIN(day) FROM period_days))"), 2,
            "ставку «на период» должны брать обе ветки: агрегатная и детальная")
        self.assertNotIn("COALESCE(u.rate, 1.0) AS rate", source,
                         "ставка из карточки в расчёте доступности больше не читается")

    def test_current_operator_fte_reads_the_effective_rate(self):
        source = _function_source(SERVICE, "_current_operator_fte_tx")
        self.assertIn("operator_effective_rate(u.id, COALESCE(%s::date, CURRENT_DATE))", source)

    def test_chat_capacity_reads_the_effective_rate(self):
        """У чата это решает не вес человека, а попадёт ли он в расчёт вообще.

        Ниже `_chat_operator_capacity_tx` стоит фильтр `rate not in CHAT_RATES`,
        и 0,5 уходит в off_scale целиком. Поэтому подмена 0,50 → 0,75 добавляет
        человека в штат, а не четверть ставки.
        """
        source = _function_source(CHAT, "_chat_operator_capacity_tx")
        self.assertIn("operator_effective_rate(u.id, COALESCE(%s::date, CURRENT_DATE))", source)

    def test_auction_snapshot_and_claim_read_the_effective_rate(self):
        """Снимок (норма и «только своя ставка») и оба места, где смену берут."""
        for name in ("_build_shift_auction_snapshot_common_tx",
                     "claim_shift_auction_test_lot",
                     "self_schedule_shift_auction_shift"):
            source = _function_source(DATABASE, name)
            self.assertIn("operator_effective_rate(", source,
                          f"{name} обязана брать ставку с учётом подмены")

    def test_auction_rate_date_is_the_plan_period_start(self):
        """Одна ставка на весь прогон: иначе норма менялась бы посреди недели."""
        source = _function_source(DATABASE, "_shift_auction_rate_on_date_tx")
        self.assertIn("SELECT date_from FROM resource_saved_schedule_plans WHERE id = %s", source)


class AvailabilityQueryArityTests(unittest.TestCase):
    """Число колонок SELECT обязано совпадать с числом имён в распаковке.

    Реальная поломка 29.08.2026: колонку base_rate добавили в CTE `operators`, но
    не протащили через `operator_days` в финальный SELECT — запрос вернул 14
    значений на 15 имён, и раздел упал с «not enough values to unpack
    (expected 15, got 14)». Ни один страж этого не ловил, потому что все они
    читали ТЕКСТ, а арность — свойство запроса целиком.
    """

    @staticmethod
    def _body():
        src = _read(SERVICE)
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "_period_operator_availability_tx"):
                return ast.get_source_segment(src, node)
        raise AssertionError("_period_operator_availability_tx не найдена")

    @staticmethod
    def _top_level_columns(select_text, first_token):
        """Колонки верхнего уровня: запятые вне скобок (COUNT(...) не считаем)."""
        depth = 0
        count = 1
        for ch in select_text[select_text.index(first_token):]:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                count += 1
        return count

    @staticmethod
    def _unpacked_names(body, loop_index):
        chunk = body[loop_index:]
        chunk = chunk[chunk.index("(") + 1:chunk.index(") = row")]
        return [item.strip() for item in chunk.split(",") if item.strip()]

    def test_details_branch_select_matches_its_unpacking(self):
        body = self._body()
        head = body.rindex("        SELECT\n            id,")
        select_text = body[head:body.index("FROM operator_days", head)]
        columns = self._top_level_columns(select_text, "id,")
        names = self._unpacked_names(body, body.rindex("for row in cursor.fetchall():"))
        self.assertEqual(columns, len(names),
                         f"детальная ветка: колонок {columns}, имён {len(names)}")

    def test_base_rate_is_carried_through_every_stage(self):
        """CTE → operator_days → финальный SELECT → GROUP BY.

        Пропуск любого звена и есть та самая поломка: в `operators` колонка была,
        а до строки результата не доезжала.
        """
        body = self._body()
        details = body[body.index("supervisor.name AS supervisor_name"):]
        self.assertIn("COALESCE(u.rate, 1.0) AS base_rate", details, "нет в CTE operators")
        self.assertIn("o.base_rate", details, "не протащена в operator_days")
        self.assertIn("            base_rate,\n", details, "нет в финальном SELECT")
        self.assertIn("rate, base_rate, current_status", details, "нет в GROUP BY")

    def test_aggregate_branch_was_not_touched(self):
        """У агрегатной ветки своя арность — base_rate ей не нужен."""
        body = self._body()
        aggregate = body[body.index("if not include_details:"):body.index("for row in cursor.fetchall():")]
        self.assertNotIn("base_rate", aggregate,
                         "в агрегатной ветке base_rate лишний — он сдвинет её распаковку")


class RateOverrideMustNotReachMoneyTests(unittest.TestCase):
    """Отрицательные стражи. Они здесь главные."""

    def test_month_freeze_never_reads_the_override(self):
        """Заморозка месяца делает ставку историей — подменённую туда нельзя.

        Если 0,75 доедет до снапшотов, в истории оно станет неотличимо от
        настоящей ставки, и вернуть 0,50 будет уже нечем.
        """
        source = _function_source(DATABASE, "_freeze_month_to_snapshots_tx")
        self.assertNotIn("operator_effective_rate", source,
                         "подмена не должна попадать в заморозку месяца")

    def test_payroll_and_hours_sql_never_reads_the_override(self):
        """Учёт часов и ЗП живут на помесячной work_hours.rate — мимо подмены."""
        offenders = []
        for sql in _sql_literals(DATABASE):
            if "operator_effective_rate" not in sql:
                continue
            flat = " ".join(sql.split())
            if "work_hours" in flat or "norm_hours" in flat:
                offenders.append(flat[:120])
        self.assertEqual(offenders, [],
                         f"подмена протекла в учёт часов/ЗП: {offenders}")

    def test_override_is_a_separate_layer_not_a_users_rate_edit(self):
        """Правка users.rate замораживает прошлые месяцы и якорит текущий.

        Две правки «туда и обратно» за две недели оставили бы в помесячной
        ставке два следа, и вернуться к настоящей ставке было бы нельзя без
        ручной чистки work_hours. Поэтому подмена обязана жить своей таблицей.
        """
        source = _function_source(SERVICE, "set_operator_rate_override")
        self.assertIn("INSERT INTO operator_rate_overrides", source)
        self.assertNotIn("UPDATE users", source,
                         "подмена не имеет права трогать ставку в карточке")


class RateOverrideApiTests(unittest.TestCase):
    def test_route_exists_and_write_is_admin_only(self):
        """Читать может весь раздел, менять — только админ.

        Ставка отсюда двигает недельную норму часов в аукционе, то есть это
        решение уровня руководителя, а не просмотр витрины.
        """
        backend = _read(BACKEND)
        self.assertIn("@app.route('/api/resource_fte/rate_overrides'", backend)
        block = backend[backend.index("def api_resource_fte_rate_overrides"):]
        block = block[:block.index("@app.route('/api/resource_fte/operator_availability'")]
        self.assertIn("{'super_admin', 'admin'}", block)
        self.assertIn("403", block)

    def test_clearing_deletes_the_row_instead_of_writing_the_card_rate(self):
        """Иначе последующая правка карточки молча разошлась бы с расчётом."""
        source = _function_source(SERVICE, "set_operator_rate_override")
        self.assertIn("DELETE FROM operator_rate_overrides", source)


class RateOverrideFrontendTests(unittest.TestCase):
    def test_details_modal_has_a_rate_selector(self):
        view = _read(VIEW)
        self.assertIn("const RATE_OVERRIDE_CHOICES = [1, 0.75, 0.5]", view,
                      "0,5 обязана быть в наборе: именно так человека возвращают в чат")
        self.assertIn("canEditRates", view)
        self.assertIn("onRateChange", view)

    def test_selector_is_hidden_from_non_admins(self):
        view = _read(VIEW)
        self.assertIn("['admin', 'super_admin'].includes(String(user?.role || ''))", view)

    def test_screen_says_where_the_rate_applies(self):
        """Без этой строки подмену прочитают как смену ставки в карточке."""
        view = _read(VIEW)
        self.assertIn("действует только в расчёте ресурсов", view)
        self.assertIn("В учёт часов и зарплату не попадает", view)

    def test_saving_resets_the_availability_cache(self):
        """Кеш держит уже посчитанные ставки — без сброса правка не видна."""
        view = _read(VIEW)
        block = view[view.index("const handleOperatorRateChange"):]
        block = block[:block.index("useEffect(")]
        self.assertIn("setOperatorAvailabilityDetailsByKey({})", block)


if __name__ == "__main__":
    unittest.main()

"""«Чаты ОП»: показатели делятся по направлениям отдела продаж.

Раздел начинался как «Чаты Верификаторов», но к Wazzup подключили и другие
направления ОП (Поток), поэтому показатели теперь делятся. Тесты закрепляют три
вещи, которые ломаются молча:

1. итог по направлению считает БЭКЕНД по сырым значениям — среднее и медиану
   времени ответа нельзя получить усреднением строк таблицы;
2. названные группы (Верификаторы, Поток) возвращаются ВСЕГДА, даже пустыми —
   иначе «Поток без сообщений» выглядит как пропавший раздел;
3. непривязанные авторы получают свою, последнюю группу: без неё их цифры
   тихо выпали бы из суммы направлений.
"""

import ast
import textwrap
import unittest
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
API_PATH = ROOT / "bot_schedule2.py"
VIEW_PATH = ROOT / "src" / "components" / "wazzup" / "WazzupChatsView.jsx"
APP_PATH = ROOT / "src" / "App.jsx"


def _class_member(source, class_name, member_name):
    tree = source_cache.parse(source)
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == member_name for t in node.targets
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.FunctionDef) and node.name == member_name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{class_name}.{member_name} не найден")


def _module_function(source, name, namespace):
    tree = source_cache.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "<wazzup-split>", "exec"), namespace)
    return namespace[name]


def _module_assignment(source, name, namespace):
    """Константа уровня модуля — как она в исходнике, без переписывания в тест."""
    tree = source_cache.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in item.targets)
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "<wazzup-split>", "exec"), namespace)
    return namespace[name]


class _Cursor:
    """Курсор-запоминалка: отдаёт заранее разложенные ответы по порядку."""

    def __init__(self, fetchall_results):
        self.queries = []
        self._fetchall = list(fetchall_results)

    def execute(self, query, params=None):
        self.queries.append((" ".join(query.split()), params))

    def fetchall(self):
        return self._fetchall.pop(0) if self._fetchall else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DirectionSqlTests(unittest.TestCase):
    """SQL несёт направление до самого итога."""

    @classmethod
    def setUpClass(cls):
        cls.source = DATABASE_PATH.read_text(encoding="utf-8-sig")
        cls.cte = _class_member(cls.source, "Database", "_WAZZUP_ANALYTICS_CTE")
        cls.directions = _class_member(
            cls.source, "Database", "WAZZUP_ANALYTICS_DIRECTIONS")

    def test_named_directions_are_verifier_and_potok(self):
        self.assertEqual(
            [(d["direction_id"], d["key"], d["label"]) for d in self.directions],
            [(71, "verifier", "Верификаторы"), (74, "potok", "Поток")],
            "порядок и состав названных групп — договорённость с владельцем",
        )

    def test_cte_carries_direction_from_users_to_first_reply(self):
        cte = " ".join(self.cte.split())
        # направление берётся из карточки оператора, а не из имени автора
        self.assertIn("LEFT JOIN users u ON u.id = map.user_id", cte)
        self.assertIn("u.direction_id", cte)
        # «первый ответ» тоже должен знать направление — иначе среднее время
        # ответа по направлению посчитать нечем
        self.assertIn("a.grp, a.direction_id, EXTRACT(EPOCH", cte)

    def test_direction_totals_are_raw_not_averaged_rows(self):
        method = _class_member(self.source, "Database", "wazzup_operator_analytics")
        namespace = {}
        exec(textwrap.dedent(method), namespace)
        cursor = _Cursor(fetchall_results=[
            [],  # строки
            [    # is_total, направление, имя, чаты, сообщения, людей, ответов, avg, median
                (1, None, None, 18087, 54403, 30, 4508, 4168.13, 91.4),
                (0, 71, "Верификатор", 13306, 48306, 15, 4278, 3217.13, 92.78),
                (0, None, None, 2782, 3610, 15, 150, 33477.09, 109.9),
            ],
        ])

        class FakeDatabase:
            _WAZZUP_ANALYTICS_CTE = self.cte
            wazzup_operator_analytics = namespace["wazzup_operator_analytics"]

            def _get_cursor(inner_self):
                return cursor

        result = FakeDatabase().wazzup_operator_analytics()

        directions_query = cursor.queries[1][0]
        self.assertIn("GROUP BY GROUPING SETS ((direction_id), ())", directions_query)
        self.assertIn("percentile_cont(0.5) WITHIN GROUP (ORDER BY secs)",
                      directions_query)
        # итоговая строка отличается от «без привязки» только GROUPING()
        self.assertIn("GROUPING(direction_id) AS is_total", directions_query)
        self.assertEqual(result["summary"]["chats"], 18087)
        self.assertEqual(result["summary"]["managers"], 30)
        # диалоги направления считаются ВМЕСТЕ с grp — тогда итог группы это
        # ровно сумма её строк, как и показывает подвал таблицы
        self.assertIn(
            "COUNT(DISTINCT (grp, local_date, channel_id, chat_id))",
            directions_query,
        )
        # FULL JOIN здесь Postgres не умеет (IS NOT DISTINCT FROM не хэшируется),
        # а LEFT JOIN достаточно: first_reply выведен из agent_msgs
        self.assertNotIn("FULL JOIN", directions_query)
        self.assertIn(
            "LEFT JOIN by_dir_resp r ON r.is_total = t.is_total "
            "AND r.direction_id IS NOT DISTINCT FROM t.direction_id",
            directions_query,
        )
        self.assertEqual(
            result["directions"][0],
            {"direction_id": 71, "direction_name": "Верификатор", "chats": 13306,
             "messages": 48306, "managers": 15, "answered_chats": 4278,
             "avg_response_secs": 3217.13, "median_response_secs": 92.78},
        )
        self.assertIsNone(result["directions"][1]["direction_id"])

    def test_row_query_returns_direction_instead_of_verifier_flag(self):
        method = _class_member(self.source, "Database", "wazzup_operator_analytics")
        namespace = {}
        exec(textwrap.dedent(method), namespace)
        cursor = _Cursor(fetchall_results=[
            [("u:292", 292, "Қыздарбек Дильназ", "k.dilnaz", "9156630",
              5293, 1516, None, 551, 60.0, 30.0, 71, "Верификатор")],
            [],
        ])

        class FakeDatabase:
            _WAZZUP_ANALYTICS_CTE = self.cte
            wazzup_operator_analytics = namespace["wazzup_operator_analytics"]

            def _get_cursor(inner_self):
                return cursor

        item = FakeDatabase().wazzup_operator_analytics()["items"][0]
        self.assertEqual(item["direction_id"], 71)
        self.assertEqual(item["direction_name"], "Верификатор")
        self.assertNotIn("is_verifier", item,
                         "флаг «верификатор» заменён направлением: групп больше двух")


class DirectionGroupTests(unittest.TestCase):
    """Сборка групп для экрана: состав, порядок, ярлыки."""

    @classmethod
    def setUpClass(cls):
        cls.source = API_PATH.read_text(encoding="utf-8-sig")
        named = _class_member(
            DATABASE_PATH.read_text(encoding="utf-8-sig"),
            "Database", "WAZZUP_ANALYTICS_DIRECTIONS")

        class _Db:
            WAZZUP_ANALYTICS_DIRECTIONS = named

        cls.namespace = {"db": _Db()}
        for name in ("WAZZUP_UNLINKED_GROUP_KEY", "WAZZUP_UNLINKED_GROUP_LABEL"):
            _module_assignment(cls.source, name, cls.namespace)
        for name in ("_wazzup_group_meta", "_wazzup_group_key",
                     "_wazzup_group_label", "_wazzup_direction_groups"):
            _module_function(cls.source, name, cls.namespace)

    def _row(self, direction_id, direction_name, messages, **kw):
        row = {"direction_id": direction_id, "direction_name": direction_name,
               "messages": messages, "chats": messages, "managers": 1,
               "answered_chats": 0, "avg_response_secs": None,
               "median_response_secs": None}
        row.update(kw)
        return row

    def test_named_groups_survive_empty_data(self):
        groups = self.namespace["_wazzup_direction_groups"]([])
        self.assertEqual([g["key"] for g in groups], ["verifier", "potok"])
        self.assertEqual([g["label"] for g in groups], ["Верификаторы", "Поток"])
        self.assertEqual([g["messages"] for g in groups], [0, 0])
        self.assertEqual([g["managers"] for g in groups], [0, 0])

    def test_order_named_then_others_then_unlinked(self):
        groups = self.namespace["_wazzup_direction_groups"]([
            self._row(None, None, 3610),
            self._row(73, "Основа ОП", 2487),
            self._row(72, "Яндекс Регистрация", 9000),
            self._row(71, "Верификатор", 48306),
        ])
        self.assertEqual(
            [g["key"] for g in groups],
            ["verifier", "potok", "dir72", "dir73", "unlinked"],
            "названные — первыми и в своём порядке, непривязанные — последними",
        )
        # неназванные направления между собой — по числу сообщений
        self.assertEqual([g["label"] for g in groups],
                         ["Верификаторы", "Поток", "Яндекс Регистрация",
                          "Основа ОП", "Без привязки"])

    def test_labels_override_singular_direction_names(self):
        groups = self.namespace["_wazzup_direction_groups"]([
            self._row(71, "Верификатор", 1),
            self._row(74, "Поток", 1),
        ])
        # в базе направление называется «Верификатор», на экране нужен коллектив
        self.assertEqual(groups[0]["label"], "Верификаторы")
        self.assertEqual(groups[1]["label"], "Поток")

    def test_unlinked_group_keeps_its_numbers(self):
        groups = self.namespace["_wazzup_direction_groups"](
            [self._row(None, None, 3610, chats=2782, managers=15)])
        unlinked = next(g for g in groups if g["key"] == "unlinked")
        self.assertEqual((unlinked["managers"], unlinked["chats"], unlinked["messages"]),
                         (15, 2782, 3610))
        self.assertIsNone(unlinked["directionId"])


class NameSuggestionTests(unittest.TestCase):
    """Автоподсказка привязки: казахское и русское написание — один человек."""

    @classmethod
    def setUpClass(cls):
        # правила живут в wazzup/names.py; bot_schedule2 их только импортирует
        from wazzup import names as wazzup_names
        cls.namespace = {
            "_wazzup_normalize_name": wazzup_names.normalize_name,
            "_wazzup_suggest_user": wazzup_names.suggest_user,
        }
        source = API_PATH.read_text(encoding="utf-8-sig")
        assert "from wazzup.names import normalize_name as _wazzup_normalize_name" in source
        assert "from wazzup.names import suggest_user as _wazzup_suggest_user" in source

    def test_soft_sign_does_not_break_the_match(self):
        # в Wazzup «Кенжебай Адильхан», в карточке «Кенжебай Әділхан»
        normalize = self.namespace["_wazzup_normalize_name"]
        self.assertEqual(normalize("Кенжебай Адильхан"),
                         normalize("Кенжебай Әділхан"))

    def test_suggests_operator_despite_alphabet(self):
        suggest = self.namespace["_wazzup_suggest_user"]
        operators = [{"id": 461, "name": "Кенжебай Әділхан"},
                     {"id": 506, "name": "Оңласын Әдемі"}]
        self.assertEqual(suggest("Кенжебай Адильхан", operators)["id"], 461)
        self.assertEqual(suggest("Онласын Адеми", operators)["id"], 506)


class SectionRenameTests(unittest.TestCase):
    """Раздел называется «Чаты ОП» — и в сайдбаре, и в шапке самого раздела."""

    def test_sidebar_item_renamed_in_every_branch(self):
        app = APP_PATH.read_text(encoding="utf-8-sig")
        # пункт живёт в трёх ветках сайдбара (СВ, главы отделов, админ) —
        # переименовать надо во всех, иначе раздел называется по-разному
        self.assertEqual(app.count('<span className="sidebar-text">Чаты ОП</span>'), 3)
        self.assertNotIn("Чаты Верификаторов", app)

    def test_view_header_renamed(self):
        view = VIEW_PATH.read_text(encoding="utf-8-sig")
        self.assertIn(">Чаты ОП</h2>", view)
        self.assertNotIn("Чаты Верификаторов", view)


class AnalyticsViewSplitTests(unittest.TestCase):
    """Фронт берёт итог группы у бэкенда, а не считает его из строк."""

    @classmethod
    def setUpClass(cls):
        cls.view = VIEW_PATH.read_text(encoding="utf-8-sig")

    def test_rows_are_filtered_by_group(self):
        self.assertIn("(group === ALL_GROUP || r.group === group)", self.view)

    def test_summary_comes_from_the_selected_group(self):
        # если бы итог считался из rows, среднее и медиана времени ответа
        # получились бы усреднением средних — это другая (неверная) цифра
        self.assertIn("const summary = activeGroup || {};", self.view)
        self.assertIn("...(data?.groups || [])", self.view)

    def test_unlinked_authors_are_called_out(self):
        self.assertIn("не привязаны к операторам", self.view)
        self.assertIn("onGoToMapping", self.view)

    def test_csv_export_carries_direction(self):
        self.assertIn("'Направление'", self.view)
        self.assertIn("r.groupLabel || ''", self.view)


if __name__ == "__main__":
    unittest.main()

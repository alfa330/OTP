# -*- coding: utf-8 -*-
"""Раздел «Настройки SIP»: персональные аккаунты операторов, два провайдера.

Правила, которые здесь закреплены:
  * SIP-номер нормализуется (пробелы/скобки из копипаста режем, кириллицу не пускаем);
  * один номер на двоих запрещён — иначе звонки привязываются не к тому оператору,
    но уникален он в пределах домена: на разных АТС одинаковые номера — норма;
  * значения по умолчанию берутся ТОЛЬКО у отдела. Общий ярус sip_config из
    резолвера снят: одна унификация на все отделы уводила отдел без своих
    настроек на чужую АТС, и именно так Тез уехал не туда;
  * провайдер — свойство отдела. «asterisk» — локальная АТС (логин = номер,
    пароль «база + номер», есть автодозвон и FOP2); «binotel» — облако, где
    сервер, логин и пароль у каждого свои, а автодозвона и FOP2 нет вовсе;
  * строка персональных настроек не остаётся пустышкой, а история пишется
    только когда что-то действительно поменялось; пароль кабинета Binotel в
    историю не попадает никогда — только факт замены;
  * доступ к разделу — тот же гейт, что был у панели (админ / глава отдела / СВ ОП),
    со скоупом по отделу.
"""

import ast
import json
import re
import textwrap
import unittest
from functools import lru_cache
from pathlib import Path
from typing import Optional

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
BOT_PATH = ROOT / "bot_schedule2.py"
APP_PATH = ROOT / "src" / "App.jsx"
VIEW_PATH = ROOT / "src" / "components" / "sip" / "SipSettingsView.jsx"

DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
DATABASE_MODULE = source_cache.parse(DATABASE_SOURCE)
DATABASE_CLASS = next(
    node for node in DATABASE_MODULE.body
    if isinstance(node, ast.ClassDef) and node.name == "Database"
)


def _read(path):
    return path.read_text(encoding="utf-8-sig")


@lru_cache(maxsize=None)
def _source_of(node, source):
    """Исходник узла без декораторов.

    Кэш обязателен, а не приятен: `ast.get_source_segment` каждый раз режет
    `database.py` (2,5 МБ, 57 тыс. строк) на строки — 0,28 с на вызов. При
    ~17 вызовах в `_database_namespace` и вызове из каждого setUp набор шёл
    4,5 минуты; с кэшем те же 17 разборов случаются один раз за процесс.
    Функция чистая (узлы дерева никто не меняет, см. tests/source_cache.py),
    поэтому мемоизация ничего не искажает.
    """
    text = textwrap.dedent(ast.get_source_segment(source, node))
    # Декораторы (@staticmethod) мешают вызывать метод как обычную функцию.
    return "\n".join(line for line in text.splitlines() if not line.startswith("@"))


def _select_columns(select_sql):
    """Колонки SELECT'а по порядку — позиционный маппер читает row строго по нему.

    Резать по запятым «в лоб» нельзя: внутри COALESCE(...) их полно. Считаем
    глубину скобок и режем только на верхнем уровне, а строки-комментарии
    выбрасываем — колонками они не являются.
    """
    head = select_sql.split("FROM users", 1)[0]
    head = "\n".join(
        line for line in head.splitlines() if not line.strip().startswith("--")
    ).strip()
    if head.upper().startswith("SELECT"):
        head = head[len("SELECT"):]
    columns, depth, current = [], 0, ""
    for ch in head:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            columns.append(" ".join(current.split()))
            current = ""
            continue
        current += ch
    if current.strip():
        columns.append(" ".join(current.split()))
    return columns


def _max_row_index(method_name):
    """Наибольший row[N], который читает позиционный маппер Database."""
    node = next(
        n for n in DATABASE_CLASS.body
        if isinstance(n, ast.FunctionDef) and n.name == method_name
    )
    return max(
        n.slice.value for n in ast.walk(node)
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
        and n.value.id == "row" and isinstance(n.slice, ast.Constant)
    )


def _fake_execute_values(cursor, sql, argslist, template=None):
    cursor.calls.append((" ".join(str(sql).split()), list(argslist), template))


def _database_namespace(method_names):
    """Исполняет методы Database без импорта модуля (он поднимает пул к БД)."""
    # Optional нужен настоящий: аннотация `-> Optional[dict]` вычисляется прямо
    # при exec'е, и заглушка None падала бы на `None[dict]`.
    ns = {"json": json, "re": re, "Optional": Optional,
          "execute_values": _fake_execute_values}
    for node in DATABASE_MODULE.body:
        # BINOTEL_ — не прихоть: BINOTEL_CABINET_URL_DEFAULT нужен
        # get_binotel_account, и без него метод падал бы на NameError.
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.startswith(("SIP_", "BINOTEL_"))
            for t in node.targets
        ):
            exec(_source_of(node, DATABASE_SOURCE), ns)
        if isinstance(node, ast.FunctionDef) and node.name in (
            "normalize_sip_identifier", "build_sip_password", "parse_sip_flag",
            "normalize_sip_provider",
        ):
            exec(_source_of(node, DATABASE_SOURCE), ns)
    for node in DATABASE_CLASS.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.startswith("_SIP_") for t in node.targets
        ):
            exec(_source_of(node, DATABASE_SOURCE), ns)
        if isinstance(node, ast.FunctionDef) and node.name in method_names:
            exec(_source_of(node, DATABASE_SOURCE), ns)
    return ns


def _no_common_tier(*_args, **_kwargs):
    """Заглушка get_sip_config: обращение к общему ярусу — это регрессия.

    Ярус «Общие: для отделов без своих настроек» снят продуктовым решением, и
    молчаливое чтение sip_config снова подставило бы отделу без настроек чужую
    АТС. Пусть такой вызов падает громко, а не выдаёт правдоподобный ответ.
    """
    raise AssertionError("общий ярус sip_config снят — читать его больше нельзя")


class _FakeCursor:
    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows or []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(str(sql).split()), params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _StubDb:
    """Достаточный кусок Database, чтобы гонять методы без базы."""

    def __init__(self, ns, cursor=None):
        self._ns = ns
        self.cursor = cursor or _FakeCursor()
        for name, value in ns.items():
            if name.startswith("_SIP_"):
                setattr(self, name, value)

    def _get_cursor(self):
        return self.cursor

    def _mask_sip_secret(self, value):
        return self._ns["_mask_sip_secret"](value)

    def _sip_operator_row(self, row):
        return self._ns["_sip_operator_row"](row)

    def call(self, name, *args, **kwargs):
        return self._ns[name](self, *args, **kwargs)


OPERATOR_STATE = {
    "id": 41, "name": "Иван", "role": "operator", "status": "working",
    "department_id": 367, "department_name": "Отдел продаж", "supervisor_id": 9,
    "group_name": "Группа 1",
    "sip_number": "1024", "sip_password": "", "sip_domain": "",
    "autodial_number": "", "autodial_password": "", "autodial_domain": "",
    "updated_at": None, "updated_by_name": None,
    "department_sip_server": "", "department_base_password": "", "department_autodial_code": "",
    "department_autodial_server": "", "department_autodial_base_password": "",
    # Вход в FOP2 включён у всех, кому его отдельно не выключали.
    "fop2_enabled": True,
    # Провайдер отдела: локальная АТС по умолчанию, Binotel — только у Тез КЦ.
    "department_provider": "asterisk",
    "sip_login": "",
    "binotel_cabinet_login": "", "binotel_employee_id": "", "binotel_cabinet_url": "",
    "has_binotel_cabinet_password": False,
}


class NormalizeSipIdentifierTests(unittest.TestCase):
    def setUp(self):
        self.normalize = _database_namespace(set())["normalize_sip_identifier"]

    def test_empty_values_become_empty_string(self):
        self.assertEqual("", self.normalize(None))
        self.assertEqual("", self.normalize("   "))

    def test_copy_paste_noise_is_cleaned(self):
        self.assertEqual("1024", self.normalize(" 10 24 "))
        self.assertEqual("+7700123", self.normalize("+7 (700) 123"))

    def test_service_codes_are_allowed(self):
        self.assertEqual("*55", self.normalize("*55", field="Код автодозвона"))
        self.assertEqual("#1_a.b-c", self.normalize("#1_a.b-c"))

    def test_cyrillic_and_too_long_are_rejected(self):
        with self.assertRaises(ValueError):
            self.normalize("номер")
        with self.assertRaises(ValueError):
            self.normalize("1" * 65)

    def test_error_names_the_field(self):
        with self.assertRaises(ValueError) as ctx:
            self.normalize("да", field="Код автодозвона")
        self.assertIn("Код автодозвона", str(ctx.exception))


class BuildSipPasswordTests(unittest.TestCase):
    """База пароля — шаблон: «Secret{номер}!» → «Secret1024!»."""

    def setUp(self):
        self.build = _database_namespace(set())["build_sip_password"]

    def test_plain_base_is_still_a_prefix(self):
        self.assertEqual("pwd1024", self.build("pwd", "1024"))

    def test_placeholder_allows_a_suffix(self):
        self.assertEqual("Secret1024!", self.build("Secret{номер}!", "1024"))
        self.assertEqual("Secret3001!", self.build("Secret{SIP номер}!", " 3001 "))

    def test_placeholder_can_stand_anywhere(self):
        self.assertEqual("1024-pbx", self.build("{номер}-pbx", "1024"))
        self.assertEqual("a1024b1024c", self.build("a{n}b{n}c", "1024"))

    def test_empty_parts_give_empty_password(self):
        self.assertEqual("", self.build("", "1024"))
        self.assertEqual("", self.build("pwd", ""))


class SaveUserSipSettingsTests(unittest.TestCase):
    def setUp(self):
        self.ns = _database_namespace({
            "save_user_sip_settings", "_mask_sip_secret", "_sip_operator_row",
            "normalize_sip_domain",
        })
        self.db = _StubDb(self.ns)
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.current = dict(OPERATOR_STATE)
        self.owners = {}
        self.checked = []
        self.db.get_sip_operator = lambda user_id: dict(self.current)
        # Домен и база пароля берутся у отдела; общий ярус читать больше нечем.
        self.db.get_sip_config = _no_common_tier
        self.login_owner = None
        self.checked_logins = []

        def _find(entries, exclude_user_ids=None):
            self.checked = list(entries)
            self.excluded = exclude_user_ids
            return self.owners
        self.db.find_sip_number_owners = _find

        def _find_login(sip_login, exclude_user_ids=None):
            self.checked_logins.append((sip_login, exclude_user_ids))
            return self.login_owner
        self.db.find_sip_login_owner = _find_login

    def _save(self, payload):
        return self.ns["save_user_sip_settings"](self.db, 41, payload, changed_by=7)

    def _sql(self):
        return [sql for sql, _ in self.db.cursor.calls]

    def test_duplicate_number_is_refused_with_owner_and_domain(self):
        # Домен теперь берётся у отдела: без него пара «номер + домен» вообще
        # не проверяется (см. test_a_pair_without_a_domain_is_not_checked).
        self.current["department_sip_server"] = "sip.local"
        self.owners = {("1088", "sip.local"): {"user_id": 5, "name": "Пётр", "kind": "main"}}
        with self.assertRaises(ValueError) as ctx:
            self._save({"sip_number": "1088"})
        self.assertIn("Пётр", str(ctx.exception))
        self.assertIn("sip.local", str(ctx.exception))
        self.assertEqual([("1088", "sip.local")], self.checked)   # регистр снят
        self.assertEqual([], self.db.cursor.calls)

    def test_a_pair_without_a_domain_is_not_checked(self):
        """Отдел без настроек даёт пустой домен — сверять такую пару нельзя.

        Раньше пустоту закрывал общий ярус. Теперь его нет, и если считать
        пару «1088@» настоящей, все ненастроенные отделы схлопнутся в один
        домен и начнут отбирать номера друг у друга на ровном месте.
        """
        self.assertEqual("", self.current["department_sip_server"])
        self._save({"sip_number": "1088"})
        self.assertEqual([], self.checked)
        # Сохранение при этом проходит: номер в users всё равно меняется.
        self.assertTrue(any("UPDATE users SET sip_number" in s for s in self._sql()))

    def test_same_number_on_another_domain_is_rechecked(self):
        """Номер тот же, а домен новый — на новой АТС он может быть занят."""
        self._save({"sip_domain": "PBX.Other"})
        self.assertEqual([("1024", "pbx.other")], self.checked)

    def test_department_domain_is_the_default_for_its_operators(self):
        """У отдела своя АТС — «пустой» домен сотрудника означает именно её.

        И это теперь единственный источник по умолчанию: общего яруса,
        который подставлялся следующим, больше нет.
        """
        self.current["department_sip_server"] = "PBX.Sales"
        self._save({"sip_number": "1088"})
        self.assertEqual([("1088", "pbx.sales")], self.checked)
        self.assertEqual([41], self.excluded)   # себя из проверки исключаем

    def test_autodial_has_its_own_default_domain(self):
        """У автодозвона отдельная АТС — «пустой» домен второго номера = она."""
        self.current["department_sip_server"] = "pbx.sales"
        self.current["department_autodial_server"] = "dialer.sales"
        self._save({"autodial_number": "3001"})
        self.assertEqual([("3001", "dialer.sales")], self.checked)

    def test_autodial_falls_back_to_the_main_domain(self):
        self.current["department_sip_server"] = "pbx.sales"
        self._save({"autodial_number": "3001"})
        self.assertEqual([("3001", "pbx.sales")], self.checked)

    def test_same_number_on_both_accounts_is_fine_across_pbx(self):
        """Основной и автодозвон могут совпасть, если АТС разные."""
        self.current["department_autodial_server"] = "dialer.sales"
        self._save({"autodial_number": "1024"})
        self.assertTrue(any("INSERT INTO user_sip_settings" in s for s in self._sql()))

    def test_moving_to_another_domain_does_not_flag_the_old_pair(self):
        self.current.update({"sip_domain": "pbx.other"})
        self._save({"sip_password": "x"})
        self.assertEqual([], self.checked)   # пара не менялась — проверять нечего

    def test_autodial_may_repeat_the_main_number_on_another_domain(self):
        with self.assertRaises(ValueError):
            self._save({"autodial_number": "1024"})
        self._save({"autodial_number": "1024", "autodial_domain": "pbx.other"})
        self.assertTrue(any("INSERT INTO user_sip_settings" in s for s in self._sql()))

    def test_unchanged_payload_writes_nothing(self):
        result = self._save(dict(self.current))
        self.assertEqual([], self.db.cursor.calls)
        self.assertEqual(self.current["sip_number"], result["sip_number"])

    def test_number_change_updates_users_profile_and_history(self):
        self._save({"sip_number": "1088"})
        sql = self._sql()
        self.assertTrue(any("UPDATE users SET sip_number" in s for s in sql))
        self.assertTrue(any("UPDATE operator_profiles SET sip_number" in s for s in sql))
        self.assertTrue(any("INSERT INTO user_history" in s for s in sql))

    def test_personal_params_are_upserted_and_number_kept_in_users(self):
        self._save({"sip_password": "s3cret", "autodial_number": "2024"})
        sql = self._sql()
        upsert = next(s for s in sql if "INSERT INTO user_sip_settings" in s)
        self.assertIn("ON CONFLICT (user_id) DO UPDATE SET", upsert)
        self.assertFalse(any("DELETE FROM user_sip_settings" in s for s in sql))
        # Основной номер не переезжает в user_sip_settings — он остаётся в users.
        self.assertNotIn("sip_number", upsert.split("VALUES", 1)[0])

    def test_disabling_fop2_alone_keeps_the_row(self):
        """Выключенный FOP2 — сам по себе повод хранить строку.

        Строка user_sip_settings удаляется, когда персональных настроек не осталось.
        Если не считать выключенный флаг настройкой, запись с одним лишь
        fop2_enabled=FALSE будет тут же удалена, и сотрудник молча вернётся
        в очереди Asterisk.
        """
        self._save({"fop2_enabled": False})
        calls = self.db.cursor.calls
        self.assertFalse(any("DELETE FROM user_sip_settings" in s for s, _ in calls))
        sql, params = next(
            (s, p) for s, p in calls if "INSERT INTO user_sip_settings" in s
        )
        self.assertIn("fop2_enabled", sql)
        # Порядок параметров upsert: user_id, пароль, домен, SIP-логин, номер
        # автодозвона, его пароль и домен, флаг FOP2, автор правки. Логин
        # Binotel встал третьим, поэтому флаг переехал с 6-й позиции на 7-ю.
        self.assertIs(False, params[7])

    def test_only_fop2_change_is_not_treated_as_no_op(self):
        """PUT, где поменялся только флаг, обязан дойти до базы.

        Сохранение выходит раньше времени, если считает, что ничего не изменилось.
        """
        self._save({"fop2_enabled": False})
        self.assertNotEqual([], self.db.cursor.calls)

    def test_turning_fop2_back_on_releases_the_row(self):
        """Флаг вернули в норму, других персональных настроек нет — строка не нужна."""
        self.current["fop2_enabled"] = False
        self._save({"fop2_enabled": True})
        self.assertTrue(any("DELETE FROM user_sip_settings" in s for s in self._sql()))

    def test_fop2_flag_is_written_to_history(self):
        """Флаг меняет маршрутизацию звонков — в истории он должен быть виден."""
        self._save({"fop2_enabled": False})
        sql, params = next(
            (s, p) for s, p in self.db.cursor.calls if "INSERT INTO sip_config_history" in s
        )
        snapshot = json.loads(params[2])
        self.assertIs(False, snapshot["fop2_enabled"])

    def test_new_columns_are_appended_and_never_wedged_into_the_middle(self):
        """_sip_operator_row читает row по индексам — порядок колонок и есть контракт.

        Провайдер, SIP-логин и учётка кабинета Binotel добавлялись уже после
        fop2_enabled. Вставка любой из них в середину SELECT сдвинула бы всё,
        что ниже, и раздел начал бы показывать чужие значения — например,
        подставил бы имя редактора в поле домена.
        """
        columns = _select_columns(self.ns["_SIP_OPERATOR_SELECT"])
        # Столько колонок, сколько индексов читает маппер: лишняя (или забытая)
        # колонка — это и есть сдвиг.
        self.assertEqual(_max_row_index("_sip_operator_row") + 1, len(columns))
        # Прежний «последний» флаг стоит там, где его ждёт row[21], а хвост
        # занят полями Binotel — в том порядке, в каком их дописывали.
        self.assertTrue(columns[21].endswith("AS fop2_enabled"), columns[21])
        self.assertTrue(columns[22].endswith("AS department_provider"), columns[22])
        self.assertTrue(columns[23].endswith("AS sip_login"), columns[23])
        self.assertTrue(
            columns[-1].endswith("AS has_binotel_cabinet_password"), columns[-1])

    def test_the_cabinet_password_never_leaves_the_database(self):
        """Панель получает только признак «пароль задан».

        Список операторов видит каждый глава отдела и СВ ОП; сам пароль от
        кабинета Binotel — это доступ к настройкам телефонии всей компании,
        и в выборку раздела он попадать не должен ни в каком виде.
        """
        select = self.ns["_SIP_OPERATOR_SELECT"]
        columns = _select_columns(select)
        self.assertTrue(all("b.cabinet_password" not in c
                            for c in columns if "IS NOT NULL" not in c), columns)
        self.assertIn("(NULLIF(b.cabinet_password, '') IS NOT NULL)", columns[-1])

    def test_sip_login_is_forced_empty_on_a_local_pbx(self):
        """У локальной АТС логин и есть номер — отдельное поле там только мусор.

        Отдел могли перевести на Binotel и обратно. Оставшийся от той жизни
        логин увёл бы телефон регистрироваться чужой учёткой провайдера, а не
        своим внутренним номером.
        """
        self.current["sip_login"] = "68m77pnw"
        self._save({"sip_login": "68m77pnw", "sip_password": "own"})
        _, params = next(
            (s, p) for s, p in self.db.cursor.calls if "INSERT INTO user_sip_settings" in s)
        self.assertEqual("", params[3])
        # Раз логина нет, то и на занятость его проверять незачем.
        self.assertEqual([], self.checked_logins)

    def test_cabinet_fields_are_ignored_but_not_wiped_on_a_local_pbx(self):
        """Поля кабинета в «Таксопарках» не показываются — и не должны стираться.

        Форма отдаёт их пустыми просто потому, что их там нет. Если принять эту
        пустоту за «очистить», перевод отдела на Binotel и обратно молча снёс бы
        всем учётки кабинета вместе с возможностью менять статус.
        """
        self.current["binotel_cabinet_login"] = "op@tez.kz"
        self._save({"binotel_cabinet_login": "", "binotel_employee_id": "",
                    "sip_password": "own"})
        self.assertFalse(any("INSERT INTO binotel_user_accounts" in s for s in self._sql()))
        _, params = next(
            (s, p) for s, p in self.db.cursor.calls if "INSERT INTO sip_config_history" in s)
        self.assertEqual("op@tez.kz", json.loads(params[2])["binotel_cabinet_login"])

    def test_clearing_every_override_removes_the_row(self):
        self.current.update({"sip_password": "s3cret", "autodial_number": "2024"})
        self._save({"sip_password": "", "autodial_number": ""})
        self.assertTrue(any("DELETE FROM user_sip_settings" in s for s in self._sql()))

    def test_history_row_is_scoped_to_the_operator_and_masks_password(self):
        self._save({"sip_password": "supersecret"})
        sql, params = next(
            (s, p) for s, p in self.db.cursor.calls if "INSERT INTO sip_config_history" in s
        )
        self.assertIn("target_user_id", sql)
        self.assertEqual(7, params[0])
        self.assertEqual(41, params[1])
        snapshot = json.loads(params[2])
        self.assertNotIn("supersecret", params[2])
        self.assertTrue(snapshot["sip_password"].endswith("et"))
        # Провайдер отдела виден в истории: по ней разбирают, почему у
        # сотрудника «пропал» автодозвон после перевода отдела на Binotel.
        self.assertEqual("asterisk", snapshot["provider"])


class SaveUserSipSettingsBinotelTests(unittest.TestCase):
    """Карточка сотрудника в отделе на Binotel: другой набор полей.

    Провайдер сохранение берёт САМО, из отдела сотрудника, — payload'у тут не
    верят. Иначе форма «Таксопарков», случайно открытая на тезовце, вернула бы
    ему автодозвон и вход в FOP2, которых у провайдера нет.
    """

    def setUp(self):
        self.ns = _database_namespace({
            "save_user_sip_settings", "_mask_sip_secret", "_sip_operator_row",
            "normalize_sip_domain",
        })
        self.db = _StubDb(self.ns)
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.current = dict(OPERATOR_STATE)
        self.current.update({
            "department_provider": "binotel",
            "department_name": "Тез КЦ",
            "sip_number": "6715",
            # Устоявшееся состояние тезовца: FOP2 у него уже выключен.
            "fop2_enabled": False,
        })
        self.db.get_sip_operator = lambda user_id: dict(self.current)
        self.db.get_sip_config = _no_common_tier
        self.db.find_sip_number_owners = lambda entries, exclude_user_ids=None: {}
        self.login_owner = None
        self.checked_logins = []

        def _find_login(sip_login, exclude_user_ids=None):
            self.checked_logins.append((sip_login, exclude_user_ids))
            return self.login_owner
        self.db.find_sip_login_owner = _find_login

    def _save(self, payload):
        return self.ns["save_user_sip_settings"](self.db, 41, payload, changed_by=7)

    def _call(self, marker):
        return next((c for c in self.db.cursor.calls if marker in c[0]), None)

    def test_the_login_is_stored_verbatim(self):
        """Логин провайдера через normalize_sip_identifier не гоняем.

        Тот режет «@» и «:» и рубит строку на 64 символах — а здесь это
        выданная Binotel непрозрачная строка: «почищенный» логин становится
        просто чужим, и регистрация не проходит.
        """
        self._save({"sip_login": "68m77pnw@sip52:5060", "sip_password": "V3ry"})
        sql, params = self._call("INSERT INTO user_sip_settings")
        self.assertIn("sip_login", sql.split("VALUES", 1)[0])
        self.assertEqual("68m77pnw@sip52:5060", params[3])

    def test_autodial_is_wiped_and_fop2_forced_off(self):
        """Отдел могли перевести на Binotel с локальной АТС.

        Остатки прошлой жизни — второй номер и включённый FOP2 — телефон
        честно попробует поднять: аккаунт в никуда плюс вечный спиннер входа
        в FOP2. Гасим их на записи, а не надеемся на форму.
        """
        self.current.update({
            "autodial_number": "3001", "autodial_password": "own",
            "autodial_domain": "dialer.sales", "fop2_enabled": True,
        })
        self._save({"sip_login": "68m77pnw", "autodial_number": "3001",
                    "autodial_password": "own", "fop2_enabled": True})
        _, params = self._call("INSERT INTO user_sip_settings")
        self.assertEqual(("", "", ""), (params[4], params[5], params[6]))
        self.assertIs(False, params[7])

    def test_a_login_taken_by_someone_else_is_refused(self):
        """Логин уникален глобально: двое с одним отбирают регистрацию друг у друга."""
        self.login_owner = {"user_id": 9, "name": "Чужой"}
        with self.assertRaises(ValueError) as ctx:
            self._save({"sip_login": "68m77pnw"})
        self.assertIn("Чужой", str(ctx.exception))
        self.assertIn("68m77pnw", str(ctx.exception))
        self.assertEqual([], self.db.cursor.calls)

    def test_an_unchanged_login_is_not_rechecked(self):
        """Иначе сотрудник спотыкался бы о собственный логин при любой правке."""
        self.current["sip_login"] = "68m77pnw"
        self._save({"binotel_employee_id": "480431", "binotel_cabinet_login": "op@tez.kz"})
        self.assertEqual([], self.checked_logins)

    # ── Учётка веб-кабинета ──────────────────────────────────────────────────

    def test_an_empty_cabinet_password_does_not_erase_the_stored_one(self):
        """Пустой пароль = «не менять» — ровно так ведёт себя и сам кабинет.

        Глава отдела правит employeeID, пароль не вводит (его и не показывают).
        Прими эту пустоту за значение — и телефон потеряет доступ в кабинет,
        то есть перестанет переключать статусы.
        """
        self.current.update({"binotel_cabinet_login": "op@tez.kz",
                             "has_binotel_cabinet_password": True})
        self._save({"binotel_employee_id": "480431", "binotel_cabinet_password": ""})
        sql, params = self._call("INSERT INTO binotel_user_accounts")
        self.assertIn("cabinet_password = COALESCE( NULLIF(EXCLUDED.cabinet_password, ''), "
                      "binotel_user_accounts.cabinet_password)", sql)
        self.assertEqual("", params[2])   # пустую строку съест NULLIF на стороне БД

    def test_an_empty_cabinet_password_alone_is_not_a_change(self):
        """«Сохранить», ничего не тронув, не должно писать в базу вообще.

        Если считать пустой пароль изменением, каждое открытие карточки
        плодило бы строку в истории и переставляло updated_at/updated_by.
        """
        self.current["binotel_cabinet_login"] = "op@tez.kz"
        self._save({"binotel_cabinet_password": "   "})
        self.assertEqual([], self.db.cursor.calls)

    def test_a_new_cabinet_password_is_written(self):
        self._save({"binotel_cabinet_login": "op@tez.kz",
                    "binotel_cabinet_password": "supersecret"})
        _, params = self._call("INSERT INTO binotel_user_accounts")
        self.assertEqual("supersecret", params[2])

    def test_the_cabinet_password_never_reaches_the_history(self):
        """Вкладку «История» видит вся панель, а это доступ в кабинет провайдера.

        Маскировать его, как SIP-пароль, мало: даже «su…et» — подсказка.
        В историю уходит только факт замены.
        """
        self._save({"binotel_cabinet_login": "op@tez.kz",
                    "binotel_cabinet_password": "supersecret"})
        _, params = self._call("INSERT INTO sip_config_history")
        self.assertNotIn("supersecret", params[2])
        snapshot = json.loads(params[2])
        self.assertNotIn("binotel_cabinet_password", snapshot)
        self.assertIs(True, snapshot["binotel_cabinet_password_set"])
        # Логин и employeeID секретами не считаются — по ним и разбирают инциденты.
        self.assertEqual("op@tez.kz", snapshot["binotel_cabinet_login"])
        self.assertEqual("binotel", snapshot["provider"])

    def test_the_history_marks_a_save_without_a_new_password(self):
        """Обратный случай того же флага: пароль не трогали — и это видно."""
        self.current["binotel_cabinet_login"] = "op@tez.kz"
        self._save({"binotel_employee_id": "480431"})
        _, params = self._call("INSERT INTO sip_config_history")
        self.assertIs(False, json.loads(params[2])["binotel_cabinet_password_set"])


class BulkUpdateSipOverridesTests(unittest.TestCase):
    """Массовое изменение пароля/домена и входа в FOP2 (Ctrl-выбор в списке)."""

    def setUp(self):
        self.ns = _database_namespace({
            "bulk_update_user_sip_overrides", "_mask_sip_secret", "normalize_sip_domain",
        })
        # (id, name, sip_password, sip_domain, autodial_number, autodial_password,
        #  autodial_domain, sip_number, department_sip_server, department_autodial_server,
        #  fop2_enabled, sip_login)
        # sip_login массовая правка не меняет, но обязана дотащить до апсерта —
        # иначе чистка пароля посчитает строку пустой и удалит учётку Binotel.
        rows = [
            (1, 'Иван', '', '', '2024', '', '', '1024', '', '', True, ''),        # автодозвон есть, персональных нет
            (2, 'Пётр', 'own', 'pbx.old', '', '', '', '1088', '', '', True, ''),  # были персональные значения
            (3, 'Мария', '', 'pbx.new', '', '', '', '1099', '', '', True, ''),    # уже с нужным доменом — не трогаем
        ]
        self.db = _StubDb(self.ns, _FakeCursor(rows))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        # Общего яруса нет: домен по умолчанию берётся только у отдела.
        self.db.get_sip_config = _no_common_tier
        self.db.get_sip_operators_by_ids = lambda ids: [{"id": i} for i in ids]
        self.owners = {}
        self.checked = []

        def _find(entries, exclude_user_ids=None):
            self.checked = list(entries)
            self.excluded = exclude_user_ids
            return self.owners
        self.db.find_sip_number_owners = _find

    def _bulk(self, payload, ids=(1, 2, 3)):
        return self.ns["bulk_update_user_sip_overrides"](self.db, list(ids), payload, changed_by=7)

    def _batched(self, marker):
        return next((c for c in self.db.cursor.calls if marker in c[0]), None)

    def test_empty_selection_or_fields_are_refused(self):
        with self.assertRaises(ValueError):
            self._bulk({"sip_domain": "pbx.new"}, ids=())
        with self.assertRaises(ValueError):
            self._bulk({"sip_number": "1024"})

    def test_only_listed_fields_change_and_numbers_survive(self):
        self._bulk({"sip_domain": "pbx.new"})
        _, values, _ = self._batched("INSERT INTO user_sip_settings")
        by_id = {row[0]: row for row in values}
        # Домен применён обоим, у кого он отличался; сотрудник 3 уже такой — пропущен.
        self.assertEqual({1, 2}, set(by_id))
        self.assertEqual("pbx.new", by_id[1][2])
        # Кортеж апсерта: id, пароль, домен, SIP-логин, номер автодозвона, его
        # пароль и домен, флаг FOP2, автор. Логин встал третьим, поэтому всё
        # начиная с автодозвона сдвинулось на одну позицию вправо.
        self.assertEqual("2024", by_id[1][4])   # номер автодозвона не тронут
        self.assertEqual("own", by_id[2][1])    # персональный пароль не тронут

    def test_domain_change_rechecks_numbers_on_the_new_pbx(self):
        """Переезд на другой домен может столкнуть номера с уже занятыми там."""
        self._bulk({"sip_domain": "pbx.new"})
        self.assertIn(("1024", "pbx.new"), self.checked)
        # Автодозвон не переезжает, а своей АТС у отдела нет — домен пустой.
        # Раньше сюда подставлялся общий sip_server; яруса больше нет, и такую
        # пару SQL занятости просто не сверяет (WHERE w.dom <> '').
        self.assertIn(("2024", ""), self.checked)
        self.assertEqual([1, 2, 3], self.excluded)           # своих из проверки исключаем

    def test_conflict_on_the_target_domain_blocks_the_whole_batch(self):
        self.owners = {("1024", "pbx.new"): {"user_id": 9, "name": "Чужой", "kind": "main"}}
        with self.assertRaises(ValueError) as ctx:
            self._bulk({"sip_domain": "pbx.new"})
        self.assertIn("Чужой", str(ctx.exception))
        self.assertIn("pbx.new", str(ctx.exception))
        self.assertEqual([], [c for c in self.db.cursor.calls if "INSERT" in c[0]])

    def test_department_domain_is_the_default_in_bulk_too(self):
        """Сотрудник отдела с своей АТС считается на её домене, а не на общем."""
        rows = [(1, 'Иван', '', '', '', '', '', '1024', 'pbx.sales', '', True, '')]
        self.db = _StubDb(self.ns, _FakeCursor(rows))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.db.get_sip_config = _no_common_tier
        self.db.get_sip_operators_by_ids = lambda ids: []
        self.db.find_sip_number_owners = lambda entries, exclude_user_ids=None: (
            self.checked.extend(entries) or {})
        self.checked = []
        self._bulk({"sip_password": "x"}, ids=(1,))
        self.assertEqual([("1024", "pbx.sales")], self.checked)

    def test_two_selected_landing_on_one_number_and_domain_are_refused(self):
        rows = [
            (1, 'Иван', '', '', '', '', '', '1024', '', '', True, ''),
            (2, 'Пётр', '', 'pbx.old', '', '', '', '1024', '', '', True, ''),   # тот же номер, но другая АТС
        ]
        self.db = _StubDb(self.ns, _FakeCursor(rows))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.db.get_sip_config = _no_common_tier
        self.db.find_sip_number_owners = lambda entries, exclude_user_ids=None: {}
        with self.assertRaises(ValueError) as ctx:
            self._bulk({"sip_domain": "pbx.new"}, ids=(1, 2))
        self.assertIn("достанется двоим", str(ctx.exception))
        self.assertIn("Иван", str(ctx.exception))
        self.assertIn("Пётр", str(ctx.exception))

    def test_moving_onto_a_selected_neighbours_number_is_refused(self):
        """Дыра в защите от дублей: find_sip_number_owners исключает всю выборку,
        поэтому переезжающий вставал ровно на номер того выбранного, кто с места
        не двинулся, — молча и с 200 в ответ. Стоящие занимают свои пары."""
        rows = [
            (1, 'Иван', '', 'pbx.new', '', '', '', '1024', '', '', True, ''),   # уже там — не двинется
            (2, 'Пётр', '', 'pbx.old', '', '', '', '1024', '', '', True, ''),   # переезжает на pbx.new
        ]
        self.db = _StubDb(self.ns, _FakeCursor(rows))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.db.get_sip_config = _no_common_tier
        self.db.get_sip_operators_by_ids = lambda ids: [{"id": i} for i in ids]
        self.db.find_sip_number_owners = lambda entries, exclude_user_ids=None: {}
        with self.assertRaises(ValueError) as ctx:
            self._bulk({"sip_domain": "pbx.new"}, ids=(1, 2))
        self.assertIn("достанется двоим", str(ctx.exception))
        self.assertIn("Иван", str(ctx.exception))
        self.assertEqual([], [c for c in self.db.cursor.calls if "INSERT" in c[0]])

    def test_two_standing_on_one_pair_do_not_block_each_other(self):
        """Задвоенная пара сама по себе пачку не валит: если оба выбранных с места
        не двигаются, спорить не о чем — было так и осталось."""
        rows = [
            (1, 'Иван', '', '', '', '', '', '1024', '', '', True, ''),
            (2, 'Пётр', '', '', '', '', '', '1024', '', '', True, ''),
        ]
        self.db = _StubDb(self.ns, _FakeCursor(rows))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.db.get_sip_config = _no_common_tier
        self.db.get_sip_operators_by_ids = lambda ids: [{"id": i} for i in ids]
        self.db.find_sip_number_owners = lambda entries, exclude_user_ids=None: {}
        self._bulk({"fop2_enabled": False}, ids=(1, 2))
        _, values, _ = self._batched("INSERT INTO user_sip_settings")
        self.assertEqual({1, 2}, {row[0] for row in values})

    def test_everything_runs_in_batches_not_per_user(self):
        self._bulk({"sip_domain": "pbx.new"})
        inserts = [c for c in self.db.cursor.calls if "INSERT INTO user_sip_settings" in c[0]]
        history = [c for c in self.db.cursor.calls if "INSERT INTO sip_config_history" in c[0]]
        self.assertEqual(1, len(inserts))
        self.assertEqual(1, len(history))
        self.assertEqual(2, len(history[0][1]))

    def test_clearing_the_last_override_removes_the_row(self):
        self._bulk({"sip_password": "", "sip_domain": ""}, ids=(2,))
        deletes = [c for c in self.db.cursor.calls if "DELETE FROM user_sip_settings" in c[0]]
        self.assertEqual(1, len(deletes))
        self.assertEqual(([2],), deletes[0][1])

    def test_row_with_a_number_is_kept_even_with_empty_overrides(self):
        # У сотрудника 1 есть номер автодозвона — строку удалять нельзя.
        self._bulk({"sip_password": "", "sip_domain": ""}, ids=(1,))
        self.assertIsNone(self._batched("DELETE FROM user_sip_settings"))
        self.assertIsNone(self._batched("INSERT INTO user_sip_settings"))

    def test_history_is_scoped_per_operator_and_masks_the_password(self):
        self._bulk({"sip_password": "supersecret"}, ids=(1,))
        _, values, template = self._batched("INSERT INTO sip_config_history")
        changed_by, target_user_id, snapshot = values[0]
        self.assertEqual((7, 1), (changed_by, target_user_id))
        self.assertNotIn("supersecret", snapshot)
        self.assertTrue(json.loads(snapshot)["bulk"])
        self.assertIn("%s::jsonb", template)


    # ── Вход в FOP2 мультивыбором ────────────────────────────────────────────
    # Ради этого раздел и получил массовую правку: выключать FOP2 приходилось
    # заходя в карточку каждого сотрудника по отдельности.

    def _only_fop2_off(self, sip_password='', sip_domain=''):
        """Подменяет выборку на одного Петра с выключенным FOP2."""
        rows = [(2, 'Пётр', sip_password, sip_domain, '', '', '', '1088', '', '', False, '')]
        self.db = _StubDb(self.ns, _FakeCursor(rows))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.db.get_sip_config = _no_common_tier
        self.db.get_sip_operators_by_ids = lambda ids: [{"id": i} for i in ids]
        self.db.find_sip_number_owners = lambda entries, exclude_user_ids=None: {}

    def test_the_flag_alone_is_a_valid_bulk_change(self):
        """Один только выключатель — уже повод для записи, а не «нет полей»."""
        self._bulk({"fop2_enabled": False})
        _, values, _ = self._batched("INSERT INTO user_sip_settings")
        self.assertEqual({1, 2, 3}, {row[0] for row in values})
        self.assertEqual([False, False, False], [row[7] for row in values])
        # Пароль и домен у каждого остались своими: их в payload'е не было.
        by_id = {row[0]: row for row in values}
        self.assertEqual("own", by_id[2][1])
        self.assertEqual("pbx.old", by_id[2][2])

    def test_flag_only_change_does_not_recheck_the_numbers(self):
        """Номера не двигаются, значит и сверять занятость нечего: иначе давно
        задвоенная в данных пара завалила бы всю пачку чужим номером."""
        self._bulk({"fop2_enabled": False})
        self.assertEqual([], self.checked)

    def test_conflicting_numbers_do_not_block_turning_fop2_off(self):
        """Два выбранных с одним номером на одной АТС — не повод отказать в
        выключении FOP2: пара как была задвоена, так и останется."""
        rows = [
            (1, 'Иван', '', '', '', '', '', '1024', '', '', True, ''),
            (2, 'Пётр', '', '', '', '', '', '1024', '', '', True, ''),
        ]
        self.db = _StubDb(self.ns, _FakeCursor(rows))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.db.get_sip_config = _no_common_tier
        self.db.get_sip_operators_by_ids = lambda ids: [{"id": i} for i in ids]
        self.db.find_sip_number_owners = lambda entries, exclude_user_ids=None: {}
        self._bulk({"fop2_enabled": False}, ids=(1, 2))
        _, values, _ = self._batched("INSERT INTO user_sip_settings")
        self.assertEqual({1, 2}, {row[0] for row in values})

    def test_a_string_from_the_form_turns_the_flag_off(self):
        """JSON присылает bool, multipart — '0': str(False) сошла бы за истину."""
        self._bulk({"fop2_enabled": "0"}, ids=(1,))
        _, values, _ = self._batched("INSERT INTO user_sip_settings")
        self.assertIs(False, values[0][7])

    def test_an_unchanged_flag_writes_nothing(self):
        """FOP2 у всех и так включён — повторное «включить» ничего не пишет."""
        self._bulk({"fop2_enabled": True})
        self.assertIsNone(self._batched("INSERT INTO user_sip_settings"))
        self.assertIsNone(self._batched("DELETE FROM user_sip_settings"))
        self.assertIsNone(self._batched("INSERT INTO sip_config_history"))

    def test_clearing_overrides_keeps_the_row_of_someone_without_fop2(self):
        """Тот самый молчаливый откат: чистка пароля удаляла строку целиком, и
        сотрудник, снятый с очередей, возвращался в них сам собой."""
        self._only_fop2_off(sip_password='own', sip_domain='pbx.old')
        self._bulk({"sip_password": "", "sip_domain": ""}, ids=(2,))
        self.assertIsNone(self._batched("DELETE FROM user_sip_settings"))
        _, values, _ = self._batched("INSERT INTO user_sip_settings")
        self.assertIs(False, values[0][7])

    def test_turning_the_flag_back_on_releases_the_row(self):
        """Выключатель был единственным персональным значением — пустышку не храним."""
        self._only_fop2_off()
        self._bulk({"fop2_enabled": True}, ids=(2,))
        deletes = [c for c in self.db.cursor.calls if "DELETE FROM user_sip_settings" in c[0]]
        self.assertEqual(1, len(deletes))
        self.assertEqual(([2],), deletes[0][1])

    def test_the_flag_is_written_to_history(self):
        """Флаг решает, доходят ли до сотрудника звонки из очередей, — в истории
        обязано быть видно, кто и когда его снял, даже в массовой правке."""
        self._bulk({"fop2_enabled": False}, ids=(1,))
        _, values, _ = self._batched("INSERT INTO sip_config_history")
        snapshot = json.loads(values[0][2])
        self.assertIs(False, snapshot["fop2_enabled"])
        self.assertTrue(snapshot["bulk"])

    def test_the_upsert_carries_the_flag_column(self):
        """Колонка обязана быть и в списке INSERT, и в DO UPDATE: без второго
        массовая правка пароля возвращала бы FOP2 всем, кому его выключали."""
        self._bulk({"fop2_enabled": False}, ids=(1,))
        sql, values, template = self._batched("INSERT INTO user_sip_settings")
        self.assertIn("fop2_enabled, updated_by, updated_at", sql)
        self.assertIn("fop2_enabled = EXCLUDED.fop2_enabled", sql)
        # Плейсхолдеров ровно столько же, сколько полей в кортеже.
        self.assertEqual(len(values[0]), template.count("%s"))

    def test_the_upsert_carries_the_login_column_too(self):
        """SIP-логин обязан быть и в INSERT, и в DO UPDATE.

        Массовая правка его не меняет, но перезаписывает строку целиком: без
        колонки в INSERT логин ушёл бы в NULL, а без DO UPDATE — потерялся при
        любом апсерте. И то и другое отбирает у оператора Тез регистрацию.
        """
        self._bulk({"fop2_enabled": False}, ids=(1,))
        sql, values, _ = self._batched("INSERT INTO user_sip_settings")
        self.assertIn("sip_login", sql.split("VALUES", 1)[0])
        self.assertIn("sip_login = EXCLUDED.sip_login", sql)

    def test_the_login_of_a_binotel_operator_survives_a_password_cleanup(self):
        """Тот же молчаливый откат, что был у FOP2, но дороже.

        Чистка пароля/домена считает строку пустой и отправляет её в DELETE.
        Если логин Binotel не учитывать, оператор Тез потеряет учётку у
        провайдера — и телефон перестанет регистрироваться вообще.
        """
        rows = [(2, 'Пётр', 'own', 'pbx.old', '', '', '', '6715', '', '', True, '68m77pnw')]
        self.db = _StubDb(self.ns, _FakeCursor(rows))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]
        self.db.get_sip_config = _no_common_tier
        self.db.get_sip_operators_by_ids = lambda ids: [{"id": i} for i in ids]
        self.db.find_sip_number_owners = lambda entries, exclude_user_ids=None: {}
        self._bulk({"sip_password": "", "sip_domain": ""}, ids=(2,))
        self.assertIsNone(self._batched("DELETE FROM user_sip_settings"))
        _, values, _ = self._batched("INSERT INTO user_sip_settings")
        self.assertEqual("68m77pnw", values[0][3])


class ParseSipFlagTests(unittest.TestCase):
    """Разбор булева поля из payload'а — общий у карточки и массовой правки."""

    def setUp(self):
        self.parse = _database_namespace(set())["parse_sip_flag"]

    def test_absent_value_keeps_the_default(self):
        self.assertIs(True, self.parse(None, True))
        self.assertIs(False, self.parse(None, False))

    def test_bool_passes_through(self):
        self.assertIs(False, self.parse(False))
        self.assertIs(True, self.parse(True))

    def test_form_strings_are_understood(self):
        for value in ('0', 'false', 'FALSE', 'no', 'off', '', '  '):
            self.assertIs(False, self.parse(value), value)
        for value in ('1', 'true', 'on', 'yes'):
            self.assertIs(True, self.parse(value), value)


class FindSipNumberOwnersTests(unittest.TestCase):
    """SQL занятости: сравниваем пару «номер + домен».

    Пустой домен больше НЕ значит «общий»: общего яруса нет, и у отдела без
    своих настроек домен просто неизвестен. Такие пары из сравнения выпадают.
    """

    def setUp(self):
        self.ns = _database_namespace({"find_sip_number_owners", "normalize_sip_domain"})
        self.db = _StubDb(self.ns, _FakeCursor([]))
        self.db.normalize_sip_domain = self.ns["normalize_sip_domain"]

    def _call(self, entries, exclude=None):
        self.ns["find_sip_number_owners"](self.db, entries, exclude_user_ids=exclude)
        return self.db.cursor.calls[0]

    def test_nothing_to_check_makes_no_query(self):
        self.assertEqual({}, self.ns["find_sip_number_owners"](self.db, [('', 'x')]))
        self.assertEqual([], self.db.cursor.calls)

    def test_pairs_are_matched_on_number_and_domain(self):
        sql, params = self._call([('1024', 'PBX.Other'), ('2024', '')], exclude=[41])
        self.assertIn("JOIN taken t ON t.num = w.num AND t.dom = w.dom", sql)
        self.assertEqual(['1024', '2024'], params[0])
        self.assertEqual(['pbx.other', ''], params[1])   # регистр снят
        self.assertEqual([41], params[2])

    def test_domain_resolves_personal_then_department(self):
        """Цепочка укоротилась до двух звеньев: персональное → отдела.

        Раньше третьим шёл общий sip_config, и он же закрывал пустоту. Теперь
        общего яруса нет: sip_config в запросе не упоминается вовсе, а отдел
        без настроек честно даёт пустой домен.
        """
        sql, _ = self._call([('1024', '')])
        self.assertNotIn("sip_config", sql)   # sip_department_config — другая таблица
        self.assertIn("LEFT JOIN sip_department_config dc ON dc.department_id = u.department_id", sql)
        self.assertIn(
            "COALESCE( NULLIF(LOWER(TRIM(COALESCE(s.sip_domain, ''))), ''), "
            "NULLIF(LOWER(TRIM(COALESCE(dc.sip_server, ''))), ''), '') AS dom",
            sql,
        )

    def test_a_pair_without_a_domain_never_matches(self):
        """Пустой домен из сравнения выброшен — и это главное следствие правки.

        Без общего яруса у всех ненастроенных отделов домен пустой. Считай
        такую пару настоящей — и «1024@» одного таксопарка столкнётся с «1024@»
        другого: раздел начнёт отказывать в сохранении на ровном месте.
        """
        sql, _ = self._call([('1024', '')])
        self.assertIn("WHERE w.dom <> ''", sql)

    def test_autodial_domain_has_its_own_chain(self):
        """Автодозвон часто на отдельной АТС: свой домен отдела → домен основного."""
        sql, _ = self._call([('3001', '')])
        self.assertIn(
            "COALESCE( NULLIF(LOWER(TRIM(COALESCE(s.autodial_domain, ''))), ''), "
            "NULLIF(LOWER(TRIM(COALESCE(dc.autodial_server, ''))), ''), "
            "NULLIF(LOWER(TRIM(COALESCE(dc.sip_server, ''))), ''), '')",
            sql,
        )

    def test_both_accounts_are_scanned(self):
        sql, _ = self._call([('1024', '')])
        self.assertIn("'main'::text AS kind", sql)
        self.assertIn("'autodial'::text", sql)


class FindSipLoginOwnerTests(unittest.TestCase):
    """SIP-логин Binotel уникален ГЛОБАЛЬНО — в отличие от внутреннего номера.

    Номер живёт в пределах домена: на разных АТС одинаковые номера — норма.
    А логин это учётка на стороне провайдера, домена у неё нет вовсе; двое с
    одним логином отбирают регистрацию друг у друга, и звонки достаются тому,
    кто зарегистрировался последним. Отследить такое по жалобам почти нельзя,
    поэтому проверка обязана стоять на сохранении.
    """

    def setUp(self):
        self.ns = _database_namespace({"find_sip_login_owner"})

    def _call(self, rows, login="68m77pnw", exclude=None):
        db = _StubDb(self.ns, _FakeCursor(rows))
        owner = self.ns["find_sip_login_owner"](db, login, exclude_user_ids=exclude)
        return owner, db.cursor.calls

    def test_a_taken_login_names_its_owner(self):
        owner, calls = self._call([(5, "Пётр")], exclude=[41])
        self.assertEqual({"user_id": 5, "name": "Пётр"}, owner)
        sql, params = calls[0]
        # Регистр не спасает: «68M77PNW» — тот же логин у провайдера.
        self.assertIn("LOWER(TRIM(s.sip_login)) = LOWER(%s)", sql)
        # Домена в условии нет намеренно, см. docstring класса.
        self.assertNotIn("sip_domain", sql)
        self.assertEqual(("68m77pnw", [41]), params)

    def test_the_operator_himself_is_excluded(self):
        """Иначе сотрудник спотыкался бы о собственный логин.

        Сохранение карточки перепроверяет логин при каждой правке соседних
        полей; без исключения себя оператор с уже занесённым логином не смог бы
        поменять ни employeeID, ни пароль.
        """
        owner, calls = self._call([], exclude=[41])
        self.assertIsNone(owner)
        sql, params = calls[0]
        self.assertIn("u.id <> ALL(%s)", sql)
        self.assertEqual([41], params[1])

    def test_a_free_login_gives_none(self):
        self.assertIsNone(self._call([])[0])

    def test_an_empty_login_makes_no_query(self):
        """Пустое поле — это «логина ещё нет», а не «найди всех с пустым».

        Без этой отсечки первый же сохранённый пустой логин объявил бы себя
        владельцем, и завести логин не смог бы больше никто.
        """
        owner, calls = self._call([(5, "Пётр")], login="   ")
        self.assertIsNone(owner)
        self.assertEqual([], calls)


class DepartmentSipConfigTests(unittest.TestCase):
    """Настройки SIP по отделам: у каждой АТС свой набор номеров и свой провайдер.

    Провайдер — свойство отдела, а не сотрудника: раздел делится на «Таксопарки»
    (asterisk) и «Tez» (binotel), и от этой колонки зависит, какие поля у
    сотрудника вообще имеют смысл.
    """

    def setUp(self):
        self.ns = _database_namespace({
            "get_sip_department_configs", "update_sip_department_config", "_mask_sip_secret",
        })
        self.db = _StubDb(self.ns, _FakeCursor([]))
        self.state = {
            "department_id": 12, "department_name": "СЗоВ", "department_code": "szov",
            "sip_server": "", "base_password": "", "autodial_code": "", "autodial_server": "",
            "autodial_base_password": "",
            # Локальная АТС по умолчанию: Binotel только у Тез КЦ.
            "provider": "asterisk",
            "configured": False, "updated_at": None, "updated_by_name": None,
            "operators_count": 7,
        }
        self.db.get_sip_department_configs = lambda ids=None: [dict(self.state)]

    def _sql(self):
        return [sql for sql, _ in self.db.cursor.calls]

    def test_listing_marks_departments_without_their_own_settings(self):
        # Колонки: id, имя, код, сервер, база пароля, код автодозвона, его
        # сервер и база, ПРОВАЙДЕР, configured, updated_at, автор, счётчик.
        # provider встал перед configured — все флаги за ним сдвинулись.
        db = _StubDb(self.ns, _FakeCursor([
            (12, 'СЗоВ', 'szov', '', '', '', '', '', 'asterisk', False, None, None, 7),
            (367, 'Отдел продаж', 'op', 'pbx.sales', 'sales', '*77', 'dialer.sales',
             'Secret{номер}!', 'asterisk', True, None, 'Админ', 3),
            (900, 'Тез КЦ', 'tez', '', '', '', '', '', 'binotel', True, None, 'Админ', 5),
        ]))
        rows = self.ns["get_sip_department_configs"](db)
        self.assertEqual([False, True, True], [r["configured"] for r in rows])
        self.assertEqual([7, 3, 5], [r["operators_count"] for r in rows])
        # Провайдер доезжает до раздела: по нему он и делится на два.
        self.assertEqual(['asterisk', 'asterisk', 'binotel'], [r["provider"] for r in rows])
        sql, params = db.cursor.calls[0]
        self.assertIn("LEFT JOIN sip_department_config c ON c.department_id = d.id", sql)
        self.assertIn("(c.department_id IS NOT NULL) AS configured", sql)
        # Отделы без своей строки — тоже asterisk, а не пустая строка: иначе
        # раздел не смог бы решить, в какую половину их показать.
        self.assertIn("COALESCE(c.provider, 'asterisk') AS provider", sql)
        self.assertEqual(["operator", "trainee"], params[0])   # считаем только тех, у кого телефон

    def test_saving_upserts_and_validates_the_autodial_code(self):
        self.ns["update_sip_department_config"](self.db, 12, {"sip_server": "pbx.sales", "autodial_code": "*7 7"}, user_id=5)
        upsert = next(s for s in self._sql() if "INSERT INTO sip_department_config" in s)
        self.assertIn("ON CONFLICT (department_id) DO UPDATE SET", upsert)
        _, params = next((s, p) for s, p in self.db.cursor.calls if "INSERT INTO sip_department_config" in s)
        self.assertEqual("*77", params[3])    # пробел из копипаста вычищен
        with self.assertRaises(ValueError):
            self.ns["update_sip_department_config"](self.db, 12, {"autodial_code": "код"}, user_id=5)

    def test_clearing_every_field_drops_the_row_of_a_local_pbx_department(self):
        """У asterisk-отдела пустая строка ничего не значит — её убираем.

        Раньше это называлось «вернуть общие настройки»; общего яруса больше
        нет, и теперь пустая строка означает ровно «отдел не настроен».
        Хранить её незачем: она же и портила бы счётчик configured.
        """
        self.state.update({"sip_server": "pbx.sales", "configured": True})
        self.ns["update_sip_department_config"](
            self.db, 12,
            {"sip_server": "", "base_password": "", "autodial_code": "", "autodial_server": "",
             "autodial_base_password": ""},
            user_id=5)
        self.assertTrue(any("DELETE FROM sip_department_config" in s for s in self._sql()))

    def test_a_binotel_department_keeps_its_row_even_with_every_field_empty(self):
        """У Binotel полей отдела нет вообще: сервер, логин и пароль персональные.

        Строка такого отдела «пустая» по определению, и если удалять её по
        старому правилу, отдел потеряет сам признак провайдера — Тез молча
        уедет обратно на механику локальной АТС с паролем «база + номер».
        """
        self.state.update({"provider": "binotel", "sip_server": "pbx.old", "configured": True})
        self.ns["update_sip_department_config"](
            self.db, 12,
            {"sip_server": "", "base_password": "", "autodial_code": "", "autodial_server": "",
             "autodial_base_password": ""},
            user_id=5)
        self.assertFalse(any("DELETE FROM sip_department_config" in s for s in self._sql()))
        _, params = next((s, p) for s, p in self.db.cursor.calls
                         if "INSERT INTO sip_department_config" in s)
        self.assertEqual("binotel", params[6])

    def test_switching_a_department_to_binotel_is_stored_and_logged(self):
        """Перевод отдела — событие уровня «у всех поменялась телефония»."""
        self.ns["update_sip_department_config"](self.db, 12, {"provider": "binotel"}, user_id=5)
        _, params = next((s, p) for s, p in self.db.cursor.calls
                         if "INSERT INTO sip_department_config" in s)
        self.assertEqual("binotel", params[6])
        _, hist = next((s, p) for s, p in self.db.cursor.calls
                       if "INSERT INTO sip_config_history" in s)
        self.assertEqual("binotel", json.loads(hist[2])["provider"])

    def test_an_unknown_provider_is_refused_instead_of_silently_defaulting(self):
        """Опечатка в payload'е не должна тихо превращаться в «asterisk».

        Съеденный «binotell» отдал бы тезовцам пароль «база + номер» локальной
        АТС и сломал бы регистрацию всему отделу — молча и с 200 в ответ.
        """
        with self.assertRaises(ValueError):
            self.ns["update_sip_department_config"](self.db, 12, {"provider": "oktell"}, user_id=5)
        self.assertEqual([], self.db.cursor.calls)

    def test_the_provider_survives_an_ordinary_field_edit(self):
        """Правка сервера у binotel-отдела не должна ронять его обратно.

        provider приезжает из payload'а не всегда (форма «Таксопарков» его не
        шлёт вовсе), поэтому «не передали» обязано значить «оставить как есть».
        """
        self.state.update({"provider": "binotel", "configured": True})
        self.ns["update_sip_department_config"](self.db, 12, {"sip_server": "pbx.tez"}, user_id=5)
        _, params = next((s, p) for s, p in self.db.cursor.calls
                         if "INSERT INTO sip_department_config" in s)
        self.assertEqual("binotel", params[6])

    def test_autodial_server_and_base_are_stored_separately(self):
        self.ns["update_sip_department_config"](self.db, 12, {
            "sip_server": "pbx.sales", "autodial_server": "dialer.sales",
            "autodial_base_password": "Secret{номер}!",
        }, user_id=5)
        _, params = next((s, p) for s, p in self.db.cursor.calls if "INSERT INTO sip_department_config" in s)
        self.assertEqual("pbx.sales", params[1])
        self.assertEqual("dialer.sales", params[4])
        self.assertEqual("Secret{номер}!", params[5])

    def test_only_the_autodial_server_is_enough_to_configure_a_department(self):
        self.ns["update_sip_department_config"](self.db, 12, {"autodial_server": "dialer.sales"}, user_id=5)
        self.assertTrue(any("INSERT INTO sip_department_config" in s for s in self._sql()))
        self.assertFalse(any("DELETE FROM sip_department_config" in s for s in self._sql()))

    def test_history_row_is_scoped_to_the_department_and_masks_the_password(self):
        self.ns["update_sip_department_config"](self.db, 12, {"base_password": "supersecret"}, user_id=5)
        sql, params = next((s, p) for s, p in self.db.cursor.calls if "INSERT INTO sip_config_history" in s)
        self.assertIn("department_id", sql)
        self.assertEqual((5, 12), (params[0], params[1]))
        self.assertNotIn("supersecret", params[2])

    def test_unknown_department_is_refused(self):
        self.db.get_sip_department_configs = lambda ids=None: []
        with self.assertRaises(ValueError):
            self.ns["update_sip_department_config"](self.db, 999, {"sip_server": "x"}, user_id=5)


class UserSipAccountTests(unittest.TestCase):
    """Что уезжает в iCORE Phone у локальной АТС: цепочка «персональное → отдела».

    Общего яруса в резолвере больше нет. Он существовал как «настройки для всех
    отделов сразу», и ровно он подставлял отделу без своих настроек чужую АТС —
    сотрудник получал правдоподобные данные регистрации и не регистрировался.
    Теперь источник по умолчанию ровно один: настройки его отдела.
    """

    def setUp(self):
        self.ns = _database_namespace({"get_user_sip_account"})
        self.db = _StubDb(self.ns)
        self.row = dict(OPERATOR_STATE)
        # Отдел на локальной АТС: сервер, база пароля и код автодозвона — его.
        self.row.update({
            "department_sip_server": "sip.local",
            "department_base_password": "pwd",
            "department_autodial_code": "*55",
        })
        # Читать общий ярус резолверу больше нечем — и не нужно.
        self.db.get_sip_config = _no_common_tier
        self.db.get_sip_operator = lambda user_id: dict(self.row)

    def _account(self):
        return self.ns["get_user_sip_account"](self.db, 41)

    def test_defaults_come_from_the_department_settings(self):
        """Персональных полей нет — всё собирается из настроек отдела.

        Рядом с плоскими полями телефон получает явные domain/auth_id/number:
        у локальной АТС логин и есть номер, но один и тот же код телефона
        применяется к обоим провайдерам.
        """
        main = self._account()["main"]
        self.assertEqual({"username": "1024", "password": "pwd1024",
                          "server": "sip.local", "transport": "UDP",
                          "domain": "sip.local", "auth_id": "1024",
                          "number": "1024"}, main)

    def test_personal_password_and_domain_win(self):
        self.row.update({"sip_password": "own", "sip_domain": "pbx.other"})
        main = self._account()["main"]
        self.assertEqual("own", main["password"])
        self.assertEqual("pbx.other", main["server"])

    def test_autodial_is_absent_without_a_number(self):
        account = self._account()
        self.assertIsNone(account["autodial"])
        self.assertEqual("*55", account["autodial_code"])   # код отдела, не общий

    def test_autodial_account_is_built_like_the_main_one(self):
        self.row["autodial_number"] = "2024"
        autodial = self._account()["autodial"]
        self.assertEqual("2024", autodial["username"])
        self.assertEqual("pwd2024", autodial["password"])
        self.assertEqual("sip.local", autodial["server"])

    def test_missing_number_gives_no_account_at_all(self):
        self.row["sip_number"] = ""
        self.assertIsNone(self._account()["main"])

    def test_department_settings_feed_the_whole_account(self):
        """Все четыре значения по умолчанию у отдела свои — и они же итоговые."""
        self.row.update({
            "department_sip_server": "pbx.sales",
            "department_base_password": "sales",
            "department_autodial_code": "*77",
            "autodial_number": "2024",
        })
        account = self._account()
        self.assertEqual("pbx.sales", account["main"]["server"])
        self.assertEqual("sales1024", account["main"]["password"])
        self.assertEqual("pbx.sales", account["autodial"]["server"])
        self.assertEqual("*77", account["autodial_code"])

    def test_autodial_goes_to_its_own_pbx_when_set(self):
        self.row.update({
            "department_sip_server": "pbx.sales",
            "department_autodial_server": "dialer.sales",
            "autodial_number": "3001",
        })
        account = self._account()
        self.assertEqual("pbx.sales", account["main"]["server"])
        self.assertEqual("dialer.sales", account["autodial"]["server"])

    def test_a_department_without_settings_has_nothing_to_register_with(self):
        """Отдел ничего не задал — подставлять больше нечего, и это правильно.

        Раньше пустоту закрывал общий ярус, и телефон уходил регистрироваться
        на чужую АТС: вместо честной ошибки «отдел не настроен» оператор
        получал бесконечный «Регистрация…». Пусть лучше сервер и пароль будут
        пустыми — эндпоинт на них отвечает 409 с внятным текстом.
        """
        self.row.update({
            "department_sip_server": "", "department_base_password": "",
            "department_autodial_server": "", "autodial_number": "3001",
        })
        account = self._account()
        self.assertEqual("", account["main"]["server"])
        self.assertEqual("", account["main"]["password"])
        self.assertEqual("", account["autodial"]["server"])

    def test_autodial_has_its_own_password_base(self):
        """У аккаунта автодозвона свой пароль: «Secret{номер}!»."""
        self.row.update({
            "autodial_number": "3001",
            "department_autodial_base_password": "Secret{номер}!",
        })
        account = self._account()
        self.assertEqual("Secret3001!", account["autodial"]["password"])
        self.assertEqual("pwd1024", account["main"]["password"])   # основной по своей базе

    def test_autodial_password_falls_back_to_the_main_base(self):
        self.row["autodial_number"] = "3001"
        self.assertEqual("pwd3001", self._account()["autodial"]["password"])

    def test_personal_autodial_password_wins_over_the_base(self):
        self.row.update({
            "autodial_number": "3001", "autodial_password": "own",
            "department_autodial_base_password": "Secret{номер}!",
        })
        self.assertEqual("own", self._account()["autodial"]["password"])

    def test_personal_values_win_over_the_department_ones(self):
        self.row.update({
            "department_sip_server": "pbx.sales", "department_base_password": "sales",
            "sip_domain": "pbx.own", "sip_password": "own",
        })
        main = self._account()["main"]
        self.assertEqual("pbx.own", main["server"])
        self.assertEqual("own", main["password"])

    def test_the_answer_carries_no_common_config_block(self):
        """Ключ «config» убран из ответа вместе с общим ярусом.

        Телефон брал из него сервер, когда у отдела ничего не задано. Пока ключ
        существует, та ветка в телефоне может ожить — и снова увести отдел без
        настроек на чужую АТС.
        """
        account = self._account()
        self.assertNotIn("config", account)
        self.assertEqual("asterisk", account["provider"])
        self.assertIsNone(account["binotel"])


class UserSipAccountBinotelTests(unittest.TestCase):
    """Тез КЦ на Binotel: наследовать нечего, всё выдано провайдером персонально.

    Проверено живьём: регистрация идёт по чистому UDP/5060 с realm «Binotel»,
    логин («extHash») с внутренним номером не совпадает, а SIP-пароля в API
    кабинета нет вовсе — его вводят руками.
    """

    def setUp(self):
        self.ns = _database_namespace({"get_user_sip_account"})
        self.db = _StubDb(self.ns)
        self.row = dict(OPERATOR_STATE)
        self.row.update({
            "department_provider": "binotel",
            "department_name": "Тез КЦ",
            "sip_login": "68m77pnw",
            "sip_password": "V3ryS3cret",
            "sip_domain": "sip52.binotel.com",
            "sip_number": "6715",
        })
        self.cabinet = {
            "cabinet_url": "https://my.binotel.kz",
            "cabinet_login": "op@tez.kz", "cabinet_password": "cab",
            "employee_id": "480431",
            "status_url": "https://my.binotel.kz/f/pbx/#/settings/users/manage-users/480431",
        }
        self.db.get_sip_config = _no_common_tier
        self.db.get_sip_operator = lambda user_id: dict(self.row)
        self.db.get_binotel_account = lambda user_id: dict(self.cabinet)

    def _account(self):
        return self.ns["get_user_sip_account"](self.db, 41)

    def test_registration_goes_by_the_provider_login_not_the_number(self):
        """Логин Binotel («68m77pnw») с внутренним номером не совпадает.

        Подставь телефон в username номер — и регистрации не будет вовсе:
        такой учётки у провайдера просто нет.
        """
        main = self._account()["main"]
        self.assertEqual("68m77pnw", main["username"])
        self.assertEqual("68m77pnw", main["auth_id"])
        self.assertEqual("sip52.binotel.com", main["server"])
        self.assertEqual("sip52.binotel.com", main["domain"])
        # Пароль только персональный: «база + номер» здесь смысла не имеет.
        self.assertEqual("V3ryS3cret", main["password"])
        self.assertEqual("UDP", main["transport"])   # чистый UDP/5060, TLS не нужен

    def test_the_internal_number_is_kept_for_display_and_call_matching(self):
        """Номер из users остаётся в ответе, хоть в регистрации и не участвует:
        по нему телефон показывает «кто я» и матчит входящие звонки."""
        self.assertEqual("6715", self._account()["main"]["number"])

    def test_there_is_no_autodial_and_no_fop2_at_all(self):
        """Автодозвон и FOP2 — механика локальной АТС, в Binotel их нет.

        Оставь их включёнными — и телефон поднимет второй аккаунт в никуда, а
        потом уйдёт вечно крутить спиннер входа в FOP2, которого не существует.
        """
        account = self._account()
        self.assertIsNone(account["autodial"])
        self.assertEqual("", account["autodial_code"])
        self.assertIs(False, account["fop2_enabled"])

    def test_the_cabinet_block_travels_with_the_account(self):
        """Статус в Binotel телефон меняет через веб-кабинет, а не через SIP,
        поэтому учётку кабинета он получает вместе с данными регистрации."""
        account = self._account()
        self.assertEqual("binotel", account["provider"])
        self.assertEqual(self.cabinet, account["binotel"])

    def test_incomplete_credentials_give_no_account(self):
        """Пока не заполнены все три поля, «аккаунт» собирать нечего.

        SIP-пароля в API кабинета нет — его вводит руками глава отдела, и до
        этого момента честнее отдать None, чем полуготовую регистрацию.
        """
        for field in ("sip_password", "sip_login", "sip_domain"):
            row = dict(self.row)
            row[field] = ""
            self.db.get_sip_operator = lambda user_id, r=row: dict(r)
            self.assertIsNone(self._account()["main"], field)

    def test_the_answer_carries_no_common_config_block(self):
        self.assertNotIn("config", self._account())


class BinotelAccountTests(unittest.TestCase):
    """Учётка веб-кабинета: пароль отдаётся только самому оператору.

    Панель руководителя видит лишь признак «задан» (см. _SIP_OPERATOR_SELECT),
    а телефон получает пароль целиком — он сам поднимает сессию кабинета и
    переключает presenceState.
    """

    def setUp(self):
        self.ns = _database_namespace({"get_binotel_account"})

    def _call(self, rows):
        db = _StubDb(self.ns, _FakeCursor(rows))
        return self.ns["get_binotel_account"](db, 41), db.cursor.calls

    def test_password_and_status_link_are_built_for_the_phone(self):
        account, calls = self._call([("op@tez.kz", "cab", "480431", "")])
        self.assertEqual("cab", account["cabinet_password"])
        self.assertEqual("https://my.binotel.kz", account["cabinet_url"])   # дефолт кабинета
        self.assertTrue(account["status_url"].endswith("/manage-users/480431"),
                        account["status_url"])
        self.assertEqual((41,), calls[0][1])

    def test_an_explicit_cabinet_url_wins_over_the_default(self):
        account, _ = self._call([("op@tez.kz", "cab", "480431", "https://my.binotel.ua/")])
        self.assertEqual("https://my.binotel.ua/", account["cabinet_url"])
        # Хвост «/» из формы не должен превращаться в «//f/pbx».
        self.assertIn("https://my.binotel.ua/f/pbx", account["status_url"])

    def test_the_link_works_before_the_employee_id_is_known(self):
        """employeeID узнаётся только из самого кабинета, а ссылка нужна раньше:
        «me» кабинет разбирает сам, поэтому руководитель не заперт."""
        account, _ = self._call([("op@tez.kz", "cab", "", "")])
        self.assertTrue(account["status_url"].endswith("/manage-users/me"),
                        account["status_url"])

    def test_no_login_means_no_account(self):
        """Строка-заготовка (её мог создать сам апсерт) — это ещё не учётка."""
        self.assertIsNone(self._call([("", "", "", "")])[0])
        self.assertIsNone(self._call([])[0])


class SipOperatorsScopeTests(unittest.TestCase):
    def setUp(self):
        self.ns = _database_namespace({"get_sip_operators", "_sip_operator_row"})
        self.db = _StubDb(self.ns)

    def _query(self, **kwargs):
        self.ns["get_sip_operators"](self.db, **kwargs)
        return self.db.cursor.calls[0]

    def test_only_operators_and_active_by_default(self):
        sql, params = self._query()
        self.assertIn("u.role = ANY(%s)", sql)
        self.assertEqual(["operator", "trainee"], params[0])
        self.assertIn("LOWER(COALESCE(u.status, '')) <> ALL(%s)", sql)
        self.assertEqual(["fired", "dismissal"], params[1])

    def test_admin_scope_has_no_department_filter(self):
        sql, params = self._query(department_ids=None)
        self.assertNotIn("u.department_id = ANY(%s)", sql)
        self.assertEqual(2, len(params))

    def test_supervisor_also_sees_own_operators_outside_the_department(self):
        sql, params = self._query(department_ids=[367], supervisor_id=9)
        self.assertIn("(u.department_id = ANY(%s) OR u.supervisor_id = %s)", sql)
        self.assertEqual([367], params[2])
        self.assertEqual(9, params[3])

    def test_include_inactive_drops_the_status_filter(self):
        sql, params = self._query(include_inactive=True)
        self.assertNotIn("<> ALL(%s)", sql)
        self.assertEqual(1, len(params))

    def test_list_is_sorted_by_name(self):
        sql, _ = self._query()
        self.assertTrue(sql.rstrip().endswith("ORDER BY u.name"))


class SipEndpointTests(unittest.TestCase):
    """Гейт доступа и скоуп новых эндпоинтов раздела."""

    def setUp(self):
        self.source = _read(BOT_PATH)
        self.module = source_cache.parse(self.source)

    def _function(self, name):
        node = next(
            n for n in self.module.body
            if isinstance(n, ast.FunctionDef) and n.name == name
        )
        return ast.get_source_segment(self.source, node)

    def test_routes_exist(self):
        self.assertIn("@app.route('/api/sip_config/operators', methods=['GET', 'OPTIONS'])", self.source)
        self.assertIn(
            "@app.route('/api/sip_config/operators/<int:target_user_id>', methods=['PUT', 'OPTIONS'])",
            self.source,
        )

    def test_phone_download_is_limited_to_sales_and_admins(self):
        """Ограничение обязано жить на сервере, а не только в спрятанной кнопке.

        За подписанной ссылкой приходят и кнопка в iCORE, и автообновление самого
        телефона, поэтому проверка стоит именно здесь.
        """
        body = self._function('icore_phone_download_endpoint')
        self.assertIn('_can_download_icore_phone', body)
        self.assertIn('403', body)

        gate = self._function('_can_download_icore_phone')
        self.assertIn('_is_admin_role', gate)
        self.assertIn('ICORE_PHONE_DEPARTMENT_IDS', gate)
        # Отдел продаж; расширяется добавлением id, а не правкой условий.
        self.assertIn('ICORE_PHONE_DEPARTMENT_IDS = (367,)', self.source)

    def test_phone_version_manifest_stays_public(self):
        """Манифест версии закрывать нельзя: истёкшая сессия иначе запирает парк
        машин на старой версии — узнать о новой они не смогут."""
        idx = self.source.index("@app.route('/api/phone/version'")
        head = self.source[idx:self.source.index('def icore_phone_version_endpoint')]
        self.assertNotIn('@require_api_key', head)
        body = self._function('icore_phone_version_endpoint')
        self.assertIn("'no-store'", body)

    def test_both_endpoints_reuse_the_panel_gate(self):
        for name in ("sip_config_operators_endpoint", "sip_config_operator_update_endpoint"):
            body = self._function(name)
            self.assertIn("_can_manage_sip_config(requester_id, role)", body)
            self.assertIn("@require_api_key", self.source.split(f"def {name}")[0].rsplit("@app.route", 1)[1])

    def test_department_settings_route_is_scoped_to_own_departments(self):
        self.assertIn(
            "@app.route('/api/sip_config/departments/<int:department_id>', methods=['PUT', 'OPTIONS'])",
            self.source,
        )
        body = self._function("sip_config_department_endpoint")
        self.assertIn("_can_manage_sip_config(requester_id, role)", body)
        self.assertIn("_sip_department_scope(requester_id, role)", body)
        self.assertIn("Это не ваш отдел", body)

    def test_operators_payload_carries_department_settings(self):
        body = self._function("sip_config_operators_endpoint")
        self.assertIn("db.get_sip_department_configs(department_ids=department_ids)", body)

    def test_bulk_route_does_not_collide_with_the_single_one(self):
        self.assertIn("@app.route('/api/sip_config/operators/bulk', methods=['PUT', 'OPTIONS'])", self.source)
        # int-конвертер не матчит "bulk", поэтому маршруты не конфликтуют.
        self.assertIn("<int:target_user_id>", self.source)

    def test_bulk_endpoint_checks_gate_scope_role_and_existence(self):
        body = self._function("sip_config_operators_bulk_endpoint")
        self.assertIn("_can_manage_sip_config(requester_id, role)", body)
        self.assertIn("_sip_department_scope(requester_id, role)", body)
        self.assertIn("_sip_target_in_scope(t, department_ids, supervisor_id)", body)
        self.assertIn("Часть сотрудников не найдена", body)
        self.assertIn("В выборке есть сотрудники не из вашего отдела", body)
        self.assertIn("операторам и стажёрам", body)

    def test_scope_check_helper_covers_department_and_own_operators(self):
        ns = {}
        exec(self._function("_sip_target_in_scope"), ns)
        check = ns["_sip_target_in_scope"]
        target = {"department_id": 12, "supervisor_id": 9}
        self.assertTrue(check(target, None, None))                    # админ — все
        self.assertTrue(check(target, [12], None))                    # свой отдел
        self.assertFalse(check(target, [367], None))                  # чужой отдел
        self.assertTrue(check(target, [367], 9))                      # свой оператор у СВ
        self.assertFalse(check(target, [367], 5))

    def test_update_endpoint_enforces_department_boundary(self):
        body = self._function("sip_config_operator_update_endpoint")
        self.assertIn("_sip_department_scope(requester_id, role)", body)
        self.assertIn("_sip_target_in_scope(target, department_ids, supervisor_id)", body)
        self.assertIn("Сотрудник не из вашего отдела", body)
        self.assertIn("403", body)

    def test_scope_helper_keeps_head_inside_the_department(self):
        ns = {}
        helper = self._function("_sip_department_scope")
        exec(helper, ns)
        ns["_is_admin_role"] = lambda role: role == "admin"
        ns["_headed_department_ids"] = lambda uid: frozenset({12}) if uid == 2 else frozenset()
        ns["_department_scope_id_for_requester"] = lambda uid: 367

        self.assertEqual((None, None), ns["_sip_department_scope"](1, "admin"))
        self.assertEqual(([12], None), ns["_sip_department_scope"](2, "admin"))
        self.assertEqual(([367], 3), ns["_sip_department_scope"](3, "sv"))

    def test_operator_endpoint_serves_autodial_and_the_shared_code(self):
        body = self._function("operator_sip_settings_endpoint")
        self.assertIn("db.get_user_sip_account(requester_id)", body)
        self.assertIn('"autodial": autodial', body)
        self.assertIn('"autodial_code"', body)
        # Плоские поля основного аккаунта остаются на месте — старый телефон не ломаем.
        self.assertIn("**main", body)

    def test_operator_endpoint_tells_the_phone_which_provider_it_is(self):
        """Провайдер решает всю механику телефона, поэтому едет в том же ответе.

        Без него телефон не знает, что у Binotel нет ни автодозвона, ни FOP2,
        а статус переключается запросом в веб-кабинет, а не по SIP.
        """
        body = self._function("operator_sip_settings_endpoint")
        self.assertIn('"provider": provider', body)
        # Учётка кабинета — только самому оператору: он сам поднимает сессию.
        self.assertIn('"binotel": account.get("binotel")', body)

    def test_the_409_names_the_field_the_operator_actually_has(self):
        """Текст «проверьте SIP-номер» тезовца отправляет чинить не то поле.

        У Binotel номер в регистрации не участвует вовсе — там пустыми бывают
        логин, пароль или сервер, и руководителю надо назвать именно их.
        """
        body = self._function("operator_sip_settings_endpoint")
        self.assertIn("SIP-логин, пароль или сервер Binotel", body)
        self.assertIn("SIP-номер не назначен", body)
        self.assertIn("provider == 'binotel'", body)


class SipSectionFrontendTests(unittest.TestCase):
    def setUp(self):
        self.app = _read(APP_PATH)

    def test_modal_is_replaced_by_a_real_section(self):
        self.assertFalse((ROOT / "src" / "components" / "modals" / "SipSettingsModal.jsx").exists())
        self.assertNotIn("SipSettingsModal", self.app)
        self.assertIn(
            "const SipSettingsView = lazyWithRetry(() => import('./components/sip/SipSettingsView'));",
            self.app,
        )

    def test_sidebar_navigates_to_the_view_in_both_branches(self):
        """Пункт меню продублирован в свёрнутом и развёрнутом сайдбаре.

        Считаем «не меньше двух», а не «ровно два»: App.jsx правят постоянно, и
        точное число ловило бы не пропажу ветки, а любую соседнюю правку.
        """
        self.assertGreaterEqual(
            self.app.count("handleSidebarViewNavigation(e, 'sip_settings')"), 2)
        self.assertGreaterEqual(
            self.app.count("view === 'sip_settings' ? 'bg-blue-700' : ''"), 2)

    def test_section_renders_behind_the_access_flag(self):
        self.assertIn('view === "sip_settings" && canAccessSipSettings', self.app)

    def test_department_allowlist_guard_lets_the_section_through(self):
        self.assertIn("if (view === 'sip_settings' && canAccessSipSettings) return;", self.app)

    def test_view_is_built_from_shared_ios_primitives(self):
        view = _read(VIEW_PATH)
        self.assertIn("from '../ui/ios'", view)
        for token in ("iosCard", "iosInput", "IosModal", "APPLE_FONT"):
            self.assertIn(token, view)

    def test_view_loads_operators_and_departments_in_one_request(self):
        """Список сотрудников и настройки отделов приходят одним ответом.

        Точное число `await fetch(` здесь больше не считаем: раздел делится на
        «Таксопарки» и «Tez», общий ярус из него убирают — счётчик ловил бы не
        лишний запрос, а любую перестановку вкладок. Проверяем то, ради чего он
        и стоял: отдельного GET /api/sip_config нет, история грузится лениво,
        а ссылка на программу берётся свежей (она живёт час).
        """
        view = _read(VIEW_PATH)
        self.assertIn("/api/sip_config/operators", view)
        self.assertIn("setDepartments(Array.isArray(data.departments)", view)
        self.assertIn("/api/phone/version", view)
        self.assertIn("/api/phone/download", view)
        self.assertIn("if (tab === 'history' && !historyLoadedRef.current) fetchHistory();", view)

    def test_duplicates_and_conflicts_are_scoped_to_the_domain(self):
        view = _read(VIEW_PATH)
        self.assertIn("const numberKey = (number, domain) =>", view)
        self.assertIn("const effectiveDomain = (personal, common) =>", view)
        # Подсветка дублей и проверка конфликта считают пару, а не голый номер.
        duplicates = view.split("const duplicateKeys = useMemo(", 1)[1].split("}, [", 1)[0]
        self.assertIn("numberKey(number, domain)", duplicates)
        # Домен по умолчанию — отдела сотрудника, а не глобальный.
        self.assertIn("const common = commonFor(op);", duplicates)
        self.assertIn("effectiveDomain(op.sip_domain, common.server)", duplicates)
        conflicts = view.split("const conflicts = useMemo(", 1)[1].split("}, [", 1)[0]
        self.assertIn("numberKey(form.sip_number, effective.domain)", conflicts)
        self.assertIn("numberKey(form.autodial_number, effective.autodialDomain)", conflicts)
        self.assertNotIn("duplicateNumbers", view)

    def test_defaults_are_taken_from_the_department_first(self):
        """Значения по умолчанию у каждого сотрудника — его отдела.

        Проверяем ПЕРВОЕ звено цепочки, а не всю её целиком: общий ярус из
        commonFor убирают прямо сейчас, и ассёрт на хвост «|| settings.X»
        сломался бы от чужой правки, ничего полезного не поймав. А вот
        отдел обязан стоять первым в любом случае — иначе таксопарк со своей
        АТС снова получит чужой сервер.
        """
        view = _read(VIEW_PATH)
        common = view.split("const commonFor = useCallback(", 1)[1].split("}, [", 1)[0]
        self.assertRegex(common, r"const server = op\?\.department_sip_server\s*\|\|")
        self.assertRegex(common, r"const base = op\?\.department_base_password\s*\|\|")
        self.assertRegex(common, r"autodialServer: op\?\.department_autodial_server\s*\|\|")
        self.assertRegex(
            common, r"autodialBase: op\?\.department_autodial_base_password\s*\|\|")
        self.assertRegex(common, r"code: op\?\.department_autodial_code\s*\|\|")
        # У автодозвона своя АТС: домен считается по ней, а не по основной.
        self.assertIn("effectiveDomain(op.autodial_domain, common.autodialServer)", view)

    def test_departments_are_edited_one_by_one_and_show_who_is_configured(self):
        """Отделы редактируются по одному, и видно, у кого настройки заданы.

        Русские подписи («Настроен», «По умолчанию») здесь намеренно не
        проверяются: раздел как раз переименовывают под «Таксопарки»/«Tez»,
        и ассёрт на текст кнопки ловил бы редизайн, а не поломку.
        """
        view = _read(VIEW_PATH)
        self.assertIn("dept.configured", view)
        self.assertIn("departments.filter((d) => d.configured).length", view)
        self.assertIn("/api/sip_config/departments/${deptEditing.department_id}", view)

    def test_ctrl_and_shift_selection_drives_the_bulk_editor(self):
        view = _read(VIEW_PATH)
        self.assertIn("if (event.ctrlKey || event.metaKey)", view)   # ⌘ на маке
        self.assertIn("if (event.shiftKey)", view)                   # диапазон
        self.assertIn("/api/sip_config/operators/bulk", view)
        self.assertIn("Выбрано: {selected.size}", view)
        # Массово меняются пароль, домен и FOP2 — номера у каждого свои.
        bulk_fields = view.split("const BULK_FIELDS = [", 1)[1].split("];", 1)[0]
        self.assertIn("sip_domain", bulk_fields)
        self.assertIn("autodial_password", bulk_fields)
        self.assertNotIn("sip_number", bulk_fields)

    def test_fop2_is_switched_for_the_whole_selection(self):
        """Ради этого правка и делалась: раньше выключатель жил только в карточке,
        и снимать людей с очередей приходилось по одному."""
        view = _read(VIEW_PATH)
        bulk_fields = view.split("const BULK_FIELDS = [", 1)[1].split("];", 1)[0]
        self.assertIn("key: 'fop2_enabled'", bulk_fields)
        self.assertIn("flag: true", bulk_fields)
        # У флага своё «пусто»: пустая строка ушла бы на бэкенд как «включён».
        self.assertIn("value: f.flag ? false : ''", view)
        self.assertIn("body[f.key] = f.flag ? Boolean(value) : value.trim();", view)
        # Три положения одним переключателем, а не тумблер «менять это поле»:
        # зелёный тумблер рядом с «Вход в FOP2» читался бы как сам вход, и
        # выбранное «Не входит» ниже противоречило бы ему.
        choices = view.split("const BULK_FLAG_CHOICES = [", 1)[1].split("];", 1)[0]
        self.assertIn("on: false, value: false, label: 'Не менять'", choices)
        self.assertIn("on: true, value: true, label: 'Входит'", choices)
        self.assertIn("on: true, value: false, label: 'Не входит'", choices)
        self.assertIn("const bulkFlagPicked = (state, choice) =>", view)
        # «Не менять» — состояние по умолчанию: применить, не выбрав, нельзя.
        self.assertIn("(acc, f) => ({ ...acc, [f.key]: { on: false, value: f.flag ? false : '' } })", view)
        # Последствия те же, что в карточке, — предупреждение обязано быть и здесь.
        self.assertIn("перестанут вставать в очереди Asterisk", view)
        # Массовая правка номеров не касается: в истории не должно быть «номер снят».
        self.assertIn("(s.bulk ? null : 'номер снят')", view)


if __name__ == "__main__":
    unittest.main()

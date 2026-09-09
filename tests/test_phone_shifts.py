"""Телефонные смены в графике чата (постановка #304, Сабыр Азана).

Оператор чата отдельно закрывает телефон. Такие смены ставятся руками поверх
сгенерированного графика кнопкой «+ Линия», НЕ входят в расчёт ресурсов и на
всех экранах — в планировщике и в аукционе — выделяются зелёным.

Правило живёт в трёх файлах сразу, и каждая его половина по отдельности
бесполезна: признак ``shiftKind`` рождается в планировщике, переживает
сохранение плана в ``meta`` строки смены и приезжает в аукцион отдельной
колонкой лота. Здесь сторожится вся цепочка.
"""
import ast
import re
import unittest
from pathlib import Path

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "database.py"
PLANNER_PATH = ROOT / "src" / "components" / "resources" / "ResourceSchedulePlanner.jsx"
AUCTION_PATH = ROOT / "src" / "components" / "resources" / "ShiftAuctionView.jsx"

DATABASE_SOURCE = DATABASE_PATH.read_text(encoding="utf-8-sig")
PLANNER_SOURCE = PLANNER_PATH.read_text(encoding="utf-8")
AUCTION_SOURCE = AUCTION_PATH.read_text(encoding="utf-8")
DATABASE_MODULE = source_cache.parse(DATABASE_SOURCE)


def _database_class():
    return next(
        node for node in DATABASE_MODULE.body
        if isinstance(node, ast.ClassDef) and node.name == "Database"
    )


def _method_source(name):
    method = next(
        node for node in _database_class().body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(DATABASE_SOURCE, method)


def _jsx_block(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


class PlannerPhoneShiftTests(unittest.TestCase):
    """Планировщик: откуда берётся телефонная смена и почему её не видит расчёт."""

    def test_phone_templates_are_the_two_windows_from_the_task(self):
        """Окна заданы постановкой, а не редактором шаблонов.

        В редакторе шаблонов чата их нет и быть не должно: смены для телефона
        ставит руками супервайзер, а генератор про них не знает.
        """
        block = _jsx_block(PLANNER_SOURCE, "const PHONE_SHIFT_TEMPLATES", "const isPhoneShift")
        self.assertIn("'8*17'", block)
        self.assertIn("'17*02'", block)
        # Обе смены по 9 часов, ставка полная — половинных в чате нет вовсе.
        self.assertEqual(2, block.count("rate: 1"))

    def test_coverage_ignores_phone_shifts(self):
        """Главное требование постановки: «не включая в расчёт ресурсов».

        Покрытие часов и все итоги дня и периода считаются одной функцией, так
        что достаточно, чтобы телефонная смена не доходила до накопления часов.
        """
        block = _jsx_block(PLANNER_SOURCE, "const buildCoverageFromDays", "const nextDays = days.map")
        self.assertIn("if (isPhoneShift(shift)) return;", block)
        # Проверка стоит ДО накопления часов, иначе она ничего не меняет.
        self.assertLess(
            block.index("if (isPhoneShift(shift)) return;"),
            block.index("covered[hourIndex] +="),
            "телефонная смена отсеивается уже после того, как попала в покрытие",
        )

    def test_phone_kind_survives_save_and_marks_the_plan_dirty(self):
        """Подпись плана обязана знать про вид смены.

        Без этого смена, превращённая в телефонную, не считается изменением:
        кнопка сохранения остаётся серой, и правка теряется при перезагрузке.
        """
        block = _jsx_block(PLANNER_SOURCE, "const plannerDaysSignature", "const coverageTone")
        self.assertIn("shiftKind: shift.shiftKind || ''", block)

    def test_choice_appears_only_in_the_chat_planner(self):
        """Выбор вида смены — чатовый: телефон закрывают его операторы.

        Направление выводится из apiPrefix тем же приёмом, что ключ хранилища
        шаблонов и направление аукциона, а не отдельным пропом, который новая
        витрина может забыть проставить.
        """
        self.assertIn(
            "const phoneShiftsEnabledFor = (apiPrefix) => auctionDirectionFor(apiPrefix) === 'chat';",
            PLANNER_SOURCE,
        )
        self.assertIn("phoneShiftsEnabledFor(apiPrefix)", PLANNER_SOURCE)
        self.assertIn("phoneShiftsEnabled={phoneShiftsEnabled}", PLANNER_SOURCE)
        # У линии остаётся прежняя кнопка без меню.
        self.assertIn("{phoneShiftsEnabled ? (", PLANNER_SOURCE)

    def test_added_phone_shift_carries_the_kind(self):
        block = _jsx_block(PLANNER_SOURCE, "const addShift = useCallback", "const activeDayIndex")
        self.assertIn("options?.phoneTemplate", block)
        self.assertIn("{ shiftKind: PHONE_SHIFT_KIND }", block)

    def test_planner_paints_phone_shifts_green(self):
        """Зелёный — второе требование постановки, «для визуального выделения»."""
        block = _jsx_block(PLANNER_SOURCE, "const inactiveShiftClass = isAuctionShift", "return (")
        self.assertIn("isPhone", block)
        self.assertIn("bg-emerald-500", block)

    def test_auction_lots_bring_the_kind_back_to_the_planner(self):
        """Режим «покрытие по аукциону» тоже не должен считать телефонные смены."""
        block = _jsx_block(PLANNER_SOURCE, "const auctionShiftsByDate = useMemo", "const auctionClaimedCount")
        self.assertIn("shiftKind: String(lot.shift_kind || '')", block)


class SavedSchedulePhoneShiftTests(unittest.TestCase):
    """Сохранение плана: признак обязан пережить круг «сохранил — перечитал»."""

    def test_shift_kind_is_normalized_into_meta(self):
        source = _method_source("_normalize_resource_saved_schedule_shifts")
        self.assertIn("RESOURCE_SHIFT_KIND_PHONE", source)
        self.assertIn('meta["shiftKind"]', source)
        # Чужое значение в meta не протаскивается: вид смены — закрытый список.
        self.assertIn('meta.pop("shiftKind", None)', source)

    def test_kind_is_not_swallowed_by_the_known_keys_filter(self):
        """Ловушка: meta собирается вычитанием известных ключей.

        Попади ``shiftKind`` в этот набор — признак молча пропал бы при
        сохранении, и телефонная смена вернулась бы из базы обычной.
        """
        source = _method_source("_normalize_resource_saved_schedule_shifts")
        known = source[source.index("if key not in {"):source.index("}", source.index("if key not in {"))]
        self.assertNotIn("shiftKind", known)

    def test_serialized_shift_returns_meta_first(self):
        """Признак приезжает обратно только потому, что meta разворачивается первой."""
        source = _method_source("_serialize_resource_saved_schedule_shift_row")
        self.assertIn("**shift_meta", source)


class AuctionPhoneShiftTests(unittest.TestCase):
    """Аукцион: вид смены доезжает до сетки и красит лот зелёным."""

    def test_lot_serializer_exposes_the_kind(self):
        source = _method_source("_serialize_shift_auction_lot_row")
        self.assertIn('"shift_kind": (row[23] or "") if len(row) > 23 else ""', source)

    def test_every_lot_query_selects_the_kind(self):
        """Сериализатор читает лот ПО НОМЕРУ колонки.

        Оба запроса, которые через него проходят (снимок аукциона и выгрузка
        отчёта), обязаны отдать колонку последней — иначе вид смены либо пропадёт,
        либо в него попадёт чужое поле.
        """
        for name in ("_build_shift_auction_snapshot_common_tx", "get_shift_auction_test_export_data"):
            with self.subTest(method=name):
                source = _method_source(name)
                self.assertIn("source_shift.meta->>'shiftKind' AS shift_kind", source)
                self.assertLess(
                    source.index("l.self_scheduled_by"),
                    source.index("source_shift.meta->>'shiftKind'"),
                    "колонка вида смены встала не последней — номера колонок разъехались",
                )

    def test_planner_and_preview_lots_carry_the_kind(self):
        """Три остальных источника лотов — тоже с видом смены.

        Планировщик берёт лоты активного прогона и опубликованной истории, а
        супервайзер смотрит предпросмотр периода. Пропусти любой — телефонные
        смены на этом экране станут обычными.
        """
        for name in ("get_shift_auction_lots_for_planner_date", "get_shift_auction_period_preview"):
            with self.subTest(method=name):
                source = _method_source(name)
                self.assertIn("shiftKind", source)
                self.assertIn('"shift_kind"', source)
        planner_lots = _method_source("get_shift_auction_lots_for_planner_date")
        self.assertEqual(
            2, len(re.findall(r"meta->>'shiftKind'", planner_lots)),
            "активный прогон и опубликованная история — два разных запроса",
        )

    def test_grid_paints_phone_lots_green(self):
        self.assertIn(
            "const isPhoneAuctionLot = (lot) => String(lot?.shift_kind || '') === 'phone';",
            AUCTION_SOURCE,
        )
        self.assertIn("const getAuctionLotPhoneTone", AUCTION_SOURCE)
        block = _jsx_block(AUCTION_SOURCE, "const isPhoneLot = isPhoneAuctionLot(lot);", "const title =")
        # Зелёный вытесняет и синюю шкалу старта, и оранжевый добора: фаза
        # аукциона — про время, а зелёный — про вид смены.
        self.assertIn("isPhoneLot ? getAuctionLotPhoneTone(lot) : getAuctionLotStartTone(lot)", block)
        self.assertIn("isPhoneLot ? getAuctionLotPhoneTone(lot) : getAuctionLotPostAuctionTone(lot)", block)

    def test_claimed_by_someone_else_keeps_the_kind_visible(self):
        block = _jsx_block(AUCTION_SOURCE, "tone = lotClaimedByCurrentUser", "const isOpenPostStyle")
        self.assertIn("isPhoneLot", block)
        self.assertIn("bg-emerald-50", block)

    def test_realtime_event_does_not_wipe_the_kind(self):
        """Событие SSE несёт урезанный лот — вид смены в нём не приходит.

        Слияние идёт `{...currentLot, ...incomingLot}`, поэтому ключа в событии
        быть НЕ должно: иначе он пришёл бы пустым и перекрасил смену обратно.
        """
        source = _method_source("claim_shift_auction_test_lot")
        payload = source[source.index("lot_payload = {"):source.index("event = self._insert_shift_auction_test_event")]
        self.assertNotIn("shift_kind", payload)
        merge = (ROOT / "src" / "components" / "resources" / "shiftAuctionRealtimeLots.js").read_text(encoding="utf-8")
        self.assertIn("{ ...currentLot, ...incomingLot", merge)


if __name__ == "__main__":
    unittest.main()

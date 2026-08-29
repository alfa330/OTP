# -*- coding: utf-8 -*-
"""Взятую в чате ЧАСТЬ смены оператор должен уметь вернуть — как целую на линии.

Бэкенд это умел с самого начала: `release_shift_auction_test_lot` удаляет
строку своей части (`claim_stage='auction'`) и не трогает куски коллег. Модуль
отбора своих смен тоже — `collectMyAuctionDayClaims` отдаёт для такой строки
настоящий лот, то есть «вернуть можно». А кнопки не было: панель дня требовала
`row.lot.status === 'claimed'`, тогда как лот с взятой частью НАМЕРЕННО остаётся
`available` — иначе оставшийся кусок пропал бы у остальных. Условие снимало
кнопку у каждой части, кроме той, что закрыла смену целиком.

Проверяется текстом файла: разметка панели дня в React-компоненте, поднимать её
целиком ради одного условия дороже, чем сторожить сам код. Правило же
возвращаемости живёт в модулях (`shiftAuctionDayClaims`,
`shiftAuctionRealtimeLots`) и покрыто настоящими тестами на node:test.
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEW = os.path.join(REPO_ROOT, "src", "components", "resources", "ShiftAuctionView.jsx")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _day_panel_release_region(source):
    """Кусок разметки «Ваши смены» — от строк дня до кнопки возврата."""
    start = source.index("myActiveDayClaimRows.map((row) => {")
    end = source.index("</ul>", start)
    return source[start:end]


class ShiftAuctionChatReleaseTests(unittest.TestCase):
    def setUp(self):
        self.source = _read(VIEW)
        self.region = _day_panel_release_region(self.source)

    def test_release_button_does_not_require_a_claimed_lot(self):
        """Часть смены оставляет лот `available` — по статусу её не найти."""
        self.assertIn("const releasable = Boolean(", self.region)
        self.assertNotIn(
            "row.lot.status === 'claimed'",
            self.region,
            "Условие status === 'claimed' убирает «Вернуть» у каждой взятой части: "
            "лот с частью намеренно остаётся available."
        )

    def test_release_button_trusts_the_tested_module(self):
        """Возвращаемость решает collectMyAuctionDayClaims, а не разметка."""
        self.assertIn("&& row.lot", self.region)
        self.assertIn("Number.isFinite(Number(row.lot.id))", self.region)

    def test_confirm_dialog_gets_the_operators_own_piece(self):
        """В окне подтверждения должна стоять МОЯ доля, а не вся смена."""
        self.assertIn("openReleaseConfirm([row.claimLot || row.lot])", self.region)
        self.assertRegex(
            self.source,
            r"claimLot,\s*\n\s*start: range",
            "claimLot обязан доехать до строки дня, иначе окно покажет чужие часы."
        )

    def test_confirm_dialog_names_a_part_a_part(self):
        """Возврат части не должен обещать, что освободится вся смена."""
        self.assertIn("Хотите ли вы вернуть свою часть смены?", self.source)
        self.assertIn("части, взятые коллегами в этой смене, останутся у них", self.source)
        self.assertIn("'Вернуть часть'", self.source)

    def test_release_drops_only_my_segment_locally(self):
        """Ответ ручки сегментов не несёт — свою долю фронт убирает сам."""
        self.assertIn("const dropMyReleasedSegment = (target) =>", self.source)
        drop = self.source[self.source.index("const dropMyReleasedSegment"):]
        drop = drop[:drop.index("setReleasingLotId(numericId);")]
        self.assertIn("Number(segment?.claimed_by) === Number(user?.id)", drop)
        self.assertIn("releasedSegment.start", drop)
        self.assertIn("releasedSegment.end", drop)
        # Иначе своя часть висела бы «взятой» до следующего полного снапшота.
        self.assertIn("dropMyReleasedSegment({ ...l, ...serverLot", self.source)

    def test_realtime_merge_lives_in_a_tested_module(self):
        """Патч лота вынесен из компонента — у него есть свои node:test."""
        self.assertIn(
            "import { mergeRealtimeAuctionLot } from './shiftAuctionRealtimeLots';",
            self.source
        )
        self.assertNotIn("const mergeRealtimeAuctionLot = (", self.source)
        module = _read(os.path.join(
            REPO_ROOT, "src", "components", "resources", "shiftAuctionRealtimeLots.js"
        ))
        self.assertIn("eventType === 'lot_released'", module)
        self.assertIn("'lot_claimed'", module)

    def test_release_effect_depends_on_the_user_it_reads(self):
        """dropMyReleasedSegment читает user?.id — он обязан быть в зависимостях."""
        deps_start = self.source.index("const handleReleaseLot = useCallback(")
        deps = self.source[deps_start:]
        deps = deps[:deps.index("const toggleDayOff")]
        self.assertRegex(deps, r"\}, \[[^\]]*user\?\.id[^\]]*\]\);")


if __name__ == "__main__":
    unittest.main()

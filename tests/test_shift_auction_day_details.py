"""Контракт карточки дня в «Аукционе смен».

Оператор должен видеть свои выбранные смены по клику на день в нижней панели —
и пока аукцион идёт, и после его закрытия. Раньше карточка дня открывалась
только супервайзеру/админу, а оператору клик либо открывал диалог возврата
(только на открытом аукционе), либо не делал ничего.
"""

import unittest
from pathlib import Path

_RESOURCES = Path(__file__).resolve().parents[1] / "src" / "components" / "resources"

FRONTEND_SOURCE = (_RESOURCES / "ShiftAuctionView.jsx").read_text(encoding="utf-8")
DAY_CLAIMS_SOURCE = (_RESOURCES / "shiftAuctionDayClaims.js").read_text(encoding="utf-8")


class ShiftAuctionDayDetailsTests(unittest.TestCase):
    def test_day_panel_is_shared_by_role(self):
        # Одна оболочка панели, тело ветвится по canMonitor.
        self.assertIn("{isDayDetailsOpen && activeDayDate ? (", FRONTEND_SOURCE)
        self.assertNotIn("isAdminDayDetailsOpen", FRONTEND_SOURCE)

    def test_panel_opens_for_operators_too(self):
        self.assertIn(
            "setActiveDayDate(date);\n    setIsDayDetailsOpen(true);",
            FRONTEND_SOURCE,
        )

    def test_day_click_opens_the_panel_instead_of_the_release_dialog(self):
        # Клик по дню — просмотр, а не сразу необратимый возврат.
        self.assertNotIn("canReleaseHere", FRONTEND_SOURCE)
        self.assertIn("нажмите, чтобы посмотреть свои смены", FRONTEND_SOURCE)
        # «Свой график» остаётся первой веткой обработчика.
        self.assertIn("openSelfSchedule(item.date)", FRONTEND_SOURCE)

    def test_own_partial_claims_are_read_from_segments(self):
        # Взятая в доборе ЧАСТЬ смены живёт только в lot.claim_segments:
        # сам лот остаётся 'available' с пустым claimed_by. Сам отбор проверяет
        # tests/shift_auction_day_claims.test.mjs — здесь только что он подключён.
        self.assertIn("const myActiveDayClaimRows = useMemo(() => {", FRONTEND_SOURCE)
        self.assertIn("collectMyAuctionDayClaims({", FRONTEND_SOURCE)
        self.assertIn(
            "Number(segment.claimed_by) === myId",
            DAY_CLAIMS_SOURCE,
        )

    def test_release_from_panel_is_gated_by_can_claim(self):
        self.assertIn(
            "const canReleaseFromDayPanel = !canMonitor && canClaim;",
            FRONTEND_SOURCE,
        )
        # В подтверждение уходит настоящий лот, а не синтетическая строка сегмента.
        self.assertIn("openReleaseConfirm([row.lot])", FRONTEND_SOURCE)

    def test_own_claimed_shift_stays_distinguishable_in_the_grid(self):
        # Регресс 5be4368d: ветку «моя смена» вырезали, и все взятые лоты стали
        # серыми — свою было не отличить от чужой.
        self.assertIn("tone = lotClaimedByCurrentUser", FRONTEND_SOURCE)
        self.assertIn("border-emerald-600 bg-emerald-600 text-white", FRONTEND_SOURCE)

    def test_post_auction_claim_is_not_offered_for_release(self):
        # Сервер отвечает POST_AUCTION_LOT_NOT_RELEASABLE — кнопки быть не должно.
        self.assertIn("!lot.post_auction_claimed", DAY_CLAIMS_SOURCE)

    def test_day_cell_time_matches_the_hours_next_to_it(self):
        # Часы считаются по фактически взятому окну, значит и подпись тоже.
        self.assertIn("const formatCompactAuctionClaimLabel = (lot) => {", FRONTEND_SOURCE)
        self.assertIn(
            "formatCompactAuctionClaimLabel(item.myClaimedLot)",
            FRONTEND_SOURCE,
        )

    def test_instructions_describe_the_day_card(self):
        self.assertIn("Шаг 4 · Посмотрите свои смены и верните лишнюю", FRONTEND_SOURCE)
        self.assertNotIn("Шаг 4 · Передумали? Верните смену", FRONTEND_SOURCE)


if __name__ == "__main__":
    unittest.main()

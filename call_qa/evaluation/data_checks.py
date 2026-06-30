"""Провайдер проверки данных для критериев source=system_api (действия в ПО/бэкенде,
которых нет в разговоре: внесение в ПО, факт регистрации, эскалация и т.п.).

Сейчас провайдера нет (NullDataChecker) → такие критерии получают вердикт Pending
(уходят на ревью или берут default_verdict). Когда появится ВАШ API проверки данных:
реализуйте DataCheckProvider.check() и верните его из get_data_checker() — критерии
начнут проверяться автоматически, без изменений в остальном коде."""
from __future__ import annotations


class DataCheckProvider:
    def supports(self, criterion: dict) -> bool:
        """Умеет ли провайдер проверить этот критерий."""
        return False

    def check(self, criterion: dict, call_context: dict) -> dict:
        """Возвращает {verdict, confidence, evidence, comment} по данным из ПО.
        call_context: {call_id, phone_number, operator_id, ...} — что нужно вашему API."""
        raise NotImplementedError


class NullDataChecker(DataCheckProvider):
    """Заглушка: API проверки данных пока нет."""


# Пример будущей реализации:
# class CrmDataChecker(DataCheckProvider):
#     SUPPORTED = {"внесение информаци", "сделка состоял"}
#     def supports(self, criterion):
#         n = criterion["name"].lower()
#         return any(s in n for s in self.SUPPORTED)
#     def check(self, criterion, call_context):
#         ok = your_api.check_registration(call_context["phone_number"])
#         return {"verdict": "Correct" if ok else "Incorrect", "confidence": 0.99,
#                 "evidence": "данные из CRM", "comment": "проверено по API"}


def get_data_checker() -> DataCheckProvider:
    # TODO: когда появится API — вернуть реальный провайдер (выбор через config).
    return NullDataChecker()

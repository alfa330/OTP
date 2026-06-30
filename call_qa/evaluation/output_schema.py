"""Схемы оценки.

Evaluation/CriterionVerdict — ВЫХОД Claude (только критерии source=transcript).
CallResult/CriterionResult — ИТОГОВЫЙ результат по всем критериям, с источником
(transcript | system_api | manual) и возможным вердиктом Pending."""
from typing import List, Literal, Optional
from pydantic import BaseModel


# --- Выход Claude (структурный) ---
class CriterionVerdict(BaseModel):
    idx: int
    verdict: Literal["Correct", "Incorrect", "N/A"]
    confidence: float
    evidence_quote: str
    comment: str


class Evaluation(BaseModel):
    per_criterion: List[CriterionVerdict]
    overall_comment: str


# --- Итог по звонку (Claude + проверки данных + ручные) ---
class CriterionResult(BaseModel):
    idx: int
    name: str
    source: str                              # transcript | system_api | manual
    verdict: str                             # Correct | Incorrect | N/A | Pending
    confidence: Optional[float] = None
    evidence_quote: str = ""
    comment: str = ""


class CallResult(BaseModel):
    per_criterion: List[CriterionResult]
    overall_comment: str

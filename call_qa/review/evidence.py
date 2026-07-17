"""Server-side validation of evidence selected during human review."""
from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata


VALID_VERDICTS = frozenset({"Correct", "Incorrect", "N/A", "Deficiency"})
VALID_EVIDENCE_STATUSES = frozenset({"verified", "no_evidence"})


class EvidenceValidationError(ValueError):
    pass


def _normalise_with_offsets(value: str) -> tuple[str, list[int]]:
    """NFKC/casefold text while retaining a map to source character offsets."""
    out: list[str] = []
    offsets: list[int] = []
    pending_space = False
    pending_offset = 0
    for source_idx, source_char in enumerate(value or ""):
        expanded = unicodedata.normalize("NFKC", source_char).casefold()
        for char in expanded:
            if char.isalnum():
                if pending_space and out:
                    out.append(" "); offsets.append(pending_offset)
                out.append(char); offsets.append(source_idx)
                pending_space = False
            else:
                if not pending_space:
                    pending_offset = source_idx
                pending_space = True
    return "".join(out), offsets


def locate_excerpt(transcript: str, excerpt: str, *, fuzzy_threshold: float = 0.94) -> tuple[int, int] | None:
    """Return source offsets for an exact/very-high-confidence fuzzy match.

    Normalisation intentionally ignores punctuation, letter case and repeated
    whitespace.  The narrowly scoped fuzzy path handles a small ASR typo, but is
    anchored by a distinctive word and never accepts short generic fragments.
    """
    haystack, mapping = _normalise_with_offsets(transcript)
    needle, _ = _normalise_with_offsets(excerpt)
    if not needle or not haystack or len(needle) < 4:
        return None

    start = haystack.find(needle)
    if start < 0 and len(needle) >= 20:
        words = [w for w in needle.split() if len(w) >= 4]
        anchor = max(words, key=len, default="")
        candidates: list[tuple[float, int, int]] = []
        if anchor:
            for match in re.finditer(rf"(?<!\w){re.escape(anchor)}(?!\w)", haystack):
                anchor_in_needle = needle.find(anchor)
                expected = max(0, match.start() - anchor_in_needle)
                for delta in (-max(3, len(needle) // 12), 0, max(3, len(needle) // 12)):
                    left = max(0, expected + delta)
                    right = min(len(haystack), left + len(needle))
                    candidate = haystack[left:right]
                    score = SequenceMatcher(None, needle, candidate, autojunk=False).ratio()
                    candidates.append((score, left, right))
        if candidates:
            score, candidate_start, candidate_end = max(candidates)
            if score >= fuzzy_threshold:
                start = candidate_start
                needle = haystack[candidate_start:candidate_end]

    if start < 0 or not mapping:
        return None
    end_norm = min(len(mapping) - 1, start + len(needle) - 1)
    return mapping[start], mapping[end_norm] + 1


def validate_evidence(transcript: str, *, excerpt: str | None,
                      evidence_status: str | None,
                      excerpt_verified: bool = False) -> tuple[str, int | None, int | None]:
    status = str(evidence_status or ("verified" if excerpt_verified else "")).strip().lower()
    if status not in VALID_EVIDENCE_STATUSES:
        raise EvidenceValidationError(
            "подтвердите цитату из транскрипта или явно отметьте отсутствие доказательства")
    if status == "no_evidence":
        if excerpt and excerpt.strip():
            raise EvidenceValidationError("для статуса «нет цитаты» поле excerpt должно быть пустым")
        return status, None, None
    if not excerpt_verified:
        raise EvidenceValidationError("цитата должна быть явно подтверждена проверяющим")
    clean = (excerpt or "").strip()
    if not clean:
        raise EvidenceValidationError("подтверждённая цитата не может быть пустой")
    offsets = locate_excerpt(transcript or "", clean)
    if offsets is None:
        raise EvidenceValidationError("цитата не найдена в сохранённом транскрипте звонка")
    return status, offsets[0], offsets[1]

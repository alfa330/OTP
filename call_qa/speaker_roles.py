"""Кто в звонке оператор, а кто клиент — для показа расшифровки в карточке.

Распознавание делит запись на голоса (метки Soniox «1», «2», …; оценщик видит их
как [S1]/[S2]), но кто из них сотрудник, не знает. Это решает модель-оценщик — по
смыслу («Определи сам, кто оператор» во вступлении промпта). Карточка до 05.10.2026
решала отдельно и грубо: оператор — «кто больше говорит». Две независимые догадки
расходились ровно там, где клиент говорит дольше, — в жалобах и длинных объяснениях
водителя: его реплики уезжали в синие пузыри «Оператор», а комментарии ИИ при этом
верно называли водителя водителем. Замер 05.10.2026 по 1 565 расшифровкам: стороны
стояли наоборот в 148 карточках (9,5 %). Сверка со слепой разметкой 259 звонков (все
спорные, все с тремя голосами и выборка остальных): по правилам ниже верно 258.
Расхождение — короткий звонок 6758, где стороны перепутала сама модель, и карточка
повторяет её понимание. Модель путала стороны ещё в двух коротких звонках, но там
её цитаты «оператора» короче порога («Да. Иә.», «Жарайды») и не голосуют — карточка
показывает верно, а комментарий ИИ ей противоречит.

Теперь сторона голоса берётся из ответа самой оценки. Обоснование критерия
(evidence_quote) — дословная цитата расшифровки, и цитирует модель того, чью работу
оценивает, — оператора. Голос, которому принадлежит большинство цитат, и есть
оператор в понимании модели. Промпт и схема ответа не меняются: иначе сменился бы
отпечаток оценки, и все сохранённые оценки разом стали бы «устаревшими», а старые
так и остались бы без ответа. Цитаты же лежат в каждом сохранённом прогоне —
исправление действует и на уже сделанные оценки.

Когда цитат нет или голоса делят их поровну (короткий звонок, всё N/A), сторона
решается по самопредставлению сотрудника — «такси сервисі, есімім …», «звоню из
таксопарка», «қандай көмек көрсете аламын». Фразы ответа на звонок («алло»,
«тыңдап тұрмын», «слушаю вас») сюда намеренно не входят: на исходящем их говорит
клиент, снимая трубку. Дальше — число критериев с цитатами голоса, и только если
нет и этого — прежнее «кто больше говорит». Доли на тех же 1 565: по цитатам
решается 1 513 звонков, по самопредставлению 23, по критериям 2, по доле речи 27.
"""
from __future__ import annotations

import re
from collections import Counter

OPERATOR = "operator"
CLIENT = "client"

# Метка голоса в тексте, который видит оценщик (soniox.assemble): «[S1] …». Модель
# переносит её в цитату в трёх видах (замер 05.10.2026 по 16 959 цитатам): «[S1]»,
# «S2: …» / «S2 (клиент): …» и изредка кириллицей — «С2: …». У вида с двоеточием цифра
# стоит вплотную к букве и за двоеточием не идёт цифра: «С 9:00» — это время, не метка.
_MARK_RE = re.compile(r"\[\s*[SС]\s*(\d+)\s*\]|(?<!\w)[SС](\d+)(?:\s*\([^)]*\))?\s*:(?!\d)")
# Пропуск внутри цитаты: модель склеивает несмежные куски через многоточие.
_GAP_RE = re.compile(r"\.\.\.|…")
_NON_WORD_RE = re.compile(r"[\W_]+")
# Короче — не улика: «алло», «да, да», «рахмет» есть у обеих сторон.
_MIN_PIECE_CHARS = 8


def _norm(text) -> str:
    return " ".join(_NON_WORD_RE.sub(" ", str(text or "").lower()).split())


def _line_text(line: dict) -> str:
    segs = line.get("seg") if isinstance(line.get("seg"), list) else []
    return "".join(str(seg.get("t") or "") for seg in segs if isinstance(seg, dict))


def _speaker_key(value) -> str | None:
    return None if value is None else str(value)


# ── исходные метки голосов у реплик ──────────────────────────────────────────

def speaker_runs(tokens) -> list:
    """Голоса реплик по порядку: реплика — это непрерывный отрезок токенов одного
    голоса (так режут и api._lines_from_tokens, и soniox.assemble)."""
    runs, previous = [], object()
    for token in tokens or []:
        speaker = token.get("speaker") if isinstance(token, dict) else None
        if speaker != previous:
            runs.append(speaker)
            previous = speaker
    return runs


def with_speaker_ids(lines, tokens) -> list:
    """Реплики с исходной меткой голоса (`spk`).

    У расшифровок, сохранённых до 05.10.2026, в репликах осталась только готовая
    подпись «operator»/«client» — метку восстанавливаем по токенам той же записи:
    реплик ровно столько, сколько отрезков голоса. Нарезка не сошлась или подписи
    противоречат меткам (один «оператор» на двух голосах) — реплики отдаются как
    были: лучше прежняя подпись, чем чужая метка не у той реплики."""
    lines = list(lines or [])
    if not lines or all(isinstance(line, dict) and "spk" in line for line in lines):
        return lines
    runs = speaker_runs(tokens)
    if len(runs) != len(lines) or not all(isinstance(line, dict) for line in lines):
        return lines
    operator_marks = {_speaker_key(speaker) for line, speaker in zip(lines, runs)
                      if line.get("speaker") == OPERATOR}
    if len(operator_marks) > 1:
        return lines
    return [{**line, "spk": _speaker_key(speaker)} for line, speaker in zip(lines, runs)]


# ── кого модель считает оператором ───────────────────────────────────────────

def _speaker_texts(lines) -> dict[str, str]:
    """Нормализованная речь каждого голоса подряд. Цитата, которую модель склеила
    из двух реплик одного голоса через реплику собеседника, здесь тоже находится:
    реплики голоса идут друг за другом."""
    texts: dict[str, list[str]] = {}
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        speaker = _speaker_key(line.get("spk"))
        if speaker is None:
            continue
        text = _norm(_line_text(line))
        if text:
            texts.setdefault(speaker, []).append(text)
    return {speaker: " " + " ".join(parts) + " " for speaker, parts in texts.items()}


def _owner(piece: str, texts: dict[str, str]) -> str | None:
    """Чей голос сказал этот кусок цитаты: ровно один голос, иначе не улика."""
    needle = _norm(piece)
    if len(needle) < _MIN_PIECE_CHARS:
        return None
    owners = [speaker for speaker, text in texts.items() if f" {needle} " in text]
    return owners[0] if len(owners) == 1 else None


def _quote_fragments(quote: str) -> list[tuple[str | None, str]]:
    """Цитата → [(метка голоса от модели или None, текст)] по меткам голоса и
    пропускам «…»: модель склеивает так несмежные куски, в том числе реплику
    водителя с ответом оператора, — каждый кусок голосует сам за себя."""
    tagged, mark, start = [], None, 0
    for match in _MARK_RE.finditer(quote):
        tagged.append((mark, quote[start:match.start()]))
        mark, start = match.group(1) or match.group(2), match.end()
    tagged.append((mark, quote[start:]))
    return [(mark, piece) for mark, text in tagged for piece in _GAP_RE.split(text)]


def _evidence(criteria, lines) -> tuple[Counter, Counter]:
    """Голос → (сколько кусков обоснований модели он сказал, в скольких критериях).

    Чей кусок — решает сама расшифровка: кусок ищется в речи каждого голоса. Не
    нашёлся (модель пересказала, а не процитировала) — верим метке, которую модель
    поставила перед ним: GLM начинает почти каждую цитату с «[S2] …», то есть сама
    подписывает голос. Кусок без метки, которого нет в расшифровке, — не улика.
    Реплики собеседника, процитированные как контекст («Вы сказали, Караганда,
    верно? [S1] Нет, Шымкент.»), тоже голосуют, но их меньше: критерии шкалы — о
    работе оператора, и цитирует модель прежде всего его."""
    texts = _speaker_texts(lines)
    pieces: Counter = Counter()
    per_criterion: Counter = Counter()
    if len(texts) < 2:
        return pieces, per_criterion
    for criterion in criteria or []:
        if not isinstance(criterion, dict):
            continue
        quote = criterion.get("evidence_quote")
        if quote is None:
            quote = criterion.get("evidence")
        owners = set()
        for mark, piece in _quote_fragments(str(quote or "")):
            owner = _owner(piece, texts)
            if owner is None and mark in texts and len(_norm(piece)) >= _MIN_PIECE_CHARS:
                owner = mark
            if owner is not None:
                pieces[owner] += 1
                owners.add(owner)
        per_criterion.update(owners)
    return pieces, per_criterion


def evidence_votes(criteria, lines) -> Counter:
    """Голос → сколько кусков обоснований модели он сказал (см. _evidence)."""
    return _evidence(criteria, lines)[0]


# ── самопредставление сотрудника ─────────────────────────────────────────────

# Только то, что говорит о себе сотрудник компании: звонит ОТ компании, называет
# службу или предлагает помощь. Не улика: ответ на звонок («алло», «тыңдап тұрмын»,
# «слушаю вас») — на исходящем его говорит клиент; голое «хабарласып тұрмын» /
# «звондап жатырмын» («я звоню») — его говорит и водитель, звоня сам. Глагол звонка
# считается только рядом с компанией: «звоню с таксопарка», «Яндекс Таксиден
# звондап», «тіркелу бөлімінен хабарласып». Сверка 05.10.2026 по 259 размеченным
# звонкам (самопредставление как единственный признак): 162 верно, 2 ошибки — оба
# раза диаризация отдала начало речи оператора голосу клиента.
_COMPANY = r"(?:такси|парк|платформ|орталығ|орталық|бөлім|сервис|яндекс|компани|қолдау|служб)"
# «Звоню из …» без компании — тоже сотрудник («звоню из отдела регистрации», «звоню
# вам из «Яндекс Такси»»), кроме города: «звоню из города Тараз» говорит водитель.
_INTRODUCTION_RE = re.compile(
    r"(?:звоню вам|звоню|звоним вам) из(?!\s+(?:город\w*|г\.))"
    r"|вас беспокоит|чем могу помочь|чем могу вам помочь"
    r"|звон(?:ю|им)\s+(?:вам\s+)?(?:с|со)\s+\w*" + _COMPANY +
    r"|\w*" + _COMPANY + r"\w*(?:нан|нен|дан|ден|тан|тен)\s+(?:звондап|хабарласып|қоңырау)"
    r"|қандай көмек|көмек көрсете аламын|көмектесе аламын"
    r"|такси сервис|тіркеу орталығ|тіркелу орталығ|центр\w*[\s-]+регистрац")
# Сколько первых реплик смотреть: сотрудник представляется в начале разговора.
_INTRODUCTION_LINES = 8


def introduction_votes(lines) -> Counter:
    votes: Counter = Counter()
    seen = 0
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        text = _line_text(line).strip()
        if not text:
            continue
        seen += 1
        if seen > _INTRODUCTION_LINES:
            break
        speaker = _speaker_key(line.get("spk"))
        if speaker is not None and _INTRODUCTION_RE.search(text.lower()):
            votes[speaker] += 1
    return votes


def talk_share(lines) -> Counter:
    """Голос → сколько он сказал (буквы без пробелов) — прежний способ, последний."""
    share: Counter = Counter()
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        speaker = _speaker_key(line.get("spk"))
        if speaker is not None:
            share[speaker] += len(_norm(_line_text(line)).replace(" ", ""))
    return share


def _leaders(votes: Counter) -> list[str]:
    """Голоса с наибольшим числом голосов (больше нуля); их больше одного — ничья."""
    top = max(votes.values(), default=0)
    return sorted(voice for voice, count in votes.items() if count == top) if top > 0 else []


def resolve(lines, criteria=None) -> dict | None:
    """Чей голос — оператор: {"operator": [метки], "method": …} или None, если у
    реплик нет исходных меток голоса (переписка, расшифровка без токенов).

    method по порядку: evidence — по кускам цитат оценки; introduction — по
    самопредставлению; evidence_criteria — по числу критериев, где модель цитирует
    голос; talk_share — кто больше говорит (последнее средство, как было до
    05.10.2026). Ничья не выбрасывает признак, а сужает выбор: дальше решают только
    среди равных — иначе доля речи могла бы назначить оператором голос, которого
    модель ни разу не процитировала.

    Счёт по критериям — именно после самопредставления. Куски считаются по
    отдельности, и цитата водителя, склеенная через «…», весит вдвое; на ничьей
    3:3 (звонок 6723 в замере) доля речи выбирала водителя с перевесом в одну букву,
    а по критериям оператор впереди 3:2. Первым его ставить нельзя: на звонке 7122
    он выбирает водителя, а самопредставление — верно."""
    lines = [line for line in (lines or []) if isinstance(line, dict)]
    if not lines or not all("spk" in line for line in lines):
        return None
    pieces, per_criterion = _evidence(criteria, lines)
    candidates = None
    for method, votes in (("evidence", pieces),
                          ("introduction", introduction_votes(lines)),
                          ("evidence_criteria", per_criterion),
                          ("talk_share", talk_share(lines))):
        if candidates is not None:
            votes = Counter({voice: n for voice, n in votes.items() if voice in candidates})
        leaders = _leaders(votes)
        if len(leaders) == 1:
            return {"operator": leaders, "method": method}
        if leaders:
            candidates = set(leaders)
    return {"operator": [], "method": "unknown"}


def assign(lines, roles: dict | None) -> list:
    """Подписи реплик по решению resolve(). Реплика без метки голоса (токен без
    голоса у Soniox) — клиентская, как было и прежде."""
    if not roles:
        return list(lines or [])
    operators = {str(speaker) for speaker in roles.get("operator") or []}
    out = []
    for line in lines or []:
        if isinstance(line, dict) and "spk" in line:
            speaker = _speaker_key(line.get("spk"))
            line = {**line, "speaker": OPERATOR if speaker in operators else CLIENT}
        out.append(line)
    return out

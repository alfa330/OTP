"""Поиск задач с опечатками — чистая часть, без базы и Flask.

Просьба владельца 27.11.2026: «сделать поиск задачи, чтобы можно было искать
задачу легко и просто, так же учитывать ошибки букв и тд, поиск будет искать
по выбранной доске и проекту». Охват (доска, проект, раздел) решает база —
сюда приходят уже отобранные задачи, модуль только сопоставляет и ранжирует.

Почему не pg_trgm, как у вики. Прод с 25.11.2026 живёт на новой базе, и есть
ли там расширение, отсюда не проверить; раздел, который без него молча теряет
опечатки, хуже раздела, которому расширения не нужны. Задач — сотни и
тысячи, а не миллионы: сопоставление словарём в Python укладывается в десятки
миллисекунд (замер — tests/test_task_search.py). И мера здесь точнее триграмм:
по триграммам «марк» похоже на «Марию» (0,6 при пороге 0,45), а перестановка
букв в середине длинного слова («сотрдуник») до порога не дотягивает.

Что прощается:
  * регистр, ё/е и казахские буквы (та же свёртка, что у вики: wiki.text);
  * одна ошибка в слове от 4 букв и две — от 9: пропущенная, лишняя, чужая
    или переставленная буква (расстояние Дамерау — Левенштейна). Пороги —
    как у Meilisearch и Algolia, с поправкой: у слова из четырёх букв первую
    букву не прощаем, иначе «план» находил бы «клан»;
  * окончания: «табель» находит «табеля», «отчеты» — «отчёт» (общая основа);
  * начало слова: по «отч» находится «Отчёт» — поиск идёт по мере ввода;
  * забытая раскладка: «jnxtn» — это «отчет», «еудупкфь» — «telegram»;
  * ключ задачи: «HR-125», «hr125», «рк-125» (та же раскладка) и просто «125».

Порядок выдачи — ступенями, а не суммой (урок поиска вики, wiki/search.py):
  0 — запрос и есть ключ задачи;
  1 — нашлись все слова запроса, и все точно (целиком, началом, основой);
  2 — нашлись все слова, но хоть одно — похожим написанием;
  3 — совпала только часть слов. Эта ступень идёт в ход, только когда ничего
      со всеми словами не нашлось: иначе «отчёт по зарплате» тащил бы за собой
      все задачи со словом «отчёт».
Внутри ступени — сколько слов нашлось, насколько точно и в каком поле (тема
важнее людей, люди — описания), и только потом свежесть.
"""

import math
import re
from bisect import bisect_left
from dataclasses import dataclass, field

from wiki.text import EN_TO_RU_LAYOUT, fold_kazakh

# Длиннее этого запрос смысла не несёт; обязательных слов — не больше восьми.
MAX_QUERY_CHARS = 120
MAX_TOKENS = 8

# Описание сравниваем по первым N знакам: суть задачи — в начале, а хвост
# длинной постановки раздувал бы словарь каждого запроса.
DESCRIPTION_SEARCH_CHARS = 2000

# Сколько задач охвата сверяется за один запрос. На проде их порядка тысячи
# (ноябрь 2026); потолок — страховка: поиск идёт по мере ввода, и запрос по
# огромному охвату тянул бы на каждую букву мегабайты описаний (разбор
# 27.11.2026). Упёрлись — ищем по самым свежим и пишем об этом в журнал.
MAX_CANDIDATES = 5000

# Отрывок описания в строке выдачи.
SNIPPET_BEFORE = 40
SNIPPET_LENGTH = 140

# ── Ступени ─────────────────────────────────────────────────────────────────
TIER_KEY = 0
TIER_EXACT = 1
TIER_SIMILAR = 2
TIER_PARTIAL = 3

# ── Как совпало слово ───────────────────────────────────────────────────────
# Оценка — до 100. «Точные» виды не опускают задачу ниже первой ступени,
# похожие (FUZZY, PREFIX_FUZZY) — опускают на вторую.
EXACT = 'exact'
PREFIX = 'prefix'
STEM = 'stem'
INFIX = 'infix'
KEY = 'key'
FUZZY = 'fuzzy'
PREFIX_FUZZY = 'prefix_fuzzy'
STRONG_KINDS = frozenset({EXACT, PREFIX, STEM, INFIX, KEY})
_KIND_RANK = {EXACT: 6, KEY: 6, PREFIX: 5, STEM: 4, INFIX: 3, FUZZY: 2, PREFIX_FUZZY: 1}

# Поле, где нашлось слово, — множитель к оценке. Тема — то, что человек
# помнит о задаче; люди — второе, что он помнит; описание — подсказка.
# Номер задачи чуть выше темы: «3» на открытой доске — это HR-3, а не «Отчёт
# за 3 квартал».
SUBJECT = 'subject'
PEOPLE = 'people'
DESCRIPTION = 'description'
_FIELD_WEIGHT = {SUBJECT: 1.0, KEY: 1.1, PEOPLE: 0.8, DESCRIPTION: 0.5}

# Вариант с исправленной раскладкой чуть ниже прямого: «ghbdtn» почти
# наверняка «привет», но буквальное совпадение всё равно честнее.
_LAYOUT_PENALTY = 0.92
# Бонус за запрос, целиком стоящий в теме подряд («табель за ноябрь»).
_PHRASE_BONUS = 25.0
# Совпал ключ задачи — выше любого совпадения текстом.
_KEY_BONUS = 200.0

# Предлоги и союзы не обязаны находиться: «отчёт по ЗП» должен найти и
# «Отчёт ЗП». Совпали — немного добавляют, не совпали — не мешают.
STOP_WORDS = frozenset({
    'и', 'в', 'во', 'на', 'по', 'за', 'с', 'со', 'к', 'ко', 'о', 'об', 'обо',
    'от', 'до', 'из', 'у', 'для', 'при', 'про', 'над', 'под', 'а', 'но', 'или',
})

_WORD_RE = re.compile(r'[^\W_]+')
_LATIN_RE = re.compile(r'[a-z]')
_CYRILLIC_RE = re.compile(r'[а-я]')
# Ключ задачи внутри запроса. С дефисом код может нести цифры (IC2-5), без
# дефиса — только буквы: «HR125» иначе не разобрать на код и номер.
_KEY_DASH_RE = re.compile(r'(?<![0-9a-z])([a-z][a-z0-9]{1,9})\s*-\s*(\d{1,9})(?!\d)')
_KEY_GLUED_RE = re.compile(r'(?<![0-9a-z])([a-z]{2,10}) ?(\d{1,9})(?!\d)')

# Обратная раскладка: набрано кириллицей вместо латиницы («еудупкфь»).
RU_TO_EN_LAYOUT = {ru: en for en, ru in EN_TO_RU_LAYOUT.items() if en.isalpha()}

# Свёртка ё и казахских букв — посимвольная, один знак на один.
_FOLD_TABLE = {ord(char): fold_kazakh(char.lower()) for char in 'ёәғқңөұүһіЁӘҒҚҢӨҰҮҺІ'}


def fold(text):
    """Строка в том виде, в каком её сравнивает поиск. ДЛИНА СОХРАНЯЕТСЯ.

    Посимвольно, один к одному, — на этом держится подсветка: позиции слов в
    свёрнутой теме совпадают с позициями в исходной. str.lower() у редких
    букв меняет длину («İ» → «i̇»), такие строки сворачиваются по букве.
    """
    text = str(text or '')
    lowered = text.lower()
    if len(lowered) != len(text):
        lowered = ''.join(low if len(low) == 1 else char
                          for char, low in ((char, char.lower()) for char in text))
    return lowered.translate(_FOLD_TABLE)


def split_words(text):
    """Слова свёрнутого текста — без позиций и без частей (для запроса и фразы)."""
    return _WORD_RE.findall(fold(text))


# Внутри слова — отдельно буквы и цифры: «2гис» → «2», «гис».
_RUN_RE = re.compile(r'\d+|[^\W\d_]+')


def words_with_positions(text):
    """[(начало, конец, слово)] задачи: позиции годятся и для исходного текста.

    Кроме самих слов — их второе написание, которым их тоже ищут (разбор
    27.11.2026): части слова из букв и цифр («2ГИС» находится по «гис» и по
    «2 гис») и склейка слов через один дефис («e-mail» — по «email»).
    """
    folded = fold(text)
    matches = list(_WORD_RE.finditer(folded))
    spans = []
    for match in matches:
        word = match.group(0)
        spans.append((match.start(), match.end(), word))
        if not word.isdecimal() and any(char.isdecimal() for char in word):
            for part in _RUN_RE.finditer(word):
                spans.append((match.start() + part.start(), match.start() + part.end(), part.group(0)))
    for left, right in zip(matches, matches[1:]):
        if right.start() == left.end() + 1 and folded[left.end()] == '-':
            spans.append((left.start(), right.end(), left.group(0) + right.group(0)))
    spans.sort(key=lambda span: (span[0], -span[1]))
    return spans


def document_words(text):
    """Слова задачи со вторыми написаниями — для словаря поиска."""
    return [word for _start, _end, word in words_with_positions(text)]


def utf16_ranges(text, ranges):
    """Позиции знаков Python → позиции UTF-16, в которых режет строку браузер.

    Эмодзи и прочие знаки вне BMP — один знак в Python и два в JavaScript:
    без пересчёта каждое «🔥» перед словом сдвигало подсветку на букву (разбор
    27.11.2026; темы задач копируют из Telegram вместе с эмодзи).
    """
    text = str(text or '')
    if not ranges or all(ord(char) < 0x10000 for char in text):
        return ranges
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + (2 if ord(char) >= 0x10000 else 1))
    return [[offsets[start], offsets[end]] for start, end in ranges]


def fix_layout_to_ru(text):
    """«jnxtn» → «отчет»: набрано латиницей на русской раскладке."""
    return ''.join(EN_TO_RU_LAYOUT.get(char, char) for char in str(text or '').lower())


def fix_layout_to_en(text):
    """«еудупкфь» → «telegram»: набрано кириллицей на английской раскладке."""
    return ''.join(RU_TO_EN_LAYOUT.get(char, char) for char in str(text or '').lower())


# ── Расстояние между словами ────────────────────────────────────────────────

def typo_budget(token):
    """Сколько ошибок прощаем слову запроса такой длины."""
    if token.isdecimal():
        return 0  # «125» и «126» — разные задачи, а не опечатка
    if len(token) >= 9:
        return 2
    if len(token) >= 4:
        return 1
    return 0


def within_one(left, right):
    """Слова расходятся не больше чем на одну правку (перестановка соседей — одна)."""
    len_left, len_right = len(left), len(right)
    if abs(len_left - len_right) > 1:
        return False
    if len_left > len_right:
        left, right = right, left
        len_left, len_right = len_right, len_left
    index = 0
    while index < len_left and left[index] == right[index]:
        index += 1
    if index == len_left:
        return True  # равны или у второго одна лишняя буква в конце
    if len_left == len_right:
        if left[index + 1:] == right[index + 1:]:
            return True  # одна чужая буква
        return (index + 1 < len_left and left[index] == right[index + 1]
                and left[index + 1] == right[index] and left[index + 2:] == right[index + 2:])
    return left[index:] == right[index + 1:]  # одна лишняя буква в середине


def distance_within(left, right, limit):
    """Расстояние Дамерау — Левенштейна (OSA), если оно не больше limit, иначе None."""
    if left == right:
        return 0
    if limit <= 0 or abs(len(left) - len(right)) > limit:
        return None
    if limit == 1:
        return 1 if within_one(left, right) else None
    cols = len(right)
    before = None
    previous = list(range(cols + 1))
    for row in range(1, len(left) + 1):
        char = left[row - 1]
        current = [row] + [0] * cols
        row_best = row
        for col in range(1, cols + 1):
            value = min(previous[col] + 1, current[col - 1] + 1,
                        previous[col - 1] + (char != right[col - 1]))
            if (before is not None and col > 1 and char == right[col - 2]
                    and left[row - 2] == right[col - 1]):
                value = min(value, before[col - 2] + 1)
            current[col] = value
            if value < row_best:
                row_best = value
        if row_best > limit:
            return None
        before, previous = previous, current
    return previous[cols] if previous[cols] <= limit else None


def _common_prefix(left, right):
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def is_stem_match(token, word):
    """Одна основа, разные окончания: «табель» — «табеля», «отчеты» — «отчет».

    Основа — общее начало не короче четырёх букв и не короче слова запроса без
    двух последних; хвосты — до двух букв у запроса и до трёх у слова. Так
    «водитель» находит «водителей», а «марка» не находит «маркетинг» (хвост
    «етинг» длиннее окончания). У слова из четырёх букв основа — само слово без
    последней: «акты» находит «акт», но «план» не находит «плат».
    """
    if token == word or word.startswith(token) or token.isdecimal():
        return False
    length = len(token)
    if length == 4:
        return word == token[:3]
    if length < 5:
        return False
    common = _common_prefix(token, word)
    return common >= max(4, length - 2) and length - common <= 2 and len(word) - common <= 3


def _last_letter_swap(token, word):
    """Слова одной длины, разница — только последняя буква («табль» — «табло»)."""
    return len(token) == len(word) and token[:-1] == word[:-1] and token != word


def _fuzzy_allowed(token, word):
    """Можно ли вообще сравнивать на опечатку — дешёвое отсечение до расстояния.

    Ошибку разом и в первой, и во второй букве не прощаем: такое слово человек
    почти наверняка набирал другое. У слова из четырёх букв первая обязана
    совпасть.
    """
    if len(token) == 4:
        return word[:1] == token[:1]
    return word[:1] == token[:1] or word[1:2] == token[1:2]


def match_word(token, word, fuzzy=True):
    """(вид, оценка) совпадения слова запроса со словом задачи или None.

    Короткие слова строже: двухбуквенное — только целиком («оп» — отдел ОП, а не
    все «операторы»), внутри слова — от четырёх букв.
    """
    if token == word:
        return EXACT, 100.0
    length = len(token)
    is_number = token.isdecimal()
    if word.startswith(token) and (length >= 3 or (is_number and length >= 2)):
        return PREFIX, 90.0 + 8.0 * length / len(word)
    if is_number:
        return None
    if is_stem_match(token, word):
        return STEM, 85.0
    if length >= 4 and token in word:
        return INFIX, 65.0
    budget = typo_budget(token) if fuzzy else 0
    if not budget or not _fuzzy_allowed(token, word):
        return None
    return _fuzzy_kind(token, word, budget)


def _fuzzy_kind(token, word, budget):
    """Похожее написание — когда точные виды уже не подошли."""
    distance = distance_within(token, word, budget)
    if distance is not None:
        return FUZZY, 60.0 if distance == 1 else 45.0
    # Слово ещё набирается — и уже с опечаткой: «атчетн» → «отчетность».
    length = len(token)
    if length >= 5 and len(word) > length:
        best = None
        for cut in (length - 1, length, length + 1):
            if cut > len(word):
                continue
            distance = distance_within(token, word[:cut], budget)
            if distance is not None and (best is None or distance < best):
                best = distance
        if best is not None:
            return PREFIX_FUZZY, 55.0 if best == 1 else 40.0
    return None


# ── Запрос ──────────────────────────────────────────────────────────────────

@dataclass
class QueryToken:
    text: str
    required: bool
    # То же слово в обратной раскладке («учсуд» → «excel») — запасное написание.
    alternate: str = ''
    # Слово — часть ключа задачи из запроса («hr» и «125» в «HR-125»).
    key_part: bool = False


@dataclass
class QueryVariant:
    tokens: list
    penalty: float = 1.0
    phrase: str = ''

    @property
    def required(self):
        return [token for token in self.tokens if token.required]


@dataclass
class ParsedQuery:
    raw: str
    variants: list
    keys: list            # [(код, номер)] — ключи задач из запроса
    numbers: list         # номера без кода: «125», «#125»
    key_only: bool        # запрос целиком — один ключ задачи
    layout_text: str = ''  # запрос в исправленной раскладке, если он был латиницей

    @property
    def empty(self):
        return not any(variant.required for variant in self.variants)


def _unique_words(text):
    seen, words = set(), []
    for word in split_words(text):
        if word not in seen:
            seen.add(word)
            words.append(word)
    return words


def _key_words(keys):
    """Слова, из которых в запросе собран ключ: код, номер, склейка, код в раскладке."""
    words = set()
    for code, number in keys:
        code = code.lower()
        words.update({code, str(number), '%s%d' % (code, number), fix_layout_to_ru(code)})
    return words


def _build_variant(words, key_words, penalty=1.0):
    tokens = []
    for word in words:
        # Номер значим при любой длине: «3» на открытой доске — это задача HR-3.
        required = (len(word) >= 2 or word.isdecimal()) and word not in STOP_WORDS
        alternate = ''
        # Обратную раскладку пробуем только у слов, набранных как есть: в
        # варианте с уже исправленной раскладкой она вернула бы исходную латиницу.
        if penalty == 1.0 and len(word) >= 3 and _CYRILLIC_RE.search(word) and not _LATIN_RE.search(word):
            flipped = fix_layout_to_en(word)
            if flipped != word and not _CYRILLIC_RE.search(flipped):
                alternate = flipped
        tokens.append(QueryToken(word, required, alternate, word in key_words))
    # Запрос из одних предлогов («по») всё же ищем — иначе искать нечего.
    if tokens and not any(token.required for token in tokens):
        for token in tokens:
            token.required = len(token.text) >= 2 or token.text.isdecimal()
    required = [token for token in tokens if token.required][:MAX_TOKENS]
    optional = [token for token in tokens if not token.required][:4]
    return QueryVariant(required + optional, penalty, ' '.join(words) if len(words) >= 2 else '')


def _keys_from(text):
    keys = []
    for pattern in (_KEY_DASH_RE, _KEY_GLUED_RE):
        for match in pattern.finditer(text):
            key = (match.group(1).upper(), int(match.group(2)))
            if key[1] > 0 and key not in keys:
                keys.append(key)
    return keys


def parse_query(raw):
    """Запрос → слова, варианты написания и ключи задач."""
    raw = str(raw or '').strip()[:MAX_QUERY_CHARS]
    lowered = raw.lower()
    keys = _keys_from(lowered)
    # «рк-125»: код ключа набран на русской раскладке.
    for key in _keys_from(fix_layout_to_en(lowered)):
        if key not in keys:
            keys.append(key)
    key_words = _key_words(keys)

    words = _unique_words(raw)
    variants = [_build_variant(words, key_words)] if words else []
    layout_text = ''
    key_only = len(keys) == 1 and bool(words) and all(word in key_words for word in words)
    if _LATIN_RE.search(lowered) and not key_only:
        # Раскладку чиним по ВСЕЙ строке, а не по словам: на месте «б», «ю»,
        # «ж», «х», «э» латиница ставит знаки препинания («j,jhel» —
        # «оборуд»), и разбор на слова разрезал бы слово надвое.
        flipped_words = _unique_words(fix_layout_to_ru(lowered))
        if flipped_words and flipped_words != words and any(_CYRILLIC_RE.search(w) for w in flipped_words):
            variants.append(_build_variant(flipped_words, key_words, _LAYOUT_PENALTY))
            layout_text = ' '.join(flipped_words)

    numbers = sorted({int(word) for word in words if word.isdecimal() and len(word) <= 9 and int(word) > 0})
    return ParsedQuery(raw, variants, keys, numbers, key_only, layout_text)


# ── Документ и выдача ───────────────────────────────────────────────────────

@dataclass
class Document:
    id: int
    subject: str = ''
    description: str = ''
    people: tuple = ()         # имена: исполнители, постановщик, поручитель
    key_code: str = ''
    key_number: int = 0
    key_aliases: tuple = ()    # прежние ключи: ((код, номер), …)
    created_rank: float = 0.0  # больше — свежее; решает только при равенстве


@dataclass
class Hit:
    id: int
    tier: int
    matched: int
    required: int
    score: float
    created_rank: float
    via_layout: bool = False
    key_matched: bool = False
    variant_index: int = 0
    # слово запроса → {слово темы: вид совпадения} — для подсветки
    subject_words: dict = field(default_factory=dict)
    # слова запроса, которые нашлись только в описании или у людей
    elsewhere: tuple = ()

    def sort_key(self):
        return (self.tier, -self.matched, -self.score, -self.created_rank, -self.id)


class _Vocabulary:
    """Все слова отобранных задач: слово → [(задача, поле)].

    Сопоставление идёт по словарю, а не по задачам: одинаковых слов в темах
    много («отчёт», «заполнить», «ноябрь»), и каждое сверяется с запросом
    один раз, а не столько раз, сколько задач его содержит.
    """

    def __init__(self, documents):
        self.occurrences = {}
        self.subject_text = []
        for index, doc in enumerate(documents):
            # Фраза «подряд» сверяется по словам как они есть, словарь — со вторыми
            # написаниями (части «2гис», склейки «e-mail»), см. words_with_positions.
            self.subject_text.append(' '.join(split_words(doc.subject)))
            for field_name, words in (
                    (SUBJECT, document_words(doc.subject)),
                    (PEOPLE, document_words(' '.join(doc.people or ()))),
                    (DESCRIPTION, document_words((doc.description or '')[:DESCRIPTION_SEARCH_CHARS]))):
                for word in set(words):
                    self.occurrences.setdefault(word, []).append((index, field_name))
        self.sorted_words = sorted(self.occurrences)
        # Похожее написание ищем только в теме и у людей: по описанию прощать
        # опечатки — значит находить посторонние задачи по случайному слову
        # длинной постановки (тот же вывод у вики: «триграммного слоя по телу нет»).
        self.fuzzy_words = [word for word in self.sorted_words
                            if any(place[1] != DESCRIPTION for place in self.occurrences[word])]

    def _with_prefix(self, prefix):
        words = self.sorted_words
        for index in range(bisect_left(words, prefix), len(words)):
            word = words[index]
            if not word.startswith(prefix):
                break
            yield word

    def matches(self, token, loose=True):
        """{слово словаря: (вид, оценка)} для одного слова запроса.

        loose=False — только целиком, началом и основой: так сверяется запасное
        написание в другой раскладке, иначе каждое русское слово тащило бы в
        выдачу латинский мусор вроде «jnxtn».
        """
        found = {}
        if token in self.occurrences:
            found[token] = (EXACT, 100.0)
        length = len(token)
        is_number = token.isdecimal()
        if length >= 3 or (is_number and length >= 2):
            for word in self._with_prefix(token):
                if word not in found:
                    found[word] = match_word(token, word, fuzzy=False)
        if is_number or length < 4:
            return found
        base = token[:3] if length == 4 else token[:max(4, length - 2)]
        for word in self._with_prefix(base):
            if word not in found and is_stem_match(token, word):
                found[word] = (STEM, 85.0)
        if not loose:
            return found
        for word in self.sorted_words:
            if token in word and word not in found:
                found[word] = (INFIX, 65.0)
        budget = typo_budget(token)
        for word in self.fuzzy_words:
            if word in found or len(word) < length - budget or not _fuzzy_allowed(token, word):
                continue
            if len(word) > length + budget and length < 5:
                continue  # у коротких «набирается с опечаткой» не бывает
            result = _fuzzy_kind(token, word, budget)
            if result:
                found[word] = result
        return found


def _key_matches(parsed, documents):
    """(задачи с ключом из запроса, {номер: задачи с этим номером})."""
    wanted = set(parsed.keys)
    numbers = set(parsed.numbers)
    exact, by_number = set(), {}
    if not wanted and not numbers:
        return exact, by_number
    for index, doc in enumerate(documents):
        own = []
        if doc.key_code and doc.key_number:
            own.append((str(doc.key_code).upper(), int(doc.key_number)))
        for code, number in doc.key_aliases or ():
            if code and number:
                own.append((str(code).upper(), int(number)))
        for key in own:
            if key in wanted:
                exact.add(index)
            if key[1] in numbers:
                by_number.setdefault(key[1], set()).add(index)
    return exact, by_number


def _new_slot():
    return {'best': 0.0, 'strong': False, 'key': False, 'fields': set(), 'words': {}}


def _score_variant(variant, vocabulary, exact_keys, by_number):
    """{индекс задачи: [итог по каждому слову запроса]} одного варианта написания."""
    per_doc = {}
    size = len(variant.tokens)

    def slot_of(doc_index, position):
        slots = per_doc.get(doc_index)
        if slots is None:
            slots = per_doc[doc_index] = [None] * size
        if slots[position] is None:
            slots[position] = _new_slot()
        return slots[position]

    for position, token in enumerate(variant.tokens):
        sources = [(vocabulary.matches(token.text), 1.0, token.text)]
        if token.alternate:
            sources.append((vocabulary.matches(token.alternate, loose=False), _LAYOUT_PENALTY, token.alternate))
        for found, factor, spelling in sources:
            # Набранное слово само встречается в задачах — значит, это настоящая
            # форма слова, и «смены» → «смена» действительно другое окончание.
            literal = any(kind in (EXACT, PREFIX) for kind, _score in found.values())
            for word, (kind, score) in found.items():
                if kind == STEM and not literal and _last_letter_swap(spelling, word):
                    # «табль» → «табло»: заменена одна последняя буква. Окончание
                    # это или опечатка, по одному слову не различить, — значит,
                    # похожее, наравне с «табль» → «табель», а не точное.
                    kind, score = FUZZY, 60.0
                for doc_index, field_name in vocabulary.occurrences[word]:
                    slot = slot_of(doc_index, position)
                    slot['best'] = max(slot['best'], score * factor * _FIELD_WEIGHT[field_name])
                    slot['strong'] = slot['strong'] or kind in STRONG_KINDS
                    slot['fields'].add(field_name)
                    if field_name == SUBJECT:
                        previous = slot['words'].get(word)
                        if previous is None or _KIND_RANK[kind] > _KIND_RANK[previous]:
                            slot['words'][word] = kind
        # Ключ задачи: у задачи HR-125 слова «hr» и «125» запроса нашлись ключом.
        key_docs = set(exact_keys) if token.key_part else set()
        # Голый номер «125» — тоже ключ: на открытой доске это одна задача.
        # Номер из ключа «HR-125» — нет: там он значит только вместе с кодом.
        if token.text.isdecimal() and not token.key_part:
            key_docs |= by_number.get(int(token.text), set()) if len(token.text) <= 9 else set()
        for doc_index in key_docs:
            slot = slot_of(doc_index, position)
            slot['best'] = max(slot['best'], 100.0 * _FIELD_WEIGHT[KEY])
            slot['strong'] = slot['key'] = True
            slot['fields'].add(KEY)
    return per_doc


def search(query, documents):
    """Ранжированная выдача: [Hit] в порядке показа. Пустой запрос — пустой список."""
    parsed = query if isinstance(query, ParsedQuery) else parse_query(query)
    if parsed.empty or not documents:
        return []
    vocabulary = _Vocabulary(documents)
    exact_keys, by_number = _key_matches(parsed, documents)

    best = {}
    for variant_index, variant in enumerate(parsed.variants):
        required_count = len(variant.required)
        if not required_count:
            continue
        for doc_index, slots in _score_variant(variant, vocabulary, exact_keys, by_number).items():
            pairs = list(zip(variant.tokens, slots))
            matched = sum(1 for token, slot in pairs if token.required and slot)
            if not matched:
                continue  # совпали одни предлоги
            if matched == required_count:
                strong = all(slot['strong'] for token, slot in pairs if token.required)
                tier = TIER_EXACT if strong else TIER_SIMILAR
            else:
                tier = TIER_PARTIAL
            key_matched = doc_index in exact_keys or any(slot and slot['key'] for slot in slots)
            if parsed.key_only and doc_index in exact_keys:
                tier = TIER_KEY
            score = sum(slot['best'] for slot in slots if slot) * variant.penalty
            if variant.phrase and variant.phrase in vocabulary.subject_text[doc_index]:
                score += _PHRASE_BONUS
            if doc_index in exact_keys:
                score += _KEY_BONUS
            doc = documents[doc_index]
            hit = Hit(
                id=doc.id, tier=tier, matched=matched, required=required_count, score=score,
                created_rank=doc.created_rank, via_layout=variant.penalty < 1.0,
                key_matched=key_matched, variant_index=variant_index,
                subject_words={token.text: dict(slot['words']) for token, slot in pairs if slot and slot['words']},
                elsewhere=tuple(token.text for token, slot in pairs
                                if token.required and slot and not slot['words'] and not slot['key']),
            )
            current = best.get(doc_index)
            if current is None or hit.sort_key() < current.sort_key():
                best[doc_index] = hit

    hits = list(best.values())
    complete = [hit for hit in hits if hit.tier <= TIER_SIMILAR]
    if complete:
        hits = complete
    else:
        # Деградация: всех слов нет ни у одной задачи — показываем те, где
        # совпала хотя бы половина, больше совпавших — выше.
        hits = [hit for hit in hits if hit.matched >= max(1, math.ceil(hit.required / 2))]
    hits.sort(key=Hit.sort_key)
    return hits


# ── Подсветка для строки выдачи ─────────────────────────────────────────────

def _merge_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _word_range(start, end, word, token, kind):
    """Какую часть слова подсветить: набранное начало, вхождение или слово целиком."""
    if kind == PREFIX:
        return [start, start + min(len(token), end - start)]
    if kind == INFIX:
        offset = word.find(token)
        if offset >= 0:
            return [start + offset, start + offset + len(token)]
    return [start, end]


def _variant_of(parsed, hit):
    if not parsed.variants:
        return None
    return parsed.variants[min(hit.variant_index, len(parsed.variants) - 1)]


def _strong_ranges(text, tokens):
    """Подсветка слов запроса в тексте — только точные совпадения."""
    ranges = []
    for start, end, word in words_with_positions(text):
        for token in tokens:
            result = None
            for spelling in filter(None, (token.text, token.alternate)):
                result = match_word(spelling, word, fuzzy=False)
                if result:
                    ranges.append(_word_range(start, end, word, spelling, result[0]))
                    break
            if result:
                break
    return _merge_ranges(ranges)


def _people_ranges(name, tokens):
    ranges = []
    for start, end, word in words_with_positions(name):
        for token in tokens:
            if any(match_word(spelling, word) for spelling in filter(None, (token.text, token.alternate))):
                ranges.append([start, end])
                break
    return _merge_ranges(ranges)


def _snippet(description, tokens):
    """Отрывок описания вокруг первого найденного слова — с подсветкой."""
    text = (description or '')[:DESCRIPTION_SEARCH_CHARS]
    ranges = _strong_ranges(text, tokens)
    if not ranges:
        return None
    first = ranges[0][0]
    begin = max(0, first - SNIPPET_BEFORE)
    if begin > 0:
        # Начинаем со слова, а не с его середины.
        space = text.rfind(' ', 0, begin)
        if space >= 0 and first - space <= SNIPPET_BEFORE + 20:
            begin = space + 1
    end = min(len(text), begin + SNIPPET_LENGTH)
    piece = text[begin:end]
    # Переводы строк — пробелом, один знак на один: подсветка не съезжает.
    piece = piece.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    shifted = [[start - begin, min(stop, end) - begin] for start, stop in ranges if begin <= start < end]
    return {
        'text': piece,
        'ranges': utf16_ranges(piece, _merge_ranges(shifted)),
        'cut_start': begin > 0,
        'cut_end': end < len(description or ''),
    }


def describe(parsed, hit, doc):
    """Что показать в строке выдачи: подсветка темы, отрывок описания, люди."""
    subject_kinds = {}
    for token_text, words in (hit.subject_words or {}).items():
        for word, kind in words.items():
            current = subject_kinds.get(word)
            if current is None or _KIND_RANK[kind] > _KIND_RANK[current[1]]:
                subject_kinds[word] = (token_text, kind)
    subject = []
    for start, end, word in words_with_positions(doc.subject):
        found = subject_kinds.get(word)
        if found:
            subject.append(_word_range(start, end, word, found[0], found[1]))

    variant = _variant_of(parsed, hit)
    elsewhere = [token for token in (variant.tokens if variant else []) if token.text in set(hit.elsewhere)]
    snippet = _snippet(doc.description, elsewhere) if elsewhere and doc.description else None
    people = []
    if elsewhere:
        for name in doc.people or ():
            ranges = _people_ranges(name, elsewhere)
            if ranges and all(item['name'] != name for item in people):
                people.append({'name': name, 'ranges': utf16_ranges(name, ranges)})
    return {
        'tier': hit.tier,
        # Позиции — в единицах UTF-16: в них режет строку браузер.
        'subject': utf16_ranges(doc.subject, _merge_ranges(subject)),
        'key': bool(hit.key_matched),
        'snippet': snippet,
        'people': people[:3],
        'layout': bool(hit.via_layout),
    }

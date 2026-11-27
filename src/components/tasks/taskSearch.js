/*
 * Поиск задач с опечатками — чистая часть фронта (владелец 27.11.2026: «поиск
 * задачи, чтобы можно было искать задачу легко и просто, так же учитывать
 * ошибки букв и тд, поиск будет искать по выбранной доске и проекту»).
 *
 * Сопоставляет и ранжирует сервер (task_search.py): он видит все задачи доски,
 * а не загруженную порцию. Здесь — когда вообще спрашивать, как разложить
 * выдачу по ступеням и как подсветить найденное. Модуль гоняется node --test,
 * поэтому импорты — с расширением.
 */

/* Ступени выдачи — те же числа, что в task_search.py (TIER_*). */
export const SEARCH_TIER_KEY = 0;
export const SEARCH_TIER_EXACT = 1;
export const SEARCH_TIER_SIMILAR = 2;
export const SEARCH_TIER_PARTIAL = 3;

/* Подписи над ступенями. Точные находки идут без подписи: это и есть ответ.
   Похожие и частичные подписаны, чтобы было видно, почему задача в выдаче, —
   слова запроса в ней буквально нет. */
const TIER_LABELS = {
  [SEARCH_TIER_SIMILAR]: 'Похожие по написанию',
  [SEARCH_TIER_PARTIAL]: 'Совпали не все слова',
};

/* Открытое окно портала: такой меткой помечает себя каждое окно (карточка
   задачи, «Новая задача», полноэкранные листы; см. src/utils/mobileScreenExit.js).
   Пока оно открыто, Ctrl/⌘+K не уводит фокус в поле поиска под ним. Константой
   здесь, а не строкой в доске: страж телефонной оболочки считает окном любой
   .jsx с этой меткой (tests/test_mobile_shell.py), а доска окном не является. */
export const OPEN_WINDOW_SELECTOR = '.otp-modal-root';

/* Сколько находок просить за раз: первая страница — сразу, остальное по кнопке. */
export const BOARD_SEARCH_PAGE = 30;
/* Пауза после последней буквы: короче — запрос на каждую букву, длиннее —
   выдача заметно отстаёт от набора. */
export const SEARCH_DEBOUNCE_MS = 250;

const WORD_RE = /[\p{L}\p{N}]+/gu;

/**
 * Есть ли что искать: слово хотя бы из двух букв или номер («3» — задача HR-3).
 * Те же правила, что у сервера (task_search.parse_query): запрос из одних
 * однобуквенных слов он не ищет, а отдаёт весь список — его не просим вовсе.
 */
export const isSearchableQuery = (value) => {
  const words = String(value || '').match(WORD_RE) || [];
  return words.some((word) => word.length >= 2 || /^\p{N}+$/u.test(word));
};

/** Что просить у сервера: охват доски + строка поиска + страница. Сводка не нужна. */
export const searchRequestParams = (scopeParams, query, offset = 0, limit = BOARD_SEARCH_PAGE) => ({
  ...(scopeParams || {}),
  q: String(query || '').trim(),
  limit,
  offset,
  summary: 0,
});

/**
 * Подсветка: текст → куски [{ text, mark }]. ranges — [[начало, конец], …] из
 * search_match сервера; кривые, перекрытые и вылезающие за край — сглаживаются,
 * текст при этом не теряется ни на букву.
 */
export const highlightSegments = (text, ranges) => {
  const source = String(text ?? '');
  const clean = (Array.isArray(ranges) ? ranges : [])
    .map((range) => [Number(range?.[0]), Number(range?.[1])])
    .filter(([start, end]) => Number.isFinite(start) && Number.isFinite(end))
    .map(([start, end]) => [Math.max(0, Math.min(source.length, start)), Math.max(0, Math.min(source.length, end))])
    .filter(([start, end]) => end > start)
    .sort((left, right) => left[0] - right[0]);
  const merged = [];
  clean.forEach(([start, end]) => {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  });
  const segments = [];
  let cursor = 0;
  merged.forEach(([start, end]) => {
    if (start > cursor) segments.push({ text: source.slice(cursor, start), mark: false });
    segments.push({ text: source.slice(start, end), mark: true });
    cursor = end;
  });
  if (cursor < source.length || !segments.length) segments.push({ text: source.slice(cursor), mark: false });
  return segments.filter((segment, index) => segment.text || index === 0);
};

/** Ступень задачи в выдаче; задачи без отметки сервера — как точные. */
export const searchTierOf = (task) => {
  const tier = Number(task?.search_match?.tier);
  return Number.isFinite(tier) ? tier : SEARCH_TIER_EXACT;
};

export const isSearchResultList = (list) => Array.isArray(list) && list.some((task) => task?.search_match);

/**
 * Строки списка выдачи: задачи и подписи ступеней между ними. По форме — как
 * разделители дат в «Все задачи» ({ type, key, label | task }): ранжированная
 * выдача идёт не по дате, и подписи «Сегодня / Вчера» встали бы вперемешку.
 */
export const withSearchSeparators = (list) => {
  const rows = [];
  let previous = null;
  (Array.isArray(list) ? list : []).forEach((task, index) => {
    const tier = searchTierOf(task);
    // Ключ и точные — одна группа без подписи.
    const group = tier <= SEARCH_TIER_EXACT ? SEARCH_TIER_EXACT : tier;
    if (group !== previous && TIER_LABELS[group]) {
      rows.push({ type: 'separator', key: `search-tier:${group}:${task?.id ?? index}`, label: TIER_LABELS[group] });
    }
    previous = group;
    rows.push({ type: 'task', key: `task:${task?.id ?? index}`, task });
  });
  return rows;
};

/** Следующая страница выдачи поверх уже показанной — без повторов, если между запросами задачи сдвинулись. */
export const appendSearchPage = (shown, next) => {
  const seen = new Set((shown || []).map((task) => task?.id));
  return [...(shown || []), ...(next || []).filter((task) => !seen.has(task?.id))];
};

const pluralFound = (count) => {
  const mod10 = Math.abs(count) % 10;
  const mod100 = Math.abs(count) % 100;
  if (mod10 === 1 && mod100 !== 11) return 'задача';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'задачи';
  return 'задач';
};

/** «Нашлось 12 задач» — подпись над выдачей. */
export const foundLabel = (count) => {
  const value = Math.max(0, Number(count) || 0);
  if (!value) return 'Ничего не нашлось';
  return `${value % 10 === 1 && value % 100 !== 11 ? 'Нашлась' : 'Нашлось'} ${value} ${pluralFound(value)}`;
};

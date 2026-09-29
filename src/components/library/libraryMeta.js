/*
 * «Библиотека» (задача #282): чистые функции раздела — без React и без сети.
 * Их грузит напрямую Node в tests/library_meta.test.mjs.
 *
 * СТРАНИЦА — 1800 ЗНАКОВ, А НЕ ЭКРАН. Разметку считает сервер при загрузке
 * книги (library/epub.py): для каждой главы — где она начинается и сколько в
 * ней знаков. Ридер только складывает: начало текущей главы + доля
 * пролистанного внутри неё. Так «стр. 40 из 285» у телефона и у компьютера —
 * одно и то же место книги, и мониторинг сравнивает сравнимое.
 */

export const STATUS_NOT_STARTED = 'not_started';
export const STATUS_IN_PROGRESS = 'in_progress';
export const STATUS_FINISHED = 'finished';

/* Подписи — дословно из ТЗ. */
export const STATUS_LABELS = Object.freeze({
    [STATUS_NOT_STARTED]: 'Не начато',
    [STATUS_IN_PROGRESS]: 'В процессе',
    [STATUS_FINISHED]: 'Закончено',
});

export const LIBRARY_TABS = Object.freeze({
    all: 'all',
    saved: 'saved',
    archive: 'archive',
    monitoring: 'monitoring',
});

/* Номер страницы (с единицы) для смещения в знаках. ТА ЖЕ формула, что
   library/epub.py: page_for_offset — тест сверяет их текстом. */
export const pageForOffset = (offset, totalChars, charsPerPage) => {
    const perPage = Math.max(1, Number(charsPerPage) || 1800);
    const totalPages = Math.max(1, Math.ceil(Math.max(0, Number(totalChars) || 0) / perPage));
    const page = Math.floor(Math.max(0, Number(offset) || 0) / perPage) + 1;
    return Math.max(1, Math.min(totalPages, page));
};

/*
 * Место в книге от движка страниц -> { offset, percent, page, atEnd }.
 *
 * position.section — номер главы (у сервера тот же порядок, с пустыми и
 * нелинейными главами), position.fraction — доля главы до начала текущего
 * экрана. Процент — от НАЧАЛА экрана, поэтому 100 % арифметикой не получается:
 * его ставит только последняя страница, до которой ДОЛИСТАЛИ вперёд
 * (position.atEnd && position.paged) — ТЗ 4.3 «при пролистывании последней
 * страницы». Переход по оглавлению к короткому «Об авторе» книгу не заканчивает.
 */
export const progressFromPosition = (position, book) => {
    if (!position || !Number.isInteger(position.section) || position.section < 0) return null;
    const spine = Array.isArray(book?.spine) ? book.spine : [];
    const totalChars = Math.max(0, Number(book?.total_chars) || 0);
    const perPage = Number(book?.chars_per_page) || 1800;
    const totalPages = Math.max(1, Number(book?.total_pages) || 1);
    if (position.atEnd && position.paged) {
        return { offset: totalChars, percent: 100, page: totalPages, atEnd: true };
    }
    const section = spine[position.section] || { start: 0, chars: 0 };
    const fraction = Math.max(0, Math.min(1, Number(position.fraction) || 0));
    const offset = Math.round((Number(section.start) || 0) + fraction * (Number(section.chars) || 0));
    const raw = totalChars > 0 ? (offset / totalChars) * 100 : 0;
    // До конца — не больше 99.9: «100 %» на экране без отметки «Закончено»
    // выглядело бы как ошибка счёта.
    const percent = Math.max(0, Math.min(99.9, Math.round(raw * 10) / 10));
    return { offset, percent, page: pageForOffset(offset, totalChars, perPage), atEnd: false };
};

/* Место для сервера: «номер главы:доля главы» — 12:0.4375 (library/routes.py:
   POSITION_RE). Доля — шесть знаков после точки, сколько принимает сервер: с
   четырьмя страница 35 из 60 сохранялась как 0.5833 и открывалась 34-й, а на
   развороте — целым разворотом раньше. */
export const formatPosition = (section, fraction) => {
    const share = Math.max(0, Math.min(0.999999, Number(fraction) || 0));
    return `${Math.max(0, Math.trunc(section) || 0)}:${Number(share.toFixed(6))}`;
};

/* Экран главы по доле: доля — начало экрана (или его середина), и допуск
   держит страницу на месте, когда доля пришла округлённой чуть меньше. */
export const pageAtFraction = (fraction, pages) => (
    Math.max(0, Math.min(pages - 1, Math.floor((Number(fraction) || 0) * pages + 0.01)))
);

export const parsePosition = (value) => {
    const match = /^(\d{1,5}):(0(?:\.\d{1,6})?|1(?:\.0{1,6})?)$/.exec(String(value || ''));
    return match ? { section: Number(match[1]), fraction: Number(match[2]) } : null;
};

/* Процент для подписи: без хвоста «.0», целые — целыми. */
export const formatPercent = (value) => {
    const number = Math.max(0, Math.min(100, Number(value) || 0));
    if (number >= 100) return '100 %';
    if (number > 0 && number < 1) return '<1 %';
    return `${Math.floor(number)} %`;
};

/* Пункт оглавления, в котором сейчас читатель: последний, чьё место не
   дальше текущего. -1 — ещё до первого пункта.

   Сравнивается СМЕЩЕНИЕ в знаках, а не страница: в начале книги на первой
   странице сидят титул, содержание и первая глава разом, и по странице
   обложка читалась бы как «Глава 1». Страница — запасной путь для
   оглавления без смещений.

   Список просматривается ЦЕЛИКОМ: порядок оглавления не обязан совпадать с
   порядком текста («Аннотация» из конца книги первым пунктом), и остановка на
   первом «дальнем» пункте теряла бы главу до конца книги. Берётся ближайший
   пункт не дальше читателя; из равных — последний (глава после титула той же
   страницы). */
export const currentTocIndex = (toc, page, offset = null) => {
    const list = Array.isArray(toc) ? toc : [];
    const byOffset = Number.isFinite(offset) && list.every((item) => Number.isFinite(item?.offset));
    const here = byOffset ? offset : page;
    let found = -1;
    let best = -Infinity;
    for (let index = 0; index < list.length; index += 1) {
        const mark = byOffset ? list[index].offset : (Number(list[index]?.page) || 0);
        if (mark <= here && mark >= best) {
            best = mark;
            found = index;
        }
    }
    return found;
};

/* Книга выдана отделу? Пустой отдел ('' / null) — «Все отделы». */
export const bookInDepartment = (book, departmentId) => {
    if (departmentId === '' || departmentId === null || departmentId === undefined) return true;
    const ids = Array.isArray(book?.department_ids) ? book.department_ids : [];
    return ids.includes(Number(departmentId));
};

/*
 * Что показать на вкладке: книги выбранного отдела, а из них —
 * «Общий доступ» (всё не из архива), «Сохранённые» (свои закладки не из
 * архива) или «Архив». Каталог приходит один раз, отдел и вкладка делят его
 * на месте.
 */
export const filterBooks = (books, tab, departmentId = '') => {
    const list = (Array.isArray(books) ? books : []).filter((book) => bookInDepartment(book, departmentId));
    if (tab === LIBRARY_TABS.archive) return list.filter((book) => book?.archived);
    const shelf = list.filter((book) => !book?.archived);
    return tab === LIBRARY_TABS.saved ? shelf.filter((book) => book?.saved) : shelf;
};

/* Книг на полке (не в архиве) у каждого отдела — для счётчиков в выборе
   отдела. Книга нескольких отделов считается у каждого. */
export const shelfCountByDepartment = (books) => {
    const counts = new Map();
    for (const book of Array.isArray(books) ? books : []) {
        if (book?.archived) continue;
        for (const id of Array.isArray(book?.department_ids) ? book.department_ids : []) {
            counts.set(id, (counts.get(id) || 0) + 1);
        }
    }
    return counts;
};

/* Выбранный отдел запоминается у смотрящего: тренер одного отдела не
   выбирает его заново при каждом входе. Отдел, которого больше нет в
   списке, превращается во «Все отделы». */
export const DEPARTMENT_STORAGE_KEY = 'otp.library.department';

export const restoreDepartment = (stored, departments) => {
    const id = Number(stored);
    if (!Number.isInteger(id) || id <= 0) return '';
    return (Array.isArray(departments) ? departments : []).some((item) => item?.id === id) ? id : '';
};

/* Дата последней активности для мониторинга. Сервер отдаёт часы Алматы БЕЗ
   пояса — показываем как есть, без перевода: иначе вечерняя запись уехала бы
   на завтра (та же ловушка, что с датами API во всём портале). */
export const formatActivity = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ''));
    if (!match) return '—';
    const [, year, month, day, hour, minute] = match;
    return `${day}.${month}.${year}, ${hour}:${minute}`;
};

/* Только день — для «Начал» и «Закончено» в мониторинге. */
export const formatDay = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    return match ? `${match[3]}.${match[2]}.${match[1]}` : '—';
};

export const EPUB_ACCEPT = '.epub,application/epub+zip';

export const isEpubFile = (file) => /\.epub$/i.test(String(file?.name || ''));

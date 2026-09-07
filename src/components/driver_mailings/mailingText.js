/*
 * Текст рассылки: разметка, двуязычная склейка и вставка форматирования.
 *
 * Почему свой разбор, а не общий renderMarkdown из ui/markdown.jsx. Тот рендерит
 * ПОЛНЫЙ markdown — заголовки, таблицы, код. Приложение Pro ничего из этого не
 * понимает: у него ровно четыре возможности — жирный, курсив, ссылка и список.
 * Показать в предпросмотре таблицу, которой у водителя не будет, хуже, чем не
 * показать ничего: человек отправит текст, уверенный, что тот выглядит иначе.
 *
 * Результат разбора — дерево узлов, из которого React собирает элементы. Ни
 * dangerouslySetInnerHTML, ни санитайзера: текст пишет сотрудник, но пишет его
 * в поле, а не в HTML, и превращать это в HTML по дороге незачем.
 */

/* Разделитель двуязычной рассылки — ТРИ НИЖНИХ ПОДЧЁРКИВАНИЯ.
 *
 * Приложение Pro рисует черту между языками именно по `___`: это разметка, и
 * приложение превращает её в горизонтальную линию. Первая версия ставила десять
 * символов «─» — в поле ввода они выглядят чертой, но водителю приезжают
 * обычным текстом, и вместо аккуратной линии он видит ряд палочек во всю ширину
 * (сверено со скриншотами из самого приложения 07.09.2026).
 *
 * Пустые строки вокруг обязательны: без них Pro считает подчёркивания
 * продолжением абзаца и черту не рисует. */
export const BILINGUAL_SEPARATOR = '\n\n___\n\n';

/* Пределы кабинета. Приходят с сервера в ответе overview, здесь — запасные
   значения на случай, если кабинет не ответил: интерфейс должен считать
   символы и до того, как узнал лимит. */
export const DEFAULT_MAX_TITLE = 120;
export const DEFAULT_MAX_MESSAGE = 1500;

/** Склейка двуязычного сообщения. Пустая часть не тянет за собой разделитель. */
export const composeBilingual = (kk, ru) => {
    const left = String(kk ?? '').trim();
    const right = String(ru ?? '').trim();
    if (left && right) return left + BILINGUAL_SEPARATOR + right;
    return left || right;
};

/**
 * Обратное разбиение — для кнопки «Повторить» в журнале.
 *
 * Возвращает { kk, ru, bilingual }. Разделитель ищем по строке из трёх и более
 * подчёркиваний, а не по точному совпадению: рассылку могли собрать руками в
 * кабинете, и длина черты там гуляет. Старые «─» тоже узнаём — рассылки,
 * отправленные до смены разделителя, открываются кнопкой «Повторить», и
 * разобрать их обратно на два языка мы обязаны.
 */
export const splitBilingual = (text) => {
    const source = String(text ?? '');
    const match = source.match(/\n\s*(?:_{3,}|─{3,})\s*\n/);
    if (!match) return { kk: '', ru: source, bilingual: false };
    return {
        kk: source.slice(0, match.index).trim(),
        ru: source.slice(match.index + match[0].length).trim(),
        bilingual: true,
    };
};

/*
 * Разбор строки на инлайновые узлы: жирный, курсив, ссылка.
 *
 * Порядок в регулярном выражении значим: ссылка ищется первой, иначе «_» внутри
 * адреса (их полно в utm-метках) превратился бы в курсив и разорвал ссылку.
 */
const INLINE_RE = /\[([^\]\n]+)\]\(([^)\s]+)\)|\*\*([^*\n]+)\*\*|_([^_\n]+)_/g;

const parseInline = (line) => {
    const nodes = [];
    let last = 0;
    let match;
    INLINE_RE.lastIndex = 0;
    while ((match = INLINE_RE.exec(line)) !== null) {
        if (match.index > last) nodes.push(line.slice(last, match.index));
        if (match[1] !== undefined) nodes.push({ type: 'link', text: match[1], href: match[2] });
        else if (match[3] !== undefined) nodes.push({ type: 'bold', text: match[3] });
        else nodes.push({ type: 'italic', text: match[4] });
        last = match.index + match[0].length;
    }
    if (last < line.length) nodes.push(line.slice(last));
    return nodes;
};

/*
 * Строка списка. Считаем маркерами и «•», и «-», и «*»: в поле человек наберёт
 * то, что привык, а кнопка панели ставит «• ». Ведущие пробелы допустимы —
 * при вставке из другого редактора они приезжают почти всегда.
 */
const BULLET_RE = /^[ \t]*[•\-*][ \t]+(.*)$/;

/* Строка-разделитель между языками. Разбирается ДО инлайновой разметки: иначе
   `___` уехало бы в курсив (`_текст_`) и предпросмотр показал бы подчёркивания
   вместо черты — то есть врал бы ровно про то место, из-за которого разделитель
   и меняли. */
const RULE_RE = /^\s*(?:_{3,}|─{3,})\s*$/;

/**
 * Текст рассылки → список блоков для предпросмотра.
 *
 * Блоки: { type: 'list', items: [inline[]] }, { type: 'line', nodes: inline[] },
 * { type: 'rule' } — черта между языками, { type: 'gap' } — пустая строка.
 * Пустая строка — это { type: 'gap' }: в уведомлении Pro пустая строка видна как
 * отбивка абзаца, и съедать её нельзя, иначе предпросмотр врёт про вёрстку.
 */
export const parseMailingText = (text) => {
    const lines = String(text ?? '').split('\n');
    const blocks = [];
    let list = null;
    const flush = () => { if (list) { blocks.push(list); list = null; } };
    for (const line of lines) {
        if (RULE_RE.test(line)) {
            flush();
            blocks.push({ type: 'rule' });
            continue;
        }
        const bullet = line.match(BULLET_RE);
        if (bullet) {
            if (!list) list = { type: 'list', items: [] };
            list.items.push(parseInline(bullet[1]));
            continue;
        }
        flush();
        if (!line.trim()) blocks.push({ type: 'gap' });
        else blocks.push({ type: 'line', nodes: parseInline(line) });
    }
    flush();
    return blocks;
};

/** Текст без разметки — для превью строки в журнале и для подписи в списке. */
export const stripMailingMarkup = (text) => String(text ?? '')
    .replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, '$1')
    .replace(/\*\*([^*\n]+)\*\*/g, '$1')
    .replace(/_([^_\n]+)_/g, '$1')
    .replace(/^[ \t]*[•\-*][ \t]+/gm, '')
    .replace(/^\s*(?:_{3,}|─{3,})\s*$/gm, ' ')
    .replace(/\s+/g, ' ')
    .trim();

/*
 * Вставка форматирования в поле ввода.
 *
 * Возвращает новое значение и новое выделение, а не правит DOM: значение поля
 * контролируемое, и трогать его мимо React означало бы потерять правку на
 * следующем рендере. Выделение проставляет вызывающий код — только он знает
 * ссылку на textarea.
 *
 * Если выделения нет, вставляется заготовка с осмысленным словом внутри и
 * выделяется именно она: пустые «****» человек потом не находит.
 */
const WRAPPERS = {
    bold: { open: '**', close: '**', sample: 'текст' },
    italic: { open: '_', close: '_', sample: 'текст' },
};

/* Заготовки ссылки — ровно те слова, что в задании: «[введите текст](вставьте
   ссылку)». Обе видны в поле и обе выделяются, поэтому их нельзя не заметить и
   нельзя случайно отправить: незаполненная заготовка бросается в глаза и в
   предпросмотре. */
export const LINK_TEXT_PLACEHOLDER = 'введите текст';
export const LINK_HREF_PLACEHOLDER = 'вставьте ссылку';

export const applyFormatting = (value, selectionStart, selectionEnd, kind) => {
    const text = String(value ?? '');
    const from = Math.max(0, Math.min(selectionStart ?? 0, text.length));
    const to = Math.max(from, Math.min(selectionEnd ?? from, text.length));
    const selected = text.slice(from, to);

    if (kind === 'link') {
        const label = selected || LINK_TEXT_PLACEHOLDER;
        const inserted = `[${label}](${LINK_HREF_PLACEHOLDER})`;
        // Курсор ставим внутрь скобок с адресом и выделяем заготовку: подпись
        // человек уже написал или увидит выделенной, а адрес надо вставить
        // обязательно. Заготовка — слова «вставьте ссылку», как в задании:
        // «https://» выглядело наполовину готовым адресом, и его дописывали
        // прямо к нему, получая «https://https://...».
        const caret = from + label.length + 3;
        return {
            value: text.slice(0, from) + inserted + text.slice(to),
            selectionStart: caret,
            selectionEnd: caret + LINK_HREF_PLACEHOLDER.length,
        };
    }

    if (kind === 'list') {
        // Список применяется к строкам, попавшим в выделение целиком: иначе
        // маркер вставлялся бы в середину слова.
        const lineStart = text.lastIndexOf('\n', from - 1) + 1;
        const lineEndRaw = text.indexOf('\n', to);
        const lineEnd = lineEndRaw === -1 ? text.length : lineEndRaw;
        const chunk = text.slice(lineStart, lineEnd) || '';
        const marked = chunk
            .split('\n')
            .map((line) => (BULLET_RE.test(line) ? line : `• ${line}`))
            .join('\n');
        return {
            value: text.slice(0, lineStart) + marked + text.slice(lineEnd),
            selectionStart: lineStart,
            selectionEnd: lineStart + marked.length,
        };
    }

    const wrap = WRAPPERS[kind];
    if (!wrap) return { value: text, selectionStart: from, selectionEnd: to };

    // Повторное нажатие на уже обёрнутом фрагменте снимает разметку — так ведут
    // себя все редакторы, и без этого «жирный» можно только добавить.
    const before = text.slice(Math.max(0, from - wrap.open.length), from);
    const after = text.slice(to, to + wrap.close.length);
    if (selected && before === wrap.open && after === wrap.close) {
        const start = from - wrap.open.length;
        return {
            value: text.slice(0, start) + selected + text.slice(to + wrap.close.length),
            selectionStart: start,
            selectionEnd: start + selected.length,
        };
    }

    const body = selected || wrap.sample;
    const inserted = wrap.open + body + wrap.close;
    return {
        value: text.slice(0, from) + inserted + text.slice(to),
        selectionStart: from + wrap.open.length,
        selectionEnd: from + wrap.open.length + body.length,
    };
};

/*
 * Русское числительное для получателей и диспетчерских. Своё, а не Intl.PluralRules:
 * нужны сами слова, а правило для русского здесь в три строки.
 */
export const plural = (count, one, few, many) => {
    const n = Math.abs(Number(count) || 0) % 100;
    const n1 = n % 10;
    if (n > 10 && n < 20) return many;
    if (n1 > 1 && n1 < 5) return few;
    if (n1 === 1) return one;
    return many;
};

/*
 * Число с неразрывным пробелом между разрядами.
 *
 * Разделитель нормализуем явно: toLocaleString отдаёт то U+00A0, то U+202F,
 * то обычный пробел — в зависимости от среды. Обычный пробел здесь
 * недопустим: охват стоит в кнопке отправки, и «1 144» с переносом посреди
 * числа читается как два разных числа.
 */
export const formatCount = (value) => {
    // null и undefined — это «ещё не посчитали», а не ноль. Number(null) даёт 0,
    // и без явной проверки пустой охват выглядел бы как «получателей нет».
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return n.toLocaleString('ru-RU').replace(/[\s  ]/g, ' ');
};

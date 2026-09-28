/*
 * Чистые помощники загрузчика книги: пути внутри архива и стиль книги.
 * Без JSZip и без DOM-дерева — их грузит Node в tests/library_epub_text.test.mjs.
 *
 * КНИГА НЕ ХОДИТ В ИНТЕРНЕТ. Любой адрес в стиле книги — либо ресурс из самой
 * книги (blob:), либо ничего. Иначе картинка-«пиксель» в CSS сообщала бы
 * третьему сайту, кто, когда и какую книгу читает.
 */

/* Путь внутри архива: href относительно каталога dir, без #якоря. Та же
   нормализация, что у сервера (library/epub.py: _join), — иначе главы и
   картинки находились бы по-разному. */
export const resolvePath = (dir, href) => {
    let path = String(href || '').split('#')[0];
    try { path = decodeURIComponent(path); } catch { /* оставить как есть */ }
    const parts = (dir ? `${dir}/${path}` : path).split('/');
    const out = [];
    for (const part of parts) {
        if (!part || part === '.') continue;
        if (part === '..') out.pop();
        else out.push(part);
    }
    return out.join('/');
};

/* Якорь ссылки: «%D1%81%D0%BD1» — это id="сн1". */
export const decodeAnchor = (anchor) => {
    try { return decodeURIComponent(String(anchor || '')); } catch { return String(anchor || ''); }
};

/* «page-break-before: always» у главы — это «начать с новой страницы».
   Страницы ридера — колонки, и браузер понимает здесь разрыв колонки. */
export const normalizeBreaks = (css) => String(css || '')
    .replace(/page-break-(before|after)\s*:\s*(always|left|right|page)/gi, 'break-$1: column')
    // Без просмотра назад (?<!…): Safari до 16.4 его не знает, и модуль целиком
    // не загрузился бы на старом iPhone.
    .replace(/(^|[^-\w])break-(before|after)\s*:\s*(page|left|right|recto|verso)/gi, '$1break-$2: column');

/* Значение, которое может сходить в сеть или спрятать это за экранированием:
   url(...) не на blob:, image-set(...), обратная косая (\75 rl — тот же url). */
const UNSAFE_VALUE = /image-set\(|\\|expression\(|url\(\s*(?!["']?blob:)/i;

/* Атрибут style="…" элемента книги: объявления, способные сходить в сеть, —
   прочь, fixed — прочь, разрывы страниц — в разрывы колонок. */
export const cleanInlineStyle = (value) => normalizeBreaks(String(value || '')
    .split(';')
    .filter((part) => part.trim() && !UNSAFE_VALUE.test(part) && !/position\s*:\s*fixed/i.test(part))
    .join(';'));

/* Разбор стиля браузером (CSSOM): он сам раскрывает экранирование и не
   пускает @import, и проверка идёт по тому, что браузер реально применит, а не
   по тексту, который можно записать десятком способов. */
const cleanWithCssom = (css) => {
    const sheet = new CSSStyleSheet();
    try { sheet.replaceSync(css); } catch { return ''; }
    const cleanRules = (list, owner) => {
        for (let i = list.length - 1; i >= 0; i -= 1) {
            const rule = list[i];
            const kind = rule.constructor?.name || '';
            if (kind === 'CSSImportRule' || kind === 'CSSFontFaceRule'
                || (typeof rule.selectorText === 'string' && /:host/i.test(rule.selectorText))) {
                try { owner.deleteRule(i); } catch { /* правило-ключ кадра не удаляется по индексу */ }
                continue;
            }
            if (rule.style) {
                for (let j = rule.style.length - 1; j >= 0; j -= 1) {
                    const property = rule.style[j];
                    const value = rule.style.getPropertyValue(property);
                    if (UNSAFE_VALUE.test(value)
                        || (property === 'position' && /fixed|var\(/i.test(value))) {
                        rule.style.removeProperty(property);
                    }
                }
            }
            if (rule.cssRules && typeof rule.deleteRule === 'function') cleanRules(rule.cssRules, rule);
        }
    };
    cleanRules(sheet.cssRules, sheet);
    return Array.from(sheet.cssRules).map((rule) => rule.cssText).join('\n');
};

/* Запасной путь без CSSOM (старый браузер, тесты в Node): грубее, но в ту же
   сторону — объявление с опасным значением удаляется целиком. */
const cleanWithRegex = (css) => css
    .replace(/[^;{}]*(?:image-set\(|\\|expression\(|url\(\s*(?!["']?blob:))[^;{}]*;?/gi, '')
    .replace(/[^{}]*:host[^{}]*\{[^}]*\}/gi, '');

/*
 * Стиль книги для теневого корня ридера.
 *
 *  - url(...) — через resolve: ресурсы книги становятся blob-адресами,
 *    внешние выбрасываются;
 *  - @import и @font-face — прочь: подгружать нечего, шрифт ридер задаёт сам;
 *  - селекторы html/body — на .lr-body: в теневом корне нет ни того, ни
 *    другого, а правила вида «body p { … }» книге нужны;
 *  - position: fixed — прочь: фиксированный блок книги встал бы поверх ридера;
 *  - :host — прочь: книга не должна стилизовать сам лист ридера;
 *  - разрывы страниц — в разрывы колонок.
 */
export const bookCss = (text, resolve = () => '') => {
    let css = String(text || '');
    css = css.replace(/\/\*[\s\S]*?\*\//g, '');
    css = css.replace(/@import[^;]*;/gi, '');
    css = css.replace(/@font-face\s*\{[^}]*\}/gi, '');
    css = css.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/gi, (_m, _q, ref) => {
        const url = resolve(ref);
        return url ? `url("${url}")` : 'none';
    });
    css = css.replace(/position\s*:\s*fixed/gi, 'position: static');
    // html/body как селектор типа: в начале, после «}», «,», «>» или пробела и
    // перед пробелом, «{», «,», «.», «#», «:», «[» или «>».
    css = css.replace(/(^|[\s,>}])(html|body)(?=[\s,{.#:[>~+])/gi, '$1.lr-body');
    css = normalizeBreaks(css);
    const cssom = typeof CSSStyleSheet === 'function' && typeof CSSStyleSheet.prototype.replaceSync === 'function';
    return cssom ? cleanWithCssom(css) : cleanWithRegex(css);
};

/* Буквица — только у абзаца, который начинается с буквы (можно после кавычки
   или скобки) и тянется хотя бы на пару строк: у тире диалога, у числа или у
   короткой строки опущенная заглавная выглядит опечаткой вёрстки. */
export const dropCapText = (text) => {
    const value = String(text || '').trim();
    return value.length >= 90 && /^[«"„“(]?\p{L}/u.test(value);
};

/* Строка «при главе», а не текст: подзаголовок, эпиграф, подпись, дата.
   Буквицу такой строке не ставим — ищем первый абзац самого текста. */
export const isAsideParagraph = ({ className = '', parentClass = '', align = '', style = '' } = {}) => (
    /center|right|epigraph|motto|subtitle|author|sign|date|cite|annotation|poem|verse|stanza/i
        .test(`${className} ${parentClass}`)
    || /^(center|right)$/i.test(String(align).trim())
    || /text-align\s*:\s*(center|right|end)/i.test(String(style))
);

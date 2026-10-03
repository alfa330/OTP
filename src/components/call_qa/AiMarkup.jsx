import React, { useMemo } from 'react';
import DOMPurify from 'dompurify';
import '../wiki/wiki-theme.css';
import '../wiki/wiki-blocks.css';
import './ai-markup.css';

/* Разметка ответа ИИ в «ИИ-оценке» — сводка дня и ответы чата.
 *
 * Модель пишет HTML с теми же блоками, что статьи вики (вводка, плашка,
 * карточки, показатели, шаги, чипы, галочки — wiki/ai/markup.py), сервер
 * чистит его и чинит блоки (call_qa/digest/render.py), а метки [[#12]]
 * превращает в span[data-qa-ref="вид:id"]. Здесь — второй рубеж санитайзера
 * (ответ внешней модели — наименее доверенный текст в системе, даже после
 * сервера) и превращение меток в кнопки, открывающие разговор.
 *
 * Список атрибутов — ровно те, что пишет сервер: data-* блоков вики (тот же
 * набор, что SANITIZE_OPTIONS витрины вики) и две метки ссылки. Разойдись он с
 * сервером — блок дойдёт до экрана безымянным div'ом: без фона и без колонок,
 * и ни одной ошибки нигде. Паритет сторожит tests/test_ai_qa_digest.py.
 */

export const MARKUP_TAGS = [
    'p', 'br', 'hr', 'h3', 'h4', 'strong', 'b', 'em', 'i', 'u', 's', 'mark', 'code',
    'ul', 'ol', 'li', 'blockquote', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'div', 'span',
];

export const MARKUP_ATTRS = [
    'data-wiki-block', 'data-tone', 'data-cols', 'data-numbered', 'data-variant',
    'data-qa-ref', 'data-qa-kind', 'title', 'colspan', 'rowspan',
];

// Вид субъекта — как его пишет сервер (config.SUBJECT_*): буквы, цифры и «_».
// Цифра в имени есть: у заявки Chat2Desk вид «c2d_snapshot», и без неё ссылки на
// переписку СЗоВ оставались голым текстом.
const REF_RE = /^([a-z][a-z0-9_]*):(\d+)$/;

/** Ключ ссылки «вид:id» → {subject, id}; чужое — null. */
export const parseRef = (value) => {
    const match = REF_RE.exec(String(value || ''));
    return match ? { subject: match[1], id: Number(match[2]) } : null;
};

/* Очищенный HTML → тот же HTML, где ссылки — настоящие кнопки. Кнопка, а не
   span с обработчиком: её достаёт Tab, нажимает Enter и пробел, и экранный
   диктор называет её кнопкой. Подпись и подсказку собрал сервер — здесь они
   только переносятся текстом, мимо разметки. */
export const prepareMarkup = (html) => {
    // Без DOM (серверная отрисовка в тестах) у DOMPurify нет sanitize — и
    // показывать чужой HTML без санитайзера нельзя вовсе: тогда пусто.
    if (typeof DOMPurify?.sanitize !== 'function') return '';
    const clean = DOMPurify.sanitize(String(html || ''), {
        ALLOWED_TAGS: MARKUP_TAGS, ALLOWED_ATTR: MARKUP_ATTRS, ALLOW_DATA_ATTR: false,
    });
    if (!clean || clean.indexOf('data-qa-ref') === -1 || typeof DOMParser === 'undefined') return clean;
    const parsed = new DOMParser().parseFromString(clean, 'text/html');
    parsed.body.querySelectorAll('span[data-qa-ref]').forEach((span) => {
        const ref = span.getAttribute('data-qa-ref');
        if (!parseRef(ref)) { span.replaceWith(parsed.createTextNode(span.textContent || '')); return; }
        const button = parsed.createElement('button');
        button.type = 'button';
        button.className = 'qa-ref';
        button.setAttribute('data-qa-ref', ref);
        button.setAttribute('data-qa-kind', span.getAttribute('data-qa-kind') === 'chat' ? 'chat' : 'call');
        const title = span.getAttribute('title') || '';
        if (title) {
            button.title = `${title} — открыть`;
            button.setAttribute('aria-label', `Открыть: ${title}`);
        }
        button.textContent = span.textContent || '';
        span.replaceWith(button);
    });
    return parsed.body.innerHTML;
};

export default function AiMarkup({ html, onOpenRef, variant = 'page', className = '' }) {
    const safe = useMemo(() => prepareMarkup(html), [html]);
    if (!safe) return null;
    const onClick = (event) => {
        const target = event.target?.closest?.('[data-qa-ref]');
        if (!target || !onOpenRef) return;
        const ref = parseRef(target.getAttribute('data-qa-ref'));
        if (!ref) return;
        event.preventDefault();
        onOpenRef(ref, target);
    };
    return (
        /* wiki-scope — палитра и тёмный слой вики; qa-scope — метка для
           ai-markup.css: на телефоне слой вики (wiki-mobile.css) красит корень
           .wiki-scope серым полотном и ставит тексту кегль статьи, а у сводки
           свой масштаб раздела. */
        <div className={`wiki-scope qa-scope ${className}`}>
            <div
                className={`wiki-prose qa-markup ${variant === 'chat' ? 'qa-markup--chat' : ''}`}
                onClick={onClick}
                /* Строка прошла DOMPurify выше (prepareMarkup), а до того —
                   серверный санитайзер: вставлять её иначе нельзя. */
                dangerouslySetInnerHTML={{ __html: safe }}
            />
        </div>
    );
}

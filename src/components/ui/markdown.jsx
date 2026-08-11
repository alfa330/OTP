import React, { useMemo } from 'react';
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import './markdown.css';

/* Разметка ответа ИИ: markdown → HTML → санитайзер → разметка в пузыре чата.
 *
 * Зачем полноценный рендер, а не «сгладить звёздочки». Таблица — главный формат
 * справочных данных вики: город, цена, срок, парк, комиссия. В корпусе их 63, и
 * помощник неизбежно отвечает такими же. Плоский текст разрушает таблицу ровно
 * там, где она нужнее всего: значения перестают соотноситься с колонками, и
 * оператор читает «Астана 5% Алматы 7%» вместо сетки.
 *
 * Санитайзер обязателен, и это не формальность: HTML сюда приходит из ответа
 * ВНЕШНЕЙ модели, то есть из наименее доверенного источника в системе. Список
 * тегов узкий и закрытый — ни картинок, ни iframe, ни style. DOMPurify в проекте
 * уже используется для тела статей (WikiArticle), берём тот же инструмент.
 *
 * Ссылки открываются в новой вкладке с rel="noopener": помощник цитирует статьи
 * с внешними ссылками на 2gis и таблицы Google, и уводить оператора из чата,
 * потеряв переписку, нельзя.
 */

marked.setOptions({
    gfm: true,        // таблицы, зачёркивание, автоссылки
    breaks: true,     // перевод строки в ответе — это перевод строки
});

const ALLOWED_TAGS = [
    'p', 'br', 'hr', 'strong', 'em', 'del', 'code', 'pre', 'blockquote',
    'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
];

const ALLOWED_ATTR = ['href', 'title', 'colspan', 'rowspan', 'align'];

/** HTML ответа: разобрать markdown и вычистить. Пустая строка при любой ошибке. */
export const renderMarkdown = (text) => {
    const source = String(text || '');
    if (!source.trim()) return '';
    let html;
    try {
        html = marked.parse(source);
    } catch {
        // Битую разметку не показываем сырой: экранируем и отдаём как абзац.
        const escaped = source
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        html = `<p>${escaped}</p>`;
    }
    const clean = DOMPurify.sanitize(html, {
        ALLOWED_TAGS,
        ALLOWED_ATTR,
        ALLOW_DATA_ATTR: false,
    });
    // target/rel навешиваются ПОСЛЕ очистки, здесь, а не обработчиком клика:
    // клик ловит только левую кнопку, а ссылку открывают и средней, и с
    // клавиатуры — тогда оператор уходил бы из чата, теряя переписку. Значения
    // ставим свои, поэтому санитайзеру они уже не подконтрольны и не нужны.
    const parsed = new DOMParser().parseFromString(clean, 'text/html');
    parsed.body.querySelectorAll('a[href]').forEach((link) => {
        link.setAttribute('target', '_blank');
        link.setAttribute('rel', 'noopener noreferrer');
    });
    return parsed.body.innerHTML;
};

/**
 * Разметка ответа помощника.
 *
 * Таблица прокручивается внутри своего контейнера, а не растягивает пузырь:
 * ширина пузыря ограничена, и без этого длинная строка ломала бы вёрстку всей
 * ленты.
 */
export default function Markdown({ text, className = '' }) {
    const html = useMemo(() => renderMarkdown(text), [text]);
    if (!html) return null;
    return (
        <div
            className={`ai-md text-[13.5px] leading-snug ${className}`}
            // Источник — ответ внешней модели, поэтому строка прошла DOMPurify
            // выше; вставлять её без санитайзера нельзя ни при каких условиях.
            dangerouslySetInnerHTML={{ __html: html }}
        />
    );
}

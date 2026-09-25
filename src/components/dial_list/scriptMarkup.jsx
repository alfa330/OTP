import React from 'react';

/*
 * Разметка скрипта обзвона — общая для сайта и iCORE Phone.
 *
 * Правила нарочно крошечные: их читает и телефон (свой отрисовщик на C++),
 * и предпросмотр здесь, и они обязаны совпадать буква в букву. Всё, что не
 * попало в список, — обычный текст. Никакого HTML и ссылок.
 *
 * Строчные (по началу строки):
 *   "# "   заголовок          "## "  подзаголовок
 *   "- "   пункт списка       "> "   примечание (серая плашка)
 *   "---"  разделитель        пустая строка — новый абзац
 *   остальное — абзац; соседние непустые строки склеиваются пробелом.
 *   Соседние пункты складываются в один список, соседние примечания — в одну
 *   плашку (в телефоне так же).
 *
 * Внутри строки (в любом месте):
 *   **жирный**   ==выделение== (жёлтый фон, тёмный текст).
 *   Внутри выделения допускается жирный; внутри жирного всё буквально.
 */

// Префиксы, которые редактор снимает перед тем, как поставить другой.
export const LINE_PREFIXES = ['## ', '# ', '- ', '> '];

/** Разбор строки на отрезки: text / bold / mark (у mark — свои children). */
export const parseInline = (text, allowMark = true) => {
    const src = String(text ?? '');
    const re = /\*\*(.+?)\*\*|==(.+?)==/g;
    const out = [];
    let last = 0;
    let m;
    while ((m = re.exec(src))) {
        if (m[2] !== undefined && !allowMark) continue;
        if (m.index > last) out.push({ type: 'text', text: src.slice(last, m.index) });
        if (m[1] !== undefined) out.push({ type: 'bold', text: m[1] });
        else out.push({ type: 'mark', children: parseInline(m[2], false) });
        last = m.index + m[0].length;
    }
    if (last < src.length) out.push({ type: 'text', text: src.slice(last) });
    return out;
};

/**
 * Разбор текста на блоки:
 *   { type: 'h1' | 'h2' | 'p' | 'note', text }
 *   { type: 'ul', items: [text] }
 *   { type: 'hr' }
 */
export const parseScript = (text) => {
    const lines = String(text ?? '').replace(/\r\n?/g, '\n').split('\n');
    const blocks = [];
    let cur = null;

    const flush = () => {
        if (!cur) return;
        if (cur.type === 'ul') blocks.push({ type: 'ul', items: cur.items });
        else blocks.push({ type: cur.type, text: cur.lines.join(' ') });
        cur = null;
    };
    const append = (type, value) => {
        if (type === 'ul') {
            if (!cur || cur.type !== 'ul') { flush(); cur = { type: 'ul', items: [] }; }
            cur.items.push(value);
            return;
        }
        if (!cur || cur.type !== type) { flush(); cur = { type, lines: [] }; }
        cur.lines.push(value);
    };

    for (const raw of lines) {
        const line = raw.replace(/\s+$/, '');
        if (!line.trim()) { flush(); continue; }
        if (line.trim() === '---') { flush(); blocks.push({ type: 'hr' }); continue; }
        if (line.startsWith('## ')) { flush(); blocks.push({ type: 'h2', text: line.slice(3).trim() }); continue; }
        if (line.startsWith('# ')) { flush(); blocks.push({ type: 'h1', text: line.slice(2).trim() }); continue; }
        if (line.startsWith('- ')) { append('ul', line.slice(2).trim()); continue; }
        if (line.startsWith('> ')) { append('note', line.slice(2).trim()); continue; }
        append('p', line.trim());
    }
    flush();
    return blocks;
};

const Inline = ({ spans }) => spans.map((s, i) => {
    if (s.type === 'bold') return <strong key={i} className="font-semibold text-slate-900">{s.text}</strong>;
    if (s.type === 'mark') {
        return (
            <mark key={i} className="rounded-md bg-amber-200/80 px-1 py-0.5 text-slate-900">
                <Inline spans={s.children} />
            </mark>
        );
    }
    return <React.Fragment key={i}>{s.text}</React.Fragment>;
});

/** Так скрипт увидит оператор. Пустой текст — ничего не рисует. */
export const ScriptView = ({ text, className = '' }) => {
    const blocks = parseScript(text);
    if (!blocks.length) return null;
    return (
        <div className={`space-y-2.5 ${className}`}>
            {blocks.map((b, i) => {
                switch (b.type) {
                    case 'h1':
                        return <h3 key={i} className={`text-[16px] font-semibold leading-snug text-slate-900 ${i ? 'pt-1.5' : ''}`}><Inline spans={parseInline(b.text)} /></h3>;
                    case 'h2':
                        return <h4 key={i} className={`text-[14px] font-semibold leading-snug text-slate-800 ${i ? 'pt-1' : ''}`}><Inline spans={parseInline(b.text)} /></h4>;
                    case 'ul':
                        return (
                            <ul key={i} className="list-disc space-y-1 pl-5 text-[14px] leading-relaxed text-slate-700">
                                {b.items.map((item, j) => <li key={j}><Inline spans={parseInline(item)} /></li>)}
                            </ul>
                        );
                    case 'note':
                        return (
                            <div key={i} className="rounded-xl bg-slate-100 px-3.5 py-2.5 text-[13px] leading-relaxed text-slate-600">
                                <Inline spans={parseInline(b.text)} />
                            </div>
                        );
                    case 'hr':
                        return <div key={i} role="separator" className="h-px bg-slate-200" />;
                    default:
                        return <p key={i} className="text-[14px] leading-relaxed text-slate-700"><Inline spans={parseInline(b.text)} /></p>;
                }
            })}
        </div>
    );
};

export default ScriptView;

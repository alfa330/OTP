import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import DOMPurify from 'dompurify';
import {
    ArrowLeft, Clock, Eye, Link2, List, Loader2, Star, User,
} from 'lucide-react';
import { iosCard, iosGroupLabel, iosBtnSecondary, IosBadge } from '../ui/ios';
import { scrollToElement } from './scrollContainer';
import WikiAckPanel from './WikiAckPanel';

/* Страница статьи.
 *
 * Оглавление строится из готового DOM после вставки контента, а не парсингом
 * строки: заголовкам всё равно нужно проставить id, чтобы по ним можно было
 * прокрутить, и делать это дважды бессмысленно.
 *
 * Прокрутка — всегда через scrollContainer.js. В исходной вике все переходы
 * шли через window.scrollTo, который в нашем каркасе не делает ничего:
 * скроллится .main-content, а не окно.
 *
 * Санитизация на клиенте — второй рубеж. Первый (серверный) появится вместе с
 * редактором на этапе 4; сейчас содержимое создаётся только миграцией.
 */

const STATUS_LABELS = {
    draft: 'Черновик',
    on_approval: 'На согласовании',
    published: 'Опубликована',
    requires_verification: 'Требует проверки',
    archived: 'В архиве',
    expired: 'Устарела',
};

const STATUS_TONES = {
    draft: 'slate',
    on_approval: 'amber',
    published: 'green',
    requires_verification: 'amber',
    archived: 'slate',
    expired: 'red',
};

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const fmtDate = (iso) => (iso
    ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' })
    : '—');

/* Разрешаем ровно те теги и атрибуты, что реально встречаются в контенте вики
   (посчитано по дампу прода: 26 тегов, 11 data-атрибутов). Без явного списка
   DOMPurify вырезал бы data-* и обесцветил 251 выделение, а 35 раскрывающихся
   блоков превратились бы в простые абзацы. */
const SANITIZE_OPTIONS = {
    ADD_TAGS: ['details', 'summary', 'mark', 'colgroup', 'col'],
    ADD_ATTR: [
        'data-color', 'data-title', 'data-default-open', 'data-allow-multiple',
        'data-required-for-ack', 'data-wiki-collapsible', 'data-wiki-collapsible-group',
        'data-id', 'data-icon', 'data-size', 'data-layout', 'open', 'colspan', 'rowspan',
    ],
};

export default function WikiArticle({ base, headers, slug, onBack, showToast }) {
    const [article, setArticle] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [toc, setToc] = useState([]);
    const [activeId, setActiveId] = useState('');
    const bodyRef = useRef(null);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError('');
        axios.get(`${base}/articles/${encodeURIComponent(slug)}`, { headers })
            .then((r) => { if (!cancelled) setArticle(r.data); })
            .catch((e) => { if (!cancelled) setError(errText(e, 'Статья не найдена')); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [base, headers, slug]);

    const safeHtml = useMemo(
        () => (article?.content ? DOMPurify.sanitize(article.content, SANITIZE_OPTIONS) : ''),
        [article?.content],
    );

    // Оглавление собираем после того, как контент оказался в DOM.
    useEffect(() => {
        if (!safeHtml || !bodyRef.current) { setToc([]); return; }
        const nodes = bodyRef.current.querySelectorAll('h1, h2, h3');
        const entries = [];
        nodes.forEach((node, index) => {
            const text = (node.textContent || '').trim();
            if (!text) return;
            if (!node.id) node.id = `wiki-h-${index}`;
            entries.push({ id: node.id, text, level: Number(node.tagName.slice(1)) });
        });
        setToc(entries);
        setActiveId(entries[0]?.id || '');
    }, [safeHtml]);

    // Подсветка активного пункта оглавления. IntersectionObserver вместо
    // обработчика прокрутки: он не будит React на каждый кадр.
    useEffect(() => {
        if (!toc.length || !bodyRef.current) return undefined;
        const observer = new IntersectionObserver(
            (records) => {
                const visible = records
                    .filter((r) => r.isIntersecting)
                    .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
                if (visible[0]?.target?.id) setActiveId(visible[0].target.id);
            },
            { rootMargin: '-88px 0px -70% 0px', threshold: 0 },
        );
        toc.forEach(({ id }) => {
            const node = document.getElementById(id);
            if (node) observer.observe(node);
        });
        return () => observer.disconnect();
    }, [toc]);

    const toggleFavorite = () => {
        if (!article) return;
        axios.post(`${base}/articles/${article.id}/favorite`, {}, { headers })
            .then(() => showToast?.('Добавлено в избранное', 'success'))
            .catch((e) => showToast?.(errText(e, 'Не удалось'), 'error'));
    };

    if (loading) {
        return (
            <div className={`${iosCard} flex items-center justify-center gap-2 py-16 text-slate-400`}>
                <Loader2 size={18} className="animate-spin" />
                <span className="text-[13px]">Открываем статью…</span>
            </div>
        );
    }

    if (error) {
        return (
            <div className={`${iosCard} px-6 py-14 text-center`}>
                <div className="text-[15px] font-semibold text-slate-900">{error}</div>
                <p className="mx-auto mt-1 max-w-sm text-[13px] leading-relaxed text-slate-500">
                    Возможно, статья удалена или у вас нет к ней доступа.
                </p>
                <button type="button" className={`${iosBtnSecondary} mt-4`} onClick={onBack}>
                    <ArrowLeft size={14} /> К списку
                </button>
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <button type="button" className={iosBtnSecondary} onClick={onBack}>
                <ArrowLeft size={14} /> К списку
            </button>

            {/* Панель ознакомления идёт ПЕРЕД статьёй: требование надо видеть
                до чтения, а не найти под текстом. */}
            <WikiAckPanel
                base={base}
                headers={headers}
                articleId={article.id}
                bodyRef={bodyRef}
                showToast={showToast}
            />

            <article className={`${iosCard} overflow-hidden`}>
                <header className="border-b border-slate-100 px-5 py-4 sm:px-7 sm:py-6">
                    <div className="mb-2 flex flex-wrap items-center gap-1.5">
                        <IosBadge tone={STATUS_TONES[article.status] || 'slate'}>
                            {STATUS_LABELS[article.status] || article.status}
                        </IosBadge>
                        {article.visibility_mode === 'restricted' && (
                            <IosBadge tone="amber">Только по списку</IosBadge>
                        )}
                        {article.strict_mode && <IosBadge tone="red">Строгий режим</IosBadge>}
                        {article.tags?.map((tag) => (
                            <IosBadge key={tag} tone="slate">{tag}</IosBadge>
                        ))}
                    </div>

                    <h1 className="text-[24px] font-semibold leading-tight tracking-[-0.015em] text-slate-900 sm:text-[28px]">
                        {article.title}
                    </h1>
                    {article.summary && (
                        <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-slate-500">
                            {article.summary}
                        </p>
                    )}

                    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-400">
                        {article.author_name && (
                            <span className="flex items-center gap-1"><User size={12} /> {article.author_name}</span>
                        )}
                        <span className="flex items-center gap-1">
                            <Clock size={12} /> {fmtDate(article.updated_at)}
                        </span>
                        <span className="flex items-center gap-1 tabular-nums">
                            <Eye size={12} /> {article.views}
                        </span>
                        <button
                            type="button"
                            onClick={toggleFavorite}
                            className="flex items-center gap-1 rounded-md px-1 transition hover:text-amber-600"
                        >
                            <Star size={12} /> В избранное
                        </button>
                    </div>
                </header>

                <div className="flex flex-col gap-6 px-5 py-5 sm:px-7 sm:py-7 lg:flex-row-reverse">
                    {toc.length > 1 && (
                        <nav className="lg:w-56 lg:shrink-0">
                            <div className="lg:sticky lg:top-4">
                                <div className={`${iosGroupLabel} mb-1.5 flex items-center gap-1.5`}>
                                    <List size={12} /> Содержание
                                </div>
                                <ul className="space-y-0.5 border-l border-slate-200 pl-3">
                                    {toc.map((item) => (
                                        <li key={item.id}>
                                            <button
                                                type="button"
                                                onClick={() => scrollToElement(document.getElementById(item.id))}
                                                className={`block w-full text-left text-[12.5px] leading-snug transition ${
                                                    activeId === item.id
                                                        ? 'font-medium text-indigo-600'
                                                        : 'text-slate-500 hover:text-slate-800'
                                                }`}
                                                style={{ paddingLeft: `${(item.level - 1) * 10}px` }}
                                            >
                                                {item.text}
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        </nav>
                    )}

                    <div
                        ref={bodyRef}
                        className="wiki-prose min-w-0 flex-1"
                        dangerouslySetInnerHTML={{ __html: safeHtml }}
                    />
                </div>

                {article.backlinks?.length > 0 && (
                    <footer className="border-t border-slate-100 px-5 py-4 sm:px-7">
                        <div className={`${iosGroupLabel} mb-2 flex items-center gap-1.5`}>
                            <Link2 size={12} /> Сюда ссылаются
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                            {article.backlinks.map((link) => (
                                <IosBadge key={link.id} tone="blue">{link.title}</IosBadge>
                            ))}
                        </div>
                    </footer>
                )}
            </article>

            {article.why && (
                <p className="px-1 text-[11.5px] text-slate-400">
                    Доступ: {article.why}
                </p>
            )}
        </div>
    );
}

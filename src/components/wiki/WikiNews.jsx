import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import Link from '@tiptap/extension-link';
import Highlight from '@tiptap/extension-highlight';
import {
    Bold, Check, Italic, Link2, List, ListOrdered, Loader2, Megaphone, Plus,
    Underline as UnderlineIcon, Users, X,
} from 'lucide-react';
import {
    iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
    IosBadge, IosMenu, IosModal, IosSegmented, IosToggle,
} from '../ui/ios';
import { delayLabel, publishedLabel, roleTitle } from '../news/newsShared';
import '../news/news-modal.css';

/* Вкладка «Новости» — там, где новость ПИШУТ.
 *
 * Показывать её тому, кто новость получает, незачем: у него она приходит окном
 * поверх портала (src/components/news/NewsOfDayModal.jsx), и второй экран с тем
 * же текстом был бы дублем. Поэтому вкладка живёт внутри «Вики» и открывается
 * тем, у кого есть потолок выдачи, — супервайзеру и выше.
 *
 * ПОЧЕМУ ДАННЫЕ НЕ ИЗ /api/wiki. Роуты вики стоят за тумблером отдела и
 * QR-подтверждением сессии, а новость обязана доехать и до тех, кто через эти
 * двери не проходит. Поэтому у раздела свой /api/news — вкладка лишь его
 * витрина, а не его граница.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const BUCKETS = [
    { value: 'published', label: 'Опубликованные' },
    { value: 'draft', label: 'Черновики' },
    { value: 'archived', label: 'Архив' },
];

/* Виды адресата в том порядке, в каком их выбирают: сначала «кому вообще»
   (отдел, направление, группа), потом поимённо. Должность стоит последней и
   доступна только тому, у кого нет границы отдела: правило на роль адресует
   людей по всей компании (см. wiki/access.py: COMPANY_WIDE_SUBJECTS). */
const SUBJECT_TABS = [
    { key: 'department', label: 'Отделы' },
    { key: 'direction', label: 'Направления' },
    { key: 'group', label: 'Группы' },
    { key: 'user', label: 'Люди' },
    { key: 'otp_role', label: 'Должности' },
];

const SUBJECT_TITLES = {
    department: 'отдел',
    direction: 'направление',
    group: 'группа',
    user: 'сотрудник',
    otp_role: 'должность',
};

const ruleKey = (rule) => `${rule.subject_type}:${rule.subject_id ?? rule.subject_role}`;

/* Заготовки задержки. Числом в поле её тоже задают, но человек, ставящий
   объявление на смену, думает не в секундах, а «быстро / нормально / вдумчиво». */
const DELAY_PRESETS = [0, 10, 30, 60];

// ─────────────────────────────────────────────────────────────────────────────
// Выбор адресатов
// ─────────────────────────────────────────────────────────────────────────────

function AudiencePicker({ open, onClose, access, value, onChange }) {
    const [tab, setTab] = useState('department');
    const [query, setQuery] = useState('');

    const tabs = useMemo(() => SUBJECT_TABS.filter((item) => {
        if (item.key === 'user') return (access?.people || []).length > 0;
        if (item.key === 'otp_role') return (access?.roles || []).length > 0;
        return (access?.subjects?.[item.key] || []).length > 0;
    }), [access]);

    useEffect(() => {
        if (tabs.length && !tabs.some((t) => t.key === tab)) setTab(tabs[0].key);
    }, [tabs, tab]);

    const options = useMemo(() => {
        const text = query.trim().toLowerCase();
        const match = (name) => !text || String(name || '').toLowerCase().includes(text);
        if (tab === 'user') {
            return (access?.people || [])
                .filter((person) => match(person.name))
                .map((person) => ({
                    key: `user:${person.id}`,
                    rule: { subject_type: 'user', subject_id: person.id, subject_role: null },
                    title: person.name,
                    hint: [roleTitle(person.role), person.department_name].filter(Boolean).join(' · '),
                }));
        }
        if (tab === 'otp_role') {
            return (access?.roles || [])
                .filter((role) => match(roleTitle(role.code) || role.code))
                .map((role) => ({
                    key: `otp_role:${role.code}`,
                    rule: { subject_type: 'otp_role', subject_id: null, subject_role: role.code },
                    title: roleTitle(role.code) || role.code,
                    hint: 'все сотрудники этой должности',
                }));
        }
        return (access?.subjects?.[tab] || [])
            .filter((item) => match(item.name))
            .map((item) => ({
                key: `${tab}:${item.id}`,
                rule: { subject_type: tab, subject_id: item.id, subject_role: null },
                title: item.name,
                hint: SUBJECT_TITLES[tab],
            }));
    }, [access, query, tab]);

    const selected = useMemo(() => new Set((value || []).map(ruleKey)), [value]);

    const toggle = (option) => {
        if (selected.has(option.key)) {
            onChange((value || []).filter((rule) => ruleKey(rule) !== option.key));
        } else {
            onChange([...(value || []), option.rule]);
        }
    };

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Кому показать новость"
            subtitle={access?.bounded
                ? 'Список сужен вашим отделом и вашей должностью'
                : 'Список сужен вашей должностью'}
            maxWidth="max-w-xl"
            footer={(
                <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>
            )}
        >
            {tabs.length > 1 && (
                <IosSegmented
                    value={tab}
                    options={tabs.map((item) => ({ value: item.key, label: item.label }))}
                    onChange={setTab}
                    stretch
                />
            )}
            <input
                className={`${iosInput} mt-3`}
                placeholder="Поиск"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
            />
            <div className="mt-3 space-y-1">
                {options.length === 0 && (
                    <p className="px-1 py-6 text-center text-[13px] text-slate-400">Ничего не нашлось</p>
                )}
                {options.map((option) => {
                    const active = selected.has(option.key);
                    return (
                        <button
                            key={option.key}
                            type="button"
                            onClick={() => toggle(option)}
                            className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition active:scale-[0.99] ${
                                active ? 'bg-indigo-50' : 'hover:bg-slate-50'}`}
                        >
                            <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-md ring-1 ${
                                active ? 'bg-indigo-600 text-white ring-indigo-600' : 'ring-slate-300'}`}>
                                {active && <Check className="h-3.5 w-3.5" aria-hidden="true" />}
                            </span>
                            <span className="min-w-0 flex-1">
                                <span className="block truncate text-[14px] text-slate-900">{option.title}</span>
                                {option.hint && (
                                    <span className="block truncate text-[12px] text-slate-400">{option.hint}</span>
                                )}
                            </span>
                        </button>
                    );
                })}
            </div>
        </IosModal>
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Форма новости
// ─────────────────────────────────────────────────────────────────────────────

function NewsForm({ open, post, access, onClose, onSave, saving }) {
    const [title, setTitle] = useState('');
    const [mandatory, setMandatory] = useState(true);
    const [delay, setDelay] = useState(access?.default_confirm_delay_seconds ?? 10);
    const [expires, setExpires] = useState('');
    const [audience, setAudience] = useState([]);
    const [pickerOpen, setPickerOpen] = useState(false);
    const [error, setError] = useState('');

    const editor = useEditor({
        extensions: [
            // Заголовки до третьего уровня: объявление на экран — не статья,
            // и пятый уровень вложенности в нём означал бы, что это не новость.
            StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
            Underline,
            Link.configure({ openOnClick: false, autolink: true }),
            Highlight,
        ],
        content: '',
        editorProps: {
            attributes: {
                class: 'news-body min-h-[180px] focus:outline-none',
            },
        },
    }, []);

    /* Форма заполняется при ОТКРЫТИИ, а не на каждый рендер: правка в поле не
       должна затираться тем же объектом post, приехавшим из списка заново. */
    useEffect(() => {
        if (!open) return;
        setTitle(post?.title || '');
        setMandatory(post ? !!post.is_mandatory : true);
        setDelay(post ? post.confirm_delay_seconds : (access?.default_confirm_delay_seconds ?? 10));
        setExpires(post?.expires_at ? String(post.expires_at).slice(0, 16) : '');
        setAudience((post?.audience || []).map((rule) => ({
            subject_type: rule.subject_type,
            subject_id: rule.subject_id,
            subject_role: rule.subject_role,
            subject_name: rule.subject_name,
        })));
        setError('');
        editor?.commands.setContent(post?.body || '');
    }, [open, post, access, editor]);

    const audienceLabel = useCallback((rule) => {
        if (rule.subject_type === 'otp_role') {
            return roleTitle(rule.subject_role) || rule.subject_role;
        }
        if (rule.subject_name) return rule.subject_name;
        const pool = rule.subject_type === 'user'
            ? (access?.people || [])
            : (access?.subjects?.[rule.subject_type] || []);
        return pool.find((item) => item.id === rule.subject_id)?.name || '—';
    }, [access]);

    const submit = (publish) => {
        const text = title.trim();
        if (!text) { setError('Укажите заголовок'); return; }
        if (!audience.length) { setError('Укажите, кому адресована новость'); return; }
        const body = editor?.getHTML() || '';
        // Пустой абзац TipTap отдаёт как <p></p> — для проверки «текст есть»
        // это ничем не отличается от пустого поля.
        if (!editor?.getText().trim()) { setError('Напишите текст новости'); return; }
        setError('');
        onSave({
            title: text,
            body,
            is_mandatory: mandatory,
            confirm_delay_seconds: Number(delay) || 0,
            expires_at: expires || null,
            audience: audience.map((rule) => ({
                subject_type: rule.subject_type,
                subject_id: rule.subject_id,
                subject_role: rule.subject_role,
            })),
            publish,
        });
    };

    const toolbarButton = (active, onClick, Icon, label) => (
        <button
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={onClick}
            aria-label={label}
            title={label}
            className={`grid h-8 w-8 place-items-center rounded-lg transition active:scale-95 ${
                active ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}
        >
            <Icon className="h-4 w-4" aria-hidden="true" />
        </button>
    );

    return (
        <>
            <IosModal
                open={open}
                onClose={onClose}
                title={post ? 'Новость' : 'Новая новость'}
                /* У опубликованной говорим главное, а не «опубликована» —
                   это и так видно по вкладке. Главное здесь то, чего не видно:
                   правка не спрашивает подтверждения заново. */
                subtitle={post?.status === 'published'
                    ? 'Правка не сбрасывает подтверждения — чтобы спросить заново, опубликуйте новую'
                    : null}
                maxWidth="max-w-2xl"
                footer={(
                    <>
                        {error && <span className="mr-auto text-[12px] text-rose-600">{error}</span>}
                        <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                        {post?.status !== 'published' && (
                            <button type="button" className={iosBtnSecondary}
                                    disabled={saving} onClick={() => submit(false)}>
                                В черновики
                            </button>
                        )}
                        <button type="button" className={iosBtnPrimary}
                                disabled={saving} onClick={() => submit(true)}>
                            {saving && <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />}
                            {post?.status === 'published' ? 'Сохранить' : 'Опубликовать'}
                        </button>
                    </>
                )}
            >
                <label className={iosGroupLabel}>Заголовок</label>
                <input
                    className={iosInput}
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="Например: Акция «Приведи друга» — во всех таксопарках"
                    maxLength={255}
                />

                <label className={`${iosGroupLabel} mt-4`}>Текст</label>
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="flex flex-wrap items-center gap-0.5 border-b border-slate-100 px-2 py-1.5">
                        {toolbarButton(editor?.isActive('bold'),
                            () => editor?.chain().focus().toggleBold().run(), Bold, 'Полужирный')}
                        {toolbarButton(editor?.isActive('italic'),
                            () => editor?.chain().focus().toggleItalic().run(), Italic, 'Курсив')}
                        {toolbarButton(editor?.isActive('underline'),
                            () => editor?.chain().focus().toggleUnderline().run(), UnderlineIcon, 'Подчёркнутый')}
                        <span className="mx-1 h-5 w-px bg-slate-200" />
                        {toolbarButton(editor?.isActive('bulletList'),
                            () => editor?.chain().focus().toggleBulletList().run(), List, 'Список')}
                        {toolbarButton(editor?.isActive('orderedList'),
                            () => editor?.chain().focus().toggleOrderedList().run(), ListOrdered, 'Нумерованный список')}
                        <span className="mx-1 h-5 w-px bg-slate-200" />
                        {toolbarButton(editor?.isActive('link'), () => {
                            const previous = editor?.getAttributes('link')?.href || '';
                            const href = window.prompt('Адрес ссылки', previous);
                            if (href === null) return;
                            if (!href) { editor?.chain().focus().unsetLink().run(); return; }
                            editor?.chain().focus().extendMarkRange('link')
                                .setLink({ href, target: '_blank' }).run();
                        }, Link2, 'Ссылка')}
                    </div>
                    <div className="px-3.5 py-3">
                        <EditorContent editor={editor} />
                    </div>
                </div>

                <label className={`${iosGroupLabel} mt-4`}>Кому</label>
                <div className={`${iosCard} p-3`}>
                    <div className="flex flex-wrap items-center gap-1.5">
                        {audience.map((rule) => (
                            <span key={ruleKey(rule)}
                                  className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 py-1 pl-2.5 pr-1 text-[12px] text-slate-700">
                                {audienceLabel(rule)}
                                <button
                                    type="button"
                                    onClick={() => setAudience(audience.filter(
                                        (item) => ruleKey(item) !== ruleKey(rule)))}
                                    className="grid h-4 w-4 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                                    aria-label={`Убрать ${audienceLabel(rule)}`}
                                >
                                    <X className="h-3 w-3" aria-hidden="true" />
                                </button>
                            </span>
                        ))}
                        <button
                            type="button"
                            onClick={() => setPickerOpen(true)}
                            className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-1 text-[12px] font-medium text-indigo-700 transition hover:bg-indigo-100 active:scale-[0.98]"
                        >
                            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                            Добавить
                        </button>
                    </div>
                    {/* Обычной подписью, а не подсказкой за «i»: это не
                        уточнение для любопытных, а граница, которая молча
                        сузит выбранное. Прочитать её надо ДО того, как автор
                        решит, что написал всему отделу. */}
                    <p className="mt-2 text-[12px] text-slate-400">
                        {audience.length
                            ? 'Из выбранного новость увидят только те, кто ниже вас по должности'
                            : 'Новость увидят только те, кто ниже вас по должности'}
                    </p>
                </div>

                <div className={`${iosCard} mt-4 divide-y divide-slate-100`}>
                    <div className="flex items-center justify-between gap-3 px-3.5 py-3">
                        <div className="min-w-0">
                            <p className="text-[14px] text-slate-900">Обязательно к прочтению</p>
                            <p className="text-[12px] text-slate-400">
                                {mandatory
                                    ? 'Окно нельзя закрыть, отметка попадёт в журнал'
                                    : 'Окно закрывается крестиком, журнал не ведётся'}
                            </p>
                        </div>
                        <IosToggle checked={mandatory} onChange={setMandatory} />
                    </div>
                    {/* Задержка нужна только обязательной: у необязательной
                        кнопки «Прочитал» нет вовсе, и поле рядом с ней было бы
                        настройкой того, чего не существует. */}
                    {mandatory && (
                        <div className="px-3.5 py-3">
                            <p className="text-[14px] text-slate-900">Задержка кнопки «Прочитал»</p>
                            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                {DELAY_PRESETS.map((preset) => (
                                    <button
                                        key={preset}
                                        type="button"
                                        onClick={() => setDelay(preset)}
                                        className={`rounded-full px-3 py-1 text-[12px] tabular-nums transition active:scale-[0.98] ${
                                            Number(delay) === preset
                                                ? 'bg-slate-900 text-white'
                                                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                                    >
                                        {preset === 0 ? 'сразу' : `${preset} с`}
                                    </button>
                                ))}
                                <input
                                    type="number"
                                    min={0}
                                    max={600}
                                    value={delay}
                                    /* Режем здесь же: сервер всё равно приведёт
                                       значение к потолку, и «1000» в поле рядом
                                       с подписью «загорится через 1000 с» было
                                       бы обещанием, которого он не выполнит. */
                                    onChange={(e) => setDelay(Math.max(0, Math.min(600,
                                        Number(e.target.value) || 0)))}
                                    className="w-20 rounded-xl bg-slate-100 px-3 py-1 text-[12px] tabular-nums text-slate-900 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                                    aria-label="Задержка в секундах"
                                />
                            </div>
                            <p className="mt-1.5 text-[12px] text-slate-400">{delayLabel(delay)}</p>
                        </div>
                    )}
                    <div className="flex items-center justify-between gap-3 px-3.5 py-3">
                        <div className="min-w-0">
                            <p className="text-[14px] text-slate-900">Показывать до</p>
                            <p className="text-[12px] text-slate-400">
                                Пусто — пока не подтвердят
                            </p>
                        </div>
                        <input
                            type="datetime-local"
                            value={expires}
                            onChange={(e) => setExpires(e.target.value)}
                            className="rounded-xl bg-slate-100 px-3 py-1.5 text-[13px] text-slate-900 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70"
                        />
                    </div>
                </div>
            </IosModal>

            <AudiencePicker
                open={pickerOpen}
                onClose={() => setPickerOpen(false)}
                access={access}
                value={audience}
                onChange={setAudience}
            />
        </>
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Журнал прочтений
// ─────────────────────────────────────────────────────────────────────────────

function NewsReport({ open, post, apiBaseUrl, headers, onClose }) {
    const [state, setState] = useState(null);
    const [loading, setLoading] = useState(false);
    const [onlyPending, setOnlyPending] = useState(false);

    useEffect(() => {
        if (!open || !post?.id) { setState(null); return; }
        setLoading(true);
        axios.get(`${apiBaseUrl}/api/news/posts/${post.id}/report`, { headers })
            .then((r) => setState(r.data))
            .catch(() => setState(null))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers, open, post?.id]);

    const rows = useMemo(() => {
        const items = state?.items || [];
        // «Только непрочитавшие» — про тех, от кого ещё ждут: человек, который
        // из адресатов выбыл, в этот список не попадает, дожимать его незачем.
        return onlyPending
            ? items.filter((row) => !row.confirmed_at && row.in_audience)
            : items;
    }, [state, onlyPending]);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Кто прочитал"
            subtitle={post?.title}
            maxWidth="max-w-xl"
            footer={<button type="button" className={iosBtnSecondary} onClick={onClose}>Закрыть</button>}
        >
            {loading && (
                <p className="py-8 text-center text-[13px] text-slate-400">
                    <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />Считаем
                </p>
            )}
            {!loading && state && (
                <>
                    <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                            <p className="text-[14px] text-slate-900 tabular-nums">
                                Подтвердили {state.confirmed} из {state.total}
                            </p>
                            {/* Подтвердившие, которых в адресатах уже нет,
                                в знаменатель не идут, но и не исчезают: журнал
                                читают при разборе, задним числом. */}
                            {state.confirmed_outside > 0 && (
                                <p className="text-[12px] text-slate-400 tabular-nums">
                                    и ещё {state.confirmed_outside} — из тех, кто больше не в адресатах
                                </p>
                            )}
                        </div>
                        <button
                            type="button"
                            onClick={() => setOnlyPending((value) => !value)}
                            className={`rounded-full px-3 py-1 text-[12px] transition active:scale-[0.98] ${
                                onlyPending ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600'}`}
                        >
                            Только непрочитавшие
                        </button>
                    </div>
                    <div className="mt-3 space-y-1">
                        {rows.length === 0 && (
                            <p className="py-6 text-center text-[13px] text-slate-400">
                                {onlyPending ? 'Прочитали все' : 'Адресатов нет'}
                            </p>
                        )}
                        {rows.map((row) => (
                            <div key={row.user_id}
                                 className="flex items-center justify-between gap-3 rounded-xl px-3 py-2 hover:bg-slate-50">
                                <div className="min-w-0">
                                    <p className="truncate text-[14px] text-slate-900">{row.name}</p>
                                    <p className="truncate text-[12px] text-slate-400">
                                        {[roleTitle(row.role), row.department_name,
                                          row.in_audience ? null : 'уже не в адресатах']
                                            .filter(Boolean).join(' · ')}
                                    </p>
                                </div>
                                {/* Обе стороны — серой подписью.
                                    Плашка на непрочитавшем выглядела разумно на
                                    одной строке и превращалась в стену цвета на
                                    восемнадцати: цвет, который стоит у половины
                                    списка, не значит ничего. Кто не прочитал,
                                    видно и так — они наверху (сортировка) и
                                    посчитаны в шапке. */}
                                <span className="shrink-0 text-[12px] tabular-nums text-slate-400">
                                    {row.confirmed_at
                                        ? publishedLabel(row.confirmed_at)
                                        : (row.shown_at ? 'открыл, не подтвердил' : 'не видел')}
                                </span>
                            </div>
                        ))}
                    </div>
                </>
            )}
        </IosModal>
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// Витрина
// ─────────────────────────────────────────────────────────────────────────────

export default function WikiNews({ apiBaseUrl, headers, showToast }) {
    const [bucket, setBucket] = useState('published');
    const [items, setItems] = useState([]);
    const [access, setAccess] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);
    const [formPost, setFormPost] = useState(undefined);   // undefined — закрыта
    const [reportPost, setReportPost] = useState(null);

    /* showToast приходит из App новой функцией на каждом её рендере. В
       зависимостях загрузчика это означало бы перезапрос списка на каждый чужой
       рендер — та же ловушка, что уже ловили в разделе «Опросы». */
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const load = useCallback(() => {
        setLoading(true);
        return axios.get(`${apiBaseUrl}/api/news/posts`, { headers, params: { status: bucket } })
            .then((r) => { setItems(r.data?.items || []); setError(''); })
            .catch((e) => { setItems([]); setError(errText(e, 'Не удалось загрузить новости')); })
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers, bucket]);

    useEffect(() => { load(); }, [load]);

    useEffect(() => {
        axios.get(`${apiBaseUrl}/api/news/access`, { headers })
            .then((r) => setAccess(r.data))
            .catch(() => setAccess(null));
    }, [apiBaseUrl, headers]);

    /* Карточку для правки берём с сервера целиком: в списке нет ни текста, ни
       адресатов, и открытая по нему форма показала бы пустую новость. */
    const openForm = useCallback((post) => {
        if (!post) { setFormPost(null); return; }
        if (post.can_edit === false) {
            toastRef.current?.('Править новость может её автор', 'error');
            return;
        }
        axios.get(`${apiBaseUrl}/api/news/posts/${post.id}`, { headers })
            .then((r) => setFormPost(r.data))
            .catch((e) => toastRef.current?.(errText(e, 'Не удалось открыть новость'), 'error'));
    }, [apiBaseUrl, headers]);

    const save = useCallback((payload) => {
        setSaving(true);
        const post = formPost;
        const request = post?.id
            ? axios.patch(`${apiBaseUrl}/api/news/posts/${post.id}`, payload, { headers })
                .then((r) => (payload.publish && r.data?.status !== 'published'
                    ? axios.post(`${apiBaseUrl}/api/news/posts/${post.id}/publish`, {}, { headers })
                    : r))
            : axios.post(`${apiBaseUrl}/api/news/posts`, payload, { headers });
        request
            .then(() => {
                setFormPost(undefined);
                toastRef.current?.(payload.publish ? 'Новость опубликована' : 'Черновик сохранён',
                                   'success');
                /* Уводим в ту корзину, где сохранённое теперь лежит: иначе
                   черновик, сохранённый со вкладки «Опубликованные», исчезает
                   без следа — список перечитывается, а его в нём нет. */
                const target = payload.publish ? 'published' : 'draft';
                if (bucket !== target) setBucket(target);
                else load();
            })
            .catch((e) => toastRef.current?.(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setSaving(false));
    }, [apiBaseUrl, headers, formPost, bucket, load]);

    const act = useCallback((post, action) => {
        const url = `${apiBaseUrl}/api/news/posts/${post.id}${action === 'delete' ? '' : `/${action}`}`;
        const request = action === 'delete'
            ? axios.delete(url, { headers })
            : axios.post(url, {}, { headers });
        request
            .then(() => {
                toastRef.current?.({
                    publish: 'Новость опубликована',
                    archive: 'Новость снята с показа',
                    delete: 'Черновик удалён',
                }[action], 'success');
                load();
            })
            .catch((e) => toastRef.current?.(errText(e, 'Не получилось'), 'error'));
    }, [apiBaseUrl, headers, load]);

    /* «Раздел разворачивается» и «нет прав» — разные ответы, и путать их
       нельзя: первый пройдёт сам, а второй человек понесёт в поддержку. */
    if (access && access.schema_ready === false) {
        return (
            <div className={`${iosCard} p-6 text-center`}>
                <p className="text-[14px] text-slate-900">Раздел «Новости» разворачивается</p>
                <p className="mt-1 text-[13px] text-slate-400">Загляните чуть позже</p>
            </div>
        );
    }
    if (access && !access.can_publish) {
        return (
            <div className={`${iosCard} p-6 text-center`}>
                <p className="text-[14px] text-slate-900">Новости публикуют супервайзер и выше</p>
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <IosSegmented
                    value={bucket}
                    options={BUCKETS}
                    onChange={setBucket}
                />
                <button type="button" className={iosBtnPrimary} onClick={() => openForm(null)}>
                    <Megaphone className="mr-1.5 inline h-4 w-4" aria-hidden="true" />
                    Новая новость
                </button>
            </div>

            {error && <p className="text-[13px] text-rose-600">{error}</p>}

            {loading && (
                <p className="py-10 text-center text-[13px] text-slate-400">
                    <Loader2 className="mr-1.5 inline h-4 w-4 animate-spin" />Загружаем
                </p>
            )}

            {!loading && items.length === 0 && (
                <div className={`${iosCard} px-6 py-10 text-center`}>
                    <p className="text-[14px] text-slate-900">
                        {bucket === 'published' ? 'Опубликованных новостей нет'
                            : bucket === 'draft' ? 'Черновиков нет' : 'Архив пуст'}
                    </p>
                    <p className="mt-1 text-[13px] text-slate-400">
                        Новость показывается окном поверх портала — сотрудник увидит её
                        при входе, а если он уже в системе, окно всплывёт сразу после публикации.
                    </p>
                </div>
            )}

            {!loading && items.map((post) => (
                <div key={post.id} className={`${iosCard} flex items-start gap-3 px-4 py-3.5`}>
                    <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                            <p className="text-[15px] font-medium text-slate-900">{post.title}</p>
                            {/* Плашка только у того, что не опубликовано:
                                «опубликована» и так видно по вкладке, а метка
                                на каждой строке была бы шумом. */}
                            {post.status === 'draft' && <IosBadge tone="slate">черновик</IosBadge>}
                            {post.status === 'archived' && <IosBadge tone="slate">снята</IosBadge>}
                            {!post.is_mandatory && <IosBadge tone="slate">необязательная</IosBadge>}
                        </div>
                        <p className="mt-1 truncate text-[12px] text-slate-400">
                            {[post.author_name, post.author_department,
                              publishedLabel(post.published_at || post.created_at)]
                                .filter(Boolean).join(' · ')}
                        </p>
                        {post.status === 'published' && post.is_mandatory && (
                            <button
                                type="button"
                                onClick={() => setReportPost(post)}
                                className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-slate-500 underline-offset-2 transition hover:text-slate-900 hover:underline"
                            >
                                <Users className="h-3.5 w-3.5" aria-hidden="true" />
                                <span className="tabular-nums">
                                    Прочитали: {post.confirmed_count} из {post.audience_count}
                                </span>
                            </button>
                        )}
                    </div>
                    {/* Что можно с этой новостью, решает СЕРВЕР и присылает
                        признаком can_edit: коллега того же уровня видит чужое
                        объявление своего отдела, но правит его только автор.
                        Вторая формула здесь дала бы пункт меню, на который
                        сервер отвечает 403.
                        Пустое меню не рисуем вовсе: «три точки», за которыми
                        ничего нет, — обещание действия, которого не будет. */}
                    {(() => {
                        const actions = [
                            ...(post.can_edit
                                ? [{ key: 'edit', label: 'Изменить',
                                     onSelect: () => openForm(post) }]
                                : []),
                            ...(post.can_edit && post.status !== 'published'
                                ? [{ key: 'publish', label: 'Опубликовать',
                                     onSelect: () => act(post, 'publish') }]
                                : []),
                            ...(post.can_take_down && post.status === 'published'
                                ? [{ key: 'archive', label: 'Снять с показа',
                                     onSelect: () => act(post, 'archive') }]
                                : []),
                            ...(post.status === 'published' && post.is_mandatory
                                ? [{ key: 'report', label: 'Кто прочитал',
                                     onSelect: () => setReportPost(post) }]
                                : []),
                            ...(post.can_edit && post.status !== 'published'
                                ? [{ key: 'delete', label: 'Удалить', danger: true,
                                     separatorBefore: true,
                                     onSelect: () => act(post, 'delete') }]
                                : []),
                        ];
                        return actions.length ? <IosMenu items={actions} /> : null;
                    })()}
                </div>
            ))}

            <NewsForm
                open={formPost !== undefined}
                post={formPost}
                access={access}
                saving={saving}
                onClose={() => setFormPost(undefined)}
                onSave={save}
            />
            <NewsReport
                open={!!reportPost}
                post={reportPost}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                onClose={() => setReportPost(null)}
            />
        </div>
    );
}

import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ImagePlus, Loader2, Send, Trash2 } from 'lucide-react';
import {
    APPLE_FONT, IosHint, IosLightbox, IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel,
} from '../ui/ios';
import { DEFAULT_CHAT_THEME, chatThemeById, chatThemeStyle, loadThemeLayer } from './chatThemes';
import {
    DEFAULT_WALLPAPER_SETTINGS, WALLPAPER_LIMITS, loadStaffWallpapers, prepareWallpaperFile, saveChatWallpaper,
    wallpaperImage,
} from './chatWallpaper';
import ChatWallpaperLayer from './ChatWallpaperLayer';
import MessageDeliveryStatus from './MessageDeliveryStatus';
import './chatCustomize.css';

/* «Кастомизация» «Чатов ОП» — одно окно вместо прежних кнопок «Тема» и обоев
 * (просьба владельца 23.10.2026): тема окна, своя картинка на фоне и её вид.
 *
 * Слева — живое превью: уменьшенная копия НАСТОЯЩЕГО окна переписки тех же
 * пропорций, что окно у человека на экране, и с теми же классами разметки.
 * Поэтому темы (их слои красят окно по классам) и обои с видимой частью в
 * превью выглядят ровно так, как лягут в разделе. Пока не нажали «Готово»,
 * всё здесь — черновик: окно раздела не меняется.
 *
 * Админам — вторая вкладка: у кого какие обои стоят (wazzup/appearance.py). */

const FALLBACK_PANE = Object.freeze({ width: 860, height: 620 });
const SAME = (a, b) => Object.keys(DEFAULT_WALLPAPER_SETTINGS).every((key) => a?.[key] === b?.[key]);
const round1 = (value) => Math.round(value * 10) / 10;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/* Насколько картинка, растянутая «на всё окно» (cover), шире и выше окна —
   столько и можно сдвигать. Размытый слой шире окна на два радиуса с каждой
   стороны (ChatWallpaperLayer.jsx) — считаем от него. */
export function previewOverflow(pane, image, blur = 0) {
    if (!image?.width || !image?.height) return { x: 0, y: 0 };
    const pad = blur > 0 ? Math.ceil(blur * 2) : 0;
    const width = pane.width + pad * 2;
    const height = pane.height + pad * 2;
    const cover = Math.max(width / image.width, height / image.height);
    return { x: image.width * cover - width, y: image.height * cover - height };
}

/* Сдвиг пальцем на dx/dy пикселей окна -> новая видимая часть в процентах.
   Тянут вправо — картинка едет вправо, то есть видна её более левая часть. */
export function movedPosition(start, dx, dy, over) {
    return {
        x: over.x > 0.5 ? round1(clamp(start.x - (dx / over.x) * 100, 0, 100)) : start.x,
        y: over.y > 0.5 ? round1(clamp(start.y - (dy / over.y) * 100, 0, 100)) : start.y,
    };
}

const signed = (value) => (value > 0 ? `+${value}` : value < 0 ? `−${Math.abs(value)}` : '0');
const SLIDERS = [
    { key: 'brightness', label: 'Яркость', origin: 0, format: (value) => `${signed(value)} %` },
    { key: 'blur', label: 'Размытие', origin: 0, format: (value) => `${value * 5} %` },
    { key: 'saturation', label: 'Насыщенность', origin: 100, format: (value) => `${value} %` },
];

const fmtUpdated = (value) => {
    const date = value ? new Date(value) : null;
    if (!date || Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

function WallpaperSlider({ label, value, min, max, origin, format, onChange }) {
    const id = useId();
    const at = (point) => ((point - min) / (max - min)) * 100;
    const from = Math.min(at(origin), at(value));
    const to = Math.max(at(origin), at(value));
    return (
        <div>
            <div className="flex items-baseline justify-between gap-3">
                <label htmlFor={id} className="text-[13px] font-medium text-slate-700">{label}</label>
                <span className="text-[12px] tabular-nums text-slate-400">{format(value)}</span>
            </div>
            {/* Двойной щелчок возвращает ползунок в исходную точку — как в «Фото» macOS. */}
            <input id={id} type="range" min={min} max={max} step={1} value={value}
                onChange={(event) => onChange(Number(event.target.value))}
                onDoubleClick={() => onChange(origin)}
                className="wz-slider" style={{ '--from': `${from}%`, '--to': `${to}%` }} />
        </div>
    );
}

/* Образец темы — маленькое окно: подложка, чужой пузырь слева, свой справа. */
function ThemePicker({ themes, value, onChange }) {
    return (
        <div role="radiogroup" aria-label="Тема" className="grid grid-cols-4 gap-x-2 gap-y-3">
            {themes.map((theme) => {
                const active = theme.id === value;
                return (
                    <button key={theme.id} type="button" role="radio" aria-checked={active}
                        onClick={() => onChange(theme.id)}
                        className="group flex min-w-0 flex-col items-center gap-1.5 rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                        <span className={`relative block h-12 w-full overflow-hidden rounded-xl ring-1 transition ${
                            active ? 'ring-2 ring-blue-500 ring-offset-2' : 'ring-slate-900/10 group-hover:ring-slate-900/25'}`}
                            style={{ background: theme.swatch || theme.wall }}>
                            <span className="absolute left-[7px] top-[9px] h-[8px] w-[55%] rounded-[3px]" style={{ background: theme.incoming }} />
                            <span className="absolute bottom-[9px] right-[7px] h-[8px] w-[55%] rounded-[3px]" style={{ background: theme.outgoing }} />
                        </span>
                        <span className={`line-clamp-2 max-w-full text-center text-[11.5px] leading-[14px] ${
                            active ? 'font-semibold text-slate-900' : 'text-slate-500'}`}>
                            {theme.name}
                        </span>
                    </button>
                );
            })}
        </div>
    );
}

function PreviewBubble({ out = false, author = '', time, children }) {
    return (
        <div className={`wazzup-message-row flex items-start gap-1.5 ${out ? 'justify-end' : 'justify-start'} px-3 sm:px-4`}>
            <div className={`flex min-w-0 max-w-[85%] flex-col sm:max-w-[min(78%,720px)] ${out ? 'items-end' : 'items-start'}`}>
                {out && author && (
                    <div className="wz-wall-label mb-1 max-w-full truncate px-1 text-[11px] font-medium leading-4 text-slate-500">{author}</div>
                )}
                <div className={`wazzup-message-bubble min-w-0 max-w-full rounded-2xl px-3 py-2 text-[16px] leading-[1.45] shadow-[0_1px_1px_rgba(15,23,42,0.05)] ${
                    out ? 'wazzup-message-outgoing rounded-br-md bg-[#dcf8c6] text-slate-900'
                        : 'wazzup-message-incoming rounded-bl-md bg-white text-slate-900 ring-1 ring-slate-200/60'}`}>
                    <div className="whitespace-pre-wrap break-words">{children}</div>
                    <div className="wazzup-message-meta mt-0.5 flex flex-wrap items-center justify-end gap-1 text-[11px] text-slate-500">
                        <span>{time}</span>
                        {out && <MessageDeliveryStatus status="read" />}
                    </div>
                </div>
            </div>
        </div>
    );
}

/* Копия окна переписки (WazzupChatsView.jsx: .wz-chat-pane) — те же классы,
   чтобы тема и стекло подписей ложились так же. Меняешь разметку окна —
   поправь и здесь (сверяет tests/wazzup_chat_customize.test.mjs). */
export function PreviewPane({ image, settings, author }) {
    return (
        <div className="wz-chat-pane relative isolate flex min-w-0 flex-1 flex-col bg-[#f2f2f7]">
            {image?.url && <ChatWallpaperLayer url={image.url} settings={settings} className="-z-10" />}
            <div className="relative z-20 flex flex-wrap items-center justify-between gap-2 border-b border-slate-200/70 bg-white/85 px-4 py-2.5 backdrop-blur-xl">
                <div className="flex min-w-0 items-center gap-2.5">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-200 text-[13px] font-medium text-slate-600">К</div>
                    <div className="min-w-0">
                        <div className="truncate text-[14px] font-semibold text-slate-900">Клиент</div>
                        <div className="truncate text-[11px] text-slate-400">+7 700 000 00 00 · WhatsApp</div>
                    </div>
                </div>
            </div>
            <div className="wz-thread-surface relative min-h-0 flex-1">
                <div className="flex h-full flex-col justify-end overflow-hidden py-3">
                    <div className="mx-auto w-full max-w-[1040px] space-y-2">
                        <div className="flex justify-center py-1.5">
                            <span className="wz-day-pill rounded-full bg-slate-500/10 px-3 py-1 text-[11px] font-medium text-slate-500">Сегодня</span>
                        </div>
                        <PreviewBubble time="10:41">Здравствуйте! Отправил документы, проверьте, пожалуйста</PreviewBubble>
                        <PreviewBubble out author={author} time="10:42">
                            Добрый день! Уже смотрю — всё в порядке, можно выходить на линию
                        </PreviewBubble>
                        <PreviewBubble time="10:43">Спасибо!</PreviewBubble>
                    </div>
                </div>
            </div>
            <div className="shrink-0 border-t border-slate-200/70 bg-white p-2.5 sm:px-4">
                <div className="mx-auto flex w-full max-w-[1040px] items-end gap-1.5">
                    <div className="min-h-10 min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-[15px] leading-6 text-slate-400">Сообщение</div>
                    <span className="ml-1 inline-flex h-10 shrink-0 items-center justify-center rounded-xl bg-blue-500 px-3 text-white">
                        <Send size={18} />
                    </span>
                </div>
            </div>
        </div>
    );
}

/* Превью вписано в своё место целиком, с пропорциями окна человека. Картинку
   в нём можно тянуть: видимая часть — background-position, а сдвиг пальцем
   пересчитывается в проценты по тому, насколько картинка шире или выше окна. */
function ChatPreview({ pane, theme, image, settings, author, onMove }) {
    const holder = useRef(null);
    const drag = useRef(null);
    const [room, setRoom] = useState(null);
    useLayoutEffect(() => {
        const element = holder.current;
        if (!element) return undefined;
        const read = () => setRoom((prev) => {
            const next = { width: element.clientWidth, height: element.clientHeight };
            return prev && prev.width === next.width && prev.height === next.height ? prev : next;
        });
        read();
        if (typeof ResizeObserver !== 'function') return undefined;
        const observer = new ResizeObserver(read);
        observer.observe(element);
        return () => observer.disconnect();
    }, []);
    const scale = room ? Math.min(room.width / pane.width, room.height / pane.height) : 0;
    const themed = theme.id !== DEFAULT_CHAT_THEME;
    const movable = Boolean(image?.url && image.width && image.height);

    const onPointerDown = (event) => {
        if (!movable || event.button > 0) return;
        event.currentTarget.setPointerCapture?.(event.pointerId);
        drag.current = { id: event.pointerId, px: event.clientX, py: event.clientY,
            start: { x: settings.x, y: settings.y }, over: previewOverflow(pane, image, settings.blur) };
    };
    const onPointerMove = (event) => {
        const current = drag.current;
        if (!current || current.id !== event.pointerId) return;
        const next = movedPosition(current.start, (event.clientX - current.px) / scale,
            (event.clientY - current.py) / scale, current.over);
        onMove(next.x, next.y);
    };
    const onPointerEnd = (event) => {
        if (drag.current?.id === event.pointerId) drag.current = null;
    };
    const onKeyDown = (event) => {
        const step = event.shiftKey ? 10 : 2;
        const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[event.key];
        if (!movable || !delta) return;
        event.preventDefault();
        onMove(clamp(settings.x + delta[0], 0, 100), clamp(settings.y + delta[1], 0, 100));
    };

    return (
        <div ref={holder} className="flex h-full min-h-0 w-full items-center justify-center">
            {scale > 0 && (
                <div role="group"
                    aria-label={movable ? 'Превью окна чата. Перетащите картинку или нажимайте стрелки, чтобы выбрать видимую часть' : 'Превью окна чата'}
                    tabIndex={movable ? 0 : -1} onKeyDown={onKeyDown}
                    onPointerDown={onPointerDown} onPointerMove={onPointerMove}
                    onPointerUp={onPointerEnd} onPointerCancel={onPointerEnd}
                    data-testid="chat-customize-preview"
                    className={`relative shrink-0 overflow-hidden rounded-[18px] shadow-[0_18px_44px_rgba(15,23,42,0.18)] ring-1 ring-slate-900/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                        movable ? 'cursor-grab touch-none active:cursor-grabbing' : ''}`}
                    style={{ width: Math.floor(pane.width * scale), height: Math.floor(pane.height * scale) }}>
                    <div aria-hidden="true" className="pointer-events-none select-none"
                        style={{ width: pane.width, height: pane.height, transform: `scale(${scale})`, transformOrigin: '0 0' }}>
                        <div className="wz-workspace h-full w-full" data-chat-theme={themed ? theme.id : undefined}
                            data-chat-wallpaper={image?.url ? '' : undefined}
                            style={{ fontFamily: APPLE_FONT, ...(themed ? chatThemeStyle(theme) : {}) }}>
                            {/* .wz-columns не прямым ребёнком окна: на телефоне правило
                                раскладки раздела (workspace.css) растянуло бы его на экран. */}
                            <div className="h-full">
                                <div className="wz-columns relative flex h-full overflow-hidden bg-white">
                                    <PreviewPane image={image} settings={settings} author={author} />
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function useWallpaperImage(id, thumb) {
    const [url, setUrl] = useState('');
    useEffect(() => {
        if (!id) return undefined;
        let alive = true;
        setUrl('');
        wallpaperImage(id, { thumb }).then((value) => { if (alive) setUrl(value); }).catch(() => {});
        return () => { alive = false; };
    }, [id, thumb]);
    return url;
}

function StaffCard({ item, onOpen }) {
    const url = useWallpaperImage(item.wallpaper.id, true);
    const updated = fmtUpdated(item.wallpaper.updatedAt);
    return (
        <button type="button" onClick={onOpen}
            className={`${iosCard} group overflow-hidden text-left transition hover:shadow-[0_6px_20px_rgba(15,23,42,0.10)] active:scale-[0.99]`}>
            <span className="relative block aspect-[16/10] overflow-hidden bg-slate-100">
                {url ? <ChatWallpaperLayer url={url} settings={item.settings} scale={0.3} />
                    : <Loader2 size={18} className="absolute left-1/2 top-1/2 -ml-[9px] -mt-[9px] animate-spin text-slate-400" />}
            </span>
            <span className="block px-3 py-2.5">
                <span className="block truncate text-[13.5px] font-semibold text-slate-900">{item.name || `Учётка ${item.userId}`}</span>
                <span className="block truncate text-[12px] text-slate-500">
                    {[item.department, updated].filter(Boolean).join(' · ')}
                </span>
            </span>
        </button>
    );
}

function StaffLightbox({ items, index, onIndex, onClose }) {
    const item = items[index];
    const full = useWallpaperImage(item?.wallpaper.id, false);
    const thumb = useWallpaperImage(item?.wallpaper.id, true);
    if (!item) return null;
    return <IosLightbox url={full || thumb} alt={`Обои: ${item.name || `учётка ${item.userId}`}`} onClose={onClose}
        counter={items.length > 1 ? `${index + 1} из ${items.length}` : ''}
        onPrev={items.length > 1 ? () => onIndex((index - 1 + items.length) % items.length) : undefined}
        onNext={items.length > 1 ? () => onIndex((index + 1) % items.length) : undefined} />;
}

function StaffWallpapers({ onCount }) {
    const [items, setItems] = useState(null);
    const [error, setError] = useState('');
    const [open, setOpen] = useState(-1);
    const load = useCallback(() => {
        let alive = true;
        setError('');
        loadStaffWallpapers().then((list) => {
            if (!alive) return;
            setItems(list);
            onCount?.(list.length);
        }).catch((problem) => {
            if (alive) setError(problem?.response?.data?.error || 'Не удалось загрузить список обоев');
        });
        return () => { alive = false; };
    }, [onCount]);
    useEffect(() => load(), [load]);

    if (error) {
        return (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 py-10 text-center text-[13.5px] text-slate-500">
                <p>{error}</p>
                <button type="button" onClick={load} className={iosBtnSecondary}>Повторить</button>
            </div>
        );
    }
    if (items === null) {
        return <div className="flex flex-1 items-center justify-center py-10"><Loader2 size={20} className="animate-spin text-slate-400" /></div>;
    }
    if (!items.length) {
        return <div className="flex flex-1 items-center justify-center px-6 py-10 text-[13.5px] text-slate-500">Обои пока никто не поставил</div>;
    }
    return (
        <div className="thin-scroll min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {items.map((item, index) => <StaffCard key={item.userId} item={item} onOpen={() => setOpen(index)} />)}
            </div>
            {open >= 0 && <StaffLightbox items={items} index={open} onIndex={setOpen} onClose={() => setOpen(-1)} />}
        </div>
    );
}

export default function ChatCustomizeSheet({
    open, onClose, themes, selectedTheme, onChooseTheme, wallpaper, admin = false, author = '', pane,
}) {
    const [tab, setTab] = useState('mine');
    const [themeId, setThemeId] = useState(selectedTheme);
    const [image, setImage] = useState(null);
    const [settings, setSettings] = useState(DEFAULT_WALLPAPER_SETTINGS);
    const [touched, setTouched] = useState(false);
    const [busy, setBusy] = useState('');
    const [error, setError] = useState('');
    const [staffCount, setStaffCount] = useState(null);
    const [dragOver, setDragOver] = useState(false);
    const fileInput = useRef(null);
    // Своя, ещё не сохранённая картинка: её адрес освобождаем, когда она больше не нужна.
    const pendingUrl = useRef('');
    const releasePending = useCallback((keep = '') => {
        const url = pendingUrl.current;
        pendingUrl.current = '';
        // Окно гаснет ещё ~200 мс с превью на экране — отпускаем адрес позже.
        if (url && url !== keep) setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, []);

    const stored = wallpaper.wallpaper;
    const storedImage = stored ? { kind: 'stored', id: stored.id, url: wallpaper.url, width: stored.width, height: stored.height } : null;

    // Каждое открытие — с того, что стоит сейчас.
    useEffect(() => {
        if (!open) return;
        setTab('mine');
        setThemeId(selectedTheme);
        setImage(storedImage);
        setSettings(wallpaper.settings);
        setTouched(false);
        setBusy('');
        setError('');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);
    /* Обои пришли позже, чем открыли окно (первый ответ сервера, загрузка
       картинки), — черновик их подхватывает, пока человек их не трогал. */
    useEffect(() => {
        if (!open || touched) return;
        setImage(storedImage);
        setSettings(wallpaper.settings);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, touched, stored?.id, wallpaper.url, wallpaper.settings]);
    useEffect(() => { if (!open) releasePending(); }, [open, releasePending]);
    useEffect(() => () => releasePending(), [releasePending]);
    useEffect(() => { if (open) loadThemeLayer(themeId).catch(() => {}); }, [open, themeId]);

    const cancel = useCallback(() => { if (busy !== 'save') onClose(); }, [busy, onClose]);
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => { if (event.key === 'Escape' && !event.defaultPrevented) cancel(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open, cancel]);

    const pick = async (file) => {
        if (!file || busy) return;
        setError('');
        setBusy('prepare');
        try {
            const prepared = await prepareWallpaperFile(file);
            releasePending();
            pendingUrl.current = prepared.url;
            setImage({ kind: 'new', ...prepared });
            // Новая картинка — с середины; яркость и прочее остаются, как настроены.
            setSettings((current) => ({ ...current, x: 50, y: 50 }));
            setTouched(true);
        } catch (problem) {
            setError(problem?.message || 'Не удалось открыть картинку');
        } finally {
            setBusy('');
        }
    };
    const removeImage = () => {
        setImage(null);
        setTouched(true);
        setError('');
    };
    const change = (patch) => {
        setSettings((current) => ({ ...current, ...patch }));
        setTouched(true);
    };

    const save = async () => {
        if (busy) return;
        setError('');
        setBusy('save');
        try {
            if (image?.kind === 'new') await saveChatWallpaper({ image, settings });
            else if (!image && stored) await saveChatWallpaper({ remove: true, settings });
            else if (image?.kind === 'stored' && !SAME(settings, wallpaper.settings)) await saveChatWallpaper({ settings });
            if (themeId !== selectedTheme) onChooseTheme(themeId);
            // Сохранённая картинка уже показана в разделе — её адрес не освобождаем.
            if (image?.kind === 'new') pendingUrl.current = '';
            setBusy('');
            onClose();
        } catch (problem) {
            setBusy('');
            setError(problem?.message || 'Не удалось сохранить');
        }
    };

    if (typeof document === 'undefined') return null;
    const theme = chatThemeById(themeId);
    const wallpaperAllowed = wallpaper.mayUpload || Boolean(stored);
    const settingsChanged = !SAME(settings, DEFAULT_WALLPAPER_SETTINGS);
    const onDrop = (event) => {
        event.preventDefault();
        setDragOver(false);
        if (wallpaperAllowed) pick(event.dataTransfer?.files?.[0]);
    };
    const dropTarget = wallpaperAllowed ? {
        onDragOver: (event) => { event.preventDefault(); setDragOver(true); },
        onDragLeave: () => setDragOver(false),
        onDrop,
    } : {};

    const mine = (
        <div className="min-h-0 flex-1 overflow-y-auto md:grid md:grid-cols-[minmax(0,1fr)_360px] md:overflow-hidden" {...dropTarget}>
            <div className={`h-[44vh] min-h-[240px] p-4 transition md:h-full md:p-6 ${dragOver ? 'bg-blue-50/70' : ''}`}>
                <ChatPreview pane={pane || FALLBACK_PANE} theme={theme} image={image} settings={settings} author={author}
                    onMove={(x, y) => change({ x, y })} />
            </div>
            <div className="thin-scroll space-y-5 px-4 pb-5 md:overflow-y-auto md:border-l md:border-slate-200/70 md:py-5">
                {themes.length > 1 && (
                    <section className="space-y-2">
                        <div className={iosGroupLabel}>Тема</div>
                        <div className={`${iosCard} p-3`}>
                            <ThemePicker themes={themes} value={themeId} onChange={setThemeId} />
                        </div>
                    </section>
                )}
                {wallpaperAllowed && (
                    <section className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <span className={iosGroupLabel}>Обои</span>
                            <IosHint label="Об обоях" text="Своя картинка на фоне переписки — видна на любом компьютере. Подойдут JPG, PNG или WebP; файл можно перетащить прямо в окно. В превью картинку можно сдвинуть, чтобы выбрать видимую часть. Обои видите вы и администраторы." />
                        </div>
                        <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" className="hidden"
                            aria-label="Выбрать картинку для обоев"
                            onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; pick(file); }} />
                        {image ? (
                            <div className={`${iosCard} flex items-center gap-3 p-3`}>
                                <span className="relative h-14 w-20 shrink-0 overflow-hidden rounded-xl bg-slate-100 ring-1 ring-slate-900/10">
                                    {image.url ? <img src={image.url} alt="" className="h-full w-full object-cover" />
                                        : <Loader2 size={16} className="absolute left-1/2 top-1/2 -ml-2 -mt-2 animate-spin text-slate-400" />}
                                </span>
                                <span className="flex min-w-0 flex-1 flex-wrap gap-2">
                                    <button type="button" onClick={() => fileInput.current?.click()} disabled={Boolean(busy) || !wallpaper.mayUpload}
                                        className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 text-[12.5px] font-semibold text-slate-700 transition hover:bg-slate-200 active:scale-[0.97] disabled:opacity-50">
                                        {busy === 'prepare' ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />} Заменить
                                    </button>
                                    <button type="button" onClick={removeImage} disabled={Boolean(busy)}
                                        className="inline-flex items-center gap-1.5 rounded-full bg-rose-50 px-3 py-1.5 text-[12.5px] font-semibold text-rose-600 transition hover:bg-rose-100 active:scale-[0.97] disabled:opacity-50">
                                        <Trash2 size={13} /> Убрать
                                    </button>
                                </span>
                            </div>
                        ) : (
                            <button type="button" onClick={() => fileInput.current?.click()} disabled={Boolean(busy) || !wallpaper.mayUpload}
                                className={`flex w-full flex-col items-center justify-center gap-1.5 rounded-2xl border border-dashed px-4 py-5 text-center transition active:scale-[0.99] disabled:opacity-60 ${
                                    dragOver ? 'border-blue-400 bg-blue-50/70' : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-blue-50/40'}`}>
                                {busy === 'prepare' ? <Loader2 size={22} className="animate-spin text-blue-500" /> : <ImagePlus size={22} className="text-blue-500" />}
                                <span className="text-[13.5px] font-semibold text-slate-700">Загрузить картинку</span>
                            </button>
                        )}
                    </section>
                )}
                {image && (
                    <section className="space-y-2">
                        <div className="flex items-center justify-between gap-2">
                            <span className={iosGroupLabel}>Вид обоев</span>
                            {settingsChanged && (
                                <button type="button" onClick={() => change(DEFAULT_WALLPAPER_SETTINGS)}
                                    className="rounded-lg px-1.5 py-0.5 text-[12px] font-medium text-blue-600 transition hover:bg-blue-50">
                                    Сбросить
                                </button>
                            )}
                        </div>
                        <div className={`${iosCard} space-y-3.5 p-4`}>
                            {SLIDERS.map((slider) => (
                                <WallpaperSlider key={slider.key} label={slider.label} value={settings[slider.key]}
                                    min={WALLPAPER_LIMITS[slider.key].min} max={WALLPAPER_LIMITS[slider.key].max}
                                    origin={slider.origin} format={slider.format}
                                    onChange={(value) => change({ [slider.key]: value })} />
                            ))}
                        </div>
                    </section>
                )}
            </div>
        </div>
    );

    const footer = tab === 'mine' ? (
        <>
            {error && <p role="alert" className="mr-auto min-w-0 text-[12.5px] leading-snug text-rose-600">{error}</p>}
            <button type="button" onClick={cancel} disabled={busy === 'save'} className={iosBtnSecondary}>Отмена</button>
            <button type="button" onClick={save} disabled={Boolean(busy)} className={iosBtnPrimary}>
                {busy === 'save' ? <><Loader2 size={15} className="animate-spin" /> Сохраняем…</> : 'Готово'}
            </button>
        </>
    ) : null;

    return createPortal(
        <IosModal open={open} onClose={cancel} title="Кастомизация" maxWidth="max-w-5xl" footer={footer}
            bodyClassName="thin-scroll flex min-h-0 flex-1 flex-col overflow-hidden md:basis-[640px]">
            {admin && (
                <div className="shrink-0 border-b border-slate-200/70 px-4 py-2.5 sm:px-5">
                    <IosSegmented value={tab} onChange={setTab} stretch slide ariaLabel="Что показать"
                        options={[{ value: 'mine', label: 'Мой вид' },
                            { value: 'staff', label: 'Обои сотрудников', count: staffCount ?? undefined }]} />
                </div>
            )}
            {tab === 'staff' && admin ? <StaffWallpapers onCount={setStaffCount} /> : mine}
        </IosModal>,
        document.body,
    );
}

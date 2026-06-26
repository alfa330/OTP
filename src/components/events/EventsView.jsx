import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';

/* ──────────────────────────────────────────────────────────────────────
 * Раздел «Ивенты»: общедоступная лента постов с фото/видео, лайками и
 * комментариями. Сетка (как в Instagram) ↔ строки. Создание постов —
 * только админ/СВ/глава отдела (бэкенд решает can_publish и список отделов).
 * Стилистика сайта: Tailwind, синий (blue-600) + slate, скругления, тени.
 * ────────────────────────────────────────────────────────────────────── */

const PAGE_SIZE = 12;
const COMMENTS_PAGE_SIZE = 20;
const MAX_MEDIA = 10;
const LAYOUT_STORAGE_KEY = 'events_layout';

const cls = (...values) => values.filter(Boolean).join(' ');

const readLayout = () => {
    try {
        const saved = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
        return saved === 'list' ? 'list' : 'grid';
    } catch {
        return 'grid';
    }
};

const MONTHS_RU = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

// Бэкенд хранит время в локальной зоне (Asia/Almaty), отдаёт ISO без TZ —
// трактуем как локальное и показываем по-человечески.
const formatDate = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const now = new Date();
    const sameDay = (a, b) => a.getFullYear() === b.getFullYear()
        && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    const hhmm = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (sameDay(date, now)) return `сегодня, ${hhmm}`;
    if (sameDay(date, yesterday)) return `вчера, ${hhmm}`;
    const year = date.getFullYear() === now.getFullYear() ? '' : ` ${date.getFullYear()}`;
    return `${date.getDate()} ${MONTHS_RU[date.getMonth()]}${year}, ${hhmm}`;
};

const initialsOf = (name) => {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
};

const Avatar = ({ name, url, size = 36 }) => {
    const [failed, setFailed] = useState(false);
    const dimension = { width: size, height: size };
    if (url && !failed) {
        return (
            <img
                src={url}
                alt=""
                style={dimension}
                onError={() => setFailed(true)}
                className="rounded-full object-cover bg-slate-100 flex-shrink-0"
            />
        );
    }
    return (
        <div
            style={dimension}
            className="rounded-full bg-blue-100 text-blue-700 flex items-center justify-center font-semibold flex-shrink-0"
        >
            <span style={{ fontSize: Math.max(10, Math.round(size * 0.36)) }}>{initialsOf(name)}</span>
        </div>
    );
};

const pluralDepartments = (n) => {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return 'отдел';
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return 'отдела';
    return 'отделов';
};

const DepartmentBadge = ({ event }) => {
    const ids = event.department_ids || [];
    const names = event.department_names || [];
    const all = ids.length === 0;
    let label;
    if (all) label = 'Все отделы';
    else if (ids.length === 1) label = names[0] || 'Отдел';
    else label = `${ids.length} ${pluralDepartments(ids.length)}`;
    return (
        <span
            title={all ? 'Все отделы' : names.join(', ')}
            className={cls(
                'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium',
                all ? 'bg-blue-50 text-blue-600' : 'bg-slate-100 text-slate-600',
            )}
        >
            <FaIcon className={all ? 'fas fa-globe' : 'fas fa-building'} />
            {label}
        </span>
    );
};

const SkeletonBlock = ({ className = '' }) => <div className={cls('sk-shimmer', className)} />;

/* ── Карусель медиа (фото + видео) ─────────────────────────────────── */
const MediaCarousel = ({ media = [], full = false, rounded = 'rounded-xl' }) => {
    const [index, setIndex] = useState(0);
    const safe = useMemo(() => media.filter(Boolean), [media]);
    useEffect(() => { setIndex(0); }, [safe.length]);
    if (!safe.length) return null;
    const item = safe[Math.min(index, safe.length - 1)];
    const go = (delta, e) => {
        if (e) e.stopPropagation();
        setIndex((prev) => (prev + delta + safe.length) % safe.length);
    };
    const aspect = full ? 'max-h-[70vh]' : 'aspect-[4/3]';
    return (
        <div className={cls('relative w-full bg-slate-900/95 overflow-hidden select-none', rounded, full ? '' : aspect)}>
            <div className={cls('w-full flex items-center justify-center', full ? '' : 'h-full')}>
                {item.type === 'video' ? (
                    <video
                        key={item.id}
                        src={item.url}
                        poster={item.poster_url || undefined}
                        controls
                        playsInline
                        preload="metadata"
                        className={cls('w-full bg-black', full ? 'max-h-[70vh]' : 'h-full object-cover')}
                    />
                ) : (
                    <img
                        key={item.id}
                        src={full ? item.url : (item.preview_url || item.url)}
                        alt=""
                        loading="lazy"
                        decoding="async"
                        className={cls('w-full', full ? 'max-h-[70vh] object-contain' : 'h-full object-cover')}
                    />
                )}
            </div>
            {safe.length > 1 && (
                <>
                    <button
                        type="button"
                        onClick={(e) => go(-1, e)}
                        className="absolute left-2 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-black/45 hover:bg-black/65 text-white flex items-center justify-center backdrop-blur-sm transition-colors"
                        aria-label="Предыдущее"
                    >
                        <FaIcon className="fas fa-chevron-left" />
                    </button>
                    <button
                        type="button"
                        onClick={(e) => go(1, e)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-black/45 hover:bg-black/65 text-white flex items-center justify-center backdrop-blur-sm transition-colors"
                        aria-label="Следующее"
                    >
                        <FaIcon className="fas fa-chevron-right" />
                    </button>
                    <div className="absolute bottom-2 left-1/2 -translate-x-1/2 flex items-center gap-1.5">
                        {safe.map((m, i) => (
                            <span
                                key={m.id ?? i}
                                className={cls('h-1.5 rounded-full transition-all', i === index ? 'w-4 bg-white' : 'w-1.5 bg-white/55')}
                            />
                        ))}
                    </div>
                    <span className="absolute top-2 right-2 px-2 py-0.5 rounded-full bg-black/45 text-white text-[11px] font-medium backdrop-blur-sm">
                        {index + 1}/{safe.length}
                    </span>
                </>
            )}
        </div>
    );
};

/* ── Кнопки действий поста (лайк / комментарии) ────────────────────── */
const PostActions = ({ event, onToggleLike, onOpenComments, busyLike }) => (
    <div className="flex items-center gap-1 text-slate-600">
        <button
            type="button"
            onClick={onToggleLike}
            disabled={busyLike}
            className={cls(
                'inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium transition-colors',
                event.liked ? 'text-rose-600 hover:bg-rose-50' : 'text-slate-600 hover:bg-slate-100',
            )}
            aria-pressed={event.liked}
        >
            <FaIcon className="fas fa-heart" fill={event.liked ? 'currentColor' : 'none'} />
            <span>{event.like_count || 0}</span>
        </button>
        <button
            type="button"
            onClick={onOpenComments}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-100 transition-colors"
        >
            <FaIcon className="fas fa-comment" />
            <span>{event.comment_count || 0}</span>
        </button>
    </div>
);

/* ── Карточка-строка (режим «строки») ──────────────────────────────── */
const EventRow = ({ event, onOpen, onToggleLike, onDelete, busyLike }) => (
    <article className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden transition-shadow hover:shadow-md">
        <div className="px-4 sm:px-5 pt-4 flex items-start gap-3">
            <Avatar name={event.author_name} url={event.author_avatar_url} size={40} />
            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold text-gray-900 truncate">{event.author_name}</span>
                    <DepartmentBadge event={event} />
                </div>
                <div className="text-xs text-gray-400 mt-0.5">{formatDate(event.created_at)}</div>
            </div>
            {event.can_delete && (
                <button
                    type="button"
                    onClick={() => onDelete(event)}
                    className="text-slate-300 hover:text-rose-500 p-1.5 rounded-lg hover:bg-rose-50 transition-colors"
                    title="Удалить пост"
                >
                    <FaIcon className="fas fa-trash-alt" />
                </button>
            )}
        </div>

        {(event.title || event.body) && (
            <div className="px-4 sm:px-5 pt-3">
                {event.title && <h3 className="text-base font-bold text-gray-900 mb-1">{event.title}</h3>}
                {event.body && <p className="text-sm text-gray-700 whitespace-pre-wrap break-words leading-relaxed">{event.body}</p>}
            </div>
        )}

        {event.media?.length > 0 && (
            <div className="px-4 sm:px-5 pt-3 cursor-pointer" onClick={() => onOpen(event)}>
                <MediaCarousel media={event.media} />
            </div>
        )}

        <div className="px-3 sm:px-4 py-2 mt-1 flex items-center justify-between border-t border-gray-100">
            <PostActions
                event={event}
                busyLike={busyLike}
                onToggleLike={() => onToggleLike(event)}
                onOpenComments={() => onOpen(event)}
            />
            <button
                type="button"
                onClick={() => onOpen(event)}
                className="text-sm text-blue-600 hover:text-blue-700 font-medium px-2"
            >
                Открыть
            </button>
        </div>
    </article>
);

/* ── Плитка (режим «сетка», как в Instagram) ───────────────────────── */
const EventTile = ({ event, onOpen }) => {
    const cover = event.media?.[0];
    const coverUrl = cover ? (cover.type === 'video' ? cover.poster_url : (cover.preview_url || cover.url)) : null;
    return (
        <button
            type="button"
            onClick={() => onOpen(event)}
            className="group relative aspect-square w-full overflow-hidden rounded-xl bg-slate-100 ring-1 ring-gray-200 hover:ring-blue-300 transition-all"
        >
            {coverUrl ? (
                <img
                    src={coverUrl}
                    alt=""
                    loading="lazy"
                    decoding="async"
                    className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-[1.04]"
                />
            ) : (
                <div className="w-full h-full flex flex-col items-center justify-center gap-2 p-3 text-center bg-gradient-to-br from-slate-50 to-blue-50">
                    <FaIcon className="fas fa-calendar-days text-blue-300 text-2xl" />
                    <span className="text-[11px] text-slate-500 line-clamp-3">{event.title || event.body}</span>
                </div>
            )}

            {cover?.type === 'video' && (
                <span className="absolute top-2 right-2 w-7 h-7 rounded-full bg-black/45 text-white flex items-center justify-center backdrop-blur-sm">
                    <FaIcon className="fas fa-play" />
                </span>
            )}
            {event.media?.length > 1 && (
                <span className="absolute top-2 left-2 w-6 h-6 rounded-md bg-black/40 text-white flex items-center justify-center backdrop-blur-sm">
                    <FaIcon className="fas fa-images" />
                </span>
            )}

            <div className="absolute inset-x-0 bottom-0 p-2 bg-gradient-to-t from-black/65 to-transparent opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                <div className="flex items-center gap-3 text-white text-xs font-medium">
                    <span className="inline-flex items-center gap-1"><FaIcon className="fas fa-heart" /> {event.like_count || 0}</span>
                    <span className="inline-flex items-center gap-1"><FaIcon className="fas fa-comment" /> {event.comment_count || 0}</span>
                </div>
            </div>
        </button>
    );
};

/* ── Один комментарий ──────────────────────────────────────────────── */
const CommentItem = ({ comment, onDelete }) => (
    <div className="flex items-start gap-2.5">
        <Avatar name={comment.user_name} url={comment.author_avatar_url} size={32} />
        <div className="min-w-0 flex-1">
            <div className="bg-slate-100 rounded-2xl rounded-tl-sm px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                    <span className="text-[13px] font-semibold text-gray-900 truncate">{comment.user_name}</span>
                    {comment.can_delete && (
                        <button
                            type="button"
                            onClick={() => onDelete(comment)}
                            className="text-slate-300 hover:text-rose-500 transition-colors flex-shrink-0"
                            title="Удалить комментарий"
                        >
                            <FaIcon className="fas fa-trash-alt" style={{ fontSize: '0.8em' }} />
                        </button>
                    )}
                </div>
                <p className="text-sm text-gray-700 whitespace-pre-wrap break-words">{comment.body}</p>
            </div>
            <div className="text-[11px] text-gray-400 mt-0.5 ml-3">{formatDate(comment.created_at)}</div>
        </div>
    </div>
);

const EventsView = ({ user, departments = [], showToast, apiBaseUrl, withAccessTokenHeader, onSeen }) => {
    const [events, setEvents] = useState([]);
    const [isLoading, setIsLoading] = useState(true);
    const [isLoadingMore, setIsLoadingMore] = useState(false);
    const [hasMore, setHasMore] = useState(false);
    const [nextBefore, setNextBefore] = useState(null);
    const [error, setError] = useState('');
    const [layout, setLayout] = useState(readLayout);
    const [canPublish, setCanPublish] = useState(false);
    const [canTargetAll, setCanTargetAll] = useState(false);
    const [publishDepartments, setPublishDepartments] = useState([]);
    const [composerOpen, setComposerOpen] = useState(false);
    const [detailEvent, setDetailEvent] = useState(null);
    const [likeBusy, setLikeBusy] = useState(() => new Set());

    const showToastRef = useRef(showToast);
    useEffect(() => { showToastRef.current = showToast; }, [showToast]);
    const notify = useCallback((message, type = 'success') => {
        if (typeof showToastRef.current === 'function') showToastRef.current(message, type);
    }, []);

    const apiRoot = apiBaseUrl || '';
    const authHeaders = useCallback((extra = {}) => {
        const base = { 'X-User-Id': String(user?.id || ''), ...extra };
        return typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader(base) : base;
    }, [user?.id, withAccessTokenHeader]);

    const loadFeedRef = useRef(null);

    const loadFeed = useCallback(async ({ append = false, signal } = {}) => {
        if (append) setIsLoadingMore(true); else { setIsLoading(true); setError(''); }
        try {
            const before = append ? nextBefore : null;
            const response = await axios.get(`${apiRoot}/api/events`, {
                headers: authHeaders(),
                params: { limit: PAGE_SIZE, ...(before ? { before } : {}) },
                signal,
            });
            const data = response?.data || {};
            const rows = Array.isArray(data.events) ? data.events : [];
            setEvents((prev) => {
                if (!append) return rows;
                const seen = new Set(prev.map((e) => e.id));
                return [...prev, ...rows.filter((e) => !seen.has(e.id))];
            });
            setHasMore(Boolean(data.has_more));
            setNextBefore(data.next_before ?? null);
            setCanPublish(Boolean(data.can_publish));
            setCanTargetAll(Boolean(data.can_target_all));
            setPublishDepartments(Array.isArray(data.publish_departments) ? data.publish_departments : []);
        } catch (requestError) {
            if (requestError?.code === 'ERR_CANCELED' || axios.isCancel?.(requestError)) return;
            if (!append) setError(requestError?.response?.data?.error || 'Не удалось загрузить ивенты');
            else notify('Не удалось загрузить ещё', 'error');
        } finally {
            if (append) setIsLoadingMore(false); else setIsLoading(false);
        }
    }, [apiRoot, authHeaders, nextBefore, notify]);
    loadFeedRef.current = loadFeed;

    const markSeen = useCallback(async () => {
        try {
            await axios.post(`${apiRoot}/api/events/seen`, {}, { headers: authHeaders() });
            if (typeof onSeen === 'function') onSeen();
        } catch {
            /* бейдж не критичен — молча игнорируем */
        }
    }, [apiRoot, authHeaders, onSeen]);

    useEffect(() => {
        const controller = new AbortController();
        loadFeedRef.current?.({ append: false, signal: controller.signal });
        markSeen();
        return () => controller.abort();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        try { window.localStorage.setItem(LAYOUT_STORAGE_KEY, layout); } catch { /* noop */ }
    }, [layout]);

    // Бесконечная подгрузка по достижению сентинела.
    const sentinelRef = useRef(null);
    useEffect(() => {
        const node = sentinelRef.current;
        if (!node || !hasMore || isLoading) return undefined;
        const observer = new IntersectionObserver((entries) => {
            if (entries[0]?.isIntersecting && !isLoadingMore) {
                loadFeedRef.current?.({ append: true });
            }
        }, { rootMargin: '600px 0px' });
        observer.observe(node);
        return () => observer.disconnect();
    }, [hasMore, isLoading, isLoadingMore, nextBefore]);

    const patchEvent = useCallback((id, patch) => {
        setEvents((prev) => prev.map((e) => (e.id === id ? { ...e, ...patch } : e)));
        setDetailEvent((prev) => (prev && prev.id === id ? { ...prev, ...patch } : prev));
    }, []);

    // Счётчик комментов меняем функционально (от предыдущего значения), чтобы
    // несколько подряд добавлений/удалений не затирали друг друга устаревшим snapshot.
    const bumpCommentCount = useCallback((id, delta) => {
        setEvents((prev) => prev.map((e) => (e.id === id
            ? { ...e, comment_count: Math.max(0, (e.comment_count || 0) + delta) } : e)));
        setDetailEvent((prev) => (prev && prev.id === id
            ? { ...prev, comment_count: Math.max(0, (prev.comment_count || 0) + delta) } : prev));
    }, []);

    const toggleLike = useCallback(async (event) => {
        if (likeBusy.has(event.id)) return;
        setLikeBusy((prev) => new Set(prev).add(event.id));
        // оптимистично
        const optimistic = {
            liked: !event.liked,
            like_count: Math.max(0, (event.like_count || 0) + (event.liked ? -1 : 1)),
        };
        patchEvent(event.id, optimistic);
        try {
            const response = await axios.post(`${apiRoot}/api/events/${event.id}/like`, {}, { headers: authHeaders() });
            const data = response?.data || {};
            patchEvent(event.id, { liked: Boolean(data.liked), like_count: Number(data.like_count) || 0 });
        } catch (e) {
            patchEvent(event.id, { liked: event.liked, like_count: event.like_count || 0 });
            notify(e?.response?.data?.error || 'Не удалось поставить лайк', 'error');
        } finally {
            setLikeBusy((prev) => { const next = new Set(prev); next.delete(event.id); return next; });
        }
    }, [apiRoot, authHeaders, likeBusy, notify, patchEvent]);

    const deleteEvent = useCallback(async (event) => {
        if (!window.confirm('Удалить этот пост? Действие необратимо.')) return;
        try {
            await axios.delete(`${apiRoot}/api/events/${event.id}`, { headers: authHeaders() });
            setEvents((prev) => prev.filter((e) => e.id !== event.id));
            setDetailEvent((prev) => (prev && prev.id === event.id ? null : prev));
            notify('Пост удалён', 'success');
        } catch (e) {
            notify(e?.response?.data?.error || 'Не удалось удалить пост', 'error');
        }
    }, [apiRoot, authHeaders, notify]);

    const handleCreated = useCallback((event) => {
        setEvents((prev) => [event, ...prev.filter((e) => e.id !== event.id)]);
        setComposerOpen(false);
        notify('Ивент опубликован', 'success');
    }, [notify]);

    const headerActions = (
        <div className="flex items-center gap-2">
            <div className="inline-flex items-center gap-0.5 p-0.5 bg-gray-100 rounded-lg">
                <button
                    type="button"
                    onClick={() => setLayout('grid')}
                    className={cls('px-2.5 py-1.5 rounded-md text-sm transition-colors', layout === 'grid' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700')}
                    title="Сетка"
                    aria-pressed={layout === 'grid'}
                >
                    <FaIcon className="fas fa-table-cells-large" />
                </button>
                <button
                    type="button"
                    onClick={() => setLayout('list')}
                    className={cls('px-2.5 py-1.5 rounded-md text-sm transition-colors', layout === 'list' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700')}
                    title="Строки"
                    aria-pressed={layout === 'list'}
                >
                    <FaIcon className="fas fa-list" />
                </button>
            </div>
            {canPublish && (
                <button
                    type="button"
                    onClick={() => setComposerOpen(true)}
                    className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm font-medium shadow-sm bg-blue-600 text-white hover:bg-blue-700 transition-colors"
                >
                    <FaIcon className="fas fa-plus text-xs" />
                    <span className="hidden sm:inline">Создать</span>
                </button>
            )}
        </div>
    );

    return (
        <div className="space-y-4 max-w-5xl mx-auto">
            {/* Шапка раздела */}
            <div className="bg-white rounded-2xl border border-gray-200 shadow-sm">
                <div className="px-4 sm:px-6 py-4 flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-xl bg-blue-600 flex items-center justify-center shadow-sm">
                            <FaIcon className="fas fa-calendar-days text-white text-base" />
                        </div>
                        <div>
                            <h2 className="text-xl font-bold text-gray-900 leading-tight">Ивенты</h2>
                            <p className="text-xs text-gray-500 mt-0.5">Новости, события и анонсы компании</p>
                        </div>
                    </div>
                    {headerActions}
                </div>
            </div>

            {/* Лента */}
            {isLoading ? (
                <div className={layout === 'grid'
                    ? 'grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 sm:gap-3'
                    : 'space-y-4'}>
                    {Array.from({ length: layout === 'grid' ? 8 : 3 }).map((_, i) => (
                        <SkeletonBlock key={i} className={layout === 'grid' ? 'aspect-square rounded-xl' : 'h-64 rounded-2xl'} />
                    ))}
                </div>
            ) : error ? (
                <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-8 text-center">
                    <div className="w-12 h-12 rounded-2xl bg-rose-50 flex items-center justify-center mx-auto mb-3">
                        <FaIcon className="fas fa-circle-exclamation text-rose-400 text-xl" />
                    </div>
                    <p className="text-sm text-gray-500">{error}</p>
                    <button
                        type="button"
                        onClick={() => loadFeedRef.current?.({ append: false })}
                        className="mt-3 px-4 py-2 rounded-xl text-sm font-medium bg-blue-600 text-white hover:bg-blue-700"
                    >
                        Повторить
                    </button>
                </div>
            ) : events.length === 0 ? (
                <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-10 text-center">
                    <div className="w-14 h-14 rounded-2xl bg-blue-50 flex items-center justify-center mx-auto mb-3">
                        <FaIcon className="fas fa-calendar-days text-blue-300 text-2xl" />
                    </div>
                    <p className="text-sm text-gray-500">Ивентов пока нет</p>
                    {canPublish && (
                        <button
                            type="button"
                            onClick={() => setComposerOpen(true)}
                            className="mt-3 inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-blue-600 text-white hover:bg-blue-700"
                        >
                            <FaIcon className="fas fa-plus text-xs" /> Создать первый
                        </button>
                    )}
                </div>
            ) : (
                <>
                    {layout === 'grid' ? (
                        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 sm:gap-3">
                            {events.map((event) => (
                                <EventTile key={event.id} event={event} onOpen={setDetailEvent} />
                            ))}
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {events.map((event) => (
                                <EventRow
                                    key={event.id}
                                    event={event}
                                    busyLike={likeBusy.has(event.id)}
                                    onOpen={setDetailEvent}
                                    onToggleLike={toggleLike}
                                    onDelete={deleteEvent}
                                />
                            ))}
                        </div>
                    )}

                    <div ref={sentinelRef} className="h-px" />
                    {isLoadingMore && (
                        <div className="flex items-center justify-center py-4 text-slate-400 text-sm gap-2">
                            <FaIcon className="fas fa-spinner fa-spin" /> Загрузка…
                        </div>
                    )}
                    {!isLoadingMore && hasMore && (
                        <div className="flex justify-center py-2">
                            <button
                                type="button"
                                onClick={() => loadFeedRef.current?.({ append: true })}
                                className="px-5 py-2 rounded-xl text-sm font-medium bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 shadow-sm"
                            >
                                Показать ещё
                            </button>
                        </div>
                    )}
                </>
            )}

            {detailEvent && (
                <EventDetailModal
                    event={detailEvent}
                    apiRoot={apiRoot}
                    authHeaders={authHeaders}
                    currentUser={user}
                    notify={notify}
                    onClose={() => setDetailEvent(null)}
                    onToggleLike={toggleLike}
                    busyLike={likeBusy.has(detailEvent.id)}
                    onDelete={deleteEvent}
                    onCommentCountChange={(delta) => bumpCommentCount(detailEvent.id, delta)}
                />
            )}

            {composerOpen && (
                <EventComposerModal
                    apiRoot={apiRoot}
                    authHeaders={authHeaders}
                    canTargetAll={canTargetAll}
                    departments={publishDepartments}
                    notify={notify}
                    onClose={() => setComposerOpen(false)}
                    onCreated={handleCreated}
                />
            )}
        </div>
    );
};

/* ── Модалка просмотра поста с комментариями ───────────────────────── */
const EventDetailModal = ({
    event, apiRoot, authHeaders, currentUser, notify,
    onClose, onToggleLike, busyLike, onDelete, onCommentCountChange,
}) => {
    const [comments, setComments] = useState([]);
    const [loadingComments, setLoadingComments] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [hasMore, setHasMore] = useState(false);
    const [nextBefore, setNextBefore] = useState(null);
    const [draft, setDraft] = useState('');
    const [sending, setSending] = useState(false);
    const deletingRef = useRef(new Set());

    useEffect(() => {
        const handler = (e) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', handler);
        const prevOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => {
            window.removeEventListener('keydown', handler);
            document.body.style.overflow = prevOverflow;
        };
    }, [onClose]);

    const loadComments = useCallback(async (append = false) => {
        if (append) { if (loadingMore) return; setLoadingMore(true); } else setLoadingComments(true);
        try {
            const before = append ? nextBefore : null;
            const response = await axios.get(`${apiRoot}/api/events/${event.id}/comments`, {
                headers: authHeaders(),
                params: { limit: COMMENTS_PAGE_SIZE, ...(before ? { before } : {}) },
            });
            const data = response?.data || {};
            // бэкенд отдаёт новые сверху; показываем старые сверху → разворачиваем
            const incoming = Array.isArray(data.comments) ? [...data.comments].reverse() : [];
            setComments((prev) => {
                if (!append) return incoming;
                const seen = new Set(prev.map((c) => c.id));
                return [...incoming.filter((c) => !seen.has(c.id)), ...prev];
            });
            setHasMore(Boolean(data.has_more));
            setNextBefore(data.next_before ?? null);
        } catch (e) {
            if (!append) notify(e?.response?.data?.error || 'Не удалось загрузить комментарии', 'error');
        } finally {
            if (append) setLoadingMore(false); else setLoadingComments(false);
        }
    }, [apiRoot, authHeaders, event.id, nextBefore, notify, loadingMore]);

    useEffect(() => {
        loadComments(false);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [event.id]);

    const submitComment = useCallback(async (e) => {
        e?.preventDefault?.();
        const body = draft.trim();
        if (!body || sending) return;
        setSending(true);
        try {
            const response = await axios.post(
                `${apiRoot}/api/events/${event.id}/comments`,
                { body },
                { headers: authHeaders() },
            );
            const comment = response?.data?.comment;
            if (comment) {
                setComments((prev) => [...prev, comment]);
                onCommentCountChange?.(1);
            }
            setDraft('');
        } catch (err) {
            notify(err?.response?.data?.error || 'Не удалось отправить комментарий', 'error');
        } finally {
            setSending(false);
        }
    }, [apiRoot, authHeaders, draft, event.id, notify, onCommentCountChange, sending]);

    const deleteComment = useCallback(async (comment) => {
        if (deletingRef.current.has(comment.id)) return; // защита от двойного клика
        deletingRef.current.add(comment.id);
        try {
            await axios.delete(`${apiRoot}/api/events/${event.id}/comments/${comment.id}`, { headers: authHeaders() });
            setComments((prev) => prev.filter((c) => c.id !== comment.id));
            onCommentCountChange?.(-1);
        } catch (e) {
            notify(e?.response?.data?.error || 'Не удалось удалить комментарий', 'error');
        } finally {
            deletingRef.current.delete(comment.id);
        }
    }, [apiRoot, authHeaders, event.id, notify, onCommentCountChange]);

    return (
        <div
            className="fixed inset-0 z-[80] flex items-stretch justify-center bg-slate-900/50 backdrop-blur-sm sm:items-center sm:p-4"
            role="dialog"
            aria-modal="true"
            onClick={onClose}
        >
            <div
                className="flex w-full max-w-2xl flex-col overflow-hidden bg-white shadow-2xl sm:max-h-[92vh] sm:rounded-2xl"
                onClick={(e) => e.stopPropagation()}
            >
                {/* Заголовок */}
                <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-100 flex-shrink-0">
                    <Avatar name={event.author_name} url={event.author_avatar_url} size={40} />
                    <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-semibold text-gray-900 truncate">{event.author_name}</span>
                            <DepartmentBadge event={event} />
                        </div>
                        <div className="text-xs text-gray-400">{formatDate(event.created_at)}</div>
                    </div>
                    {event.can_delete && (
                        <button
                            type="button"
                            onClick={() => onDelete(event)}
                            className="text-slate-300 hover:text-rose-500 p-2 rounded-lg hover:bg-rose-50 transition-colors"
                            title="Удалить пост"
                        >
                            <FaIcon className="fas fa-trash-alt" />
                        </button>
                    )}
                    <button
                        type="button"
                        onClick={onClose}
                        className="text-slate-400 hover:text-slate-700 p-2 rounded-lg hover:bg-slate-100 transition-colors"
                        aria-label="Закрыть"
                    >
                        <FaIcon className="fas fa-times" />
                    </button>
                </div>

                {/* Тело со скроллом */}
                <div className="flex-1 overflow-y-auto ios-modal-scroll">
                    {event.media?.length > 0 && (
                        <div className="bg-slate-900">
                            <MediaCarousel media={event.media} full rounded="rounded-none" />
                        </div>
                    )}
                    {(event.title || event.body) && (
                        <div className="px-4 py-3 border-b border-gray-100">
                            {event.title && <h3 className="text-lg font-bold text-gray-900 mb-1">{event.title}</h3>}
                            {event.body && <p className="text-sm text-gray-700 whitespace-pre-wrap break-words leading-relaxed">{event.body}</p>}
                        </div>
                    )}

                    <div className="px-3 py-2 border-b border-gray-100">
                        <PostActions
                            event={event}
                            busyLike={busyLike}
                            onToggleLike={() => onToggleLike(event)}
                            onOpenComments={() => {}}
                        />
                    </div>

                    {/* Комментарии */}
                    <div className="px-4 py-3 space-y-3">
                        {hasMore && !loadingComments && (
                            <button
                                type="button"
                                onClick={() => loadComments(true)}
                                disabled={loadingMore}
                                className="inline-flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-700 font-medium disabled:opacity-50"
                            >
                                {loadingMore && <FaIcon className="fas fa-spinner fa-spin" />}
                                Показать предыдущие комментарии
                            </button>
                        )}
                        {loadingComments ? (
                            <div className="space-y-3">
                                {Array.from({ length: 3 }).map((_, i) => (
                                    <div key={i} className="flex gap-2.5">
                                        <SkeletonBlock className="w-8 h-8 rounded-full" />
                                        <SkeletonBlock className="h-12 flex-1 rounded-2xl" />
                                    </div>
                                ))}
                            </div>
                        ) : comments.length === 0 ? (
                            <p className="text-sm text-gray-400 text-center py-4">Пока нет комментариев. Будьте первым!</p>
                        ) : (
                            comments.map((comment) => (
                                <CommentItem key={comment.id} comment={comment} onDelete={deleteComment} />
                            ))
                        )}
                    </div>
                </div>

                {/* Композер комментария */}
                <form onSubmit={submitComment} className="flex items-center gap-2 px-3 py-2.5 border-t border-gray-100 flex-shrink-0 bg-white">
                    <Avatar name={currentUser?.name} url={currentUser?.avatar_url} size={32} />
                    <input
                        type="text"
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        placeholder="Написать комментарий…"
                        maxLength={2000}
                        className="flex-1 px-3 py-2 text-sm border border-gray-200 rounded-full bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
                    />
                    <button
                        type="submit"
                        disabled={!draft.trim() || sending}
                        className="w-9 h-9 flex items-center justify-center rounded-full bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex-shrink-0"
                        aria-label="Отправить"
                    >
                        <FaIcon className={sending ? 'fas fa-spinner fa-spin' : 'fas fa-paper-plane'} />
                    </button>
                </form>
            </div>
        </div>
    );
};

/* ── Захват кадра-постера из видео на клиенте (без ffmpeg на сервере) ─ */
const captureVideoPoster = (file) => new Promise((resolve) => {
    let settled = false;
    const url = URL.createObjectURL(file);
    const video = document.createElement('video');
    // Сторож чистим ТОЛЬКО внутри done(): если canvas.toBlob не вызовет колбэк
    // (баг браузера), 5-секундный таймаут всё равно разрешит промис и не оставит
    // композер навечно в состоянии «обработка».
    const done = (result) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        try { URL.revokeObjectURL(url); } catch { /* noop */ }
        resolve(result);
    };
    const fail = () => done({ blob: null, width: 0, height: 0, duration: null });
    var timer = window.setTimeout(fail, 5000);
    video.preload = 'metadata';
    video.muted = true;
    video.playsInline = true;
    video.onloadeddata = () => {
        try { video.currentTime = Math.min(0.1, (video.duration || 1) * 0.1); } catch { fail(); }
    };
    video.onseeked = () => {
        try {
            const w = video.videoWidth || 640;
            const h = video.videoHeight || 360;
            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            canvas.getContext('2d').drawImage(video, 0, 0, w, h);
            canvas.toBlob(
                (blob) => done({ blob, width: w, height: h, duration: Number.isFinite(video.duration) ? video.duration : null }),
                'image/jpeg',
                0.85,
            );
        } catch {
            fail();
        }
    };
    video.onerror = () => fail();
    video.src = url;
});

/* ── Мульти-выбор отделов-получателей (пустой набор = все отделы) ───── */
const DepartmentMultiSelect = ({ departments = [], value = [], onChange }) => {
    const [open, setOpen] = useState(false);
    const selected = useMemo(() => new Set(value.map(Number)), [value]);
    const nameById = useMemo(() => {
        const m = new Map();
        (departments || []).forEach((d) => m.set(Number(d.id), d.name));
        return m;
    }, [departments]);
    let summary;
    if (!value.length) summary = 'Все отделы';
    else if (value.length === 1) summary = nameById.get(Number(value[0])) || 'Отдел';
    else summary = `${value.length} ${pluralDepartments(value.length)}`;
    const toggleDept = (id) => {
        const n = Number(id);
        if (selected.has(n)) onChange(value.filter((v) => Number(v) !== n));
        else onChange([...value, n]);
    };
    const Check = ({ on, round }) => (
        <span className={cls(
            'w-4 h-4 flex items-center justify-center border flex-shrink-0',
            round ? 'rounded-full' : 'rounded',
            on ? 'border-blue-600 bg-blue-600 text-white' : 'border-gray-300',
        )}>
            {on && <FaIcon className="fas fa-check" style={{ fontSize: '0.6em' }} />}
        </span>
    );
    return (
        <div className="border border-gray-200 rounded-lg overflow-hidden">
            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                className="w-full px-3 py-2.5 text-sm bg-white flex items-center justify-between gap-2 hover:bg-slate-50 transition-colors"
            >
                <span className="flex items-center gap-2 text-slate-700 min-w-0">
                    <FaIcon className={value.length ? 'fas fa-building' : 'fas fa-globe'} />
                    <span className="truncate">{summary}</span>
                </span>
                <FaIcon className={cls('fas fa-chevron-down text-gray-400 transition-transform', open && 'rotate-180')} />
            </button>
            {open && (
                <div className="max-h-56 overflow-y-auto border-t border-gray-100 ios-modal-scroll">
                    <button
                        type="button"
                        onClick={() => onChange([])}
                        className={cls('w-full px-3 py-2 text-sm flex items-center gap-2 hover:bg-slate-50 text-left', !value.length && 'bg-blue-50/60')}
                    >
                        <Check on={!value.length} round />
                        <FaIcon className="fas fa-globe text-blue-500" /> Все отделы
                    </button>
                    {(departments || []).map((d) => {
                        const checked = selected.has(Number(d.id));
                        return (
                            <button
                                key={d.id}
                                type="button"
                                onClick={() => toggleDept(d.id)}
                                className={cls('w-full px-3 py-2 text-sm flex items-center gap-2 hover:bg-slate-50 text-left', checked && 'bg-blue-50/40')}
                            >
                                <Check on={checked} />
                                <FaIcon className="fas fa-building text-slate-400" />
                                <span className="truncate">{d.name}</span>
                            </button>
                        );
                    })}
                    {(departments || []).length === 0 && (
                        <div className="px-3 py-2 text-sm text-gray-400">Нет доступных отделов</div>
                    )}
                </div>
            )}
        </div>
    );
};

/* ── Модалка создания поста ────────────────────────────────────────── */
const EventComposerModal = ({ apiRoot, authHeaders, canTargetAll, departments = [], notify, onClose, onCreated }) => {
    const [title, setTitle] = useState('');
    const [body, setBody] = useState('');
    // Выбранные отделы-получатели (id). Пустой массив => «Все отделы».
    // Глобальный админ выбирает любой набор; привязанный публикатор жёстко
    // ограничен своим единственным отделом.
    const [selectedDeptIds, setSelectedDeptIds] = useState(() => (
        canTargetAll ? [] : (departments?.[0]?.id != null ? [Number(departments[0].id)] : [])
    ));
    const [items, setItems] = useState([]); // {id, file, type, previewUrl, poster?:{blob,width,height,duration}}
    const [processing, setProcessing] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [progress, setProgress] = useState(0);
    const fileInputRef = useRef(null);
    const idSeq = useRef(0);

    useEffect(() => {
        const handler = (e) => { if (e.key === 'Escape' && !submitting) onClose(); };
        window.addEventListener('keydown', handler);
        const prevOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => {
            window.removeEventListener('keydown', handler);
            document.body.style.overflow = prevOverflow;
        };
    }, [onClose, submitting]);

    // Чистим object-URL превью/постеров. Трекаем КАЖДЫЙ созданный URL в ref,
    // чтобы ничего не утекло, даже если композер закрыли во время захвата постера.
    const createdUrlsRef = useRef([]);
    const mountedRef = useRef(true);
    const trackUrl = useCallback((u) => { if (u) createdUrlsRef.current.push(u); }, []);
    useEffect(() => () => {
        mountedRef.current = false;
        createdUrlsRef.current.forEach((u) => { try { URL.revokeObjectURL(u); } catch { /* noop */ } });
        createdUrlsRef.current = [];
    }, []);

    const addFiles = useCallback(async (fileList) => {
        const files = Array.from(fileList || []);
        if (!files.length) return;
        const room = MAX_MEDIA - items.length;
        if (room <= 0) { notify(`Не более ${MAX_MEDIA} файлов`, 'error'); return; }
        const accepted = files.slice(0, room);
        if (files.length > room) notify(`Добавлены не все файлы: лимит ${MAX_MEDIA}`, 'error');
        setProcessing(true);
        try {
            const built = [];
            for (const file of accepted) {
                const isVideo = (file.type || '').startsWith('video/');
                const isImage = (file.type || '').startsWith('image/');
                if (!isVideo && !isImage) { notify(`Файл «${file.name}» не поддерживается`, 'error'); continue; }
                idSeq.current += 1;
                const entry = {
                    id: `m${idSeq.current}`,
                    file,
                    type: isVideo ? 'video' : 'image',
                    previewUrl: URL.createObjectURL(file),
                    poster: null,
                    posterUrl: null,
                };
                trackUrl(entry.previewUrl);
                if (isVideo) {
                    // eslint-disable-next-line no-await-in-loop
                    entry.poster = await captureVideoPoster(file);
                    entry.posterUrl = entry.poster?.blob ? URL.createObjectURL(entry.poster.blob) : null;
                    trackUrl(entry.posterUrl);
                }
                // Композер размонтировали, пока шёл захват — не коммитим в стейт
                // и отзываем URL'ы текущего элемента (ранее созданные уже отозваны).
                if (!mountedRef.current) {
                    try { URL.revokeObjectURL(entry.previewUrl); } catch { /* noop */ }
                    if (entry.posterUrl) { try { URL.revokeObjectURL(entry.posterUrl); } catch { /* noop */ } }
                    return;
                }
                built.push(entry);
            }
            if (built.length && mountedRef.current) setItems((prev) => [...prev, ...built]);
        } finally {
            if (mountedRef.current) setProcessing(false);
        }
    }, [items.length, notify, trackUrl]);

    const removeItem = useCallback((id) => {
        setItems((prev) => {
            const target = prev.find((it) => it.id === id);
            if (target) {
                try { URL.revokeObjectURL(target.previewUrl); } catch { /* noop */ }
                if (target.posterUrl) { try { URL.revokeObjectURL(target.posterUrl); } catch { /* noop */ } }
            }
            return prev.filter((it) => it.id !== id);
        });
    }, []);

    const submit = useCallback(async () => {
        if (submitting) return;
        const trimmedBody = body.trim();
        if (!trimmedBody && items.length === 0) { notify('Добавьте текст или медиа', 'error'); return; }
        setSubmitting(true);
        setProgress(0);
        try {
            const form = new FormData();
            form.append('title', title.trim());
            form.append('body', trimmedBody);
            form.append('department_ids', JSON.stringify(selectedDeptIds));
            const meta = [];
            items.forEach((it) => {
                form.append('media', it.file, it.file.name);
                if (it.type === 'video') {
                    const hasPoster = !!(it.poster && it.poster.blob);
                    meta.push({
                        type: 'video',
                        has_poster: hasPoster,
                        duration: it.poster?.duration ?? null,
                        width: it.poster?.width ?? 0,
                        height: it.poster?.height ?? 0,
                    });
                    // Постер шлём по порядку только для видео с has_poster — бэкенд
                    // совмещает posters[] с видео по этому флагу (без рассинхрона).
                    if (hasPoster) {
                        form.append('posters', it.poster.blob, 'poster.jpg');
                    }
                } else {
                    meta.push({ type: 'image' });
                }
            });
            form.append('media_meta', JSON.stringify(meta));
            const response = await axios.post(`${apiRoot}/api/events`, form, {
                headers: authHeaders(),
                onUploadProgress: (e) => {
                    if (e.total) setProgress(Math.round((e.loaded / e.total) * 100));
                },
            });
            const created = response?.data?.event;
            if (created) onCreated(created);
            else { notify('Ивент опубликован', 'success'); onClose(); }
        } catch (e) {
            notify(e?.response?.data?.error || 'Не удалось опубликовать ивент', 'error');
        } finally {
            setSubmitting(false);
            setProgress(0);
        }
    }, [apiRoot, authHeaders, body, selectedDeptIds, items, notify, onClose, onCreated, submitting, title]);

    return (
        <div
            className="fixed inset-0 z-[85] flex items-stretch justify-center bg-slate-900/50 backdrop-blur-sm sm:items-center sm:p-4"
            role="dialog"
            aria-modal="true"
            onClick={() => { if (!submitting) onClose(); }}
        >
            <div
                className="flex w-full max-w-lg flex-col overflow-hidden bg-white shadow-2xl sm:max-h-[92vh] sm:rounded-2xl"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 flex-shrink-0">
                    <h3 className="text-base font-bold text-gray-900 flex items-center gap-2">
                        <span className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center">
                            <FaIcon className="fas fa-plus text-white text-xs" />
                        </span>
                        Новый ивент
                    </h3>
                    <button
                        type="button"
                        onClick={() => { if (!submitting) onClose(); }}
                        className="text-slate-400 hover:text-slate-700 p-2 rounded-lg hover:bg-slate-100 transition-colors"
                        aria-label="Закрыть"
                    >
                        <FaIcon className="fas fa-times" />
                    </button>
                </div>

                <div className="flex-1 overflow-y-auto ios-modal-scroll px-4 py-4 space-y-4">
                    <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Заголовок</label>
                        <input
                            type="text"
                            value={title}
                            onChange={(e) => setTitle(e.target.value)}
                            placeholder="Например: Корпоратив в пятницу 🎉"
                            maxLength={255}
                            className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition placeholder-gray-400"
                        />
                    </div>
                    <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Текст</label>
                        <textarea
                            value={body}
                            onChange={(e) => setBody(e.target.value)}
                            placeholder="Расскажите подробнее…"
                            rows={4}
                            maxLength={8000}
                            className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition resize-y placeholder-gray-400"
                        />
                    </div>

                    <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Кому виден</label>
                        {canTargetAll ? (
                            <DepartmentMultiSelect
                                departments={departments}
                                value={selectedDeptIds}
                                onChange={setSelectedDeptIds}
                            />
                        ) : (
                            <div className="px-3 py-2.5 text-sm border border-gray-200 rounded-lg bg-slate-50 text-slate-600 flex items-center gap-2">
                                <FaIcon className="fas fa-building" />
                                {departments?.[0]?.name || 'Ваш отдел'}
                            </div>
                        )}
                    </div>

                    <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">
                            Медиа <span className="text-gray-300">({items.length}/{MAX_MEDIA})</span>
                        </label>
                        <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                            {items.map((it) => (
                                <div key={it.id} className="relative aspect-square rounded-lg overflow-hidden bg-slate-100 ring-1 ring-gray-200">
                                    {it.type === 'video' ? (
                                        it.posterUrl ? (
                                            <img src={it.posterUrl} alt="" className="w-full h-full object-cover" />
                                        ) : (
                                            <video src={it.previewUrl} muted className="w-full h-full object-cover" />
                                        )
                                    ) : (
                                        <img src={it.previewUrl} alt="" className="w-full h-full object-cover" />
                                    )}
                                    {it.type === 'video' && (
                                        <span className="absolute bottom-1 left-1 w-5 h-5 rounded-full bg-black/55 text-white flex items-center justify-center">
                                            <FaIcon className="fas fa-play" style={{ fontSize: '0.6em' }} />
                                        </span>
                                    )}
                                    <button
                                        type="button"
                                        onClick={() => removeItem(it.id)}
                                        disabled={submitting}
                                        className="absolute top-1 right-1 w-5 h-5 rounded-full bg-black/55 hover:bg-rose-500 text-white flex items-center justify-center transition-colors"
                                        aria-label="Удалить"
                                    >
                                        <FaIcon className="fas fa-times" style={{ fontSize: '0.6em' }} />
                                    </button>
                                </div>
                            ))}
                            {items.length < MAX_MEDIA && (
                                <button
                                    type="button"
                                    onClick={() => fileInputRef.current?.click()}
                                    disabled={processing || submitting}
                                    className="aspect-square rounded-lg border-2 border-dashed border-gray-200 hover:border-blue-300 hover:bg-blue-50/40 text-gray-400 hover:text-blue-500 flex flex-col items-center justify-center gap-1 transition-colors"
                                >
                                    <FaIcon className={processing ? 'fas fa-spinner fa-spin text-lg' : 'fas fa-image text-lg'} />
                                    <span className="text-[10px]">{processing ? 'Обработка' : 'Добавить'}</span>
                                </button>
                            )}
                        </div>
                        <p className="text-[11px] text-gray-400 mt-1.5">Фото и видео. Для видео постер создаётся автоматически.</p>
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="image/*,video/mp4,video/webm,video/quicktime"
                            multiple
                            hidden
                            onChange={(e) => { addFiles(e.target.files); e.target.value = ''; }}
                        />
                    </div>
                </div>

                <div className="px-4 py-3 border-t border-gray-100 flex-shrink-0">
                    {submitting && progress > 0 && (
                        <div className="h-1.5 w-full bg-gray-100 rounded-full overflow-hidden mb-2">
                            <div className="h-full bg-blue-500 rounded-full transition-all duration-200" style={{ width: `${progress}%` }} />
                        </div>
                    )}
                    <div className="flex items-center justify-end gap-2">
                        <button
                            type="button"
                            onClick={() => { if (!submitting) onClose(); }}
                            disabled={submitting}
                            className="px-4 py-2 rounded-xl text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors disabled:opacity-50"
                        >
                            Отмена
                        </button>
                        <button
                            type="button"
                            onClick={submit}
                            disabled={submitting || processing}
                            className="inline-flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                            <FaIcon className={submitting ? 'fas fa-spinner fa-spin' : 'fas fa-paper-plane'} />
                            {submitting ? (progress > 0 ? `${progress}%` : 'Публикация…') : 'Опубликовать'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default EventsView;

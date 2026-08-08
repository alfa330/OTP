import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, BookOpen, FileText, FolderTree, KeyRound, Layers,
    Loader2, RefreshCw, ShieldCheck, Users,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosGroupLabel, iosBtnSecondary, IosBadge,
} from '../ui/ios';
import './wiki-theme.css';

/* Раздел «Вики» — корпоративная база знаний.
 *
 * Этап 1: каркас. Раздел уже подключён к бэкенду, проверяет схему и показывает
 * человеку его собственный периметр доступа. Содержимого (пространства, разделы,
 * статьи) пока нет — оно приходит на этапах 2-3.
 *
 * Почему периметр показан прямо в интерфейсе. Модель прав здесь глубже, чем в
 * остальных разделах: способности роли, правила разделов, правила отдельных
 * статей и запреты поверх них. Когда уровней четыре, вопрос «почему я этого не
 * вижу» возникает неизбежно, и ответ на него должен быть в интерфейсе, а не в
 * логах.
 *
 * Тёмной темы в разделе нет намеренно: портал светлый, а классы dark:* из
 * исходной вики сработали бы от темы системы — darkMode в tailwind.config.cjs
 * не задан, значит Tailwind работает в режиме media.
 */

const CAPABILITY_LABELS = {
    can_read: 'Читать',
    can_create: 'Создавать',
    can_edit: 'Редактировать',
    can_delete: 'Удалять',
    can_publish: 'Публиковать',
    can_approve: 'Согласовывать',
    can_manage_users: 'Управлять людьми',
    can_manage_structure: 'Управлять структурой',
    can_manage_access: 'Управлять доступами',
};

const ACCESS_MODE_LABELS = {
    auto: 'Автоматический',
    manual: 'Ручная выдача',
};

const errText = (error, fallback) =>
    error?.response?.data?.error || error?.message || fallback;

const StatTile = ({ icon: Icon, value, label }) => (
    <div className="flex items-center gap-3 px-4 py-3.5">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-indigo-50 text-indigo-600">
            <Icon size={17} />
        </div>
        <div className="min-w-0">
            <div className="text-[19px] font-semibold leading-none text-slate-900 tabular-nums">
                {value}
            </div>
            <div className="mt-1 truncate text-[12px] text-slate-500">{label}</div>
        </div>
    </div>
);

const Skeleton = () => (
    <div className="space-y-5">
        <div className={`${iosCard} h-[86px]`}>
            <div className="sk-shimmer h-full w-full rounded-2xl" />
        </div>
        <div className={`${iosCard} h-[140px]`}>
            <div className="sk-shimmer h-full w-full rounded-2xl" />
        </div>
    </div>
);

export default function WikiView({ apiBaseUrl, withAccessTokenHeader, showToast, user }) {
    const headers = useMemo(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    const base = `${apiBaseUrl}/api/wiki`;

    const [state, setState] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    const load = useCallback((silent = false) => {
        if (!silent) setLoading(true);
        axios.get(`${base}/ping`, { headers })
            .then((r) => {
                setState(r.data);
                setError('');
            })
            .catch((e) => {
                setState(null);
                setError(errText(e, 'Не удалось связаться с разделом'));
            })
            .finally(() => setLoading(false));
    }, [base, headers]);

    useEffect(() => { load(); }, [load]);

    const capabilities = state?.capabilities || {};
    const granted = Object.keys(CAPABILITY_LABELS).filter((key) => capabilities[key]);
    const counters = state?.counters;
    const subjects = state?.subjects || {};

    const refresh = () => {
        load(true);
        showToast?.('Обновлено', 'success');
    };

    return (
        <div
            className="wiki-scope min-h-full bg-slate-50 px-4 pb-10 pt-[68px] sm:px-6 min-[769px]:pt-8"
            style={{ fontFamily: APPLE_FONT }}
        >
            <div className="mx-auto w-full max-w-5xl space-y-5">

                {/* Шапка раздела. Отступ сверху на мобильном — под фиксированный
                    гамбургер портала (44×44, z-index 60), иначе он накроет заголовок. */}
                <header className="flex flex-wrap items-start justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-indigo-600 text-white shadow-sm">
                            <BookOpen size={21} />
                        </div>
                        <div>
                            <h1 className="text-[22px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
                                Вики
                            </h1>
                            <p className="text-[13px] text-slate-500">
                                База знаний компании
                            </p>
                        </div>
                    </div>
                    <button type="button" onClick={refresh} className={iosBtnSecondary}>
                        <RefreshCw size={15} />
                        Обновить
                    </button>
                </header>

                {loading && <Skeleton />}

                {!loading && error && (
                    <div className={`${iosCard} flex items-start gap-3 p-4`}>
                        <AlertCircle size={18} className="mt-0.5 shrink-0 text-rose-500" />
                        <div className="min-w-0">
                            <div className="text-[14px] font-semibold text-slate-900">
                                Раздел недоступен
                            </div>
                            <div className="mt-0.5 text-[13px] text-slate-500">{error}</div>
                            <button
                                type="button"
                                onClick={() => load()}
                                className={`${iosBtnSecondary} mt-3`}
                            >
                                Попробовать снова
                            </button>
                        </div>
                    </div>
                )}

                {!loading && !error && state && (
                    <>
                        {/* Схема не развёрнута — это не ошибка, а ожидаемое состояние
                            до первого деплоя с миграцией. Говорим об этом прямо. */}
                        {!state.schema_ready && (
                            <div className={`${iosCard} flex items-start gap-3 p-4`}>
                                <Loader2 size={18} className="mt-0.5 shrink-0 animate-spin text-amber-500" />
                                <div>
                                    <div className="text-[14px] font-semibold text-slate-900">
                                        Раздел разворачивается
                                    </div>
                                    <div className="mt-0.5 text-[13px] text-slate-500">
                                        Таблицы ещё не созданы. Они появятся при ближайшем
                                        перезапуске сервера.
                                    </div>
                                </div>
                            </div>
                        )}

                        {state.schema_ready && counters && (
                            <section className="space-y-1.5">
                                <div className={iosGroupLabel}>Содержимое</div>
                                <div className={`${iosCard} grid grid-cols-1 divide-y divide-slate-100 sm:grid-cols-3 sm:divide-x sm:divide-y-0`}>
                                    <StatTile icon={Layers} value={counters.spaces} label="Пространств" />
                                    <StatTile icon={FolderTree} value={counters.sections} label="Разделов" />
                                    <StatTile
                                        icon={FileText}
                                        value={counters.articles_published}
                                        label={`Статей${counters.articles_total !== counters.articles_published
                                            ? ` (всего ${counters.articles_total})` : ''}`}
                                    />
                                </div>
                            </section>
                        )}

                        {/* Пустое состояние — этап 1 - содержимого ещё нет. */}
                        {state.schema_ready && counters && counters.articles_total === 0 && (
                            <div className={`${iosCard} flex flex-col items-center justify-center gap-2 px-6 py-14 text-center`}>
                                <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                                    <BookOpen size={22} />
                                </div>
                                <div className="text-[15px] font-semibold text-slate-900">
                                    Пока пусто
                                </div>
                                <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                                    Структура и статьи появятся на следующих этапах переноса.
                                    Сейчас работает каркас раздела и модель доступа.
                                </p>
                            </div>
                        )}

                        {/* Периметр доступа. Отвечает на вопрос «почему я вижу
                            именно это» до того, как он будет задан. */}
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Ваш доступ</div>
                            <div className={`${iosCard} divide-y divide-slate-100`}>

                                <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3.5">
                                    <div className="flex items-center gap-2.5 text-[13.5px] text-slate-600">
                                        <ShieldCheck size={16} className="text-slate-400" />
                                        Режим выдачи
                                    </div>
                                    <IosBadge tone={state.access_mode === 'manual' ? 'amber' : 'slate'}>
                                        {ACCESS_MODE_LABELS[state.access_mode] || state.access_mode}
                                    </IosBadge>
                                </div>

                                <div className="px-4 py-3.5">
                                    <div className="mb-2 flex items-center gap-2.5 text-[13.5px] text-slate-600">
                                        <KeyRound size={16} className="text-slate-400" />
                                        Что вы можете
                                    </div>
                                    <div className="flex flex-wrap gap-1.5">
                                        {granted.length === 0 && (
                                            <span className="text-[13px] text-slate-400">
                                                Прав пока нет
                                            </span>
                                        )}
                                        {granted.map((key) => (
                                            <IosBadge
                                                key={key}
                                                tone={key === 'can_delete' || key === 'can_manage_access' ? 'amber' : 'blue'}
                                            >
                                                {CAPABILITY_LABELS[key]}
                                            </IosBadge>
                                        ))}
                                    </div>
                                    {state.wiki_roles?.length > 0 && (
                                        <div className="mt-2 text-[12px] text-slate-500">
                                            Роли в вики: {state.wiki_roles.join(', ')}
                                        </div>
                                    )}
                                </div>

                                <div className="px-4 py-3.5">
                                    <div className="mb-2 flex items-center gap-2.5 text-[13.5px] text-slate-600">
                                        <Users size={16} className="text-slate-400" />
                                        Правила применяются к вам как к
                                    </div>
                                    <div className="flex flex-wrap gap-1.5">
                                        {(subjects.otp_role || []).map((role) => (
                                            <IosBadge key={`r-${role}`} tone="slate">роль: {role}</IosBadge>
                                        ))}
                                        {(subjects.department || []).map((id) => (
                                            <IosBadge key={`d-${id}`} tone="slate">отдел #{id}</IosBadge>
                                        ))}
                                        {(subjects.group || []).map((id) => (
                                            <IosBadge key={`g-${id}`} tone="slate">группа #{id}</IosBadge>
                                        ))}
                                        {(subjects.direction || []).map((id) => (
                                            <IosBadge key={`n-${id}`} tone="slate">направление #{id}</IosBadge>
                                        ))}
                                    </div>
                                    <p className="mt-2 text-[12px] leading-relaxed text-slate-500">
                                        Правило, выданное на роль ниже вашей, действует и на вас —
                                        руководитель видит всё, что видит подчинённый.
                                    </p>
                                </div>

                            </div>
                        </section>
                    </>
                )}
            </div>
        </div>
    );
}

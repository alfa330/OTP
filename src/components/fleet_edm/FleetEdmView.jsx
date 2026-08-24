import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, CheckCircle2, ChevronDown, CircleStop, Download, Ellipsis,
    FileDown, FileSpreadsheet, Loader2, RefreshCw, RotateCcw, Trash2, UploadCloud,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosBtnPrimary, iosBtnGhost, iosGroupLabel, IosBadge,
} from '../ui/ios';

/* Красная кнопка живёт здесь, а не в общем ios.jsx: она нужна одному экрану, а
 * общий файл правят обе машины сразу, и лишняя правка там — лишний конфликт. */
const btnDanger = 'inline-flex items-center justify-center gap-1.5 rounded-xl bg-rose-600 '
    + 'px-3 py-2 text-[13px] font-semibold text-white shadow-sm transition-all '
    + 'hover:bg-rose-700 active:scale-[0.98] disabled:opacity-50';

/* Раздел «Провайдер ЭДО» (задача #176).
 *
 * Человек загружает выгрузку из диспетчерской, робот проходит по списку в
 * кабинете Яндекс.Fleet и возвращает тот же список с колонкой «Провайдер ЭДО».
 *
 * Раскладка подчинена одному действию: сверху зона загрузки, под ней история
 * выгрузок. Всё служебное — состояние связи с кабинетом — убрано вниз одной
 * строкой и раскрывается по требованию: сотруднику, который пришёл за файлом,
 * читать про сессии незачем, а когда связь оборвётся, строка сама станет
 * заметной и скажет, что делать.
 *
 * Обход занимает минуты, поэтому загрузка отвечает сразу, а карточка живёт в
 * базе и опрашивается. Страницу можно закрыть: работа идёт на сервере. */

const POLL_MS = 5000;

const STATUS_META = {
    running: { label: 'Формируется', tone: 'blue' },
    done: { label: 'Готово', tone: 'green' },
    error: { label: 'Ошибка', tone: 'red' },
};

const ERROR_HINTS = {
    session_expired: 'Связь с диспетчерской прервалась — выгрузка не дошла до данных.',
    bad_file: 'Файл не подошёл: нужен столбец с ID водителя.',
    fleet_error: 'Диспетчерская ответила не так, как ожидалось.',
    // Осталось для старых карточек: с 24.08.2026 перезапуск сервера выгрузку не
    // убивает — она продолжается сама с того места, где остановилась.
    interrupted: 'Выгрузку прервал перезапуск сервера (до того, как раздел научился продолжать сам).',
    too_many_restarts: 'Выгрузку прерывали слишком часто, и она не смогла дойти до конца. '
        + 'Попробуйте запустить её ещё раз.',
};

/* Остановленная вручную — не ошибка, и краснеть ей незачем: человек сам так решил.
 * Отдельного статуса в базе нет, отличаем по коду. */
const isStopped = (job) => job.status === 'error' && job.error_code === 'stopped';

const badgeFor = (job) => {
    if (isStopped(job)) return { label: 'Остановлена', tone: 'slate' };
    return STATUS_META[job.status] || { label: job.status, tone: 'slate' };
};

const num = (value) => Number(value || 0).toLocaleString('ru-RU');

const formatDateTime = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
};

const formatDuration = (ms) => {
    if (!ms) return '';
    const total = Math.round(ms / 1000);
    if (total < 60) return `${total} с`;
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return seconds ? `${minutes} мин ${seconds} с` : `${minutes} мин`;
};

const formatSize = (bytes) => {
    if (!bytes) return '';
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
    return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
};

/* «Сколько ждать» — измеренный темп, а не обещание.
 *
 * Замер 24.08.2026 на настоящем файле: кабинет придерживает нас на длинных
 * прогонах, и устойчивый темп выходит около 136 запросов в минуту — то есть
 * каждый запрос это почти полсекунды ожидания. Поэтому время определяется числом
 * ЗАПРОСОВ (диспетчерские × провайдеры), а не числом строк: 1 500 строк из 82
 * диспетчерских стоили 581 запрос и около пяти минут. */
const etaHint = (rowsTotal) => {
    if (!rowsTotal) return '';
    if (rowsTotal <= 500) return 'обычно 1–3 минуты';
    if (rowsTotal <= 3000) return 'обычно 4–7 минут';
    if (rowsTotal <= 15000) return 'обычно 10–15 минут';
    return 'на таком объёме — от четверти часа';
};

export default function FleetEdmView({ apiBaseUrl, withAccessTokenHeader, showToast }) {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    const base = `${apiBaseUrl}/api/fleet_edm`;

    const [overview, setOverview] = useState(null);
    const [loadError, setLoadError] = useState(null);
    const [busy, setBusy] = useState('');
    const [dragging, setDragging] = useState(false);
    const [linkOpen, setLinkOpen] = useState(false);
    // Опасные действия — в два нажатия: здесь лежит id выгрузки, у которой
    // спросили подтверждение.
    const [stopAsked, setStopAsked] = useState(null);
    const [deleteAsked, setDeleteAsked] = useState(null);
    const [menuOpen, setMenuOpen] = useState(null);

    const fileInput = useRef(null);
    const poll = useRef(null);

    const jobs = overview?.jobs || [];
    const session = overview?.session || {};
    const canManage = Boolean(overview?.can_manage_session);
    const running = useMemo(() => jobs.find((job) => job.status === 'running'), [jobs]);
    // Идущая выгрузка живёт наверху отдельной карточкой, поэтому в истории её нет:
    // один и тот же прогресс в двух местах на одном экране — это шум.
    const history = useMemo(() => jobs.filter((job) => job.status !== 'running'), [jobs]);
    const linked = Boolean(session.configured) && !session.last_error;

    const load = useCallback(() => {
        setLoadError(null);
        return axios.get(`${base}/overview`, { headers: headers() })
            .then((response) => setOverview(response.data))
            .catch((error) => setLoadError(
                error?.response?.data?.error || 'Не удалось загрузить раздел',
            ));
    }, [base, headers]);

    useEffect(() => { load(); }, [load]);

    // Пока выгрузка идёт — подтягиваем карточку; закончилась — опрос прекращается сам.
    useEffect(() => {
        clearInterval(poll.current);
        if (running) poll.current = setInterval(load, POLL_MS);
        return () => clearInterval(poll.current);
    }, [running, load]);

    useEffect(() => () => clearInterval(poll.current), []);

    // Связь оборвалась — разворачиваем подробности сами: это единственный случай,
    // когда человеку действительно нужно про неё прочитать.
    useEffect(() => {
        if (overview && !linked) setLinkOpen(true);
    }, [overview, linked]);

    // Меню строки закрывается щелчком мимо него. Слушаем mousedown и смотрим, попал
    // ли щелчок внутрь какого-нибудь меню: иначе то же нажатие, которое меню
    // открыло, тут же его и закрыло бы.
    useEffect(() => {
        if (menuOpen === null) return undefined;
        const close = (event) => {
            if (!event.target.closest?.('[data-row-menu]')) {
                setMenuOpen(null);
                setDeleteAsked(null);
            }
        };
        document.addEventListener('mousedown', close);
        return () => document.removeEventListener('mousedown', close);
    }, [menuOpen]);

    const upload = async (file) => {
        if (!file) return;
        setBusy('upload');
        try {
            const form = new FormData();
            form.append('file', file);
            const response = await axios.post(`${base}/jobs`, form, { headers: headers() });
            showToast?.(`Файл принят, выгрузка №${response.data?.job_id} пошла`, 'success');
            await load();
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось запустить выгрузку', 'error');
        } finally {
            setBusy('');
            if (fileInput.current) fileInput.current.value = '';
        }
    };

    const download = async (job, kind = 'result') => {
        setBusy(`file:${job.id}`);
        try {
            const response = await axios.get(`${base}/jobs/${job.id}/file`, {
                headers: headers(),
                responseType: 'blob',
                params: kind === 'source' ? { kind: 'source' } : undefined,
            });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = kind === 'source'
                ? (job.source_name || `Исходник ${job.id}.xlsx`)
                : (job.file_name || `Провайдер ЭДО ${job.id}.xlsx`);
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
        } catch (error) {
            showToast?.('Не удалось скачать файл', 'error');
        } finally {
            setBusy('');
        }
    };

    const stop = async (job) => {
        setBusy(`stop:${job.id}`);
        try {
            await axios.post(`${base}/jobs/${job.id}/stop`, {}, { headers: headers() });
            showToast?.(`Выгрузка №${job.id} остановлена`, 'success');
            await load();
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось остановить', 'error');
        } finally {
            setBusy('');
        }
    };

    const repeat = async (job) => {
        setBusy(`repeat:${job.id}`);
        try {
            const response = await axios.post(`${base}/jobs/${job.id}/repeat`, {},
                { headers: headers() });
            showToast?.(`Собираем заново — выгрузка №${response.data?.job_id}`, 'success');
            await load();
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось повторить', 'error');
        } finally {
            setBusy('');
        }
    };

    const remove = async (job) => {
        setBusy(`delete:${job.id}`);
        try {
            await axios.delete(`${base}/jobs/${job.id}`, { headers: headers() });
            showToast?.(`Выгрузка №${job.id} удалена`, 'success');
            await load();
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось удалить', 'error');
        } finally {
            setBusy('');
        }
    };

    const checkLink = async () => {
        setBusy('link');
        try {
            const response = await axios.post(`${base}/session/check`, {}, { headers: headers() });
            showToast?.(
                response.data?.alive
                    ? `Связь есть: ${response.data.parks_count} диспетчерских`
                    : 'Связь с диспетчерской не отвечает',
                response.data?.alive ? 'success' : 'error',
            );
            await load();
        } catch (error) {
            showToast?.('Не удалось проверить связь', 'error');
        } finally {
            setBusy('');
        }
    };

    const dropzoneDisabled = busy === 'upload' || Boolean(running) || !linked;

    return (
        <div className="mx-auto max-w-[980px] p-4 sm:p-6 space-y-6" style={{ fontFamily: APPLE_FONT }}>
            <header className="space-y-1">
                <h1 className="text-[26px] font-bold tracking-[-0.01em] text-slate-900">
                    Провайдер ЭДО
                </h1>
                <p className="text-[13.5px] text-slate-500">
                    Загрузите список водителей — вернём его же с колонкой «Провайдер ЭДО»
                    из диспетчерских Яндекс.Такси.
                </p>
            </header>

            {loadError && (
                <div className="rounded-2xl bg-rose-50 px-4 py-3 text-[13px] text-rose-700 ring-1 ring-rose-100">
                    {loadError}
                </div>
            )}

            {/* Верхний блок — ровно одно состояние из трёх. Пока выгрузка идёт, зона
                загрузки не нужна вовсе: она бы занимала полэкрана, ничего не делая,
                а прогресс показывался бы дважды. */}
            {running ? (
                <div className={`${iosCard} px-5 py-4 space-y-3`}>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <Loader2 size={18} className="shrink-0 animate-spin text-blue-500" />
                        <div className="min-w-[200px] flex-1">
                            <div className="truncate text-[14.5px] font-semibold text-slate-900">
                                {running.source_name || `Выгрузка №${running.id}`}
                            </div>
                            <div className="text-[12.5px] text-slate-500 tabular-nums">
                                {running.progress_note || 'Идёт обход диспетчерских'}
                                {running.rows_total ? ` · ${etaHint(running.rows_total)}` : ''}
                            </div>
                        </div>
                        <div className="text-[15px] font-semibold text-slate-700 tabular-nums">
                            {Math.max(0, Math.min(100, running.progress_percent || 0))}%
                        </div>
                        {/* Остановка — в два нажатия. Одно случайное касание не должно
                            убивать обход, который идёт десять минут. */}
                        {stopAsked === running.id ? (
                            <div className="flex items-center gap-1.5">
                                <button type="button" className={btnDanger}
                                        onClick={() => { setStopAsked(null); stop(running); }}
                                        disabled={busy === `stop:${running.id}`}>
                                    {busy === `stop:${running.id}`
                                        ? <Loader2 size={14} className="animate-spin" />
                                        : <CircleStop size={14} />}
                                    Точно остановить
                                </button>
                                <button type="button" className={iosBtnGhost}
                                        onClick={() => setStopAsked(null)}>
                                    Нет
                                </button>
                            </div>
                        ) : (
                            <button type="button" className={iosBtnGhost}
                                    onClick={() => setStopAsked(running.id)}>
                                <CircleStop size={14} /> Остановить
                            </button>
                        )}
                    </div>
                    <div className="h-1 w-full overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-blue-500 transition-all duration-700"
                             style={{ width: `${Math.max(0, Math.min(100, running.progress_percent || 0))}%` }} />
                    </div>
                    <div className="text-[12px] text-slate-400">
                        {running.attempts > 1
                            ? 'Сервер перезапускался — выгрузка продолжилась с того места, где '
                              + 'остановилась. Страницу можно закрыть.'
                            : 'Страницу можно закрыть — выгрузка считается на сервере. Следующий '
                              + 'файл загрузим, когда закончится этот.'}
                    </div>
                </div>
            ) : !linked ? (
                <div className={`${iosCard} flex items-start gap-3 px-5 py-4`}>
                    <AlertTriangle size={18} className="mt-0.5 shrink-0 text-amber-500" />
                    <div className="space-y-0.5">
                        <div className="text-[14.5px] font-semibold text-slate-900">
                            Выгрузки временно недоступны
                        </div>
                        <div className="text-[12.5px] leading-relaxed text-slate-500">
                            Пропала связь с диспетчерской — подробности ниже.
                            Как только её восстановят, загрузка файла снова появится здесь.
                        </div>
                    </div>
                </div>
            ) : (
                <div
                    onDragOver={(event) => { event.preventDefault(); if (!dropzoneDisabled) setDragging(true); }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(event) => {
                        event.preventDefault();
                        setDragging(false);
                        if (!dropzoneDisabled) upload(event.dataTransfer?.files?.[0]);
                    }}
                    onClick={() => { if (!dropzoneDisabled) fileInput.current?.click(); }}
                    className={`${iosCard} flex flex-col items-center justify-center gap-2 px-6 py-8 text-center transition-all ${
                        dropzoneDisabled ? 'opacity-60' : 'cursor-pointer hover:ring-slate-300 active:scale-[0.995]'
                    } ${dragging ? 'ring-2 ring-blue-500/70 bg-blue-50/40' : ''}`}
                >
                    <div className={`flex h-11 w-11 items-center justify-center rounded-2xl ${
                        dragging ? 'bg-blue-100 text-blue-600' : 'bg-slate-100 text-slate-400'
                    }`}>
                        {busy === 'upload'
                            ? <Loader2 size={20} className="animate-spin text-blue-500" />
                            : <UploadCloud size={20} />}
                    </div>
                    <div className="text-[15px] font-semibold text-slate-900">
                        Перетащите файл или нажмите, чтобы выбрать
                    </div>
                    <div className="max-w-[540px] text-[12.5px] leading-relaxed text-slate-500">
                        Excel или CSV со столбцом <b className="font-semibold text-slate-600">Contractor ID</b>.
                        Если в файле есть и <b className="font-semibold text-slate-600">ID парка</b> — получится
                        в разы быстрее.
                    </div>
                    <input
                        ref={fileInput} type="file" accept=".xlsx,.xlsm,.csv" className="hidden"
                        onChange={(event) => upload(event.target.files?.[0])}
                    />
                </div>
            )}

            {/* История */}
            <section className="space-y-2">
                <div className="flex items-end justify-between gap-2">
                    <div className={iosGroupLabel}>Выгрузки</div>
                    {history.length > 0 && (
                        <button type="button" className={iosBtnGhost} onClick={load}>
                            <RefreshCw size={14} /> Обновить
                        </button>
                    )}
                </div>
                <div className={`${iosCard} overflow-hidden`}>
                    {!overview && !loadError && (
                        <div className="flex items-center gap-2 px-5 py-7 text-[13px] text-slate-500">
                            <Loader2 size={16} className="animate-spin" /> Загружаем…
                        </div>
                    )}
                    {overview && history.length === 0 && (
                        <div className="px-5 py-10 text-center text-[13px] text-slate-400">
                            Выгрузок ещё не было
                        </div>
                    )}
                    {history.map((job, index) => (
                        <div key={job.id} className={index ? 'border-t border-slate-100' : ''}>
                            <JobRow
                                job={job}
                                busy={busy}
                                menuOpen={menuOpen === job.id}
                                deleteAsked={deleteAsked === job.id}
                                blocked={Boolean(running)}
                                onMenu={() => {
                                    setMenuOpen(menuOpen === job.id ? null : job.id);
                                    setDeleteAsked(null);
                                }}
                                onDownload={() => download(job)}
                                onSource={() => { setMenuOpen(null); download(job, 'source'); }}
                                onRepeat={() => { setMenuOpen(null); repeat(job); }}
                                onAskDelete={() => setDeleteAsked(job.id)}
                                onDelete={() => {
                                    setDeleteAsked(null);
                                    setMenuOpen(null);
                                    remove(job);
                                }}
                            />
                        </div>
                    ))}
                </div>
            </section>

            {/* Служебное: связь с кабинетом. В обычный день — одна строка. */}
            <section className="space-y-2">
                <div className={`${iosCard} overflow-hidden`}>
                    <button
                        type="button"
                        onClick={() => setLinkOpen((open) => !open)}
                        className="flex w-full items-center gap-3 px-5 py-3.5 text-left transition hover:bg-slate-50/70"
                    >
                        <span className={`h-2 w-2 shrink-0 rounded-full ${
                            linked ? 'bg-emerald-500' : 'bg-amber-500'
                        }`} />
                        <span className="flex-1 text-[13.5px] font-medium text-slate-700">
                            Связь с диспетчерской
                        </span>
                        <span className={`text-[13px] ${linked ? 'text-slate-500' : 'text-amber-700'}`}>
                            {linked ? 'активна' : 'нужно восстановить'}
                        </span>
                        <ChevronDown
                            size={16}
                            className={`text-slate-400 transition-transform ${linkOpen ? 'rotate-180' : ''}`}
                        />
                    </button>

                    {linkOpen && (
                        <div className="space-y-3 border-t border-slate-100 px-5 py-4">
                            <p className="text-[12.5px] leading-relaxed text-slate-600">
                                {linked
                                    ? <>Раздел заходит в диспетчерскую от имени рабочей учётной записи —
                                       сейчас это <span className="text-slate-800">{session.account || 'настроенный аккаунт'}</span>,
                                       доступно {num(session.parks_count)} диспетчерских.
                                       Обновлено {formatDateTime(session.updated_at)}.</>
                                    : <>Вход в диспетчерскую перестал действовать, и выгрузки временно не работают.
                                       Восстановление занимает пару минут.</>}
                            </p>

                            {/* Кто это делает — главный вопрос, поэтому он написан всем,
                                а не спрятан под правами. */}
                            <p className="text-[12.5px] leading-relaxed text-slate-500">
                                Связь настраивает и восстанавливает разработчик со своего компьютера:
                                сотрудникам раздела для этого ничего делать не нужно, вход в диспетчерскую
                                у вас не спросят. Если написано «нужно восстановить» — сообщите
                                разработчику, и выгрузки заработают снова.
                            </p>

                            {session.last_error && (
                                <div className="rounded-xl bg-amber-50 px-3 py-2 text-[12px] text-amber-800">
                                    {session.last_error}
                                </div>
                            )}

                            {canManage && (
                                <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
                                    <button type="button" className={iosBtnGhost} onClick={checkLink}
                                            disabled={busy === 'link'}>
                                        {busy === 'link'
                                            ? <Loader2 size={14} className="animate-spin" />
                                            : <RefreshCw size={14} />}
                                        Проверить связь
                                    </button>
                                    <span className="text-[11.5px] text-slate-400">
                                        Для разработчика: восстановить — <code className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">
                                        python scripts/fleet_edm_push_session.py</code> из папки проекта,
                                        затем вход в кабинет в открывшемся окне.
                                    </span>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </section>
        </div>
    );
}

function JobRow({ job, busy, menuOpen, deleteAsked, blocked, onMenu, onDownload,
                 onSource, onRepeat, onAskDelete, onDelete }) {
    const meta = badgeFor(job);
    const stats = job.stats || {};
    const check = stats.check || {};
    const stopped = isStopped(job);
    const working = busy.endsWith(`:${job.id}`);

    return (
        <div className="px-5 py-4">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${
                    job.status === 'error' && !stopped
                        ? 'bg-rose-50 text-rose-500' : 'bg-slate-100 text-slate-400'
                }`}>
                    <FileSpreadsheet size={16} />
                </div>
                <div className="min-w-[180px] flex-1">
                    <div className="truncate text-[14px] font-semibold text-slate-900">
                        {job.source_name || `Выгрузка №${job.id}`}
                    </div>
                    <div className="text-[12px] text-slate-500 tabular-nums">
                        {formatDateTime(job.created_at)}
                        {job.created_by_name ? ` · ${job.created_by_name}` : ''}
                        {job.rows_total ? ` · ${num(job.rows_total)} строк` : ''}
                    </div>
                </div>
                {job.status === 'done' && job.has_file ? (
                    <button type="button" className={iosBtnPrimary} onClick={onDownload}
                            disabled={working}>
                        {working ? <Loader2 size={14} className="animate-spin" />
                                 : <Download size={14} />}
                        Скачать
                    </button>
                ) : (
                    <IosBadge tone={meta.tone}>{meta.label}</IosBadge>
                )}
                {/* Остальное — под «тремя точками»: в обычный день человеку нужна
                    одна кнопка «Скачать», а не шесть. */}
                <div className="relative" data-row-menu>
                    <button type="button" aria-label="Ещё" onClick={onMenu}
                            className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
                        <Ellipsis size={16} />
                    </button>
                    {menuOpen && (
                        <div className="absolute right-0 top-10 z-20 w-[236px] overflow-hidden rounded-2xl bg-white py-1 shadow-lg ring-1 ring-slate-900/10">
                            <button type="button" onClick={onRepeat} disabled={blocked}
                                    className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[13px] text-slate-700 transition hover:bg-slate-50 disabled:opacity-40"
                                    title={blocked ? 'Сначала дождитесь текущей выгрузки' : ''}>
                                <RotateCcw size={14} className="shrink-0 text-slate-400" />
                                Собрать заново
                            </button>
                            <button type="button" onClick={onSource}
                                    className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[13px] text-slate-700 transition hover:bg-slate-50">
                                <FileDown size={14} className="shrink-0 text-slate-400" />
                                Скачать исходный файл
                            </button>
                            {deleteAsked ? (
                                <button type="button" onClick={onDelete}
                                        className="flex w-full items-center gap-2 bg-rose-50 px-3.5 py-2.5 text-left text-[13px] font-semibold text-rose-700 transition hover:bg-rose-100">
                                    <Trash2 size={14} className="shrink-0" />
                                    Точно удалить выгрузку
                                </button>
                            ) : (
                                <button type="button" onClick={onAskDelete}
                                        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[13px] text-rose-600 transition hover:bg-rose-50">
                                    <Trash2 size={14} className="shrink-0" />
                                    Удалить из истории
                                </button>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {job.status === 'error' && (
                <div className={`mt-3 flex items-start gap-2 rounded-xl px-3 py-2 text-[12.5px] ${
                    stopped ? 'bg-slate-50 text-slate-600' : 'bg-rose-50 text-rose-700'
                }`}>
                    {stopped ? <CircleStop size={14} className="mt-0.5 shrink-0" />
                             : <AlertTriangle size={14} className="mt-0.5 shrink-0" />}
                    <span>
                        {stopped
                            ? (job.error || 'Выгрузку остановили')
                            : (ERROR_HINTS[job.error_code] || job.error || 'Выгрузка не удалась')}
                        {stopped && job.rows_resolved
                            ? ` · успела собрать ${num(job.rows_resolved)} из ${num(job.rows_total)}`
                            : ''}
                    </span>
                </div>
            )}

            {job.status === 'done' && (
                <div
                    className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-500 tabular-nums"
                    title={`Запросов в диспетчерскую: ${num(job.requests_count)}`}
                >
                    <span className={`inline-flex items-center gap-1 ${
                        job.rows_failed ? 'text-amber-700' : 'text-emerald-600'
                    }`}>
                        <CheckCircle2 size={13} />
                        Провайдер найден у {num(job.rows_resolved)} из {num(job.rows_total)}
                    </span>
                    {check.checked > 0 && (
                        <span title="Случайные строки перепроверены по карточкам водителей — независимым способом">
                            выборочная проверка: {check.matched} из {check.checked}
                        </span>
                    )}
                    {stats.no_provider_by_kind > 0 && (
                        <span title="Сотрудники парка работают по трудовому договору — провайдер ЭДО к ним не применяется. Это ответ, а не пропуск.">
                            сотрудников парка: {num(stats.no_provider_by_kind)}
                        </span>
                    )}
                    {stats.from_card > 0 && <span>дособрано вручную: {num(stats.from_card)}</span>}
                    {formatDuration(job.duration_ms) && <span>{formatDuration(job.duration_ms)}</span>}
                    {formatSize(job.file_size) && <span>{formatSize(job.file_size)}</span>}
                </div>
            )}
        </div>
    );
}

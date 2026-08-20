import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, CheckCircle2, Download, FileSpreadsheet, Info, Loader2,
    RefreshCw, ShieldCheck, ShieldAlert, UploadCloud,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, iosGroupLabel,
    IosBadge, IosSection,
} from '../ui/ios';

/* Раздел «Провайдер ЭДО» (задача #176).
 *
 * Человек загружает выгрузку из диспетчерской, робот проходит по списку в
 * кабинете Яндекс.Fleet и возвращает тот же список с колонкой «Провайдер ЭДО».
 *
 * Почему всё вокруг ожидания. Обход занимает минуты — пять на восьми тысячах
 * строк, — поэтому загрузка отвечает сразу, а карточка выгрузки живёт в базе и
 * опрашивается. Страницу при этом можно закрыть: работа идёт на сервере.
 *
 * Второй смысловой центр — состояние сессии кабинета. Fleet не выдаёт ключей,
 * раздел ходит туда под живым логином, и этот логин когда-нибудь протухнет.
 * Молчать об этом нельзя: человек должен видеть «сессия жива» ДО того, как
 * потратит десять минут на выгрузку, которая упадёт. */

const POLL_MS = 5000;

const STATUS_META = {
    running: { label: 'Формируется', tone: 'blue' },
    done: { label: 'Готово', tone: 'green' },
    error: { label: 'Ошибка', tone: 'red' },
};

const ERROR_HINTS = {
    session_expired: 'Сессия кабинета Fleet протухла — нужен новый вход, выгрузка не дошла до данных.',
    bad_file: 'Файл не подошёл: нужна колонка с ID водителя.',
    fleet_error: 'Кабинет Fleet не ответил как ожидалось.',
    interrupted: 'Приложение перезапустилось во время выгрузки — запустите её заново.',
};

const formatDateTime = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', year: '2-digit',
        hour: '2-digit', minute: '2-digit',
    });
};

const formatDuration = (ms) => {
    if (!ms) return '—';
    const total = Math.round(ms / 1000);
    if (total < 60) return `${total} с`;
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return seconds ? `${minutes} мин ${seconds} с` : `${minutes} мин`;
};

const formatSize = (bytes) => {
    if (!bytes) return '—';
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
    return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
};

/* «Сколько ждать» — не гадание, а измеренный темп: около 140 запросов в минуту,
 * и цена определяется числом парков, а не числом строк. Пока парк не известен,
 * честнее говорить диапазоном, чем показывать точную неправду. */
const etaHint = (rowsTotal) => {
    if (!rowsTotal) return '';
    if (rowsTotal <= 500) return 'обычно меньше минуты, если водители из одного-двух парков';
    if (rowsTotal <= 3000) return 'обычно 2–4 минуты';
    if (rowsTotal <= 15000) return 'обычно 5–8 минут';
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
    const [checked, setChecked] = useState(null);

    const fileInput = useRef(null);
    const poll = useRef(null);

    const jobs = overview?.jobs || [];
    const session = overview?.session || {};
    const running = useMemo(() => jobs.find((job) => job.status === 'running'), [jobs]);
    const sessionReady = Boolean(session.configured) && !session.last_error;

    const load = useCallback(() => {
        setLoadError(null);
        return axios.get(`${base}/overview`, { headers: headers() })
            .then((response) => setOverview(response.data))
            .catch((error) => setLoadError(
                error?.response?.data?.error || 'Не удалось загрузить раздел',
            ));
    }, [base, headers]);

    useEffect(() => { load(); }, [load]);

    // Пока выгрузка идёт — подтягиваем карточку. Как только все закончились,
    // опрос прекращается сам: лишние запросы к списку никому не нужны.
    useEffect(() => {
        clearInterval(poll.current);
        if (running) poll.current = setInterval(load, POLL_MS);
        return () => clearInterval(poll.current);
    }, [running, load]);

    useEffect(() => () => clearInterval(poll.current), []);

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
            const payload = error?.response?.data || {};
            showToast?.(payload.error || 'Не удалось запустить выгрузку', 'error');
        } finally {
            setBusy('');
            if (fileInput.current) fileInput.current.value = '';
        }
    };

    const download = async (job) => {
        setBusy(`file:${job.id}`);
        try {
            const response = await axios.get(`${base}/jobs/${job.id}/file`, {
                headers: headers(), responseType: 'blob',
            });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = job.file_name || `Провайдер ЭДО ${job.id}.xlsx`;
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

    const checkSession = async () => {
        setBusy('session');
        try {
            const response = await axios.post(`${base}/session/check`, {}, { headers: headers() });
            setChecked(response.data);
            showToast?.(
                response.data?.alive
                    ? `Сессия жива: ${response.data.account || 'аккаунт'}, ${response.data.parks_count} парков`
                    : 'Сессия кабинета не отвечает — нужен новый вход',
                response.data?.alive ? 'success' : 'error',
            );
            await load();
        } catch (error) {
            showToast?.('Не удалось проверить сессию', 'error');
        } finally {
            setBusy('');
        }
    };

    const onDrop = (event) => {
        event.preventDefault();
        setDragging(false);
        upload(event.dataTransfer?.files?.[0]);
    };

    return (
        <div className="p-4 sm:p-6 space-y-5" style={{ fontFamily: APPLE_FONT }}>
            <header className="space-y-1">
                <h1 className="text-[22px] font-bold text-slate-900">Провайдер ЭДО</h1>
                <p className="text-[13px] text-slate-500">
                    Загрузите список водителей — вернём тот же список с колонкой «Провайдер ЭДО»
                    из диспетчерских Яндекс.Fleet.
                </p>
            </header>

            {loadError && (
                <div className="rounded-2xl bg-red-50 ring-1 ring-red-200 px-4 py-3 text-[13px] text-red-700">
                    {loadError}
                </div>
            )}

            {/* Состояние сессии — первым экраном: без неё выгрузка не поедет. */}
            <IosSection
                title="Кабинет Fleet"
                right={(
                    <button type="button" className={iosBtnGhost} onClick={checkSession}
                            disabled={busy === 'session'}>
                        {busy === 'session'
                            ? <Loader2 size={14} className="animate-spin" />
                            : <RefreshCw size={14} />}
                        Проверить сессию
                    </button>
                )}
            >
                <div className="flex flex-wrap items-center gap-3">
                    {sessionReady
                        ? <ShieldCheck size={20} className="text-emerald-500" />
                        : <ShieldAlert size={20} className="text-amber-500" />}
                    <div className="flex-1 min-w-[220px]">
                        <div className="text-[14px] font-semibold text-slate-900">
                            {session.configured
                                ? (session.account || 'Сессия настроена')
                                : 'Сессия не настроена'}
                        </div>
                        <div className="text-[12px] text-slate-500">
                            {session.configured
                                ? `Диспетчерских: ${session.parks_count ?? '—'} · обновлена ${formatDateTime(session.updated_at)}`
                                : 'Раздел ходит в кабинет под живым логином — его нужно передать один раз.'}
                        </div>
                    </div>
                    {checked && (
                        <IosBadge tone={checked.alive ? 'green' : 'red'}>
                            {checked.alive ? 'Проверено: жива' : 'Проверено: не отвечает'}
                        </IosBadge>
                    )}
                </div>
                {session.last_error && (
                    <div className="rounded-xl bg-amber-50 px-3 py-2 text-[12.5px] text-amber-800">
                        {session.last_error}
                    </div>
                )}
                {overview?.can_manage_session && (
                    <div className="rounded-xl bg-slate-50 px-3 py-2 text-[12px] text-slate-600 space-y-1">
                        <div className="flex items-center gap-1.5 font-semibold text-slate-700">
                            <Info size={13} /> Как обновить сессию
                        </div>
                        <div>
                            На машине, где есть браузер, выполните{' '}
                            <code className="rounded bg-white px-1.5 py-0.5 ring-1 ring-slate-200">
                                python scripts/fleet_edm_push_session.py
                            </code>{' '}
                            и войдите в кабинет в открывшемся окне. Скрипт сам передаст сессию сюда.
                        </div>
                    </div>
                )}
            </IosSection>

            {/* Загрузка файла */}
            <IosSection title="Новая выгрузка">
                <div
                    onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={onDrop}
                    onClick={() => fileInput.current?.click()}
                    className={`flex flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-4 py-8 text-center cursor-pointer transition ${
                        dragging ? 'border-blue-400 bg-blue-50/60' : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                    } ${(busy === 'upload' || running) ? 'pointer-events-none opacity-60' : ''}`}
                >
                    {busy === 'upload'
                        ? <Loader2 size={26} className="animate-spin text-blue-500" />
                        : <UploadCloud size={26} className="text-slate-400" />}
                    <div className="text-[14px] font-semibold text-slate-800">
                        {running ? 'Идёт другая выгрузка' : 'Перетащите файл или нажмите, чтобы выбрать'}
                    </div>
                    <div className="text-[12px] text-slate-500 max-w-[520px]">
                        Excel или CSV со столбцом «Contractor ID» (подойдёт «ID водителя»).
                        Если в файле есть «ID парка» — выгрузка идёт в разы быстрее: без него
                        каждого водителя приходится искать по всем диспетчерским.
                    </div>
                    <input
                        ref={fileInput} type="file" accept=".xlsx,.xlsm,.csv" className="hidden"
                        onChange={(event) => upload(event.target.files?.[0])}
                    />
                </div>
                {!sessionReady && (
                    <div className="rounded-xl bg-amber-50 px-3 py-2 text-[12.5px] text-amber-800">
                        Пока сессия кабинета не в порядке, выгрузка не сможет получить данные.
                    </div>
                )}
            </IosSection>

            {/* Список выгрузок */}
            <section className="space-y-1.5">
                <div className="flex items-end justify-between gap-2">
                    <div className={iosGroupLabel}>Выгрузки</div>
                    <button type="button" className={iosBtnGhost} onClick={load}>
                        <RefreshCw size={14} /> Обновить
                    </button>
                </div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    {!overview && !loadError && (
                        <div className="flex items-center gap-2 px-4 py-6 text-[13px] text-slate-500">
                            <Loader2 size={16} className="animate-spin" /> Загружаем…
                        </div>
                    )}
                    {overview && jobs.length === 0 && (
                        <div className="px-4 py-8 text-center text-[13px] text-slate-500">
                            Выгрузок ещё не было
                        </div>
                    )}
                    {jobs.map((job) => (
                        <JobRow
                            key={job.id} job={job}
                            busy={busy === `file:${job.id}`}
                            onDownload={() => download(job)}
                        />
                    ))}
                </div>
            </section>
        </div>
    );
}

function JobRow({ job, busy, onDownload }) {
    const meta = STATUS_META[job.status] || { label: job.status, tone: 'slate' };
    const stats = job.stats || {};
    const check = stats.check || {};
    const percent = Math.max(0, Math.min(100, job.progress_percent || 0));

    return (
        <div className="px-4 py-3 space-y-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <FileSpreadsheet size={16} className="text-slate-400 shrink-0" />
                <div className="flex-1 min-w-[200px]">
                    <div className="text-[14px] font-semibold text-slate-900 truncate">
                        {job.source_name || `Выгрузка №${job.id}`}
                    </div>
                    <div className="text-[12px] text-slate-500">
                        {formatDateTime(job.created_at)}
                        {job.created_by_name ? ` · ${job.created_by_name}` : ''}
                        {job.rows_total ? ` · ${job.rows_total.toLocaleString('ru-RU')} строк` : ''}
                    </div>
                </div>
                <IosBadge tone={meta.tone}>{meta.label}</IosBadge>
                {job.status === 'done' && job.has_file && (
                    <button type="button" className={iosBtnPrimary} onClick={onDownload} disabled={busy}>
                        {busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                        Скачать
                    </button>
                )}
            </div>

            {job.status === 'running' && (
                <div className="space-y-1">
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-blue-500 transition-all duration-500"
                             style={{ width: `${percent}%` }} />
                    </div>
                    <div className="text-[12px] text-slate-500">
                        {job.progress_note || 'Идёт обход диспетчерских'}
                        {job.rows_total ? ` · ${etaHint(job.rows_total)}` : ''}
                    </div>
                </div>
            )}

            {job.status === 'error' && (
                <div className="flex items-start gap-2 rounded-xl bg-red-50 px-3 py-2 text-[12.5px] text-red-700">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <div>
                        <div>{ERROR_HINTS[job.error_code] || 'Выгрузка не удалась'}</div>
                        {job.error && <div className="text-red-600/80 mt-0.5">{job.error}</div>}
                    </div>
                </div>
            )}

            {job.status === 'done' && (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-500">
                    <span className="inline-flex items-center gap-1 text-emerald-600">
                        <CheckCircle2 size={13} />
                        Провайдер определён: {(job.rows_resolved || 0).toLocaleString('ru-RU')}
                        {job.rows_total ? ` из ${job.rows_total.toLocaleString('ru-RU')}` : ''}
                    </span>
                    {check.checked > 0 && (
                        <span title="Случайные строки перепроверены по карточкам водителей — независимым путём">
                            Сверка: {check.matched} из {check.checked} совпало
                        </span>
                    )}
                    {stats.from_card > 0 && <span>добрано карточками: {stats.from_card}</span>}
                    <span>{formatDuration(job.duration_ms)}</span>
                    <span>{(job.requests_count || 0).toLocaleString('ru-RU')} запросов</span>
                    <span>{formatSize(job.file_size)}</span>
                </div>
            )}
        </div>
    );
}

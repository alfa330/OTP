import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Download, FileDown, FileSpreadsheet, Loader2, Trash2, Upload } from 'lucide-react';
import { iosBtnGhost, iosCard } from '../ui/ios';
import { GroupLabel, StatusPill } from './baigaUi';
import {
    fileNameFromDisposition, fmtStamp, formatInt, periodLabel, plural, prizeLabel, weekShort,
} from './baigaMeta';

/*
 * Журнал раздела (постановка #356, п. 2 и п. 4): кто, когда и что загрузил,
 * кто и что выгрузил. Видит аналитик — тот, кто ведёт недели.
 *
 * Раскладка — iOS «inset grouped», как «Настройки»: подпись группы над
 * карточкой, строки со значком слева. Из журнала же — скачать исходник недели
 * и удалить неделю. Удаление в два шага прямо в строке: «Удалить» →
 * «Удалить неделю из поиска? Отмена / Удалить», как в карточке посылки.
 */

const KIND_LABELS = {
    sheets: 'Excel по зачётам',
    single: 'Excel одним листом',
};

const FILTER_NAMES = {
    q: 'поиск', period: 'неделя', zachet: 'зачёт', city: 'город', park: 'таксопарк', prize: 'приз',
    position_min: 'позиция от', position_max: 'позиция до', amount_min: 'сумма от', amount_max: 'сумма до',
    trips_min: 'поездок от', trips_max: 'поездок до', prize_amount_min: 'приз от', prize_amount_max: 'приз до',
};

const filtersText = (filters) => {
    const parts = Object.entries(filters || {}).map(([key, value]) => {
        if (key === 'list') return `список из ${formatInt(value?.count || 0)}`;
        if (key === 'prize') return `приз: ${prizeLabel(value)}`;
        if (key === 'period') return `неделя ${weekShort(value)}`;
        return `${FILTER_NAMES[key] || key}: ${value}`;
    });
    return parts.length ? parts.join(' · ') : 'без фильтров';
};

const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;

/* Ошибка скачивания приходит Blob'ом (responseType: 'blob') — причину достаём
   сами: «исходника больше нет» важнее общего «не удалось». */
const blobErrorOf = async (requestError, fallback) => {
    const data = requestError?.response?.data;
    if (data && typeof data.text === 'function') {
        try {
            return JSON.parse(await data.text())?.error || fallback;
        } catch (error) {
            return fallback;
        }
    }
    return errorOf(requestError, fallback);
};

/* Значок строки в стиле «Настроек»: маленькая плитка слева. */
const RowIcon = ({ icon: Icon, tone = 'slate' }) => (
    <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-[9px] ${
        tone === 'blue' ? 'bg-blue-50 text-blue-600' : tone === 'green' ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-500'
    }`}>
        <Icon size={16} />
    </span>
);

const BaigaJournal = ({ apiBaseUrl, headers, toast, onChanged, refreshKey = 0 }) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [confirmId, setConfirmId] = useState(null);
    const [busyId, setBusyId] = useState(null);

    const load = useCallback(() => {
        setError('');
        return axios.get(`${apiBaseUrl}/api/baiga/journal`, { headers: headers() })
            .then((response) => setData(response.data || { uploads: [], exports: [] }))
            .catch((requestError) => setError(errorOf(requestError, 'Не удалось открыть журнал')));
    }, [apiBaseUrl, headers]);

    // refreshKey — загрузка недели из шапки, пока открыт журнал: без него новая
    // загрузка не появлялась, а заменённая неделя оставалась «В поиске».
    useEffect(() => { load(); }, [load, refreshKey]);

    const remove = async (upload) => {
        setBusyId(upload.id);
        try {
            await axios.delete(`${apiBaseUrl}/api/baiga/uploads/${upload.id}`, { headers: headers() });
            toast(`Неделя ${weekShort(upload.period_start, upload.period_end)} удалена`, 'success');
            setConfirmId(null);
            await load();
            onChanged?.();
        } catch (requestError) {
            toast(errorOf(requestError, 'Не удалось удалить неделю'), 'error');
        } finally {
            setBusyId(null);
        }
    };

    const downloadSource = async (upload) => {
        setBusyId(upload.id);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/baiga/uploads/${upload.id}/file`, {
                headers: headers(), responseType: 'blob',
            });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = fileNameFromDisposition(response.headers?.['content-disposition'], upload.file_name);
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            load();
        } catch (requestError) {
            toast(await blobErrorOf(requestError, 'Не удалось скачать исходник'), 'error');
            load();
        } finally {
            setBusyId(null);
        }
    };

    if (error) {
        return <div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>;
    }
    if (!data) {
        return (
            <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-500">
                <Loader2 size={15} className="animate-spin" /> Открываем журнал…
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <section>
                <GroupLabel>Загрузки</GroupLabel>
                <div className={`${iosCard} overflow-hidden`}>
                    {!data.uploads.length && (
                        <div className="px-4 py-10 text-center text-[13.5px] text-slate-500">Загрузок ещё не было</div>
                    )}
                    <ul className="divide-y divide-slate-100">
                        {data.uploads.map((upload) => {
                            const active = upload.status === 'active';
                            const confirming = confirmId === upload.id;
                            return (
                                <li key={upload.id} className="flex flex-col gap-2 px-4 py-3 md:flex-row md:items-center md:gap-4">
                                    <div className="flex min-w-0 grow basis-0 items-center gap-3">
                                        <RowIcon icon={Upload} tone={active ? 'green' : 'slate'} />
                                        <div className="min-w-0 grow basis-0">
                                            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                                <span className={`text-[14px] font-semibold ${active ? 'text-slate-900' : 'text-slate-500'}`}>
                                                    {upload.week_number ? `Неделя ${upload.week_number} · ` : ''}
                                                    {periodLabel(upload.period_start, upload.period_end)}
                                                </span>
                                                <StatusPill status={upload.status} />
                                            </div>
                                            <div className="mt-0.5 truncate text-[12.5px] text-slate-500" title={upload.file_name}>
                                                {fmtStamp(upload.uploaded_at)} · {upload.uploaded_by_name || '—'}
                                                {' · '}{formatInt(upload.rows_count)} {plural(upload.rows_count, 'строка', 'строки', 'строк')}
                                                {' · '}{upload.file_name}
                                            </div>
                                            {upload.closed_at && (
                                                <div className="text-[12px] text-slate-400">
                                                    {upload.status === 'replaced' ? 'Заменена' : 'Удалена'} {fmtStamp(upload.closed_at)}
                                                    {upload.closed_by_name ? ` · ${upload.closed_by_name}` : ''}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                    {active && (
                                        <div className="flex shrink-0 flex-wrap items-center gap-1 pl-11 md:pl-0">
                                            {confirming ? (
                                                <>
                                                    <span className="mr-1 text-[12.5px] text-slate-600">Удалить неделю из поиска?</span>
                                                    <button type="button" className={iosBtnGhost} onClick={() => setConfirmId(null)}>
                                                        Отмена
                                                    </button>
                                                    <button
                                                        type="button"
                                                        className="inline-flex items-center justify-center gap-1.5 rounded-xl bg-red-600 px-3 py-2 text-[13px] font-semibold text-white transition hover:bg-red-700 active:scale-[0.98] disabled:opacity-50"
                                                        disabled={busyId === upload.id}
                                                        onClick={() => remove(upload)}
                                                    >
                                                        {busyId === upload.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                                                        Удалить
                                                    </button>
                                                </>
                                            ) : (
                                                <>
                                                    {upload.has_file && (
                                                        <button type="button" className={iosBtnGhost} disabled={busyId === upload.id}
                                                                onClick={() => downloadSource(upload)}>
                                                            <Download size={14} /> Исходник
                                                        </button>
                                                    )}
                                                    <button type="button" className={`${iosBtnGhost} !text-red-600 hover:bg-red-50`}
                                                            onClick={() => setConfirmId(upload.id)}>
                                                        <Trash2 size={14} /> Удалить
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                    )}
                                </li>
                            );
                        })}
                    </ul>
                </div>
            </section>

            <section>
                <GroupLabel>Выгрузки</GroupLabel>
                <div className={`${iosCard} overflow-hidden`}>
                    {!data.exports.length && (
                        <div className="px-4 py-10 text-center text-[13.5px] text-slate-500">Выгрузок ещё не было</div>
                    )}
                    <ul className="divide-y divide-slate-100">
                        {data.exports.map((entry) => {
                            const source = entry.kind === 'source';
                            return (
                                <li key={entry.id} className="flex items-center gap-3 px-4 py-3">
                                    <RowIcon icon={source ? FileDown : FileSpreadsheet} tone={source ? 'slate' : 'blue'} />
                                    <div className="min-w-0 grow basis-0">
                                        <div className="flex flex-wrap items-baseline gap-x-2 text-[13.5px]">
                                            <span className="font-medium text-slate-800">
                                                {source
                                                    ? `Исходник недели ${weekShort(entry.upload_period_start, entry.upload_period_end) || ''}`.trim()
                                                    : KIND_LABELS[entry.mode] || 'Выгрузка'}
                                            </span>
                                            <span className="tabular-nums text-slate-500">
                                                {formatInt(entry.rows_count)} {plural(entry.rows_count, 'строка', 'строки', 'строк')}
                                            </span>
                                        </div>
                                        <div className="mt-0.5 truncate text-[12.5px] text-slate-500">
                                            {fmtStamp(entry.created_at)} · {entry.actor_name || '—'}
                                            {!source && ` · ${filtersText(entry.filters)}`}
                                        </div>
                                    </div>
                                </li>
                            );
                        })}
                    </ul>
                </div>
            </section>
        </div>
    );
};

export default BaigaJournal;

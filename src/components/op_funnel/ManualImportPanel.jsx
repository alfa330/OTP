import React, { useCallback, useEffect, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { IosBadge, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import { formatMoment, formatNumber, shortDay } from './funnelFormat';

/**
 * Загрузка выгрузки СРМ для направления «Верификатор».
 *
 * Зачем она нужна, если тикеты уже тянутся сами. Обращения приходят ручкой
 * `/api/partners/list-tickets` (её завели по нашей заявке 11.09.2026), но видны
 * только те, что заведены под учётными записями партнёра: на день подключения в
 * этот периметр входила одна учётка, и за три с половиной месяца пришло 215
 * обращений — все от «ИИ Агента». Пока периметр не расширят, автоматический счёт
 * занижен, и закрыть разницу можно только файлом.
 *
 * Вторая причина, которая останется и потом: часами. У верификаторов нет
 * статусов телефона, часы берутся по графику смен, и поправить фактические можно
 * только руками.
 *
 * Что важно в интерфейсе: показать РЕЗУЛЬТАТ разбора до того, как человек
 * начнёт искать расхождения в числах. Прочитано строк, записано, пропущено — и
 * отдельно имена, которые не удалось узнать: «Аман Алан» в файле против «Алан
 * Аман Нурбекұлы» в портале автомат не свяжет и не должен, но человек свяжет за
 * минуту — если увидит, что связывать.
 */

export default function ManualImportPanel({ api, direction, showToast, onImported }) {
    const [file, setFile] = useState(null);
    const [sending, setSending] = useState(false);
    const [result, setResult] = useState(null);
    const [history, setHistory] = useState([]);
    const [error, setError] = useState('');
    const inputRef = useRef(null);

    const toastRef = useRef(showToast);
    toastRef.current = showToast;
    const importedRef = useRef(onImported);
    importedRef.current = onImported;

    const loadHistory = useCallback(async () => {
        if (!api || !direction) return;
        try {
            const data = await api.runs({ direction });
            if (data === null) return;
            setHistory(data.imports || []);
        } catch (exc) {
            // Журнал загрузок — не главное на экране: молчим в интерфейсе, но не
            // делаем вид, что загрузка прошла.
            setHistory([]);
        }
    }, [api, direction]);

    useEffect(() => { loadHistory(); }, [loadHistory]);

    const send = async () => {
        if (!file) return;
        setSending(true);
        setError('');
        setResult(null);
        try {
            const data = await api.manualImport({ direction, file });
            setResult(data);
            if (data?.rows_written) {
                toastRef.current?.(
                    `Загружено строк: ${data.rows_written}. Числа появятся после обновления данных за этот период.`,
                    'success',
                );
                importedRef.current?.(data);
            } else {
                toastRef.current?.('Ни одной строки записать не удалось — смотрите разбор ниже', 'warning');
            }
            setFile(null);
            if (inputRef.current) inputRef.current.value = '';
            await loadHistory();
        } catch (exc) {
            setError(exc?.message || 'Не удалось загрузить файл');
            toastRef.current?.(exc?.message || 'Не удалось загрузить файл', 'error');
        } finally {
            setSending(false);
        }
    };

    return (
        <section className="rounded-2xl bg-white ring-1 ring-slate-200/70 shadow-sm p-4 sm:p-5">
            <header className="mb-3">
                <h3 className="text-[15px] font-semibold text-slate-900">Выгрузка тикетов и часов</h3>
                <p className="text-xs text-slate-500 mt-1">
                    Тикеты подтягиваются из СРМ сами, но пока видны только обращения,
                    заведённые под учётными записями партнёра — счёт может быть занижен.
                    Часы у верификаторов система берёт по графику смен. Загруженный файл
                    сильнее автоматики: строки за те же сутки будут исправлены, а не удвоены.
                </p>
            </header>

            <div className="flex flex-wrap items-center gap-2">
                <input
                    ref={inputRef}
                    type="file"
                    accept=".xlsx,.xlsm"
                    onChange={(event) => setFile(event.target.files?.[0] || null)}
                    className="text-[13px] text-slate-600 file:mr-3 file:rounded-xl file:border-0
                               file:bg-slate-100 file:px-3 file:py-1.5 file:text-[13px] file:text-slate-700
                               hover:file:bg-slate-200"
                />
                <button
                    type="button"
                    disabled={!file || sending}
                    onClick={send}
                    className={`${file && !sending ? iosBtnPrimary : iosBtnSecondary} px-4 py-2 text-[13px]`}
                >
                    {sending ? 'Загружаем…' : 'Загрузить'}
                </button>
            </div>

            {error ? (
                <div className="mt-3 rounded-xl bg-rose-50 ring-1 ring-rose-200 p-3 text-sm text-rose-700">
                    {error}
                </div>
            ) : null}

            {result ? (
                <div className="mt-4 rounded-xl bg-slate-50 ring-1 ring-slate-200/70 p-3">
                    <div className="flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-slate-700">
                        <span>Прочитано строк: <b className="tabular-nums">{formatNumber(result.rows_read)}</b></span>
                        <span>Записано: <b className="tabular-nums">{formatNumber(result.rows_written)}</b></span>
                        <span>Пропущено: <b className="tabular-nums">{formatNumber(result.rows_skipped)}</b></span>
                        {result.period?.from ? (
                            <span className="text-slate-500">
                                период {shortDay(result.period.from)} — {shortDay(result.period.to)}
                            </span>
                        ) : null}
                    </div>
                    {result.rows_skipped ? (
                        <p className="mt-1.5 text-[11px] text-slate-500">
                            Пропущены пустые строки операторов, которые в этот день не работали,
                            и строки с неузнанным именем.
                        </p>
                    ) : null}

                    {(result.unmapped || []).length ? (
                        <div className="mt-3">
                            <div className="text-[13px] font-medium text-amber-800">
                                Не узнали {result.unmapped.length} имён
                            </div>
                            <p className="text-[11px] text-slate-500 mt-0.5">
                                Свяжите их с сотрудниками в блоке «Сопоставление операторов» выше —
                                автоматически связывать такие имена нельзя: ошибка поставит чужие
                                часы в чужую строку, и в отчёте это не видно.
                            </p>
                            <ul className="mt-2 flex flex-wrap gap-1.5">
                                {result.unmapped.map((item) => (
                                    <li key={item.name}>
                                        <IosBadge tone="amber">
                                            {item.name} · {formatNumber(item.rows)}
                                        </IosBadge>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ) : null}
                </div>
            ) : null}

            {history.length ? (
                <div className="mt-4">
                    <div className="text-[13px] font-medium text-slate-700 mb-1.5">Прошлые загрузки</div>
                    <ul className="space-y-1">
                        {history.map((item) => (
                            <li
                                key={item.id}
                                className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[12px] text-slate-600"
                            >
                                <span className="text-slate-400 tabular-nums">
                                    {formatMoment(item.uploaded_at)}
                                </span>
                                <span className="truncate max-w-[220px]">{item.file_name || 'без имени'}</span>
                                <span className="tabular-nums">
                                    записано {formatNumber(item.rows_written)} из {formatNumber(item.rows_read)}
                                </span>
                                {item.uploaded_by ? (
                                    <span className="text-slate-400">{item.uploaded_by}</span>
                                ) : null}
                                {(item.unmapped || []).length ? (
                                    <IosBadge tone="amber">
                                        не узнано {item.unmapped.length}
                                    </IosBadge>
                                ) : null}
                            </li>
                        ))}
                    </ul>
                </div>
            ) : null}

            {!history.length && !result ? (
                <p className="mt-4 text-center text-xs text-slate-400">
                    <FaIcon className="fas fa-file-arrow-up mr-1.5" />
                    Загрузок ещё не было
                </p>
            ) : null}
        </section>
    );
}

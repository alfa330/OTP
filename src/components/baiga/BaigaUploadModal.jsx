import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { AlertTriangle, ChevronDown, FileSpreadsheet, Loader2, RefreshCw, Upload, XCircle } from 'lucide-react';
import { IosModal, iosBtnGhost, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import { fmtStamp, formatInt, formatMoney, periodLabel, plural } from './baigaMeta';

/*
 * Загрузка недели (постановка #356, п. 4): файл → предпросмотр → загрузка.
 *
 * Предпросмотр делает сервер (POST /uploads/preview) и ничего не пишет: период,
 * листы с числом строк, ошибки и предупреждения с листом и строкой Excel. С
 * ошибками кнопки «Загрузить» нет — исправляют файл, а не загрузку. Если
 * неделя уже есть, кнопка называется «Заменить неделю» и рядом сказано, кто и
 * когда её загрузил: замену подтверждают глядя на то, что заменяется.
 *
 * Файл уходит на сервер дважды (предпросмотр и загрузка) — намеренно: сервер
 * разбирает его заново и не верит экрану, а неделя весит ~140 КБ.
 */

const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;

const where = (item) => [
    item.sheet && `Лист «${item.sheet}»`,
    item.row && `строка ${item.row}`,
    item.column,
].filter(Boolean).join(' · ');

const IssueList = ({ items, more = 0 }) => (
    <ul className="mt-2 space-y-1 text-[12.5px]">
        {items.map((item, index) => (
            // eslint-disable-next-line react/no-array-index-key
            <li key={index} className="flex flex-wrap gap-x-2 leading-snug">
                {where(item) && <span className="text-slate-500">{where(item)}</span>}
                <span className="text-slate-800">{item.message}</span>
            </li>
        ))}
        {more > 0 && <li className="text-slate-500">и ещё {formatInt(more)}</li>}
    </ul>
);

const WarningGroup = ({ group }) => {
    const [open, setOpen] = useState(false);
    return (
        <div className="rounded-xl bg-amber-50/70 px-3 py-2 ring-1 ring-amber-200/70">
            <button type="button" onClick={() => setOpen((prev) => !prev)}
                    className="flex w-full items-center gap-2 text-left text-[13px] text-amber-900">
                <AlertTriangle size={14} className="shrink-0 text-amber-500" />
                <span className="font-medium">{group.label}</span>
                <span className="tabular-nums text-amber-700">{formatInt(group.count)}</span>
                <ChevronDown size={14} className={`ml-auto shrink-0 text-amber-500 transition ${open ? 'rotate-180' : ''}`} />
            </button>
            {open && <IssueList items={group.items} more={group.count - group.items.length} />}
        </div>
    );
};

const BaigaUploadModal = ({ open, onClose, apiBaseUrl, headers, maxUploadMb = 20, onUploaded, toast }) => {
    const [file, setFile] = useState(null);
    const [preview, setPreview] = useState(null);
    const [phase, setPhase] = useState('pick'); // pick | checking | preview | uploading
    const [error, setError] = useState('');
    const [dragging, setDragging] = useState(false);
    const inputRef = useRef(null);
    // Номер проверки: ответ предпросмотра относится к ТОМУ файлу и ТОМУ
    // открытию окна, ради которых его спрашивали. Окно закрыли или бросили
    // другой файл — старый ответ не должен лечь на экран (и подсунуть чужую
    // плашку «неделя уже загружена» к другому файлу).
    const checkSeq = useRef(0);

    useEffect(() => {
        if (open) return;
        checkSeq.current += 1;
        setFile(null);
        setPreview(null);
        setPhase('pick');
        setError('');
        setDragging(false);
    }, [open]);

    const form = (chosen, extra = {}) => {
        const data = new FormData();
        data.append('file', chosen);
        // Имя отдельным полем: в нём период недели, и терять кириллицу по
        // дороге (как теряет её secure_filename) серверу нельзя.
        data.append('file_name', chosen.name);
        Object.entries(extra).forEach(([key, value]) => data.append(key, value));
        return data;
    };

    const check = async (chosen) => {
        if (!chosen) return;
        setFile(chosen);
        setPreview(null);
        setError('');
        if (chosen.size > maxUploadMb * 1024 * 1024) {
            setPhase('pick');
            setError(`Файл больше ${maxUploadMb} МБ`);
            return;
        }
        setPhase('checking');
        checkSeq.current += 1;
        const id = checkSeq.current;
        try {
            const response = await axios.post(`${apiBaseUrl}/api/baiga/uploads/preview`, form(chosen),
                { headers: headers() });
            if (id !== checkSeq.current) return;
            setPreview(response.data);
            setPhase('preview');
        } catch (requestError) {
            if (id !== checkSeq.current) return;
            setPhase('pick');
            setError(errorOf(requestError, 'Не удалось проверить файл'));
        }
    };

    const upload = async () => {
        if (!file || !preview?.ok || phase === 'uploading') return;
        setPhase('uploading');
        setError('');
        try {
            const response = await axios.post(`${apiBaseUrl}/api/baiga/uploads`,
                form(file, preview.existing ? { replace: '1' } : {}), { headers: headers() });
            const rows = response.data?.upload?.rows_count ?? preview.stats.rows;
            toast(`${response.data?.replaced ? 'Неделя заменена' : 'Неделя загружена'}: ${formatInt(rows)} ${plural(rows, 'строка', 'строки', 'строк')}`,
                'success');
            onUploaded?.();
            onClose();
        } catch (requestError) {
            const body = requestError?.response?.data;
            if (body?.code === 'BAIGA_WEEK_EXISTS' && body.existing) {
                // Пока смотрели предпросмотр, неделю загрузил коллега: показываем,
                // что именно заменится, и ждём второго нажатия.
                setPreview((prev) => ({ ...prev, existing: body.existing }));
            } else if (body?.preview) {
                setPreview(body.preview);
            }
            setPhase('preview');
            setError(errorOf(requestError, 'Не удалось загрузить неделю'));
        }
    };

    const pick = () => inputRef.current?.click();

    const period = preview?.period;
    const stats = preview?.stats;
    const existing = preview?.existing;
    const busy = phase === 'checking' || phase === 'uploading';

    const footer = (
        <>
            {phase === 'preview' && (
                <button type="button" className={`${iosBtnGhost} mr-auto`} onClick={pick} disabled={busy}>
                    <RefreshCw size={14} /> Другой файл
                </button>
            )}
            <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={phase === 'uploading'}>
                Отмена
            </button>
            {(phase === 'preview' || phase === 'uploading') && (
                <button type="button" className={iosBtnPrimary} onClick={upload} disabled={!preview?.ok || busy}>
                    {phase === 'uploading' ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
                    {existing ? 'Заменить неделю' : 'Загрузить'}
                </button>
            )}
        </>
    );

    return (
        <IosModal
            open={open}
            onClose={phase === 'uploading' ? () => {} : onClose}
            title="Загрузить неделю"
            subtitle={file ? file.name : 'Файл Excel с итогами Байги'}
            maxWidth="max-w-2xl"
            footer={footer}
        >
            <input
                ref={inputRef}
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                className="hidden"
                onChange={(event) => { check(event.target.files?.[0]); event.target.value = ''; }}
            />

            {(phase === 'pick' || phase === 'checking') && (
                <div
                    onDragOver={(event) => { event.preventDefault(); if (phase !== 'checking') setDragging(true); }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(event) => {
                        event.preventDefault();
                        setDragging(false);
                        // Пока проверяется один файл, второй не принимаем: две
                        // проверки наперегонки показали бы итоги не того файла.
                        if (phase !== 'checking') check(event.dataTransfer.files?.[0]);
                    }}
                    className={`flex flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition ${
                        dragging ? 'border-blue-400 bg-blue-50/60' : 'border-slate-200 bg-slate-50/60'
                    }`}
                >
                    {phase === 'checking' ? (
                        <>
                            <Loader2 size={26} className="animate-spin text-slate-400" />
                            <div className="text-[13.5px] text-slate-600">Проверяем файл…</div>
                        </>
                    ) : (
                        <>
                            <FileSpreadsheet size={30} className="text-slate-400" />
                            <div className="text-[14px] text-slate-700">Перетащите файл недели сюда</div>
                            <button type="button" className={iosBtnPrimary} onClick={pick}>Выбрать файл</button>
                            <div className="text-[12px] text-slate-500">
                                Excel .xlsx до {maxUploadMb} МБ · период берётся из имени файла
                            </div>
                        </>
                    )}
                </div>
            )}

            {error && (
                <div className="mt-3 rounded-xl bg-rose-50 px-3.5 py-2.5 text-[13px] text-rose-700 ring-1 ring-rose-200">
                    {error}
                </div>
            )}

            {preview && phase !== 'pick' && phase !== 'checking' && (
                <div className="space-y-3">
                    {period && (
                        <div>
                            <div className="text-[16px] font-semibold text-slate-900">
                                {period.week ? `Неделя ${period.week} · ` : ''}{periodLabel(period.start, period.end)}
                                {' '}{period.end?.slice(0, 4)}
                            </div>
                            {stats && (
                                <div className="mt-0.5 text-[13px] tabular-nums text-slate-600">
                                    {formatInt(stats.rows)} {plural(stats.rows, 'строка', 'строки', 'строк')}
                                    {' · '}{formatInt(stats.drivers)} {plural(stats.drivers, 'водитель', 'водителя', 'водителей')}
                                    {stats.prize_rows > 0 && ` · призов ${formatInt(stats.prize_rows)} на ${formatMoney(stats.prize_total)}`}
                                </div>
                            )}
                        </div>
                    )}

                    {existing && (
                        <div className="rounded-xl bg-blue-50 px-3.5 py-2.5 text-[13px] text-blue-900 ring-1 ring-blue-200">
                            {existing.same_file
                                ? 'Этот же файл уже загружен'
                                : 'Эта неделя уже загружена'}
                            {existing.uploaded_at && ` ${fmtStamp(existing.uploaded_at)}`}
                            {existing.uploaded_by_name && ` · ${existing.uploaded_by_name}`}
                            {` · ${formatInt(existing.rows_count)} ${plural(existing.rows_count, 'строка', 'строки', 'строк')}.`}
                            {' '}{existing.same_file ? 'Замена ничего не изменит.' : 'Новая загрузка заменит её целиком.'}
                        </div>
                    )}

                    {preview.errors_total > 0 && (
                        <div className="rounded-xl bg-rose-50/80 px-3.5 py-2.5 ring-1 ring-rose-200">
                            <div className="flex items-center gap-2 text-[13px] font-medium text-rose-800">
                                <XCircle size={15} className="shrink-0 text-rose-500" />
                                Ошибки — загрузка невозможна
                                <span className="tabular-nums text-rose-600">{formatInt(preview.errors_total)}</span>
                            </div>
                            <IssueList items={preview.errors} more={preview.errors_total - preview.errors.length} />
                        </div>
                    )}

                    {preview.warnings?.length > 0 && (
                        <div className="space-y-1.5">
                            {preview.warnings.map((group) => <WarningGroup key={group.kind} group={group} />)}
                        </div>
                    )}

                    {preview.sheets?.length > 0 && (
                        <div className="rounded-xl bg-slate-50 px-3.5 py-2.5 ring-1 ring-slate-200/70">
                            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                Зачёты
                            </div>
                            <ul className="grid gap-x-6 gap-y-1 text-[13px] sm:grid-cols-2">
                                {preview.sheets.map((sheet) => (
                                    <li key={sheet.name} className="flex min-w-0 items-baseline justify-between gap-3">
                                        <span className="truncate text-slate-700">{sheet.name}</span>
                                        <span className="shrink-0 tabular-nums text-slate-500">{formatInt(sheet.rows)}</span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>
            )}
        </IosModal>
    );
};

export default BaigaUploadModal;

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FileSpreadsheet, Upload } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { IosModal, iosBtnSecondary, iosCard } from '../ui/ios';
import { errorOf, plural, shortTime } from './hiringMeta';
import { Empty, Group, Label, Loading, Notice, TOOLBAR_BTN_PRIMARY } from './hiringUi';

/*
 * Вкладка «Загрузки» (ТЗ 5.1): база контактов файлом Excel / CSV с полями ФИО, телефон, город,
 * комментарий; источник выбирается один на весь файл. Телефоны приводятся к +7XXXXXXXXXX,
 * пустые и неразборные строки отбрасываются; итог — загружено, дублей, ошибок. Каждая
 * загрузка — партия: по ней считается эффективность баз в отчётах.
 */

const UploadTab = ({ api, toast, boot, onChanged, version }) => {
    const [sourceId, setSourceId] = useState('');
    const [file, setFile] = useState(null);
    const [uploading, setUploading] = useState(false);
    const [batches, setBatches] = useState(null);
    const [opened, setOpened] = useState(null);
    const inputRef = useRef(null);
    const sources = (boot.dictionaries?.sources || []).filter((s) => s.kind === 'contacts' && s.is_active !== false);

    const load = useCallback(() => api.get('/batches')
        .then(({ data }) => setBatches(data.items))
        .catch((error) => toast(errorOf(error, 'Не удалось загрузить партии'), 'error')), [api, toast]);
    useEffect(() => { load(); }, [load, version]);

    const upload = async () => {
        if (!file) return toast('Выберите файл', 'error');
        if (!sourceId) return toast('Выберите источник контактов', 'error');
        const form = new FormData();
        form.append('file', file);
        form.append('source_id', sourceId);
        setUploading(true);
        try {
            // Content-Type для FormData axios ставит сам (с границей частей); свой заголовок здесь не нужен.
            const { data } = await api.post('/upload', form);
            const batch = data.batch;
            toast(`Загружено ${batch.loaded}, дублей ${batch.duplicates}, ошибок ${batch.errors}`, 'success');
            setFile(null);
            if (inputRef.current) inputRef.current.value = '';
            setOpened(batch);
            onChanged();
        } catch (error) {
            toast(errorOf(error, 'Файл не загрузился'), 'error');
        } finally {
            setUploading(false);
        }
        return undefined;
    };

    const openBatch = async (id) => {
        try {
            const { data } = await api.get(`/batches/${id}`);
            setOpened(data.batch);
        } catch (error) {
            toast(errorOf(error, 'Партия не открылась'), 'error');
        }
    };

    return (
        <div className="space-y-4">
            <Group title="Загрузить базу контактов">
                <div className={`${iosCard} p-4`}>
                    <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
                        <div>
                            <Label required hint="Один источник на весь файл: по нему считается эффективность базы. Новые источники заводятся в «Настройках».">Источник контактов</Label>
                            <CustomSelect variant="ios" value={sourceId} onChange={setSourceId} placeholder="Выберите источник" invalid={!sourceId}
                                          options={sources.map((s) => ({ value: String(s.id), label: s.name }))} />
                        </div>
                        <div>
                            <Label hint="Excel (.xlsx) или CSV. Колонки узнаются по заголовкам: ФИО, телефон, город, комментарий; без заголовков — в этом порядке.">Файл</Label>
                            <label className="inline-flex h-[41px] cursor-pointer items-center gap-2 rounded-xl bg-slate-100 px-3.5 text-[13.5px] text-slate-700 hover:bg-slate-200/70">
                                <FileSpreadsheet size={15} className="text-slate-500" />
                                <span className="max-w-[220px] truncate">{file ? file.name : 'Выбрать файл'}</span>
                                <input ref={inputRef} type="file" accept=".xlsx,.xlsm,.csv,.txt" className="hidden" onChange={(e) => setFile(e.target.files?.[0] || null)} />
                            </label>
                        </div>
                        <button type="button" className={`${TOOLBAR_BTN_PRIMARY} h-[41px]`} onClick={upload} disabled={uploading || !file || !sourceId}>
                            <Upload size={14} /> {uploading ? 'Загружаем…' : 'Загрузить'}
                        </button>
                    </div>
                    {sources.length === 0 && (
                        <div className="mt-3"><Notice tone="amber" title="Нет источников контактов">Заведите источник вида «контакты» в «Настройках» — например, «База бывших водителей».</Notice></div>
                    )}
                </div>
            </Group>

            <Group title="Партии загрузок">
                <div className={`${iosCard} overflow-hidden`}>
                    {!batches ? <Loading text="Загружаем партии…" /> : batches.length === 0 ? <Empty>Загрузок ещё не было</Empty> : (
                        <ul className="divide-y divide-slate-100">
                            {batches.map((batch) => (
                                <li key={batch.id}>
                                    <button type="button" onClick={() => openBatch(batch.id)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-slate-50">
                                        <div className="min-w-0 grow basis-0">
                                            <div className="truncate text-[13.5px] font-medium text-slate-900">{batch.source_name} · {batch.file_name || 'файл'}</div>
                                            <div className="truncate text-[12px] text-slate-500">{batch.loaded_by_name || '—'} · {shortTime(batch.loaded_at)}</div>
                                        </div>
                                        <div className="shrink-0 text-right text-[12px] tabular-nums text-slate-500">
                                            <div><span className="font-semibold text-slate-900">{batch.loaded}</span> загружено</div>
                                            <div>{batch.duplicates} дублей · <span className={batch.errors ? 'text-rose-600' : ''}>{batch.errors} ошибок</span></div>
                                        </div>
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </Group>

            <IosModal open={Boolean(opened)} onClose={() => setOpened(null)} title={opened ? `Партия №${opened.id}` : ''}
                      subtitle={opened ? `${opened.source_name} · ${opened.file_name || 'файл'} · ${shortTime(opened.loaded_at)}` : ''}
                      footer={<div className="flex justify-end"><button type="button" className={iosBtnSecondary} onClick={() => setOpened(null)}>Закрыть</button></div>}>
                {opened && (
                    <div className="space-y-3 text-[13px]">
                        <div className="grid grid-cols-3 gap-2 text-center">
                            {[['Строк в файле', opened.total_rows], ['Загружено', opened.loaded], ['Дублей', opened.duplicates]].map(([label, value]) => (
                                <div key={label} className="rounded-xl bg-slate-50 px-2 py-2 ring-1 ring-slate-200/60">
                                    <div className="text-[18px] font-semibold tabular-nums text-slate-900">{value}</div>
                                    <div className="text-[11.5px] text-slate-500">{label}</div>
                                </div>
                            ))}
                        </div>
                        {opened.errors > 0 ? (
                            <div>
                                <div className="mb-1 text-[12px] font-medium text-slate-500">{opened.errors} {plural(opened.errors, 'строка', 'строки', 'строк')} с ошибкой</div>
                                <ul className="max-h-72 divide-y divide-slate-100 overflow-y-auto rounded-xl ring-1 ring-slate-200/60">
                                    {(opened.error_rows || []).map((row, index) => (
                                        <li key={`${row.row}-${index}`} className="flex gap-3 px-3 py-1.5">
                                            <span className="w-14 shrink-0 tabular-nums text-slate-400">стр. {row.row}</span>
                                            <span className="min-w-0 grow text-slate-700">{row.error}</span>
                                            <span className="shrink-0 tabular-nums text-slate-400">{row.value}</span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ) : <div className="text-slate-500">Ошибок нет — все строки с телефоном разобраны.</div>}
                        <div className="text-[12px] text-slate-500">Дубль — телефон, который уже есть в открытой карточке: в неё дописан «Повторный отклик». Телефоны действующих сотрудников в пул не попадают.</div>
                    </div>
                )}
            </IosModal>
        </div>
    );
};

export default UploadTab;

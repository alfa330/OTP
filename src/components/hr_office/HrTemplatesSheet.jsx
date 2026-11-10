import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Download, Loader2, RotateCcw, Upload } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import { IosModal, iosBtnGhost, iosCard, iosGroupLabel } from '../ui/ios';
import { blobErrorText, dateText, errorText } from './hrOffice';
import { Loading } from './hrUi';

/*
 * Шаблоны документов приема. Стандартные собраны из файлов HR: прочерки заменены
 * метками {{…}}, реквизиты компании — тоже метками. Свой шаблон HR загружает здесь:
 * текст договора меняется без выкладки; меток можно ставить сколько угодно из списка.
 */
export default function HrTemplatesSheet({ open = true, api, toast, onClose }) {
    const [templates, setTemplates] = useState(null);
    const [markers, setMarkers] = useState([]);
    const [busy, setBusy] = useState('');
    const [error, setError] = useState('');
    const fileRef = useRef(null);
    const kindRef = useRef('');

    const load = useCallback(() => api.get('/templates')
        .then(({ data }) => { setTemplates(data.templates || []); setMarkers(data.markers || []); })
        .catch((requestError) => setError(errorText(requestError, 'Шаблоны не загрузились'))), [api]);
    useEffect(() => { load(); }, [load]);

    const download = async (item) => {
        setBusy(`get:${item.kind}`);
        try {
            await api.download('get', `/templates/${item.kind}`, { fallback: `${item.title}.docx` });
        } catch (requestError) {
            toast?.(await blobErrorText(requestError, 'Шаблон не скачался'), 'error');
        } finally {
            setBusy('');
        }
    };
    const pick = (item) => {
        kindRef.current = item.kind;
        fileRef.current?.click();
    };
    const upload = async (event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (!file || !kindRef.current) return;
        setBusy(`put:${kindRef.current}`);
        try {
            const { data } = await api.upload(`/templates/${kindRef.current}`, file);
            toast?.(data.unknown?.length
                ? `Шаблон загружен. Незнакомые метки: ${data.unknown.join(', ')} — они останутся как есть`
                : 'Шаблон загружен', data.unknown?.length ? 'warning' : 'success');
            load();
        } catch (requestError) {
            toast?.(errorText(requestError, 'Шаблон не загрузился'), 'error');
        } finally {
            setBusy('');
        }
    };
    const reset = async (item) => {
        setBusy(`del:${item.kind}`);
        try {
            await api.del(`/templates/${item.kind}`);
            toast?.('Вернули стандартный шаблон', 'success');
            load();
        } catch (requestError) {
            toast?.(errorText(requestError, 'Не получилось'), 'error');
        } finally {
            setBusy('');
        }
    };

    return (
        <IosModal open={open} onClose={onClose} maxWidth="max-w-2xl" title="Шаблоны документов" subtitle="Пакет приема по ГПХ и СМЗ"
                  sizeKey={templates ? 'ready' : 'loading'}>
            <input ref={fileRef} type="file" accept=".docx" className="hidden" onChange={upload} />
            {error && <div className="text-[13.5px] text-rose-600">{error}</div>}
            {!templates && !error && <Loading />}
            {templates && (
                <div className="space-y-1.5 motion-safe:animate-fade-in-soft">
                    <div className="flex items-center gap-1.5">
                        <span className={iosGroupLabel}>Метки в шаблоне</span>
                        <InfoHint title="Метки" text={`В тексте Word пишутся в двойных фигурных скобках, например {{ФИО}}. Доступны: ${markers.join(', ')}.`} />
                    </div>
                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {templates.map((item) => (
                            <div key={item.kind} className="flex flex-wrap items-center gap-3 px-4 py-3">
                                <div className="min-w-0 flex-1">
                                    <div className="truncate text-[14px] font-medium text-slate-900">{item.title}</div>
                                    <div className="truncate text-[12.5px] text-slate-500">
                                        {item.custom
                                            ? `Свой: ${item.file_name}${item.uploaded_by ? ` · ${item.uploaded_by}` : ''}${item.uploaded_at ? ` · ${dateText(item.uploaded_at)}` : ''}`
                                            : 'Стандартный — по образцу HR'}
                                    </div>
                                </div>
                                <div className="flex gap-1">
                                    <button type="button" className={iosBtnGhost} onClick={() => download(item)} disabled={!!busy}>
                                        {busy === `get:${item.kind}` ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Скачать
                                    </button>
                                    <button type="button" className={iosBtnGhost} onClick={() => pick(item)} disabled={!!busy}>
                                        {busy === `put:${item.kind}` ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Заменить
                                    </button>
                                    {item.custom && (
                                        <button type="button" className={iosBtnGhost} onClick={() => reset(item)} disabled={!!busy}>
                                            <RotateCcw size={14} /> Стандартный
                                        </button>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                    <div className="px-1 text-[11.5px] text-slate-400">
                        Трудового договора среди образцов HR нет — для ТД собирается только анкета.
                    </div>
                </div>
            )}
        </IosModal>
    );
}

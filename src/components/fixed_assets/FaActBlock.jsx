import React, { useState } from 'react';
import { Download, FileText, Loader2 } from 'lucide-react';
import { errorText, fileSizeLabel, fmtDate, openBlob, saveBlob } from './fixedAssetsMeta';
import { Group, GroupRow, StatusWord } from './fixedAssetsUi';

/*
 * Блок акта — шесть полей из ТЗ (раздел 5): скан подписанного акта (PDF), дата подписания,
 * кто передал оригинал, дата передачи в бухгалтерию, «Оригинал получен», бухгалтер, принявший
 * оригинал. Один и тот же блок стоит в карточке запроса и в карточке ОС — у приёма,
 * перемещения и списания. Пустое поле остаётся на месте прочерком: по блоку видно, чего ещё
 * не хватает акту.
 */
const Dash = () => <span className="text-slate-300">—</span>;

export default function FaActBlock({ act, requestId, api, toast, canDownload = false, rejected = false, onReplace = null }) {
    const [busy, setBusy] = useState('');
    if (!act) return null;
    const run = async (name, work, fallback) => {
        setBusy(name);
        try {
            await work();
        } catch (requestError) {
            toast?.(errorText(requestError, fallback), 'error');
        } finally {
            setBusy('');
        }
    };
    const downloadAct = () => run('act', async () => saveBlob(await api.blob(`/requests/${requestId}/act`), `Акт № ${act.number}.docx`),
        'Акт не собрался');
    const openScan = () => run('scan', async () => openBlob(await api.blob(`/files/${act.scan.id}`)), 'Скан не открылся');
    return (
        <Group>
            <div className="flex items-center justify-between gap-3 px-4 py-2.5">
                <div className="min-w-0">
                    <div className="text-[14.5px] font-medium text-slate-900">Акт № {act.number}</div>
                    {act.act_date && <div className="text-[12.5px] text-slate-500">от {fmtDate(act.act_date)}</div>}
                </div>
                {canDownload && (
                    <button type="button" onClick={downloadAct} disabled={busy === 'act'}
                            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13.5px] font-semibold text-blue-600 transition hover:bg-blue-50 disabled:opacity-50">
                        {busy === 'act' ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />} Скачать
                    </button>
                )}
            </div>

            {act.scan ? (
                <div className="flex items-center gap-3 px-4 py-2.5">
                    <button type="button" onClick={openScan} disabled={busy === 'scan'}
                            className="flex min-w-0 flex-1 items-center gap-3 text-left transition hover:opacity-80 disabled:opacity-50">
                        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-rose-500 text-white">
                            {busy === 'scan' ? <Loader2 size={16} className="animate-spin" /> : <FileText size={16} />}
                        </span>
                        <span className="min-w-0">
                            <span className="block truncate text-[14px] font-medium text-slate-900">{act.scan.file_name || 'акт.pdf'}</span>
                            <span className="block text-[12.5px] text-slate-500">Скан подписанного акта · {fileSizeLabel(act.scan.file_size)}</span>
                        </span>
                    </button>
                    {onReplace && (
                        <button type="button" onClick={onReplace} className="shrink-0 rounded-lg px-2 py-1 text-[13px] font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700">
                            Заменить
                        </button>
                    )}
                </div>
            ) : (
                <GroupRow label="Скан подписанного акта"><span className="text-slate-400">не приложен</span></GroupRow>
            )}
            <GroupRow label="Дата подписания">{act.signed_on ? fmtDate(act.signed_on) : <Dash />}</GroupRow>
            <GroupRow label="Кто передал оригинал">{act.original_handed_by_name || <Dash />}</GroupRow>
            <GroupRow label="Дата передачи в бухгалтерию">{act.original_handed_on ? fmtDate(act.original_handed_on) : <Dash />}</GroupRow>
            <GroupRow label="Оригинал получен">
                {act.original_received
                    ? <StatusWord tone="green">Да</StatusWord>
                    : (rejected ? <Dash /> : <span className="text-slate-400">Нет</span>)}
            </GroupRow>
            <GroupRow label="Бухгалтер, принявший оригинал">{act.original_received_by_name || <Dash />}</GroupRow>
        </Group>
    );
}

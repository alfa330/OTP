import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Download, Loader2 } from 'lucide-react';
import { iosBtnPrimary, iosBtnSecondary, IosModal } from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
import { DATE_TRIGGER } from '../complaints/styles';
import { defaultPeriod, exportFileName, exportQuery, periodProblem } from './exportPeriod.js';

/* Выгрузка «Обращений» в Excel за период (владелец, 30.09.2026). Кнопка —
 * у тех, кому сервер её откроет (capabilities.can_export: СВ, глава, админ).
 *
 * Файл собирает сервер (crm/report.py), здесь — только период и скачивание:
 * ответ приходит блобом и сохраняется в том же окне, без новой вкладки. */

const FieldLabel = ({ children }) => (
    <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">{children}</label>
);

export default function TicketsExport({ open, onClose, apiBaseUrl, headers, showToast }) {
    const [period, setPeriod] = useState(defaultPeriod);
    const [busy, setBusy] = useState(false);

    useEffect(() => { if (open) setPeriod(defaultPeriod()); }, [open]);

    const problem = periodProblem(period.from, period.to);

    const download = async () => {
        if (problem) return;
        setBusy(true);
        try {
            const response = await axios.get(
                `${apiBaseUrl}/api/crm/export?${exportQuery(period.from, period.to)}`,
                { headers: headers(), responseType: 'blob' });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = exportFileName(period.from, period.to);
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            onClose();
        } catch (err) {
            // Ошибка приходит тем же блобом — достаём из него текст сервера.
            let message = 'Не удалось собрать выгрузку';
            try {
                const text = await err?.response?.data?.text?.();
                message = JSON.parse(text || '{}').error || message;
            } catch (_) { /* останется общая фраза */ }
            showToast?.(message, 'error');
        } finally {
            setBusy(false);
        }
    };

    return (
        <IosModal open={open} onClose={() => { if (!busy) onClose(); }} title="Выгрузка обращений"
                  subtitle="Все обращения, заведённые за период, одним файлом Excel"
                  maxWidth="max-w-md"
                  footer={(
                      <>
                          <button type="button" onClick={onClose} disabled={busy} className={iosBtnSecondary}>
                              Отмена
                          </button>
                          <button type="button" onClick={download} disabled={busy || Boolean(problem)}
                                  className={iosBtnPrimary}>
                              {busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                              Скачать
                          </button>
                      </>
                  )}>
            <div className="space-y-2">
                <div className="grid grid-cols-2 gap-3">
                    <div>
                        <FieldLabel>С</FieldLabel>
                        <IosDatePicker value={period.from} max={period.to || undefined} className="w-full"
                                       triggerClassName={DATE_TRIGGER}
                                       onChange={(value) => setPeriod((prev) => ({ ...prev, from: value }))}
                                       ariaLabel="Начало периода" />
                    </div>
                    <div>
                        <FieldLabel>По</FieldLabel>
                        <IosDatePicker value={period.to} min={period.from || undefined} className="w-full"
                                       triggerClassName={DATE_TRIGGER}
                                       onChange={(value) => setPeriod((prev) => ({ ...prev, to: value }))}
                                       ariaLabel="Конец периода" />
                    </div>
                </div>
                {problem && period.from && period.to && (
                    <div className="px-1 text-[11.5px] text-rose-600">{problem}</div>
                )}
            </div>
        </IosModal>
    );
}

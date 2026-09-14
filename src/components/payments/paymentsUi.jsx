import React, { useCallback, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Download, FileText, Loader2, Paperclip, Trash2, X } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { iosBtnGhost, iosGroupLabel, iosInput } from '../ui/ios';
import InfoHint from '../common/InfoHint';
import {
    ATTACHMENT_LABELS, fileSizeLabel, fmtMoney, parseAmount, requestState, stateMeta, tonePill,
} from './paymentsMeta';

/*
 * Мелкие примитивы раздела «Оплата счетов», общие для формы, карточки и
 * справочников. Всё построено на примитивах ui/ios — своего визуального языка
 * раздел не заводит.
 */

/* Подпись поля. Пояснение — под «i» у метки, а не строкой под полем (решение
   владельца 27.08.2026): текст нужен один раз, когда человек не понял поле. */
export const Field = ({ label, hint, required = false, optionalMark = true, children, className = '' }) => (
    <label className={`block space-y-1.5 ${className}`}>
        <span className="flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            {label}
            {!required && optionalMark && (
                <span className="font-normal normal-case tracking-normal text-slate-400">необязательно</span>
            )}
            {hint && <InfoHint text={hint} />}
        </span>
        {children}
    </label>
);

/* Строка «подпись — значение» в карточке. Пустое значение строку не рисует —
   включая `false`, которое отдаёт `{value && …}`. */
export const Row = ({ label, children, wide = false }) => {
    if (children === null || children === undefined || children === '' || children === false) return null;
    return (
        <div className="flex gap-3 py-1.5">
            <div className={`${wide ? 'w-[168px]' : 'w-[140px]'} shrink-0 text-[12.5px] text-slate-500`}>{label}</div>
            <div className="min-w-0 flex-1 whitespace-pre-line break-words text-[13.5px] text-slate-900">{children}</div>
        </div>
    );
};

export const StatePill = ({ request, className = '' }) => {
    const state = requestState(request);
    const meta = stateMeta(state);
    if (!meta.tone) return null;
    const pill = tonePill(meta.tone);
    return (
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[12px] font-medium ${pill.fill} ${className}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${pill.dot}`} />
            {meta.label}
        </span>
    );
};

/* Поле суммы: печатается как обычный текст, на потере фокуса приводится к
   формату «1 234,50». Число уходит наружу числом. */
export const AmountInput = ({ value, onChange, placeholder = '0', className = '', disabled = false, ariaLabel }) => {
    const [text, setText] = useState(() => (value === '' || value === null || value === undefined ? '' : fmtMoney(value, { currency: false })));
    const [focused, setFocused] = useState(false);
    const shown = focused ? text : (value === '' || value === null || value === undefined || value === 0 ? '' : fmtMoney(value, { currency: false }));
    return (
        <input
            type="text"
            inputMode="decimal"
            aria-label={ariaLabel}
            disabled={disabled}
            className={`${iosInput} tabular-nums ${className}`}
            placeholder={placeholder}
            value={shown}
            onFocus={() => { setFocused(true); setText(value ? String(value).replace('.', ',') : ''); }}
            onChange={(event) => { setText(event.target.value); onChange(parseAmount(event.target.value)); }}
            onBlur={() => setFocused(false)}
        />
    );
};

/* Выбор сотрудника — CustomSelect с поиском; варианты собираются один раз. */
export const UserSelect = ({ users, value, onChange, placeholder = 'Выберите сотрудника', exclude = [], ariaLabel, multiple = false }) => {
    const options = useMemo(() => (users || [])
        .filter((user) => !exclude.includes(user.id))
        .map((user) => ({
            value: user.id,
            label: user.department_name ? `${user.name} · ${user.department_name}` : user.name,
        })), [users, exclude]);
    return (
        <CustomSelect
            value={value}
            onChange={onChange}
            options={options}
            placeholder={placeholder}
            variant="ios"
            searchable
            ariaLabel={ariaLabel || placeholder}
            multiple={multiple}
        />
    );
};

/* ── Файлы ─────────────────────────────────────────────────────────────────── */

const ACCEPT = '.pdf,.jpg,.jpeg,.png,.webp,.heic,.doc,.docx,.xls,.xlsx,.csv,.txt,.zip';

/* Очередь файлов ДО отправки: имя, размер, вид документа. Вид спрашивается у
   каждого файла, потому что от него зависит проверка шага («счёт приложен?»). */
export const FilePicker = ({ files, onChange, kinds, disabled = false, label = 'Приложить файлы' }) => {
    const inputRef = useRef(null);
    const defaultKind = kinds?.[0]?.value || 'other';
    const add = useCallback((list) => {
        const next = Array.from(list || []).map((file) => ({
            key: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2, 7)}`,
            file,
            kind: defaultKind,
        }));
        if (next.length) onChange([...(files || []), ...next]);
    }, [defaultKind, files, onChange]);
    return (
        <div className="space-y-2">
            {(files || []).map((entry) => (
                <div key={entry.key} className="flex items-center gap-2 rounded-xl bg-slate-100 px-3 py-2">
                    <FileText size={15} className="shrink-0 text-slate-400" />
                    <div className="min-w-0 flex-1">
                        <div className="truncate text-[13px] text-slate-800">{entry.file.name}</div>
                        <div className="text-[11.5px] text-slate-500">{fileSizeLabel(entry.file.size)}</div>
                    </div>
                    {kinds && kinds.length > 1 && (
                        <div className="w-[190px] shrink-0">
                            <CustomSelect
                                value={entry.kind}
                                onChange={(kind) => onChange(files.map((item) => (item.key === entry.key ? { ...item, kind } : item)))}
                                options={kinds}
                                variant="ios"
                                ariaLabel="Вид документа"
                            />
                        </div>
                    )}
                    <button
                        type="button"
                        aria-label="Убрать файл"
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-700"
                        onClick={() => onChange(files.filter((item) => item.key !== entry.key))}
                    >
                        <X size={14} />
                    </button>
                </div>
            ))}
            <input
                ref={inputRef}
                type="file"
                multiple
                accept={ACCEPT}
                className="hidden"
                disabled={disabled}
                onChange={(event) => { add(event.target.files); event.target.value = ''; }}
            />
            <button
                type="button"
                disabled={disabled}
                className={`${iosBtnGhost} -ml-2`}
                onClick={() => inputRef.current?.click()}
            >
                <Paperclip size={14} />
                {label}
            </button>
        </div>
    );
};

export const downloadAttachment = async ({ apiBaseUrl, headers, attachment, inline = false }) => {
    const response = await axios.get(
        `${apiBaseUrl}/api/payments/attachments/${attachment.id}/download${inline ? '?inline=1' : ''}`,
        { headers: headers(), responseType: 'blob' },
    );
    const url = URL.createObjectURL(response.data);
    if (inline) {
        window.open(url, '_blank', 'noopener');
        setTimeout(() => URL.revokeObjectURL(url), 60000);
        return;
    }
    const link = document.createElement('a');
    link.href = url;
    link.download = attachment.file_name || 'file';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const isViewable = (attachment) => /^(image\/|application\/pdf)/.test(attachment?.content_type || '');

/* Список сохранённых файлов заявки: вид, имя, кто и когда, скачать / снять. */
export const AttachmentList = ({ attachments, apiBaseUrl, headers, canRemove, onRemove, showToast, showStep = false }) => {
    const [busy, setBusy] = useState(null);
    if (!attachments || !attachments.length) return null;
    const open = async (attachment, inline) => {
        setBusy(attachment.id);
        try {
            await downloadAttachment({ apiBaseUrl, headers, attachment, inline });
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось скачать файл', 'error');
        } finally {
            setBusy(null);
        }
    };
    return (
        <div className="space-y-1">
            {attachments.map((attachment) => (
                <div key={attachment.id} className="flex items-center gap-2.5 rounded-xl px-2 py-1.5 transition hover:bg-slate-50">
                    <FileText size={15} className="shrink-0 text-slate-400" />
                    <button
                        type="button"
                        className="min-w-0 flex-1 text-left"
                        onClick={() => open(attachment, isViewable(attachment))}
                        title={isViewable(attachment) ? 'Открыть' : 'Скачать'}
                    >
                        <div className="truncate text-[13px] text-slate-900 underline decoration-transparent underline-offset-2 transition hover:decoration-slate-300">
                            {attachment.file_name}
                        </div>
                        <div className="truncate text-[11.5px] text-slate-500">
                            {ATTACHMENT_LABELS[attachment.kind] || attachment.kind}
                            {showStep && attachment.step_no ? ` · шаг ${attachment.step_no}` : ''}
                            {' · '}{fileSizeLabel(attachment.file_size)}
                            {attachment.uploaded_by_name ? ` · ${attachment.uploaded_by_name}` : ''}
                        </div>
                    </button>
                    <button
                        type="button"
                        aria-label="Скачать"
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                        disabled={busy === attachment.id}
                        onClick={() => open(attachment, false)}
                    >
                        {busy === attachment.id ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                    </button>
                    {canRemove?.(attachment) && (
                        <button
                            type="button"
                            aria-label="Снять файл"
                            className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                            onClick={() => onRemove?.(attachment)}
                        >
                            <Trash2 size={14} />
                        </button>
                    )}
                </div>
            ))}
        </div>
    );
};

export const SectionTitle = ({ children, right = null }) => (
    <div className="flex items-end justify-between gap-2">
        <div className={iosGroupLabel}>{children}</div>
        {right}
    </div>
);

export const ErrorBox = ({ text }) => (text ? (
    <div className="rounded-2xl bg-rose-50 px-4 py-3 text-[13px] leading-relaxed text-rose-700 ring-1 ring-rose-200">{text}</div>
) : null);

export const NoticeBox = ({ text, tone = 'amber', children }) => {
    const cls = tone === 'amber'
        ? 'bg-amber-50 text-amber-800 ring-amber-200'
        : tone === 'blue'
            ? 'bg-blue-50 text-blue-800 ring-blue-100'
            : 'bg-slate-50 text-slate-700 ring-slate-200';
    return (
        <div className={`rounded-2xl px-4 py-3 text-[13px] leading-relaxed ring-1 ${cls}`}>
            {text}
            {children}
        </div>
    );
};

export const appendPayloadFiles = (formData, files) => {
    (files || []).forEach((entry) => formData.append('files', entry.file, entry.file.name));
    formData.append('kinds', JSON.stringify((files || []).map((entry) => entry.kind || 'other')));
};

export const errorText = (error, fallback) => {
    const data = error?.response?.data;
    if (data?.errors?.length) return data.errors.join('. ');
    if (data?.missing?.length) return data.missing.join('. ');
    return data?.error || fallback;
};

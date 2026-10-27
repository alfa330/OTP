import React, { useEffect, useRef } from 'react';
import { Check, FileSpreadsheet, Loader2 } from 'lucide-react';
import { IosModal, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import useEscapeClose from '../trainings/useEscapeClose';
import { hoursReportTitle, pickHoursReportScope } from './hoursReportOptions';

/*
 * Окно «Сформировать отчёт» в «Учете часов» — общее для компьютера и телефона
 * (на телефоне IosModal сам становится экраном). Варианты и их описания —
 * hoursReportOptions.js; описание стоит под названием сразу, без «i»: окно
 * затем и открывается, чтобы прочитать, чем отчёты отличаются.
 *
 * Выбор — плитками, выбранная — мягкой голубой заливкой с галочкой, как метки
 * и плитки выбора на остальном сайте. Скачивание — отдельной кнопкой: «Общий»
 * у глобального админа собирает всю компанию, случайный щелчок по плитке не
 * должен запускать такую выгрузку.
 *
 * С клавиатуры: при открытии фокус встаёт на выбранную плитку (общий IosModal
 * фокус не переносит, и Tab шёл по странице под затемнением), Esc закрывает.
 */
export default function HoursReportDialog({
    open,
    onClose,
    month,
    options = [],
    value,
    onChange,
    onDownload,
    isDownloading = false,
}) {
    const selected = pickHoursReportScope(options, value);
    const selectedRef = useRef(null);
    useEscapeClose(open, onClose);
    useEffect(() => {
        if (open) selectedRef.current?.focus({ preventScroll: true });
    }, [open]);
    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={hoursReportTitle(month)}
            subtitle="Excel, по листу на каждый показатель"
            maxWidth="max-w-md"
            footer={(
                <>
                    <button type="button" onClick={onClose} className={iosBtnSecondary}>Отмена</button>
                    <button
                        type="button"
                        onClick={() => onDownload?.(selected)}
                        disabled={!selected || isDownloading}
                        className={iosBtnPrimary}
                    >
                        {isDownloading
                            ? <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                            : <FileSpreadsheet size={15} aria-hidden="true" />}
                        {isDownloading ? 'Формируется…' : 'Скачать'}
                    </button>
                </>
            )}
        >
            <div role="radiogroup" aria-label="Какой отчёт" className="flex flex-col gap-2.5">
                {options.map((option) => {
                    const checked = option.value === selected;
                    return (
                        <button
                            key={option.value}
                            ref={checked ? selectedRef : undefined}
                            type="button"
                            role="radio"
                            aria-checked={checked}
                            disabled={option.disabled}
                            onClick={() => onChange?.(option.value)}
                            className={`flex w-full items-start gap-3 rounded-2xl px-4 py-3 text-left ring-1 transition active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 disabled:cursor-not-allowed disabled:opacity-60 disabled:active:scale-100 ${
                                checked ? 'bg-blue-50 ring-blue-200' : 'bg-white ring-slate-200/70 hover:bg-slate-50'
                            }`}
                        >
                            {/* grow, а не flex-1: на телефоне общий слой разделов ставит
                                колонкой ряд items-start с потомком .flex-1 (каркасы «панель —
                                рабочая область», mobile-shell.css), и галочка уезжала под текст. */}
                            <span className="min-w-0 grow">
                                <span className={`block text-[14px] font-semibold ${checked ? 'text-blue-700' : 'text-slate-900'}`}>
                                    {option.label}
                                </span>
                                <span className="mt-0.5 block text-[12.5px] leading-snug text-slate-500">{option.description}</span>
                            </span>
                            <span
                                aria-hidden="true"
                                className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full ${
                                    checked ? 'bg-blue-600 text-white' : 'ring-1 ring-inset ring-slate-300'
                                }`}
                            >
                                {checked ? <Check size={13} strokeWidth={3} /> : null}
                            </span>
                        </button>
                    );
                })}
            </div>
        </IosModal>
    );
}

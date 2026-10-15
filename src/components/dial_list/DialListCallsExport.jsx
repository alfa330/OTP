import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosBtnGhost, iosBtnPrimary } from '../ui/ios';
import { IosDateRangeCalendar, rangeLabel } from '../ui/DateRangePicker';
import { EXPORT_MAX_DAYS, exportFileName, exportPresets, isoDay, maxDaysLabel, rangeDays } from './callsExport';

/*
 * «Детализация» в шапке раздела «Удаленный КЦ» (владелец, 15.10.2026: «выгрузить детализацию
 * за выбранный период — что-то наподобие журнала звонков»). Excel: каждый звонок строкой,
 * итоги операторов и контекст (dial_list/calls_export.py).
 *
 * Форма — эталон пикера сайта, выгрузка табло СЗоВ (ChatExportControls): кнопка раскрывает
 * календарь периода с пресетами, под ним «Подтвердить» и строка о том, что попадёт в файл.
 * Кнопка в шапке, а не во вкладке: детализация — про весь отдел, и нужна с любой вкладки.
 *
 * Пока файл собирается, панель остаётся открытой (спиннер на «Подтвердить») и закрывается,
 * когда файл отдан. «За эти дни звонков не было» и отказ сервера пишутся строкой под кнопкой —
 * там, куда человек смотрит, и можно сразу выбрать другие дни. Тостом — только если панель
 * успели закрыть: у тостов сайта два цвета, и «звонков не было» красным читалось бы как сбой.
 *
 * Панель раскрывается в ту сторону, где ей хватает места: на широком экране кнопка стоит у
 * правого края шапки, а на телефоне ряд переносится под заголовок, к левому краю, — привязка
 * к одной стороне уводила бы календарь за край экрана.
 */

const PANEL_WIDTH = 268; // ширина карточки IosDateRangeCalendar
const EDGE_GAP = 8;
const PRESETS = exportPresets();
const EMPTY_CODE = 'DIAL_LIST_CALLS_EXPORT_EMPTY';
const NOTICE_TONE = { empty: 'text-amber-600', error: 'text-rose-500' };

/* Почему файла нет — словами: отказ сервера (4xx) — его же текстом, сбой — без «Internal
   server error» и без «Failed to fetch». */
const failureText = (status, data) => {
    if (status && status < 500 && data?.error) return data.error;
    return status ? 'Сервер не собрал файл — попробуйте ещё раз' : 'Нет связи с сервером — попробуйте ещё раз';
};

const DialListCallsExport = ({ apiBaseUrl, authHeaders, departmentId, showToast }) => {
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState(null); // { kind: 'empty' | 'error', text }
    const [range, setRange] = useState(() => {
        const today = isoDay(new Date());
        return { from: today, to: today };
    });
    const [alignRight, setAlignRight] = useState(true);
    const ref = useRef(null);
    const openRef = useRef(open);
    openRef.current = open;
    // showToast — новая функция на каждый рендер App: держим её в ref, а не в зависимостях.
    const toastRef = useRef(showToast);
    toastRef.current = showToast;

    // Клик мимо и Esc закрывают панель — как у остальных пикеров сайта.
    useEffect(() => {
        if (!open) return undefined;
        const onDown = (event) => {
            if (ref.current && !ref.current.contains(event.target)) setOpen(false);
        };
        const onKey = (event) => { if (event.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);

    // Сторону выбираем до первой отрисовки панели, чтобы она не прыгала.
    useLayoutEffect(() => {
        if (!open || !ref.current) return;
        const rect = ref.current.getBoundingClientRect();
        setAlignRight(rect.right - PANEL_WIDTH >= EDGE_GAP);
    }, [open]);

    const days = rangeDays(range.from, range.to);
    const tooLong = days > EXPORT_MAX_DAYS;

    const toggle = () => {
        setNotice(null);
        setOpen((value) => !value);
    };

    const pickRange = (next) => {
        setNotice(null);
        setRange({ from: next.from || next.to, to: next.to || next.from });
    };

    // Строка под кнопкой, а если панель уже закрыли — тост.
    const tell = (kind, text) => {
        if (openRef.current) setNotice({ kind, text });
        else toastRef.current?.(text, kind === 'empty' ? 'warning' : 'error');
    };

    const download = async () => {
        const { from, to } = range;
        setNotice(null);
        setBusy(true);
        try {
            const query = new URLSearchParams({ date_from: from, date_to: to });
            const resp = await fetch(
                `${apiBaseUrl}/api/dial_list/departments/${departmentId}/calls/export?${query.toString()}`,
                { credentials: 'include', headers: authHeaders() },
            );
            if (!resp.ok) {
                const data = await resp.json().catch(() => ({}));
                // Звонков за период не было — это ответ, а не сбой: пустой файл сервер не отдаёт.
                if (data?.code === EMPTY_CODE) tell('empty', `За ${rangeLabel(from, to)} звонков не было`);
                else tell('error', failureText(resp.status, data));
                return;
            }
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = exportFileName(from, to);
            document.body.appendChild(link);
            link.click();
            link.remove();
            // Не сразу: часть браузеров ещё читает ссылку после click().
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            setOpen(false);
        } catch {
            tell('error', failureText(0));
        } finally {
            setBusy(false);
        }
    };

    // Строка — в одну строчку: перенос менял бы высоту панели прямо под курсором. «Звонки и
    // операторы» — как называются листы файла.
    let hint = `${rangeLabel(range.from, range.to)} · звонки и операторы`;
    let hintTone = 'text-slate-400';
    if (tooLong) {
        hint = `Не больше ${maxDaysLabel()} — сократите период`;
        hintTone = 'text-rose-500';
    } else if (notice) {
        hint = notice.text;
        hintTone = NOTICE_TONE[notice.kind];
    }

    return (
        <div ref={ref} className="relative">
            <button
                type="button"
                onClick={toggle}
                aria-expanded={open}
                aria-haspopup="dialog"
                title="Звонки за период в Excel"
                className={`${iosBtnGhost} ${open ? 'bg-slate-100 text-slate-900' : ''}`}
            >
                {/* Спиннер на самой кнопке — только когда панель закрыли, не дождавшись файла:
                    при открытой он уже крутится на «Подтвердить». */}
                <FaIcon className={`fas ${busy && !open ? 'fa-spinner fa-spin' : 'fa-file-excel'}`} />
                {busy && !open ? 'Готовим…' : 'Детализация'}
            </button>
            {open && (
                <div
                    role="dialog"
                    aria-label="Детализация звонков"
                    className={`absolute top-full z-[60] mt-2 motion-safe:animate-popover-in ${alignRight ? 'right-0' : 'left-0'}`}
                >
                    <IosDateRangeCalendar
                        from={range.from}
                        to={range.to}
                        max={isoDay(new Date())}
                        presets={PRESETS}
                        onChange={pickRange}
                        footer={(
                            <div className="mt-2.5 border-t border-slate-100 pt-2.5">
                                <button
                                    type="button"
                                    className={`${iosBtnPrimary} w-full`}
                                    disabled={busy || !days || tooLong}
                                    onClick={download}
                                >
                                    <FaIcon className={`fas ${busy ? 'fa-spinner fa-spin' : 'fa-file-excel'}`} />
                                    {busy ? 'Готовим файл…' : 'Подтвердить'}
                                </button>
                                <p role="status" className={`mt-1.5 text-center text-[11px] ${hintTone}`}>{hint}</p>
                            </div>
                        )}
                    />
                </div>
            )}
        </div>
    );
};

export default DialListCallsExport;

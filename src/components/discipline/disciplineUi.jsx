import React from 'react';
import { ChevronRight, FileText, Image as ImageIcon, Loader2, Paperclip, Search, Video } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput, IosHint, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import {
    fileSizeText, fmtDateTime, fmtShort as fmtShortDate, fmtTime as fmtClock, PLATE_TONES, stepChip, STATE_LABELS,
    STATE_TONES, TONE_CHIP,
} from './disciplineRules';

/*
 * Общие элементы раздела «Дисциплина». Размеры — как у «Учёта воды» и
 * «Профиля»: до 1280 px как в остальном портале, на компьютере (xl) кегль на
 * ступень крупнее, на большом экране (2xl) — кегль «Профиля» и колонка до
 * 1800 px. Иначе раздел на мониторе выглядел бы мелким (замечание владельца
 * про «Учёт воды», 12.10.2026).
 */

export const T = {
    title: 'xl:text-[24px] xl:tracking-tight 2xl:text-[30px]',
    lead: 'xl:text-[14px] 2xl:text-[16px]',
    body: 'xl:text-[14px] 2xl:text-[16px]',
    sub: 'xl:text-[13px] 2xl:text-[14px]',
    note: 'xl:text-[12px] 2xl:text-[13px]',
    caps: 'xl:text-[11.5px] 2xl:text-[12.5px]',
};

const BUTTON = 'xl:text-[14px] 2xl:px-5 2xl:py-3 2xl:text-[15px] 2xl:leading-6';
export const dInput = `${iosInput} 2xl:px-4 2xl:py-3 2xl:text-[16px]`;
export const dLabel = `${iosGroupLabel} ${T.caps}`;
export const dBtnPrimary = `${iosBtnPrimary} ${BUTTON}`;
export const dBtnSecondary = `${iosBtnSecondary} ${BUTTON}`;
export const dBtnGhost = `${iosBtnGhost} xl:text-[13.5px] 2xl:px-4 2xl:py-2.5 2xl:text-[15px]`;
export const dBtnDanger = 'inline-flex items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-2.5 '
    + `text-[13.5px] font-semibold text-white transition hover:bg-rose-700 active:scale-[0.98] disabled:opacity-50 ${BUTTON}`;
export const dTabs = 'xl:[&>button]:px-4 xl:[&>button]:py-[7px] xl:[&>button]:text-[14px] '
    + '2xl:rounded-[13px] 2xl:p-1 2xl:[&>button]:rounded-[10px] 2xl:[&>button]:px-6 2xl:[&>button]:py-2 '
    + '2xl:[&>button]:text-[15px] 2xl:[&>button]:leading-6';
export const INVALID_RING = 'ring-2 ring-rose-400/80';

/* Поле поиска с лупой. У dInput на большом экране свой отступ 2xl:px-4, и
   простой pl-9 он перебивал (правило ступени стоит в CSS позже): лупа ложилась
   на первые буквы «ФИО или логин» (скриншот владельца 22.10.2026). Отступ под
   лупу задан и на этой ступени, лупа там под кегль 16 px — как в «Учёте воды». */
export const SearchField = ({ className = '', invalid = false, ...input }) => (
    <label className={`relative block ${className}`}>
        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 2xl:left-4 2xl:size-[18px]" />
        <input {...input} className={`${dInput} pl-9 2xl:pl-12 ${invalid ? INVALID_RING : ''}`} />
    </label>
);

/* Группа: заголовок, справа — чип или действие, пояснение — под «i» у
   заголовка (правило портала), а не строкой под карточкой. */
/* Выбор отдела (у супер-админа и главы нескольких отделов). На компьютере —
   сегменты; на телефоне длинные названия («СЗоВ — Служба заботы о водителях»)
   в сегменты не влезали и обрезались по краям — там выпадающий список
   портала. */
export const DepartmentPicker = ({ value, onChange, options, mobile, className = '' }) => (mobile ? (
    <div className={`w-full ${className}`}>
        <CustomSelect variant="ios" value={value} options={options} onChange={onChange} ariaLabel="Отдел" />
    </div>
) : (
    <IosSegmented value={value} onChange={onChange} slide ariaLabel="Отдел" options={options} className={className} />
));

export const Group = ({ title, right = null, hint = null, children, className = '', cardClassName = '' }) => (
    <section className={`space-y-1.5 2xl:space-y-2 ${className}`}>
        {(title || right || hint) && (
            <div className="flex items-end justify-between gap-2 px-1">
                <span className="flex min-w-0 items-center gap-1.5">
                    {title ? <span className={dLabel}>{title}</span> : null}
                    {hint ? <IosHint text={hint} /> : null}
                </span>
                {right}
            </div>
        )}
        <div className={`${iosCard} ${cardClassName || 'p-4 xl:p-5 2xl:p-6'}`}>{children}</div>
    </section>
);

export const Chip = ({ tone = 'slate', children, className = '' }) => (
    <span className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-[3px] text-[11.5px] font-medium 2xl:text-[13px] ${TONE_CHIP[tone] || TONE_CHIP.slate} ${className}`}>
        {children}
    </span>
);

export const StepChip = ({ item }) => {
    const chip = stepChip(item);
    if (!chip || !chip.label) return null;
    return <Chip tone={chip.tone}>{chip.label}</Chip>;
};

export const StateBadge = ({ state }) => (
    state ? <Chip tone={STATE_TONES[state] || 'slate'}>{STATE_LABELS[state] || state}</Chip> : null
);

/* Плашка у поля «Оператор» (ТЗ п. 6.2). Кликабельна: раскрывает прошлые
   нарушения, не покидая форму. */
export const Plate = ({ plate, previous = [], expanded = false, onToggle }) => {
    if (!plate) return null;
    const tone = PLATE_TONES[plate.tone] || PLATE_TONES.gray;
    const canExpand = previous.length > 0;
    return (
        <div className={`rounded-2xl px-4 py-3 ${tone}`}>
            <button
                type="button"
                onClick={canExpand ? onToggle : undefined}
                className={`flex w-full items-start justify-between gap-3 text-left ${canExpand ? 'cursor-pointer' : 'cursor-default'}`}
                aria-expanded={canExpand ? expanded : undefined}
            >
                <span className="min-w-0">
                    <span className={`block text-[13.5px] font-semibold ${T.body}`}>{plate.title}</span>
                    <span className={`mt-0.5 block text-[12.5px] leading-snug opacity-90 ${T.sub}`}>{plate.text}</span>
                </span>
                {canExpand && (
                    <ChevronRight size={16} className={`mt-0.5 shrink-0 opacity-60 transition-transform ${expanded ? 'rotate-90' : ''}`} />
                )}
            </button>
            {expanded && canExpand && (
                <ul className="mt-2.5 space-y-1 border-t border-current/10 pt-2.5">
                    {previous.map((item) => (
                        <li key={item.id} className={`flex items-baseline gap-3 text-[12.5px] ${T.sub}`}>
                            <span className="w-[112px] shrink-0 whitespace-nowrap tabular-nums opacity-70">{fmtDateTime(item.occurred_at)}</span>
                            <span className="min-w-0">{item.type_title}</span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

/* Поле формы: подпись, «i» с пояснением (пояснение — под «i», а не строкой
   под полем) и красное «чего не хватает» прямо у поля. */
export const Field = ({ label, hint = null, error = null, children, htmlFor, className = '', anchor }) => (
    <div className={`space-y-1.5 ${className}`} data-field={anchor || undefined}>
        {label && (
            <div className="flex items-center gap-1.5 px-1">
                <label htmlFor={htmlFor} className={dLabel}>{label}</label>
                {hint && <IosHint text={hint} />}
            </div>
        )}
        {children}
        {error && <div className={`px-1 text-[12px] font-medium text-rose-600 ${T.note}`}>{error}</div>}
    </div>
);

export const Spinner = ({ size = 16, className = '' }) => (
    <Loader2 size={size} className={`animate-spin text-slate-400 ${className}`} aria-label="Загрузка" />
);

export const LoadingRows = ({ rows = 4 }) => (
    <div className="space-y-2" aria-busy="true">
        {Array.from({ length: rows }).map((_, index) => (
            <div key={index} className="h-[52px] animate-pulse rounded-xl bg-slate-100/80" />
        ))}
    </div>
);

export const EmptyState = ({ icon: Icon = FileText, title, text = null }) => (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
        <span className="grid h-11 w-11 place-items-center rounded-2xl bg-slate-100 text-slate-400">
            <Icon size={20} />
        </span>
        <div className={`text-[14px] font-semibold text-slate-700 ${T.body}`}>{title}</div>
        {text && <div className={`max-w-sm text-[12.5px] text-slate-500 ${T.sub}`}>{text}</div>}
    </div>
);

const fileIcon = (file) => {
    if (/^image\//.test(file?.content_type || '')) return ImageIcon;
    if (/^video\//.test(file?.content_type || '')) return Video;
    return FileText;
};

/* Список файлов записи: вид, имя, кто и когда; недействительный — зачёркнут с
   причиной, прежняя версия — приглушена. Файл не удаляется никогда (п. 13). */
export const FileList = ({ files = [], onOpen, onInvalidate = null, canInvalidate = null, showKind = true, emptyText = null }) => {
    if (!files.length) {
        return emptyText ? <div className={`text-[12.5px] text-slate-400 ${T.sub}`}>{emptyText}</div> : null;
    }
    return (
        <ul className="divide-y divide-slate-100">
            {files.map((file) => {
                const Icon = fileIcon(file);
                const dim = file.invalid || file.superseded;
                return (
                    <li key={file.id} className="flex items-center gap-3 py-2 first:pt-0 last:pb-0">
                        <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ${dim ? 'bg-slate-50 text-slate-300' : 'bg-blue-50 text-blue-600'}`}>
                            <Icon size={16} />
                        </span>
                        <button type="button" onClick={() => onOpen?.(file)} className="min-w-0 flex-1 text-left">
                            <span className={`block truncate text-[13px] font-medium ${dim ? 'text-slate-400' : 'text-slate-800'} ${file.invalid ? 'line-through' : ''} ${T.sub}`}>
                                {file.name}
                            </span>
                            <span className={`block truncate text-[11.5px] text-slate-400 ${T.note}`}>
                                {[showKind ? file.kind_label : null, fileSizeText(file.size), fmtDateTime(file.uploaded_at),
                                    file.uploaded_by_name, file.superseded ? 'прежняя версия' : null,
                                    file.invalid ? `недействителен: ${file.invalid_reason || ''}` : null].filter(Boolean).join(' · ')}
                            </span>
                        </button>
                        {onInvalidate && !file.invalid && (!canInvalidate || canInvalidate(file)) && (
                            <button type="button" className={`${iosBtnGhost} !px-2 !py-1 text-[12px]`} onClick={() => onInvalidate(file)}>
                                Недействителен
                            </button>
                        )}
                    </li>
                );
            })}
        </ul>
    );
};

/* Ссылка-действие раздела: на компьютере и большом экране растёт вместе с
   соседними кнопками (кегль и поля dBtnGhost). Первая в ряду (edge) сдвинута
   влево на своё поле — текст стоит по краю карточки. Ссылке после другой кнопки
   сдвиг не нужен: она наезжала бы на соседку (на 2xl — на 4–8 px, и при
   наведении две плашки склеивались). */
export const dLinkAction = dBtnGhost;
export const dLinkEdge = '-ml-2 2xl:-ml-4';

/* edge — кнопка первая в своём ряду (как было всегда); после другой кнопки —
   edge={false}. busy — значок сменяется спиннером, ряд не перестраивается. */
export const AttachButton = ({ label = 'Приложить файл', accept, multiple = false, onFiles, disabled = false, invalid = false,
    busy = false, edge = true }) => {
    const inputRef = React.useRef(null);
    return (
        <>
            <input
                ref={inputRef}
                type="file"
                className="hidden"
                accept={accept}
                multiple={multiple}
                onChange={(event) => {
                    const files = Array.from(event.target.files || []);
                    event.target.value = '';
                    if (files.length) onFiles(files);
                }}
            />
            <button
                type="button"
                disabled={disabled || busy}
                onClick={() => inputRef.current?.click()}
                className={`${dLinkAction} ${edge ? dLinkEdge : ''} ${invalid ? `!text-rose-600 ${INVALID_RING}` : ''}`}
            >
                {busy ? <Spinner size={14} /> : <Paperclip size={14} />}
                {label}
            </button>
        </>
    );
};

export const ACCEPT_DOCS = '.pdf,.jpg,.jpeg,.png,.heic,.doc,.docx,image/*,application/pdf';
export const ACCEPT_EVIDENCE = `${ACCEPT_DOCS},.mp4,video/mp4`;
export const ACCEPT_ACT = '.pdf,.jpg,.jpeg,.png,image/jpeg,image/png,application/pdf';

export { iosBtnPrimary, iosBtnSecondary, iosCard };

/* Строка записи в списках: дата, кто, что, ступень. Нажатие открывает запись. */
export const RecordRow = ({ item, onOpen, showOperator = true, dense = false }) => {
    const cancelled = item.state === 'cancelled';
    return (
        <button
            type="button"
            onClick={() => onOpen?.(item.id)}
            className={`flex w-full items-center gap-3 px-4 text-left transition hover:bg-slate-50 active:bg-slate-100 ${dense ? 'py-2.5' : 'py-3'} xl:px-5 2xl:py-3.5`}
        >
            <span className={`w-[50px] shrink-0 text-[12.5px] tabular-nums text-slate-500 xl:w-[64px] ${T.sub}`}>
                {fmtShortDate(item.occurred_at)}
                <span className="block text-[11px] text-slate-400 2xl:text-[12px]">{fmtClock(item.occurred_at)}</span>
            </span>
            <span className="min-w-0 flex-1">
                {showOperator && (
                    <span className={`block truncate text-[13.5px] font-semibold text-slate-900 ${T.body} ${cancelled ? 'line-through opacity-60' : ''}`}>
                        {item.operator_name}
                    </span>
                )}
                <span className={`block truncate text-[12.5px] ${showOperator ? 'text-slate-500' : 'text-slate-800'} ${T.sub} ${cancelled ? 'line-through opacity-60' : ''}`}>
                    {item.type_title}
                    {item.explanation_overdue ? <span className="ml-1.5 font-medium text-rose-600">· срок объяснительной прошёл</span> : null}
                </span>
            </span>
            <span className="flex shrink-0 items-center gap-1.5">
                {item.under_control && !cancelled ? <Chip tone="red">Контроль</Chip> : null}
                <StepChip item={item} />
            </span>
            <ChevronRight size={15} className="shrink-0 text-slate-300" />
        </button>
    );
};

import React from 'react';
import { CornerDownLeft, MapPin, Percent } from 'lucide-react';
import { OfficeStatusBadge } from './officeBadges';
import { officeDayStatus } from './officeDayStatus';
import { officeTodayISO } from './officeSchedule';

/* Справочник в выдаче поиска: офисы и комиссии Яндекса.
 *
 * Адреса и комиссии живут не в статьях, а во вкладках «Офисы» и «Города», и
 * поиск их не видел — на «офис алматы» и «комиссия яндекса в кокшетау» он
 * отдавал статьи, где слово просто упомянуто. Что показать, решает сервер
 * (wiki/directory.py): строка офиса, строка комиссий города или строка-дверь
 * во вкладку, если город не назван. Здесь — только как это нарисовать и что
 * можно открыть.
 *
 * Строк немного и они сверху: на такой запрос это и есть ответ, статьи ниже —
 * его окружение. Одна вёрстка на оба поиска раздела (поле в шапке и главная):
 * разъехавшись, они показывали бы разное на один запрос.
 */

/* Вид строки → вкладка, которую она открывает. Строка без открытой вкладки
   не рисуется: щелчок по ней вёл бы в раздел, которого у человека нет. */
const TAB_OF = { office: 'offices', offices_tab: 'offices', city: 'cities', cities_tab: 'cities' };

export const directoryTab = (entry) => TAB_OF[entry?.kind] || null;

/** Строки, которые есть куда открыть. canOpen — { offices, cities }. */
export const openableDirectory = (entries, canOpen) => (entries || []).filter((entry) => {
    const tab = directoryTab(entry);
    return !!tab && !!canOpen?.[tab];
});

const plural = (count, one, few, many) => {
    const tail = count % 100;
    if (tail >= 11 && tail <= 14) return many;
    const last = count % 10;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
};

/** Ключ строки. Сервер одну точку на два города не повторяет, но for_city в
 *  ключе — страховка: два одинаковых ключа React сливает, и в списке
 *  оставалась бы строка от прошлого запроса. */
export const directoryKey = (entry) => (
    `${entry?.kind}-${entry?.id ?? entry?.city ?? 'all'}-${entry?.for_city || ''}`
);

/* Номер парка — с подписью, как в карточке офиса: без неё WhatsApp iTaxi
   читался бы номером офиса. Сервер шлёт только свой номер офиса или номер
   названного в запросе парка (wiki/directory._row_phone). */
const phoneText = (entry) => {
    if (!entry.phone) return null;
    const phone = entry.phone_park ? `${entry.phone_park}: ${entry.phone}` : entry.phone;
    return entry.phone_note ? `${phone} (${entry.phone_note})` : phone;
};

/** Подпись строки — отдельно от разметки, чтобы её проверял тест. */
export function directoryText(entry) {
    switch (entry?.kind) {
    case 'office': {
        const place = entry.no_office
            ? 'офиса в городе нет, принимают по телефону'
            : (entry.address || '');
        const parts = [entry.for_city ? `${entry.city} — для водителей из ${entry.for_city}` : entry.city,
            place, phoneText(entry)].filter(Boolean);
        return { title: entry.name, subtitle: parts.join(' · ') };
    }
    case 'offices_tab':
        return entry.city
            ? {
                title: `Все офисы: ${entry.city}`,
                subtitle: entry.count
                    ? `${entry.count} ${plural(entry.count, 'точка', 'точки', 'точек')} — адреса, телефоны и статус на сегодня`
                    : 'Адреса, телефоны и статус на сегодня',
            }
            : { title: 'Офисы', subtitle: 'Адреса, телефоны и статус офисов на сегодня — по городам' };
    case 'city': {
        const tariffs = (entry.tariffs || []).map((tariff) => `${tariff.name} ${tariff.commission}`);
        const extra = [];
        if (!entry.narrowed && (entry.options || []).length) {
            extra.push(`доп. опции: ${entry.options.length}`);
        }
        if (!entry.narrowed && entry.park_commission) extra.push(`парк ${entry.park_commission}`);
        return {
            title: `Комиссия Яндекса · ${entry.name}`,
            subtitle: tariffs.concat(extra).join(' · ') || 'Тарифы и комиссии города',
        };
    }
    case 'cities_tab': {
        const summary = (entry.tariffs || []).map((row) => (
            `${row.name}: ${row.range} в ${row.cities} ${plural(row.cities, 'городе', 'городах', 'городах')}`));
        return {
            title: 'Комиссия Яндекса по городам',
            subtitle: summary.join(' · ') || 'Тарифы и комиссии каждого города',
        };
    }
    default:
        return { title: '', subtitle: '' };
    }
}

const ICONS = { office: MapPin, offices_tab: MapPin, city: Percent, cities_tab: Percent };

/** Строка справочника. Выделение и Enter — как у статьи: она в общем списке строк. */
export function DirectoryRow({ entry, dataRow, selected, onPick, onHover }) {
    const Icon = ICONS[entry.kind] || MapPin;
    const { title, subtitle } = directoryText(entry);
    const today = officeTodayISO();
    const status = entry.kind === 'office' ? officeDayStatus(entry, today) : null;
    return (
        <li data-row={dataRow}>
            <button
                type="button"
                onClick={onPick}
                onMouseEnter={onHover}
                className={`flex w-full items-start gap-2.5 rounded-xl px-3 py-2 text-left transition ${
                    selected ? 'bg-indigo-50' : 'hover:bg-slate-50'
                }`}
            >
                <Icon size={15} className={`mt-0.5 shrink-0 ${selected ? 'text-indigo-500' : 'text-slate-300'}`} />
                <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="min-w-0 max-w-full truncate text-[13.5px] font-medium text-slate-900">
                            {title}
                        </span>
                        {status && (
                            <span className="inline-flex shrink-0">
                                <OfficeStatusBadge schedule={entry.schedule} status={status}
                                                   isToday dayISO={today} />
                            </span>
                        )}
                    </span>
                    {subtitle && (
                        <span className="mt-0.5 block text-[12px] leading-snug text-slate-500 [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] overflow-hidden">
                            {subtitle}
                        </span>
                    )}
                </span>
                {selected && <CornerDownLeft size={13} className="mt-1 shrink-0 text-indigo-400" />}
            </button>
        </li>
    );
}

/** Секция «Справочник» выдачи. rows — общий список строк (для индексов). */
export function DirectorySection({ entries, rows, selectedIndex, onPick, onHover }) {
    if (!entries.length) return null;
    return (
        <div className="mb-2">
            <div className="mb-1.5 flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                <MapPin size={11} /> Справочник
            </div>
            <ul className="space-y-0.5">
                {entries.map((row) => {
                    const index = rows.indexOf(row);
                    return (
                        <DirectoryRow
                            key={directoryKey(row.entry)}
                            entry={row.entry}
                            dataRow={index}
                            selected={index === selectedIndex}
                            onPick={() => onPick(row)}
                            onHover={() => onHover(index)}
                        />
                    );
                })}
            </ul>
        </div>
    );
}

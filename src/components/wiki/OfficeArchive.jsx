import React from 'react';
import { Archive, ArchiveRestore, MapPin } from 'lucide-react';
import { iosBtnGhost, iosCard } from '../ui/ios';
/* Правила — в officeArchiveRules.js, а не в officeArchive.js: на Mac имя без
   учёта регистра совпало бы с этим файлом, и сборщик брал бы компонент вместо
   правил. */
import { archivedLine } from './officeArchiveRules';

/* Вкладка «Архив» раздела «Офисы» — офисы, убранные из справочника.
 *
 * Список, а не таблица статусов: у архивного офиса статуса нет, и залитая
 * зелёным строка «Открыт» про офис, которого больше нет, была бы неправдой.
 * В строке — что это за офис, когда и кем убран, и «Вернуть» словами: значок
 * без подписи на этой вкладке был бы единственным действием, которое
 * приходится угадывать. Нажатие на сам офис открывает его карточку — посмотреть
 * адрес и телефоны, прежде чем возвращать.
 */
export default function OfficeArchive({ offices, query, busy, onOpen, onRestore }) {
    if (!offices.length) {
        return (
            <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                    <Archive size={22} />
                </div>
                <div className="text-[15px] font-semibold text-slate-900">
                    {query ? 'Ничего не найдено' : 'Архив пуст'}
                </div>
                <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                    {query
                        ? 'В архиве нет офиса с таким названием, городом или адресом.'
                        : 'Сюда попадают офисы, убранные из справочника. Ничего не удаляется '
                          + 'насовсем — любой офис можно вернуть.'}
                </p>
            </div>
        );
    }

    return (
        <div className={`${iosCard} wiki-m-office-archive divide-y divide-slate-100 overflow-hidden`}>
            {offices.map((office) => {
                const line = archivedLine(office);
                return (
                    <div key={office.id} className="flex items-center gap-3 py-2.5 pl-4 pr-3">
                        {/* Подсказки у строки нет — как у карточек офисов: вся
                            строка нажимается, и пузырь над серединой ряда шириной
                            в экран вставал бы далеко от курсора, поверх поиска. */}
                        <button
                            type="button"
                            onClick={() => onOpen(office)}
                            className="min-w-0 flex-1 rounded-lg py-0.5 text-left transition hover:opacity-80"
                        >
                            <div className="flex flex-wrap items-baseline gap-x-2">
                                <span className="text-[14px] font-semibold text-slate-900">{office.name}</span>
                                {office.city && office.city !== office.name && (
                                    <span className="text-[12.5px] text-slate-500">{office.city}</span>
                                )}
                            </div>
                            <div className="mt-0.5 flex items-start gap-1.5 text-[13px] leading-snug text-slate-600">
                                <MapPin size={13} className="mt-[2px] shrink-0 text-slate-400" />
                                {office.no_office
                                    ? <span className="italic text-slate-500">Офиса в городе нет</span>
                                    : <span className="min-w-0">{office.address || '—'}</span>}
                            </div>
                            {line && (
                                <div className="mt-0.5 text-[11.5px] tabular-nums text-slate-400">{line}</div>
                            )}
                        </button>
                        <button
                            type="button"
                            disabled={busy}
                            onClick={() => onRestore(office)}
                            data-tip="Вернуть офис в справочник — он снова появится в списке и поиске"
                            className={`${iosBtnGhost} shrink-0 text-slate-600 disabled:opacity-50`}
                        >
                            <ArchiveRestore size={14} /> Вернуть
                        </button>
                    </div>
                );
            })}
        </div>
    );
}

import React, { useMemo, useState } from 'react';
import {
    Accessibility, AlertTriangle, Archive, Baby, Bike, Building2,
    Car, ChevronDown, ChevronRight, CircleDollarSign, DoorOpen, Droplets, ExternalLink,
    FileText, Fuel, Loader2, MapPin, Milestone, Package, PawPrint, Pencil, Phone,
    Plane, RefreshCw, ShieldCheck, Snowflake, Sparkles, Tag, Wrench,
} from 'lucide-react';
import { iosCard, iosGroupLabel, IosMenu } from '../ui/ios';
import { regionOfCity } from '../../utils/kazakhstanCities';
import { OfficeStatusBadge } from './officeBadges';
import { officeDayStatus } from './officeDayStatus';
import { officeTodayISO, scheduleLines } from './officeSchedule';
import {
    cityOffices, cityTariffs, cityUpdatedAt, commissionRange, formatDate, formatPercent,
    formatTime, hasPhoneOrders, mainInterval, optionIconKey, serviceIconKey, sourceHost,
    yandexExtras,
} from './cityRules';

/* Карточка города: всё, что оператору нужно знать о заказах в городе.
 *
 * Порядок — по частоте вопроса: сколько берут (комиссии), какие тарифы и по
 * чём, что ещё умеет Яндекс, что даёт наш парк, куда отправить водителя.
 * Пустые блоки не рисуются вовсе: заголовок над пустотой — тот же шум, что
 * прочерк в каждой строке.
 *
 * Тариф в списке — одна строка: название, требование к авто, цена «от» и
 * комиссия. Остальное (км, минута, ожидание, заказ по телефону, опции,
 * маршруты) раскрывается нажатием: двенадцать строк цены на каждый из
 * тринадцати тарифов Алматы превратили бы карточку в прайс-лист.
 */

const SERVICE_ICONS = {
    bike: Bike, car: Car, wash: Droplets, repair: Wrench, insurance: ShieldCheck,
    brand: Tag, fuel: Fuel, money: CircleDollarSign, docs: FileText, other: Sparkles,
};

const OPTION_ICONS = {
    child: Baby, pet: PawPrint, airport: Plane, door: DoorOpen, bag: Package,
    ski: Snowflake, stop: Milestone, wheelchair: Accessibility, other: Sparkles,
};

const Section = ({ title, right = null, children }) => (
    <section className="space-y-2">
        <div className="flex items-end justify-between gap-2">
            <div className={iosGroupLabel}>{title}</div>
            {right}
        </div>
        {children}
    </section>
);

/* Строка цены: подпись с уточнением слева, значение справа. */
const PriceRow = ({ label, note, value }) => (
    <div className="flex items-baseline justify-between gap-3 py-1.5">
        <div className="min-w-0 text-[13px] text-slate-700">
            {label}
            {note && <span className="text-slate-400"> · {note}</span>}
        </div>
        <div className="shrink-0 text-right text-[13px] font-medium tabular-nums text-slate-900">
            {value}
        </div>
    </div>
);

const DetailBlock = ({ title, children }) => (
    <div className="space-y-0.5">
        {title && (
            <div className="pt-1 text-[11.5px] font-semibold text-slate-500">{title}</div>
        )}
        {children}
    </div>
);

/* Раскрытый тариф: цена заказа, телефон, опции, маршруты. */
const TariffDetail = ({ tariff, cityTakesPhone }) => {
    const intervals = tariff.detail?.intervals || [];
    const several = intervals.length > 1;
    const phoneDiff = tariff.detail?.phone_diff || [];

    return (
        <div className="space-y-3 border-t border-slate-100 bg-slate-50/60 px-4 pb-3.5 pt-2.5">
            {intervals.map((interval, index) => {
                const paid = interval.options.filter((option) => !option.free);
                const free = interval.options.filter((option) => option.free);
                return (
                    <div key={`${interval.name}-${index}`} className="space-y-2.5">
                        <DetailBlock title={several ? [interval.name, interval.title].filter(Boolean).join(' · ') : null}>
                            <div className="divide-y divide-slate-200/70">
                                {interval.rows.map((row, rowIndex) => (
                                    <PriceRow key={`${row.label}-${rowIndex}`} {...row} />
                                ))}
                            </div>
                        </DetailBlock>

                        {/* Телефон — только в первом периоде: отличия считаются
                            от него (wiki/yandex_tariffs.py: _phone_diff). */}
                        {index === 0 && cityTakesPhone && (
                            <DetailBlock title="Заказ по телефону">
                                {tariff.detail?.by_phone ? (
                                    phoneDiff.length ? (
                                        <div className="divide-y divide-slate-200/70">
                                            {phoneDiff.map((row) => <PriceRow key={row.label} {...row} />)}
                                        </div>
                                    ) : (
                                        <div className="py-1.5 text-[13px] text-slate-600">
                                            Можно, на тех же условиях
                                        </div>
                                    )
                                ) : (
                                    <div className="py-1.5 text-[13px] text-slate-500">
                                        По телефону этот тариф не заказать
                                    </div>
                                )}
                            </DetailBlock>
                        )}

                        {paid.length > 0 && (
                            <DetailBlock title="Опции">
                                <div className="divide-y divide-slate-200/70">
                                    {paid.map((option) => (
                                        <PriceRow key={option.label} label={option.label}
                                                  value={option.value} />
                                    ))}
                                </div>
                            </DetailBlock>
                        )}
                        {free.length > 0 && (
                            <p className="text-[12px] leading-relaxed text-slate-500">
                                <span className="font-medium text-slate-600">Бесплатно: </span>
                                {free.map((option) => option.label).join(' · ')}
                            </p>
                        )}

                        {interval.routes.length > 0 && (
                            <DetailBlock title="Фиксированные маршруты">
                                <div className="divide-y divide-slate-200/70">
                                    {interval.routes.map((route, routeIndex) => {
                                        const next = route.rows.find((row) => row.label.startsWith('Далее'));
                                        const note = [route.note, next && `далее ${next.value}`]
                                            .filter(Boolean).join(', ');
                                        return (
                                            <PriceRow key={`${route.title}-${routeIndex}`}
                                                      label={route.title} note={note}
                                                      value={route.value || '—'} />
                                        );
                                    })}
                                </div>
                            </DetailBlock>
                        )}
                    </div>
                );
            })}
        </div>
    );
};

const CommissionPill = ({ value }) => (
    value === null || value === undefined ? null : (
        <span className="shrink-0 rounded-lg bg-blue-50 px-2 py-0.5 text-[12.5px] font-semibold tabular-nums text-blue-700 ring-1 ring-blue-100">
            {formatPercent(value)}
        </span>
    )
);

const TariffRow = ({ tariff, open, onToggle, cityTakesPhone }) => {
    const expandable = !!mainInterval(tariff);
    const body = (
        <>
            <div className="min-w-0 flex-1">
                <div className="text-[14.5px] font-medium text-slate-900">{tariff.name}</div>
                {tariff.requirement && (
                    <div className="mt-0.5 text-[12px] text-slate-500">{tariff.requirement}</div>
                )}
            </div>
            {tariff.from && (
                <span className="shrink-0 text-[13px] tabular-nums text-slate-500">
                    {tariff.source === 'yandex' ? `от ${tariff.from}` : tariff.from}
                </span>
            )}
            <CommissionPill value={tariff.commission} />
            {expandable && (
                <ChevronDown
                    size={16}
                    className={`shrink-0 text-slate-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
                />
            )}
        </>
    );

    return (
        <div>
            {expandable ? (
                <button
                    type="button"
                    onClick={onToggle}
                    aria-expanded={open}
                    className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50"
                >
                    {body}
                </button>
            ) : (
                <div className="flex items-center gap-3 px-4 py-2.5">{body}</div>
            )}
            {open && expandable && <TariffDetail tariff={tariff} cityTakesPhone={cityTakesPhone} />}
        </div>
    );
};

/* Офис «куда направлять водителя»: адрес, часы и живой статус. Нажатие
   открывает ту же карточку офиса, что во вкладке «Офисы». */
const OfficeRow = ({ office, onOpen, tick }) => {
    const today = officeTodayISO();
    const status = officeDayStatus(office, today);
    const hours = scheduleLines(office.schedule)
        .filter((line) => !line.isDayOff)
        .map((line) => `${line.days} ${line.time}`)
        .join(', ');
    return (
        <button
            type="button"
            onClick={() => onOpen(office)}
            className="flex w-full items-center gap-3 rounded-2xl bg-slate-50 px-3.5 py-3 text-left ring-1 ring-slate-200/70 transition hover:bg-slate-100 active:scale-[0.99]"
        >
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-white text-slate-500 ring-1 ring-slate-200/70">
                <Building2 size={16} />
            </span>
            <span className="min-w-0 flex-1">
                <span className="block text-[13.5px] font-medium text-slate-900">
                    {[office.city, office.address].filter(Boolean).join(', ') || office.name}
                </span>
                {hours && (
                    <span className="mt-0.5 block text-[12px] tabular-nums text-slate-500">{hours}</span>
                )}
            </span>
            <OfficeStatusBadge schedule={office.schedule} status={status} isToday dayISO={today} tick={tick} />
            <ChevronRight size={16} className="shrink-0 text-slate-400" />
        </button>
    );
};

export default function CityCard({
    city, loading = false, offices = [], canManage = false, busy = false, tick = 0,
    zoneColor = null, onEdit, onSync, onArchive, onOpenOffice,
    embedded = false,
}) {
    const [openKey, setOpenKey] = useState('');
    const tariffs = useMemo(() => cityTariffs(city), [city]);
    const extras = useMemo(() => yandexExtras(tariffs), [tariffs]);
    const range = useMemo(() => commissionRange(tariffs), [tariffs]);
    const takesPhone = useMemo(() => hasPhoneOrders(tariffs), [tariffs]);
    const places = useMemo(() => cityOffices(city, offices), [city, offices]);

    if (!city) return null;

    const region = regionOfCity(city.name);
    const updated = cityUpdatedAt(city);
    const options = city.option_commissions || [];
    const phone = city.yandex_data?.phone || city.order_phone;
    const neverSynced = city.yandex_url && !city.yandex_checked_at;
    const hasParkCommission = city.park_commission !== null && city.park_commission !== undefined;
    const hasTiles = !!range || hasParkCommission;

    const menu = canManage ? [
        /* «Обновить с Яндекса» здесь нет намеренно: кнопка стоит внизу, рядом
           с источником и временем сверки, — второй такой же пункт в меню был
           бы дублем на одном экране. */
        { key: 'edit', label: 'Изменить', icon: Pencil, onSelect: () => onEdit(city) },
        /* Вернуть город из архива — добавить его снова через «+ Город»:
           вернётся та же карточка (routes_cities). Своего переключателя
           архива у вкладки нет — решение владельца 24.09.2026. */
        { key: 'archive', label: 'В архив', icon: Archive, danger: true,
          onSelect: () => onArchive(city), separatorBefore: true },
    ] : [];

    return (
        /* embedded — карточка внутри экрана-модалки на телефоне: там название
           города уже стоит в заголовке экрана, и второе такое же под ним —
           дубль; рамка карточки внутри листа тоже лишняя. */
        <article className={`${embedded ? '' : `${iosCard} p-4 sm:p-5`} space-y-5`}>
            {/* Шапка: город, область, дата изменения, действия. */}
            <header className="flex items-start gap-3">
                {!embedded && (
                    <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-600">
                        <MapPin size={18} />
                    </span>
                )}
                <div className="min-w-0 flex-1">
                    {!embedded && (
                        <h3 className="text-[19px] font-semibold leading-tight tracking-tight text-slate-900">
                            {city.name}
                        </h3>
                    )}
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-slate-500">
                        {region && <span>{region}</span>}
                        {/* На телефоне дате справа от названия места нет. */}
                        {updated && (
                            <span className="tabular-nums sm:hidden">Обновлено {formatDate(updated)}</span>
                        )}
                        {city.serving_office_city && (
                            <span className="inline-flex items-center gap-1.5">
                                <span className="h-2 w-2 rounded-full" style={{ background: zoneColor || '#cbd5e1' }} />
                                Обслуживает офис {city.serving_office_city}
                            </span>
                        )}
                    </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                    {updated && (
                        <span className="hidden text-[12px] tabular-nums text-slate-400 sm:inline">
                            Обновлено {formatDate(updated)}
                        </span>
                    )}
                    {menu.length > 0 && <IosMenu items={menu} label="Действия с городом" />}
                </div>
            </header>

            {loading && (
                <div className="flex items-center gap-2 text-[13px] text-slate-400">
                    <Loader2 size={14} className="animate-spin" /> Загружаем тарифы…
                </div>
            )}

            {/* Комиссии — плитками, как на макете: это первое, о чём
                спрашивает водитель, и искать цифру в тексте не нужно. */}
            {/* На телефоне — одна плитка в строку: в половине ширины
                диапазон «15,1–19,2%» рвался на две строки посередине. */}
            {hasTiles && (
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                    {range && (
                        <div className="rounded-2xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200/70">
                            <div className="text-[12px] text-slate-500">Комиссия Яндекса</div>
                            <div className="mt-0.5 text-[24px] font-semibold leading-tight tabular-nums text-slate-900">{range}</div>
                        </div>
                    )}
                    {hasParkCommission && (
                        <div className="rounded-2xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200/70">
                            <div className="text-[12px] text-slate-500">Комиссия парка</div>
                            <div className="mt-0.5 text-[24px] font-semibold leading-tight tabular-nums text-slate-900">
                                {formatPercent(city.park_commission)}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {!loading && (
                <Section
                    title="Тарифы и комиссия"
                    right={phone ? (
                        <a
                            href={`tel:${String(phone).replace(/[^\d+]/g, '')}`}
                            className="inline-flex items-center gap-1 px-1 text-[12px] font-medium tabular-nums text-slate-500 hover:text-slate-800"
                            title="Номер заказа такси по телефону"
                        >
                            <Phone size={12} /> {phone}
                        </a>
                    ) : null}
                >
                    {tariffs.length > 0 ? (
                        <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70 divide-y divide-slate-100">
                            {tariffs.map((tariff) => (
                                <TariffRow
                                    key={tariff.key}
                                    tariff={tariff}
                                    open={openKey === tariff.key}
                                    onToggle={() => setOpenKey(openKey === tariff.key ? '' : tariff.key)}
                                    cityTakesPhone={takesPhone}
                                />
                            ))}
                        </div>
                    ) : (
                        <div className="rounded-2xl bg-slate-50 px-4 py-4 text-[13px] leading-relaxed text-slate-500 ring-1 ring-slate-200/70">
                            {neverSynced
                                ? 'Тарифы подтягиваются с Яндекса — это займёт пару минут.'
                                : city.yandex_url
                                    ? 'У Яндекса по этому городу тарифов нет.'
                                    : canManage
                                        ? 'Тарифов пока нет. Добавьте ссылку на тарифы Яндекса или свой тариф в редакторе.'
                                        : 'Тарифы по городу ещё не заполнены.'}
                        </div>
                    )}
                </Section>
            )}

            {/* Опции — отдельно от тарифов (задача #368): это не тариф, и в
                плитку «Комиссия Яндекса» они не входят. Под тарифами, а не
                внизу: вопрос тот же — сколько берут. */}
            {!loading && options.length > 0 && (
                <Section title="Комиссия за доп. опции">
                    <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70 divide-y divide-slate-100">
                        {options.map((option, index) => (
                            // eslint-disable-next-line react/no-array-index-key
                            <div key={index} className="flex items-center gap-3 px-4 py-2.5">
                                <div className="min-w-0 flex-1 text-[14.5px] font-medium text-slate-900">
                                    {option.name}
                                </div>
                                <CommissionPill value={option.commission} />
                            </div>
                        ))}
                    </div>
                </Section>
            )}

            {extras.length > 0 && (
                <Section title="Дополнительные услуги Яндекса">
                    <div className="flex flex-wrap gap-1.5">
                        {extras.map((label) => {
                            const Icon = OPTION_ICONS[optionIconKey(label)] || Sparkles;
                            return (
                                <span key={label} className="inline-flex items-center gap-1.5 rounded-xl bg-slate-50 px-2.5 py-1.5 text-[12.5px] text-slate-700 ring-1 ring-slate-200/70">
                                    <Icon size={13} className="text-slate-500" /> {label}
                                </span>
                            );
                        })}
                    </div>
                </Section>
            )}

            {city.services?.length > 0 && (
                <Section title="Услуги нашего парка">
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                        {city.services.map((service, index) => {
                            const Icon = SERVICE_ICONS[serviceIconKey(service.title)] || Sparkles;
                            return (
                                <div key={`${service.title}-${index}`} className="rounded-2xl bg-slate-50 px-3.5 py-3 ring-1 ring-slate-200/70">
                                    <Icon size={17} className="text-blue-600" />
                                    <div className="mt-2 text-[13.5px] font-semibold text-slate-900">{service.title}</div>
                                    {service.note && (
                                        <div className="mt-0.5 text-[12px] text-slate-500">{service.note}</div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </Section>
            )}

            {places.length > 0 && (
                <Section title="Куда направлять водителя">
                    <div className="space-y-2">
                        {places.map((office) => (
                            <OfficeRow key={office.id} office={office} onOpen={onOpenOffice} tick={tick} />
                        ))}
                    </div>
                </Section>
            )}

            {city.note && (
                <Section title="Заметка">
                    <p className="whitespace-pre-line rounded-2xl bg-slate-50 px-4 py-3 text-[13px] leading-relaxed text-slate-700 ring-1 ring-slate-200/70">
                        {city.note}
                    </p>
                </Section>
            )}

            {/* Источник и свежесть — одной строкой внизу: на вопрос «откуда
                цифры» отвечает ссылка, на «насколько свежие» — время сверки. */}
            {city.yandex_url && (
                <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-100 pt-3 text-[12px] text-slate-400">
                    <a
                        href={city.yandex_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 hover:text-slate-700"
                    >
                        Тарифы — {sourceHost(city.yandex_url)} <ExternalLink size={11} />
                    </a>
                    {city.yandex_checked_at && !city.yandex_error && (
                        <span className="tabular-nums">
                            сверено {formatDate(city.yandex_checked_at)} в {formatTime(city.yandex_checked_at)}
                        </span>
                    )}
                    {city.yandex_error && (
                        <span className="inline-flex items-center gap-1 text-amber-700">
                            <AlertTriangle size={12} />
                            Яндекс не ответил {formatDate(city.yandex_checked_at)}
                            {city.yandex_changed_at ? ` — показаны тарифы от ${formatDate(city.yandex_changed_at)}` : ''}
                        </span>
                    )}
                    {canManage && (
                        <button
                            type="button"
                            onClick={() => onSync(city)}
                            disabled={busy}
                            className="ml-auto inline-flex items-center gap-1 rounded-lg px-2 py-1 font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 disabled:opacity-50"
                        >
                            <RefreshCw size={12} className={busy ? 'animate-spin' : ''} /> Обновить
                        </button>
                    )}
                </footer>
            )}
        </article>
    );
}

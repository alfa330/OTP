import React, { useEffect, useState } from 'react';
import axios from 'axios';
import {
    AlertCircle, ArrowLeft, Building2, Globe, Loader2, MapPin, Percent, Phone, Tag,
} from 'lucide-react';
import { iosCard, iosGroupLabel, iosBtnSecondary, IosBadge } from '../ui/ios';

/* Страница таксопарка — открывается из рельса витрины.
 *
 * Отдельная страница, а не поповер у плитки: в поповер помещались телефон и
 * комиссия, описание обрезалось на второй строке, адреса не было вовсе, а
 * акции показывались числом — за ними всё равно приходилось идти во вкладку
 * «Парки». Форму намеренно повторяем за WikiArticle — шапка с заголовком,
 * тело, разделы: для оператора парк такая же справочная страница, как статья,
 * и переучиваться на второй макет незачем.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const fmtDate = (iso) => (iso
    ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
    : null);

/* Срок акции читается одной строкой: «с 01.09.26 по 30.09.26», «до 30.09.26»,
   «с 01.09.26». Пустые границы у акций обычное дело — бессрочные идут без дат. */
const promoPeriod = (promo) => {
    const from = fmtDate(promo.starts_at);
    const to = fmtDate(promo.ends_at);
    if (from && to) return `с ${from} по ${to}`;
    if (to) return `до ${to}`;
    if (from) return `с ${from}`;
    return null;
};

/* Номер набирают, а не читают: ссылка tel: с рабочего ноутбука бесполезна, а с
   телефона это один тап вместо переписывания. */
const PhoneLink = ({ phone }) => (
    <a href={`tel:${phone.replace(/[^\d+]/g, '')}`}
       className="tabular-nums text-indigo-600 hover:underline">
        {phone}
    </a>
);

const ContactRow = ({ icon: Icon, label, children }) => (
    <div className="flex items-start gap-2.5 px-4 py-3">
        <div className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500">
            <Icon size={13} />
        </div>
        <div className="min-w-0">
            <div className="text-[11px] font-medium uppercase tracking-[0.04em] text-slate-400">{label}</div>
            <div className="mt-0.5 break-words text-[13.5px] leading-snug text-slate-900">{children}</div>
        </div>
    </div>
);

export default function WikiPark({ base, headers, slug, onBack, onOpenParks }) {
    const [park, setPark] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setLoading(true);
        setError('');
        axios.get(`${base}/parks/${encodeURIComponent(slug)}`, { headers })
            .then((r) => setPark(r.data))
            .catch((e) => { setPark(null); setError(errText(e, 'Не удалось открыть парк')); })
            .finally(() => setLoading(false));
    }, [base, headers, slug]);

    if (loading) {
        return (
            <div className={`${iosCard} flex items-center justify-center gap-2 py-16 text-slate-400`}>
                <Loader2 size={18} className="animate-spin" />
                <span className="text-[13px]">Загружаем парк…</span>
            </div>
        );
    }

    if (!park) {
        return (
            <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                <div className="grid h-12 w-12 place-items-center rounded-2xl bg-red-50 text-red-500">
                    <AlertCircle size={22} />
                </div>
                <div className="text-[15px] font-semibold text-slate-900">Парк не открылся</div>
                <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">{error}</p>
                <button type="button" className={`${iosBtnSecondary} mt-4`} onClick={onBack}>
                    <ArrowLeft size={14} /> К статьям
                </button>
            </div>
        );
    }

    const online = park.phones || [];
    const offices = park.offices || [];
    // Адрес парка — его офис из справочника; собственный текст остался только у
    // записей, заведённых до перехода на выбор офиса.
    const head = park.head_office;
    const address = head?.address || park.address;
    const hasContacts = !!(park.city || online.length || address || park.website);

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                <button type="button" className={iosBtnSecondary} onClick={onBack}>
                    <ArrowLeft size={14} /> К статьям
                </button>
                {onOpenParks && (
                    <button type="button" className={iosBtnSecondary} onClick={onOpenParks}>
                        <Building2 size={14} /> Справочник парков
                    </button>
                )}
            </div>

            <article className={`${iosCard} overflow-clip`}>
                <header className="border-b border-slate-100 px-5 py-4 sm:px-7 sm:py-6">
                    <div className="flex items-start gap-4">
                        <div className="grid h-14 w-14 shrink-0 place-items-center overflow-hidden rounded-2xl bg-indigo-50 text-indigo-600">
                            {park.logo_url
                                ? <img src={park.logo_url} alt="" className="h-full w-full object-cover" />
                                : <Building2 size={24} />}
                        </div>
                        <div className="min-w-0 flex-1">
                            <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                                <IosBadge tone="blue">Таксопарк</IosBadge>
                                {park.status === 'archived' && <IosBadge tone="amber">В архиве</IosBadge>}
                                {park.promotions?.length > 0 && (
                                    <IosBadge tone="amber">
                                        <Tag size={11} /> акций: {park.promotions.length}
                                    </IosBadge>
                                )}
                            </div>
                            <h1 className="text-[24px] font-semibold leading-tight tracking-[-0.015em] text-slate-900 sm:text-[28px]">
                                {park.name}
                            </h1>
                            {park.description && (
                                <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-slate-500">
                                    {park.description}
                                </p>
                            )}
                        </div>
                    </div>

                    {/* Комиссия вынесена из общего списка контактов: ради неё парк
                        и открывают, и в строчку с городом и телефоном она теряется. */}
                    <div className="mt-4 inline-flex items-center gap-3 rounded-2xl bg-slate-50 px-4 py-3">
                        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-indigo-50 text-indigo-600">
                            <Percent size={17} />
                        </div>
                        <div>
                            <div className="text-[19px] font-semibold leading-none text-slate-900 tabular-nums">
                                {park.commission != null ? `${park.commission}%` : '—'}
                            </div>
                            <div className="mt-1 text-[12px] text-slate-500">
                                {park.commission != null ? 'Комиссия парка' : 'Комиссия не указана'}
                            </div>
                        </div>
                    </div>
                </header>

                <div className="space-y-5 px-5 py-5 sm:px-7 sm:py-6">
                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Контакты</div>
                        {hasContacts ? (
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {park.city && (
                                    <ContactRow icon={MapPin} label="Город">{park.city}</ContactRow>
                                )}
                                {address && (
                                    <ContactRow icon={MapPin} label="Адрес">
                                        {address}
                                        {head?.name && (
                                            <span className="mt-0.5 block text-[12px] text-slate-400">
                                                {head.name}
                                                {head.city ? ` · ${head.city}` : ''}
                                            </span>
                                        )}
                                    </ContactRow>
                                )}
                                {online.length > 0 && (
                                    <ContactRow icon={Phone} label="Телефон">
                                        <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                                            {online.map((phone) => (
                                                <PhoneLink key={phone} phone={phone} />
                                            ))}
                                        </div>
                                    </ContactRow>
                                )}
                                {park.website && (
                                    <ContactRow icon={Globe} label="Сайт">
                                        <a href={park.website} target="_blank" rel="noopener noreferrer"
                                           className="text-indigo-600 hover:underline">
                                            {park.website}
                                        </a>
                                    </ContactRow>
                                )}
                            </div>
                        ) : (
                            <div className={`${iosCard} px-4 py-6 text-center text-[13px] text-slate-400`}>
                                Контакты парка ещё не заполнены.
                            </div>
                        )}
                    </section>

                    {offices.length > 0 && (
                        <section className="space-y-1.5">
                            <div className={`${iosGroupLabel} flex items-center gap-1.5`}>
                                <MapPin size={12} /> Офисы парка
                            </div>
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {offices.map((office) => (
                                    <div key={office.office_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-3">
                                        <span className="text-[13.5px] font-medium text-slate-900">
                                            {office.name}
                                        </span>
                                        {office.city && (
                                            <span className="text-[12px] text-slate-400">{office.city}</span>
                                        )}
                                        <span className="flex flex-wrap gap-x-3 gap-y-0.5 text-[13px]">
                                            {(office.phones || []).map((phone) => (
                                                <PhoneLink key={phone} phone={phone} />
                                            ))}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </section>
                    )}

                    {park.promotions?.length > 0 && (
                        <section className="space-y-1.5">
                            <div className={`${iosGroupLabel} flex items-center gap-1.5`}>
                                <Tag size={12} /> Действующие акции
                            </div>
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {park.promotions.map((promo) => (
                                    <div key={promo.id} className="px-4 py-3">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="text-[14px] font-medium text-slate-900">{promo.title}</span>
                                            {promoPeriod(promo) && (
                                                <IosBadge tone="amber">{promoPeriod(promo)}</IosBadge>
                                            )}
                                        </div>
                                        {promo.description && (
                                            <p className="mt-0.5 text-[12.5px] leading-relaxed text-slate-500">
                                                {promo.description}
                                            </p>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </section>
                    )}
                </div>
            </article>
        </div>
    );
}

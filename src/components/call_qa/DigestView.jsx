import React from 'react';
import {
    AlertCircle, Calendar, ChevronRight, Loader2, MessageSquare, PhoneCall, RotateCcw, ScrollText, Sparkles,
} from 'lucide-react';
import {
    APPLE_FONT, iosBtnPrimary, iosBtnSecondary, iosCard, IosHint, IosMenu, IosSegmented, scoreTone,
} from '../ui/ios';
import AiMarkup from './AiMarkup';
import AiChatPanel from './AiChatPanel';
import PeriodView from './PeriodView';
import { useStickyHeight, useWide } from './chatLayout';
import {
    DAY_ROW_CLASS, DateLeaf, DayHeader, DayStat, MonthSection, RowChevron, Score, ShareBar, dayTitle, monthGroups,
} from './dayKit';
import { dayNeighbours, plural } from './queueDayRules';
import { isoOf } from './usePeriod';

/* Вкладка «Сводка»: что ИИ увидел в разговорах дня — по направлениям, и чат о
 * том же дне; сверху — сводка за прошедший месяц и вход в произвольный период.
 *
 * Два экрана, как у очереди: дни по месяцам (строка — заголовок сводки и цифры
 * дня), по нажатию — экран дня. На экране дня слева сводка: «Главное» по отделу
 * и разделы направлений переключателем, над текстом — точные цифры раздела
 * (их считает сервер, а не модель); справа — переписка с ИИ. На узком экране
 * сводка и чат — две вкладки одного экрана: колонка в 380 px на телефоне не
 * помещается, а лист поверх сводки закрывал бы то, о чём спрашивают.
 *
 * Над списком дней — карточка последнего прошедшего месяца (сводка за месяц
 * пишется первого числа, по просьбе владельца она на видном месте) и кнопка «За
 * период»: экран периода (PeriodView) — цифры и графики за несколько дней, до
 * месяца, и чат о них. У заголовка каждого прошедшего месяца — ссылка на его
 * сводку.
 *
 * Ссылки в тексте ([[#12]] у модели) — кнопки разговоров: открывают карточку,
 * а «Назад» возвращает сюда же (состояние — в CallQaView, useDigest/usePeriod).
 */

const DAY_COLUMNS = 'sm:grid sm:grid-cols-[minmax(0,1fr)_4rem_4.5rem_5.5rem_1rem] sm:items-center sm:gap-4';
const MONTHS_TITLE = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь',
                      'Октябрь', 'Ноябрь', 'Декабрь'];
const MONTHS_ACC = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь',
                    'октябрь', 'ноябрь', 'декабрь'];
const DEFAULT_PERIOD_DAYS = 30;

const SUGGESTIONS = [
    'Кому из сотрудников нужна помощь в первую очередь и почему?',
    'Какие неверные сведения сотрудники давали клиентам?',
    'Покажи все острые ситуации дня с цитатами',
    'С какими вопросами чаще всего обращались клиенты?',
    'Чем этот день отличается от обычного?',
];

const talksText = (n) => `${n} ${plural(n, 'разговор', 'разговора', 'разговоров')}`;
const criticalText = (n) => `${n} ${plural(n, 'критическое', 'критических', 'критических')}`;
const round = (value) => (value == null ? null : Math.round(value));

const fmtTime = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

const fmtStamp = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const sameDay = date.toDateString() === new Date().toDateString();
    return sameDay ? `сегодня в ${fmtTime(iso)}`
        : `${date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })} в ${fmtTime(iso)}`;
};

/** Период по умолчанию для кнопки «За период»: последние 30 дней. */
export const defaultPeriod = (now = new Date()) => {
    const from = new Date(now);
    from.setDate(from.getDate() - (DEFAULT_PERIOD_DAYS - 1));
    return { from: isoOf(from), to: isoOf(now) };
};

/** Последний прошедший месяц из тех, по которым есть дни, — для карточки сверху. */
export const latestCompleteMonth = (months) => Object.keys(months || {})
    .filter((key) => months[key]?.complete).sort().pop() || null;

/** Что писать о сводке месяца в карточке и у заголовка месяца. */
export const monthStatusText = (info) => {
    if (!info) return 'Сводка ещё не составлена';
    if (info.running) return 'ИИ пишет сводку за месяц…';
    if (info.status === 'ready' && info.headline) return info.headline;
    if (info.hidden) return 'Сводка — в общих разделах, их видит глава отдела';
    if (info.status === 'failed') return 'Сводку составить не удалось — откройте месяц, чтобы повторить';
    return 'Сводка ещё не составлена';
};

/* ── список дней ─────────────────────────────────────────────────────────── */

function DigestDayRow({ day, onOpen }) {
    const info = dayTitle(day.day);
    const ai = round(day.ai_avg);
    const human = round(day.human_avg);
    const who = [
        talksText(day.evaluated || 0),
        day.operators ? `${day.operators} ${plural(day.operators, 'сотрудник', 'сотрудника', 'сотрудников')}` : null,
    ].filter(Boolean).join(' · ');
    const status = day.running ? 'running' : day.headline ? 'ready' : day.digest_hidden ? 'hidden' : 'none';
    return (
        <button type="button" onClick={() => onOpen(day.day)} data-qa-day-tile={day.day}
                aria-label={[info.title, day.headline || 'сводка не составлена', talksText(day.evaluated || 0)].join(', ')}
                className={`${DAY_ROW_CLASS} ${DAY_COLUMNS}`}>
            <div className="flex min-w-0 flex-1 items-center gap-3.5">
                <DateLeaf day={day} info={info} />
                <div className="min-w-0 grow basis-0">
                    {status === 'running' ? (
                        <div className="flex items-center gap-1.5 text-[14px] font-medium text-blue-600">
                            <Loader2 size={14} className="animate-spin" aria-hidden="true" />ИИ пишет сводку…
                        </div>
                    ) : status === 'ready' ? (
                        <div className="line-clamp-2 text-[14px] font-medium leading-snug text-slate-900">{day.headline}</div>
                    ) : status === 'hidden' ? (
                        <div className="text-[14px] text-slate-500">Сводка — в общих разделах, их видит глава отдела</div>
                    ) : (
                        <div className="text-[14px] text-slate-400">Сводка не составлена</div>
                    )}
                    <div className="mt-0.5 truncate text-[12.5px] text-slate-500">
                        {day.critical > 0 && (
                            <span className="font-medium text-rose-600">{criticalText(day.critical)} · </span>
                        )}
                        {who}
                    </div>
                    <div className="mt-1 flex items-baseline gap-3 text-[12px] text-slate-400 sm:hidden">
                        <span>ИИ <Score value={ai} /></span>
                        <span>Человек <Score value={human} /></span>
                        <span>проверено <span className="font-medium tabular-nums text-slate-600">{day.reviewed || 0} из {day.evaluated || 0}</span></span>
                    </div>
                </div>
            </div>
            <Score value={ai} className="hidden text-right text-[15px] sm:block" />
            <Score value={human} className="hidden text-right text-[15px] sm:block" />
            <span className="hidden text-right text-[13px] tabular-nums text-slate-600 sm:block">
                {day.reviewed || 0} из {day.evaluated || 0}
            </span>
            <RowChevron />
        </button>
    );
}

/* Карточка последнего прошедшего месяца над списком: заголовок сводки ИИ (или
   её состояние) и итоги месяца по дням списка. Нажатие открывает месяц. */
function MonthCard({ monthKey, info, days, onOpen }) {
    const [year, month] = monthKey.split('-').map(Number);
    const own = (days || []).filter((d) => String(d.day).startsWith(`${monthKey}-`));
    const evaluated = own.reduce((sum, d) => sum + (d.evaluated || 0), 0);
    const critical = own.reduce((sum, d) => sum + (d.critical || 0), 0);
    const weighted = own.reduce((acc, d) => (d.ai_avg == null ? acc
        : { sum: acc.sum + d.ai_avg * (d.evaluated || 0), n: acc.n + (d.evaluated || 0) }), { sum: 0, n: 0 });
    const avg = weighted.n ? Math.round(weighted.sum / weighted.n) : null;
    const ready = info?.status === 'ready' && info.headline;
    const title = `Сводка за ${MONTHS_ACC[month - 1]}${year === new Date().getFullYear() ? '' : ` ${year}`}`;
    return (
        <button type="button" onClick={() => onOpen(monthKey)} data-qa-month-card={monthKey}
                aria-label={`${title}: ${monthStatusText(info)}`}
                className={`${iosCard} group flex w-full items-center gap-4 px-4 py-3.5 text-left transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 active:bg-slate-100 sm:px-5`}>
            <div className="grid h-11 w-11 shrink-0 place-items-center rounded-[10px] bg-blue-500/10 text-blue-600" aria-hidden="true">
                <ScrollText size={21} />
            </div>
            <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold text-slate-900">{title}</div>
                <div className={`mt-0.5 line-clamp-2 text-[13.5px] leading-snug ${
                    info?.running ? 'text-blue-600' : ready ? 'text-slate-700' : 'text-slate-400'}`}>
                    {info?.running && <Loader2 size={13} className="mr-1.5 inline animate-spin align-[-2px]" aria-hidden="true" />}
                    {monthStatusText(info)}
                </div>
                <div className="mt-1 truncate text-[12px] text-slate-500">
                    {talksText(evaluated)}
                    {critical > 0 && <> · <span className="font-medium text-rose-600">{criticalText(critical)}</span></>}
                    {avg != null && <> · средний балл ИИ <Score value={avg} className="text-[12px]" /></>}
                </div>
            </div>
            <RowChevron />
        </button>
    );
}

function DigestDays({ digest, period, onOpenDay }) {
    const days = digest.days;
    if (days === null) {
        return (
            <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-12 text-slate-500`} role="status">
                <Loader2 size={20} className="animate-spin" aria-hidden="true" />Загружаю дни…
            </div>
        );
    }
    if (digest.daysError) {
        return (
            <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-12 text-center`} role="alert">
                <AlertCircle size={25} className="text-rose-500" />
                <p className="text-[13px] font-medium text-slate-700">Не удалось загрузить сводки</p>
                <button type="button" onClick={digest.reloadDays} className={iosBtnSecondary}>
                    <RotateCcw size={14} />Повторить
                </button>
            </div>
        );
    }
    if (!days.length) {
        return (
            <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                <ScrollText size={26} className="text-slate-300" />
                <p className="text-[13.5px] font-medium text-slate-600">Оценённых разговоров пока нет</p>
                <p className="text-[12.5px] text-slate-400">Сводка появится после первой ночной выборки.</p>
            </div>
        );
    }
    const months = digest.months || {};
    const cardMonth = latestCompleteMonth(months);
    const groups = monthGroups(days).map((group) => ({
        ...group,
        ready: group.days.filter((d) => d.headline).length,
        monthInfo: months[group.key] || null,
    }));
    return (
        <div className="space-y-6">
            <div className="space-y-3">
                <div className="flex items-center justify-between gap-3 px-1">
                    <p className="flex min-w-0 grow basis-0 items-center gap-1.5 text-[13px] text-slate-500">
                        Сводку пишет ИИ после ночной выборки
                        <IosHint label="Как пишется сводка"
                                 text="Сводку пишет та же модель, что статьи вики, по всем оценённым разговорам дня: что случилось острого, какие ошибки повторяются, где сотрудникам не хватает знаний, что беспокоит клиентов и кому нужна помощь. Первого числа она же пишет сводку за прошедший месяц. Цифры над текстом считает сервер, а не модель. ИИ может ошибаться — сверяйтесь с разговорами по ссылкам." />
                    </p>
                    <div className="flex shrink-0 items-center gap-2">
                        <button type="button" onClick={() => period.open(defaultPeriod())} className={iosBtnSecondary}
                                title="Цифры, графики и вопросы ИИ за несколько дней — до месяца">
                            <Calendar size={14} aria-hidden="true" />За период
                        </button>
                        <button type="button" onClick={digest.reloadDays} title="Обновить" aria-label="Обновить список"
                                className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-slate-400 transition hover:bg-slate-200/60 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                            <RotateCcw size={16} />
                        </button>
                    </div>
                </div>
                {cardMonth && (
                    <MonthCard monthKey={cardMonth} info={months[cardMonth]} days={days} onOpen={period.openMonth} />
                )}
            </div>
            {groups.map((group) => (
                <MonthSection key={group.key} group={group} columns={DAY_COLUMNS}
                              aside={(
                                  <span className="flex items-center gap-2 text-[12.5px] text-slate-500">
                                      {group.ready > 0 && (
                                          <span>{group.ready} {plural(group.ready, 'сводка', 'сводки', 'сводок')}</span>
                                      )}
                                      {group.monthInfo?.complete && group.key !== cardMonth && (
                                          <button type="button" onClick={() => period.openMonth(group.key)}
                                                  className="inline-flex items-center gap-0.5 rounded-lg px-1.5 py-0.5 font-medium text-blue-600 transition hover:bg-blue-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                                              Сводка за месяц<ChevronRight size={13} aria-hidden="true" />
                                          </button>
                                      )}
                                  </span>
                              )}
                              head={(
                                  <>
                                      <span>День</span>
                                      <span className="text-right" title="Средний балл ИИ за день">ИИ</span>
                                      <span className="text-right" title="Средний балл человека за день">Человек</span>
                                      <span className="text-right" title="Проверено человеком из оценённых ИИ">Проверено</span>
                                      <span />
                                  </>
                              )}
                              renderDay={(day) => <DigestDayRow day={day} onOpen={onOpenDay} />} />
            ))}
        </div>
    );
}

/* ── экран дня ───────────────────────────────────────────────────────────── */

/* Точные цифры раздела над текстом ИИ — те же клетки, что в сводке дня очереди. */
function SectionStats({ stats, usual }) {
    if (!stats) return null;
    const ai = round(stats.ai_avg);
    const human = round(stats.human_avg);
    const usualAi = round(usual?.ai_avg);
    const evaluated = stats.evaluated || 0;
    const reviewed = stats.reviewed || 0;
    return (
        <dl className="grid grid-cols-2 gap-px border-b border-slate-100 bg-slate-100 sm:grid-cols-4">
            <DayStat label="Оценено" value={evaluated}
                     sub={stats.operators ? `${stats.operators} ${plural(stats.operators, 'сотрудник', 'сотрудника', 'сотрудников')}` : null}
                     subTone="text-slate-400" />
            <DayStat label="Средний балл ИИ" value={ai} tone={ai != null ? scoreTone(ai) : null}
                     sub={usualAi != null ? `обычно ${usualAi}` : null}
                     subTone={usualAi != null && ai != null && ai < usualAi - 4 ? 'font-medium text-amber-600' : 'text-slate-400'} />
            <DayStat label="Критических" value={stats.critical ?? 0}
                     tone={stats.critical ? 'red' : null}
                     sub={stats.below_60 ? `ниже 60 баллов — ${stats.below_60}` : 'ниже 60 баллов нет'}
                     subTone="text-slate-400" />
            <DayStat label="Проверено людьми" value={`${reviewed} из ${evaluated}`}
                     sub={human != null ? `средний балл человека ${human}` : null} subTone="text-slate-400">
                <ShareBar share={evaluated ? reviewed / evaluated : 0} />
            </DayStat>
        </dl>
    );
}

/* Под текстом — только когда он написан: число разговоров уже стоит в шапке
   карточки, второй раз его не повторяем. */
function SummaryFooter({ data }) {
    if (!data?.generated_at) return null;
    return (
        <div className="flex items-center gap-1.5 border-t border-slate-100 px-5 py-2.5 text-[11.5px] text-slate-400 sm:px-6">
            <Sparkles size={12} aria-hidden="true" />
            <span>Сводка ИИ · {fmtStamp(data.generated_at)}</span>
            <IosHint label="Откуда сводка" align="left"
                     text="Текст пишет модель по оценкам ИИ и транскриптам разговоров. Она может ошибаться: прежде чем делать выводы о сотруднике, откройте разговор по ссылке. Где разговор уже проверил человек, сводка опирается на его оценку." />
        </div>
    );
}

function Placeholder({ icon: Icon = ScrollText, tone = 'text-slate-300', title, text = null, children = null }) {
    return (
        <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
            <Icon size={26} className={tone} aria-hidden="true" />
            <p className="text-[14px] font-semibold text-slate-700">{title}</p>
            {text && <p className="max-w-md text-[12.5px] leading-relaxed text-slate-500">{text}</p>}
            {children && <div className="mt-2 flex flex-wrap justify-center gap-2">{children}</div>}
        </div>
    );
}

/* Опрос генерации потерял связь — сводка, может быть, уже готова. Честно
   говорим и даём проверить, вместо вечного «страница обновится сама». */
function StalledPlaceholder({ digest }) {
    return (
        <Placeholder icon={AlertCircle} tone="text-amber-500" title="Связь с сервером прервалась"
                     text="Сводка, возможно, уже дописана — проверьте.">
            <button type="button" onClick={digest.reloadView} className={iosBtnSecondary}>
                <RotateCcw size={14} />Проверить
            </button>
        </Placeholder>
    );
}

function SummaryPane({ digest, data, stalled, onOpenRef }) {
    const sections = data.sections || [];
    const options = [
        ...(data.overview_html ? [{ value: 'overview', label: 'Главное' }] : []),
        ...sections.map((s) => ({
            value: s.key, label: s.direction,
            icon: s.family === 'chats'
                ? <MessageSquare size={13} className="text-slate-400" aria-hidden="true" />
                : <PhoneCall size={13} className="text-slate-400" aria-hidden="true" />,
        })),
    ];
    const selected = options.some((o) => o.value === digest.section) ? digest.section : options[0]?.value;
    const section = sections.find((s) => s.key === selected);
    const running = Boolean(data.running);
    const hasText = Boolean(data.overview_html) || sections.some((s) => s.html);
    const canGenerate = data.can_generate !== false;

    if (!data.evaluated) {
        return (
            <div className={iosCard}>
                <Placeholder title="За этот день нет оценённых разговоров"
                             text="Сводку пишут по оценкам ИИ — оценённых разговоров с датой этого дня нет." />
            </div>
        );
    }
    if (!hasText && !running && data.status === 'ready' && data.hidden_own > 0) {
        // Сводка составлена, но разговоры зрителя вошли только в разделы, общие
        // с чужими направлениями, — их ему не показывают. «Сводки ещё нет» и
        // кнопка «Составить» здесь были бы неправдой: составлять нечего.
        return (
            <div className={`${iosCard} overflow-hidden`}>
                <SectionStats stats={data.stats} usual={data.usual} />
                <Placeholder title="Сводка этого дня — в общих разделах"
                             text="Разговоры ваших направлений вошли в разделы вместе с другими направлениями. Такие разделы видит глава отдела." />
            </div>
        );
    }
    if (!hasText) {
        return (
            <div className={`${iosCard} overflow-hidden`}>
                <SectionStats stats={data.stats} usual={data.usual} />
                {running && stalled ? (
                    <StalledPlaceholder digest={digest} />
                ) : running ? (
                    <Placeholder icon={Loader2} tone="animate-spin text-blue-500" title="ИИ пишет сводку…"
                                 text={`Читает ${talksText(data.evaluated)} и их оценки. Обычно это около минуты — страница обновится сама.`} />
                ) : data.status === 'failed' && data.error ? (
                    <Placeholder icon={AlertCircle} tone="text-rose-500" title="Сводку составить не удалось" text={data.error}>
                        {canGenerate && (
                            <button type="button" onClick={() => digest.generate(false)} disabled={digest.generating}
                                    className={iosBtnPrimary}><RotateCcw size={15} />Повторить</button>
                        )}
                    </Placeholder>
                ) : canGenerate ? (
                    <Placeholder icon={Sparkles} tone="text-blue-500" title="Сводки за этот день ещё нет"
                                 text="ИИ пишет её после ночной выборки. Составить сейчас — около минуты: что случилось острого, какие ошибки повторяются, где не хватает знаний и кому нужна помощь.">
                        <button type="button" onClick={() => digest.generate(false)} disabled={digest.generating}
                                className={iosBtnPrimary}>
                            {digest.generating ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                            Составить сводку
                        </button>
                    </Placeholder>
                ) : (
                    <Placeholder icon={Sparkles} tone="text-blue-500" title="Сводки за этот день ещё нет"
                                 text="ИИ пишет её после ночной выборки." />
                )}
            </div>
        );
    }

    return (
        <div className="space-y-3">
            {options.length > 1 && (
                <div className="-mx-1 overflow-x-auto px-1 pb-0.5">
                    {/* min-w-max: на телефоне оболочка прокручивает полосу вбок, но без
                        него кнопки сжимались и «Яндекс Регистрация» обрезалась. */}
                    <IosSegmented value={selected} options={options} onChange={digest.setSection}
                                  ariaLabel="Раздел сводки" className="min-w-max" />
                </div>
            )}
            <article className={`${iosCard} overflow-hidden`} aria-label={section ? `Сводка: ${section.direction}` : 'Сводка: главное'}>
                <SectionStats stats={section ? section.stats : data.stats} usual={section ? section.usual : data.usual} />
                <div className="px-5 py-5 sm:px-6">
                    {selected === 'overview' ? (
                        <AiMarkup html={data.overview_html} onOpenRef={onOpenRef} />
                    ) : section?.html ? (
                        <AiMarkup html={section.html} onOpenRef={onOpenRef} />
                    ) : section?.missing ? (
                        <Placeholder icon={Sparkles} tone="text-blue-500" title="Этого направления в сводке ещё нет"
                                     text={canGenerate
                                         ? 'Его разговоры оценили после того, как сводку составили. Обновите сводку — ИИ допишет и его.'
                                         : 'Его разговоры оценили после того, как сводку составили, — ИИ допишет его при следующем обновлении.'}>
                            {canGenerate && (
                                <button type="button" onClick={() => digest.generate(false)} disabled={digest.generating || running}
                                        className={iosBtnSecondary}><RotateCcw size={14} />Обновить сводку</button>
                            )}
                        </Placeholder>
                    ) : (
                        /* Упавший раздел дописывает обычное «Обновить»: сервер
                           переписывает только недостающее, force для этого не нужен. */
                        <Placeholder icon={AlertCircle} tone="text-amber-500" title="ИИ не смог написать этот раздел"
                                     text={section?.error || 'Раздел допишется при следующем обновлении сводки.'}>
                            {canGenerate && (
                                <button type="button" onClick={() => digest.generate(false)} disabled={digest.generating || running}
                                        className={iosBtnSecondary}><RotateCcw size={14} />Дописать раздел</button>
                            )}
                        </Placeholder>
                    )}
                </div>
                <SummaryFooter data={data} />
            </article>
        </div>
    );
}

/* Состояние сводки справа от даты: пишется / устарела / составить. Пока всё
   свежо — ничего: время составления стоит под текстом. */
function DigestStatus({ digest, data, trouble }) {
    if (!data) return null;
    const hasText = Boolean(data.overview_html) || (data.sections || []).some((s) => s.html);
    // Связь пропала посреди опроса или обновления — сводка на экране могла
    // устареть, говорим об этом и даём проверить.
    if (hasText && trouble) {
        return (
            <button type="button" onClick={digest.reloadView}
                    className="inline-flex items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-[13px] font-medium text-amber-700 transition hover:bg-amber-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60">
                <AlertCircle size={14} aria-hidden="true" />Нет связи — проверить
            </button>
        );
    }
    // Первая генерация видна в самой карточке («ИИ пишет сводку…» вместо
    // текста); здесь — только перегенерация поверх уже написанной сводки.
    if (data.running) {
        return hasText ? (
            <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-blue-600" role="status">
                <Loader2 size={14} className="animate-spin" aria-hidden="true" />ИИ обновляет сводку…
            </span>
        ) : null;
    }
    const menu = data.can_force && hasText ? (
        <IosMenu label="Ещё" items={[{ key: 'force', label: 'Переписать заново', icon: RotateCcw,
                                         onSelect: () => digest.generate(true) }]} />
    ) : null;
    // Устарела (новые оценки) или недописана (упал раздел, не написалось
    // «Главное») — одна кнопка: сервер допишет только недостающее.
    if (hasText && (data.stale || data.incomplete) && data.can_generate !== false) {
        const label = data.stale
            ? (data.new_evaluations > 0
                ? `Новых оценок: ${data.new_evaluations} — обновить`
                : 'Оценки изменились — обновить')
            : 'Сводка дописана не полностью — дописать';
        return (
            <span className="inline-flex items-center gap-1">
                <button type="button" onClick={() => digest.generate(false)} disabled={digest.generating}
                        className="inline-flex items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-[13px] font-medium text-amber-700 transition hover:bg-amber-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60 disabled:opacity-60">
                    {digest.generating ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                    {label}
                </button>
                {menu}
            </span>
        );
    }
    return menu;
}

function DigestDay({ digest, scoped, chatRestore, onOpenRef }) {
    const day = digest.openDay;
    const data = digest.view?.day === day ? digest.view.data : null;
    const loading = digest.view?.day === day && digest.view.loading && !data;
    const error = digest.view?.day === day ? digest.view.error : '';
    // Сводка на экране, а связи нет: опрос генерации сдался или обновление упало.
    const trouble = digest.view?.day === day && Boolean(data) && (digest.view.stalled || Boolean(error));
    const { earlier, later } = dayNeighbours(digest.days || [], day);
    const { pane, setPane } = digest;
    const [chatRef, chatHeight] = useStickyHeight(true);
    const wide = useWide();
    const chatCount = (digest.chat.messages || []).filter((m) => m.role === 'assistant').length;

    return (
        <div className="space-y-4">
            <DayHeader day={day} earlier={earlier} later={later} onClose={digest.close}
                       onOpenDay={digest.open} titleId="qa-digest-day-title"
                       right={<DigestStatus digest={digest} data={data} trouble={trouble} />} />

            <div className="xl:hidden">
                <IosSegmented value={pane} onChange={setPane} stretch ariaLabel="Сводка или чат"
                              options={[
                                  { value: 'summary', label: 'Сводка', icon: <ScrollText size={14} aria-hidden="true" /> },
                                  { value: 'chat', label: 'Спросить ИИ', icon: <Sparkles size={14} aria-hidden="true" />,
                                    count: chatCount },
                              ]} />
            </div>

            <div className="xl:grid xl:grid-cols-[minmax(0,1fr)_380px] xl:items-start xl:gap-4">
                <div data-qa-pane="summary" className={pane === 'chat' ? 'hidden xl:block' : ''}>
                    {loading ? (
                        <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-16 text-[13px] text-slate-500`} role="status">
                            <Loader2 size={18} className="animate-spin" aria-hidden="true" />Загружаю сводку…
                        </div>
                    ) : error && !data ? (
                        <div className={iosCard}>
                            <Placeholder icon={AlertCircle} tone="text-rose-500" title={error}>
                                <button type="button" onClick={digest.reloadView} className={iosBtnSecondary}>
                                    <RotateCcw size={14} />Повторить
                                </button>
                            </Placeholder>
                        </div>
                    ) : data ? (
                        <SummaryPane digest={digest} data={data} stalled={trouble} onOpenRef={onOpenRef} />
                    ) : null}
                </div>
                <div ref={chatRef} data-qa-pane="chat"
                     className={`${pane === 'summary' ? 'hidden xl:block' : ''} xl:sticky xl:top-4`}>
                    <AiChatPanel
                        title="Спросить ИИ" subtitle={`о разговорах дня${scoped ? ' · ваши направления' : ''}`}
                        intro="ИИ видит все оценённые разговоры дня: оценки, цитаты и транскрипты (у длинных середина пропущена). Спросите о чём угодно — по каждому ответу можно открыть разговор."
                        suggestions={SUGGESTIONS} thread={{ ...digest.chat, key: digest.chat.day }} asking={digest.asking}
                        draft={digest.draft} setDraft={digest.setDraft} draftFocus={digest.draftFocus}
                        onAsk={digest.ask} onClear={digest.clearChat} onSuggest={digest.suggest} onOpenRef={onOpenRef}
                        visible={wide || pane === 'chat'} restore={chatRestore}
                        placeholder="Спросите о разговорах дня…" busyText="Читаю разговоры дня…"
                        className="h-[min(72vh,720px)] xl:h-auto"
                        style={chatHeight ? { height: chatHeight } : undefined} />
                </div>
            </div>
        </div>
    );
}

// Без экрана периода (стенд, старый вызов) — кнопки периода просто не действуют.
const NO_PERIOD = { range: null, open: () => {}, openMonth: () => {} };

export default function DigestView({ digest, period = NO_PERIOD, scoped = false, chatRestore = null, onOpenRef }) {
    return (
        <div style={{ fontFamily: APPLE_FONT }}>
            {period?.range
                ? <PeriodView period={period} scoped={scoped} chatRestore={chatRestore} onOpenRef={onOpenRef} />
                : digest.openDay
                    ? <DigestDay digest={digest} scoped={scoped} chatRestore={chatRestore} onOpenRef={onOpenRef} />
                    : <DigestDays digest={digest} period={period} onOpenDay={(day) => digest.open(day, { fromList: true })} />}
        </div>
    );
}

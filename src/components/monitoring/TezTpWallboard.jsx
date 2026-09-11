import React from 'react';
import FaIcon from '../common/FaIcon';
import { APPLE_FONT, iosCard } from '../ui/ios';
import { Grid, KeyTile, Section, StatTile } from './SzovWallboardTiles';
import { readWallboardMetric } from './szovWallboardShared';
import TezOperatorsTable from './TezOperatorsTable';
import {
    TEZ_SL_THRESHOLD_SECONDS,
    TEZ_TP_METRIC_MAP,
    formatCount,
    formatSeconds,
    tezBreakChip,
} from './tezWallboardShared';

/*
 * «Табло Тез КЦ» — направление ТП (техподдержка). У Тез своя телефония, Binotel, и у ТП есть
 * настоящая очередь, поэтому раскладка повторяет «Линию» СЗоВ: взгляду руководителя не нужно
 * переучиваться, переходя с одного табло на другое.
 *
 * Чего здесь нет и почему:
 *   - «Перезвона»: такого статуса в Binotel не существует вовсе (пространство статусов —
 *     активен / работа в CRM / перерыв / неактивен). Заглушка на стене врала бы каждую минуту;
 *   - «Сброса на приветствии»: отвал на IVR виден только в тающей рабочей очереди кабинета.
 *
 * Кирпичи (секция, сетка, плитки) — общие с табло СЗоВ: свои размеры цифр и своя палитра
 * означали бы, что два экрана на одной стене выглядят как два разных продукта.
 */

/** Ключевая плитка по каталогу ТП: подпись, значение и тон приходят оттуда. */
const MetricKeyTile = ({ metricKey, snapshot, scale = 1 }) => {
    const metric = TEZ_TP_METRIC_MAP[metricKey];
    const { value, tone } = readWallboardMetric(metric, snapshot);
    return <KeyTile label={metric.label} value={value} hint={metric.hint} tone={tone} scale={scale} />;
};

/** Плитка дня/операторов. Показатель-пара отдаёт второе число приглушённым. */
const MetricStatTile = ({ metricKey, snapshot, scale = 1 }) => {
    const metric = TEZ_TP_METRIC_MAP[metricKey];
    const { value, secondary, tone } = readWallboardMetric(metric, snapshot);
    return <StatTile label={metric.label} value={value} secondary={secondary} tone={tone} scale={scale} />;
};

/*
 * Правая колонка табло: имя сверху, время в статусе под ним.
 *
 * Написана здесь, а не взята у СЗоВ: `StatusBlock`/`StatusColumn` прибиты к SzovWallboardView
 * регексами стражей, и вынос разметки из того файла уронил бы тесты чужого раздела. Форма
 * элемента списка та же (`reason_key`, `seconds`), поэтому и выглядит колонка так же.
 *
 * Блок один — «На перерыве»: второго («Перезвон») у Binotel нет. Снизу отделён состав
 * направления: счётчики «онлайн», «перерыв» и «прочие» в сумме дают его целиком, но со стены
 * складывать неудобно, а без знаменателя не понять, много «двое на перерыве» или мало.
 * Колонка одна на оба направления Тез — у ОП тот же список и тот же смысл.
 */
export const TezStatusColumn = ({ now, scale = 1 }) => {
    const items = Array.isArray(now.break_list) ? now.break_list : [];
    const nameSize = `clamp(1rem, ${(1.25 * scale).toFixed(2)}vw, ${(1.375 * scale).toFixed(3)}rem)`;
    return (
        /*
         * Высота ограничена экраном, а список внутри скроллится. Без этого в обед, когда на
         * перерыве человек десять, хвост списка и строка «Всего сотрудников» просто уезжают
         * за нижний край стены — причём молча: колонка растёт вместе с содержимым, и никакой
         * полосы прокрутки не появляется. Замерено на 1920×1080: при десяти длинных ФИО
         * видно семь. То же поведение есть и у табло СЗоВ, но чинить его там — отдельная
         * правка чужого раздела.
         */
        <div className={`${iosCard} flex max-h-[calc(100vh-7rem)] flex-col p-5`}>
            <div className="mb-2 flex items-center gap-2.5 text-[15px] font-semibold text-slate-500">
                <FaIcon className="fas fa-list-ul"></FaIcon>
                <span>На перерыве</span>
            </div>
            {items.length === 0 ? (
                <div className="py-1.5 text-[15px] text-slate-400">Никого</div>
            ) : (
                <ul className="min-h-0 divide-y divide-slate-100 overflow-y-auto">
                    {items.map((item) => {
                        const chip = tezBreakChip(item);
                        return (
                            <li key={`${item.operator_id ?? item.name}-${item.since ?? ''}`} className="py-3">
                                <div className="flex items-start gap-2">
                                    <span className="min-w-0 leading-snug text-slate-800" style={{ fontSize: nameSize }}>
                                        {item.name}
                                    </span>
                                    {chip ? (
                                        <span className={`mt-0.5 shrink-0 rounded-md px-2 py-0.5 text-[12px] font-medium ${chip.className}`}>
                                            {chip.label}
                                        </span>
                                    ) : null}
                                </div>
                                {/* Время в статусе кабинет отдаёт не всегда; formatDuration нарисовал бы
                                    «0:00» — «только что ушёл», хотя мы этого не знаем. */}
                                <div className="mt-0.5 text-[14px] font-medium tabular-nums text-slate-400">
                                    {formatSeconds(item.seconds)}
                                </div>
                            </li>
                        );
                    })}
                </ul>
            )}
            <div className="mt-auto border-t border-slate-200/70 pt-4 text-[14px] text-slate-400">
                Всего сотрудников: {formatCount(now.operators_total)}
            </div>
        </div>
    );
};

/** Тело табло ТП. Отдельный компонент, чтобы встроенный и полноэкранный режим шли одной разметкой. */
export default function TezTpWallboardBody({ snapshot, scale = 1 }) {
    const now = snapshot?.now || {};
    // Порог берём из снимка: его пишет сам кабинет в подписи колонки, и меняют его там же.
    const slSeconds = Number(snapshot?.sl_threshold_seconds) || TEZ_SL_THRESHOLD_SECONDS;

    // «Работа в CRM» и «не на линии» своей плитки не имеют — чтобы люди в этих статусах не
    // пропадали из виду, показываем их приглушённой строкой, и только когда они есть.
    const asideParts = [
        [Number(now.operators_other) || 0, 'в прочих статусах (работа в CRM, не на линии)'],
    ].filter(([count]) => count > 0).map(([count, label]) => `${formatCount(count)} ${label}`);

    return (
        /*
         * Сверху — плитки и колонка перерывов, под ними во всю ширину список людей.
         *
         * Список вынесен ИЗ левой колонки намеренно: оставь его внутри, и колонка «На
         * перерыве» растянется на высоту таблицы, оставив справа пустое поле в треть экрана.
         * Теперь высоту сетки задают плитки, а таблице достаётся вся ширина стены.
         */
        <div className="space-y-4" style={{ fontFamily: APPLE_FONT }}>
            {/* Две колонки: показатели слева, перерывы узкой колонкой справа.
                На узком экране колонка уезжает вниз. */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_19rem]">
                <div className="space-y-4">
                    <Section icon="fa-bolt" title="Ключевые показатели · сейчас">
                        <Grid>
                            <MetricKeyTile metricKey="tp_queue" snapshot={snapshot} scale={scale} />
                            <MetricKeyTile metricKey="tp_ar" snapshot={snapshot} scale={scale} />
                            <MetricKeyTile metricKey="tp_online" snapshot={snapshot} scale={scale} />
                            <MetricKeyTile metricKey="tp_break" snapshot={snapshot} scale={scale} />
                        </Grid>
                    </Section>

                    <Section
                        icon="fa-chart-bar"
                        title="Показатели за день"
                        /* Подпись SL стоит здесь, а не в плитке: у кабинета Binotel уровень
                           обслуживания считается ОТ ПРИНЯТЫХ, а у табло СЗоВ — от всех попавших
                           в очередь. Одинаково подписанные плитки значили бы разное, и разницу
                           надо назвать словами ровно один раз. */
                        right={(
                            <span className="hidden text-right text-[12.5px] leading-tight text-slate-400 sm:block">
                                SL — доля принятых, отвеченных за {slSeconds} с
                            </span>
                        )}
                    >
                        <Grid>
                            <MetricStatTile metricKey="tp_served_pair" snapshot={snapshot} scale={scale} />
                            <MetricStatTile metricKey="tp_lost" snapshot={snapshot} scale={scale} />
                            <MetricStatTile metricKey="tp_sl" snapshot={snapshot} scale={scale} />
                            <MetricStatTile metricKey="tp_avg_wait" snapshot={snapshot} scale={scale} />
                        </Grid>
                    </Section>

                    <Section icon="fa-headset" title="Операторы">
                        <Grid>
                            <MetricStatTile metricKey="tp_free" snapshot={snapshot} scale={scale} />
                            <MetricStatTile metricKey="tp_talking" snapshot={snapshot} scale={scale} />
                            <MetricStatTile metricKey="tp_avg_talk" snapshot={snapshot} scale={scale} />
                            <MetricStatTile metricKey="tp_outgoing_pair" snapshot={snapshot} scale={scale} />
                        </Grid>
                        {asideParts.length > 0 ? (
                            <div className="mt-3 px-1 text-[14px] text-slate-400">
                                Ещё {asideParts.join(' · ')}
                            </div>
                        ) : null}
                    </Section>
                </div>

                {/* Колонка «На перерыве» и список операторов соседствуют намеренно, хотя
                    ушедшие на перерыв есть и там и там: колонку считает кабинет Binotel, и
                    она полна всегда, а статусы списка приходят от телефонов, а обновлён пока
                    не весь флот. Когда события будет слать каждый телефон, колонка станет
                    подмножеством списка — и тогда её место займёт освободившаяся ширина. */}
                <TezStatusColumn now={now} scale={scale} />
            </div>

            {/* Поимённо — под всей сеткой: плитка отвечает «сколько», строка — «кто именно и
                что у него за день», и семи колонкам нужна вся ширина стены. */}
            <TezOperatorsTable rows={snapshot?.roster} direction="tp" scale={scale} />
        </div>
    );
}

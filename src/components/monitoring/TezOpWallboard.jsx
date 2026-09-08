import React from 'react';
import { APPLE_FONT } from '../ui/ios';
import { Grid, KeyTile, Section, StatTile } from './SzovWallboardTiles';
import { readWallboardMetric } from './szovWallboardShared';
import { TEZ_OP_METRIC_MAP, formatCount } from './tezWallboardShared';
import { TezStatusColumn } from './TezTpWallboard';

/*
 * «Табло Тез КЦ» — направление ОП (отдел продаж). Второй экран того же раздела: переключатель
 * стоит в шапке, кирпичи и колонка — те же, что у ТП, чтобы взгляд не переучивался.
 *
 * Отличие одно, но принципиальное: у ОП НЕТ очереди. Звонки приходят прямо на продавца, поэтому
 * ни «в очереди», ни SL, ни среднего ожидания у этого направления не существует — их нет ни на
 * стене, ни в каталоге: плитка с вечным прочерком обещает показатель, которого не бывает.
 * Главная величина дня здесь — исходящие: сколько набрали и до скольких дозвонились.
 */

/** Ключевая плитка по каталогу ОП: подпись, значение и тон приходят оттуда. */
const MetricKeyTile = ({ metricKey, snapshot, scale = 1 }) => {
    const metric = TEZ_OP_METRIC_MAP[metricKey];
    const { value, tone } = readWallboardMetric(metric, snapshot);
    return <KeyTile label={metric.label} value={value} hint={metric.hint} tone={tone} scale={scale} />;
};

/** Плитка дня. Показатель-пара отдаёт второе число приглушённым. */
const MetricStatTile = ({ metricKey, snapshot, scale = 1 }) => {
    const metric = TEZ_OP_METRIC_MAP[metricKey];
    const { value, secondary, tone } = readWallboardMetric(metric, snapshot);
    return <StatTile label={metric.label} value={value} secondary={secondary} tone={tone} scale={scale} />;
};

/** Тело табло ОП. Отдельный компонент, чтобы встроенный и полноэкранный режим шли одной разметкой. */
export default function TezOpWallboardBody({ snapshot, scale = 1 }) {
    const now = snapshot?.now || {};

    // «Работа в CRM» и «не на линии» своей плитки не имеют — чтобы люди в этих статусах не
    // пропадали из виду, показываем их приглушённой строкой, и только когда они есть.
    const asideParts = [
        [Number(now.operators_other) || 0, 'в прочих статусах (работа в CRM, не на линии)'],
    ].filter(([count]) => count > 0).map(([count, label]) => `${formatCount(count)} ${label}`);

    return (
        // Раскладка та же, что у ТП: показатели слева, перерывы узкой колонкой справа.
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_19rem]" style={{ fontFamily: APPLE_FONT }}>
            <div className="space-y-4">
                <Section icon="fa-bolt" title="Ключевые показатели · сейчас">
                    {/* Онлайн = свободны + в разговоре, и все три числа стоят рядом
                        намеренно: сумма проверяется взглядом за секунду. «Онлайн» у продавцов
                        считается по регистрации телефона, а не по статусу — статусы в отделе
                        продаж почти не переключают, и плитка по ним была бы вечным нулём при
                        полусотне сделанных за день звонков. */}
                    <Grid>
                        <MetricKeyTile metricKey="op_online" snapshot={snapshot} scale={scale} />
                        <MetricKeyTile metricKey="op_free" snapshot={snapshot} scale={scale} />
                        <MetricKeyTile metricKey="op_talking" snapshot={snapshot} scale={scale} />
                        <MetricKeyTile metricKey="op_break" snapshot={snapshot} scale={scale} />
                    </Grid>
                    {asideParts.length > 0 ? (
                        <div className="mt-3 px-1 text-[14px] text-slate-400">
                            Ещё {asideParts.join(' · ')}
                        </div>
                    ) : null}
                </Section>

                <Section
                    icon="fa-chart-bar"
                    title="Показатели за день"
                    /* Подпись стоит здесь по той же причине, что подпись SL у ТП: плитка
                       «Ср. разговор» на обоих направлениях подписана одинаково, а значит
                       разное — у ТП это принятый входящий, у ОП исходящий, и рядом на одной
                       стене их сравнят. Подсказки каталога («На исходящий разговор») на
                       экран не выходят: StatTile подписи не рисует, она питает виджет.
                       Заодно строка объясняет, почему в секции три плитки, а не четыре. */
                    right={(
                        <span className="hidden text-right text-[12.5px] leading-tight text-slate-400 sm:block">
                            Показатели — по исходящим: входящих нет
                        </span>
                    )}
                >
                    {/* Три плитки, а не четыре: входящих у отдела продаж нет вовсе, и
                        «Принято», «Потеряно» и AR стояли бы здесь вечными нулями. cols={3}
                        нужен, чтобы четвёртая ячейка не осталась дырой — со стены пустая
                        ячейка читается как «сюда что-то не приехало». */}
                    <Grid cols={3}>
                        {/* Пара направления: главное число — поднято, приглушённое — совершено. */}
                        <MetricStatTile metricKey="op_outgoing_pair" snapshot={snapshot} scale={scale} />
                        <MetricStatTile metricKey="op_dial_rate" snapshot={snapshot} scale={scale} />
                        <MetricStatTile metricKey="op_avg_talk" snapshot={snapshot} scale={scale} />
                    </Grid>
                </Section>
            </div>

            <TezStatusColumn now={now} scale={scale} />
        </div>
    );
}

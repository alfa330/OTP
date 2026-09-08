import React, { useCallback, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import FaIcon from '../common/FaIcon';
import FullscreenSheet from '../common/FullscreenSheet';
import { APPLE_FONT, iosCard, iosBtnGhost } from '../ui/ios';
import { formatClock, wallboardStaleNotice } from './szovWallboardShared';
import { SegmentedSwitch } from './SzovWallboardTiles';
import {
    TEZ_WALLBOARD_DIRECTIONS,
    TEZ_WALLBOARD_DIRECTION_LIST,
    useTezOpWallboardSnapshot,
    useTezTpWallboardSnapshot,
} from './tezWallboardShared';
import TezTpWallboardBody from './TezTpWallboard';
import TezOpWallboardBody from './TezOpWallboard';

/*
 * «Табло Тез КЦ» — онлайн-мониторинг двух направлений отдела (задача #292): ТП (техподдержка,
 * у неё есть очередь) и ОП (отдел продаж, очереди нет). Источник у обоих один — кабинет
 * my.binotel.kz: у Тез своя телефония, не Oktell, поэтому и раздел свой, а не ветка в «Табло
 * СЗоВ» (там на каждую секцию и плитку стоит по стражу, и общий экран сломал бы чужой).
 *
 * Экран рассчитан на вывод на стену, поэтому:
 *   - обновляется сам, без перезагрузки страницы (опрос раз в 20 с, снимок общий на всех зрителей);
 *   - цветом помечаем только то, что несёт смысл: AR выше нормы, SL ниже порога и статусы людей;
 *   - когда кабинет молчит, цифры на стене НЕ гасим — вешаем янтарный чип с возрастом данных.
 *
 * Кнопок «Отбивка» и «Перерывы не по графику» здесь нет: и то и другое у Тез не заведено.
 * Кнопки «Виджет» тоже нет — окно «поверх окон» у документа одно, и оно сейчас намертво
 * привязано к направлениям СЗоВ; отдать его Тез — отдельная работа.
 */

/*
 * Своё время кабинет отдаёт только вместе с активным звонком: когда звонков нет — а ночью и
 * в тихую минуту их нет, — снимок штампуется нашими часами. Подписывать это «данные Binotel
 * на …» значит называть чужой источник; расхождение секундное, но подпись должна быть правдой.
 */
const clockLabel = (snapshot) => {
    if (!snapshot?.binotel_now) return null;
    const own = snapshot.binotel_now_source === 'cabinet';
    return `${own ? 'данные Binotel на' : 'снимок на'} ${formatClock(snapshot.binotel_now)}`;
};

const FULLSCREEN_Z = 150;

const DIRECTIONS = TEZ_WALLBOARD_DIRECTION_LIST;

/*
 * Ключ хранения — свой. `otp:szov-wallboard-direction` трогать нельзя: по нему лежит выбор
 * направления чужого раздела, и чужое 'tez_op' в нём означало бы молча открытую «Линию».
 */
const directionStorageKey = (userId) => `otp:tez-wallboard-direction${userId ? `:${userId}` : ''}`;

const readStoredDirection = (userId) => {
    if (typeof window === 'undefined') return DIRECTIONS[0].key;
    try {
        const stored = window.localStorage.getItem(directionStorageKey(userId));
        return DIRECTIONS.some((item) => item.key === stored) ? stored : DIRECTIONS[0].key;
    } catch (error) {
        return DIRECTIONS[0].key;
    }
};

const writeStoredDirection = (userId, direction) => {
    if (typeof window === 'undefined') return;
    try {
        window.localStorage.setItem(directionStorageKey(userId), direction);
    } catch (error) {
        // Выбор направления — предпочтение браузера: не сохранилось, откроется ТП.
    }
};

/*
 * Шапка одна на оба направления: заголовок, переключатель, пометка о замерших данных, кнопки.
 * Различается только подпись под заголовком — она называет направление и момент данных.
 */
const TezWallboardHeader = ({
    subtitle, direction, onDirectionChange, staleNotice, loading, onRefresh, onFullscreen,
}) => (
    <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
            <h1 className="text-[26px] font-semibold text-slate-900">Табло Тез КЦ</h1>
            <p className="mt-1 text-[14px] text-slate-500">{subtitle}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
            <SegmentedSwitch value={direction} options={DIRECTIONS} onChange={onDirectionChange} />
            {staleNotice ? (
                <span className="rounded-full bg-amber-50 px-3 py-1 text-[12px] font-medium text-amber-700 ring-1 ring-amber-200">
                    {staleNotice}
                </span>
            ) : null}
            <button type="button" className={iosBtnGhost} onClick={() => onRefresh()} disabled={loading}>
                <FaIcon className="fas fa-rotate"></FaIcon>
                Обновить
            </button>
            <button type="button" className={iosBtnGhost} onClick={onFullscreen}>
                <FaIcon className="fas fa-expand"></FaIcon>
                На весь экран
            </button>
        </div>
    </div>
);

/** Пустые состояния (грузим / кабинет молчит) — одни на оба направления. */
const TezWallboardPlaceholder = ({ header, message, tone = 'muted' }) => (
    <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
        {header}
        <div className={`${iosCard} p-6 text-[13px] ${tone === 'error' ? 'text-rose-600' : 'text-slate-500'}`}>
            {message}
        </div>
    </div>
);

/** Направление ТП: очередь техподдержки в кабинете Binotel. */
const TpWallboard = ({ apiBaseUrl, withAccessTokenHeader, direction, onDirectionChange }) => {
    const config = TEZ_WALLBOARD_DIRECTIONS.tez_tp;
    const { snapshot, error, loading, refresh } = useTezTpWallboardSnapshot({ apiBaseUrl, withAccessTokenHeader });
    const [fullscreen, setFullscreen] = useState(false);

    const staleNotice = useMemo(() => wallboardStaleNotice(snapshot, error, config.source), [config.source, error, snapshot]);

    const header = (
        <TezWallboardHeader
            subtitle={`Техподдержка в реальном времени${
                clockLabel(snapshot) ? ` · ${clockLabel(snapshot)}` : ''}`}
            direction={direction}
            onDirectionChange={onDirectionChange}
            staleNotice={staleNotice}
            loading={loading}
            onRefresh={refresh}
            onFullscreen={() => setFullscreen(true)}
        />
    );

    if (!snapshot) {
        return (
            <TezWallboardPlaceholder
                header={header}
                message={loading ? 'Загружаем данные Binotel…' : (error || 'Данные недоступны')}
                tone={loading ? 'muted' : 'error'}
            />
        );
    }

    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            {header}
            <TezTpWallboardBody snapshot={snapshot} scale={1} />
            {fullscreen ? createPortal(
                <FullscreenSheet
                    open
                    wide
                    z={FULLSCREEN_Z}
                    icon={config.icon}
                    title={config.title}
                    subtitle={`${clockLabel(snapshot) ? `${clockLabel(snapshot)} · ` : ''}Esc чтобы выйти`}
                    onClose={() => setFullscreen(false)}
                >
                    <TezTpWallboardBody snapshot={snapshot} scale={1.5} />
                </FullscreenSheet>,
                document.body,
            ) : null}
        </div>
    );
};

/** Направление ОП: продавцы того же кабинета, но без очереди. */
const OpWallboard = ({ apiBaseUrl, withAccessTokenHeader, direction, onDirectionChange }) => {
    const config = TEZ_WALLBOARD_DIRECTIONS.tez_op;
    const { snapshot, error, loading, refresh } = useTezOpWallboardSnapshot({ apiBaseUrl, withAccessTokenHeader });
    const [fullscreen, setFullscreen] = useState(false);

    const staleNotice = useMemo(() => wallboardStaleNotice(snapshot, error, config.source), [config.source, error, snapshot]);

    const header = (
        <TezWallboardHeader
            subtitle={`Отдел продаж в реальном времени${
                clockLabel(snapshot) ? ` · ${clockLabel(snapshot)}` : ''}`}
            direction={direction}
            onDirectionChange={onDirectionChange}
            staleNotice={staleNotice}
            loading={loading}
            onRefresh={refresh}
            onFullscreen={() => setFullscreen(true)}
        />
    );

    if (!snapshot) {
        return (
            <TezWallboardPlaceholder
                header={header}
                message={loading ? 'Загружаем данные Binotel…' : (error || 'Данные недоступны')}
                tone={loading ? 'muted' : 'error'}
            />
        );
    }

    return (
        <div className="space-y-5" style={{ fontFamily: APPLE_FONT }}>
            {header}
            <TezOpWallboardBody snapshot={snapshot} scale={1} />
            {fullscreen ? createPortal(
                <FullscreenSheet
                    open
                    wide
                    z={FULLSCREEN_Z}
                    icon={config.icon}
                    title={config.title}
                    subtitle={`${clockLabel(snapshot) ? `${clockLabel(snapshot)} · ` : ''}Esc чтобы выйти`}
                    onClose={() => setFullscreen(false)}
                >
                    <TezOpWallboardBody snapshot={snapshot} scale={1.5} />
                </FullscreenSheet>,
                document.body,
            ) : null}
        </div>
    );
};

export default function TezWallboardView(props) {
    const { user, apiBaseUrl, withAccessTokenHeader } = props;
    const userId = user?.id;
    // Выбор направления запоминаем: тому, кто следит за продажами, незачем каждый раз
    // переключаться с ТП — раздел открывается на том направлении, где его закрыли.
    const [direction, setDirection] = useState(() => readStoredDirection(userId));

    const changeDirection = useCallback((next) => {
        setDirection(next);
        writeStoredDirection(userId, next);
    }, [userId]);

    /*
     * Рисуем ТОЛЬКО выбранное направление, каждое своим компонентом со своим снимком: иначе
     * закрытое табло продолжало бы ходить в кабинет впустую. Кабинет у нас один на два
     * направления и одна учётная запись на всю компанию — лишние обходы там дорогие.
     */
    if (direction === 'tez_op') {
        return (
            <OpWallboard
                apiBaseUrl={apiBaseUrl}
                withAccessTokenHeader={withAccessTokenHeader}
                direction={direction}
                onDirectionChange={changeDirection}
            />
        );
    }
    return (
        <TpWallboard
            apiBaseUrl={apiBaseUrl}
            withAccessTokenHeader={withAccessTokenHeader}
            direction={direction}
            onDirectionChange={changeDirection}
        />
    );
}

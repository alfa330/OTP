import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { UserPlus } from 'lucide-react';
import { APPLE_FONT, IosHint, IosSegmented, iosCard } from '../ui/ios';
import BoardTab from './BoardTab';
import CardSheet from './CardSheet';
import DeskTab from './DeskTab';
import ReportsTab from './ReportsTab';
import SettingsTab from './SettingsTab';
import UploadTab from './UploadTab';
import { errorOf } from './hiringMeta';
import { Loading } from './hiringUi';

/*
 * Раздел «Воронка найма» (#404): рабочий стол рекрутера и канбан-воронка найма.
 *
 * Вкладки — по правам (hiring/access.py):
 *   Рабочий стол   рекрутеры и руководитель рекрутинга: план найма, задачи на сегодня,
 *                  моя воронка, моя мотивация;
 *   Воронка        канбан-доска — всем, кому раздел открыт (тренер и руководитель отдела
 *                  видят только своих кандидатов);
 *   Отчёты         дашборд и отчёты: руководителю целиком, рекрутеру — по себе;
 *   Загрузки       база контактов файлом и партии — руководитель рекрутинга;
 *   Настройки      рекрутеры, источники, вакансии, причины, группы, план найма, интеграции.
 *
 * Карточка кандидата — одно окно на весь раздел (CardSheet): открывается с доски, с рабочего
 * стола, из отчёта и по ссылке ?view=hiring&card=<id> (колокол, Telegram).
 */

const TAB_STORAGE_KEY = 'hiring.tab';
const CARD_QUERY_PARAM = 'card';

const HOW_IT_WORKS =
    'Контакты и отклики из всех источников попадают в общий пул. «Взять в работу» закрепляет карточку за одним '
    + 'рекрутером — другие её не видят и не двигают. Карточка идёт по этапам до «Отработал 7 дней»: его подтверждает '
    + 'руководитель отдела, куда был найм, после чего найм закрыт и мотивация засчитана. Без касаний месяц — карточка '
    + 'сама возвращается в пул.';

const TAB_META = {
    desk: { label: 'Рабочий стол', short: 'Стол', subtitle: 'План найма, задачи на сегодня, моя воронка и мотивация' },
    board: { label: 'Воронка', short: 'Воронка', subtitle: 'Канбан-доска: общий пул и карточки по этапам', hint: HOW_IT_WORKS },
    reports: { label: 'Отчёты', short: 'Отчёты', subtitle: 'Дашборд воронки, потери, рекрутеры, источники, мотивация' },
    upload: { label: 'Загрузки', short: 'Загрузки', subtitle: 'База контактов файлом и партии загрузок' },
    settings: { label: 'Настройки', short: 'Настройки', subtitle: 'Рекрутеры, источники, вакансии, причины, группы, план и интеграции' },
};

const tabLabel = ({ label, short }) => (short === label ? label : (
    <>
        <span className="sm:hidden">{short}</span>
        <span className="hidden sm:inline">{label}</span>
    </>
));

const readStoredTab = () => {
    try {
        return window.localStorage.getItem(TAB_STORAGE_KEY) || '';
    } catch (storageError) {
        return '';
    }
};

const cardFromLocation = () => {
    try {
        const value = new URLSearchParams(window.location.search).get(CARD_QUERY_PARAM);
        return value && /^\d+$/.test(value) ? Number(value) : null;
    } catch (error) {
        return null;
    }
};

const writeCardParam = (cardId) => {
    try {
        const url = new URL(window.location.href);
        if (cardId) url.searchParams.set(CARD_QUERY_PARAM, String(cardId));
        else url.searchParams.delete(CARD_QUERY_PARAM);
        window.history.replaceState(window.history.state, '', url.toString());
    } catch (error) {
        // Адрес не обновился — окно всё равно открыто.
    }
};

const HiringView = ({ apiBaseUrl, withAccessTokenHeader, showToast, focusRequest = null }) => {
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);
    const headersRef = useRef(withAccessTokenHeader);
    useEffect(() => { headersRef.current = withAccessTokenHeader; }, [withAccessTokenHeader]);

    const api = useMemo(() => {
        const url = (path) => `${apiBaseUrl}/api/hiring${path}`;
        // Заголовки сливаются, а не подменяются: иначе свой Content-Type у загрузки файла стёр бы
        // авторизацию (Authorization / X-Auth-Transport), и запрос жил бы только на cookie.
        const config = (extra = {}) => {
            const { headers: own = {}, ...rest } = extra || {};
            return { headers: { ...(headersRef.current ? headersRef.current() : {}), ...own }, ...rest };
        };
        return {
            get: (path, params) => axios.get(url(path), config({ params })),
            post: (path, body, extra) => axios.post(url(path), body || {}, config(extra)),
            put: (path, body) => axios.put(url(path), body || {}, config()),
            patch: (path, body) => axios.patch(url(path), body || {}, config()),
            delete: (path) => axios.delete(url(path), config()),
            blob: (path, params) => axios.get(url(path), config({ params, responseType: 'blob' })),
        };
    }, [apiBaseUrl]);

    const [boot, setBoot] = useState(null);
    const [error, setError] = useState('');
    const [tab, setTab] = useState(readStoredTab);
    const [openCardId, setOpenCardId] = useState(cardFromLocation);
    // Перетаскивание на доске открывает карточку сразу с выбранным переходом.
    const [openMove, setOpenMove] = useState(null);
    // «Позвонить» со стола или с доски: звонок уже ушёл в iCore Phone, карточка открывается с окном результата.
    const [openCall, setOpenCall] = useState(false);
    // Растёт после любого изменения карточки — вкладки перечитывают свои списки.
    const [version, setVersion] = useState(0);

    const reloadBoot = useCallback(() => api.get('')
        .then(({ data }) => { setBoot(data); return data; })
        .catch((requestError) => { setError(errorOf(requestError, 'Раздел не открылся — попробуйте обновить страницу')); }), [api]);

    useEffect(() => {
        let alive = true;
        api.get('')
            .then(({ data }) => { if (alive) setBoot(data); })
            .catch((requestError) => { if (alive) setError(errorOf(requestError, 'Раздел не открылся — попробуйте обновить страницу')); });
        return () => { alive = false; };
    }, [api]);

    // Переход из колокола: карточка по номеру.
    useEffect(() => {
        if (focusRequest?.cardId) setOpenCardId(Number(focusRequest.cardId));
    }, [focusRequest]);

    useEffect(() => { writeCardParam(openCardId); }, [openCardId]);

    const caps = boot?.capabilities || {};
    const tabs = useMemo(() => [
        caps.recruiter && 'desk',
        'board',
        'reports',
        caps.upload && 'upload',
        caps.manage && 'settings',
    ].filter(Boolean), [caps.recruiter, caps.upload, caps.manage]);
    const active = tabs.includes(tab) ? tab : tabs[0];
    const chooseTab = (value) => {
        setTab(value);
        try {
            window.localStorage.setItem(TAB_STORAGE_KEY, value);
        } catch (storageError) {
            // Приватное окно: вкладка просто не запомнится.
        }
    };

    const onChanged = useCallback(() => setVersion((n) => n + 1), []);
    const openCard = useCallback((id, options) => {
        setOpenMove(options?.move || null);
        setOpenCall(Boolean(options?.call));
        setOpenCardId(Number(id) || null);
    }, []);
    const moveConsumed = useCallback(() => setOpenMove(null), []);
    const callConsumed = useCallback(() => setOpenCall(false), []);

    // Доска — на всю ширину экрана (владелец 30.10.2026: на большом мониторе видеть все этапы), остальные
    // вкладки — в обычной ширине раздела.
    const wide = (tabs.includes(tab) ? tab : tabs[0]) === 'board';
    const shell = (children) => (
        <div className={`relative mx-auto min-h-screen w-full ${wide ? 'max-w-none' : 'max-w-[1600px]'} bg-gray-50 px-3 py-4 sm:px-5 sm:py-6 md:min-h-0`}
             style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (error && !boot) return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>);
    if (!boot) return shell(<Loading text="Открываем раздел…" />);

    const meta = TAB_META[active] || TAB_META.board;
    const shared = { api, toast, boot, caps, onOpenCard: openCard, version, onChanged, reloadBoot };
    return shell(
        <>
            <header className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
                <div className="flex min-w-0 items-center gap-3">
                    {/* Плитка сплошным цветом, без градиента (владелец 27.10.2026). */}
                    <span className="grid h-12 w-12 shrink-0 place-items-center rounded-[14px] bg-blue-600 text-white shadow-sm">
                        <UserPlus size={24} strokeWidth={2} />
                    </span>
                    <div className="min-w-0 grow basis-0">
                        <h1 className="flex items-center gap-1.5 text-[22px] font-bold leading-tight tracking-tight text-slate-900">
                            Воронка найма
                            {meta.hint && <IosHint text={meta.hint} label="Как это работает" />}
                        </h1>
                        <p className="mt-0.5 truncate text-[13px] text-slate-500">
                            {meta.subtitle}{boot.me?.scope_label ? ` · ${boot.me.scope_label}` : ''}
                        </p>
                    </div>
                </div>
                {tabs.length > 1 && (
                    <div className="-mx-1 max-w-full overflow-x-auto px-1 [scrollbar-width:none]">
                        <IosSegmented value={active} onChange={chooseTab} ariaLabel="Вкладка раздела"
                                      options={tabs.map((value) => ({ value, label: tabLabel(TAB_META[value]) }))} />
                    </div>
                )}
            </header>
            {active === 'desk' && <DeskTab {...shared} />}
            {active === 'board' && <BoardTab {...shared} />}
            {active === 'reports' && <ReportsTab {...shared} />}
            {active === 'upload' && <UploadTab {...shared} />}
            {active === 'settings' && <SettingsTab {...shared} />}
            <CardSheet
                api={api}
                toast={toast}
                boot={boot}
                caps={caps}
                cardId={openCardId}
                onClose={() => { setOpenCardId(null); setOpenMove(null); setOpenCall(false); }}
                onChanged={onChanged}
                initialMove={openMove}
                onMoveConsumed={moveConsumed}
                initialCall={openCall}
                onCallConsumed={callConsumed}
            />
        </>,
    );
};

export default HiringView;

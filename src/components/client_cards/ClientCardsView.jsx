/* Раздел «Карточка клиента» (задача #335): запросы в другие отделы, журнал обращений,
 * отчёт по причинам и настройки карточки звонка.
 *
 * Саму карточку оператор заполняет в iCORE Phone (страница client_card.html) — здесь то,
 * что вокруг неё: оператор видит свои запросы и ответы по ним и свои обращения,
 * руководитель отдела — всю картину, отчёт и настройки. Кто что видит, решает сервер
 * (client_cards/access.py); экран рисует вкладки по присланным правам.
 *
 * Ссылки. ?view=client_cards&cc_request=N — запрос из сообщения в группе отдела;
 * ?view=client_cards&cc_tab=report — отчёт из алерта (их читает сам раздел, readLink).
 * Нажатие уведомления в колоколе приходит пропсом focusRequest из App.jsx.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';

import CustomSelect from '../ui/CustomSelect';
import { APPLE_FONT, IosSegmented, iosCard } from '../ui/ios';
import JournalTab from './JournalTab';
import ReportTab from './ReportTab';
import RequestsPanel from './RequestsPanel';
import SettingsTab from './SettingsTab';
import { createCardsApi } from './cardsApi';
import { errorText } from './cardRules';
import { Spinner } from './ui';

const TAB_STORAGE_KEY = 'client_cards:tab';
const TABS = ['requests', 'journal', 'report', 'settings'];

const readStoredTab = () => {
    try {
        return window.localStorage.getItem(TAB_STORAGE_KEY) || '';
    } catch {
        return '';
    }
};

/* Ссылка на раздел: ?cc_request=N — запрос (бот ставит её на слова «Запрос №N» в группе
   отдела), ?cc_tab=report — отчёт (из алерта). Читается один раз при открытии и сразу
   убирается из адреса: обновление страницы не должно заново открывать тот же запрос. */
const readLink = () => {
    try {
        const url = new URL(window.location.href);
        const request = Number(url.searchParams.get('cc_request') || 0);
        const tab = url.searchParams.get('cc_tab') || '';
        if (request || tab) {
            url.searchParams.delete('cc_request');
            url.searchParams.delete('cc_tab');
            window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
        }
        return { request: Number.isInteger(request) && request > 0 ? request : null, tab };
    } catch {
        return { request: null, tab: '' };
    }
};

export default function ClientCardsView({ apiBaseUrl, withAccessTokenHeader, showToast, focusRequest = null }) {
    // showToast из App.jsx — новая функция на каждый рендер: держим её в ref, иначе загрузка
    // перезапускалась бы после каждого тоста (как в «Аренде авто»).
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);
    const headersRef = useRef(withAccessTokenHeader);
    useEffect(() => { headersRef.current = withAccessTokenHeader; }, [withAccessTokenHeader]);
    const api = useMemo(
        () => createCardsApi(apiBaseUrl, () => (headersRef.current ? headersRef.current() : {})),
        [apiBaseUrl]);

    const [link] = useState(readLink);
    const [boot, setBoot] = useState(null);
    const [error, setError] = useState('');
    const [departmentId, setDepartmentId] = useState(null);
    const [tab, setTab] = useState(() => (link.request ? 'requests' : TABS.includes(link.tab) ? link.tab : readStoredTab()));
    const [focus, setFocus] = useState(link.request);

    const load = useCallback(async (department) => {
        try {
            const { data } = await api.screen(department);
            setBoot(data);
            setDepartmentId(data.department_id);
            setError('');
        } catch (requestError) {
            if (axios.isCancel(requestError)) return;
            setError(errorText(requestError, 'Раздел не открылся — попробуйте обновить страницу'));
        }
    }, [api]);

    useEffect(() => { load(null); }, [load]);

    // Нажали уведомление в колоколе: открыть вкладку «Запросы» и сам запрос. nonce — чтобы
    // повторное нажатие на то же уведомление открывало запрос снова.
    useEffect(() => {
        if (!focusRequest?.requestId) return;
        setTab('requests');
        setFocus(Number(focusRequest.requestId));
    }, [focusRequest?.requestId, focusRequest?.nonce]);

    const canManage = Boolean(boot?.can_manage);
    const tabs = useMemo(() => [
        { value: 'requests', label: 'Запросы', count: boot?.unread_requests || undefined },
        { value: 'journal', label: 'Обращения' },
        canManage && { value: 'report', label: 'Отчёт' },
        canManage && { value: 'settings', label: 'Настройки' },
    ].filter(Boolean), [canManage, boot?.unread_requests]);
    const active = tabs.some((item) => item.value === tab) ? tab : 'requests';
    const chooseTab = (value) => {
        setTab(value);
        try {
            window.localStorage.setItem(TAB_STORAGE_KEY, value);
        } catch {
            // Приватное окно: вкладка просто не запомнится.
        }
    };

    // Раздел занимает всю ширину экрана (владелец, 13.11.2026: «сжатым… лучше на всю ширину»):
    // потолка ширины нет, широкий экран разбирают сами вкладки — колонками.
    const shell = (children) => (
        <div className="relative min-h-screen w-full bg-gray-50 px-3 py-4 sm:px-5 sm:py-6 md:min-h-0"
             style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (error && !boot) return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>);
    if (!boot) return shell(<Spinner text="Открываем раздел…" />);

    const departments = boot.departments || [];
    const mergeBoot = (part) => setBoot((prev) => ({ ...prev, ...part }));

    return shell(
        <>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                    <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">Карточка клиента</h1>
                    {canManage && !boot.settings.enabled && (
                        <p className="mt-0.5 text-[12.5px] text-amber-700">
                            Карточка операторам ещё не включена — заведите причины и включите её во вкладке «Настройки».
                        </p>
                    )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {departments.length > 1 && (
                        <CustomSelect variant="ios" value={String(departmentId)} className="w-52" ariaLabel="Отдел"
                                      options={departments.map((item) => ({ value: String(item.id), label: item.name }))}
                                      onChange={(value) => { setBoot(null); load(Number(value)); }} />
                    )}
                    <IosSegmented value={active} options={tabs} onChange={chooseTab} slide ariaLabel="Вкладки раздела" />
                </div>
            </div>

            {active === 'requests' && (
                <RequestsPanel api={api} toast={toast} canManage={canManage} departmentId={departmentId}
                               focusRequestId={focus} onFocusHandled={() => setFocus(null)}
                               onUnread={(count) => setBoot((prev) => (prev && prev.unread_requests !== count
                                   ? { ...prev, unread_requests: count } : prev))} />
            )}
            {active === 'journal' && (
                <JournalTab api={api} departmentId={departmentId} categories={boot.categories} toast={toast}
                            onOpenRequest={(id) => { setFocus(id); chooseTab('requests'); }} />
            )}
            {active === 'report' && canManage && <ReportTab api={api} departmentId={departmentId} toast={toast} />}
            {active === 'settings' && canManage && (
                <SettingsTab api={api} boot={boot} toast={toast} onBoot={mergeBoot} onReload={() => load(departmentId)} />
            )}
        </>,
    );
}

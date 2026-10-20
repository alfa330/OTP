import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { BarChart3, ClipboardList, LayoutGrid, Plus, Settings2, ShieldAlert } from 'lucide-react';
import { APPLE_FONT, IosModal, IosSegmented } from '../ui/ios';
import useIsMobileShell from '../common/useIsMobileShell';
import { apiCode, apiError, createDisciplineApi } from './disciplineApi';
import { dBtnPrimary, dTabs, EmptyState, LoadingRows, T } from './disciplineUi';
import SummaryPanel from './SummaryPanel';
import JournalPanel from './JournalPanel';
import ControlsPanel from './ControlsPanel';
import ReportsPanel from './ReportsPanel';
import SettingsPanel from './SettingsPanel';
import MyRecords from './MyRecords';
import RecordForm from './RecordForm';
import { useRecordSheet } from './RecordSheet';
import { useOperatorCard } from './OperatorCard';
import FilePreview from './FilePreview';

/*
 * Раздел «Дисциплина» (ТЗ задачи #254): нарушения, объяснительные, акты и
 * особый контроль операторов СЗоВ, ОП и Теза.
 *
 * Экраны по ТЗ п. 4: «Сводка» (главный), «Журнал нарушений», «Особый
 * контроль», «Отчёты» и «Настройки и справочники» (последние два — руководителю).
 * Карточку оператора и карточку нарушения раздел открывает окнами поверх
 * любого экрана. Оператор видит только свои записи — одним экраном, без вкладок.
 *
 * Кнопка «Зафиксировать нарушение» закреплена и доступна с любого экрана
 * (п. 4.1.5): на компьютере — в шапке, которая не уезжает при прокрутке, на
 * телефоне — круглой кнопкой над нижним баром, как «Написать» в Почте iOS.
 *
 * Контракт с App.jsx тот же, что у остальных разделов: apiBaseUrl,
 * withAccessTokenHeader, showToast; focusRequest — переход из колокола
 * ({ recordId | controlId, nonce }).
 */

const TAB_KEY = 'otp.discipline.tab';

const readStoredTab = () => {
    try {
        return window.localStorage.getItem(TAB_KEY) || 'summary';
    } catch {
        return 'summary';
    }
};

const readLinkRecord = () => {
    try {
        const params = new URLSearchParams(window.location.search);
        const id = Number(params.get('record'));
        return Number.isFinite(id) && id > 0 ? id : null;
    } catch {
        return null;
    }
};

const clearLinkRecord = () => {
    try {
        const url = new URL(window.location.href);
        if (!url.searchParams.has('record')) return;
        url.searchParams.delete('record');
        window.history.replaceState(window.history.state, '', url.toString());
    } catch {
        /* адрес не трогаем — это только чистка */
    }
};

/* Переход между уровнями окна — как в «Оплате счетов»: вперёд — сдвиг справа,
   назад — слева. Первое открытие анимирует само окно. */
const LEVEL_FORWARD = 'motion-safe:animate-push-in';
const LEVEL_BACK = 'motion-safe:animate-pop-in';

/* Окно записи и сотрудника — ОДНО на всю стопку, как «Настройки» iOS: из записи
   открыли сотрудника — шеврон «назад» вернёт к записи. Два окна по очереди
   давали бы двойное затемнение на каждом шаге (правило ios.jsx).

   Жест «назад» на телефоне снимает ровно один уровень, как шеврон: onClose
   возвращает false («окно остаётся»), и запись окна в истории кладётся на
   место — следующий жест снимет следующий уровень, последний закроет окно.

   Прокрутка у каждого уровня своя: вперёд — новый уровень с начала, назад —
   прежний там, где его оставили. */
const StackWindow = ({ top, depth, motion, mobile, onBack, onClose, api, toast, version, onChanged,
    onOpenOperator, onOpenRecord, onOpenFile, onCreate }) => {
    const open = Boolean(top);
    const record = useRecordSheet({
        recordId: top?.type === 'record' ? top.id : null, open, api, toast, onChanged, onOpenOperator, onOpenFile,
        onOpenRecord,
    });
    const operator = useOperatorCard({
        operatorId: top?.type === 'operator' ? top.id : null, open, api, version, onOpenRecord, onOpenFile, onCreate,
    });
    const frame = top?.type === 'operator' ? operator : record;
    const level = top ? `${depth}:${top.type}:${top.id}` : 'none';

    const levelRef = useRef(null);
    const scrolls = useRef(new Map());
    const levelNow = useRef(level);
    levelNow.current = level;
    useEffect(() => {
        if (!open) { scrolls.current.clear(); return undefined; }
        const body = levelRef.current?.parentElement;
        if (!body) return undefined;
        const remember = () => scrolls.current.set(levelNow.current, body.scrollTop);
        body.addEventListener('scroll', remember, { passive: true });
        return () => body.removeEventListener('scroll', remember);
    }, [open]);
    useLayoutEffect(() => {
        const body = levelRef.current?.parentElement;
        if (body) body.scrollTop = motion === LEVEL_BACK ? (scrolls.current.get(level) || 0) : 0;
    }, [level]); // eslint-disable-line react-hooks/exhaustive-deps

    const stepBack = onBack ? () => { onBack(); return false; } : null;
    return (
        <IosModal open={open} onClose={mobile && stepBack ? stepBack : onClose} onBack={onBack}
                  title={frame.title} subtitle={frame.subtitle} footer={frame.footer} maxWidth="max-w-3xl"
                  sizeKey={`${level}:${frame.sizeKey}`}>
            <div ref={levelRef} key={level} className={motion}>{frame.body}</div>
        </IosModal>
    );
};

const DisciplineView = ({ apiBaseUrl, withAccessTokenHeader, showToast, focusRequest = null }) => {
    const headers = useCallback(() => (withAccessTokenHeader ? withAccessTokenHeader() : {}), [withAccessTokenHeader]);
    const api = useMemo(() => createDisciplineApi(apiBaseUrl, headers), [apiBaseUrl, headers]);
    const mobile = useIsMobileShell();
    const toast = useCallback((message, type = 'success') => showToast?.(message, type), [showToast]);

    const [ping, setPing] = useState(null);
    const [pingError, setPingError] = useState(null);
    const [tab, setTab] = useState(readStoredTab);
    const [departmentId, setDepartmentId] = useState(null);
    const [version, setVersion] = useState(0);
    const [formOpen, setFormOpen] = useState(false);
    const [formOperator, setFormOperator] = useState(null);
    // Окна записи и сотрудника — стопкой в одном окне (StackWindow).
    const [stack, setStack] = useState(() => {
        const id = readLinkRecord();
        return id ? [{ type: 'record', id }] : [];
    });
    const [levelMotion, setLevelMotion] = useState('');
    const [preview, setPreview] = useState(null);
    const top = stack[stack.length - 1] || null;
    const openRecord = useCallback((id) => { setLevelMotion(''); setStack([{ type: 'record', id }]); }, []);
    const pushRecord = useCallback((id) => {
        setLevelMotion(LEVEL_FORWARD);
        setStack((prev) => [...prev, { type: 'record', id }]);
    }, []);
    const pushOperator = useCallback((id) => {
        setLevelMotion(LEVEL_FORWARD);
        setStack((prev) => [...prev, { type: 'operator', id }]);
    }, []);
    const openOperator = useCallback((id) => { setLevelMotion(''); setStack([{ type: 'operator', id }]); }, []);
    const back = stack.length > 1 ? () => { setLevelMotion(LEVEL_BACK); setStack((prev) => prev.slice(0, -1)); } : null;
    const closeStack = () => setStack([]);

    const reload = useCallback(() => setVersion((v) => v + 1), []);

    useEffect(() => {
        let alive = true;
        api.ping()
            .then((data) => { if (alive) { setPing(data); setPingError(null); } })
            .catch((error) => { if (alive) setPingError({ message: apiError(error, 'Раздел не открылся'), code: apiCode(error) }); });
        return () => { alive = false; };
    }, [api]);

    useEffect(() => {
        if (!focusRequest?.nonce) return;
        if (focusRequest.recordId) openRecord(Number(focusRequest.recordId));
        if (focusRequest.controlId) setTab('controls');
    }, [focusRequest?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        if (top?.type === 'record') clearLinkRecord();
    }, [top?.type]);

    const caps = ping?.capabilities || {};
    const departments = ping?.departments || [];
    const tabs = useMemo(() => {
        // Вкладки — только когда раздел открылся: при отказе они обещали бы экраны,
        // которых нет.
        if (!ping || caps.is_operator) return [];
        // На телефоне — короткие подписи без значков: иначе пять вкладок не входят в ширину.
        const icon = (node) => (mobile ? null : node);
        return [
            { value: 'summary', label: 'Сводка', icon: icon(<LayoutGrid size={15} />) },
            { value: 'journal', label: 'Журнал', icon: icon(<ClipboardList size={15} />) },
            { value: 'controls', label: mobile ? 'Контроль' : 'Особый контроль', icon: icon(<ShieldAlert size={15} />) },
            caps.can_reports ? { value: 'reports', label: 'Отчёты', icon: icon(<BarChart3 size={15} />) } : null,
            caps.can_settings ? { value: 'settings', label: 'Настройки', icon: icon(<Settings2 size={15} />) } : null,
        ].filter(Boolean);
    }, [ping, mobile, caps.is_operator, caps.can_reports, caps.can_settings]);

    const activeTab = tabs.some((item) => item.value === tab) ? tab : (tabs[0]?.value || 'summary');
    const changeTab = (value) => {
        setTab(value);
        try { window.localStorage.setItem(TAB_KEY, value); } catch { /* вкладка просто не запомнится */ }
    };

    const openForm = (operator = null) => {
        setFormOperator(operator);
        setFormOpen(true);
    };

    const shared = {
        api, toast, departmentId, version, reload,
        openRecord, openOperator, openForm, openFile: setPreview,
        caps, departments,
    };

    let body;
    if (pingError) {
        body = (
            <div className="rounded-2xl bg-white p-2 ring-1 ring-slate-200/70">
                <EmptyState icon={ShieldAlert} title={pingError.message}
                            text={pingError.code === 'SENSITIVE_ACCESS_REQUIRED'
                                ? 'Покажите QR-код супервайзеру, чтобы открыть свои записи.' : null} />
            </div>
        );
    } else if (!ping) {
        body = <LoadingRows rows={6} />;
    } else if (caps.is_operator) {
        body = <MyRecords {...shared} />;
    } else if (activeTab === 'summary') {
        body = <SummaryPanel {...shared} onShowControls={() => changeTab('controls')} />;
    } else if (activeTab === 'journal') {
        body = <JournalPanel {...shared} />;
    } else if (activeTab === 'controls') {
        body = <ControlsPanel {...shared} />;
    } else if (activeTab === 'reports') {
        body = <ReportsPanel {...shared} />;
    } else {
        body = <SettingsPanel {...shared} userId={ping?.user_id} />;
    }

    const canCreate = Boolean(caps.can_create);

    /* Фон — у корня раздела: на телефоне оболочка красит страницу цветом
       корня (MobilePageChrome), и без него сквозь пустое место под карточками
       просвечивал логотип с фона портала. */
    return (
        <div className="mx-auto w-full max-w-[1180px] bg-gray-50 px-3 pb-24 pt-4 sm:px-5 sm:pt-6 xl:max-w-[1280px] 2xl:max-w-[1800px] 2xl:pt-8"
             style={{ fontFamily: APPLE_FONT }}>
            <header className="sticky top-0 z-20 -mx-3 mb-3 bg-slate-50/85 px-3 pb-3 pt-1 backdrop-blur-md sm:-mx-5 sm:px-5 xl:mb-4">
                <div className="flex flex-wrap items-end justify-between gap-3">
                    <div className="min-w-0">
                        <h1 className={`text-[19px] font-semibold leading-tight text-slate-900 ${T.title}`}>Дисциплина</h1>
                        <p className={`mt-0.5 text-[12.5px] text-slate-500 xl:mt-1 ${T.lead}`}>
                            {caps.is_operator ? 'Ваши записи о нарушениях и объяснительные'
                                : 'Нарушения, объяснительные, акты и особый контроль'}
                        </p>
                    </div>
                    {canCreate && !mobile && (
                        <button type="button" className={dBtnPrimary} onClick={() => openForm()}>
                            <Plus size={16} />
                            Зафиксировать нарушение
                        </button>
                    )}
                </div>
                {(tabs.length > 1 || departments.length > 1) && (
                    <div className="mt-3 flex flex-wrap items-center gap-2 xl:mt-4">
                        {tabs.length > 1 && (
                            <div className="max-w-full overflow-x-auto">
                                <IosSegmented value={activeTab} options={tabs} onChange={changeTab} size={mobile ? 'md' : 'lg'} slide
                                              ariaLabel="Экраны раздела" className={dTabs} />
                            </div>
                        )}
                        {departments.length > 1 && !['settings'].includes(activeTab) && (
                            <IosSegmented
                                value={departmentId || 0}
                                onChange={(value) => setDepartmentId(value || null)}
                                options={[{ value: 0, label: 'Все отделы' },
                                    ...departments.map((d) => ({ value: d.id, label: d.name }))]}
                                ariaLabel="Отдел"
                            />
                        )}
                    </div>
                )}
            </header>

            {body}

            {canCreate && mobile && (
                <button
                    type="button"
                    onClick={() => openForm()}
                    aria-label="Зафиксировать нарушение"
                    className="fixed z-[60] grid h-14 w-14 place-items-center rounded-full bg-blue-600 text-white shadow-[0_8px_24px_rgba(37,99,235,0.35)] transition active:scale-95"
                    style={{
                        right: 'calc(16px + env(safe-area-inset-right))',
                        bottom: 'calc(var(--mtb-thickness, 64px) + env(safe-area-inset-bottom) + 16px)',
                    }}
                >
                    <Plus size={24} />
                </button>
            )}

            <RecordForm
                open={formOpen}
                api={api}
                toast={toast}
                userId={ping?.user_id}
                initialOperator={formOperator}
                onClose={() => setFormOpen(false)}
                onCreated={(id) => { setFormOpen(false); reload(); openRecord(id); }}
            />
            <StackWindow
                top={top}
                depth={stack.length}
                motion={levelMotion}
                mobile={mobile}
                api={api}
                toast={toast}
                version={version}
                onClose={closeStack}
                onBack={back}
                onChanged={reload}
                onOpenOperator={pushOperator}
                onOpenRecord={pushRecord}
                onOpenFile={setPreview}
                onCreate={canCreate ? (operator) => { closeStack(); openForm(operator); } : null}
            />
            <FilePreview file={preview} api={api} toast={toast} onClose={() => setPreview(null)} />
        </div>
    );
};

export default DisciplineView;

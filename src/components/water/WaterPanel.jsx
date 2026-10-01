import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Loader2 } from 'lucide-react';
import { IosSegmented, iosCard } from '../ui/ios';
import WaterConditions from './WaterConditions';
import WaterIssue from './WaterIssue';
import WaterJournal from './WaterJournal';
import WaterSettings from './WaterSettings';
import WaterStock from './WaterStock';

/*
 * «Учёт воды» — выдача питьевой воды водителям и остатки по фронт-офисам
 * (ТЗ «Реализация учёта и выдачи питьевой воды в iCore», 29.09.2026).
 *
 * Свой раздел меню (WaterView.jsx; решение владельца 30.09.2026 — «в
 * отдельном разделе, а не в самих посылках»). Аудитория та же, что у
 * «Посылок», — фронт-офисы и колл-центр СЗоВ, — и тот же QR-замок. Панель
 * самодостаточна: всё берёт из /api/water.
 *
 * Вкладки (набор — по правам, см. tabs ниже):
 *   Выдача     — вставить ID водителя, увидеть право и выдать
 *   Проверка   — то же без кнопки «Выдать» (колл-центр СЗоВ)
 *   Остатки    — дашборд по офисам; поступления, пересчёт, пороги — руководителю
 *   Журнал     — история выдач с фильтрами и выгрузкой (руководителю)
 *   Настройки  — условия программы (руководителю); остальным — «Условия»,
 *                то же только для чтения
 */

const WaterPanel = ({ apiBaseUrl, headers, showToast }) => {
    const [capabilities, setCapabilities] = useState(null);
    const [settings, setSettings] = useState(null);
    const [schemaReady, setSchemaReady] = useState(true);
    // null — ещё не пришли, 'error' — не загрузились: «офисов в учёте нет»
    // показывается только по настоящему пустому ответу.
    const [offices, setOffices] = useState(null);
    const [directory, setDirectory] = useState([]);
    const [error, setError] = useState('');
    // null — человек ещё ничего не выбирал: открыта первая вкладка его роли.
    const [tab, setTab] = useState(null);
    const [journalKey, setJournalKey] = useState(0);

    useEffect(() => {
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/water/ping`, { headers: headers() })
            .then((response) => {
                if (cancelled) return;
                setCapabilities(response.data?.capabilities || null);
                setSettings(response.data?.settings || null);
                setSchemaReady(response.data?.schema_ready !== false);
            })
            .catch((requestError) => {
                if (!cancelled) setError(requestError?.response?.data?.error || 'Не удалось открыть учёт воды');
            });
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers]);

    const loadOffices = useCallback(() => {
        axios.get(`${apiBaseUrl}/api/water/offices`, { headers: headers() })
            .then((response) => {
                setOffices(response.data?.offices || []);
                setDirectory(response.data?.directory || []);
            })
            .catch(() => setOffices((prev) => (Array.isArray(prev) ? prev : 'error')));
    }, [apiBaseUrl, headers]);

    useEffect(() => { if (capabilities) loadOffices(); }, [capabilities, loadOffices]);

    /* Свежая строка офиса — на место, новый офис — в список, и прочь из
       справочника «добавить». Журнал при этом не трогаем: это нужно отмене,
       которая сама поправила свою строку в журнале. */
    const putOffice = useCallback((office) => {
        if (!office) return;
        setOffices((prev) => {
            const list = Array.isArray(prev) ? prev : [];
            return list.some((item) => item.id === office.id)
                ? list.map((item) => (item.id === office.id ? office : item))
                : [...list, office];
        });
        setDirectory((prev) => prev.filter((item) => item.id !== office.office_id));
    }, []);

    /* Офис поменялся из другой вкладки (выдача, поступление, пороги) — журнал
       перечитается при следующем открытии. */
    const officeUpdated = useCallback((office) => {
        if (!office) return;
        putOffice(office);
        setJournalKey((value) => value + 1);
    }, [putOffice]);

    /* Вкладки — по правам из /ping (решение владельца 01.10.2026):
         руководитель и супер-админ  Выдача · Остатки · Журнал · Настройки
         офисники из списка          Выдача · Остатки · Условия
         колл-центр СЗоВ             Остатки · Условия · Проверка
       «Условия» — та же программа, что «Настройки», только для чтения. У
       колл-центра первыми остатки и условия — их и просили; проверка водителя
       по ID третьей. */
    const tabs = useMemo(() => {
        if (!capabilities) return [];
        const manage = Boolean(capabilities.can_manage);
        if (capabilities.can_issue || manage) {
            return [
                { value: 'issue', label: 'Выдача' },
                { value: 'stock', label: 'Остатки' },
                capabilities.can_view_journal ? { value: 'journal', label: 'Журнал' } : null,
                { value: 'settings', label: manage ? 'Настройки' : 'Условия' },
            ].filter(Boolean);
        }
        return [
            { value: 'stock', label: 'Остатки' },
            { value: 'settings', label: 'Условия' },
            { value: 'issue', label: 'Проверка' },
        ];
    }, [capabilities]);
    const activeTab = tabs.some((item) => item.value === tab) ? tab : tabs[0]?.value;

    if (error) {
        return <div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>;
    }
    if (!capabilities) {
        return (
            <div className="flex items-center justify-center gap-2 py-12 text-[13px] text-slate-500">
                <Loader2 size={15} className="animate-spin" /> Открываем учёт воды…
            </div>
        );
    }
    if (!schemaReady) {
        return (
            <div className="rounded-2xl bg-amber-50 px-4 py-3 text-[13px] text-amber-800 ring-1 ring-amber-200">
                Учёт воды разворачивается — он появится после перезапуска сервера.
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <IosSegmented value={activeTab} onChange={setTab} options={tabs} ariaLabel="Учёт воды" />

            {activeTab === 'issue' && (
                <WaterIssue
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    capabilities={capabilities}
                    settings={settings}
                    offices={offices}
                    onOfficeUpdated={officeUpdated}
                    showToast={showToast}
                />
            )}
            {activeTab === 'stock' && (
                <WaterStock
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    capabilities={capabilities}
                    settings={settings}
                    offices={Array.isArray(offices) ? offices : []}
                    directory={directory}
                    onOfficeUpdated={officeUpdated}
                    showToast={showToast}
                />
            )}
            {activeTab === 'journal' && capabilities.can_view_journal && (
                <WaterJournal
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    reloadKey={journalKey}
                    canManage={Boolean(capabilities.can_manage)}
                    onStockChanged={putOffice}
                    showToast={showToast}
                />
            )}
            {activeTab === 'settings' && !capabilities.can_manage && (
                <WaterConditions settings={settings} />
            )}
            {activeTab === 'settings' && capabilities.can_manage && (
                <WaterSettings
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    onSaved={(saved) => { setSettings(saved); loadOffices(); }}
                    showToast={showToast}
                />
            )}
        </div>
    );
};

export default WaterPanel;

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Loader2 } from 'lucide-react';
import { IosSegmented, iosCard } from '../ui/ios';
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
 * Вкладки:
 *   Выдача     — вставить ID водителя, увидеть право и выдать
 *   Остатки    — дашборд по офисам, поступления, пересчёт, пороги
 *   Журнал     — история выдач с фильтрами и выгрузкой
 *   Настройки  — условия программы (только руководителю)
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
    const [tab, setTab] = useState('issue');
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

    const tabs = useMemo(() => [
        { value: 'issue', label: 'Выдача' },
        { value: 'stock', label: 'Остатки' },
        { value: 'journal', label: 'Журнал' },
        capabilities?.can_manage ? { value: 'settings', label: 'Настройки' } : null,
    ].filter(Boolean), [capabilities?.can_manage]);

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
            <IosSegmented value={tab} onChange={setTab} options={tabs} ariaLabel="Учёт воды" />

            {tab === 'issue' && (
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
            {tab === 'stock' && (
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
            {tab === 'journal' && (
                <WaterJournal
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    reloadKey={journalKey}
                    canManage={Boolean(capabilities.can_manage)}
                    onStockChanged={putOffice}
                    showToast={showToast}
                />
            )}
            {tab === 'settings' && capabilities.can_manage && (
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

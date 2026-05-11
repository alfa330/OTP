import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
  CalendarClock,
  CheckCircle2,
  Clock3,
  Gavel,
  ListChecks,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Users,
  Wifi
} from 'lucide-react';
import { isAdminLikeRole, normalizeRole } from '../../utils/roles';

const normalizeOperatorId = (value) => {
  const id = Number(value);
  return Number.isFinite(id) && id > 0 ? id : null;
};

const isDismissedOperator = (value) => {
  const status = String(value || '').trim().toLowerCase();
  return status === 'fired' || status === 'dismissal' || status === 'dismissed';
};

const normalizeOperators = (operators = [], selectedOperators = []) => {
  const rows = new Map();
  [...(Array.isArray(operators) ? operators : []), ...(Array.isArray(selectedOperators) ? selectedOperators : [])]
    .forEach((operator) => {
      const id = normalizeOperatorId(operator?.id ?? operator?.operator_id);
      if (!id) return;
      const role = normalizeRole(operator?.role || 'operator');
      if (role && role !== 'operator') return;
      rows.set(id, {
        id,
        name: operator?.name || `Оператор #${id}`,
        direction: operator?.direction || operator?.direction_name || '',
        direction_id: operator?.direction_id ?? null,
        supervisor_name: operator?.supervisor_name || '',
        rate: Number(operator?.rate || 1),
        status: operator?.status || ''
      });
    });

  return Array.from(rows.values())
    .filter((operator) => !isDismissedOperator(operator.status))
    .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'ru'));
};

const explainSteps = [
  {
    icon: CalendarClock,
    title: 'Админ утверждает смены',
    text: 'После генерации в расчете ресурсов админ выберет направление, период и время старта аукциона.'
  },
  {
    icon: Clock3,
    title: 'До старта будет таймер',
    text: 'При входе в раздел операторы увидят обратный отсчет до открытия выбора смен.'
  },
  {
    icon: Wifi,
    title: 'Выбор будет в реальном времени',
    text: 'Когда оператор заберет смену, она сразу исчезнет у остальных без обновления страницы.'
  },
  {
    icon: ListChecks,
    title: 'Можно отметить 2 выходных',
    text: 'Перед выбором смен оператор сможет указать любые два дня периода как выходные.'
  }
];

const ShiftAuctionView = ({ user, operators = [], apiBaseUrl, withAccessTokenHeader, showToast }) => {
  const role = normalizeRole(user?.role);
  const canManage = isAdminLikeRole(role);
  const apiRoot = String(apiBaseUrl || '').replace(/\/+$/, '');
  const showToastRef = useRef(showToast);

  const [settings, setSettings] = useState({
    enabled: false,
    launch_note: '',
    selected_operator_ids: [],
    selected_operators: [],
    is_current_user_tester: false
  });
  const [draftEnabled, setDraftEnabled] = useState(false);
  const [draftNote, setDraftNote] = useState('');
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    showToastRef.current = showToast;
  }, [showToast]);

  const notify = useCallback((message, type = 'success') => {
    if (typeof showToastRef.current === 'function') showToastRef.current(message, type);
  }, []);

  const buildHeaders = useCallback(() => {
    const headers = {};
    if (user?.id) headers['X-User-Id'] = String(user.id);
    return typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader(headers) : headers;
  }, [user?.id, withAccessTokenHeader]);

  const applySettings = useCallback((nextSettings) => {
    const safeSettings = nextSettings || {};
    const ids = (safeSettings.selected_operator_ids || [])
      .map(normalizeOperatorId)
      .filter(Boolean);

    setSettings({
      enabled: Boolean(safeSettings.enabled),
      launch_note: safeSettings.launch_note || '',
      selected_operator_ids: ids,
      selected_operators: Array.isArray(safeSettings.selected_operators) ? safeSettings.selected_operators : [],
      is_current_user_tester: Boolean(safeSettings.is_current_user_tester),
      updated_by_name: safeSettings.updated_by_name || '',
      updated_at: safeSettings.updated_at || null
    });
    setDraftEnabled(Boolean(safeSettings.enabled));
    setDraftNote(safeSettings.launch_note || '');
    setSelectedIds(new Set(ids));
  }, []);

  const fetchSettings = useCallback(async () => {
    if (!apiRoot || !user?.id) return;
    setIsLoading(true);
    try {
      const response = await axios.get(`${apiRoot}/api/shift_auction/test_access`, {
        headers: buildHeaders()
      });
      applySettings(response?.data?.test_access || {});
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось загрузить настройки аукциона смен', 'error');
    } finally {
      setIsLoading(false);
    }
  }, [apiRoot, applySettings, buildHeaders, notify, user?.id]);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  const operatorOptions = useMemo(
    () => normalizeOperators(operators, settings.selected_operators),
    [operators, settings.selected_operators]
  );

  const filteredOperators = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return operatorOptions;
    return operatorOptions.filter((operator) => {
      const haystack = [
        operator.name,
        operator.direction,
        operator.supervisor_name,
        operator.rate
      ].join(' ').toLowerCase();
      return haystack.includes(normalizedQuery);
    });
  }, [operatorOptions, query]);

  const selectedOperators = useMemo(
    () => operatorOptions.filter((operator) => selectedIds.has(operator.id)),
    [operatorOptions, selectedIds]
  );

  const toggleOperator = useCallback((operatorId) => {
    const id = normalizeOperatorId(operatorId);
    if (!id) return;
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleSave = useCallback(async () => {
    if (!canManage || !apiRoot) return;
    setIsSaving(true);
    try {
      const response = await axios.put(
        `${apiRoot}/api/shift_auction/test_access`,
        {
          enabled: draftEnabled,
          launch_note: draftNote,
          operator_ids: Array.from(selectedIds)
        },
        { headers: buildHeaders() }
      );
      applySettings(response?.data?.test_access || {});
      notify('Настройки тестового аукциона сохранены');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить настройки аукциона смен', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [apiRoot, applySettings, buildHeaders, canManage, draftEnabled, draftNote, notify, selectedIds]);

  const isTester = Boolean(settings.enabled && settings.is_current_user_tester);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="border-b border-slate-200 bg-white px-4 py-5 md:px-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-start gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700">
              <Gavel size={22} />
            </div>
            <div>
              <h1 className="text-2xl font-semibold text-slate-950">Аукцион смен</h1>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600">
                Раздел для будущего выбора утвержденных смен по направлению. Сейчас доступен тестовый запуск для выбранных операторов.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={fetchSettings}
            disabled={isLoading}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-100 disabled:cursor-wait disabled:opacity-60"
          >
            <RefreshCw size={16} className={isLoading ? 'animate-spin' : ''} />
            Обновить
          </button>
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 md:px-6">
        <section className={`rounded-lg border ${isTester ? 'border-emerald-200 bg-emerald-50' : 'border-amber-200 bg-amber-50'} p-5`}>
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="flex items-start gap-3">
              <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${isTester ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
                {isTester ? <ShieldCheck size={21} /> : <Clock3 size={21} />}
              </div>
              <div>
                <p className={`text-xs font-semibold uppercase tracking-wide ${isTester ? 'text-emerald-700' : 'text-amber-700'}`}>
                  {isTester ? 'Тестовый доступ включен' : 'Скоро'}
                </p>
                <h2 className="mt-1 text-xl font-semibold text-slate-950">
                  {isTester ? 'Вы в группе тестового запуска' : 'Аукцион смен готовится к запуску'}
                </h2>
                <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-700">
                  {isTester
                    ? 'После подключения боевой логики здесь появится обратный отсчет, таблица доступных смен и выбор двух выходных. Сейчас админ может проверить доступ выбранной тестовой группы.'
                    : 'Когда админ утвердит сгенерированные смены и назначит время старта, здесь появится таймер. После открытия аукциона вы будете выбирать доступные смены, а занятые смены будут исчезать у всех участников в реальном времени.'}
                </p>
                {settings.launch_note ? (
                  <p className="mt-3 rounded-md border border-white/70 bg-white/70 px-3 py-2 text-sm text-slate-700">
                    {settings.launch_note}
                  </p>
                ) : null}
              </div>
            </div>
            <div className="rounded-md border border-white/70 bg-white/75 px-3 py-2 text-sm text-slate-700">
              <span className="font-semibold">{settings.selected_operator_ids.length}</span> тестовых операторов
            </div>
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-4">
          {explainSteps.map((step) => {
            const Icon = step.icon;
            return (
              <div key={step.title} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <Icon size={20} className="text-blue-700" />
                <h3 className="mt-3 text-sm font-semibold text-slate-950">{step.title}</h3>
                <p className="mt-2 text-sm leading-5 text-slate-600">{step.text}</p>
              </div>
            );
          })}
        </section>

        {canManage && (
          <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 px-5 py-4">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-slate-950">Тестовый запуск</h2>
                  <p className="mt-1 text-sm text-slate-600">
                    Выберите операторов, которым будет открыт тестовый режим раздела.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={isSaving}
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-blue-700 px-4 text-sm font-semibold text-white transition hover:bg-blue-800 disabled:cursor-wait disabled:bg-blue-400"
                >
                  <Save size={16} />
                  {isSaving ? 'Сохранение...' : 'Сохранить'}
                </button>
              </div>
            </div>

            <div className="grid gap-5 p-5 xl:grid-cols-[minmax(0,1fr)_360px]">
              <div className="space-y-4">
                <label className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
                  <span>
                    <span className="block text-sm font-semibold text-slate-900">Включить тестовый режим</span>
                    <span className="block text-sm text-slate-500">Выбранные операторы увидят, что они включены в тестовую группу.</span>
                  </span>
                  <input
                    type="checkbox"
                    checked={draftEnabled}
                    onChange={(event) => setDraftEnabled(event.target.checked)}
                    className="h-5 w-5 rounded border-slate-300 text-blue-700 focus:ring-blue-600"
                  />
                </label>

                <div>
                  <label className="mb-2 block text-sm font-semibold text-slate-800">Текст для тестовой группы</label>
                  <textarea
                    value={draftNote}
                    onChange={(event) => setDraftNote(event.target.value)}
                    rows={3}
                    maxLength={1000}
                    placeholder="Например: Тестовый запуск начнется после проверки генерации смен."
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                  />
                </div>

                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="relative w-full sm:max-w-md">
                    <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="Поиск по оператору, направлению или СВ"
                      className="h-10 w-full rounded-lg border border-slate-200 pl-9 pr-3 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    />
                  </div>
                  <div className="text-sm text-slate-500">
                    Выбрано: <span className="font-semibold text-slate-900">{selectedIds.size}</span>
                  </div>
                </div>

                <div className="max-h-[460px] overflow-auto rounded-lg border border-slate-200">
                  {filteredOperators.length ? (
                    filteredOperators.map((operator) => {
                      const active = selectedIds.has(operator.id);
                      return (
                        <button
                          key={operator.id}
                          type="button"
                          onClick={() => toggleOperator(operator.id)}
                          className={`flex w-full items-center gap-3 border-b border-slate-100 px-4 py-3 text-left transition last:border-b-0 ${active ? 'bg-blue-50' : 'bg-white hover:bg-slate-50'}`}
                        >
                          <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${active ? 'border-blue-700 bg-blue-700 text-white' : 'border-slate-300 bg-white'}`}>
                            {active ? <CheckCircle2 size={14} /> : null}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-semibold text-slate-900">{operator.name}</span>
                            <span className="mt-0.5 block truncate text-xs text-slate-500">
                              {operator.direction || 'Без направления'} · ставка {Number(operator.rate || 1).toFixed(2)}
                              {operator.supervisor_name ? ` · ${operator.supervisor_name}` : ''}
                            </span>
                          </span>
                        </button>
                      );
                    })
                  ) : (
                    <div className="px-4 py-8 text-center text-sm text-slate-500">Операторы не найдены.</div>
                  )}
                </div>
              </div>

              <aside className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <Users size={17} className="text-blue-700" />
                  Тестовая группа
                </div>
                <div className="mt-3 space-y-2">
                  {selectedOperators.length ? (
                    selectedOperators.map((operator) => (
                      <div key={operator.id} className="rounded-md border border-slate-200 bg-white px-3 py-2">
                        <div className="truncate text-sm font-semibold text-slate-900">{operator.name}</div>
                        <div className="mt-0.5 truncate text-xs text-slate-500">{operator.direction || 'Без направления'}</div>
                      </div>
                    ))
                  ) : (
                    <p className="rounded-md border border-dashed border-slate-300 bg-white px-3 py-4 text-sm text-slate-500">
                      Пока никто не выбран.
                    </p>
                  )}
                </div>
              </aside>
            </div>
          </section>
        )}
      </div>
    </div>
  );
};

export default ShiftAuctionView;

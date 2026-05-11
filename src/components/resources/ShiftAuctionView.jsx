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
  Sparkles,
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

const toDateTimeInputValue = (value) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  const pad = (num) => String(num).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

const formatDateLabel = (value) => {
  if (!value) return 'Дата';
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('ru-RU', { weekday: 'short', day: '2-digit', month: 'short' });
};

const formatCountdown = (targetValue, nowMs) => {
  if (!targetValue) return '';
  const target = new Date(targetValue).getTime();
  if (!Number.isFinite(target)) return '';
  const diff = Math.max(0, target - nowMs);
  const totalSeconds = Math.floor(diff / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (num) => String(num).padStart(2, '0');
  return days > 0
    ? `${days} д ${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
    : `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
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
    title: 'Выбор идет в реальном времени',
    text: 'Когда оператор заберет смену, она сразу исчезнет у остальных без обновления страницы.'
  },
  {
    icon: ListChecks,
    title: 'Можно отметить 2 выходных',
    text: 'Перед выбором смен оператор сможет указать любые два дня периода как выходные.'
  }
];

const ShiftAuctionView = ({ user, operators = [], apiBaseUrl, withAccessTokenHeader, showToast, onOpenResourceGeneration }) => {
  const role = normalizeRole(user?.role);
  const canManage = isAdminLikeRole(role);
  const apiRoot = String(apiBaseUrl || '').replace(/\/+$/, '');
  const showToastRef = useRef(showToast);
  const streamAbortRef = useRef(null);
  const snapshotRequestRef = useRef(false);
  const lastEventIdRef = useRef(0);
  const daySectionRefs = useRef(new Map());

  const [settings, setSettings] = useState({
    enabled: false,
    launch_note: '',
    starts_at: null,
    ends_at: null,
    status: 'disabled',
    selected_operator_ids: [],
    selected_operators: [],
    is_current_user_tester: false
  });
  const [lots, setLots] = useState([]);
  const [myDayOffs, setMyDayOffs] = useState([]);
  const [lastEventId, setLastEventId] = useState(0);
  const [draftEnabled, setDraftEnabled] = useState(false);
  const [draftNote, setDraftNote] = useState('');
  const [draftStartsAt, setDraftStartsAt] = useState('');
  const [draftEndsAt, setDraftEndsAt] = useState('');
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isSeeding, setIsSeeding] = useState(false);
  const [claimingLotId, setClaimingLotId] = useState(null);
  const [dayOffLoadingDate, setDayOffLoadingDate] = useState('');
  const [connectionState, setConnectionState] = useState('idle');
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    showToastRef.current = showToast;
  }, [showToast]);

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const notify = useCallback((message, type = 'success') => {
    if (typeof showToastRef.current === 'function') showToastRef.current(message, type);
  }, []);

  const buildHeaders = useCallback((extra = {}) => {
    const headers = { ...extra };
    if (user?.id) headers['X-User-Id'] = String(user.id);
    return typeof withAccessTokenHeader === 'function' ? withAccessTokenHeader(headers) : headers;
  }, [user?.id, withAccessTokenHeader]);

  const applySnapshot = useCallback((snapshot) => {
    const safe = snapshot || {};
    const ids = (safe.selected_operator_ids || []).map(normalizeOperatorId).filter(Boolean);

    setSettings({
      enabled: Boolean(safe.enabled),
      launch_note: safe.launch_note || '',
      starts_at: safe.starts_at || null,
      ends_at: safe.ends_at || null,
      status: safe.status || 'disabled',
      selected_operator_ids: ids,
      selected_operators: Array.isArray(safe.selected_operators) ? safe.selected_operators : [],
      is_current_user_tester: Boolean(safe.is_current_user_tester),
      updated_by_name: safe.updated_by_name || '',
      updated_at: safe.updated_at || null
    });
    setLots(Array.isArray(safe.lots) ? safe.lots : []);
    setMyDayOffs(Array.isArray(safe.my_day_offs) ? safe.my_day_offs.filter(Boolean) : []);
    const nextEventId = Number(safe.last_event_id || 0);
    lastEventIdRef.current = nextEventId;
    setLastEventId(nextEventId);
    setDraftEnabled(Boolean(safe.enabled));
    setDraftNote(safe.launch_note || '');
    setDraftStartsAt(toDateTimeInputValue(safe.starts_at));
    setDraftEndsAt(toDateTimeInputValue(safe.ends_at));
    setSelectedIds(new Set(ids));
  }, []);

  const fetchSnapshot = useCallback(async ({ silent = false } = {}) => {
    if (!apiRoot || !user?.id) return;
    if (snapshotRequestRef.current) return;
    snapshotRequestRef.current = true;
    if (!silent) setIsLoading(true);
    try {
      const response = await axios.get(`${apiRoot}/api/shift_auction/test_snapshot`, {
        headers: buildHeaders()
      });
      applySnapshot(response?.data?.snapshot || {});
    } catch (error) {
      if (!silent) notify(error?.response?.data?.error || 'Не удалось загрузить аукцион смен', 'error');
    } finally {
      snapshotRequestRef.current = false;
      if (!silent) setIsLoading(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, notify, user?.id]);

  const handleRealtimeEvent = useCallback((event) => {
    const eventType = String(event?.event_type || '');
    const payload = event?.payload || {};
    if (eventType === 'lot_claimed' && payload.lot?.id) {
      setLots((currentLots) => currentLots.map((lot) => (
        Number(lot.id) === Number(payload.lot.id)
          ? { ...lot, ...payload.lot }
          : lot
      )));
      window.setTimeout(() => fetchSnapshot({ silent: true }), 150);
      return;
    }

    if ((eventType === 'day_off_selected' || eventType === 'day_off_removed') && Number(payload.operator_id) === Number(user?.id)) {
      setMyDayOffs(Array.isArray(payload.my_day_offs) ? payload.my_day_offs.filter(Boolean) : []);
      window.setTimeout(() => fetchSnapshot({ silent: true }), 150);
      return;
    }

    fetchSnapshot({ silent: true });
  }, [fetchSnapshot, user?.id]);

  useEffect(() => {
    fetchSnapshot();
  }, [fetchSnapshot]);

  const canOpenStream = Boolean(apiRoot && user?.id && (canManage || settings.is_current_user_tester));

  useEffect(() => {
    if (!canOpenStream) return undefined;

    let cancelled = false;
    const abortController = new AbortController();
    streamAbortRef.current?.abort?.();
    streamAbortRef.current = abortController;

    const readStream = async () => {
      setConnectionState('connecting');
      try {
        const response = await fetch(`${apiRoot}/api/shift_auction/test_events?after=${encodeURIComponent(lastEventIdRef.current || 0)}`, {
          headers: buildHeaders({ Accept: 'text/event-stream' }),
          signal: abortController.signal,
          credentials: 'include'
        });
        if (!response.ok || !response.body) throw new Error('SSE connection failed');
        setConnectionState('online');
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (!cancelled) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const chunks = buffer.split('\n\n');
          buffer = chunks.pop() || '';
          for (const chunk of chunks) {
            const dataLine = chunk.split('\n').find((line) => line.startsWith('data: '));
            if (!dataLine) continue;
            try {
              const event = JSON.parse(dataLine.slice(6));
              const eventId = Number(event?.id || 0);
              lastEventIdRef.current = Math.max(lastEventIdRef.current, eventId);
              setLastEventId((current) => Math.max(current, eventId));
              handleRealtimeEvent(event);
            } catch (parseError) {
              console.warn('Failed to parse shift auction event', parseError);
            }
          }
        }
      } catch (error) {
        if (!cancelled && error?.name !== 'AbortError') {
          setConnectionState('reconnecting');
          window.setTimeout(() => {
            if (!cancelled) fetchSnapshot({ silent: true });
          }, 1200);
        }
      }
    };

    readStream();
    return () => {
      cancelled = true;
      abortController.abort();
    };
  }, [apiRoot, buildHeaders, canOpenStream, fetchSnapshot, handleRealtimeEvent, user?.id]);

  const operatorOptions = useMemo(
    () => normalizeOperators(operators, settings.selected_operators),
    [operators, settings.selected_operators]
  );

  const filteredOperators = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return operatorOptions;
    return operatorOptions.filter((operator) => {
      const haystack = [operator.name, operator.direction, operator.supervisor_name, operator.rate].join(' ').toLowerCase();
      return haystack.includes(normalizedQuery);
    });
  }, [operatorOptions, query]);

  const selectedOperators = useMemo(
    () => operatorOptions.filter((operator) => selectedIds.has(operator.id)),
    [operatorOptions, selectedIds]
  );

  const lotDates = useMemo(
    () => Array.from(new Set((lots || []).map((lot) => lot.shift_date).filter(Boolean))).sort(),
    [lots]
  );

  const visibleLots = useMemo(() => {
    if (canManage) return lots;
    return lots.filter((lot) => lot.status === 'available' && !myDayOffs.includes(lot.shift_date));
  }, [canManage, lots, myDayOffs]);

  const lotsByDate = useMemo(() => {
    const grouped = new Map();
    lotDates.forEach((date) => grouped.set(date, []));
    visibleLots.forEach((lot) => {
      const key = lot.shift_date || 'unknown';
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(lot);
    });
    return Array.from(grouped.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [lotDates, visibleLots]);

  const myClaimedLots = useMemo(
    () => lots.filter((lot) => Number(lot.claimed_by) === Number(user?.id)),
    [lots, user?.id]
  );

  const dayNavigationItems = useMemo(() => {
    return lotDates.map((date) => {
      const dayLots = lots.filter((lot) => lot.shift_date === date);
      const claimedLots = dayLots.filter((lot) => lot.status === 'claimed');
      const myClaimed = dayLots.filter((lot) => Number(lot.claimed_by) === Number(user?.id));
      const isDayOff = myDayOffs.includes(date);
      const availableCount = visibleLots.filter((lot) => lot.shift_date === date && lot.status === 'available').length;
      let state = 'empty';
      if (isDayOff) state = 'off';
      else if (myClaimed.length > 0) state = 'shift';
      else if (availableCount > 0) state = 'available';
      return {
        date,
        total: dayLots.length,
        claimed: claimedLots.length,
        myClaimed: myClaimed.length,
        available: availableCount,
        isDayOff,
        state
      };
    });
  }, [lotDates, lots, myDayOffs, user?.id, visibleLots]);

  const countdown = settings.status === 'scheduled'
    ? formatCountdown(settings.starts_at, nowMs)
    : '';

  const isTester = Boolean(settings.enabled && settings.is_current_user_tester);
  const canUseAuction = isTester || canManage;
  const canChoose = isTester && (settings.status === 'scheduled' || settings.status === 'open');
  const canClaim = isTester && settings.status === 'open';

  const setDaySectionRef = useCallback((date, node) => {
    if (!date) return;
    if (node) daySectionRefs.current.set(date, node);
    else daySectionRefs.current.delete(date);
  }, []);

  const scrollToDay = useCallback((date) => {
    const node = daySectionRefs.current.get(date);
    if (!node) return;
    node.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

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
          starts_at: draftStartsAt || null,
          ends_at: draftEndsAt || null,
          operator_ids: Array.from(selectedIds)
        },
        { headers: buildHeaders() }
      );
      applySnapshot(response?.data?.test_access || {});
      await fetchSnapshot({ silent: true });
      notify('Настройки тестового аукциона сохранены');
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось сохранить настройки аукциона смен', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, canManage, draftEnabled, draftEndsAt, draftNote, draftStartsAt, fetchSnapshot, notify, selectedIds]);

  const handleSeedLots = useCallback(async () => {
    if (!canManage || !apiRoot) return;
    setIsSeeding(true);
    try {
      const response = await axios.post(`${apiRoot}/api/shift_auction/test_lots/seed`, {}, { headers: buildHeaders() });
      applySnapshot(response?.data?.snapshot || {});
      notify(`Тестовые смены созданы: ${Number(response?.data?.count || 0)}`);
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось создать тестовые смены', 'error');
    } finally {
      setIsSeeding(false);
    }
  }, [apiRoot, applySnapshot, buildHeaders, canManage, notify]);

  const handleClaimLot = useCallback(async (lotId) => {
    if (!canClaim || !apiRoot) return;
    setClaimingLotId(lotId);
    try {
      await axios.post(`${apiRoot}/api/shift_auction/test_lots/${lotId}/claim`, {}, { headers: buildHeaders() });
      await fetchSnapshot({ silent: true });
      notify('Смена закреплена за вами');
    } catch (error) {
      await fetchSnapshot({ silent: true });
      notify(error?.response?.data?.error || 'Не удалось забрать смену', 'error');
    } finally {
      setClaimingLotId(null);
    }
  }, [apiRoot, buildHeaders, canClaim, fetchSnapshot, notify]);

  const toggleDayOff = useCallback(async (date) => {
    if (!canChoose || !apiRoot || !date) return;
    const selected = myDayOffs.includes(date);
    setDayOffLoadingDate(date);
    try {
      const requestConfig = { headers: buildHeaders(), data: { date } };
      if (selected) {
        await axios.delete(`${apiRoot}/api/shift_auction/test_day_off`, requestConfig);
      } else {
        await axios.post(`${apiRoot}/api/shift_auction/test_day_off`, { date }, { headers: buildHeaders() });
      }
      await fetchSnapshot({ silent: true });
    } catch (error) {
      notify(error?.response?.data?.error || 'Не удалось изменить выходной', 'error');
    } finally {
      setDayOffLoadingDate('');
    }
  }, [apiRoot, buildHeaders, canChoose, fetchSnapshot, myDayOffs, notify]);

  const renderStatusCard = () => (
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
                ? 'В тестовом запуске можно проверить обратный отсчет, выбор двух выходных, захват смены и исчезновение занятой смены у остальных участников в реальном времени.'
                : 'Когда админ утвердит сгенерированные смены и назначит время старта, здесь появится таймер. После открытия аукциона вы будете выбирать доступные смены, а занятые смены будут исчезать у всех участников в реальном времени.'}
            </p>
            {settings.launch_note ? (
              <p className="mt-3 rounded-md border border-white/70 bg-white/70 px-3 py-2 text-sm text-slate-700">
                {settings.launch_note}
              </p>
            ) : null}
          </div>
        </div>
        <div className="flex flex-col gap-2 rounded-md border border-white/70 bg-white/75 px-3 py-2 text-sm text-slate-700">
          <span><span className="font-semibold">{settings.selected_operator_ids.length}</span> тестовых операторов</span>
          <span className="capitalize">Статус: <span className="font-semibold">{settings.status}</span></span>
          {countdown ? <span>Старт через: <span className="font-semibold tabular-nums">{countdown}</span></span> : null}
        </div>
      </div>
    </section>
  );

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
                Тестовый realtime-раздел для проверки будущего выбора утвержденных смен по направлению.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {canManage && typeof onOpenResourceGeneration === 'function' ? (
              <button
                type="button"
                onClick={onOpenResourceGeneration}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800"
              >
                <CalendarClock size={16} />
                Генерация графиков
              </button>
            ) : null}
            <div className={`inline-flex h-10 items-center gap-2 rounded-lg border px-3 text-sm ${connectionState === 'online' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 bg-white text-slate-600'}`}>
              <Wifi size={15} />
              {connectionState === 'online' ? 'Realtime online' : connectionState === 'connecting' ? 'Подключение...' : connectionState === 'reconnecting' ? 'Переподключение...' : 'Realtime idle'}
            </div>
            <button
              type="button"
              onClick={() => fetchSnapshot()}
              disabled={isLoading}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-100 disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshCw size={16} className={isLoading ? 'animate-spin' : ''} />
              Обновить
            </button>
          </div>
        </div>
      </div>

      <div className={`mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 md:px-6 ${canUseAuction && dayNavigationItems.length ? 'pb-28' : ''}`}>
        {renderStatusCard()}

        {!canUseAuction && (
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
        )}

        {canUseAuction && (
          <section className="grid gap-6 xl:grid-cols-[280px_minmax(0,1fr)]">
            <aside className="space-y-4">
              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <ListChecks size={17} className="text-blue-700" />
                  Мои выходные
                </div>
                <p className="mt-2 text-sm text-slate-500">Можно выбрать любые 2 дня периода.</p>
                <div className="mt-3 space-y-2">
                  {lotDates.length ? lotDates.map((date) => {
                    const active = myDayOffs.includes(date);
                    return (
                      <button
                        key={date}
                        type="button"
                        onClick={() => toggleDayOff(date)}
                        disabled={!canChoose || dayOffLoadingDate === date}
                        className={`flex w-full items-center justify-between rounded-md border px-3 py-2 text-sm transition disabled:cursor-not-allowed disabled:opacity-60 ${active ? 'border-blue-300 bg-blue-50 text-blue-800' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'}`}
                      >
                        <span>{formatDateLabel(date)}</span>
                        {active ? <CheckCircle2 size={16} /> : null}
                      </button>
                    );
                  }) : (
                    <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-3 py-4 text-sm text-slate-500">
                      Тестовые смены еще не созданы.
                    </div>
                  )}
                </div>
              </div>

              <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <Sparkles size={17} className="text-blue-700" />
                  Мои смены
                </div>
                <div className="mt-3 space-y-2">
                  {myClaimedLots.length ? myClaimedLots.map((lot) => (
                    <div key={lot.id} className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm">
                      <div className="font-semibold text-emerald-900">{formatDateLabel(lot.shift_date)}</div>
                      <div className="text-emerald-700">{lot.start_time} - {lot.end_time}</div>
                    </div>
                  )) : (
                    <p className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-3 py-4 text-sm text-slate-500">
                      Вы еще не забрали смены.
                    </p>
                  )}
                </div>
              </div>
            </aside>

            <main className="rounded-lg border border-slate-200 bg-white shadow-sm">
              <div className="border-b border-slate-200 px-5 py-4">
                <h2 className="text-lg font-semibold text-slate-950">Доступные смены</h2>
                <p className="mt-1 text-sm text-slate-600">
                  {settings.status === 'scheduled'
                    ? `Аукцион откроется через ${countdown || 'несколько секунд'}.`
                    : settings.status === 'open'
                      ? 'Нажмите “Забрать”, чтобы закрепить смену. У остальных участников она исчезнет сразу.'
                      : 'Сейчас аукцион закрыт.'}
                </p>
              </div>
              <div className="p-5">
                {lotsByDate.length ? (
                  <div className="space-y-5">
                    {lotsByDate.map(([date, dateLots]) => {
                      const dayMeta = dayNavigationItems.find((item) => item.date === date);
                      return (
                      <div key={date} ref={(node) => setDaySectionRef(date, node)} className="scroll-mt-24 rounded-lg border border-slate-100 bg-slate-50/70 p-3">
                        <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                          <div>
                            <h3 className="text-base font-semibold text-slate-950">{formatDateLabel(date)}</h3>
                            <p className="mt-0.5 text-xs text-slate-500">
                              {canManage
                                ? `Забрали ${dayMeta?.claimed || 0} из ${dayMeta?.total || 0} смен`
                                : dayMeta?.isDayOff
                                  ? 'День отмечен как выходной'
                                  : dayMeta?.myClaimed
                                    ? `Выбрано смен: ${dayMeta.myClaimed}`
                                    : 'Смена или выходной еще не выбраны'}
                            </p>
                          </div>
                          {myDayOffs.includes(date) ? <span className="rounded-full bg-blue-50 px-2 py-1 text-xs font-semibold text-blue-700">Выходной</span> : null}
                        </div>
                        {dateLots.length ? (
                        <div className="grid gap-2 md:grid-cols-2 2xl:grid-cols-3">
                          {dateLots.map((lot) => (
                            <div key={lot.id} className={`rounded-lg border p-3 ${lot.status === 'claimed' ? 'border-slate-200 bg-slate-50' : 'border-slate-200 bg-white'}`}>
                              <div className="flex items-start justify-between gap-3">
                                <div>
                                  <div className="text-base font-semibold text-slate-950">{lot.start_time} - {lot.end_time}</div>
                                  <div className="mt-1 text-xs text-slate-500">Мин. ставка: {Number(lot.rate_min || 0).toFixed(2)}</div>
                                  {canManage && lot.claimed_by_name ? (
                                    <div className="mt-1 text-xs font-medium text-emerald-700">Забрал: {lot.claimed_by_name}</div>
                                  ) : null}
                                </div>
                                {lot.status === 'available' && !canManage ? (
                                  <button
                                    type="button"
                                    onClick={() => handleClaimLot(lot.id)}
                                    disabled={!canClaim || claimingLotId === lot.id}
                                    className="inline-flex h-9 items-center rounded-md bg-blue-700 px-3 text-sm font-semibold text-white transition hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-blue-300"
                                  >
                                    {claimingLotId === lot.id ? '...' : 'Забрать'}
                                  </button>
                                ) : (
                                  <span className={`rounded-full px-2 py-1 text-xs font-semibold ${lot.status === 'claimed' ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-600'}`}>
                                    {lot.status === 'claimed' ? 'Занята' : 'Доступна'}
                                  </span>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                        ) : (
                          <div className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-6 text-center text-sm text-slate-500">
                            {myDayOffs.includes(date)
                              ? 'На этот день выбран выходной.'
                              : canManage
                                ? 'В этот день нет смен.'
                                : 'Для вас на этот день сейчас нет доступных смен.'}
                          </div>
                        )}
                      </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
                    {canManage ? 'Создайте тестовые смены для проверки realtime.' : 'Пока нет доступных смен.'}
                  </div>
                )}
              </div>
            </main>
          </section>
        )}

        {canManage && (
          <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 px-5 py-4">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-slate-950">Тестовый запуск</h2>
                  <p className="mt-1 text-sm text-slate-600">
                    Выберите операторов, задайте время открытия и создайте тестовые смены.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={handleSeedLots}
                    disabled={isSeeding}
                    className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-wait disabled:opacity-60"
                  >
                    <Sparkles size={16} />
                    {isSeeding ? 'Создание...' : 'Создать тестовые смены'}
                  </button>
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
            </div>

            <div className="grid gap-5 p-5 xl:grid-cols-[minmax(0,1fr)_360px]">
              <div className="space-y-4">
                <label className="flex items-center justify-between gap-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
                  <span>
                    <span className="block text-sm font-semibold text-slate-900">Включить тестовый режим</span>
                    <span className="block text-sm text-slate-500">Выбранные операторы увидят realtime-полигон аукциона.</span>
                  </span>
                  <input
                    type="checkbox"
                    checked={draftEnabled}
                    onChange={(event) => setDraftEnabled(event.target.checked)}
                    className="h-5 w-5 rounded border-slate-300 text-blue-700 focus:ring-blue-600"
                  />
                </label>

                <div className="grid gap-3 md:grid-cols-2">
                  <label className="block">
                    <span className="mb-2 block text-sm font-semibold text-slate-800">Старт аукциона</span>
                    <input
                      type="datetime-local"
                      value={draftStartsAt}
                      onChange={(event) => setDraftStartsAt(event.target.value)}
                      className="h-10 w-full rounded-lg border border-slate-200 px-3 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-2 block text-sm font-semibold text-slate-800">Завершение</span>
                    <input
                      type="datetime-local"
                      value={draftEndsAt}
                      onChange={(event) => setDraftEndsAt(event.target.value)}
                      className="h-10 w-full rounded-lg border border-slate-200 px-3 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                    />
                  </label>
                </div>

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

      {canUseAuction && dayNavigationItems.length ? (
        <div className="fixed inset-x-0 bottom-3 z-30 px-3 pointer-events-none">
          <div className="mx-auto max-w-5xl rounded-xl border border-slate-200 bg-white/95 p-2 shadow-2xl backdrop-blur pointer-events-auto">
            <div className="flex gap-2 overflow-x-auto">
              {dayNavigationItems.map((item) => {
                const tone = canManage
                  ? (item.claimed >= item.total && item.total > 0 ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : item.claimed > 0 ? 'border-blue-300 bg-blue-50 text-blue-800' : 'border-slate-200 bg-white text-slate-600')
                  : item.state === 'shift'
                    ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                    : item.state === 'off'
                      ? 'border-blue-300 bg-blue-50 text-blue-800'
                      : 'border-slate-200 bg-white text-slate-600';
                const statusText = canManage
                  ? `${item.claimed}/${item.total}`
                  : item.state === 'shift'
                    ? 'Смена'
                    : item.state === 'off'
                      ? 'Вых.'
                      : 'Пусто';
                return (
                  <button
                    key={item.date}
                    type="button"
                    onClick={() => scrollToDay(item.date)}
                    className={`min-w-[76px] rounded-lg border px-2 py-1.5 text-left transition hover:-translate-y-0.5 hover:shadow-sm ${tone}`}
                    title={formatDateLabel(item.date)}
                  >
                    <span className="block truncate text-[11px] font-semibold leading-4">{formatDateLabel(item.date)}</span>
                    <span className="mt-0.5 block text-xs font-bold tabular-nums">{statusText}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default ShiftAuctionView;

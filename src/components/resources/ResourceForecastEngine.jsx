import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { CalendarX, RefreshCw, Sparkles, Trash2, TrendingUp } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import { IosSegmented } from '../ui/ios';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import { RfPhoneGroup, RfPhoneGroupAction, RfPhoneNote, RfPhoneRow, RfPhoneToggleRow } from './ResourceFteMobile';

/*
 * Прогноз звонков линии (СЗоВ) новым движком: TimesFM в BigQuery + цикл месяца, люди
 * по Erlang A под цели SL/AR. Здесь — всё, что про движок видит человек: способ и цели
 * в «Настройках», сводка прогноза, коридор, поправки и ручной пересчёт в «Прогнозах»,
 * на компьютере и на телефоне. Сам расчёт и хранение — resource_fte/forecast_engine.py.
 */

export const ENGINE_DEPARTMENT = 'szov';
const RUN_POLL_MS = 8000;
// Первый пересчёт СЗоВ ещё и подтягивает два года истории из Oktell с паузами — минуты.
const RUN_POLL_LIMIT_MS = 8 * 60 * 1000;

const ruNumber = (value, digits = 0) => (
  value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))
    ? Number(value).toLocaleString('ru-RU', { maximumFractionDigits: digits, minimumFractionDigits: 0 })
    : '—'
);
const ruPercent = (ratio, digits = 1) => (
  ratio !== null && ratio !== undefined && ratio !== '' && Number.isFinite(Number(ratio)) ? `${ruNumber(Number(ratio) * 100, digits)} %` : '—'
);
const ruDate = (iso) => {
  if (!iso) return '—';
  const [y, m, d] = String(iso).slice(0, 10).split('-');
  return `${d}.${m}.${y}`;
};
const ruShortRange = (from, to) => {
  if (!from) return '—';
  const a = ruDate(from).slice(0, 5);
  const b = ruDate(to || from).slice(0, 5);
  return a === b ? a : `${a}–${b}`;
};

export const ENGINE_METHOD_HINT = {
  intro: 'Чем считаются звонки на будущие дни.',
  options: [
    ['TimesFM', 'модель Google в BigQuery: читает всю историю звонков и сама видит недельный цикл и волну середины месяца (подписание документов, блок после 15-го). На проверке по 61 неделе ошибка недельного объёма ~10 %.'],
    ['Прежний', 'тот же день недели 3 и 2 недели назад. Ошибка ~14 %, сильная волна середины месяца переносится на следующие недели.'],
  ],
};

/*
 * Состояние движка: данные, ручной пересчёт с ожиданием, поправки.
 * Пересчёт идёт на сервере в своём потоке; пока он идёт, запрос на ещё один не теряется —
 * сервер ставит его в очередь. «Занято» берём у сервера (run_state), а не только у себя:
 * кнопка не врёт, когда идёт ночной пересчёт или его запустил другой планировщик.
 */
export function useForecastEngine({ enabled, apiRoot, apiPrefix, buildHeaders, notify, onChanged }) {
  const [info, setInfo] = useState(null);
  const [waiting, setWaiting] = useState(false);
  const [deletingIds, setDeletingIds] = useState(() => new Set());
  const requestRef = useRef(0);
  const pollRef = useRef(null);
  const generationRef = useRef(0);
  const mountedRef = useRef(false);
  const infoRef = useRef(null);
  const deletingRef = useRef(new Set());
  // Свежие обработчики для опроса: за минуты пересчёта планировщик успевает сменить
  // период, и по окончании обновить надо уже его, а не тот, что был при запуске.
  const notifyRef = useRef(notify);
  const onChangedRef = useRef(onChanged);
  const headersRef = useRef(buildHeaders);
  useEffect(() => {
    notifyRef.current = notify;
    onChangedRef.current = onChanged;
    headersRef.current = buildHeaders;
  });

  const fetchInfo = useCallback(async () => {
    if (!enabled || !apiRoot) return null;
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    try {
      const response = await axios.get(`${apiRoot}${apiPrefix}/engine`, {
        params: { department: ENGINE_DEPARTMENT },
        headers: headersRef.current(),
      });
      if (!mountedRef.current || requestRef.current !== requestId) return null;
      const data = response.data || null;
      infoRef.current = data;
      setInfo(data);
      return data;
    } catch (error) {
      // Прежнее состояние остаётся: пропавший список поправок толкал бы завести их заново.
      return null;
    }
  }, [apiPrefix, apiRoot, enabled]);
  const fetchInfoRef = useRef(fetchInfo);
  useEffect(() => { fetchInfoRef.current = fetchInfo; }, [fetchInfo]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, []);
  useEffect(() => { fetchInfo(); }, [fetchInfo]);

  const busy = waiting || Boolean(info?.run_state?.department_busy);

  // Ждём пересчёт, который начнётся после `previousRun`. Новый вызов отменяет прежнее
  // ожидание (поколение), уход с экрана — тоже: ни тостов, ни запросов после него.
  const waitForRun = useCallback((previousRun) => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    if (pollRef.current) clearTimeout(pollRef.current);
    const started = Date.now();
    setWaiting(true);
    const finish = (message, tone) => {
      pollRef.current = null;
      setWaiting(false);
      if (message) notifyRef.current?.(message, tone);
      onChangedRef.current?.();
    };
    const tick = async () => {
      const data = await fetchInfoRef.current();
      if (!mountedRef.current || generation !== generationRef.current) return;
      if (data) {
        const run = data.last_run;
        const departmentBusy = Boolean(data.run_state?.department_busy);
        const newRun = Boolean(run) && (run.id !== previousRun?.id || previousRun?.status === 'running');
        if (!departmentBusy && newRun && run.status !== 'running') {
          if (run.status === 'success') {
            finish(run.method === 'calendar' ? 'Прогноз пересчитан календарной моделью: TimesFM был недоступен' : 'Прогноз пересчитан');
          } else {
            finish(run.detail || 'Пересчёт прогноза не удался', 'error');
          }
          return;
        }
        if (!departmentBusy && !newRun) {
          finish('Пересчёт не запустился — попробуйте ещё раз', 'error');
          return;
        }
      }
      if (Date.now() - started > RUN_POLL_LIMIT_MS) {
        finish('Пересчёт ещё идёт — цифры обновятся, когда он закончится');
        return;
      }
      pollRef.current = setTimeout(tick, RUN_POLL_MS);
    };
    pollRef.current = setTimeout(tick, RUN_POLL_MS);
  }, []);

  const run = useCallback(async () => {
    if (!enabled || busy) return;
    const previousRun = infoRef.current?.last_run || null;
    try {
      const response = await axios.post(`${apiRoot}${apiPrefix}/engine/run`, { department: ENGINE_DEPARTMENT }, {
        headers: headersRef.current({ 'Content-Type': 'application/json' }),
      });
      notifyRef.current?.(response.data?.status === 'queued'
        ? 'Идёт другой пересчёт — этот начнётся сразу за ним'
        : 'Пересчёт прогноза запущен');
      waitForRun(previousRun);
    } catch (error) {
      notifyRef.current?.(error?.response?.data?.error || 'Не удалось запустить пересчёт', 'error');
    }
  }, [apiPrefix, apiRoot, busy, enabled, waitForRun]);

  const addAdjustment = useCallback(async (draft) => {
    const previousRun = infoRef.current?.last_run || null;
    try {
      const response = await axios.post(`${apiRoot}${apiPrefix}/engine/adjustments`, { department: ENGINE_DEPARTMENT, ...draft }, {
        headers: headersRef.current({ 'Content-Type': 'application/json' }),
      });
      if (response.data?.adjustment?.kind === 'exclude') {
        notifyRef.current?.(response.data?.run === 'queued'
          ? 'Дни исключены — прогноз пересчитается сразу за идущим пересчётом'
          : 'Дни исключены — прогноз пересчитывается');
        waitForRun(previousRun);
        await fetchInfoRef.current();
      } else {
        notifyRef.current?.('Поправка добавлена');
        await fetchInfoRef.current();
        onChangedRef.current?.();
      }
      return true;
    } catch (error) {
      notifyRef.current?.(error?.response?.data?.error || 'Не удалось добавить поправку', 'error');
      return false;
    }
  }, [apiPrefix, apiRoot, waitForRun]);

  const deleteAdjustment = useCallback(async (adjustment) => {
    if (deletingRef.current.has(adjustment.id)) return;
    deletingRef.current.add(adjustment.id);
    setDeletingIds(new Set(deletingRef.current));
    const previousRun = infoRef.current?.last_run || null;
    try {
      const response = await axios.delete(`${apiRoot}${apiPrefix}/engine/adjustments/${adjustment.id}`, { headers: headersRef.current() });
      if (adjustment.kind === 'exclude') {
        notifyRef.current?.(response.data?.run === 'queued'
          ? 'Дни вернулись в историю — прогноз пересчитается сразу за идущим пересчётом'
          : 'Дни вернулись в историю — прогноз пересчитывается');
        waitForRun(previousRun);
        await fetchInfoRef.current();
      } else {
        notifyRef.current?.('Поправка удалена');
        await fetchInfoRef.current();
        onChangedRef.current?.();
      }
    } catch (error) {
      notifyRef.current?.(error?.response?.data?.error || 'Не удалось удалить поправку', 'error');
    } finally {
      deletingRef.current.delete(adjustment.id);
      if (mountedRef.current) setDeletingIds(new Set(deletingRef.current));
    }
  }, [apiPrefix, apiRoot, waitForRun]);

  return { info, busy, waiting, deletingIds, fetchInfo, run, addAdjustment, deleteAdjustment };
}

const Label = ({ children, hint }) => (
  <span className="flex min-h-5 items-center gap-1.5 text-xs font-medium text-slate-600">
    {children}
    {hint ? <InfoHint side="left" text={typeof hint === 'string' ? hint : undefined}>{typeof hint === 'string' ? null : hint}</InfoHint> : null}
  </span>
);

const MethodHint = () => (
  <div className="space-y-1.5">
    <div>{ENGINE_METHOD_HINT.intro}</div>
    {ENGINE_METHOD_HINT.options.map(([name, meaning]) => (
      <div key={name}><span className="font-semibold text-slate-800">{name}</span> — {meaning}</div>
    ))}
  </div>
);

// [ключ, подпись, пояснение, шаг, от, до, масштаб]: проценты хранятся долями (0,80), на
// экране — проценты (80); порог SL — секунды как есть.
const TARGET_FIELDS = [
  ['sl_target', 'SL за день, %', 'Доля звонков, отвеченных за порог, ко всем дошедшим до очереди — как считает табло. Цель держится в целом за день.', 1, 50, 99, 100],
  ['sl_seconds', 'Порог SL, сек', 'Сколько секунд ожидания в очереди считается «вовремя».', 5, 5, 300, 1],
  ['ar_min', 'AR от, %', 'Нижняя граница коридора потерь. Людей сверх цели не добавляем, но если по плану потерь меньше — значит, людей держит предел занятости или SL часа; такие дни отмечены в прогнозе.', 0.5, 0, 50, 100],
  ['ar_max', 'AR до, %', 'Верхняя граница потерь: людей ставим, пока потерь за день не станет не больше этого.', 0.5, 0.5, 50, 100],
  ['hour_sl_floor', 'SL часа не ниже, %', 'Чтобы дневная цель не выполнялась за счёт брошенных тихих часов: в каждом часе SL не ниже этого.', 5, 0, 95, 100],
  ['max_occupancy', 'Занятость до, %', 'Предел загрузки человека в любом часе: выше — люди выгорают, очередь растёт скачками.', 1, 50, 98, 100],
];

const toFieldText = (value, scale) => {
  if (value === '' || value === null || value === undefined || !Number.isFinite(Number(value))) return '';
  return String(Math.round(Number(value) * scale * 100) / 100);
};

/*
 * Поле с границами. Пока человек печатает, текст его — не переписываем («2,25» не
 * превращается в «2,3» на полуслове); в черновик уходит уже обрезанное границами
 * значение, так что сохранить «150 %» нельзя. При уходе с поля текст выравнивается.
 */
function useBoundedField(value, scale, min, max, onCommit) {
  const [text, setText] = useState(() => toFieldText(value, scale));
  const editingRef = useRef(false);
  useEffect(() => {
    if (!editingRef.current) setText(toFieldText(value, scale));
  }, [value, scale]);
  const commit = (raw, final) => {
    const cleaned = String(raw ?? '').replace(',', '.').trim();
    const parsed = Number(cleaned);
    if (cleaned === '' || !Number.isFinite(parsed)) {
      if (final) setText(toFieldText(value, scale));
      return;
    }
    const clamped = Math.min(max, Math.max(min, parsed));
    const stored = scale === 1 ? Math.round(clamped) : clamped / scale;
    onCommit(stored);
    if (final) setText(toFieldText(stored, scale));
  };
  return {
    value: text,
    onFocus: () => { editingRef.current = true; },
    onChange: (raw) => { setText(raw); commit(raw, false); },
    onBlur: (raw) => { editingRef.current = false; commit(raw, true); },
  };
}

function TargetField({ field, draft, setDraft, inputClass }) {
  const [key, label, hint, step, min, max, scale] = field;
  const bound = useBoundedField(draft[key], scale, min, max, (stored) => setDraft((current) => ({ ...(current || {}), [key]: stored })));
  return (
    <div>
      <Label hint={hint}>{label}</Label>
      <input type="number" step={step} min={min} max={max} value={bound.value} aria-label={label}
        onFocus={bound.onFocus} onChange={(event) => bound.onChange(event.target.value)} onBlur={(event) => bound.onBlur(event.target.value)}
        className={`${inputClass} mt-1 w-full`} />
    </div>
  );
}

function PhoneTargetField({ field, draft, setDraft }) {
  const [key, label, , , min, max, scale] = field;
  const bound = useBoundedField(draft[key], scale, min, max, (stored) => setDraft((current) => ({ ...(current || {}), [key]: stored })));
  return (
    <label className="rf-m-row flex w-full items-center gap-3 px-4 py-2">
      <span className="min-w-0 flex-1 text-[16px] text-slate-900">{label}</span>
      <input type="text" inputMode="decimal" value={bound.value} aria-label={label}
        onFocus={bound.onFocus} onChange={(event) => bound.onChange(event.target.value)} onBlur={(event) => bound.onBlur(event.target.value)}
        className="rf-m-input h-9 w-20 shrink-0 rounded-lg bg-slate-100 px-3 text-right text-[16px] font-semibold tabular-nums text-slate-900 outline-none focus:ring-2 focus:ring-blue-500/60" />
    </label>
  );
}

const MeasuredParams = ({ params }) => (
  <>
    AHT {params?.aht_seconds ? `${ruNumber(params.aht_seconds)} с` : '—'}
    {' · '}терпение {params?.patience_seconds ? `${ruNumber(params.patience_seconds)} с` : '—'}
    {params?.made_on ? ` · замер ${ruDate(params.made_on)}` : ''}
  </>
);
const PARAMS_HINT = 'AHT — разговор оператора, постобработка и удержание, без IVR и очереди. Терпение — сколько в среднем звонящий готов ждать в очереди. Оба меряются каждую ночь по звонкам последних 28 дней.';

/* «Настройки расчета»: способ прогноза и цели сервиса — верх той же карточки, что и
   прочие настройки, и сохраняются её кнопкой «Сохранить». */
export function EngineSettingsSection({ draft, setDraft, engine, inputClass }) {
  if (!draft) return null;
  const isEngine = (draft.forecast_engine || 'timesfm') === 'timesfm';
  return (
    <div className="space-y-3 border-b border-slate-100 pb-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
        <Sparkles size={16} />
        Прогноз и цели сервиса
      </div>
      <div className="space-y-1.5">
        <Label hint={<MethodHint />}>Способ прогноза</Label>
        <IosSegmented
          value={draft.forecast_engine || 'timesfm'}
          onChange={(value) => setDraft((current) => ({ ...(current || {}), forecast_engine: value }))}
          options={[{ value: 'timesfm', label: 'TimesFM' }, { value: 'legacy', label: 'Прежний' }]}
          ariaLabel="Способ прогноза"
          stretch
        />
      </div>
      {isEngine ? (
        <>
          <div className="grid grid-cols-2 gap-3">
            {TARGET_FIELDS.map((field) => (
              <TargetField key={field[0]} field={field} draft={draft} setDraft={setDraft} inputClass={inputClass} />
            ))}
          </div>
          <div className="flex items-center gap-1.5 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
            <span><MeasuredParams params={engine?.params} /></span>
            <InfoHint side="left" text={PARAMS_HINT} />
          </div>
        </>
      ) : (
        <p className="text-xs text-slate-500">Звонки — тот же день недели 3 и 2 недели назад, люди — через «Принято», OCC и UR ниже.</p>
      )}
    </div>
  );
}

/* То же на телефоне — группой над «Настройками расчета», та же кнопка «Сохранить». */
export function EnginePhoneSettings({ draft, setDraft, engine }) {
  if (!draft) return null;
  const isEngine = (draft.forecast_engine || 'timesfm') === 'timesfm';
  return (
    <RfPhoneGroup
      label="Прогноз и цели"
      hint={isEngine
        ? 'TimesFM — модель Google: видит недельный цикл и волну середины месяца. Людей по часам ставит Erlang A: SL за день не ниже цели, потери в коридоре AR, в каждом часе — не ниже SL часа и не выше предела занятости.'
        : 'Прежний способ: тот же день недели 3 и 2 недели назад, люди — через «Принято», OCC и UR.'}
    >
      <RfPhoneToggleRow
        title="Способ прогноза"
        toggle={(
          <IosSegmented
            value={draft.forecast_engine || 'timesfm'}
            onChange={(value) => setDraft((current) => ({ ...(current || {}), forecast_engine: value }))}
            options={[{ value: 'timesfm', label: 'TimesFM' }, { value: 'legacy', label: 'Прежний' }]}
            ariaLabel="Способ прогноза"
          />
        )}
      />
      {isEngine ? TARGET_FIELDS.map((field) => (
        <PhoneTargetField key={field[0]} field={field} draft={draft} setDraft={setDraft} />
      )) : null}
      {isEngine ? (
        <RfPhoneRow
          title="AHT · терпение"
          subtitle={engine?.params?.made_on ? `замер ${ruDate(engine.params.made_on)}` : null}
          value={`${ruNumber(engine?.params?.aht_seconds)} с · ${ruNumber(engine?.params?.patience_seconds)} с`}
        />
      ) : null}
    </RfPhoneGroup>
  );
}

const Stat = ({ label, value, hint }) => (
  <div className="min-w-0 rounded-lg bg-slate-50 px-3 py-2">
    <div className="text-[11px] font-medium text-slate-500">{label}</div>
    <div className="mt-0.5 truncate text-sm font-semibold text-slate-900" title={typeof value === 'string' ? value : undefined}>{value}</div>
    {hint ? <div className="mt-0.5 truncate text-[11px] text-slate-500">{hint}</div> : null}
  </div>
);

const midMonthPreset = () => {
  const now = new Date();
  const base = now.getDate() > 16 ? new Date(now.getFullYear(), now.getMonth() + 1, 1) : now;
  const from = new Date(base.getFullYear(), base.getMonth(), 13);
  const to = new Date(base.getFullYear(), base.getMonth(), 16);
  return { from: isoDate(from), to: isoDate(to) };
};

const ADJUSTMENT_HINT = (
  <div className="space-y-1.5">
    <div>То, чего модель знать не может.</div>
    <div><span className="font-semibold text-slate-800">Событие</span> — в эти дни звонков будет больше или меньше на указанный процент (сильная волна документов, рассылка, сбой приложения). Применяется сразу.</div>
    <div><span className="font-semibold text-slate-800">Исключить дни</span> — дни сбоя телефонии или ошибочных данных не учитываются в истории. Прогноз пересчитается за минуту-две.</div>
  </div>
);

// Прошедшие «события» ни на что уже не влияют — их не показываем; исключённые дни
// влияют на историю, пока она в окне модели, — показываем все.
const visibleAdjustments = (adjustments) => {
  const today = isoDate(new Date());
  return (adjustments || []).filter((item) => item.kind === 'exclude' || String(item.date_to) >= today);
};

const engineStatusLines = (summary, engine) => {
  const lines = [];
  const lastRun = engine?.last_run;
  const lastSuccess = engine?.last_success;
  const legacyDays = summary ? Math.max(0, Number(summary.period_days || 0) - Number(summary.days || 0)) : 0;
  if (summary && Number(summary.below_ar_min_days) > 0) {
    lines.push(['slate', `В ${ruNumber(summary.below_ar_min_days)} дн. потерь по плану меньше ${ruPercent(summary.targets?.ar_min, 0)}: людей с запасом — держит предел занятости или SL часа.`]);
  }
  if (legacyDays > 0) {
    lines.push(['amber', `${ruNumber(legacyDays)} дн. периода посчитаны прежним способом: на них прогноза TimesFM ещё нет.`]);
  }
  if (lastRun?.status === 'failed') {
    lines.push(['rose', `Последний пересчёт (${ruDate(lastRun.made_on)}) не удался: ${lastRun.detail || 'причина не записана'}.`]);
  }
  if (lastSuccess?.method === 'calendar') {
    lines.push(['amber', `TimesFM был недоступен — прогноз посчитан календарной моделью. ${lastSuccess.detail || ''}`.trim()]);
  }
  return lines;
};

const LINE_TONES = {
  slate: 'bg-slate-50 text-slate-600',
  amber: 'bg-amber-50 text-amber-800',
  rose: 'bg-rose-50 text-rose-800',
};

const callsLabel = (summary) => (
  Number(summary?.days) < Number(summary?.period_days)
    ? `Звонков за ${ruNumber(summary.days)} дн. TimesFM`
    : 'Звонков за период'
);

/* «Прогнозы»: сводка движка за период, коридор, поправки и ручной пересчёт. */
export function EngineForecastCard({ summary, engine, mode, busy, deletingIds, onRun, onAdd, onDelete, inputClass }) {
  const [kind, setKind] = useState('uplift');
  const [range, setRange] = useState(() => midMonthPreset());
  const [percent, setPercent] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const presets = useMemo(() => [
    { label: '13–16 число', range: midMonthPreset },
    { label: 'Сегодня', range: () => { const t = isoDate(new Date()); return { from: t, to: t }; } },
  ], []);

  if (mode === 'legacy') {
    return (
      <section className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600 shadow-sm">
        <TrendingUp size={16} className="shrink-0 text-slate-400" />
        Прогноз звонков — прежним способом: TimesFM выключен в «Настройках».
      </section>
    );
  }

  const lastSuccess = engine?.last_success;
  const adjustments = visibleAdjustments(engine?.adjustments);
  const statusLines = engineStatusLines(summary, engine);

  const submit = async () => {
    if (!range?.from) return;
    if (kind === 'uplift' && (percent === '' || !Number.isFinite(Number(percent)))) return;
    setSaving(true);
    const ok = await onAdd({ kind, date_from: range.from, date_to: range.to || range.from, percent: kind === 'uplift' ? Number(percent) : undefined, note });
    setSaving(false);
    if (ok) { setPercent(''); setNote(''); }
  };

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-900">
            <TrendingUp size={16} />
            Прогноз звонков: {summary?.method_label || 'TimesFM'}
            <InfoHint side="left">
              <div className="space-y-1.5">
                <MethodHint />
                <div className="text-slate-500">Коридор — 80 %: в 8 днях из 10 факт ложится внутрь. Людей ставим под цели SL и AR по Erlang A.</div>
              </div>
            </InfoHint>
          </div>
          <div className="mt-1 text-xs text-slate-500">
            {summary?.made_on
              ? `Сделан по данным на ${ruDate(summary.made_on)}`
              : lastSuccess
                ? 'На этот период прогноза TimesFM ещё нет — дни посчитаны прежним способом'
                : 'Первый прогноз появится после ночного пересчёта или по кнопке'}
          </div>
        </div>
        <button type="button" onClick={onRun} disabled={busy}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60">
          <RefreshCw size={16} className={busy ? 'animate-spin motion-reduce:animate-none' : ''} aria-hidden="true" />
          {busy ? 'Считаем прогноз…' : 'Пересчитать прогноз'}
        </button>
      </div>

      {summary ? (
        <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label={callsLabel(summary)} value={ruNumber(summary.calls)} hint={`коридор ${ruNumber(summary.calls_low)}–${ruNumber(summary.calls_high)}`} />
          <Stat label={`SL за ${ruNumber(summary.targets?.sl_seconds)} с, ожидаемый`} value={ruPercent(summary.period_sl)} hint={`цель от ${ruPercent(summary.targets?.sl_target, 0)}`} />
          <Stat label="AR, ожидаемый" value={ruPercent(summary.period_ar)} hint={`цель ${ruPercent(summary.targets?.ar_min, 0)}–${ruPercent(summary.targets?.ar_max, 0)}`} />
          <Stat label="AHT · терпение" value={`${ruNumber(summary.aht_seconds)} с · ${summary.patience_seconds ? `${ruNumber(summary.patience_seconds)} с` : '—'}`} hint={summary.params_measured_on ? `замер ${ruDate(summary.params_measured_on)}` : null} />
        </div>
      ) : null}

      {statusLines.map(([tone, text]) => (
        <div key={text} className={`mt-3 rounded-lg px-3 py-2 text-xs ${LINE_TONES[tone]}`}>{text}</div>
      ))}

      <div className="mt-4 border-t border-slate-100 pt-3">
        <div className="flex items-center gap-1.5 text-sm font-semibold text-slate-900">
          <CalendarX size={15} />
          Поправки
          <InfoHint side="left">{ADJUSTMENT_HINT}</InfoHint>
        </div>
        {adjustments.length ? (
          <div className="mt-2 divide-y divide-slate-100">
            {adjustments.map((item) => (
              <div key={item.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <div className="min-w-0">
                  <span className="font-medium text-slate-900">{ruShortRange(item.date_from, item.date_to)}</span>
                  <span className="text-slate-600">
                    {' · '}
                    {item.kind === 'exclude' ? 'исключены из истории' : `событие ${item.percent > 0 ? '+' : ''}${ruNumber(item.percent, 1)} %`}
                  </span>
                  {item.note ? <span className="text-slate-500">{' · '}{item.note}</span> : null}
                </div>
                <button type="button" onClick={() => onDelete(item)} aria-label="Удалить поправку" disabled={deletingIds?.has(item.id)}
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-40">
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <IosSegmented value={kind} onChange={setKind} ariaLabel="Вид поправки"
            options={[{ value: 'uplift', label: 'Событие' }, { value: 'exclude', label: 'Исключить дни' }]} />
          <IosDateRangePicker from={range?.from} to={range?.to} onChange={setRange} presets={presets} portal />
          {kind === 'uplift' ? (
            <input type="number" step={5} min={-90} max={300} value={percent} placeholder="+%"
              onChange={(event) => setPercent(event.target.value)} className={`${inputClass} w-24`} aria-label="Процент поправки" />
          ) : null}
          <input type="text" value={note} maxLength={300} placeholder="Комментарий"
            onChange={(event) => setNote(event.target.value)} className={`${inputClass} min-w-0 flex-1 sm:max-w-xs`} aria-label="Комментарий" />
          <button type="button" onClick={submit} disabled={saving || !range?.from || (kind === 'uplift' && percent === '')}
            className="inline-flex h-10 items-center rounded-lg bg-blue-600 px-4 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-50">
            Добавить
          </button>
        </div>
      </div>
    </section>
  );
}

/* То же на телефоне: сводка, пересчёт и поправки списком (добавляют их на компьютере). */
export function EnginePhoneForecast({ summary, engine, mode, busy, deletingIds, onRun, onDelete }) {
  if (mode === 'legacy') return null;
  const adjustments = visibleAdjustments(engine?.adjustments);
  const statusLines = engineStatusLines(summary, engine);
  return (
    <>
      <RfPhoneGroup
        label="Прогноз звонков"
        right={<RfPhoneGroupAction label="Пересчитать" onClick={onRun} busy={busy} />}
        hint={summary?.made_on ? `${summary.method_label} · по данным на ${ruDate(summary.made_on)}` : null}
      >
        {summary ? (
          <>
            <RfPhoneRow title={callsLabel(summary)} subtitle={`коридор ${ruNumber(summary.calls_low)}–${ruNumber(summary.calls_high)}`}
              value={ruNumber(summary.calls)} valueClassName="font-semibold text-slate-900" />
            <RfPhoneRow title="SL · AR, ожидаемые"
              subtitle={`цель SL от ${ruPercent(summary.targets?.sl_target, 0)}, AR ${ruPercent(summary.targets?.ar_min, 0)}–${ruPercent(summary.targets?.ar_max, 0)}`}
              value={`${ruPercent(summary.period_sl)} · ${ruPercent(summary.period_ar)}`} />
          </>
        ) : (
          <RfPhoneRow title="На этот период прогноза TimesFM ещё нет" muted />
        )}
        {adjustments.map((item) => (
          <RfPhoneRow
            key={item.id}
            title={`${ruShortRange(item.date_from, item.date_to)} · ${item.kind === 'exclude' ? 'исключены' : `событие ${item.percent > 0 ? '+' : ''}${ruNumber(item.percent, 1)} %`}`}
            subtitle={item.note || null}
            trailing={(
              <button type="button" onClick={() => onDelete(item)} aria-label="Удалить поправку" disabled={deletingIds?.has(item.id)}
                className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-slate-400 active:bg-slate-100 disabled:opacity-40">
                <Trash2 size={17} />
              </button>
            )}
          />
        ))}
      </RfPhoneGroup>
      {statusLines.map(([tone, text]) => (
        <RfPhoneNote key={text} tone={tone}>{text}</RfPhoneNote>
      ))}
    </>
  );
}

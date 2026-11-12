import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { IosModal, iosCard } from '../ui/ios';
import InfoHint from '../common/InfoHint';
import {
  formatUploadDayLabel,
  formatUploadHours,
  formatUploadPeriod,
  formatUploadShift,
  formatUploadShiftCount,
  formatUploadWhen,
  uploadDayChanged,
  uploadDayShiftTones,
} from './shiftAuctionUpload';

/*
 * Загрузка готовых смен в аукцион (Тез КЦ, задача #124).
 *
 * Файл — тот же Excel, что руководитель грузит в «Графики работы». Имена и ставки
 * строк не учитываются: каждая смена клетки становится сменой аукциона. Порядок —
 * как у загрузки таблиц в «Приёме»: файл → сверка с тем, что уже лежит на этот
 * период («было → станет», нераспознанные клетки) → «Подтвердить». Совпавший файл
 * второй раз не пишется, а запись идёт ровно по сверке: если смены периода
 * поменялись, пока окно было открыто, сервер откажет и попросит загрузить снова.
 */

const FORMAT_HINT = 'Тот же Excel, что для «Графиков работы»: первая строка — ФИО, ставка и даты, '
  + 'в клетках смены вида 9*18, 13*19/30, 21*09. Имена и ставки не учитываются — каждая '
  + 'смена из клетки станет сменой аукциона, клетка «11*15 18*20» — двумя сменами. '
  + '«Выходной» и пустые клетки пропускаются.';

const ShiftChip = ({ shift, tone = 'plain' }) => {
  const tones = {
    plain: 'bg-slate-100 text-slate-700',
    added: 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200',
    removed: 'bg-rose-50 text-rose-700 line-through ring-1 ring-rose-200',
  };
  return (
    <span className={`inline-flex h-6 items-center rounded-md px-1.5 text-[12px] font-semibold tabular-nums ${tones[tone] || tones.plain}`}>
      {formatUploadShift(shift)}
    </span>
  );
};

const ShiftAuctionUploadSheet = ({
  open,
  onClose,
  apiRoot,
  buildHeaders,
  direction,
  directionLabel,
  onSaved,
}) => {
  const inputRef = useRef(null);
  const requestRef = useRef(0);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [stage, setStage] = useState('pick');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    requestRef.current += 1;
    setFile(null);
    setPreview(null);
    setStage('pick');
    setError('');
  }, [open, direction]);

  const send = useCallback(async (selectedFile, extra = {}) => {
    const form = new FormData();
    form.append('file', selectedFile, selectedFile.name);
    form.append('direction', direction);
    Object.entries(extra).forEach(([key, value]) => form.append(key, value));
    const response = await axios.post(`${apiRoot}/api/shift_auction/upload_shifts`, form, {
      headers: buildHeaders(),
    });
    return response?.data?.upload || null;
  }, [apiRoot, buildHeaders, direction]);

  const handlePick = useCallback(async (event) => {
    const selectedFile = event.target.files?.[0] || null;
    // Тот же файл можно выбрать снова — без сброса input второй выбор молчит.
    event.target.value = '';
    if (!selectedFile) return;
    const token = requestRef.current + 1;
    requestRef.current = token;
    setFile(selectedFile);
    setPreview(null);
    setError('');
    setStage('loading');
    try {
      const result = await send(selectedFile);
      if (requestRef.current !== token) return;
      setPreview(result);
      setStage('preview');
    } catch (requestError) {
      if (requestRef.current !== token) return;
      setError(requestError?.response?.data?.error || 'Не удалось прочитать файл');
      setStage('pick');
    }
  }, [send]);

  const handleConfirm = useCallback(async () => {
    if (!file || !preview || stage === 'saving') return;
    const token = requestRef.current + 1;
    requestRef.current = token;
    setStage('saving');
    setError('');
    try {
      const result = await send(file, {
        confirm: '1',
        base_fingerprint: preview.base_fingerprint || '',
        file_sha256: preview.file_sha256 || '',
      });
      if (requestRef.current !== token) return;
      onSaved?.(result);
    } catch (requestError) {
      if (requestRef.current !== token) return;
      const code = requestError?.response?.data?.code;
      const message = requestError?.response?.data?.error || 'Не удалось сохранить смены';
      // Сверка устарела (смены периода или сам файл поменялись): старая уже не правда,
      // и «Подтвердить» по ней снова откажет — сверяем заново тем же файлом.
      if (code === 'AUCTION_UPLOAD_CHANGED' || code === 'AUCTION_UPLOAD_FILE_CHANGED') {
        try {
          const fresh = await send(file);
          if (requestRef.current !== token) return;
          setPreview(fresh);
          setError(`${message}. Сверка обновлена — проверьте и подтвердите снова.`);
          setStage('preview');
        } catch (refreshError) {
          if (requestRef.current !== token) return;
          setPreview(null);
          setError(refreshError?.response?.data?.error || message);
          setStage('pick');
        }
        return;
      }
      setError(message);
      setStage('preview');
    }
  }, [file, onSaved, preview, send, stage]);

  const busy = stage === 'loading' || stage === 'saving';
  const blocked = preview?.blocked || null;
  const unchanged = Boolean(preview?.is_unchanged);
  const canConfirm = Boolean(preview && !blocked && !unchanged && preview.shift_count > 0 && !busy);
  const changes = preview?.changes || null;
  const existing = preview?.existing || null;

  const footer = preview && unchanged ? (
    <button
      type="button"
      onClick={onClose}
      className="h-11 w-full rounded-xl bg-slate-900 text-[15px] font-semibold text-white transition active:scale-[0.98]"
    >
      Готово
    </button>
  ) : (
    <button
      type="button"
      onClick={handleConfirm}
      disabled={!canConfirm}
      className="h-11 w-full rounded-xl bg-blue-600 text-[15px] font-semibold text-white transition active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500"
    >
      {stage === 'saving' ? 'Сохраняю…' : 'Подтвердить'}
    </button>
  );

  return (
    <IosModal
      open={open}
      onClose={() => (stage === 'saving' ? false : onClose?.())}
      title={(
        <span className="inline-flex items-center">
          Загрузить смены
          <InfoHint side="left" className="ml-1.5 align-middle" text={FORMAT_HINT} />
        </span>
      )}
      subtitle={directionLabel || ''}
      footer={stage === 'pick' || stage === 'loading' ? null : footer}
      maxWidth="max-w-2xl"
      sizeKey={preview ? 'preview' : 'pick'}
    >
      <div className="space-y-3">
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          className="hidden"
          onChange={handlePick}
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          className={`${iosCard} flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-slate-50 active:scale-[0.99] disabled:cursor-wait`}
        >
          <span className="min-w-0">
            <span className="block truncate text-[15px] font-semibold text-slate-900">
              {file ? file.name : 'Выбрать файл Excel'}
            </span>
            <span className="block text-[13px] text-slate-500">
              {stage === 'loading'
                ? 'Сверяю…'
                : (file
                  ? (preview && preview.sheet_count > 1 ? `Лист «${preview.sheet_name}» · выбрать другой файл` : 'Выбрать другой файл')
                  : 'График с датами в первой строке')}
            </span>
          </span>
          <span className="shrink-0 rounded-lg bg-blue-50 px-3 py-1.5 text-[13px] font-semibold text-blue-700">
            {file ? 'Заменить' : 'Выбрать'}
          </span>
        </button>

        {error ? (
          <p className="rounded-xl bg-rose-50 px-4 py-3 text-[14px] leading-snug text-rose-700">{error}</p>
        ) : null}

        {preview ? (
          <>
            <div className={`${iosCard} px-4 py-3`}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <span className="text-[15px] font-semibold text-slate-900">
                  {formatUploadPeriod(preview.date_from, preview.date_to)}
                </span>
                <span className="text-[14px] font-semibold tabular-nums text-slate-700">
                  {formatUploadShiftCount(preview.shift_count)} · {formatUploadHours(preview.net_minutes)} ч без перерывов
                </span>
              </div>
              {existing && changes ? (
                <p className="mt-1 text-[13px] leading-snug text-slate-600">
                  {unchanged
                    ? (preview.same_file
                      ? `Этот файл уже загружали ${formatUploadWhen(existing.uploaded_at)}${existing.uploaded_by_name ? ` — ${existing.uploaded_by_name}` : ''}. Изменений нет.`
                      : 'Смены совпадают с уже загруженными. Изменений нет.')
                    : (
                      <>
                        Было {existing.shift_count} → станет {preview.shift_count}
                        {changes.added ? <> · <span className="font-semibold text-emerald-700">добавится {changes.added}</span></> : null}
                        {changes.removed ? <> · <span className="font-semibold text-rose-700">уберётся {changes.removed}</span></> : null}
                      </>
                    )}
                </p>
              ) : null}
              {preview.skipped_past ? (
                <p className="mt-1 text-[13px] leading-snug text-slate-600">
                  Дни по {formatUploadDayLabel(preview.skipped_past.date_to)} пропущены
                  ({formatUploadShiftCount(preview.skipped_past.shift_count)}) — аукцион идёт только по будущим дням.
                </p>
              ) : null}
              {preview.overlaps_active ? (
                <p className="mt-1 text-[13px] leading-snug text-slate-600">
                  Пересекается с периодом {formatUploadPeriod(preview.overlaps_active.date_from, preview.overlaps_active.date_to).split(' · ')[0]},
                  который сейчас в аукционе: он останется как есть. При переходе на новый период выбор операторов сбросится.
                </p>
              ) : null}
              {preview.truncated_after_row ? (
                <p className="mt-1 text-[13px] leading-snug text-rose-700">
                  Прочитаны строки до {preview.truncated_after_row}-й — остальные в сверку не попали.
                </p>
              ) : null}
              {preview.active_in_auction && !blocked && !unchanged ? (
                <p className="mt-1 text-[13px] leading-snug text-slate-600">
                  Период уже стоит в аукционе — смены в нём заменятся сразу.
                </p>
              ) : null}
            </div>

            {blocked ? (
              <p className="rounded-xl bg-rose-50 px-4 py-3 text-[14px] leading-snug text-rose-700">{blocked.message}</p>
            ) : null}

            {preview.invalid_total ? (
              <div className="rounded-xl bg-rose-50 px-4 py-3">
                <p className="text-[14px] font-semibold text-rose-800">
                  Не распознано клеток: {preview.invalid_total} — они не попадут в аукцион
                </p>
                <ul className="mt-1 space-y-0.5 text-[13px] leading-snug text-rose-700">
                  {preview.invalid_cells.map((cell) => (
                    <li key={`${cell.cell}-${cell.value}`} className="tabular-nums">
                      {cell.cell} · «{cell.value}» — {cell.error}
                    </li>
                  ))}
                  {preview.invalid_total > preview.invalid_cells.length ? (
                    <li>и ещё {preview.invalid_total - preview.invalid_cells.length}</li>
                  ) : null}
                </ul>
              </div>
            ) : null}

            <div className={`${iosCard} divide-y divide-slate-100`}>
              {preview.days.map((day) => {
                const changed = uploadDayChanged(day);
                return (
                  <div key={day.date} className="flex gap-3 px-4 py-2.5">
                    <div className="w-[92px] shrink-0">
                      <span className="block text-[14px] font-semibold text-slate-900">{formatUploadDayLabel(day.date)}</span>
                      <span className="block text-[12px] tabular-nums text-slate-500">
                        {formatUploadShiftCount(day.count)}{changed && day.previous_count !== null ? ` · было ${day.previous_count}` : ''}
                      </span>
                    </div>
                    <div className="flex min-w-0 flex-1 flex-wrap gap-1">
                      {uploadDayShiftTones(day).map(({ shift, tone }, index) => (
                        <ShiftChip key={`${shift.start}-${shift.end}-${index}`} shift={shift} tone={tone} />
                      ))}
                      {(day.removed || []).map((shift, index) => (
                        <ShiftChip key={`removed-${shift.start}-${shift.end}-${index}`} shift={shift} tone="removed" />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        ) : null}
      </div>
    </IosModal>
  );
};

export default ShiftAuctionUploadSheet;

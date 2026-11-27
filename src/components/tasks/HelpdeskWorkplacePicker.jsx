import React, { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { SEAT_GAP, SEAT_H, SEAT_W, cabinetLabel, cabinetSeatNumbers } from '../technical/workplaceCabinets';
import { seatKey, selectionFromValue, toggleCabinetSeats, workplaceValue } from './helpdeskTicket';

/*
 * Рабочие места на схеме кабинета — поле «Где (№ РМ)» заявки в IT. Клик по
 * месту выбирает РМ, «Весь кабинет» — для массового сбоя. Значение поля —
 * строка «РМ 3, 5 (Тех поддержка)»: её прочтут и ИИ, и сисадмин в задаче.
 *
 * В «Тех причинах» схема стояла в натуральную величину и в окне уезжала за
 * край с прокруткой. Здесь холст уменьшается под ширину формы, но не мельче
 * MIN_SCALE — мельче по месту не попасть пальцем; дальше — прокрутка вбок.
 * Цвета — переменными раздела: схема та же и в тёмной теме.
 */
const MIN_SCALE = 0.56;

const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

const Seat = memo(function Seat({ number, selected, disabled, onToggle }) {
  return (
    <button
      type="button"
      className={`tv-hd-seat${selected ? ' is-selected' : ''}`}
      style={{ width: SEAT_W, height: SEAT_H }}
      aria-pressed={selected}
      aria-label={`РМ ${number}`}
      disabled={disabled}
      onClick={() => onToggle(number)}
    >
      {number}
    </button>
  );
});

const HelpdeskWorkplacePicker = ({ cabinets, value, onChange, disabled = false, invalid = false, labelId }) => {
  const list = useMemo(() => (Array.isArray(cabinets) ? cabinets : []), [cabinets]);
  const selected = useMemo(() => selectionFromValue(value, list), [value, list]);
  const firstSelectedCabinet = useMemo(() => {
    const first = [...selected][0];
    return first ? String(first).split(':')[0] : null;
  }, [selected]);
  /* Выбор на схеме приходит и извне — ИИ распознал «РМ 50» в описании: открываем
     тот кабинет, где он есть (у ОП их три), а не всегда первый. */
  const [activeId, setActiveId] = useState(() => firstSelectedCabinet || list[0]?.id || null);

  /* Своё значение — то, что схема отдала сама (клик, «Весь кабинет»). Чужое —
     пришло извне (ИИ): только на него схема переключает кабинет и подводит
     прокрутку; под пальцем ничего не двигается. */
  const emittedRef = useRef(null);
  const external = String(value ?? '') !== emittedRef.current;
  useEffect(() => {
    setActiveId((prev) => {
      const known = list.some((cabinet) => cabinet.id === prev);
      if (!known) return firstSelectedCabinet || list[0]?.id || null;
      const prevHasSelection = [...selected].some((key) => String(key).split(':')[0] === prev);
      if (external && firstSelectedCabinet && !prevHasSelection) return firstSelectedCabinet;
      return prev;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, firstSelectedCabinet, value]);
  const active = list.find((cabinet) => cabinet.id === activeId) || list[0] || null;

  const emit = useCallback((next) => {
    const text = workplaceValue(next, list);
    emittedRef.current = text;
    onChange(text);
  }, [list, onChange]);
  const toggle = useCallback((number) => {
    if (!active || disabled) return;
    const next = new Set(selected);
    const key = seatKey(active.id, number);
    if (next.has(key)) next.delete(key); else next.add(key);
    emit(next);
  }, [active, disabled, emit, selected]);

  const activeSeats = useMemo(() => (active ? cabinetSeatNumbers(active) : []), [active]);
  const wholeCabinet = activeSeats.length > 0 && activeSeats.every((number) => selected.has(seatKey(active.id, number)));
  const toggleWholeCabinet = () => {
    if (!active || disabled) return;
    emit(toggleCabinetSeats(selected, active));
  };

  // Сколько выбрано в каждом кабинете — счётчиком в его сегменте.
  const countByCabinet = useMemo(() => {
    const counts = new Map();
    selected.forEach((key) => {
      const cabinetId = String(key).split(':')[0];
      counts.set(cabinetId, (counts.get(cabinetId) || 0) + 1);
    });
    return counts;
  }, [selected]);

  /* Масштаб — по ширине поля. ResizeObserver: форма шире на большом экране и
     во весь экран на телефоне, а ширина меняется и без смены окна. */
  const frameRef = useRef(null);
  const [frameWidth, setFrameWidth] = useState(0);
  useIsoLayoutEffect(() => {
    const node = frameRef.current;
    if (!node) return undefined;
    const read = () => setFrameWidth(node.clientWidth || 0);
    read();
    if (typeof ResizeObserver !== 'function') return undefined;
    const observer = new ResizeObserver(read);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  /* На телефоне схема шире экрана и прокручивается вбок. Место, которое выбрал
     ИИ («РМ 27» из описания — правый верхний угол кабинета), иначе осталось бы
     за краем: подводим к нему. Выбор пальцем схему не двигает. */
  const width = active?.width || 600;
  const height = active?.height || 400;
  const scale = frameWidth > 0 ? Math.max(MIN_SCALE, Math.min(1, (frameWidth - 24) / width)) : 1;
  const firstSelected = useMemo(() => {
    if (!active) return null;
    const numbers = [...selected]
      .map((key) => String(key).split(':'))
      .filter(([cabinetId]) => cabinetId === active.id)
      .map(([, number]) => Number(number));
    return numbers.length ? Math.min(...numbers) : null;
  }, [active, selected]);
  useIsoLayoutEffect(() => {
    if (!external) return;
    const frame = frameRef.current;
    if (!frame || firstSelected === null || frame.scrollWidth <= frame.clientWidth + 1) return;
    const seat = frame.querySelector(`.tv-hd-seat[aria-label="РМ ${firstSelected}"]`);
    if (!seat) return;
    const frameBox = frame.getBoundingClientRect();
    const seatBox = seat.getBoundingClientRect();
    frame.scrollLeft += (seatBox.left + seatBox.width / 2) - (frameBox.left + frameBox.width / 2);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id, firstSelected, scale, value]);

  if (!list.length) {
    // Схемы для отдела нет — номер РМ словами, как писали раньше.
    return (
      <input
        type="text"
        className="tv-input"
        aria-labelledby={labelId}
        value={value ?? ''}
        disabled={disabled}
        placeholder="Например: РМ 16 СЗоВ"
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }

  const summary = workplaceValue(selected, list);

  return (
    <div className={`tv-hd-map${invalid ? ' is-invalid' : ''}`} role="group" aria-labelledby={labelId}>
      {list.length > 1 && (
        <div className="tv-composer-segmented tv-hd-map-tabs" role="tablist" aria-label="Кабинет">
          {list.map((cabinet) => {
            const count = countByCabinet.get(cabinet.id) || 0;
            return (
              <button
                key={cabinet.id}
                type="button"
                role="tab"
                aria-selected={cabinet.id === active?.id}
                className={cabinet.id === active?.id ? 'is-active' : ''}
                onClick={() => setActiveId(cabinet.id)}
              >
                {cabinetLabel(cabinet)}
                {count > 0 && <span className="tv-composer-segment-count">{count}</span>}
              </button>
            );
          })}
        </div>
      )}
      <div className="tv-hd-map-frame" ref={frameRef}>
        <div className="tv-hd-map-canvas" style={{ width: width * scale, height: height * scale }}>
          <div
            className="tv-hd-map-plan"
            style={{ width, height, transform: scale === 1 ? undefined : `scale(${scale})` }}
          >
            {(active?.blocks || []).map((block, blockIndex) => (
              <div
                key={`block-${blockIndex}`}
                className="tv-hd-map-block"
                style={{
                  left: block.left,
                  top: block.top,
                  gap: SEAT_GAP,
                  ...(block.rotate ? { transform: `rotate(${block.rotate}deg)`, transformOrigin: 'top left' } : {}),
                }}
              >
                {(block.rows || []).map((row, rowIndex) => (
                  <div key={rowIndex} className="tv-hd-map-row" style={{ gap: SEAT_GAP }}>
                    {row.map((number, cellIndex) => (
                      number === null || number === undefined
                        ? <span key={`gap-${rowIndex}-${cellIndex}`} style={{ width: SEAT_W, height: SEAT_H }} />
                        : (
                          <Seat
                            key={number}
                            number={number}
                            selected={selected.has(seatKey(active.id, number))}
                            disabled={disabled}
                            onToggle={toggle}
                          />
                        )
                    ))}
                  </div>
                ))}
              </div>
            ))}
            {(active?.freeSeats || []).map((seat) => (
              <div
                key={`free-${seat.n}`}
                className="tv-hd-map-free"
                style={{
                  left: seat.left,
                  top: seat.top,
                  ...(seat.rotate ? { transform: `rotate(${seat.rotate}deg)`, transformOrigin: 'top left' } : {}),
                }}
              >
                <Seat
                  number={seat.n}
                  selected={selected.has(seatKey(active.id, seat.n))}
                  disabled={disabled}
                  onToggle={toggle}
                />
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="tv-hd-map-foot">
        <span className={`tv-hd-map-summary${summary ? '' : ' is-empty'}`}>{summary || 'Нажмите на место на схеме'}</span>
        <span className="tv-hd-map-actions">
          <button
            type="button"
            className={wholeCabinet ? 'is-active' : ''}
            aria-pressed={wholeCabinet}
            disabled={disabled}
            onClick={toggleWholeCabinet}
          >
            Весь кабинет
          </button>
          {selected.size > 0 && (
            <button type="button" disabled={disabled} onClick={() => emit(new Set())}>Очистить</button>
          )}
        </span>
      </div>
    </div>
  );
};

export default memo(HelpdeskWorkplacePicker);

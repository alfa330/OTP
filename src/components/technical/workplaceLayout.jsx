import React from 'react';
import { SEAT_W, SEAT_H, SEAT_GAP } from './workplaceCabinets';

// ─── Раскладки кабинетов (РМ) по отделам ───────────────────────────────────────
// Данные и правила живут в workplaceCabinets.js (без React — их берут и тесты);
// здесь они переэкспортированы для прежних импортов и добавлена отрисовка схемы.
// Используется в аналитике РМ (тепловая карта); выбор РМ в заявке в IT рисует
// схему сам — tasks/HelpdeskWorkplacePicker.jsx.
export {
    SEAT_W,
    SEAT_H,
    SEAT_GAP,
    SEAT_STEP_X,
    SEAT_STEP_Y,
    WORKPLACE_CABINETS,
    cabinetSeatNumbers,
    cabinetLabel,
    visibleCabinetsFor,
} from './workplaceCabinets';

// ─── CabinetMap ────────────────────────────────────────────────────────────────
// Универсальный рендер схемы одного кабинета: рисует блоки и «свободные»
// (повёрнутые) РМ, а каждую плитку отдаёт на откуп renderSeat(seatNumber).
export const CabinetMap = React.memo(function CabinetMap({ cabinet, renderSeat }) {
    const W = cabinet?.width || 600;
    const H = cabinet?.height || 400;
    return (
        <div className="rounded-xl border border-slate-300 bg-slate-50/80 p-3 overflow-auto">
            <div style={{ position: 'relative', width: W, height: H, minWidth: W }}>
                {(cabinet?.blocks || []).map((b, bi) => (
                    <div
                        key={`block-${bi}`}
                        style={{
                            position: 'absolute',
                            left: b.left,
                            top: b.top,
                            display: 'inline-flex',
                            flexDirection: 'column',
                            gap: SEAT_GAP,
                            border: '1.5px solid #64748b',
                            borderRadius: 4,
                            padding: 3,
                            ...(b.rotate ? { transform: `rotate(${b.rotate}deg)`, transformOrigin: 'top left' } : {}),
                        }}
                    >
                        {(b.rows || []).map((row, ri) => (
                            <div key={ri} style={{ display: 'flex', gap: SEAT_GAP }}>
                                {row.map((n, ci) => (
                                    n === null || n === undefined
                                        ? <div key={`gap-${ri}-${ci}`} style={{ width: SEAT_W, height: SEAT_H }} />
                                        : <React.Fragment key={n}>{renderSeat(n)}</React.Fragment>
                                ))}
                            </div>
                        ))}
                    </div>
                ))}
                {(cabinet?.freeSeats || []).map((s) => (
                    <div
                        key={`free-${s.n}`}
                        style={{
                            position: 'absolute',
                            left: s.left,
                            top: s.top,
                            ...(s.rotate ? { transform: `rotate(${s.rotate}deg)`, transformOrigin: 'top left' } : {}),
                        }}
                    >
                        {renderSeat(s.n)}
                    </div>
                ))}
            </div>
        </div>
    );
});

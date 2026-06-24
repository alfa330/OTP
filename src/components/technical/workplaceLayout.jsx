import React from 'react';

// ─── Раскладки кабинетов (РМ) по отделам ───────────────────────────────────────
// Общий модуль: используется и в аналитике РМ (тепловая карта), и в окне IT-тикета
// (визуальный выбор РМ). Координаты — абсолютные пиксели внутри холста кабинета.

export const SEAT_W = 72;
export const SEAT_H = 56;
export const SEAT_GAP = 3;
export const SEAT_STEP_X = SEAT_W + SEAT_GAP; // 75
export const SEAT_STEP_Y = SEAT_H + SEAT_GAP; // 59

// group: 'support' — тех-поддержка (раскладка 1–30, один кабинет);
//        'sales'   — отдел продаж (ОП), 3 кабинета, РМ 28–98.
export const WORKPLACE_CABINETS = [
    {
        id: 'support',
        group: 'support',
        name: 'Тех поддержка',
        floor: '',
        width: 290 + 7 * SEAT_STEP_X + 20,
        height: 148 + 2 * SEAT_STEP_Y + 90 + SEAT_STEP_Y + 20,
        blocks: [
            { left: 290, top: 10, rows: [[27, 26, 25, 24, 23, 22]] },
            { left: 10, top: 148, rows: [[30, 29, 28]] },
            { left: 290, top: 148, rows: [[15, 16, 17, 18, 19, 20, 21], [14, 13, 12, 11, 10, 9, 8]] },
            { left: 290, top: 148 + 2 * SEAT_STEP_Y + 90, rows: [[1, 2, 3, 4, 5, 6, 7]] },
        ],
    },
    {
        id: 'op17',
        group: 'sales',
        name: 'ОП',
        floor: '17 этаж',
        width: 380 + 6 * SEAT_STEP_X + 30,
        height: 480,
        blocks: [
            { left: 380, top: 0, rows: [[70, 71, 72, 73, 74, 75]] },
            { left: 380, top: 120, rows: [[81, 80, 79, 78, 77, 76], [82, 83, 84, 85, 86, 87]] },
            // Нижний ряд начинается с 94 (выступает влево), далее 93..88
            { left: 305, top: 300, rows: [[94, 93, 92, 91, 90, 89, 88]] },
        ],
        // Угловые РМ слева-внизу: плотный параллельный столбик 98–97–96 (+42°);
        // 95 — чуть более вертикально (+32°), почти вплотную к 94.
        freeSeats: [
            { n: 98, left: 64, top: 140, rotate: 42 },
            { n: 97, left: 118, top: 188, rotate: 42 },
            { n: 96, left: 172, top: 236, rotate: 42 },
            { n: 95, left: 242, top: 274, rotate: 32 },
        ],
    },
    {
        id: 'op2',
        group: 'sales',
        name: 'ОП 2',
        floor: '18 этаж',
        width: 20 + 6 * SEAT_STEP_X + 40 + SEAT_STEP_X + 20,
        height: 40 + 2 * SEAT_STEP_Y + 70 + SEAT_STEP_Y + 30,
        blocks: [
            { left: 20, top: 40, rows: [[65, 64, 63, 62, 61, 60], [54, 55, 56, 57, 58, 59]] },
            { left: 20, top: 40 + 2 * SEAT_STEP_Y + 70, rows: [[53, 52, 51, 50, 49, 48]] },
            { left: 20 + 6 * SEAT_STEP_X + 40, top: 20, rows: [[66], [67], [68], [69]] },
        ],
    },
    {
        id: 'op3',
        group: 'sales',
        name: 'ОП 3 (Честный)',
        floor: '18 этаж',
        width: 20 + 8 * SEAT_STEP_X + 20,
        height: 40 + 2 * SEAT_STEP_Y + 70 + SEAT_STEP_Y + 30,
        blocks: [
            { left: 20, top: 40, rows: [[47, 46, 45, 44, 43, 42], [36, 37, 38, 39, 40, 41]] },
            { left: 20, top: 40 + 2 * SEAT_STEP_Y + 70, rows: [[35, 34, 33, 32, 31, 30, 29, 28]] },
        ],
    },
];

export const cabinetSeatNumbers = (cabinet) => {
    const out = [];
    (cabinet?.blocks || []).forEach((b) => (b.rows || []).forEach((r) => r.forEach((n) => {
        if (typeof n === 'number') out.push(n);
    })));
    (cabinet?.freeSeats || []).forEach((s) => { if (typeof s?.n === 'number') out.push(s.n); });
    return out;
};

export const cabinetLabel = (cabinet) => (cabinet?.floor ? `${cabinet.name} · ${cabinet.floor}` : cabinet?.name || '');

export const visibleCabinetsFor = ({ isAdmin, departmentCode }) => {
    if (isAdmin) return WORKPLACE_CABINETS;
    if (String(departmentCode || '').toLowerCase() === 'op') {
        return WORKPLACE_CABINETS.filter((c) => c.group === 'sales');
    }
    return WORKPLACE_CABINETS.filter((c) => c.group === 'support');
};

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

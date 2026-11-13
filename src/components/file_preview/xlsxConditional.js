/* Условное форматирование Excel → цвета ячеек в окне просмотра.

   На снимке владельца 13.11.2026 («2026.xlsx», лист «03.09») столбцы
   «Чаты/час», «% факт», «Ср. время», «Разница от таргета» раскрашены цветовыми
   шкалами — красный, жёлтый, зелёный. В файле таких цветов нет: есть правило,
   и считать цвет приходится самим по значениям ячеек.

   Поддержано: цветовые шкалы (2 и 3 цвета; min, max, число, процент,
   процентиль, формула), гистограммы, «значение ячейки» (больше, между…),
   формулы (xlsxFormula), текст (содержит, начинается…), пустые и ошибки,
   выше/ниже среднего, первые/последние N и N %, повторяющиеся и уникальные.
   Порядок — по priority, stopIfTrue останавливает младшие правила; формат
   старшего правила побеждает в каждом свойстве (заливка, цвет, начертание).
   Наборы значков и правила по датам не рисуются.

   Модуль чистый — проверяется в node (tests/file_preview.test.mjs). */
import { attr, child, childrenOf, parseXml, textContent } from './xmlTree.js';
import { columnIndex } from './xlsxFormat.js';
import { compileFormula, compareValues, isError, toBool, UnsupportedFormula } from './xlsxFormula.js';
import { mixColors, resolveColor } from './xlsxStyle.js';

const BLOCK = /<conditionalFormatting\b[\s\S]*?<\/conditionalFormatting>/g;

export function parseRange(text) {
    const [from, to = from] = String(text || '').toUpperCase().replace(/\$/g, '').split(':');
    const a = /^([A-Z]{1,3})(\d+)$/.exec(from);
    const b = /^([A-Z]{1,3})(\d+)$/.exec(to);
    if (!a || !b) return null;
    const r1 = Number(a[2]) - 1;
    const r2 = Number(b[2]) - 1;
    const c1 = columnIndex(a[1]);
    const c2 = columnIndex(b[1]);
    return { top: Math.min(r1, r2), bottom: Math.max(r1, r2), left: Math.min(c1, c2), right: Math.max(c1, c2) };
}

export function readConditionalRules(sheetXml, styles) {
    const rules = [];
    const blocks = String(sheetXml || '').match(BLOCK) || [];
    blocks.forEach((block, blockIndex) => {
        const root = parseXml(block).children.find((node) => typeof node === 'object');
        const ranges = String(attr(root, 'sqref') || '').split(/\s+/).map(parseRange).filter(Boolean);
        if (!ranges.length) return;
        childrenOf(root, 'cfRule').forEach((node, ruleIndex) => {
            const colorScale = child(node, 'colorScale');
            const dataBar = child(node, 'dataBar');
            rules.push({
                type: attr(node, 'type') || '',
                priority: Number(attr(node, 'priority')) || (blockIndex * 100 + ruleIndex + 1),
                stopIfTrue: ['1', 'true'].includes(String(attr(node, 'stopIfTrue'))),
                operator: attr(node, 'operator') || '',
                text: attr(node, 'text') ?? '',
                rank: Number(attr(node, 'rank')) || 10,
                percent: ['1', 'true'].includes(String(attr(node, 'percent'))),
                bottom: ['1', 'true'].includes(String(attr(node, 'bottom'))),
                above: !['0', 'false'].includes(String(attr(node, 'aboveAverage') ?? '1')),
                equalAverage: ['1', 'true'].includes(String(attr(node, 'equalAverage'))),
                stdDev: Number(attr(node, 'stdDev')) || 0,
                formulas: childrenOf(node, 'formula').map((formula) => textContent(formula)),
                format: styles?.dxfs?.[Number(attr(node, 'dxfId'))] || null,
                scale: colorScale ? {
                    stops: childrenOf(colorScale, 'cfvo').map((cfvo) => ({ type: attr(cfvo, 'type'), val: attr(cfvo, 'val') })),
                    colors: childrenOf(colorScale, 'color').map((color) => resolveColor(color, styles?.palette)),
                } : null,
                bar: dataBar ? {
                    stops: childrenOf(dataBar, 'cfvo').map((cfvo) => ({ type: attr(cfvo, 'type'), val: attr(cfvo, 'val') })),
                    color: resolveColor(child(dataBar, 'color'), styles?.palette) || '#638EC6',
                } : null,
                ranges,
            });
        });
    });
    return rules.sort((a, b) => a.priority - b.priority);
}

/* ── Значения правила ── */

function percentile(sorted, p) {
    if (!sorted.length) return 0;
    const rank = Math.min(Math.max(p, 0), 1) * (sorted.length - 1);
    const low = Math.floor(rank);
    const high = Math.ceil(rank);
    return sorted[low] + (sorted[high] - sorted[low]) * (rank - low);
}

function stopValue(stop, sorted, evaluateAt) {
    const min = sorted[0];
    const max = sorted[sorted.length - 1];
    const raw = stop.val;
    const number = () => {
        const direct = Number(raw);
        if (raw !== undefined && Number.isFinite(direct)) return direct;
        const computed = evaluateAt(raw);
        return typeof computed === 'number' ? computed : null;
    };
    switch (stop.type) {
    case 'min': return min;
    case 'max': return max;
    case 'num': case 'formula': return number();
    case 'percent': {
        const p = number();
        return p === null ? null : min + (max - min) * (p / 100);
    }
    case 'percentile': {
        const p = number();
        return p === null ? null : percentile(sorted, p / 100);
    }
    default: return null;
    }
}

function scaleColor(value, points) {
    if (value <= points[0].at) return points[0].color;
    for (let index = 1; index < points.length; index += 1) {
        const prev = points[index - 1];
        const next = points[index];
        if (value <= next.at) {
            const span = next.at - prev.at;
            return span > 0 ? mixColors(prev.color, next.color, (value - prev.at) / span) : next.color;
        }
    }
    return points[points.length - 1].color;
}

/** Применить правила к листу: cellAt(row, col) → { v, err } | undefined; возвращает Map('row:col' → оформление). */
export function applyConditionalRules(rules, { rowCount, colCount, cellAt }) {
    const result = new Map();
    const valueAt = (row, col) => {
        const cell = cellAt(row, col);
        if (!cell) return null;
        if (cell.err) return { error: cell.err };
        return cell.v ?? null;
    };
    const claim = (key, rule, look) => {
        const list = result.get(key) || [];
        list.push({ priority: rule.priority, stop: rule.stopIfTrue, look });
        result.set(key, list);
    };
    for (const rule of rules) {
        const cells = [];
        rule.ranges.forEach((range) => {
            for (let row = range.top; row <= Math.min(range.bottom, rowCount - 1); row += 1) {
                for (let col = range.left; col <= Math.min(range.right, colCount - 1); col += 1) cells.push([row, col]);
            }
        });
        if (!cells.length) continue;
        const anchor = rule.ranges[0];
        const numbers = cells.map(([row, col]) => valueAt(row, col)).filter((value) => typeof value === 'number');
        const sorted = numbers.slice().sort((a, b) => a - b);
        let formulas;
        try {
            formulas = rule.formulas.map((formula) => compileFormula(formula));
        } catch (failure) {
            if (failure instanceof UnsupportedFormula) continue;
            throw failure;
        }
        const evaluateAt = (formulaText, row = anchor.top, col = anchor.left) => {
            try {
                return compileFormula(formulaText)({ value: valueAt, rowOffset: row - anchor.top, colOffset: col - anchor.left });
            } catch {
                return null;
            }
        };
        const formulaValue = (index, row, col) => formulas[index]?.({ value: valueAt, rowOffset: row - anchor.top, colOffset: col - anchor.left });

        if (rule.type === 'colorScale' && rule.scale) {
            if (!sorted.length || rule.scale.colors.some((color) => !color)) continue;
            const points = rule.scale.stops.map((stop, index) => ({ at: stopValue(stop, sorted, evaluateAt), color: rule.scale.colors[index] }));
            if (points.some((point) => point.at === null || !Number.isFinite(point.at))) continue;
            cells.forEach(([row, col]) => {
                const value = valueAt(row, col);
                if (typeof value === 'number') claim(`${row}:${col}`, rule, { fill: scaleColor(value, points) });
            });
            continue;
        }
        if (rule.type === 'dataBar' && rule.bar) {
            if (!sorted.length) continue;
            const low = stopValue(rule.bar.stops[0] || { type: 'min' }, sorted, evaluateAt);
            const high = stopValue(rule.bar.stops[1] || { type: 'max' }, sorted, evaluateAt);
            if (low === null || high === null) continue;
            cells.forEach(([row, col]) => {
                const value = valueAt(row, col);
                if (typeof value !== 'number') return;
                const share = high > low ? (value - low) / (high - low) : 1;
                claim(`${row}:${col}`, rule, { bar: { share: Math.min(1, Math.max(0, share)), color: rule.bar.color } });
            });
            continue;
        }
        if (!rule.format) continue;

        let test = null;
        const average = numbers.length ? numbers.reduce((sum, value) => sum + value, 0) / numbers.length : 0;
        const text = (value) => (value === null || isError(value) ? '' : String(typeof value === 'boolean' ? (value ? 'TRUE' : 'FALSE') : value)).toLowerCase();
        switch (rule.type) {
        case 'cellIs':
            test = (value, row, col) => {
                const a = formulaValue(0, row, col);
                if (isError(value) || isError(a)) return false;
                const cmp = (target) => compareValues(value, target);
                switch (rule.operator) {
                case 'equal': return cmp(a) === 0;
                case 'notEqual': return cmp(a) !== 0;
                case 'greaterThan': return cmp(a) > 0;
                case 'lessThan': return cmp(a) < 0;
                case 'greaterThanOrEqual': return cmp(a) >= 0;
                case 'lessThanOrEqual': return cmp(a) <= 0;
                case 'between': case 'notBetween': {
                    const b = formulaValue(1, row, col);
                    if (isError(b)) return false;
                    const [low, high] = compareValues(a, b) <= 0 ? [a, b] : [b, a];
                    const inside = cmp(low) >= 0 && cmp(high) <= 0;
                    return rule.operator === 'between' ? inside : !inside;
                }
                default: return false;
                }
            };
            break;
        case 'expression':
            test = (value, row, col) => {
                const outcome = toBool(formulaValue(0, row, col));
                return outcome === true;
            };
            break;
        case 'containsText': test = (value) => text(value).includes(String(rule.text).toLowerCase()); break;
        case 'notContainsText': test = (value) => !text(value).includes(String(rule.text).toLowerCase()); break;
        case 'beginsWith': test = (value) => text(value).startsWith(String(rule.text).toLowerCase()); break;
        case 'endsWith': test = (value) => text(value).endsWith(String(rule.text).toLowerCase()); break;
        case 'containsBlanks': test = (value) => value === null || (typeof value === 'string' && !value.trim()); break;
        case 'notContainsBlanks': test = (value) => !(value === null || (typeof value === 'string' && !value.trim())); break;
        case 'containsErrors': test = (value) => isError(value); break;
        case 'notContainsErrors': test = (value) => !isError(value); break;
        case 'aboveAverage':
            if (rule.stdDev || !numbers.length) continue;
            test = (value) => {
                if (typeof value !== 'number') return false;
                if (rule.above) return rule.equalAverage ? value >= average : value > average;
                return rule.equalAverage ? value <= average : value < average;
            };
            break;
        case 'top10': {
            if (!sorted.length) continue;
            const count = rule.percent ? Math.max(1, Math.floor(sorted.length * rule.rank / 100)) : rule.rank;
            const edge = rule.bottom ? sorted[Math.min(count, sorted.length) - 1] : sorted[Math.max(sorted.length - count, 0)];
            test = (value) => typeof value === 'number' && (rule.bottom ? value <= edge : value >= edge);
            break;
        }
        case 'duplicateValues': case 'uniqueValues': {
            const counts = new Map();
            cells.forEach(([row, col]) => {
                const value = valueAt(row, col);
                if (value === null || isError(value)) return;
                const key = text(value);
                counts.set(key, (counts.get(key) || 0) + 1);
            });
            test = (value) => {
                if (value === null || isError(value)) return false;
                const many = (counts.get(text(value)) || 0) > 1;
                return rule.type === 'duplicateValues' ? many : !many;
            };
            break;
        }
        default:
            continue;
        }
        cells.forEach(([row, col]) => {
            let hit = false;
            try {
                hit = test(valueAt(row, col), row, col);
            } catch (failure) {
                if (!(failure instanceof UnsupportedFormula)) throw failure;
            }
            if (hit) claim(`${row}:${col}`, rule, { ...rule.format });
        });
    }

    // Старшее правило побеждает в каждом свойстве; stopIfTrue отсекает младшие.
    const looks = new Map();
    result.forEach((list, key) => {
        const look = {};
        list.sort((a, b) => a.priority - b.priority);
        for (const entry of list) {
            Object.entries(entry.look).forEach(([prop, value]) => {
                if (value !== null && value !== undefined && look[prop] === undefined) look[prop] = value;
            });
            if (entry.stop) break;
        }
        looks.set(key, look);
    });
    return looks;
}

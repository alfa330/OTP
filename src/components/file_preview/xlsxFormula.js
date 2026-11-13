/* Формулы условного форматирования Excel — маленький вычислитель.

   Правило «выделить красным, если =$K3<0» или «если значение больше $D$21»
   хранит формулу, а не готовый результат. Значения ячеек у окна просмотра
   есть, поэтому простые формулы считаем сами: числа, строки, ИСТИНА/ЛОЖЬ,
   ссылки на ячейки этого листа (с $ и без — относительно левой верхней ячейки
   правила), диапазоны внутри функций, арифметика, сравнения и знакомые
   функции. Всё, чего здесь нет (другие листы, имена, массивы, незнакомые
   функции), — UnsupportedFormula: такое правило окно просто не рисует.

   Модуль чистый — проверяется в node (tests/file_preview.test.mjs). */
import { columnIndex } from './xlsxFormat.js';

export class UnsupportedFormula extends Error {}

const error = (code) => ({ error: code });
const isError = (value) => Boolean(value && typeof value === 'object' && 'error' in value);

const TOKEN = /\s*(?:(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)|("(?:[^"]|"")*")|(\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)(?![A-Za-z0-9_(])|([A-Za-z_][A-Za-z0-9_.]*)(\s*\()?|(<>|<=|>=|[-+*/^&=<>(),%]))/y;

function tokenize(source) {
    const tokens = [];
    const text = String(source || '').replace(/^\s*=/, '');
    const pattern = new RegExp(TOKEN.source, 'y');
    let at = 0;
    while (at < text.length) {
        if (/^\s*$/.test(text.slice(at))) break;
        pattern.lastIndex = at;
        const match = pattern.exec(text);
        if (!match) throw new UnsupportedFormula(`не разобрать: ${text.slice(at, at + 12)}`);
        at = pattern.lastIndex;
        const [, number, string, ref, name, call, op] = match;
        if (number !== undefined) tokens.push({ type: 'num', value: Number(number) });
        else if (string !== undefined) tokens.push({ type: 'str', value: string.slice(1, -1).replace(/""/g, '"') });
        else if (ref !== undefined) tokens.push({ type: 'ref', value: ref.toUpperCase() });
        else if (name !== undefined) {
            const upper = name.toUpperCase();
            if (call) tokens.push({ type: 'fn', value: upper });
            else if (upper === 'TRUE' || upper === 'FALSE') tokens.push({ type: 'bool', value: upper === 'TRUE' });
            else throw new UnsupportedFormula(`имя ${name}`);
        } else tokens.push({ type: 'op', value: op });
    }
    return tokens;
}

const parseCellRef = (text) => {
    const match = /^(\$?)([A-Z]{1,3})(\$?)(\d+)$/.exec(text);
    return { colAbs: Boolean(match[1]), col: columnIndex(match[2]), rowAbs: Boolean(match[3]), row: Number(match[4]) - 1 };
};

function parse(tokens) {
    let at = 0;
    const peek = () => tokens[at];
    const take = () => tokens[at++];
    const isOp = (value) => peek()?.type === 'op' && peek().value === value;
    const expect = (value) => {
        if (!isOp(value)) throw new UnsupportedFormula(`ожидалось ${value}`);
        at += 1;
    };
    const binary = (next, ops) => () => {
        let left = next();
        while (peek()?.type === 'op' && ops.includes(peek().value)) {
            const op = take().value;
            left = { kind: 'bin', op, left, right: next() };
        }
        return left;
    };
    let comparison;
    const primary = () => {
        const token = take();
        if (!token) throw new UnsupportedFormula('конец формулы');
        if (token.type === 'num' || token.type === 'str' || token.type === 'bool') return { kind: 'lit', value: token.value };
        if (token.type === 'ref') {
            const [from, to] = token.value.split(':');
            return to ? { kind: 'range', from: parseCellRef(from), to: parseCellRef(to) } : { kind: 'ref', ref: parseCellRef(from) };
        }
        if (token.type === 'fn') {
            const args = [];
            if (!isOp(')')) {
                args.push(comparison());
                while (isOp(',')) {
                    take();
                    args.push(comparison());
                }
            }
            expect(')');
            return { kind: 'call', name: token.value, args };
        }
        if (token.type === 'op' && token.value === '(') {
            const inner = comparison();
            expect(')');
            return inner;
        }
        throw new UnsupportedFormula(`лишний ${token.value}`);
    };
    const postfix = () => {
        let node = primary();
        while (isOp('%')) {
            take();
            node = { kind: 'bin', op: '/', left: node, right: { kind: 'lit', value: 100 } };
        }
        return node;
    };
    const unary = () => {
        if (isOp('-') || isOp('+')) {
            const op = take().value;
            const operand = unary();
            return op === '-' ? { kind: 'neg', operand } : operand;
        }
        return postfix();
    };
    const power = binary(unary, ['^']);
    const multiplicative = binary(power, ['*', '/']);
    const additive = binary(multiplicative, ['+', '-']);
    const concat = binary(additive, ['&']);
    comparison = binary(concat, ['=', '<>', '<', '>', '<=', '>=']);
    const tree = comparison();
    if (at !== tokens.length) throw new UnsupportedFormula('лишнее в конце');
    return tree;
}

/* ── Значения ── */

const toNumber = (value) => {
    if (isError(value)) return value;
    if (value === null || value === undefined || value === '') return 0;
    if (typeof value === 'boolean') return value ? 1 : 0;
    if (typeof value === 'number') return value;
    const number = Number(String(value).replace(',', '.'));
    return Number.isFinite(number) && String(value).trim() !== '' ? number : error('#VALUE!');
};
const toText = (value) => {
    if (value === null || value === undefined) return '';
    if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
    return String(value);
};
const toBool = (value) => {
    if (isError(value)) return value;
    if (typeof value === 'boolean') return value;
    if (value === null || value === undefined || value === '') return false;
    if (typeof value === 'number') return value !== 0;
    const upper = String(value).toUpperCase();
    if (upper === 'TRUE') return true;
    if (upper === 'FALSE') return false;
    return error('#VALUE!');
};
const typeRank = (value) => (typeof value === 'number' ? 0 : typeof value === 'string' ? 1 : 2);

/** Сравнение по правилам Excel: числа < текст < логические, текст без учёта регистра, пусто = 0 / "" / ЛОЖЬ. */
export function compareValues(left, right) {
    let a = left;
    let b = right;
    const blank = (value) => value === null || value === undefined;
    if (blank(a) && blank(b)) return 0;
    if (blank(a)) a = typeof b === 'number' ? 0 : typeof b === 'boolean' ? false : '';
    if (blank(b)) b = typeof a === 'number' ? 0 : typeof a === 'boolean' ? false : '';
    const rankA = typeRank(a);
    const rankB = typeRank(b);
    if (rankA !== rankB) return rankA - rankB;
    if (rankA === 1) {
        const x = a.toLowerCase();
        const y = b.toLowerCase();
        return x < y ? -1 : x > y ? 1 : 0;
    }
    const x = Number(a);
    const y = Number(b);
    return x < y ? -1 : x > y ? 1 : 0;
}

const flatten = (values) => values.flatMap((value) => (Array.isArray(value) ? value : [value]));
const numbersOf = (values, fromRange) => flatten(values).filter((value) => {
    if (fromRange) return typeof value === 'number';
    return value !== null && value !== undefined;
});

function aggregate(name, args) {
    const raw = args.map((arg) => arg.value);
    const firstError = flatten(raw).find(isError);
    if (firstError) return firstError;
    const numbers = args.flatMap((arg) => (arg.range ? numbersOf([arg.value], true)
        : [toNumber(arg.value)])).filter((value) => typeof value === 'number');
    switch (name) {
    case 'SUM': return numbers.reduce((sum, value) => sum + value, 0);
    case 'MIN': return numbers.length ? Math.min(...numbers) : 0;
    case 'MAX': return numbers.length ? Math.max(...numbers) : 0;
    case 'AVERAGE': return numbers.length ? numbers.reduce((sum, value) => sum + value, 0) / numbers.length : error('#DIV/0!');
    case 'COUNT': return numbers.length;
    default: return error('#NAME?');
    }
}

const FUNCTIONS = {
    AND: (args) => {
        const values = flatten(args.map((arg) => arg.value)).filter((value) => value !== null && value !== undefined && value !== '');
        for (const value of values) {
            const bool = toBool(value);
            if (isError(bool)) return bool;
            if (!bool) return false;
        }
        return values.length ? true : error('#VALUE!');
    },
    OR: (args) => {
        const values = flatten(args.map((arg) => arg.value)).filter((value) => value !== null && value !== undefined && value !== '');
        let any = false;
        for (const value of values) {
            const bool = toBool(value);
            if (isError(bool)) return bool;
            any = any || bool;
        }
        return values.length ? any : error('#VALUE!');
    },
    NOT: ([arg]) => {
        const bool = toBool(arg?.value);
        return isError(bool) ? bool : !bool;
    },
    IF: ([test, yes, no]) => {
        const bool = toBool(test?.value);
        if (isError(bool)) return bool;
        if (bool) return yes ? yes.value : true;
        return no ? no.value : false;
    },
    ISBLANK: ([arg]) => arg?.value === null || arg?.value === undefined,
    ISERROR: ([arg]) => isError(arg?.value),
    ISERR: ([arg]) => isError(arg?.value) && arg.value.error !== '#N/A',
    ISNA: ([arg]) => isError(arg?.value) && arg.value.error === '#N/A',
    ISNUMBER: ([arg]) => typeof arg?.value === 'number',
    ISTEXT: ([arg]) => typeof arg?.value === 'string',
    ISLOGICAL: ([arg]) => typeof arg?.value === 'boolean',
    LEN: ([arg]) => (isError(arg?.value) ? arg.value : toText(arg?.value).length),
    TRIM: ([arg]) => (isError(arg?.value) ? arg.value : toText(arg?.value).trim().replace(/\s+/g, ' ')),
    LOWER: ([arg]) => (isError(arg?.value) ? arg.value : toText(arg?.value).toLowerCase()),
    UPPER: ([arg]) => (isError(arg?.value) ? arg.value : toText(arg?.value).toUpperCase()),
    LEFT: ([arg, count]) => toText(arg?.value).slice(0, count ? Number(toNumber(count.value)) : 1),
    RIGHT: ([arg, count]) => {
        const text = toText(arg?.value);
        const n = count ? Number(toNumber(count.value)) : 1;
        return n > 0 ? text.slice(-n) : '';
    },
    ABS: ([arg]) => {
        const n = toNumber(arg?.value);
        return isError(n) ? n : Math.abs(n);
    },
    INT: ([arg]) => {
        const n = toNumber(arg?.value);
        return isError(n) ? n : Math.floor(n);
    },
    ROUND: ([arg, digits]) => {
        const n = toNumber(arg?.value);
        const d = digits ? toNumber(digits.value) : 0;
        if (isError(n)) return n;
        if (isError(d)) return d;
        const factor = 10 ** d;
        return Math.round(n * factor) / factor;
    },
    MOD: ([a, b]) => {
        const x = toNumber(a?.value);
        const y = toNumber(b?.value);
        if (isError(x)) return x;
        if (isError(y)) return y;
        if (y === 0) return error('#DIV/0!');
        return x - y * Math.floor(x / y);
    },
    VALUE: ([arg]) => toNumber(arg?.value),
    SUM: (args) => aggregate('SUM', args),
    MIN: (args) => aggregate('MIN', args),
    MAX: (args) => aggregate('MAX', args),
    AVERAGE: (args) => aggregate('AVERAGE', args),
    COUNT: (args) => aggregate('COUNT', args),
    COUNTA: (args) => flatten(args.map((arg) => arg.value)).filter((value) => value !== null && value !== undefined && value !== '').length,
};

/* ── Вычисление ── */

function evaluate(node, ctx) {
    switch (node.kind) {
    case 'lit': return node.value;
    case 'ref': {
        const { ref } = node;
        const row = ref.row + (ref.rowAbs ? 0 : ctx.rowOffset);
        const col = ref.col + (ref.colAbs ? 0 : ctx.colOffset);
        if (row < 0 || col < 0) return error('#REF!');
        return ctx.value(row, col);
    }
    case 'range': {
        const r1 = node.from.row + (node.from.rowAbs ? 0 : ctx.rowOffset);
        const c1 = node.from.col + (node.from.colAbs ? 0 : ctx.colOffset);
        const r2 = node.to.row + (node.to.rowAbs ? 0 : ctx.rowOffset);
        const c2 = node.to.col + (node.to.colAbs ? 0 : ctx.colOffset);
        const values = [];
        const top = Math.min(r1, r2);
        const bottom = Math.min(Math.max(r1, r2), top + 20000);
        for (let row = top; row <= bottom; row += 1) {
            for (let col = Math.min(c1, c2); col <= Math.max(c1, c2); col += 1) values.push(ctx.value(row, col));
        }
        return values;
    }
    case 'neg': {
        const n = toNumber(evaluate(node.operand, ctx));
        return isError(n) ? n : -n;
    }
    case 'call': {
        const fn = FUNCTIONS[node.name];
        if (!fn) throw new UnsupportedFormula(`функция ${node.name}`);
        const args = node.args.map((arg) => ({ value: evaluate(arg, ctx), range: arg.kind === 'range' }));
        return fn(args);
    }
    case 'bin': {
        const left = evaluate(node.left, ctx);
        const right = evaluate(node.right, ctx);
        if (Array.isArray(left) || Array.isArray(right)) throw new UnsupportedFormula('диапазон вне функции');
        if (isError(left)) return left;
        if (isError(right)) return right;
        if (node.op === '&') return toText(left) + toText(right);
        if (['=', '<>', '<', '>', '<=', '>='].includes(node.op)) {
            const order = compareValues(left, right);
            return { '=': order === 0, '<>': order !== 0, '<': order < 0, '>': order > 0, '<=': order <= 0, '>=': order >= 0 }[node.op];
        }
        const a = toNumber(left);
        const b = toNumber(right);
        if (isError(a)) return a;
        if (isError(b)) return b;
        switch (node.op) {
        case '+': return a + b;
        case '-': return a - b;
        case '*': return a * b;
        case '/': return b === 0 ? error('#DIV/0!') : a / b;
        case '^': return a ** b;
        default: throw new UnsupportedFormula(node.op);
        }
    }
    default: throw new UnsupportedFormula(node.kind);
    }
}

/** Скомпилировать формулу один раз; вычислять — для каждой ячейки правила со своим сдвигом.
 *  value(row, col) → число | строка | логическое | null (пусто) | { error }. */
export function compileFormula(source) {
    const tree = parse(tokenize(source));
    return (ctx) => evaluate(tree, ctx);
}

export { isError, toBool };

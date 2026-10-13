/*
 * Карточка заявки с данными: каким словом назван день перевода и что стоит в блоке
 * «Что осталось до закрытия».
 *
 * Жалоба владельца 13.10.2026: карту сотрудника пополнили — карточка написала
 * «Оплата проведена ✓» и «Оплачено», хотя чека ещё нет (п. 5.3).
 *
 * Проверяется РАЗМЕТКА, а не текст исходника. Серверный рендер эффектов не
 * запускает, поэтому ответ сервера подкладывается в начальное состояние
 * карточки при сборке (единственная подмена — начальное значение `data`).
 * Сборка одним файлом вместе с react — на диск ничего не пишется.
 *
 * Запуск: node --test tests/payments_card_paid_word_render.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const esbuild = require('esbuild');

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const SEED = 'const [data, setData] = useState(null);';

let renderCard;
async function render(data) {
    if (!renderCard) {
        const result = await esbuild.build({
            stdin: {
                contents: `
                    import React from 'react';
                    import { renderToStaticMarkup } from 'react-dom/server';
                    import Card from './src/components/payments/PaymentRequestCard.jsx';
                    export const render = (props) => renderToStaticMarkup(React.createElement(Card, props));
                `,
                resolveDir: ROOT, loader: 'jsx',
            },
            bundle: true, write: false, format: 'cjs', platform: 'node', target: 'node18', jsx: 'transform',
            loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty' },
            logLevel: 'silent',
            plugins: [{
                name: 'seed-card-data',
                setup(build) {
                    build.onResolve({ filter: /^axios$/ }, () => ({ path: 'axios', namespace: 'stub' }));
                    build.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({
                        contents: 'const never = () => new Promise(() => {}); export default { get: never, post: never, patch: never, delete: never };',
                    }));
                    build.onLoad({ filter: /PaymentRequestCard\.jsx$/ }, (args) => {
                        const source = readFileSync(args.path, 'utf8');
                        assert.ok(source.includes(SEED), 'в карточке изменилось начальное состояние data — поправьте подмену');
                        return { contents: source.replace(SEED, 'const [data, setData] = useState(globalThis.__PAYMENT_CARD_DATA__ ?? null);'), loader: 'jsx' };
                    });
                },
            }],
        });
        const module = { exports: {} };
        new Function('module', 'exports', 'require', result.outputFiles[0].text)(module, module.exports, require);
        renderCard = module.exports.render;
    }
    globalThis.__PAYMENT_CARD_DATA__ = data;
    try {
        return renderCard({
            apiBaseUrl: 'http://api.test', headers: () => ({}), dictionaries: {}, users: [], me: { id: 1 }, meta: {},
            showToast: () => {}, onClose: () => {}, open: true, requestId: data.request.id,
        });
    } finally {
        delete globalThis.__PAYMENT_CARD_DATA__;
    }
}

const answer = (request, closing) => ({
    request: {
        id: 471, number: 471, status: 'active', stage: 'payment', request_kind: 'purchase', expense_name: 'Картридж',
        amount: 15000, due_on: '2026-10-20', paid_on: '2026-10-13', paid_amount: 15000, object_type: 'goods',
        initiator_id: 10, initiator_name: 'Инициатор', submitted_at: '2026-10-12T10:00:00', ...request,
    },
    items: [], offers: [], subtasks: [], lifecycle: [], attachments: [], events: [], history: [], duplicates: [],
    assets: [], clarify_reasons: [], submit_problems: [], permissions: {},
    contract_check: { applies: false, ok: true },
    closing,
});

/* Текст карточки построчно: подпись строки («Пополнено») — отдельный узел, значит отдельная строка. */
const lines = (html) => html.replace(/<[^>]+>/g, '\n').split(/\n+/);
/* Разметка пункта блока условий с этой подписью: галочка в нём — это svg. */
const mark = (html, label) => html.split('<li').slice(1).map((chunk) => chunk.split('</li>')[0])
    .find((chunk) => chunk.slice(chunk.indexOf('>') + 1).replace(/<[^>]+>/g, '').trim() === label);

test('карта сотрудника пополнена, чека нет: «Пополнено», а «Оплата проведена» без галочки', async () => {
    const html = await render(answer({ payment_method: 'card', card_recipient: 'employee' }, [
        { label: 'Закуп согласован', ok: true },
        { label: 'Оплата проведена', ok: false },
        { label: 'Товар получен', ok: false },
    ]));
    assert.ok(lines(html).includes('Пополнено'), 'строка дня перевода подписана «Пополнено»');
    assert.ok(!lines(html).includes('Оплачено'), 'перевод на карту оплатой не назван');
    assert.match(html, /пополнено 13\.10/);
    assert.doesNotMatch(html, /оплачено 13\.10/);
    // В блоке условий галочка стоит только у согласования.
    assert.match(mark(html, 'Закуп согласован'), /<svg/);
    assert.doesNotMatch(mark(html, 'Оплата проведена'), /<svg/);
    assert.doesNotMatch(mark(html, 'Товар получен'), /<svg/);
});

test('счёт оплачен: «Оплачено» и галочка у «Оплата проведена»', async () => {
    const html = await render(answer({ payment_method: 'invoice' }, [
        { label: 'Закуп согласован', ok: true },
        { label: 'Оплата проведена', ok: true },
    ]));
    assert.ok(lines(html).includes('Оплачено'));
    assert.ok(!lines(html).includes('Пополнено'));
    assert.match(html, /оплачено 13\.10/);
    assert.match(mark(html, 'Оплата проведена'), /<svg/);
});

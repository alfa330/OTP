import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { buildSync } from 'esbuild';

async function load(name) {
    const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
    mkdirSync(dir, { recursive: true });
    const outfile = join(dir, `taxi-park-${name}.mjs`);
    buildSync({
        entryPoints: [fileURLToPath(new URL(`../src/components/call_qa/${name}.jsx`, import.meta.url))],
        bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
        packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
        logLevel: 'silent',
    });
    return (await import(pathToFileURL(outfile))).default;
}

const TaxiPark = await load('TaxiPark');
const QueueList = await load('QueueList');
const CallReviewCard = await load('CallReviewCard');
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));

test('название парка, неизвестный парк и отдел без поля различаются', () => {
    assert.match(render(TaxiPark, { value: ' Qazaq ' }), /title="Таксопарк: Qazaq"/);
    for (const value of [null, '', '  ']) {
        assert.match(render(TaxiPark, { value }), /не определён/);
    }
    assert.equal(render(TaxiPark, {}), '');
    assert.doesNotMatch(render(TaxiPark, { value: '<script>alert(1)</script>' }), /<script>/);
});

test('парк виден в списке звонков и чатов без сделки CRM', () => {
    const html = render(QueueList, { items: [
        { id: 1, subject: 'call', operator: 'Оператор', taxi_park: 'iTaxi', reasons: [] },
        { id: 1, subject: 'c2d_snapshot', operator: 'Оператор', taxi_park: 'Qazaq', reasons: [] },
        { id: 1, subject: 'wz_episode', operator: 'Оператор', taxi_park: null, reasons: [] },
    ] });
    assert.match(html, /title="Таксопарк: iTaxi"/);
    assert.match(html, /title="Таксопарк: Qazaq"/);
    assert.match(html, /title="Таксопарк: не определён"/);
});

test('карточки звонков и переписки показывают парк независимо от оценки', () => {
    for (const subject_kind of ['call', 'imported_call', 'wz_episode', 'c2d_snapshot']) {
        const html = render(CallReviewCard, {
            call: { id: 1, subject_kind, taxi_park: 'Jana такси', operator: 'Оператор',
                    criteria: [], transcript: [], ai_score: 80 },
            onSave() {}, onSkip() {},
        });
        assert.match(html, /title="Таксопарк: Jana такси"/, subject_kind);
    }
});

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    LOGO_MAX_BYTES, LOGO_SIDE, fitLogo, logoIssue, shrinkLogo,
} from '../src/components/wiki/parkLogo.js';
import { absoluteFileUrl } from '../src/components/wiki/fileUrls.js';

const BASE = 'https://otp-2-fos4.onrender.com/api/wiki';

test('картинка нужного вида и веса проходит', () => {
    assert.equal(logoIssue({ type: 'image/png', size: 200 * 1024 }), null);
    assert.equal(logoIssue({ type: 'image/webp', size: 10 }), null);
});

test('SVG и документы отклоняются формой, а не сервером', () => {
    // Те же типы, что в wiki/routes_parks.py: расхождение читалось бы как
    // «форма приняла, а сервер отказал», то есть как поломка.
    for (const type of ['image/svg+xml', 'application/pdf', 'image/gif', '']) {
        assert.ok(logoIssue({ type, size: 10 }), type);
    }
});

test('файл тяжелее предела отклоняется с понятной причиной', () => {
    const issue = logoIssue({ type: 'image/png', size: LOGO_MAX_BYTES + 1 });
    assert.match(issue, /больше 5 МБ/);
});

test('пустой выбор — это причина, а не молчание', () => {
    assert.ok(logoIssue(null));
});

test('большая картинка ужимается по длинной стороне, пропорции сохраняются', () => {
    assert.deepEqual(fitLogo(2048, 1024), { width: LOGO_SIDE, height: LOGO_SIDE / 2 });
    assert.deepEqual(fitLogo(1000, 4000), { width: 128, height: LOGO_SIDE });
});

test('маленькая картинка не растягивается', () => {
    // Растянуть — значит показать замыленный логотип там, где был чёткий.
    assert.deepEqual(fitLogo(96, 64), { width: 96, height: 64 });
});

test('без canvas загрузка не срывается, а идёт исходником', async () => {
    // Уменьшение здесь ускорение, а не условие загрузки: в node
    // createImageBitmap нет, и функция обязана вернуть файл как есть.
    const file = { name: 'logo.png', type: 'image/png', size: 4096 };
    assert.deepEqual(await shrinkLogo(file), { blob: file, name: 'logo.png' });
});

test('адрес логотипа раскрывается до домена API', () => {
    // Дефект, который это закрывает: фронт отдаётся с Pages, API живёт на
    // Render, и относительный адрес браузер искал бы на домене страницы —
    // в рельсе парков у всех стояла бы битая картинка.
    assert.equal(
        absoluteFileUrl('/api/wiki/file/1ebdec21-42f3-4413-827b-6c6180da7317', BASE),
        'https://otp-2-fos4.onrender.com/api/wiki/file/1ebdec21-42f3-4413-827b-6c6180da7317');
});

test('пустой логотип остаётся пустым, чужой адрес не трогается', () => {
    assert.equal(absoluteFileUrl(null, BASE), null);
    assert.equal(absoluteFileUrl('https://cdn.example/logo.png', BASE),
                 'https://cdn.example/logo.png');
});

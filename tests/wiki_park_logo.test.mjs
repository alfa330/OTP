import assert from 'node:assert/strict';
import test from 'node:test';

import {
    LOGO_MAX_BYTES, LOGO_SIDE, ZOOM_MAX, fitLogo, frameLayout, frameStyle,
    logoIssue, makeFrame, normalizeFrame, panFrame, shrinkLogo, zoomFrame,
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
    // Перевод в WebP здесь экономия, а не условие загрузки: в node
    // createImageBitmap нет, и функция обязана вернуть файл как есть.
    const file = { name: 'logo.png', type: 'image/png', size: 4096 };
    assert.deepEqual(await shrinkLogo(file), { blob: file, name: 'logo.png', ratio: null });
});

/* ─── Ракурс ─────────────────────────────────────────────────────────────
   Плитка квадратная, а вывеска парка широкая: браузер по object-cover брал
   середину — то есть кусок фона между словами. Эти правила и есть выбор
   видимой части, и проверять их мышкой по кругу нельзя. */

test('без ракурса картинка вписана по короткой стороне и стоит по центру', () => {
    // Широкая картинка 2:1: по ширине она вдвое больше плитки, и лишнее
    // срезано поровну с двух сторон.
    assert.deepEqual(frameLayout(makeFrame(2)), { width: 200, height: 100, left: -50, top: -0 });
    // Высокая 1:2 — то же самое, только сверху и снизу.
    assert.deepEqual(frameLayout(makeFrame(0.5)), { width: 100, height: 200, left: -0, top: -50 });
});

test('приближение растит картинку, а не плитку', () => {
    const frame = zoomFrame(makeFrame(1), 2);
    assert.deepEqual(frameLayout(frame), { width: 200, height: 200, left: -50, top: -50 });
});

test('картинку тянут, а не рамку', () => {
    // Потянули вправо на 40 px в окошке 176 px: показалось то, что было левее,
    // то есть срезанного слева стало меньше.
    const moved = panFrame(makeFrame(2), 40, 0, 176);
    assert.ok(moved.x < 0.5);
    assert.equal(panFrame(moved, -40, 0, 176).x.toFixed(4), (0.5).toFixed(4),
                 'обратный ход возвращает ровно туда же');
});

test('кадр не уезжает за край картинки', () => {
    // Иначе с краю плитки появилось бы пустое поле — «сломанный логотип».
    assert.equal(panFrame(makeFrame(2), 10_000, 0, 176).x, 0);
    assert.equal(panFrame(makeFrame(2), -10_000, 0, 176).x, 1);
    // По короткой стороне двигать нечего: лишнего там нет.
    assert.equal(panFrame(makeFrame(2), 0, 50, 176).y, 0.5);
});

test('увеличение зажато сверху: в плитке 38 px дальше видна одна буква', () => {
    assert.equal(zoomFrame(makeFrame(1), 99).zoom, ZOOM_MAX);
    assert.equal(zoomFrame(makeFrame(1), 0.1).zoom, 1);
});

test('мусор вместо ракурса — это его отсутствие, а не поломка плитки', () => {
    for (const value of [null, undefined, 'ракурс', 42]) {
        assert.equal(normalizeFrame(value), null, String(value));
        assert.equal(frameStyle(value), null, String(value));
    }
});

test('стиль снимает max-width, иначе увеличение не работает вовсе', () => {
    // Tailwind ставит картинкам max-width: 100% — ширина в процентах молча
    // обрезалась бы по плитке.
    const style = frameStyle(makeFrame(2, { zoom: 1.5, x: 0.2, y: 0.5 }));
    assert.equal(style.maxWidth, 'none');
    assert.equal(style.position, 'absolute');
    assert.equal(style.width, '300%');
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

/*
 * Панель отдела продаж «Ограничителя Перезвона» (OktellGuardPhonePanel.jsx)
 * прогоняется через react-dom/server: браузера в тестах проекта нет, а
 * серверный рендер закрывает главное — панель рисуется на ответе сервера по
 * контракту (DESIGN §1.3) и у СВ (can_manage=false) не оставляет ни одного
 * живого контрола правила.
 *
 * Компонент собирается esbuild-ом вместе со своими модулями (ios.jsx и хуки),
 * React и lucide-react остаются внешними — их node берёт из node_modules.
 * Собранный файл лежит внутри проекта: из системной временной папки
 * `import 'react'` не разрешается (node ищет node_modules вверх от файла).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');
const { mkdirSync } = require('node:fs');
const { join } = require('node:path');
const { pathToFileURL } = require('node:url');

const CACHE = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(CACHE, { recursive: true });
const OUT = join(CACHE, 'OktellGuardPhonePanel.mjs');

buildSync({
    entryPoints: [join(process.cwd(), 'src', 'components', 'oktell_guard', 'OktellGuardPhonePanel.jsx')],
    bundle: true,
    format: 'esm',
    platform: 'node',
    target: 'node18',
    outfile: OUT,
    jsx: 'transform',
    loader: { '.js': 'jsx' },
    external: ['react', 'react-dom', 'react-dom/*', 'lucide-react'],
    logLevel: 'silent',
});

const OktellGuardPhonePanel = (await import(pathToFileURL(OUT).href)).default;

const SETTINGS = {
    department: 'op',
    departments: [{ code: 'szov', name: 'СЗоВ' }, { code: 'op', name: 'Отдел продаж' }],
    phone_settings: {
        enabled: true, threshold_s: 300, warn_before_s: 60, groups: ['yar', 'potok'],
        updated_at: 'Mon, 05 Oct 2026 09:30:00 GMT', updated_by_name: 'Глава ОП',
    },
    can_manage: false,
    group_labels: { osnova: 'Основа', yar: 'ЯР', potok: 'Поток' },
    idle_groups: ['yar', 'potok'],
};

const EMPLOYEES = [
    {
        id: 2, name: 'Ерлан Яров', role: 'operator', sip_number: '6202', department_name: 'Отдел продаж',
        status_group: 'yar', group_label: 'ЯР', group_name: 'ЯР-2', participates: true,
        kicks_30d: 3, last_kick_at: 'Mon, 05 Oct 2026 10:07:00 GMT',
    },
    {
        id: 1, name: 'Айгуль Основа', role: 'operator', sip_number: '6101', department_name: 'Отдел продаж',
        status_group: 'osnova', group_label: 'Основа', group_name: 'Основа', participates: false,
        kicks_30d: 0, last_kick_at: null,
    },
];

const render = (props) => renderToStaticMarkup(React.createElement(OktellGuardPhonePanel, {
    request: async () => ({}),
    toast: () => {},
    onTabChange: () => {},
    initialSettings: SETTINGS,
    initialEmployees: EMPLOYEES,
    ...props,
}));

test('«Сотрудники»: группа, участие, выбросы и цифры шапки', () => {
    const html = render({ tab: 'employees' });
    assert.ok(html.includes('Ерлан Яров'));
    assert.ok(html.includes('SIP 6202'));
    assert.ok(html.includes('ЯР-2'), 'название группы рядом с её видом');
    assert.ok(html.includes('участвует'));
    assert.ok(html.includes('не участвует'), 'Основа под правило не попадает');
    assert.ok(html.includes('ЯР и Поток: «Офлайн» после 5 мин без звонков в «Исходе»'));
    assert.ok(html.includes('1 из 2'), 'под правилом — по флагу participates');
    assert.ok(html.includes('последний 05.10'), 'время последнего выброса — местное');
    // На узком экране правых колонок нет — участие и последний выброс должны
    // стоять и во второй строке под именем (sm:hidden), у каждого сотрудника.
    assert.equal((html.match(/<span class="sm:hidden">/g) || []).length, 2 + 1);
    assert.equal((html.match(/последний 05\.10/g) || []).length, 2);
    assert.equal((html.match(/не участвует/g) || []).length, 2);
    // Ничего из Oktell: ни агента, ни личных порогов.
    assert.ok(!html.includes('Скачать агента'));
    assert.ok(!html.includes('type="checkbox"'), 'выделять людей у ОП незачем — массовой правки нет');
});

test('«Общие» у СВ: всё погашено и объяснено', () => {
    const html = render({ tab: 'common' });
    assert.ok(html.includes('Раздел открыт вам на просмотр.'));
    // Тумблер, пять порогов, поле предупреждения и две галочки групп.
    const disabled = (html.match(/disabled=""/g) || []).length;
    assert.equal(disabled, 1 + 5 + 1 + 2);
    assert.ok(html.includes('value="60"'));
    assert.ok(html.includes('max="270"'), 'предупреждение не раньше полуминуты простоя');
    assert.ok(html.includes('Глава ОП'));
});

test('«Общие» у главы: контролы живые', () => {
    const html = render({ tab: 'common', initialSettings: { ...SETTINGS, can_manage: true } });
    assert.ok(!html.includes('Раздел открыт вам на просмотр.'));
    assert.equal((html.match(/disabled=""/g) || []).length, 0);
});

test('«Отчёт» без строк говорит словами, а не пустой таблицей', () => {
    const html = render({ tab: 'report' });
    assert.ok(html.includes('Кого и когда выкинуло в «Офлайн»'));
    assert.ok(html.includes('За выбранные дни никого не выкидывало.'));
    assert.ok(html.includes('Всего за период: 0.'));
});

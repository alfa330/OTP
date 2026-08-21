import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

import {
    SPACE_TABS, FEATURE_KEYS, spaceFeatures, featureOn, effectiveFeatures,
} from '../src/components/wiki/spaceFeatures.js';

test('пустой набор = всё включено', () => {
    // Пространство, заведённое до появления тумблеров, несёт пустой features.
    // Любая другая трактовка пустоты означала бы «вика выключена целиком» —
    // и выкат отобрал бы раздел у всех разом.
    const flags = spaceFeatures({});
    assert.equal(FEATURE_KEYS.every((key) => flags[key] === true), true);
    assert.deepEqual(spaceFeatures(null), flags);
    assert.deepEqual(spaceFeatures(undefined), flags);
});

test('выключенным считается только явный false', () => {
    // Ключ мог прийти из будущей версии схемы или из битой записи; всё, что
    // не false, — включено. Иначе опечатка в JSONB гасила бы вкладку молча.
    const flags = spaceFeatures({ parks: false, offices: 0, audit: null, assistant: 'нет' });
    assert.equal(flags.parks, false);
    assert.equal(flags.offices, true);
    assert.equal(flags.audit, true);
    assert.equal(flags.assistant, true);
});

test('нет пространства — считаем включённым', () => {
    // Пока ping не ответил, пространства нет вовсе, и шапка обязана выглядеть
    // как обычно, а не мигать пустым рядом вкладок.
    assert.equal(featureOn(null, 'parks'), true);
    assert.equal(featureOn({ features: { parks: false } }, 'parks'), false);
});

test('выключенная вкладка гасит свои половины', () => {
    // Половина, оставшаяся включённой под выключенной вкладкой, однажды
    // всплывёт в другом месте — в фильтре типов, в подборке, в счётчике.
    const flags = effectiveFeatures({ features: { catalog: false } });
    assert.equal(flags.catalog, false);
    assert.equal(flags.catalog_articles, false);
    assert.equal(flags.catalog_structure, false);
    assert.equal(flags.catalog_trainers, false);
});

test('«Главная» не выключается, но её половины — да', () => {
    // Витрина статей — единственный экран, куда ведут все ссылки раздела.
    const home = SPACE_TABS.find((tab) => tab.key === 'library');
    assert.equal(home.locked, true);
    assert.equal(FEATURE_KEYS.includes('library'), false);

    const flags = effectiveFeatures({ features: { library_park_rail: false } });
    assert.equal(flags.library_park_rail, false);
});

test('ключи совпадают с SPACE_FEATURES на сервере', () => {
    // Разъехавшись, списки дают тумблер, выключающий то, чего сервер не знает,
    // и наоборот — вкладку, которую сервер уже считает выключенной.
    const python = readFileSync(new URL('../wiki/schema.py', import.meta.url), 'utf8');
    const block = /SPACE_FEATURES = \(([\s\S]*?)\)\n/.exec(python);
    assert.ok(block, 'SPACE_FEATURES не найден в wiki/schema.py');
    const serverKeys = [...block[1].matchAll(/'([a-z_]+)'/g)].map((m) => m[1]);
    assert.deepEqual([...FEATURE_KEYS].sort(), [...serverKeys].sort());
});

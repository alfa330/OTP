import assert from 'node:assert/strict';
import test from 'node:test';

import { auditFacts, auditRest } from '../src/components/wiki/auditEvents.js';

/* Рубильник помощника хранится ОТКАЗОМ (ai_opt_out), а журнал подписывает поле
 * словами об участии. Обычное «да» на true прочиталось бы ровно наоборот:
 * «участие в помощнике: да» у раздела, который от помощника закрыли. */

test('закрытие раздела читается как выключенное участие', () => {
    assert.deepEqual(auditFacts({ action: 'section.update', details: { ai_opt_out: true } }),
        ['участие в помощнике: выключено']);
});

test('открытие — как включённое', () => {
    assert.deepEqual(auditFacts({ action: 'section.update', details: { ai_opt_out: false } }),
        ['участие в помощнике: включено']);
});

test('новый закрытый раздел говорит об этом в подробностях', () => {
    const item = {
        action: 'section.create',
        details: { name: 'Архив', space_id: 11, visibility_scope: 'restricted',
                   department_id: null, ai_opt_out: true },
    };
    assert.deepEqual(auditRest(item), [['участие в помощнике', 'выключено']]);
});

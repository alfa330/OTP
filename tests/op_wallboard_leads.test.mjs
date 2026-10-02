import test from 'node:test';
import assert from 'node:assert/strict';

import { OP_LEAD_SYNC_WARN_SECONDS, opLeadSyncStaleSeconds } from '../src/components/monitoring/opWallboardLeads.js';

/*
 * Табло ОП, «Принятие лида в работу». Список сделок приходит из amoCRM раз в 3 минуты; предупреждение
 * о замершем списке — только после пяти пропусков подряд. Порог проверяется исполнением: перевёрнутое
 * сравнение повесило бы янтарный чип на стену навсегда, а тест по строкам исходника этого не видит.
 */

const at = (age) => ({ lead_speed: { synced_age_seconds: age } });

test('свежий список и неизвестный возраст — без предупреждения', () => {
    assert.equal(OP_LEAD_SYNC_WARN_SECONDS, 900);
    assert.equal(opLeadSyncStaleSeconds(at(0)), null);
    assert.equal(opLeadSyncStaleSeconds(at(400)), null);
    assert.equal(opLeadSyncStaleSeconds(at(900)), null);
    assert.equal(opLeadSyncStaleSeconds(at(null)), null);
    assert.equal(opLeadSyncStaleSeconds({ lead_speed: null }), null);
    assert.equal(opLeadSyncStaleSeconds(null), null);
});

test('пять пропусков подряд — предупреждение с возрастом списка', () => {
    assert.equal(opLeadSyncStaleSeconds(at(901)), 901);
    assert.equal(opLeadSyncStaleSeconds(at('3900')), 3900);
});

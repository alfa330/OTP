import test from 'node:test';
import assert from 'node:assert/strict';
import { calculateTezOpMonthlyPlan as plan, calculateTezOpSalary } from '../src/utils/salaryFormula.js';

const settings = { planPerFte: 200, normHoursFte: 160, month: '2026-10', hireDate: '2025-01-01' };
test('план пропорционален факту, личная ставка и норма не подменяют норму FTE', () => {
  for (const [hours, target] of [[0, 0], [40, 50], [80, 100], [160, 200], [200, 250]]) {
    for (const rate of [0.5, 0.75, 1]) {
      assert.equal(plan({ ...settings, factHours: hours, rate, normHours: 160 * rate }).plan, target);
    }
  }
});
test('новичку весь месяц ×0,8, включая неполный месяц и переработку', () => {
  for (const hireDate of ['2026-10-01', '2026-10-15', '2026-10-31']) {
    for (const factHours of [0, 40, 200]) {
      const result = plan({ ...settings, hireDate, factHours });
      assert.equal(result.plan, factHours);
      assert.equal(result.isNewbie, true);
    }
  }
  assert.equal(plan({ ...settings, hireDate: '2026-09-30', factHours: 80 }).plan, 100);
  assert.equal(plan({ ...settings, factHours: 80, newbie: true }).plan, 80);
});
test('незаданные параметры и будущий приём не дают фиктивного плана', () => {
  for (const normHoursFte of [0, -10, '', Infinity, NaN]) {
    assert.equal(plan({ ...settings, normHoursFte, factHours: 80 }).plan, null);
  }
  assert.equal(plan({ ...settings, planPerFte: 0 }).plan, null);
  assert.equal(plan({ ...settings, hireDate: '2026-11-01' }).plan, null);
  assert.equal(plan({ ...settings, factHours: -10 }).plan, 0);
});
test('старый месяц без настройки сохраняет календарную норму', () => {
  assert.equal(plan({ ...settings, normHoursFte: null, month: '2026-09', factHours: 168 }).plan, 200);
  assert.equal(plan({ ...settings, month: '2026-09', factHours: 80 }).plan, 100);
});
test('ЗП использует новый индивидуальный план без промежуточного округления', () => {
  const individual = plan({ ...settings, factHours: 81.25 });
  assert.equal(individual.plan, 101.5625);
  const salary = calculateTezOpSalary({ hoursWorked: 81.25, hoursNorm: 160, planTarget: individual.plan, planFact: 100 });
  assert.equal(salary.planTarget, 101.5625);
  assert.ok(Math.abs(salary.dealPercent - 100 / 101.5625) < 1e-8);
  assert.equal(salary.bonusDeals, salary.oklad * salary.dealPercent);
});

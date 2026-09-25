import test from 'node:test';
import assert from 'node:assert/strict';

import { TRAINERS } from '../src/components/wiki/trainers/registry.js';
import { PHONE_HIDDEN_TRAINERS, phoneTrainers } from '../src/trainers_embed/trainerList.js';

/* Страница тренажёров в iCORE Phone показывает не весь реестр вики: владелец
   (25.09.2026) убрал тренажёры рабочего места и фотоконтроль. Тест держит два
   инварианта: скрытые ключи существуют в реестре (иначе список скрывает то, чего
   нет, а настоящий тренажёр под другим ключом остался на виду), и в телефоне
   остаются только тренажёры приложений водителя. */

test('скрытые ключи — настоящие тренажёры реестра', () => {
  const keys = new Set(TRAINERS.map((t) => t.key));
  for (const hidden of PHONE_HIDDEN_TRAINERS) {
    assert.ok(keys.has(hidden), `в реестре нет тренажёра ${hidden}`);
  }
});

test('в телефоне остаются тренажёры приложений водителя, порядок реестра сохранён', () => {
  const shown = phoneTrainers(TRAINERS);
  assert.deepEqual(shown.map((t) => t.key), ['yandex-pro-edo-provider', 'taxi-pro-avr', 'sapar-site-avr']);
  const titles = shown.map((t) => t.title);
  for (const removed of ['Рабочее место оператора', 'Звонок водителя', 'Фотоконтроль машины']) {
    assert.ok(!titles.includes(removed), `«${removed}» не должен показываться в телефоне`);
  }
  // Тренажёры за компьютером в телефоне не показываем вовсе.
  assert.ok(shown.every((t) => t.stage !== 'desktop' && t.stage !== 'world'));
});

test('пустой или отсутствующий список не ломает страницу', () => {
  assert.deepEqual(phoneTrainers([]), []);
  assert.deepEqual(phoneTrainers(undefined), []);
  assert.deepEqual(phoneTrainers([null, { key: 'taxi-pro-avr' }]).map((t) => t.key), ['taxi-pro-avr']);
});

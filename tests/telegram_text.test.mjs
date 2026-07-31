import test from 'node:test';
import assert from 'node:assert/strict';

import {
  decodeEntities,
  parseTelegramText,
  stripTelegramTags,
} from '../src/components/technical/telegramText.js';

test('обычный текст остаётся текстом', () => {
  assert.deepEqual(parseTelegramText('нет звука на РМ 12'), ['нет звука на РМ 12']);
});

test('жирный разбирается в узел', () => {
  assert.deepEqual(parseTelegramText('<b>Что:</b> нет звука'), [
    { tag: 'b', children: ['Что:'] },
    ' нет звука',
  ]);
});

test('синонимы сводятся к одному тегу', () => {
  assert.equal(parseTelegramText('<strong>x</strong>')[0].tag, 'b');
  assert.equal(parseTelegramText('<em>x</em>')[0].tag, 'i');
  assert.equal(parseTelegramText('<del>x</del>')[0].tag, 's');
});

test('вложенная разметка сохраняет структуру', () => {
  assert.deepEqual(parseTelegramText('<b>жирный <i>и курсив</i></b>'), [
    { tag: 'b', children: ['жирный ', { tag: 'i', children: ['и курсив'] }] },
  ]);
});

test('реальная строка тикета разбирается целиком', () => {
  const raw = '🔧 <b>Что:</b> нет звука\n📍 <b>Где:</b> РМ 12';
  // текст, метка, текст, метка, текст
  assert.deepEqual(parseTelegramText(raw), [
    '🔧 ',
    { tag: 'b', children: ['Что:'] },
    ' нет звука\n📍 ',
    { tag: 'b', children: ['Где:'] },
    ' РМ 12',
  ]);
  assert.equal(stripTelegramTags(raw), '🔧 Что: нет звука\n📍 Где: РМ 12');
});

test('код внутри тикета не ломает разбор', () => {
  assert.deepEqual(parseTelegramText('ошибка <code>DNS_PROBE_FINISHED</code> на РМ 67'), [
    'ошибка ',
    { tag: 'code', children: ['DNS_PROBE_FINISHED'] },
    ' на РМ 67',
  ]);
});

test('неизвестный тег остаётся видимым текстом и не исполняется', () => {
  // Главное: script не превращается в узел дерева — UI отрисует его как текст.
  const nodes = parseTelegramText('<script>alert(1)</script>');
  assert.deepEqual(nodes, ['<script>alert(1)</script>']);
  assert.ok(nodes.every((n) => typeof n === 'string'));
});

test('img с onerror тоже остаётся текстом', () => {
  const nodes = parseTelegramText('<img src=x onerror="alert(1)">');
  assert.deepEqual(nodes, ['<img src=x onerror="alert(1)">']);
});

test('лишний закрывающий тег не роняет разбор', () => {
  // Текст остаётся двумя соседними кусками — на отрисовку это не влияет,
  // важно, что ничего не потерялось и дерево не схлопнулось.
  const nodes = parseTelegramText('текст</b> ещё');
  assert.ok(nodes.every((n) => typeof n === 'string'));
  assert.equal(stripTelegramTags('текст</b> ещё'), 'текст ещё');
});

test('незакрытый тег не теряет содержимое', () => {
  assert.deepEqual(parseTelegramText('<b>хвост без закрытия'), [
    { tag: 'b', children: ['хвост без закрытия'] },
  ]);
});

test('сущности разворачиваются, амперсанд последним', () => {
  assert.equal(decodeEntities('a &lt;b&gt; &amp; c'), 'a <b> & c');
  assert.equal(decodeEntities('&amp;lt;'), '&lt;');
});

test('пустой и пропущенный ввод безопасны', () => {
  assert.deepEqual(parseTelegramText(''), []);
  assert.deepEqual(parseTelegramText(null), []);
  assert.deepEqual(parseTelegramText(undefined), []);
  assert.equal(stripTelegramTags(''), '');
});

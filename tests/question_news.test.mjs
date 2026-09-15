// «Опубликовать как новость» (задача #321): черновик новости из разобранного вопроса.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    NEWS_TITLE_MAX, answerToHtml, newsDraftFromQuestion, newsTitleFromQuestion,
} from '../src/components/wiki/questionNews.js';

test('ответ становится абзацами, одиночный перенос — переносом', () => {
    assert.equal(answerToHtml('Первая строка\nвторая\n\nНовый абзац'),
                 '<p>Первая строка<br>вторая</p><p>Новый абзац</p>');
});

test('разметка из ответа экранируется, а не исполняется', () => {
    assert.equal(answerToHtml('<script>alert(1)</script> & "кавычки"'),
                 '<p>&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;кавычки&quot;</p>');
});

test('пустой ответ — пустой текст, а не пустой абзац', () => {
    assert.equal(answerToHtml('  \n\n '), '');
    assert.equal(answerToHtml(null), '');
});

test('заголовок — вопрос в одну строку и не длиннее предела сервера', () => {
    assert.equal(newsTitleFromQuestion('  Какая   комиссия\nЯндекса? '), 'Какая комиссия Яндекса?');
    const long = newsTitleFromQuestion('а'.repeat(400));
    assert.equal(long.length, NEWS_TITLE_MAX);
    assert.ok(long.endsWith('…'));
});

test('адресат — отдел оператора с подписью для чипа', () => {
    const draft = newsDraftFromQuestion({
        question: 'Комиссия?', answer: '3%', department_id: 560, department_name: 'Тез КЦ',
    });
    assert.deepEqual(draft, {
        title: 'Комиссия?',
        body: '<p>3%</p>',
        audience: [{ subject_type: 'department', subject_id: 560, subject_role: null,
                     subject_name: 'Тез КЦ' }],
    });
});

test('без отдела адресата не угадываем', () => {
    assert.deepEqual(newsDraftFromQuestion({ question: 'Вопрос', answer: 'Ответ' }).audience, []);
});

// Правила формы теста во вкладке «Вопросы» (задача #321): черновик ИИ →
// форма, причина «почему нельзя публиковать», удаление варианта.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    QUIZ_MAX_QUESTIONS, QUIZ_MIN_QUESTIONS, dropOption, emptyQuestion, quizForForm, quizProblem,
} from '../src/components/wiki/questionQuiz.js';

const good = () => ([
    { prompt: 'Срок акции?', options: ['7 дней', '14 дней', '30 дней'], correct: 1 },
    { prompt: 'Кому положено?', options: ['Новичкам', 'Всем'], correct: 0 },
]);

test('годный тест публикуется', () => {
    assert.equal(quizProblem(good()), null);
});

test('пустой черновик ИИ добивается пустыми вопросами до минимума', () => {
    const form = quizForForm([]);
    assert.equal(form.length, QUIZ_MIN_QUESTIONS);
    assert.deepEqual(form[0], emptyQuestion());
    assert.equal(quizProblem(form), 'В вопросе 1 нет текста');
});

test('лишние вопросы черновика отрезаются по потолку', () => {
    const many = Array.from({ length: QUIZ_MAX_QUESTIONS + 3 },
                            (_, i) => ({ prompt: `В${i}`, options: ['а', 'б'], correct: 0 }));
    assert.equal(quizForForm(many).length, QUIZ_MAX_QUESTIONS);
});

test('отметка верного вне диапазона в форме не сохраняется', () => {
    const [first] = quizForForm([{ prompt: 'В', options: ['а', 'б'], correct: 5 }]);
    assert.equal(first.correct, null);
});

test('причина называет номер вопроса', () => {
    const quiz = good();
    quiz[1].correct = null;
    assert.equal(quizProblem(quiz), 'В вопросе 2 не отмечен верный вариант');
    quiz[1].correct = 0;
    quiz[1].options = ['Всем', ' всем '];
    assert.equal(quizProblem(quiz), 'В вопросе 2 варианты повторяются');
});

test('один вопрос — законный тест, лишние — нет', () => {
    assert.equal(quizProblem(good().slice(0, 1)), null);
    assert.equal(quizProblem([...good(), ...good(), ...good(), ...good(), ...good(), ...good()]),
                 'В тесте должно быть от 1 до 10 вопросов');
});

test('удаление варианта сдвигает отметку верного, а удалённый верный её снимает', () => {
    const item = { prompt: 'В', options: ['а', 'б', 'в'], correct: 2 };
    assert.deepEqual(dropOption(item, 0), { prompt: 'В', options: ['б', 'в'], correct: 1 });
    assert.equal(dropOption(item, 2).correct, null);
    assert.equal(dropOption({ ...item, correct: 0 }, 1).correct, 0);
});

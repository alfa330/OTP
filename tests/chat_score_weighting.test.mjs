import test from 'node:test';
import assert from 'node:assert/strict';

import {
    calculateWeightedChatAverage,
    getChatScoreContribution,
} from '../src/utils/chatScore.js';


test('chat average is weighted by rating count', () => {
    const result = calculateWeightedChatAverage([
        { avg_score: 5, score_sum: 5, score_count: 1 },
        { avg_score: 4, score_sum: 36, score_count: 9 },
    ]);

    assert.equal(result, 4.1);
    assert.notEqual(result, 4.5);
});


test('legacy average-only rows keep backend-compatible weight one', () => {
    const contribution = getChatScoreContribution({ avg_score: 4.75 });
    assert.deepEqual(contribution, { sum: 4.75, count: 1 });
    assert.equal(calculateWeightedChatAverage([{ avg_score: 4.75 }]), 4.75);

    const nullSum = getChatScoreContribution({
        score_sum: null,
        score_count: 9,
        avg_score: 4,
    });
    assert.deepEqual(nullSum, { sum: 4, count: 1 });
});


test('empty or invalid rows do not create a score', () => {
    assert.equal(calculateWeightedChatAverage([null, {}, { avg_score: 0 }]), null);
});

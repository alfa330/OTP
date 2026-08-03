export const getChatScoreContribution = (chatMetrics) => {
    if (!chatMetrics || typeof chatMetrics !== 'object') return { sum: 0, count: 0 };

    const rawScoreSum = chatMetrics.score_sum;
    const rawScoreCount = chatMetrics.score_count;
    const hasScoreSum = rawScoreSum !== null && rawScoreSum !== undefined && String(rawScoreSum).trim() !== '';
    const hasScoreCount = rawScoreCount !== null && rawScoreCount !== undefined && String(rawScoreCount).trim() !== '';
    const scoreSum = hasScoreSum ? Number(rawScoreSum) : Number.NaN;
    const scoreCount = hasScoreCount ? Number(rawScoreCount) : Number.NaN;
    if (Number.isFinite(scoreSum) && Number.isFinite(scoreCount) && scoreCount > 0) {
        return { sum: scoreSum, count: scoreCount };
    }

    // Совместимость с историческими строками без score_sum/score_count.
    const legacyAverage = Number(chatMetrics.avg_score);
    return Number.isFinite(legacyAverage) && legacyAverage > 0
        ? { sum: legacyAverage, count: 1 }
        : { sum: 0, count: 0 };
};

export const calculateWeightedChatAverage = (metricsRows) => {
    let scoreSum = 0;
    let scoreCount = 0;
    for (const chatMetrics of (Array.isArray(metricsRows) ? metricsRows : [])) {
        const contribution = getChatScoreContribution(chatMetrics);
        scoreSum += contribution.sum;
        scoreCount += contribution.count;
    }
    return scoreCount > 0 ? scoreSum / scoreCount : null;
};

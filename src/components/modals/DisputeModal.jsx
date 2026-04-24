import React, { useEffect, useRef } from 'react';

const MAX_DISPUTE_CHARS = 500;

const DisputeModal = ({
  selectedEvaluation,
  disputeText,
  setDisputeText,
  handleSubmitDispute,
  isLoading,
  setShowDisputeModal,
}) => {
  const textareaRef = useRef(null);
  const textValue = String(disputeText || '');
  const charCount = textValue.length;
  const canSubmit = !isLoading && textValue.trim().length > 0 && charCount <= MAX_DISPUTE_CHARS;

  const score = selectedEvaluation?.score;
  const scoreNum = score != null ? Number(score) : null;
  const scoreClass =
    scoreNum == null ? 'text-gray-700'
    : scoreNum >= 90 ? 'text-green-600'
    : scoreNum >= 60 ? 'text-amber-600'
    : 'text-red-600';

  const handleClose = () => {
    if (isLoading) return;
    setShowDisputeModal(false);
    setDisputeText('');
  };

  useEffect(() => {
    const timer = setTimeout(() => textareaRef.current?.focus(), 80);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === 'Escape' && !isLoading) handleClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isLoading]);

  const handleBackdropClick = (event) => {
    if (event.target === event.currentTarget) handleClose();
  };

  const handleTextChange = (event) => {
    setDisputeText(event.target.value.slice(0, MAX_DISPUTE_CHARS));
  };

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9999] p-2 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="dispute-modal-title"
      onClick={handleBackdropClick}
    >
      <div className="workhours-modal bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[90vh] flex flex-col">
        <div className="flex-shrink-0 p-4 sm:p-6 pb-0 border-b border-gray-100">
          <div className="flex items-center justify-between">
            <h3 id="dispute-modal-title" className="text-lg sm:text-xl font-bold text-gray-800">
              <i className="fas fa-flag text-blue-500 mr-2" aria-hidden="true" />
              Запрос на переоценку
            </h3>
            <button
              onClick={handleClose}
              className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-full transition disabled:opacity-50"
              disabled={isLoading}
              aria-label="Закрыть"
              type="button"
            >
              <i className="fas fa-times text-lg" aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 sm:p-6 pt-4 workhours-modal-scroll">
          <div className="workhours-info-panel mb-4 p-4 bg-gray-50 rounded-lg border border-gray-200">
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-3">
              <div>
                <p className="text-sm text-gray-700">
                  <span className="font-semibold">Звонок:</span> {selectedEvaluation?.id ? `#${selectedEvaluation.id}` : '-'}
                </p>
                <p className="text-sm text-gray-700">
                  <span className="font-semibold">Телефон:</span> {selectedEvaluation?.phone_number || '-'}
                </p>
                {selectedEvaluation?.month && (
                  <p className="text-sm text-gray-700">
                    <span className="font-semibold">Месяц:</span> {selectedEvaluation.month}
                  </p>
                )}
              </div>

              <div className="text-left sm:text-right">
                <div className="text-xs text-gray-500">Балл</div>
                <div className={`text-lg font-semibold ${scoreClass}`}>
                  {scoreNum != null && Number.isFinite(scoreNum) ? scoreNum.toFixed(0) : '-'}
                </div>
              </div>
            </div>

            {selectedEvaluation?.evaluator && (
              <p className="text-xs text-gray-600">
                Оценщик: {selectedEvaluation.evaluator}
              </p>
            )}
          </div>

          <div className="mb-4 p-3 bg-blue-50 border border-blue-100 rounded-lg text-sm text-gray-700 flex gap-2">
            <i className="fas fa-info-circle text-blue-500 mt-0.5" aria-hidden="true" />
            <span>Опишите конкретные критерии, с которыми вы не согласны. Запрос будет передан руководителю.</span>
          </div>

          <div className="mt-2">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              <i className="fas fa-pen text-gray-400 mr-1" aria-hidden="true" /> Комментарий к запросу
            </label>
            <textarea
              ref={textareaRef}
              value={textValue}
              onChange={handleTextChange}
              maxLength={MAX_DISPUTE_CHARS}
              placeholder="Опишите, с какими критериями вы не согласны и почему..."
              className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm resize-none disabled:bg-gray-50 disabled:text-gray-500"
              rows={4}
              disabled={isLoading}
            />
            <div className="mt-1 text-right text-xs text-gray-500">
              {charCount} / {MAX_DISPUTE_CHARS}
            </div>
          </div>
        </div>

        <div className="flex-shrink-0 p-4 sm:p-6 pt-4 border-t border-gray-100 bg-gray-50 rounded-b-2xl">
          <div className="flex flex-col-reverse sm:flex-row justify-end gap-2">
            <button
              onClick={handleClose}
              className="w-full sm:w-auto px-4 py-2.5 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition font-medium text-sm disabled:opacity-50"
              disabled={isLoading}
              type="button"
            >
              Отмена
            </button>
            <button
              onClick={handleSubmitDispute}
              className={`w-full sm:w-auto px-5 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition font-medium text-sm flex items-center justify-center gap-2 ${canSubmit ? '' : 'opacity-50 cursor-not-allowed'}`}
              disabled={!canSubmit}
              type="button"
            >
              {isLoading ? (
                <>
                  <i className="fas fa-spinner fa-spin" aria-hidden="true" /> Отправка...
                </>
              ) : (
                <>
                  <i className="fas fa-paper-plane" aria-hidden="true" /> Отправить запрос
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DisputeModal;

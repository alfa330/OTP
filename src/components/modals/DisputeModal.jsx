import React from 'react';

const DisputeModal = ({ 
  selectedEvaluation, 
  disputeText, 
  setDisputeText, 
  handleSubmitDispute, 
  isLoading,
  setShowDisputeModal 
}) => {
  const getScoreColor = (score) => {
    if (!score) return 'text-gray-500';
    if (score >= 90) return 'text-green-600';
    if (score >= 60) return 'text-yellow-600';
    return 'text-red-600';
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white p-6 rounded-lg shadow-lg w-full max-w-md">
        <h2 className="text-xl font-semibold mb-4">Оспорить оценку</h2>
        
        <div className="mb-4 space-y-2">
          <p className="font-medium">Информация об оценке:</p>
          <div className="pl-4">
            <p>• ID звонка: <span className="font-semibold">{selectedEvaluation?.id}</span></p>
            <p>• Телефон: <span className="font-semibold">{selectedEvaluation?.phone_number}</span></p>
            <p>• Оценка: <span className={`font-semibold ${getScoreColor(selectedEvaluation?.score)}`}>
              {selectedEvaluation?.score?.toFixed(2)}
            </span></p>
            <p>• Месяц: <span className="font-semibold">{selectedEvaluation?.month}</span></p>
          </div>
        </div>

        <div className="mb-4">
          <label className="block mb-2 font-medium">Ваше сообщение супервайзеру:</label>
          <textarea
            value={disputeText}
            onChange={(e) => setDisputeText(e.target.value)}
            placeholder="Опишите причину оспаривания оценки..."
            className="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            rows={5}
          />
        </div>

        <div className="flex justify-between">
          <button
            onClick={handleSubmitDispute}
            className={`bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600 ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
            disabled={isLoading}
          >
            {isLoading ? 'Отправка...' : 'Отправить запрос'}
          </button>
          <button
            onClick={() => {
              setShowDisputeModal(false);
              setDisputeText('');
            }}
            className="bg-gray-500 text-white px-4 py-2 rounded hover:bg-gray-600"
          >
            Отмена
          </button>
        </div>
      </div>
    </div>
  );
};

export default DisputeModal;

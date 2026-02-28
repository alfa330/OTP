import React, { useState } from 'react';
import SalaryCalculationResult from './SalaryCalculationResult';
import FaIcon from '../common/FaIcon';

const SalaryCalculatorChat = () => {
  const [experience, setExperience] = useState('');
  const [quality, setQuality] = useState('');
  const [avgScore, setAvgScore] = useState('');
  const [responseTime, setResponseTime] = useState('');
  const [chatsPerHour, setChatsPerHour] = useState('');
  const [hoursNorm, setHoursNorm] = useState('');
  const [totalHours, setTotalHours] = useState('');
  const [bonusTraining, setBonusTraining] = useState(false);
  const [bonusRefer, setBonusRefer] = useState(false);
  const [bonusReferQuantity, setBonusReferQuantity] = useState('');
  const [bonusFilming, setBonusFilming] = useState(false);
  const [bonusFilmingQuantity, setBonusFilmingQuantity] = useState('');
  const [result, setResult] = useState(null);
  const [showTable, setShowTable] = useState(false);

  const validateForm = () => {
    // Проверяем, что все обязательные поля не пустые
    if (
      experience === '' ||
      quality === '' ||
      avgScore === '' ||
      responseTime === '' ||
      chatsPerHour === '' ||
      hoursNorm === '' ||
      totalHours === ''
    ) {
      return false;
    }
    // Проверяем диапазоны и типы
    if (
      isNaN(quality) || quality < 0 || quality > 100 ||
      isNaN(avgScore) || avgScore < 0 || avgScore > 5 ||
      isNaN(responseTime) || responseTime < 0 || responseTime > 60 ||
      isNaN(chatsPerHour) || chatsPerHour < 0 || chatsPerHour > 100 ||
      isNaN(hoursNorm) || hoursNorm < 0 || hoursNorm > 744 ||
      isNaN(totalHours) || totalHours < 0 || totalHours > 744 ||
      (bonusRefer && (bonusReferQuantity === '' || isNaN(bonusReferQuantity) || bonusReferQuantity < 1)) ||
      (bonusFilming && (bonusFilmingQuantity === '' || isNaN(bonusFilmingQuantity) || bonusFilmingQuantity < 1))
    ) {
      return false;
    }
    return true;
  };

  const hasInput = () => {
    return (
      experience !== '' ||
      quality !== '' ||
      avgScore !== '' ||
      responseTime !== '' ||
      chatsPerHour !== '' ||
      hoursNorm !== '' ||
      totalHours !== '' ||
      bonusTraining ||
      bonusRefer ||
      bonusFilming
    );
  };

  const calculateSalary = () => {
    let points = 0;
    let bonuses = 0;
    let bonusDetails = [];

    // Experience points
    if (experience === '18+') points += 50;
    else if (experience === '13-17') points += 35;
    else if (experience === '10-12') points += 25;
    else if (experience === '6-9') points += 15;
    else if (experience === '3-5') points += 10;
    else if (experience === '0-2') points += 5;

    // Quality points
    const qual = parseFloat(quality);
    if (qual >= 97 && qual <= 100) points += 25;
    else if (qual >= 94 && qual < 97) points += 20;
    else if (qual >= 90 && qual < 94) points += 15;
    else if (qual >= 86 && qual < 90) points += 10;
    else if (qual >= 80 && qual < 86) points += 5;

    // Avg score points
    const score = parseFloat(avgScore);
    if (score >= 4.9) points += 30;
    else if (score >= 4.8) points += 25;
    else if (score >= 4.7) points += 20;
    else if (score >= 4.6) points += 10;
    else if (score >= 4.5) points += 5;

    // Response time points
    const respTime = parseFloat(responseTime);
    if (respTime <= 2) points += 20;
    else if (respTime <= 3) points += 15;
    else if (respTime <= 4) points += 10;
    else if (respTime <= 4.5) points += 5;

    // Chats per hour points
    const cph = parseFloat(chatsPerHour);
    if (cph >= 25) points += 25;
    else if (cph >= 20) points += 15;
    else if (cph >= 15) points += 10;
    else if (cph >= 10) points += 5;

    // Bonuses
    if (bonusTraining) {
      bonuses += 6000;
      bonusDetails.push('Обучение: 6000 тг');
    }
    if (bonusRefer) {
      const qty = parseInt(bonusReferQuantity) || 0;
      const referBonus = 5000 * qty;
      bonuses += referBonus;
      bonusDetails.push(`Приведи друга: ${referBonus} тг (${qty} чел.)`);
    }
    if (bonusFilming) {
      const qty = parseInt(bonusFilmingQuantity) || 0;
      const filmingBonus = 5000 * qty;
      bonuses += filmingBonus;
      bonusDetails.push(`Съемки: ${filmingBonus} тг (${qty} съемок)`);
    }

    const hn = parseFloat(hoursNorm);
    const th = parseFloat(totalHours);
    const hoursPercentage = hn > 0 ? (th / hn * 100) : 0;
    const premiumCoefficient = hoursPercentage >= 90 ? 1 : 0.75;
    const pointsCoefficient = points / 100;
    const baseSalary = 700 * th;
    const premiumPart = baseSalary * pointsCoefficient * premiumCoefficient;
    const finalSalary = baseSalary + premiumPart + bonuses;

    setResult({
      points,
      premiumCoefficient,
      hoursNorm: hn.toFixed(2),
      hoursPercentage: hoursPercentage.toFixed(2),
      baseSalary: baseSalary.toFixed(2),
      premiumPart: premiumPart.toFixed(2),
      bonuses,
      bonusDetails: bonusDetails.join(', '),
      finalSalary: finalSalary.toFixed(2),
      tableData: { experience, quality: qual, avgScore: score, responseTime: respTime, chatsPerHour: cph }
    });
  };

  const clearForm = () => {
    setExperience('');
    setQuality('');
    setAvgScore('');
    setResponseTime('');
    setChatsPerHour('');
    setHoursNorm('');
    setTotalHours('');
    setBonusTraining(false);
    setBonusRefer(false);
    setBonusReferQuantity('');
    setBonusFilming(false);
    setBonusFilmingQuantity('');
    setResult(null);
    setShowTable(false);
  };

  const togglePointsTable = () => {
    setShowTable(!showTable);
  };

  const generatePointsTable = (data) => {
    const { experience, quality, avgScore, responseTime, chatsPerHour } = data;

    const experienceRanges = [
      { range: '0-2 месяца', points: 5, selected: experience === '0-2', tooltip: 'Начальный стаж' },
      { range: '3-5 месяцев', points: 10, selected: experience === '3-5', tooltip: 'Ранний опыт' },
      { range: '6-9 месяцев', points: 15, selected: experience === '6-9', tooltip: 'Средний стаж' },
      { range: '10-12 месяцев', points: 25, selected: experience === '10-12', tooltip: 'Продвинутый стаж' },
      { range: '13-17 месяцев', points: 35, selected: experience === '13-17', tooltip: 'Высокий стаж' },
      { range: '≥18 месяцев', points: 50, selected: experience === '18+', tooltip: 'Экспертный уровень' }
    ];

    const qualityRanges = [
      { range: '80-85%', points: 5, selected: quality >= 80 && quality < 86, tooltip: 'Базовое качество' },
      { range: '86-89%', points: 10, selected: quality >= 86 && quality < 90, tooltip: 'Хорошее качество' },
      { range: '90-93%', points: 15, selected: quality >= 90 && quality < 94, tooltip: 'Высокое качество' },
      { range: '94-96%', points: 20, selected: quality >= 94 && quality < 97, tooltip: 'Отличное качество' },
      { range: '97-100%', points: 25, selected: quality >= 97 && quality <= 100, tooltip: 'Идеальное качество' }
    ];

    const scoreRanges = [
      { range: '≥4.5', points: 5, selected: avgScore >= 4.5 && avgScore < 4.6, tooltip: 'Хороший балл' },
      { range: '≥4.6', points: 10, selected: avgScore >= 4.6 && avgScore < 4.7, tooltip: 'Очень хороший балл' },
      { range: '≥4.7', points: 20, selected: avgScore >= 4.7 && avgScore < 4.8, tooltip: 'Отличный балл' },
      { range: '≥4.8', points: 25, selected: avgScore >= 4.8 && avgScore < 4.9, tooltip: 'Превосходный балл' },
      { range: '≥4.9', points: 30, selected: avgScore >= 4.9, tooltip: 'Идеальный балл' }
    ];

    const responseTimeRanges = [
      { range: '≤4.5 мин', points: 5, selected: responseTime <= 4.5 && responseTime > 4, tooltip: 'Приемлемое время' },
      { range: '≤4 мин', points: 10, selected: responseTime <= 4 && responseTime > 3, tooltip: 'Хорошее время' },
      { range: '≤3 мин', points: 15, selected: responseTime <= 3 && responseTime > 2, tooltip: 'Отличное время' },
      { range: '≤2 мин', points: 20, selected: responseTime <= 2, tooltip: 'Идеальное время' }
    ];

    const chatsPerHourRanges = [
      { range: '≥10', points: 5, selected: chatsPerHour >= 10 && chatsPerHour < 15, tooltip: 'Базовая производительность' },
      { range: '≥15', points: 10, selected: chatsPerHour >= 15 && chatsPerHour < 20, tooltip: 'Хорошая производительность' },
      { range: '≥20', points: 15, selected: chatsPerHour >= 20 && chatsPerHour < 25, tooltip: 'Высокая производительность' },
      { range: '≥25', points: 25, selected: chatsPerHour >= 25, tooltip: 'Исключительная производительность' }
    ];

    return (
      <table className="min-w-full bg-white border-collapse">
        <thead>
          <tr className="bg-blue-50">
            <th className="p-4 border-b text-left text-sm font-semibold text-gray-700">Категория</th>
            <th className="p-4 border-b text-left text-sm font-semibold text-gray-700">Условие</th>
            <th className="p-4 border-b text-left text-sm font-semibold text-gray-700">Баллы</th>
          </tr>
        </thead>
        <tbody>
          <tr className="bg-gray-100 font-semibold">
            <td className="p-4 border-b" colSpan="3">Стаж</td>
          </tr>
          {experienceRanges.map((row, idx) => (
            <tr key={`exp-${idx}`} className={row.selected ? 'bg-blue-100' : ''}>
              <td className="p-4 border-b"></td>
              <td className="p-4 border-b" title={row.tooltip}>{row.range}</td>
              <td className="p-4 border-b">{row.points}</td>
            </tr>
          ))}
          <tr className="bg-gray-100 font-semibold">
            <td className="p-4 border-b" colSpan="3">Качество</td>
          </tr>
          {qualityRanges.map((row, idx) => (
            <tr key={`qual-${idx}`} className={row.selected ? 'bg-blue-100' : ''}>
              <td className="p-4 border-b"></td>
              <td className="p-4 border-b" title={row.tooltip}>{row.range}</td>
              <td className="p-4 border-b">{row.points}</td>
            </tr>
          ))}
          <tr className="bg-gray-100 font-semibold">
            <td className="p-4 border-b" colSpan="3">Средний балл по чатам</td>
          </tr>
          {scoreRanges.map((row, idx) => (
            <tr key={`score-${idx}`} className={row.selected ? 'bg-blue-100' : ''}>
              <td className="p-4 border-b"></td>
              <td className="p-4 border-b" title={row.tooltip}>{row.range}</td>
              <td className="p-4 border-b">{row.points}</td>
            </tr>
          ))}
          <tr className="bg-gray-100 font-semibold">
            <td className="p-4 border-b" colSpan="3">Время ответа</td>
          </tr>
          {responseTimeRanges.map((row, idx) => (
            <tr key={`resp-${idx}`} className={row.selected ? 'bg-blue-100' : ''}>
              <td className="p-4 border-b"></td>
              <td className="p-4 border-b" title={row.tooltip}>{row.range}</td>
              <td className="p-4 border-b">{row.points}</td>
            </tr>
          ))}
          <tr className="bg-gray-100 font-semibold">
            <td className="p-4 border-b" colSpan="3">Количество чатов в час</td>
          </tr>
          {chatsPerHourRanges.map((row, idx) => (
            <tr key={`cph-${idx}`} className={row.selected ? 'bg-blue-100' : ''}>
              <td className="p-4 border-b"></td>
              <td className="p-4 border-b" title={row.tooltip}>{row.range}</td>
              <td className="p-4 border-b">{row.points}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  };
    
  return (
        <div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div className="p-6 bg-gray-50 rounded-xl shadow-sm hover:shadow-md transition mb-4">
                    <label className="block mb-2 font-semibold text-gray-700 flex items-center gap-2">
                        <FaIcon className="fas fa-user-clock text-blue-500"></FaIcon>
                        Стаж работы (месяцев):
                    </label>
      <select
        value={experience}
        onChange={(e) => setExperience(e.target.value)}
        className="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <option value="">Выберите ваш стаж</option>
        <option value="0-2">0-2 месяцев</option>
        <option value="3-5">3-5 месяцев</option>
        <option value="6-9">6-9 месяцев</option>
        <option value="10-12">10-12 месяцев</option>
        <option value="13-17">13-17 месяцев</option>
        <option value="18+">≥18 месяцев</option>
      </select>
    </div>
                <div className="p-6 bg-gray-50 rounded-xl shadow-sm hover:shadow-md transition mb-4">
                    <label className="block mb-2 font-semibold text-gray-700 flex items-center gap-2">
                        <FaIcon className="fas fa-star text-yellow-500"></FaIcon>
                        Качество %):
                    </label>
      <input
        type="number"
        value={quality}
        onChange={(e) => setQuality(e.target.value)}
        min="0"
        max="100"
        step="0.01"
        className="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
                <div className="p-6 bg-gray-50 rounded-xl shadow-sm hover:shadow-md transition mb-4">
                    <label className="block mb-2 font-semibold text-gray-700 flex items-center gap-2">
                        <FaIcon className="fas fa-chart-line text-green-500"></FaIcon>
                        Средний балл по чатам:
                    </label>
      <input
        type="number"
        value={avgScore}
        onChange={(e) => setAvgScore(e.target.value)}
        min="0"
        max="5"
        step="0.1"
        className="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
                <div className="p-6 bg-gray-50 rounded-xl shadow-sm hover:shadow-md transition mb-4">
                    <label className="block mb-2 font-semibold text-gray-700 flex items-center gap-2">
                        <FaIcon className="fas fa-clock text-purple-500"></FaIcon>
                        Среднее время ответа (минуты):
                    </label>
      <input
        type="number"
        value={responseTime}
        onChange={(e) => setResponseTime(e.target.value)}
        min="0"
        max="60"
        step="0.01"
        className="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
                <div className="p-6 bg-gray-50 rounded-xl shadow-sm hover:shadow-md transition mb-4">
                    <label className="block mb-2 font-semibold text-gray-700 flex items-center gap-2">
                        <FaIcon className="fas fa-comments text-blue-500"></FaIcon>
                        Кол-во чатов в час:
                    </label>
      <input
        type="number"
        value={chatsPerHour}
        onChange={(e) => setChatsPerHour(e.target.value)}
        min="0"
        max="100"
        step="0.01"
        className="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
                <div className="p-6 bg-gray-50 rounded-xl shadow-sm hover:shadow-md transition mb-4">
                    <label className="block mb-2 font-semibold text-gray-700 flex items-center gap-2">
                        <FaIcon className="fas fa-bullseye text-indigo-500"></FaIcon>
                        Норма часов:
                    </label>
      <input
        type="number"
        value={hoursNorm}
        onChange={(e) => setHoursNorm(e.target.value)}
        min="0"
        max="744"
        step="0.01"
        className="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
                <div className="p-6 bg-gray-50 rounded-xl shadow-sm hover:shadow-md transition mb-4">
                    <label className="block mb-2 font-semibold text-gray-700 flex items-center gap-2">
                        <FaIcon className="fas fa-briefcase text-pink-500"></FaIcon>
                        Отработанные часы:
                    </label>
      <input
        type="number"
        value={totalHours}
        onChange={(e) => setTotalHours(e.target.value)}
        min="0"
        max="744"
        step="0.01"
        className="w-full p-3 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
                <div className="p-6 bg-gray-50 rounded-xl shadow-sm hover:shadow-md transition mb-4">
                    <label className="block mb-2 font-semibold text-gray-700 flex items-center gap-2">
                        <FaIcon className="fas fa-gift text-red-500"></FaIcon>
                        Бонусы:
                    </label>
                    <div className="space-y-4 mt-2">
                        <div className="flex items-center gap-4">
                            <input
                                type="checkbox"
                                checked={bonusTraining}
                                onChange={(e) => setBonusTraining(e.target.checked)}
                                className="w-5 h-5"
                            />
                            <label className="text-sm font-medium text-blue-700">Тренинг (6000 ТГ)</label>
                        </div>
                        <div className="flex items-center gap-4">
                            <input
                                type="checkbox"
                                checked={bonusRefer}
                                onChange={(e) => setBonusRefer(e.target.checked)}
                                className="w-5 h-5"
                            />
                            <label className="text-sm font-medium text-green-700">Пригласи друга (5000 ТГ за друга)</label>
                            {bonusRefer && (
                                <input
                                    type="number"
                                    value={bonusReferQuantity}
                                    onChange={(e) => setBonusReferQuantity(e.target.value)}
                                    min="1"
                                    step="1"
                                    placeholder="Кол-во"
                                    className="w-24 p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                />
                            )}
                        </div>
                        <div className="flex items-center gap-4">
                            <input
                                type="checkbox"
                                checked={bonusFilming}
                                onChange={(e) => setBonusFilming(e.target.checked)}
                                className="w-5 h-5"
                            />
                            <label className="text-sm font-medium text-purple-700">Съемки (5000 ТГ)</label>
                            {bonusFilming && (
                                <input
                                    type="number"
                                    value={bonusFilmingQuantity}
                                    onChange={(e) => setBonusFilmingQuantity(e.target.value)}
                                    min="1"
                                    step="1"
                                    placeholder="Кол-во"
                                    className="w-24 p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                />
                            )}
                        </div>
      </div>
    </div>
  </div>
            <div className="flex justify-center gap-4 mt-8">
                <button
                    onClick={calculateSalary}
                    className={`px-8 py-4 rounded-xl font-bold text-lg bg-green-500 text-white hover:bg-green-600 shadow transition-all duration-200 ${
                        !validateForm() ? 'opacity-50 cursor-not-allowed' : ''
                    }`}
                    disabled={!validateForm()}
                >
                    <FaIcon className="fas fa-calculator mr-2"></FaIcon> Рассчетать
                </button>
                <button
                    onClick={togglePointsTable}
                    className={`px-8 py-4 rounded-xl font-bold text-lg bg-purple-500 text-white hover:bg-purple-600 shadow transition-all duration-200 ${
                        !result ? 'opacity-50 cursor-not-allowed' : ''
                    }`}
                    disabled={!result}
                >
                    <FaIcon className="fas fa-table mr-2"></FaIcon> {showTable ? 'Скрыть таблицу' : 'Показать таблицу баллов'}
                </button>
                <button
                    onClick={clearForm}
                    className={`px-8 py-4 rounded-xl font-bold text-lg bg-red-500 text-white hover:bg-red-600 shadow transition-all duration-200 ${
                        !hasInput() ? 'opacity-50 cursor-not-allowed' : ''
                    }`}
                    disabled={!hasInput()}
                >
                    <FaIcon className="fas fa-eraser mr-2"></FaIcon> Очистить
                </button>
            </div>
            <div className="mt-8">
                <SalaryCalculationResult salaryResult={result} />
            </div>
            <div className={`mt-8 ${showTable ? '' : 'hidden'}`}> 
                {result && generatePointsTable(result.tableData)}
            </div>
        </div>
    );
};

export default SalaryCalculatorChat;

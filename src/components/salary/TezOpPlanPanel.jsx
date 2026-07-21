import React, { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import InfoHint from '../common/InfoHint';

/**
 * Панель ввода общего месячного плана отдела для модели TEZ ОП
 * («план успешек на 1 FTE» — одинаков для всех операторов отдела).
 * Показывается в учёте часов управленцам (СВ/глава отдела/админ) отдела TEZ.
 *
 * Props:
 *  - apiBaseUrl: базовый URL API
 *  - userId: id текущего пользователя (заголовок X-User-Id)
 *  - departmentId: id отдела
 *  - month: 'YYYY-MM'
 *  - canEdit: можно ли редактировать (управленец своего отдела)
 *  - onSaved: колбэк после успешного сохранения (обновление колонки «План успешек»)
 */
const TezOpPlanPanel = ({ apiBaseUrl = '', userId, departmentId, month, canEdit = false, onSaved = null }) => {
  const [planPerFte, setPlanPerFte] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  const [year, monthNum] = String(month || '').split('-').map((v) => parseInt(v, 10));
  const validPeriod = Number.isFinite(year) && Number.isFinite(monthNum);

  useEffect(() => {
    if (!departmentId || !validPeriod || !userId) return;
    let cancelled = false;
    setLoaded(false);
    axios
      .get(`${apiBaseUrl}/api/department_plan`, {
        params: { department_id: departmentId, year, month: monthNum },
        headers: { 'X-User-Id': userId },
      })
      .then((resp) => {
        if (cancelled) return;
        const value = resp?.data?.plan?.plan_per_fte;
        setPlanPerFte(value === undefined || value === null ? '' : String(value));
        setLoaded(true);
      })
      .catch(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl, userId, departmentId, year, monthNum, validPeriod]);

  const save = useCallback(() => {
    if (!canEdit || !departmentId || !validPeriod) return;
    setSaving(true);
    setMsg('');
    axios
      .post(
        `${apiBaseUrl}/api/department_plan`,
        { department_id: departmentId, year, month: monthNum, plan_per_fte: parseFloat(planPerFte) || 0 },
        { headers: { 'X-User-Id': userId } }
      )
      .then(() => {
        setMsg('Сохранено');
        setTimeout(() => setMsg(''), 2000);
        if (typeof onSaved === 'function') onSaved();
      })
      .catch(() => {
        setMsg('Ошибка сохранения');
        setTimeout(() => setMsg(''), 3000);
      })
      .finally(() => setSaving(false));
  }, [apiBaseUrl, userId, departmentId, year, monthNum, validPeriod, planPerFte, canEdit, onSaved]);

  return (
    <div className="rounded-2xl border border-teal-200 bg-teal-50/60 px-4 py-4">
      <div className="flex items-center gap-2 text-teal-800 font-semibold mb-3">
        <FaIcon className="fas fa-bullseye" />
        <span>План успешек на 1 FTE</span>
        <InfoHint title="Как считается индивидуальный план" side="left">
          По ставке: план × ставка. При переработке — пропорционально факт-часам
          (план ÷ норма FTE × факт). Новичок в месяце приёма — ×0,8, при неполном месяце —
          пропорционально рабочим дням с даты приёма. При увольнении/выходе на БС —
          пропорционально пересчитанной норме. Норма FTE месяца = округл(дни ÷ 7 × 5) × 8 ч.
          Расчёт по каждому оператору — в колонке «План успешек».
        </InfoHint>
      </div>
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <input
          type="number"
          min="0"
          step="0.01"
          value={planPerFte}
          onChange={(e) => setPlanPerFte(e.target.value)}
          disabled={!canEdit || !loaded}
          placeholder="Напр. 150"
          className="w-full sm:w-48 p-2.5 border rounded-xl focus:outline-none focus:ring-2 focus:ring-teal-500 disabled:bg-gray-100"
        />
        {canEdit && (
          <button
            onClick={save}
            disabled={saving || !loaded}
            className={`inline-flex items-center justify-center gap-2 w-full sm:w-auto px-5 py-2.5 rounded-full font-semibold text-sm text-white shadow transition ${
              saving || !loaded ? 'bg-teal-300 cursor-not-allowed' : 'bg-teal-600 hover:bg-teal-700'
            }`}
          >
            <FaIcon className="fas fa-floppy-disk" />
            Сохранить
          </button>
        )}
        {msg && <span className="text-sm font-medium text-teal-700">{msg}</span>}
      </div>
      <p className="mt-2 text-xs text-teal-700">Общий для всего отдела, одинаков для всех операторов.</p>
    </div>
  );
};

export default TezOpPlanPanel;

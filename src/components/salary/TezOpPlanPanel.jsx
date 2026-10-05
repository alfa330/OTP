import React, { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';
import InfoHint from '../common/InfoHint';
import TezDepartmentPlanSummary from './TezDepartmentPlanSummary';

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
const TezOpPlanPanel = ({ apiBaseUrl = '', userId, departmentId, month, canEdit = false, onSaved = null, refreshKey = 0 }) => {
  const [planPerFte, setPlanPerFte] = useState('');
  const [normHoursFte, setNormHoursFte] = useState('');
  // Общий план отдела и его закрытие считает бэкенд и отдаёт тем же запросом.
  const [summary, setSummary] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  // После сохранения плана общий план отдела надо пересчитать — перезапрашиваем.
  const [reloadKey, setReloadKey] = useState(0);

  const [year, monthNum] = String(month || '').split('-').map((v) => parseInt(v, 10));
  const validPeriod = Number.isFinite(year) && Number.isFinite(monthNum);

  useEffect(() => {
    if (!departmentId || !validPeriod || !userId) return;
    let cancelled = false;
    setLoaded(false);
    setMsg('');
    setPlanPerFte('');
    setNormHoursFte('');
    axios
      .get(`${apiBaseUrl}/api/department_plan`, {
        params: { department_id: departmentId, year, month: monthNum },
        headers: { 'X-User-Id': userId },
      })
      .then((resp) => {
        if (cancelled) return;
        const value = resp?.data?.plan?.plan_per_fte;
        setPlanPerFte(value === undefined || value === null ? '' : String(value));
        setNormHoursFte(String(resp?.data?.plan?.norm_hours_fte ?? ''));
        setSummary(resp?.data?.summary || null);
        setLoaded(true);
      })
      .catch(() => {
        if (!cancelled) {
          setSummary(null);
          setMsg('Не удалось загрузить настройки плана');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl, userId, departmentId, year, monthNum, validPeriod, reloadKey, refreshKey]);

  const save = useCallback(() => {
    if (!canEdit || !departmentId || !validPeriod) return;
    const norm = Number(normHoursFte);
    const plan = Number(planPerFte);
    if (!Number.isFinite(norm) || norm <= 0 || norm > 744 || !Number.isFinite(plan) || plan < 0) {
      setMsg('Укажите план ≥ 0 и норму часов от 0 до 744 (не включая 0)');
      return;
    }
    setSaving(true);
    setMsg('');
    axios
      .post(
        `${apiBaseUrl}/api/department_plan`,
        { department_id: departmentId, year, month: monthNum, plan_per_fte: plan, norm_hours_fte: norm },
        { headers: { 'X-User-Id': userId } }
      )
      .then(() => {
        setMsg('Сохранено');
        setTimeout(() => setMsg(''), 2000);
        setReloadKey((k) => k + 1);
        if (typeof onSaved === 'function') onSaved();
      })
      .catch(() => {
        setMsg('Ошибка сохранения');
        setTimeout(() => setMsg(''), 3000);
      })
      .finally(() => setSaving(false));
  }, [apiBaseUrl, userId, departmentId, year, monthNum, validPeriod, planPerFte, normHoursFte, canEdit, onSaved]);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-4">
      <div className="flex items-center gap-2 text-slate-800 font-semibold mb-3">
        <FaIcon className="fas fa-bullseye" />
        <span>План ОП TEZ</span>
        <InfoHint title="Как считается индивидуальный план" side="left">
          Индивидуальный план = план продаж на 1 FTE ÷ норма часов на 1 FTE ×
          фактически отработанные часы. Весь месяц приёма для новичка действует ×0,8.
        </InfoHint>
      </div>
      <div className="flex flex-col sm:flex-row sm:items-end gap-3">
        <label className="text-xs text-slate-600">План продаж на 1 FTE
        <input
          type="number"
          min="0"
          step="0.01"
          value={planPerFte}
          onChange={(e) => setPlanPerFte(e.target.value)}
          disabled={!canEdit || !loaded || saving}
          placeholder="Напр. 150"
          className="mt-1 block w-full sm:w-48 p-2.5 border rounded-xl focus:outline-none focus:ring-2 focus:ring-teal-500 disabled:bg-gray-100"
        />
        </label>
        <label className="text-xs text-slate-600">Норма часов на 1 FTE
          <input type="number" min="0.01" max="744" step="0.01"
            value={normHoursFte} onChange={(e) => setNormHoursFte(e.target.value)}
            disabled={!canEdit || !loaded || saving}
            className="mt-1 block w-full sm:w-48 rounded-xl border p-2.5 focus:outline-none focus:ring-2 focus:ring-teal-500 disabled:bg-gray-100" />
        </label>
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
      {summary && <div className="mt-4"><TezDepartmentPlanSummary summary={summary} /></div>}
    </div>
  );
};

export default TezOpPlanPanel;

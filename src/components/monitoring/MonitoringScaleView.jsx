import React, { useEffect, useState } from 'react';
import axios from 'axios';
import FaIcon from '../common/FaIcon';

const EMPTY_DESCRIPTION = 'Нет описания';
const STEPS = ['Основное', 'Вес и тип', 'Описание'];

const TONE_STYLES = {
  blue: { iconBg: '#dbeafe', iconColor: '#2563eb', titleColor: '#1d4ed8', valueColor: '#111827' },
  emerald: { iconBg: '#dcfce7', iconColor: '#16a34a', titleColor: '#15803d', valueColor: '#111827' },
  amber: { iconBg: '#fef3c7', iconColor: '#d97706', titleColor: '#b45309', valueColor: '#111827' },
  slate: { iconBg: '#e2e8f0', iconColor: '#475569', titleColor: '#475569', valueColor: '#111827' },
  red: { iconBg: '#fee2e2', iconColor: '#dc2626', titleColor: '#b91c1c', valueColor: '#111827' },
};

const Icon = ({ icon, size = 14, className = '', style, ...rest }) => (
  <FaIcon
    className={['fa-solid', icon, className].filter(Boolean).join(' ')}
    style={{ fontSize: size, ...style }}
    aria-hidden="true"
    {...rest}
  />
);

const normalizeCriterion = (criterion = {}) => ({
  ...criterion,
  name: String(criterion?.name || ''),
  weight: Number(criterion?.weight || 0),
  isCritical: Boolean(criterion?.isCritical),
  value: String(criterion?.value || EMPTY_DESCRIPTION),
  deficiency: criterion?.deficiency
    ? {
        weight: Number(criterion.deficiency?.weight || 0),
        description: String(criterion.deficiency?.description || EMPTY_DESCRIPTION),
      }
    : null,
});

const normalizeDirections = (items) =>
  (Array.isArray(items) ? items : [])
    .filter(Boolean)
    .filter((direction) => direction?.isActive !== false)
    .map((direction) => ({
      ...direction,
      name: String(direction?.name || ''),
      hasFileUpload: direction?.hasFileUpload !== false,
      criteria: Array.isArray(direction?.criteria) ? direction.criteria.map(normalizeCriterion) : [],
    }));

const totalCriteria = (directions) =>
  directions.reduce((sum, direction) => sum + direction.criteria.length, 0);

const weightedDirectionsCount = (directions) =>
  directions.filter((direction) => {
    const weightedCriteria = direction.criteria.filter((criterion) => !criterion.isCritical);
    if (!weightedCriteria.length) return false;
    return weightedCriteria.reduce((sum, criterion) => sum + Number(criterion.weight || 0), 0) === 100;
  }).length;

const clampIndex = (index, length) => {
  if (!length) return 0;
  return Math.min(Math.max(index, 0), length - 1);
};

const Toast = ({ toasts, remove }) => (
  <div className="msv-toast-stack">
    {toasts.map((toast) => (
      <div
        key={toast.id}
        className={`msv-toast${toast.type === 'error' ? ' is-error' : ''}`}
      >
        <Icon icon={toast.type === 'error' ? 'fa-triangle-exclamation' : 'fa-circle-check'} size={15} />
        <span className="msv-toast-text">{toast.message}</span>
        <button type="button" className="msv-toast-close" onClick={() => remove(toast.id)}>
          <Icon icon="fa-xmark" size={13} />
        </button>
      </div>
    ))}
  </div>
);

const useToast = () => {
  const [toasts, setToasts] = useState([]);

  const show = (message, type = 'success') => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((toast) => toast.id !== id));
    }, 4500);
  };

  const remove = (id) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  };

  return { toasts, show, remove };
};

const StepBar = ({ current }) => (
  <div className="msv-stepbar">
    {STEPS.map((label, index) => {
      const isDone = index < current;
      const isActive = index === current;

      return (
        <React.Fragment key={label}>
          <div className="msv-step">
            <div
              className={`msv-step-badge${isDone ? ' is-done' : ''}${isActive ? ' is-active' : ''}`}
            >
              {isDone ? <Icon icon="fa-check" size={12} /> : index + 1}
            </div>
            <span className={`msv-step-label${isDone ? ' is-done' : ''}${isActive ? ' is-active' : ''}`}>
              {label}
            </span>
          </div>
          {index < STEPS.length - 1 && (
            <div className={`msv-step-divider${isDone ? ' is-done' : ''}`} />
          )}
        </React.Fragment>
      );
    })}
  </div>
);

const EmptyState = ({ icon, text, hint }) => (
  <div className="msv-empty">
    <div className="msv-empty-icon">
      <Icon icon={icon} size={24} />
    </div>
    <p className="msv-empty-title">{text}</p>
    {hint ? <p className="msv-empty-hint">{hint}</p> : null}
  </div>
);

const SummaryCard = ({ icon, label, value, tone = 'blue' }) => {
  const palette = TONE_STYLES[tone] || TONE_STYLES.blue;

  return (
    <div className="msv-summary-card">
      <div
        className="msv-summary-icon"
        style={{ background: palette.iconBg, color: palette.iconColor }}
      >
        <Icon icon={icon} size={16} />
      </div>
      <div className="msv-summary-copy">
        <span className="msv-summary-label" style={{ color: palette.titleColor }}>
          {label}
        </span>
        <strong className="msv-summary-value" style={{ color: palette.valueColor }}>
          {value}
        </strong>
      </div>
    </div>
  );
};

export default function MonitoringScaleView({
  directions: initialDirections = [],
  onDirectionsChange,
  onRefresh,
  onSave,
  showToast,
  canEdit = true,
  user,
  apiBaseUrl,
}) {
  const { toasts, show, remove } = useToast();
  const [directions, setDirections] = useState(() => normalizeDirections(initialDirections));
  const [selectedDir, setSelectedDir] = useState(0);
  const [dirName, setDirName] = useState('');
  const [dirFile, setDirFile] = useState(true);
  const [editingDir, setEditingDir] = useState(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardStep, setWizardStep] = useState(0);
  const [editingCrit, setEditingCrit] = useState(null);
  const [critName, setCritName] = useState('');
  const [critCritical, setCritCritical] = useState(false);
  const [critWeight, setCritWeight] = useState('');
  const [critValue, setCritValue] = useState('');
  const [critHasDef, setCritHasDef] = useState(false);
  const [defWeight, setDefWeight] = useState('');
  const [defDesc, setDefDesc] = useState('');
  const [activeTab, setActiveTab] = useState('directions');
  const [isFetching, setIsFetching] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const apiRoot = String(apiBaseUrl || '').trim().replace(/\/+$/, '');
  const canUseApi = Boolean(apiRoot && user?.id && user?.apiKey);

  const notify = (message, type = 'success') => {
    if (typeof showToast === 'function') {
      showToast(message, type);
      return;
    }
    show(message, type);
  };

  const syncDirections = (nextDirections) => {
    const normalized = normalizeDirections(nextDirections);
    setDirections(normalized);
    if (typeof onDirectionsChange === 'function') {
      onDirectionsChange(normalized);
    }
  };

  const buildHeaders = (json = false) => {
    const headers = {};
    if (json) headers['Content-Type'] = 'application/json';
    if (user?.apiKey) headers['X-API-Key'] = user.apiKey;
    if (user?.id) headers['X-User-Id'] = String(user.id);
    return headers;
  };

  const totalWeight = (directionIndex) =>
    (directions[directionIndex]?.criteria || [])
      .filter((criterion) => !criterion.isCritical)
      .reduce((sum, criterion) => sum + Number(criterion.weight || 0), 0);

  const resetDirectionForm = () => {
    setDirName('');
    setDirFile(true);
    setEditingDir(null);
  };

  const resetWizard = () => {
    setCritName('');
    setCritCritical(false);
    setCritWeight('');
    setCritValue('');
    setCritHasDef(false);
    setDefWeight('');
    setDefDesc('');
    setEditingCrit(null);
    setWizardStep(0);
  };

  const loadDirections = async ({ quiet = false } = {}) => {
    if (canUseApi) {
      if (!quiet) setIsFetching(true);
      try {
        const response = await axios.get(`${apiRoot}/api/admin/directions`, {
          headers: buildHeaders(),
        });
        const data = response.data;
        if (data?.status === 'success') {
          syncDirections(data.directions || []);
          return data.directions || [];
        }
        notify(data?.error || 'Не удалось получить направления.', 'error');
      } catch (error) {
        notify(error.response?.data?.error || 'Не удалось получить направления.', 'error');
      } finally {
        if (!quiet) setIsFetching(false);
      }
      return null;
    }

    if (typeof onRefresh === 'function') {
      try {
        const result = await onRefresh();
        if (Array.isArray(result)) {
          syncDirections(result);
        }
      } catch (error) {
        notify(error?.message || 'Не удалось обновить направления.', 'error');
      }
    }
    return null;
  };

  useEffect(() => {
    setDirections(normalizeDirections(initialDirections));
  }, [initialDirections]);

  useEffect(() => {
    setSelectedDir((prev) => clampIndex(prev, directions.length));
  }, [directions.length]);

  useEffect(() => {
    if (!canUseApi) return undefined;

    let cancelled = false;

    const fetchData = async () => {
      setIsFetching(true);
      try {
        const response = await axios.get(`${apiRoot}/api/admin/directions`, {
          headers: buildHeaders(),
        });
        const data = response.data;
        if (cancelled) return;
        if (data?.status === 'success') {
          syncDirections(data.directions || []);
          return;
        }
        notify(data?.error || 'Не удалось получить направления.', 'error');
      } catch (error) {
        if (!cancelled) {
          notify(error.response?.data?.error || 'Не удалось получить направления.', 'error');
        }
      } finally {
        if (!cancelled) {
          setIsFetching(false);
        }
      }
    };

    fetchData();

    return () => {
      cancelled = true;
    };
  }, [apiRoot, canUseApi, user?.apiKey, user?.id]);

  const submitDirection = () => {
    if (!canEdit) return;
    if (!dirName.trim()) {
      notify('Введите название направления.', 'error');
      return;
    }

    if (editingDir !== null) {
      setDirections((prev) =>
        prev.map((direction, index) =>
          index === editingDir
            ? { ...direction, name: dirName.trim(), hasFileUpload: dirFile }
            : direction
        )
      );
      notify('Направление обновлено.');
    } else {
      setDirections((prev) => [
        ...prev,
        { name: dirName.trim(), hasFileUpload: dirFile, criteria: [] },
      ]);
      setSelectedDir(directions.length);
      notify('Направление добавлено.');
    }

    resetDirectionForm();
  };

  const startEditDir = (index) => {
    if (!canEdit) return;
    const targetDirection = directions[index];
    if (!targetDirection) return;
    setDirName(targetDirection.name);
    setDirFile(targetDirection.hasFileUpload);
    setEditingDir(index);
    setSelectedDir(index);
  };

  const deleteDir = (index) => {
    if (!canEdit) return;
    setDirections((prev) => prev.filter((_, currentIndex) => currentIndex !== index));
    setSelectedDir((prev) => {
      if (prev > index) return prev - 1;
      if (prev === index) return Math.max(index - 1, 0);
      return prev;
    });
    if (editingDir === index) {
      resetDirectionForm();
    }
  };

  const openWizard = (criterionIndex = null) => {
    if (!canEdit || !directions[selectedDir]) return;

    if (criterionIndex !== null) {
      const criterion = directions[selectedDir]?.criteria?.[criterionIndex];
      if (!criterion) return;
      setCritName(criterion.name);
      setCritCritical(Boolean(criterion.isCritical));
      setCritWeight(criterion.isCritical ? '' : String(criterion.weight));
      setCritValue(criterion.value === EMPTY_DESCRIPTION ? '' : criterion.value);
      if (criterion.deficiency && !criterion.isCritical) {
        setCritHasDef(true);
        setDefWeight(String(criterion.deficiency.weight));
        setDefDesc(
          criterion.deficiency.description === EMPTY_DESCRIPTION
            ? ''
            : criterion.deficiency.description
        );
      } else {
        setCritHasDef(false);
        setDefWeight('');
        setDefDesc('');
      }
      setEditingCrit(criterionIndex);
    } else {
      resetWizard();
    }

    setWizardStep(0);
    setWizardOpen(true);
  };

  const closeWizard = () => {
    setWizardOpen(false);
    resetWizard();
  };

  const nextStep = () => {
    if (wizardStep === 0 && !critName.trim()) {
      notify('Введите название критерия.', 'error');
      return;
    }

    if (wizardStep === 1 && !critCritical) {
      if (!critWeight) {
        notify('Введите вес критерия.', 'error');
        return;
      }

      const weight = Number(critWeight);
      if (Number.isNaN(weight) || weight <= 0) {
        notify('Вес должен быть положительным числом.', 'error');
        return;
      }

      const currentWeight =
        editingCrit !== null && !directions[selectedDir]?.criteria?.[editingCrit]?.isCritical
          ? directions[selectedDir]?.criteria?.[editingCrit]?.weight || 0
          : 0;
      const usedWeight = totalWeight(selectedDir) - currentWeight;

      if (usedWeight + weight > 100) {
        notify(`Превышен лимит 100. Доступно: ${100 - usedWeight}`, 'error');
        return;
      }
    }

    setWizardStep((prev) => prev + 1);
  };

  const saveCriterion = () => {
    if (!canEdit || !directions[selectedDir]) return;

    if (critHasDef && !critCritical) {
      const deficiencyWeight = Number(defWeight);
      const criterionWeight = Number(critWeight);
      if (
        Number.isNaN(deficiencyWeight) ||
        deficiencyWeight <= 0 ||
        deficiencyWeight > criterionWeight
      ) {
        notify('Вес недочёта должен быть больше 0 и не превышать вес критерия.', 'error');
        return;
      }
    }

    const nextCriterion = {
      name: critName.trim(),
      weight: critCritical ? 0 : Number(critWeight),
      isCritical: critCritical,
      value: critValue.trim() || EMPTY_DESCRIPTION,
      deficiency:
        critHasDef && !critCritical
          ? {
              weight: Number(defWeight),
              description: defDesc.trim() || EMPTY_DESCRIPTION,
            }
          : null,
    };

    setDirections((prev) =>
      prev.map((direction, directionIndex) => {
        if (directionIndex !== selectedDir) return direction;

        const nextCriteria =
          editingCrit !== null
            ? direction.criteria.map((criterion, criterionIndex) =>
                criterionIndex === editingCrit ? nextCriterion : criterion
              )
            : [...direction.criteria, nextCriterion];

        return { ...direction, criteria: nextCriteria };
      })
    );

    notify(editingCrit !== null ? 'Критерий обновлён.' : 'Критерий добавлен.');
    closeWizard();
  };

  const deleteCrit = (index) => {
    if (!canEdit) return;
    setDirections((prev) =>
      prev.map((direction, directionIndex) =>
        directionIndex !== selectedDir
          ? direction
          : {
              ...direction,
              criteria: direction.criteria.filter((_, criterionIndex) => criterionIndex !== index),
            }
      )
    );
  };

  const handleSave = async () => {
    if (!canEdit) return;

    const invalidDirection = directions.find(
      (direction, index) =>
        direction.criteria.some((criterion) => !criterion.isCritical) &&
        totalWeight(index) !== 100
    );

    if (invalidDirection) {
      notify(
        `"${invalidDirection.name}": сумма весов должна быть 100 (сейчас ${totalWeight(
          directions.indexOf(invalidDirection)
        )}).`,
        'error'
      );
      return;
    }

    setIsSaving(true);
    try {
      if (canUseApi) {
        const response = await axios.post(
          `${apiRoot}/api/admin/save_directions`,
          { directions },
          { headers: buildHeaders(true) }
        );
        const data = response.data;
        if (data?.status !== 'success') {
          notify(data?.error || 'Ошибка при сохранении.', 'error');
          return;
        }

        syncDirections(data.directions || directions);
        notify('Мониторинговая шкала сохранена.');
        return;
      }

      await onSave?.(directions);
      notify('Мониторинговая шкала сохранена.');
    } catch (error) {
      notify(error.response?.data?.error || 'Ошибка при сохранении.', 'error');
    } finally {
      setIsSaving(false);
    }
  };

  const selectedDirection = directions[selectedDir] || null;
  const selectedDirectionWeight = selectedDirection ? totalWeight(selectedDir) : 0;
  const hasCriteria = Boolean(selectedDirection?.criteria?.length);
  const hasNonCritical = Boolean(selectedDirection?.criteria?.some((criterion) => !criterion.isCritical));
  const uploadRequiredCount = directions.filter((direction) => direction.hasFileUpload).length;
  const validatedDirections = weightedDirectionsCount(directions);

  const weightTone =
    selectedDirectionWeight === 100 ? 'emerald' : selectedDirectionWeight > 100 ? 'red' : 'amber';
  const weightPalette = TONE_STYLES[weightTone];
  const isBusy = isFetching || isSaving;

  return (
    <>
      <style>{`
        @keyframes msv-slide-in {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: translateY(0); }
        }

        @keyframes msv-spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }

        .msv-shell {
          font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 1480px;
          margin: 0 auto;
          padding: 0 16px 48px;
          color: #111827;
        }

        .msv-header,
        .msv-toolbar,
        .msv-header-actions,
        .msv-select-wrap,
        .msv-inline-note,
        .msv-card-head,
        .dir-item-main,
        .dir-item-meta,
        .crit-head,
        .crit-actions,
        .msv-toast,
        .msv-step,
        .msv-summary-card,
        .toggle-row,
        .btn-primary,
        .btn-ghost,
        .icon-btn {
          display: flex;
          align-items: center;
        }

        .msv-header,
        .msv-toolbar,
        .msv-card-head,
        .dir-item,
        .msv-summary-card {
          justify-content: space-between;
        }

        .msv-header {
          gap: 16px;
          margin-bottom: 24px;
          padding-bottom: 16px;
          border-bottom: 1px solid #e5e7eb;
          flex-wrap: wrap;
        }

        .msv-header-copy h1 {
          margin: 0;
          font-size: clamp(1.55rem, 2vw, 2rem);
          font-weight: 700;
          line-height: 1.1;
        }

        .msv-subtitle {
          margin: 6px 0 0;
          font-size: 14px;
          color: #6b7280;
          line-height: 1.5;
        }

        .msv-header-actions {
          gap: 10px;
          flex-wrap: wrap;
          justify-content: flex-end;
        }

        .msv-summary-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 12px;
          margin-bottom: 24px;
        }

        .msv-summary-card {
          gap: 14px;
          padding: 16px 18px;
          border-radius: 18px;
          border: 1px solid #e5e7eb;
          background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
          box-shadow: 0 8px 24px rgba(15, 23, 42, 0.04);
        }

        .msv-summary-icon {
          width: 42px;
          height: 42px;
          border-radius: 14px;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
        }

        .msv-summary-copy {
          display: flex;
          flex-direction: column;
          gap: 4px;
          min-width: 0;
        }

        .msv-summary-label {
          font-size: 12px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }

        .msv-summary-value {
          font-size: 1.35rem;
          line-height: 1;
        }

        .msv-tabs {
          display: flex;
          gap: 10px;
          overflow-x: auto;
          padding-bottom: 4px;
          margin-bottom: 24px;
        }

        .tab-btn {
          padding: 10px 16px;
          border-radius: 999px;
          border: 1px solid #dbe2ea;
          background: #ffffff;
          cursor: pointer;
          font-size: 14px;
          font-weight: 600;
          color: #64748b;
          transition: all .18s ease;
          gap: 8px;
          white-space: nowrap;
        }

        .tab-btn:hover {
          border-color: #bfdbfe;
          color: #1d4ed8;
          background: #f8fbff;
        }

        .tab-btn.active {
          color: #1d4ed8;
          background: #eff6ff;
          border-color: #93c5fd;
          box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.08);
        }

        .msv-directions-layout {
          display: grid;
          grid-template-columns: 1fr;
          gap: 24px;
          align-items: start;
        }

        .msv-card {
          background: #ffffff;
          border: 1px solid #e5e7eb;
          border-radius: 20px;
          padding: 20px;
          box-shadow: 0 14px 40px rgba(15, 23, 42, 0.04);
        }

        .msv-card.is-muted {
          background: linear-gradient(180deg, #ffffff 0%, #fafafa 100%);
        }

        .msv-card-head {
          gap: 14px;
          margin-bottom: 18px;
          flex-wrap: wrap;
        }

        .msv-card-head h2 {
          margin: 0;
          font-size: 16px;
          font-weight: 700;
        }

        .msv-card-head p {
          margin: 4px 0 0;
          font-size: 13px;
          color: #6b7280;
        }

        .msv-form-grid,
        .msv-criteria-grid,
        .msv-modal-grid {
          display: grid;
          gap: 14px;
        }

        .msv-form-actions {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
        }

        .msv-form-actions .btn-primary {
          flex: 1 1 220px;
        }

        .field-label {
          display: block;
          font-size: 13px;
          font-weight: 600;
          color: #374151;
          margin-bottom: 6px;
        }

        input[type=text],
        input[type=number],
        textarea,
        select {
          width: 100%;
          padding: 10px 12px;
          border: 1px solid #d1d5db;
          border-radius: 12px;
          font-size: 14px;
          font-family: inherit;
          outline: none;
          transition: border .15s ease, box-shadow .15s ease;
          box-sizing: border-box;
          background: #ffffff;
          color: #111827;
        }

        input:focus,
        textarea:focus,
        select:focus {
          border-color: #2563eb;
          box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
        }

        textarea {
          resize: vertical;
          min-height: 88px;
          line-height: 1.6;
        }

        .toggle-row {
          gap: 12px;
          padding: 12px 14px;
          border-radius: 14px;
          background: #f8fafc;
          border: 1px solid #e5e7eb;
          cursor: pointer;
          user-select: none;
        }

        .toggle-row input {
          width: 16px;
          height: 16px;
          accent-color: #2563eb;
          flex-shrink: 0;
        }

        .toggle-row-title {
          display: block;
          font-size: 13px;
          font-weight: 600;
          color: #111827;
        }

        .toggle-row-hint {
          display: block;
          margin-top: 2px;
          font-size: 12px;
          color: #6b7280;
          line-height: 1.5;
        }

        .msv-list {
          display: grid;
          gap: 10px;
        }

        .dir-item {
          display: flex;
          gap: 14px;
          padding: 14px;
          border-radius: 16px;
          border: 1px solid transparent;
          background: #ffffff;
          cursor: pointer;
          transition: transform .15s ease, border-color .15s ease, background .15s ease, box-shadow .15s ease;
        }

        .dir-item:hover {
          background: #f8fbff;
          border-color: #dbeafe;
          transform: translateY(-1px);
          box-shadow: 0 10px 24px rgba(37, 99, 235, 0.06);
        }

        .dir-item.selected {
          background: linear-gradient(180deg, #eff6ff 0%, #ffffff 100%);
          border-color: #93c5fd;
        }

        .dir-item-main {
          gap: 12px;
          min-width: 0;
          flex: 1;
        }

        .dir-item-icon {
          width: 36px;
          height: 36px;
          border-radius: 12px;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
          background: #eff6ff;
          color: #2563eb;
        }

        .dir-item-copy {
          min-width: 0;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .dir-item-name {
          font-size: 14px;
          font-weight: 600;
          color: #111827;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .dir-item-meta {
          gap: 8px;
          flex-wrap: wrap;
        }

        .chip {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.01em;
        }

        .chip.blue {
          background: #eff6ff;
          color: #1d4ed8;
          border: 1px solid #bfdbfe;
        }

        .chip.slate {
          background: #f8fafc;
          color: #475569;
          border: 1px solid #cbd5e1;
        }

        .chip.emerald {
          background: #f0fdf4;
          color: #166534;
          border: 1px solid #bbf7d0;
        }

        .chip.amber {
          background: #fffbeb;
          color: #92400e;
          border: 1px solid #fde68a;
        }

        .chip.orange {
          background: #fff7ed;
          color: #9a3412;
          border: 1px solid #fed7aa;
        }

        .chip.red {
          background: #fef2f2;
          color: #b91c1c;
          border: 1px solid #fecaca;
        }

        .dir-item-actions,
        .crit-actions {
          display: flex;
          gap: 6px;
          flex-shrink: 0;
        }

        .icon-btn {
          justify-content: center;
          width: 34px;
          height: 34px;
          border-radius: 10px;
          border: none;
          background: transparent;
          cursor: pointer;
          color: #64748b;
          transition: all .15s ease;
        }

        .icon-btn:hover {
          background: #f1f5f9;
          color: #0f172a;
        }

        .icon-btn.danger:hover {
          background: #fef2f2;
          color: #dc2626;
        }

        .btn-primary,
        .btn-ghost {
          justify-content: center;
          gap: 8px;
          padding: 11px 16px;
          border-radius: 12px;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          transition: all .15s ease;
          text-decoration: none;
        }

        .btn-primary {
          border: 1px solid #2563eb;
          background: #2563eb;
          color: #ffffff;
        }

        .btn-primary:hover {
          background: #1d4ed8;
          border-color: #1d4ed8;
        }

        .btn-ghost {
          border: 1px solid #d1d5db;
          background: #ffffff;
          color: #374151;
        }

        .btn-ghost:hover {
          background: #f8fafc;
          border-color: #cbd5e1;
        }

        .btn-primary:disabled,
        .btn-ghost:disabled,
        .icon-btn:disabled,
        input:disabled,
        textarea:disabled,
        select:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .msv-inline-note {
          gap: 10px;
          padding: 12px 14px;
          border-radius: 14px;
          border: 1px solid #e5e7eb;
          background: #f8fafc;
          color: #475569;
          margin-bottom: 18px;
          line-height: 1.55;
        }

        .msv-inline-note strong {
          color: #0f172a;
        }

        .msv-toolbar {
          gap: 14px;
          margin-bottom: 20px;
          flex-wrap: wrap;
        }

        .msv-select-wrap {
          gap: 12px;
          flex: 1 1 320px;
          min-width: 0;
        }

        .msv-select-wrap .field-label {
          margin: 0;
          white-space: nowrap;
        }

        .msv-weight-card {
          margin-bottom: 18px;
          padding: 14px 16px;
          border-radius: 16px;
          border: 1px solid #e5e7eb;
        }

        .msv-weight-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 10px;
          flex-wrap: wrap;
        }

        .msv-weight-title {
          font-size: 13px;
          font-weight: 700;
        }

        .msv-weight-value {
          font-size: 14px;
          font-weight: 700;
        }

        .msv-progress-track {
          height: 8px;
          border-radius: 999px;
          background: #e5e7eb;
          overflow: hidden;
        }

        .msv-progress-bar {
          height: 100%;
          border-radius: 999px;
          transition: width .25s ease, background .25s ease;
        }

        .msv-criteria-grid {
          grid-template-columns: 1fr;
        }

        .crit-card {
          height: 100%;
          border-radius: 18px;
          border: 1px solid #e5e7eb;
          background: linear-gradient(180deg, #ffffff 0%, #fcfcfd 100%);
          padding: 16px;
          box-shadow: 0 12px 28px rgba(15, 23, 42, 0.04);
          animation: msv-slide-in .16s ease;
        }

        .crit-head {
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 12px;
        }

        .crit-main {
          display: flex;
          gap: 12px;
          min-width: 0;
          flex: 1;
        }

        .crit-icon {
          width: 36px;
          height: 36px;
          border-radius: 12px;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
          background: #f8fafc;
          color: #475569;
        }

        .crit-title {
          font-size: 15px;
          font-weight: 700;
          color: #111827;
          line-height: 1.35;
          margin: 0 0 8px;
        }

        .crit-copy p {
          margin: 0;
          font-size: 13px;
          color: #6b7280;
          line-height: 1.65;
          white-space: pre-wrap;
        }

        .crit-copy p + p {
          margin-top: 8px;
        }

        .crit-copy .is-deficiency {
          color: #9a3412;
          font-style: italic;
        }

        .msv-empty {
          text-align: center;
          padding: 36px 18px;
          border-radius: 18px;
          border: 1px dashed #cbd5e1;
          background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
          color: #64748b;
        }

        .msv-empty-icon {
          width: 54px;
          height: 54px;
          border-radius: 18px;
          margin: 0 auto 14px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: #eff6ff;
          color: #2563eb;
        }

        .msv-empty-title {
          margin: 0;
          font-size: 15px;
          font-weight: 700;
          color: #111827;
        }

        .msv-empty-hint {
          margin: 6px 0 0;
          font-size: 13px;
          line-height: 1.6;
        }

        .wiz-backdrop {
          position: fixed;
          inset: 0;
          z-index: 100;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 16px;
          background: rgba(15, 23, 42, 0.45);
          backdrop-filter: blur(4px);
        }

        .wiz-card {
          width: min(860px, 100%);
          max-height: calc(100vh - 32px);
          overflow-y: auto;
          background: #ffffff;
          border-radius: 24px;
          padding: 24px;
          box-shadow: 0 28px 80px rgba(15, 23, 42, 0.22);
        }

        .wiz-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 20px;
        }

        .wiz-head h2 {
          margin: 0;
          font-size: 18px;
          font-weight: 700;
        }

        .msv-stepbar {
          display: flex;
          align-items: flex-start;
          gap: 0;
          margin-bottom: 24px;
          overflow-x: auto;
          padding-bottom: 4px;
        }

        .msv-step {
          flex-direction: column;
          justify-content: flex-start;
          gap: 6px;
          min-width: 110px;
          text-align: center;
        }

        .msv-step-badge {
          width: 30px;
          height: 30px;
          border-radius: 999px;
          display: flex;
          align-items: center;
          justify-content: center;
          margin: 0 auto;
          font-size: 12px;
          font-weight: 700;
          background: #f1f5f9;
          color: #64748b;
          border: 2px solid #e5e7eb;
          transition: all .2s ease;
        }

        .msv-step-badge.is-active {
          background: #2563eb;
          color: #ffffff;
          border-color: #bfdbfe;
        }

        .msv-step-badge.is-done {
          background: #16a34a;
          color: #ffffff;
          border-color: #bbf7d0;
        }

        .msv-step-label {
          font-size: 12px;
          font-weight: 600;
          color: #94a3b8;
          white-space: nowrap;
        }

        .msv-step-label.is-active {
          color: #1d4ed8;
        }

        .msv-step-label.is-done {
          color: #15803d;
        }

        .msv-step-divider {
          flex: 1;
          min-width: 40px;
          height: 2px;
          border-radius: 999px;
          background: #e5e7eb;
          margin: 14px 8px 0;
        }

        .msv-step-divider.is-done {
          background: #bbf7d0;
        }

        .msv-modal-grid {
          grid-template-columns: 1fr;
        }

        .msv-help {
          padding: 12px 14px;
          border-radius: 14px;
          border: 1px solid #e5e7eb;
          background: #f8fafc;
          color: #64748b;
          font-size: 13px;
          line-height: 1.65;
        }

        .msv-help p {
          margin: 0;
        }

        .msv-indent {
          padding-left: 14px;
          border-left: 2px solid #fed7aa;
        }

        .wiz-footer {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          margin-top: 24px;
          padding-top: 16px;
          border-top: 1px solid #e5e7eb;
        }

        .msv-toast-stack {
          position: fixed;
          right: 20px;
          bottom: 20px;
          z-index: 120;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .msv-toast {
          gap: 10px;
          padding: 10px 14px;
          border-radius: 12px;
          border: 1px solid #bbf7d0;
          background: #f0fdf4;
          color: #166534;
          box-shadow: 0 12px 24px rgba(15, 23, 42, 0.08);
          max-width: 360px;
          animation: msv-slide-in .2s ease;
        }

        .msv-toast.is-error {
          border-color: #fecaca;
          background: #fef2f2;
          color: #991b1b;
        }

        .msv-toast-text {
          flex: 1;
          font-size: 13px;
          line-height: 1.5;
        }

        .msv-toast-close {
          border: none;
          background: transparent;
          color: inherit;
          padding: 0;
          cursor: pointer;
          opacity: 0.7;
        }

        .msv-spinner {
          animation: msv-spin 1s linear infinite;
        }

        @media (max-width: 767px) {
          .msv-header-actions,
          .msv-form-actions,
          .wiz-footer,
          .msv-toolbar {
            width: 100%;
          }

          .msv-header-actions > *,
          .msv-form-actions > *,
          .wiz-footer > * {
            flex: 1 1 100%;
          }

          .dir-item {
            flex-direction: column;
            align-items: stretch;
          }

          .dir-item-actions,
          .crit-actions {
            justify-content: flex-end;
          }

          .msv-select-wrap {
            flex-direction: column;
            align-items: stretch;
          }

          .msv-select-wrap .field-label {
            white-space: normal;
          }

          .wiz-footer {
            flex-direction: column-reverse;
          }
        }

        @media (min-width: 768px) {
          .msv-shell {
            padding: 0 20px 56px;
          }

          .msv-card {
            padding: 22px;
          }

          .msv-criteria-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }

          .msv-modal-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
        }

        @media (min-width: 1100px) {
          .msv-directions-layout {
            grid-template-columns: minmax(340px, 400px) minmax(0, 1fr);
          }

          .msv-sticky {
            position: sticky;
            top: 20px;
          }
        }

        @media (min-width: 1440px) {
          .msv-directions-layout {
            grid-template-columns: minmax(360px, 430px) minmax(0, 1fr);
          }

          .msv-criteria-grid {
            grid-template-columns: repeat(3, minmax(0, 1fr));
          }

          .wiz-card {
            width: min(960px, 100%);
          }
        }
      `}</style>

      <div className="msv-shell">
        <div className="msv-header">
          <div className="msv-header-copy">
            <h1>Мониторинговая шкала</h1>
            <p className="msv-subtitle">
              Настройка направлений, весов и критических критериев для оценки звонков.
            </p>
          </div>

          <div className="msv-header-actions">
            <button
              type="button"
              className="btn-ghost"
              onClick={() => loadDirections()}
              disabled={isBusy || (!canUseApi && typeof onRefresh !== 'function')}
            >
              <Icon
                icon="fa-arrows-rotate"
                size={14}
                className={isFetching ? 'fa-spin msv-spinner' : ''}
              />
              Обновить
            </button>
            <button type="button" className="btn-primary" onClick={handleSave} disabled={isBusy || !canEdit}>
              <Icon
                icon="fa-floppy-disk"
                size={14}
                className={isSaving ? 'fa-spin msv-spinner' : ''}
              />
              {isSaving ? 'Сохранение...' : 'Сохранить'}
            </button>
          </div>
        </div>

        <div className="msv-summary-grid">
          <SummaryCard icon="fa-folder" label="Направлений" value={directions.length} tone="blue" />
          <SummaryCard icon="fa-list-check" label="Критериев" value={totalCriteria(directions)} tone="emerald" />
          <SummaryCard icon="fa-bullseye" label="С весом 100%" value={validatedDirections} tone="amber" />
          <SummaryCard icon="fa-file-arrow-up" label="С загрузкой файла" value={uploadRequiredCount} tone="slate" />
        </div>

        {!canEdit ? (
          <div className="msv-inline-note">
            <Icon icon="fa-circle-info" size={14} />
            <span>
              <strong>Режим просмотра.</strong> Редактирование и сохранение доступны только администраторам.
            </span>
          </div>
        ) : null}

        <div className="msv-tabs">
          {[
            { id: 'directions', label: 'Направления', icon: 'fa-folder' },
            { id: 'criteria', label: 'Критерии', icon: 'fa-list-check' },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`tab-btn${activeTab === tab.id ? ' active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <Icon icon={tab.icon} size={14} />
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === 'directions' && (
          <div className="msv-directions-layout">
            <div className="msv-card msv-sticky">
              <div className="msv-card-head">
                <div>
                  <h2>{editingDir !== null ? 'Редактировать направление' : 'Новое направление'}</h2>
                  <p>Подготовьте структуру шкалы и настройте требование к файлу.</p>
                </div>
                <div className="chip blue">
                  <Icon icon="fa-folder" size={11} />
                  {directions.length} шт.
                </div>
              </div>

              <div className="msv-form-grid">
                <div>
                  <label className="field-label">Название</label>
                  <input
                    type="text"
                    value={dirName}
                    onChange={(event) => setDirName(event.target.value)}
                    placeholder="Введите название направления..."
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') submitDirection();
                    }}
                    disabled={!canEdit}
                  />
                </div>

                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={dirFile}
                    onChange={(event) => setDirFile(event.target.checked)}
                    disabled={!canEdit}
                  />
                  <div>
                    <span className="toggle-row-title">Требуется загрузка файла</span>
                    <span className="toggle-row-hint">
                      Участник должен приложить документ вместе с оценкой.
                    </span>
                  </div>
                </label>

                <div className="msv-form-actions">
                  <button type="button" className="btn-primary" onClick={submitDirection} disabled={!canEdit}>
                    <Icon icon={editingDir !== null ? 'fa-check' : 'fa-circle-plus'} size={14} />
                    {editingDir !== null ? 'Сохранить' : 'Добавить'}
                  </button>
                  {editingDir !== null ? (
                    <button type="button" className="btn-ghost" onClick={resetDirectionForm} disabled={!canEdit}>
                      <Icon icon="fa-xmark" size={14} />
                      Сбросить
                    </button>
                  ) : null}
                </div>
              </div>
            </div>

            <div className="msv-card is-muted">
              <div className="msv-card-head">
                <div>
                  <h2>Список направлений</h2>
                  <p>Выберите направление, чтобы перейти к критериям и их настройке.</p>
                </div>
                <div className="chip slate">
                  <Icon icon="fa-file-arrow-up" size={11} />
                  {uploadRequiredCount} с файлом
                </div>
              </div>

              {isFetching && directions.length === 0 ? (
                <EmptyState
                  icon="fa-spinner"
                  text="Загружаем направления"
                  hint="Подтягиваем актуальную шкалу с сервера."
                />
              ) : directions.length === 0 ? (
                <EmptyState
                  icon="fa-folder"
                  text="Пока нет направлений"
                  hint="Добавьте первое направление, чтобы начать настройку шкалы."
                />
              ) : (
                <div className="msv-list">
                  {directions.map((direction, index) => (
                    <div
                      key={`${direction.id || 'direction'}-${index}`}
                      className={`dir-item${selectedDir === index ? ' selected' : ''}`}
                      onClick={() => setSelectedDir(index)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          setSelectedDir(index);
                        }
                      }}
                    >
                      <div className="dir-item-main">
                        <div
                          className="dir-item-icon"
                          style={{
                            background: direction.hasFileUpload ? '#eff6ff' : '#f8fafc',
                            color: direction.hasFileUpload ? '#2563eb' : '#64748b',
                          }}
                        >
                          <Icon icon={direction.hasFileUpload ? 'fa-file-arrow-up' : 'fa-ban'} size={14} />
                        </div>

                        <div className="dir-item-copy">
                          <span className="dir-item-name">{direction.name}</span>
                          <div className="dir-item-meta">
                            <span className={`chip ${direction.hasFileUpload ? 'blue' : 'slate'}`}>
                              <Icon icon={direction.hasFileUpload ? 'fa-file-arrow-up' : 'fa-ban'} size={10} />
                              {direction.hasFileUpload ? 'Файл обязателен' : 'Без файла'}
                            </span>
                            <span className="chip emerald">
                              <Icon icon="fa-list-check" size={10} />
                              {direction.criteria.length} кр.
                            </span>
                            {direction.criteria.some((criterion) => !criterion.isCritical) ? (
                              <span
                                className={`chip ${totalWeight(index) === 100 ? 'emerald' : 'amber'}`}
                              >
                                <Icon icon="fa-bullseye" size={10} />
                                Вес {totalWeight(index)}/100
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </div>

                      {canEdit ? (
                        <div className="dir-item-actions">
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={(event) => {
                              event.stopPropagation();
                              startEditDir(index);
                            }}
                            title="Редактировать"
                          >
                            <Icon icon="fa-pen-to-square" size={13} />
                          </button>
                          <button
                            type="button"
                            className="icon-btn danger"
                            onClick={(event) => {
                              event.stopPropagation();
                              deleteDir(index);
                            }}
                            title="Удалить"
                          >
                            <Icon icon="fa-trash-can" size={13} />
                          </button>
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'criteria' && (
          <div className="msv-card">
            <div className="msv-toolbar">
              <div className="msv-select-wrap">
                <label className="field-label">Направление:</label>
                <select
                  value={selectedDirection ? String(selectedDir) : ''}
                  onChange={(event) => setSelectedDir(Number(event.target.value))}
                  disabled={!directions.length}
                >
                  {!directions.length ? (
                    <option value="">— нет направлений —</option>
                  ) : (
                    directions.map((direction, index) => (
                      <option key={`${direction.id || 'direction'}-${index}`} value={index}>
                        {direction.name}
                      </option>
                    ))
                  )}
                </select>
              </div>

              <button
                type="button"
                className="btn-primary"
                disabled={!directions.length || !canEdit}
                onClick={() => openWizard()}
              >
                <Icon icon="fa-circle-plus" size={14} />
                Добавить критерий
              </button>
            </div>

            {selectedDirection ? (
              <div className="msv-inline-note">
                <Icon icon={selectedDirection.hasFileUpload ? 'fa-file-arrow-up' : 'fa-ban'} size={14} />
                <span>
                  <strong>{selectedDirection.name}</strong>
                  {selectedDirection.hasFileUpload
                    ? ' требует загрузку подтверждающего файла.'
                    : ' не требует загрузку файла.'}
                </span>
              </div>
            ) : null}

            {selectedDirection && hasNonCritical ? (
              <div
                className="msv-weight-card"
                style={{ background: weightPalette.iconBg, borderColor: weightPalette.iconBg }}
              >
                <div className="msv-weight-head">
                  <span className="msv-weight-title" style={{ color: weightPalette.titleColor }}>
                    Сумма весов некритических критериев
                  </span>
                  <span className="msv-weight-value" style={{ color: weightPalette.iconColor }}>
                    {selectedDirectionWeight}/100
                  </span>
                </div>
                <div className="msv-progress-track">
                  <div
                    className="msv-progress-bar"
                    style={{
                      width: `${Math.min(selectedDirectionWeight, 100)}%`,
                      background: weightPalette.iconColor,
                    }}
                  />
                </div>
              </div>
            ) : null}

            {isFetching && !selectedDirection ? (
              <EmptyState
                icon="fa-spinner"
                text="Загружаем критерии"
                hint="Получаем данные по шкале с сервера."
              />
            ) : !hasCriteria ? (
              <EmptyState
                icon="fa-list-check"
                text={directions.length === 0 ? 'Сначала добавьте направление' : 'Критериев пока нет'}
                hint={
                  directions.length === 0
                    ? 'Без направления критерии добавить нельзя.'
                    : 'Нажмите «Добавить критерий», чтобы заполнить шкалу.'
                }
              />
            ) : (
              <div className="msv-criteria-grid">
                {selectedDirection.criteria.map((criterion, index) => (
                  <div key={`${criterion.name}-${index}`} className="crit-card">
                    <div className="crit-head">
                      <div className="crit-main">
                        <div
                          className="crit-icon"
                          style={{
                            background: criterion.isCritical ? '#fffbeb' : '#f0fdf4',
                            color: criterion.isCritical ? '#d97706' : '#16a34a',
                          }}
                        >
                          <Icon
                            icon={criterion.isCritical ? 'fa-triangle-exclamation' : 'fa-circle-check'}
                            size={15}
                          />
                        </div>

                        <div className="crit-copy">
                          <h3 className="crit-title">{criterion.name}</h3>
                          <div className="dir-item-meta">
                            <span className={`chip ${criterion.isCritical ? 'amber' : 'emerald'}`}>
                              <Icon
                                icon={criterion.isCritical ? 'fa-triangle-exclamation' : 'fa-bullseye'}
                                size={10}
                              />
                              {criterion.isCritical ? 'Критичный' : `${criterion.weight}%`}
                            </span>
                            {criterion.deficiency ? (
                              <span className="chip orange">
                                <Icon icon="fa-circle-info" size={10} />
                                Недочёт {criterion.deficiency.weight}%
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </div>

                      {canEdit ? (
                        <div className="crit-actions">
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={() => openWizard(index)}
                            title="Редактировать"
                          >
                            <Icon icon="fa-pen-to-square" size={13} />
                          </button>
                          <button
                            type="button"
                            className="icon-btn danger"
                            onClick={() => deleteCrit(index)}
                            title="Удалить"
                          >
                            <Icon icon="fa-trash-can" size={13} />
                          </button>
                        </div>
                      ) : null}
                    </div>

                    <div className="crit-copy">
                      {criterion.value && criterion.value !== EMPTY_DESCRIPTION ? (
                        <p>{criterion.value}</p>
                      ) : null}
                      {criterion.deficiency?.description &&
                      criterion.deficiency.description !== EMPTY_DESCRIPTION ? (
                        <p className="is-deficiency">Недочёт: {criterion.deficiency.description}</p>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {wizardOpen ? (
        <div className="wiz-backdrop" onClick={(event) => event.target === event.currentTarget && closeWizard()}>
          <div className="wiz-card">
            <div className="wiz-head">
              <h2>{editingCrit !== null ? 'Редактировать критерий' : 'Новый критерий'}</h2>
              <button type="button" className="icon-btn" onClick={closeWizard}>
                <Icon icon="fa-xmark" size={15} />
              </button>
            </div>

            <StepBar current={wizardStep} />

            {wizardStep === 0 ? (
              <div className="msv-modal-grid">
                <div>
                  <label className="field-label">
                    Название критерия <span style={{ color: '#ef4444' }}>*</span>
                  </label>
                  <input
                    type="text"
                    value={critName}
                    onChange={(event) => setCritName(event.target.value)}
                    placeholder="Например: полнота ответа..."
                    autoFocus
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') nextStep();
                    }}
                  />
                </div>

                <div className="msv-help">
                  <p>
                    Название должно коротко и понятно объяснять, что именно оценивается по этому пункту.
                  </p>
                </div>
              </div>
            ) : null}

            {wizardStep === 1 ? (
              <div className="msv-form-grid">
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={critCritical}
                    onChange={(event) => {
                      const isChecked = event.target.checked;
                      setCritCritical(isChecked);
                      if (isChecked) {
                        setCritWeight('');
                        setCritHasDef(false);
                        setDefWeight('');
                        setDefDesc('');
                      }
                    }}
                  />
                  <div>
                    <span className="toggle-row-title">Критичный критерий</span>
                    <span className="toggle-row-hint">
                      Ошибка по такому критерию автоматически обнуляет итоговую оценку.
                    </span>
                  </div>
                </label>

                {!critCritical ? (
                  <div>
                    <label className="field-label">
                      Вес <span style={{ color: '#ef4444' }}>*</span>
                    </label>
                    <input
                      type="number"
                      value={critWeight}
                      onChange={(event) => setCritWeight(event.target.value)}
                      placeholder={`Доступно: ${
                        100 -
                        totalWeight(selectedDir) +
                        (editingCrit !== null &&
                        !directions[selectedDir]?.criteria?.[editingCrit]?.isCritical
                          ? directions[selectedDir]?.criteria?.[editingCrit]?.weight || 0
                          : 0)
                      }`}
                      min="1"
                      max="100"
                      autoFocus
                    />
                  </div>
                ) : null}

                {!critCritical ? (
                  <label className="toggle-row">
                    <input
                      type="checkbox"
                      checked={critHasDef}
                      onChange={(event) => {
                        const isChecked = event.target.checked;
                        setCritHasDef(isChecked);
                        if (!isChecked) {
                          setDefWeight('');
                          setDefDesc('');
                        }
                      }}
                    />
                    <div>
                      <span className="toggle-row-title">Есть недочёт</span>
                      <span className="toggle-row-hint">
                        Для частичной ошибки можно указать отдельный сниженный вес.
                      </span>
                    </div>
                  </label>
                ) : null}

                {critHasDef && !critCritical ? (
                  <div className="msv-indent">
                    <label className="field-label">Вес недочёта</label>
                    <input
                      type="number"
                      value={defWeight}
                      onChange={(event) => setDefWeight(event.target.value)}
                      placeholder={`1 – ${critWeight || '...'}`}
                      min="1"
                    />
                  </div>
                ) : null}
              </div>
            ) : null}

            {wizardStep === 2 ? (
              <div className="msv-form-grid">
                <div>
                  <label className="field-label">Описание критерия</label>
                  <textarea
                    value={critValue}
                    onChange={(event) => setCritValue(event.target.value)}
                    placeholder="Подробно опишите, что проверяется и как оценивается..."
                    style={{ minHeight: 140 }}
                    autoFocus
                  />
                </div>

                {critHasDef && !critCritical ? (
                  <div className="msv-indent">
                    <label className="field-label">Описание недочёта</label>
                    <textarea
                      value={defDesc}
                      onChange={(event) => setDefDesc(event.target.value)}
                      placeholder="Опишите, как выглядит частичная ошибка..."
                      style={{ minHeight: 96 }}
                    />
                  </div>
                ) : null}
              </div>
            ) : null}

            <div className="wiz-footer">
              <button
                type="button"
                className="btn-ghost"
                onClick={wizardStep === 0 ? closeWizard : () => setWizardStep((prev) => prev - 1)}
              >
                <Icon icon={wizardStep === 0 ? 'fa-xmark' : 'fa-angle-left'} size={14} />
                {wizardStep === 0 ? 'Отмена' : 'Назад'}
              </button>

              {wizardStep < STEPS.length - 1 ? (
                <button type="button" className="btn-primary" onClick={nextStep}>
                  Далее
                  <Icon icon="fa-angle-right" size={14} />
                </button>
              ) : (
                <button type="button" className="btn-primary" onClick={saveCriterion}>
                  <Icon icon={editingCrit !== null ? 'fa-check' : 'fa-circle-plus'} size={14} />
                  {editingCrit !== null ? 'Сохранить' : 'Добавить'}
                </button>
              )}
            </div>
          </div>
        </div>
      ) : null}

      <Toast toasts={toasts} remove={remove} />
    </>
  );
}
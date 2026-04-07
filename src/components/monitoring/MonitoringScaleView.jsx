import React, { useState, useEffect } from 'react';
import {
  Plus, Pencil, Trash2, Check, X, ChevronRight,
  Upload, FileX, AlertTriangle, CheckCircle2,
  ArrowLeft, ArrowRight, Save, Loader2, Info
} from 'lucide-react';

// ─── Toast ────────────────────────────────────────────────────────────────────
const Toast = ({ toasts, remove }) => (
  <div style={{ position: 'fixed', bottom: 24, right: 24, zIndex: 9999, display: 'flex', flexDirection: 'column', gap: 8 }}>
    {toasts.map(t => (
      <div key={t.id} style={{
        display: 'flex', alignItems: 'flex-start', gap: 10,
        padding: '10px 14px', borderRadius: 10,
        background: t.type === 'error' ? '#fef2f2' : '#f0fdf4',
        border: `1px solid ${t.type === 'error' ? '#fecaca' : '#bbf7d0'}`,
        boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
        maxWidth: 340, fontSize: 13,
        color: t.type === 'error' ? '#991b1b' : '#166534',
        animation: 'slideIn .2s ease',
      }}>
        {t.type === 'error'
          ? <AlertTriangle size={15} style={{ marginTop: 1, flexShrink: 0 }} />
          : <CheckCircle2 size={15} style={{ marginTop: 1, flexShrink: 0 }} />}
        <span style={{ flex: 1 }}>{t.message}</span>
        <button onClick={() => remove(t.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'inherit', opacity: 0.6, lineHeight: 1 }}>
          <X size={14} />
        </button>
      </div>
    ))}
  </div>
);

const useToast = () => {
  const [toasts, setToasts] = useState([]);
  const show = (message, type = 'success') => {
    const id = Date.now();
    setToasts(p => [...p, { id, message, type }]);
    setTimeout(() => setToasts(p => p.filter(t => t.id !== id)), 4500);
  };
  const remove = id => setToasts(p => p.filter(t => t.id !== id));
  return { toasts, show, remove };
};

// ─── Step indicator ───────────────────────────────────────────────────────────
const STEPS = ['Основное', 'Вес и тип', 'Описание'];
const StepBar = ({ current }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 24 }}>
    {STEPS.map((label, i) => {
      const done = i < current;
      const active = i === current;
      return (
        <React.Fragment key={i}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            <div style={{
              width: 28, height: 28, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 12, fontWeight: 500,
              background: done ? '#16a34a' : active ? '#2563eb' : '#f3f4f6',
              color: done || active ? '#fff' : '#6b7280',
              border: active ? '2px solid #bfdbfe' : done ? '2px solid #bbf7d0' : '2px solid #e5e7eb',
              transition: 'all .2s',
            }}>
              {done ? <Check size={13} /> : i + 1}
            </div>
            <span style={{ fontSize: 11, fontWeight: active ? 500 : 400, color: active ? '#2563eb' : done ? '#16a34a' : '#9ca3af', whiteSpace: 'nowrap' }}>
              {label}
            </span>
          </div>
          {i < STEPS.length - 1 && (
            <div style={{ flex: 1, height: 1, background: done ? '#bbf7d0' : '#e5e7eb', margin: '0 4px', marginBottom: 20, transition: 'background .2s' }} />
          )}
        </React.Fragment>
      );
    })}
  </div>
);

// ─── Empty state ──────────────────────────────────────────────────────────────
const Empty = ({ text }) => (
  <div style={{ textAlign: 'center', padding: '32px 16px', color: '#9ca3af' }}>
    <div style={{ fontSize: 28, marginBottom: 8, opacity: 0.4 }}>○</div>
    <p style={{ margin: 0, fontSize: 13 }}>{text}</p>
  </div>
);

// ─── Main Section ─────────────────────────────────────────────────────────────
const MonitoringScaleSection = ({ initialDirections = [], onSave }) => {
  const { toasts, show, remove } = useToast();

  const [directions, setDirections] = useState(initialDirections);
  const [selectedDir, setSelectedDir] = useState(0);

  // direction form
  const [dirName, setDirName] = useState('');
  const [dirFile, setDirFile] = useState(true);
  const [editingDir, setEditingDir] = useState(null);

  // criterion wizard
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

  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('directions');

  const totalWeight = idx =>
    (directions[idx]?.criteria || [])
      .filter(c => !c.isCritical)
      .reduce((s, c) => s + Number(c.weight), 0);

  // ── Direction handlers ──
  const submitDirection = () => {
    if (!dirName.trim()) { show('Введите название направления.', 'error'); return; }
    if (editingDir !== null) {
      setDirections(d => d.map((dir, i) => i === editingDir ? { ...dir, name: dirName.trim(), hasFileUpload: dirFile } : dir));
      show('Направление обновлено.');
    } else {
      setDirections(d => [...d, { name: dirName.trim(), hasFileUpload: dirFile, criteria: [] }]);
      setSelectedDir(directions.length);
      show('Направление добавлено.');
    }
    setDirName(''); setDirFile(true); setEditingDir(null);
  };

  const startEditDir = i => {
    setDirName(directions[i].name);
    setDirFile(directions[i].hasFileUpload);
    setEditingDir(i);
    setSelectedDir(i);
  };

  const deleteDir = i => {
    setDirections(d => d.filter((_, j) => j !== i));
    setSelectedDir(s => (s >= i && s > 0 ? s - 1 : 0));
    if (editingDir === i) { setDirName(''); setDirFile(true); setEditingDir(null); }
  };

  // ── Wizard handlers ──
  const openWizard = (critIdx = null) => {
    if (critIdx !== null) {
      const c = directions[selectedDir].criteria[critIdx];
      setCritName(c.name); setCritCritical(c.isCritical);
      setCritWeight(c.isCritical ? '' : String(c.weight));
      setCritValue(c.value === 'Нет описания' ? '' : c.value);
      if (c.deficiency) {
        setCritHasDef(true); setDefWeight(String(c.deficiency.weight));
        setDefDesc(c.deficiency.description === 'Нет описания' ? '' : c.deficiency.description);
      } else { setCritHasDef(false); setDefWeight(''); setDefDesc(''); }
      setEditingCrit(critIdx);
    } else {
      setCritName(''); setCritCritical(false); setCritWeight('');
      setCritValue(''); setCritHasDef(false); setDefWeight(''); setDefDesc('');
      setEditingCrit(null);
    }
    setWizardStep(0); setWizardOpen(true);
  };

  const closeWizard = () => { setWizardOpen(false); setEditingCrit(null); };

  const nextStep = () => {
    if (wizardStep === 0) {
      if (!critName.trim()) { show('Введите название критерия.', 'error'); return; }
    }
    if (wizardStep === 1) {
      if (!critCritical) {
        if (!critWeight) { show('Введите вес критерия.', 'error'); return; }
        const w = Number(critWeight);
        if (isNaN(w) || w <= 0) { show('Вес должен быть положительным числом.', 'error'); return; }
        const used = totalWeight(selectedDir) - (editingCrit !== null && !directions[selectedDir].criteria[editingCrit]?.isCritical ? directions[selectedDir].criteria[editingCrit]?.weight || 0 : 0);
        if (used + w > 100) { show(`Превышен лимит 100. Доступно: ${100 - used}`, 'error'); return; }
      }
    }
    setWizardStep(s => s + 1);
  };

  const saveCriterion = () => {
    if (critHasDef) {
      const dw = Number(defWeight);
      if (isNaN(dw) || dw <= 0 || dw > Number(critWeight)) {
        show('Вес недочёта должен быть > 0 и ≤ веса критерия.', 'error'); return;
      }
    }
    const obj = {
      name: critName.trim(),
      weight: critCritical ? 0 : Number(critWeight),
      isCritical: critCritical,
      value: critValue.trim() || 'Нет описания',
      deficiency: critHasDef ? { weight: Number(defWeight), description: defDesc.trim() || 'Нет описания' } : null,
    };
    setDirections(dirs => dirs.map((dir, i) => {
      if (i !== selectedDir) return dir;
      const criteria = editingCrit !== null
        ? dir.criteria.map((c, j) => j === editingCrit ? obj : c)
        : [...dir.criteria, obj];
      return { ...dir, criteria };
    }));
    show(editingCrit !== null ? 'Критерий обновлён.' : 'Критерий добавлен.');
    closeWizard();
  };

  const deleteCrit = i => {
    setDirections(dirs => dirs.map((dir, j) => j !== selectedDir ? dir : { ...dir, criteria: dir.criteria.filter((_, k) => k !== i) }));
  };

  // ── Save ──
  const handleSave = async () => {
    const bad = directions.find((dir, i) => dir.criteria.some(c => !c.isCritical) && totalWeight(i) !== 100);
    if (bad) {
      const i = directions.indexOf(bad);
      show(`"${bad.name}": сумма весов должна быть 100 (сейчас ${totalWeight(i)}).`, 'error'); return;
    }
    setIsLoading(true);
    try { await onSave?.(directions); show('Сохранено успешно.'); }
    catch { show('Ошибка при сохранении.', 'error'); }
    finally { setIsLoading(false); }
  };

  const tw = totalWeight(selectedDir);
  const hasCriteria = directions[selectedDir]?.criteria?.length > 0;
  const hasNonCritical = directions[selectedDir]?.criteria?.some(c => !c.isCritical);

  return (
    <>
      <style>{`
        @keyframes slideIn { from { opacity:0; transform: translateY(6px); } to { opacity:1; transform: translateY(0); } }
        .tab-btn { padding: 8px 16px; border: none; background: none; cursor: pointer; font-size: 14px; border-bottom: 2px solid transparent; color: #6b7280; font-weight: 400; transition: all .15s; }
        .tab-btn:hover { color: #111827; }
        .tab-btn.active { color: #2563eb; border-bottom-color: #2563eb; font-weight: 500; }
        .dir-item { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-radius: 8px; cursor: pointer; transition: background .12s; border: 1px solid transparent; }
        .dir-item:hover { background: #f9fafb; }
        .dir-item.selected { background: #eff6ff; border-color: #bfdbfe; }
        .icon-btn { display: flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 6px; border: none; background: none; cursor: pointer; color: #9ca3af; transition: all .12s; }
        .icon-btn:hover { background: #f3f4f6; color: #374151; }
        .icon-btn.danger:hover { background: #fef2f2; color: #ef4444; }
        .crit-row { display: flex; align-items: flex-start; gap: 10; padding: 12px; border-radius: 8px; border: 1px solid #e5e7eb; background: #fff; animation: slideIn .15s ease; }
        .wiz-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 100; display: flex; align-items: center; justify-content: center; padding: 16px; }
        .wiz-card { background: #fff; border-radius: 16px; width: 100%; max-width: 520px; padding: 28px; box-shadow: 0 20px 60px rgba(0,0,0,.15); }
        input[type=text], input[type=number], textarea, select {
          width: 100%; padding: 9px 12px; border: 1px solid #d1d5db; border-radius: 8px;
          font-size: 14px; font-family: inherit; outline: none; transition: border .15s; box-sizing: border-box;
          background: #fff; color: #111827;
        }
        input:focus, textarea:focus, select:focus { border-color: #2563eb; box-shadow: 0 0 0 3px #eff6ff; }
        textarea { resize: vertical; min-height: 80px; line-height: 1.6; }
        .field-label { display: block; font-size: 13px; font-weight: 500; color: #374151; margin-bottom: 6px; }
        .chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 500; }
        .btn-primary { display: inline-flex; align-items: center; gap: 6px; padding: 9px 18px; border-radius: 8px; border: none; background: #2563eb; color: #fff; font-size: 14px; font-weight: 500; cursor: pointer; transition: background .15s; }
        .btn-primary:hover { background: #1d4ed8; }
        .btn-primary:disabled { background: #93c5fd; cursor: not-allowed; }
        .btn-ghost { display: inline-flex; align-items: center; gap: 6px; padding: 9px 18px; border-radius: 8px; border: 1px solid #e5e7eb; background: #fff; color: #374151; font-size: 14px; cursor: pointer; transition: all .15s; }
        .btn-ghost:hover { background: #f9fafb; }
        .toggle-row { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 8px; background: #f9fafb; border: 1px solid #e5e7eb; cursor: pointer; user-select: none; }
        .toggle-row input { cursor: pointer; accent-color: #2563eb; width: 16px; height: 16px; }
      `}</style>

      <div style={{ fontFamily: 'system-ui, -apple-system, sans-serif', maxWidth: 860, margin: '0 auto', padding: '0 0 40px' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24, paddingBottom: 16, borderBottom: '1px solid #e5e7eb' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600, color: '#111827' }}>Мониторинговая шкала</h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#6b7280' }}>
              {directions.length} {directions.length === 1 ? 'направление' : 'направлений'} &middot; {directions.reduce((s, d) => s + d.criteria.length, 0)} критериев
            </p>
          </div>
          <button className="btn-primary" onClick={handleSave} disabled={isLoading}>
            {isLoading ? <><Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} /> Сохранение...</> : <><Save size={15} /> Сохранить</>}
          </button>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', borderBottom: '1px solid #e5e7eb', marginBottom: 24 }}>
          {['directions', 'criteria'].map(tab => (
            <button key={tab} className={`tab-btn${activeTab === tab ? ' active' : ''}`} onClick={() => setActiveTab(tab)}>
              {tab === 'directions' ? 'Направления' : 'Критерии'}
            </button>
          ))}
        </div>

        {/* ── Directions tab ── */}
        {activeTab === 'directions' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
            {/* Form */}
            <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 20 }}>
              <h2 style={{ margin: '0 0 16px', fontSize: 15, fontWeight: 600, color: '#111827' }}>
                {editingDir !== null ? 'Редактировать направление' : 'Новое направление'}
              </h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div>
                  <label className="field-label">Название</label>
                  <input type="text" value={dirName} onChange={e => setDirName(e.target.value)}
                    placeholder="Введите название..." onKeyDown={e => e.key === 'Enter' && submitDirection()} />
                </div>
                <label className="toggle-row" style={{ marginTop: 4 }}>
                  <input type="checkbox" checked={dirFile} onChange={e => setDirFile(e.target.checked)} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 500, color: '#374151' }}>Требуется загрузка файла</div>
                    <div style={{ fontSize: 12, color: '#6b7280' }}>Участник должен прикрепить документ</div>
                  </div>
                </label>
                <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                  <button className="btn-primary" style={{ flex: 1 }} onClick={submitDirection}>
                    {editingDir !== null ? <><Check size={14} /> Сохранить</> : <><Plus size={14} /> Добавить</>}
                  </button>
                  {editingDir !== null && (
                    <button className="btn-ghost" onClick={() => { setDirName(''); setDirFile(true); setEditingDir(null); }}>
                      <X size={14} />
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* List */}
            <div>
              <h2 style={{ margin: '0 0 12px', fontSize: 15, fontWeight: 600, color: '#111827' }}>Список направлений</h2>
              {directions.length === 0 ? (
                <Empty text="Добавьте первое направление" />
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {directions.map((dir, i) => (
                    <div key={i} className={`dir-item${selectedDir === i ? ' selected' : ''}`} onClick={() => setSelectedDir(i)}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                        {dir.hasFileUpload
                          ? <Upload size={14} style={{ color: '#2563eb', flexShrink: 0 }} />
                          : <FileX size={14} style={{ color: '#9ca3af', flexShrink: 0 }} />}
                        <span style={{ fontSize: 13, fontWeight: 500, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{dir.name}</span>
                        <span style={{ fontSize: 11, color: '#6b7280', flexShrink: 0 }}>{dir.criteria.length} кр.</span>
                      </div>
                      <div style={{ display: 'flex', gap: 2, flexShrink: 0 }}>
                        <button className="icon-btn" onClick={e => { e.stopPropagation(); startEditDir(i); }} title="Редактировать"><Pencil size={13} /></button>
                        <button className="icon-btn danger" onClick={e => { e.stopPropagation(); deleteDir(i); }} title="Удалить"><Trash2 size={13} /></button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Criteria tab ── */}
        {activeTab === 'criteria' && (
          <div>
            {/* Direction selector */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, minWidth: 0 }}>
                <label className="field-label" style={{ margin: 0, whiteSpace: 'nowrap' }}>Направление:</label>
                <select value={selectedDir} onChange={e => setSelectedDir(Number(e.target.value))}
                  disabled={directions.length === 0} style={{ maxWidth: 280 }}>
                  {directions.length === 0
                    ? <option>— нет направлений —</option>
                    : directions.map((d, i) => <option key={i} value={i}>{d.name}</option>)}
                </select>
              </div>
              <button className="btn-primary" disabled={directions.length === 0} onClick={() => openWizard()}>
                <Plus size={14} /> Добавить критерий
              </button>
            </div>

            {/* Weight bar */}
            {directions.length > 0 && hasNonCritical && (
              <div style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 8, background: tw === 100 ? '#f0fdf4' : tw > 100 ? '#fef2f2' : '#fffbeb', border: `1px solid ${tw === 100 ? '#bbf7d0' : tw > 100 ? '#fecaca' : '#fde68a'}` }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 500, color: tw === 100 ? '#166534' : tw > 100 ? '#991b1b' : '#92400e' }}>
                    Сумма весов некритических критериев
                  </span>
                  <span style={{ fontSize: 13, fontWeight: 600, color: tw === 100 ? '#16a34a' : tw > 100 ? '#ef4444' : '#d97706' }}>{tw}/100</span>
                </div>
                <div style={{ height: 4, borderRadius: 999, background: '#e5e7eb', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${Math.min(tw, 100)}%`, borderRadius: 999, background: tw === 100 ? '#16a34a' : tw > 100 ? '#ef4444' : '#f59e0b', transition: 'width .3s, background .3s' }} />
                </div>
              </div>
            )}

            {/* Criteria list */}
            {!hasCriteria ? (
              <Empty text={directions.length === 0 ? 'Сначала добавьте направление' : 'Нет критериев — нажмите «Добавить критерий»'} />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {directions[selectedDir]?.criteria.map((c, i) => (
                  <div key={i} className="crit-row" style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: 12, borderRadius: 8, border: '1px solid #e5e7eb', background: '#fff' }}>
                    <div style={{ marginTop: 2, flexShrink: 0 }}>
                      {c.isCritical
                        ? <AlertTriangle size={15} style={{ color: '#f59e0b' }} />
                        : <CheckCircle2 size={15} style={{ color: '#10b981' }} />}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 14, fontWeight: 500, color: '#111827' }}>{c.name}</span>
                        {c.isCritical
                          ? <span className="chip" style={{ background: '#fffbeb', color: '#92400e', border: '1px solid #fde68a' }}>критичный</span>
                          : <span className="chip" style={{ background: '#f0fdf4', color: '#166534', border: '1px solid #bbf7d0' }}>{c.weight}%</span>}
                        {c.deficiency && (
                          <span className="chip" style={{ background: '#fff7ed', color: '#9a3412', border: '1px solid #fed7aa' }}>недочёт {c.deficiency.weight}%</span>
                        )}
                      </div>
                      {c.value && c.value !== 'Нет описания' && (
                        <p style={{ margin: 0, fontSize: 12, color: '#6b7280', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{c.value}</p>
                      )}
                      {c.deficiency?.description && c.deficiency.description !== 'Нет описания' && (
                        <p style={{ margin: '4px 0 0', fontSize: 12, color: '#9a3412', lineHeight: 1.5, fontStyle: 'italic' }}>
                          Недочёт: {c.deficiency.description}
                        </p>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: 2, flexShrink: 0 }}>
                      <button className="icon-btn" onClick={() => openWizard(i)} title="Редактировать"><Pencil size={13} /></button>
                      <button className="icon-btn danger" onClick={() => deleteCrit(i)} title="Удалить"><Trash2 size={13} /></button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Criterion Wizard Modal ── */}
      {wizardOpen && (
        <div className="wiz-backdrop" onClick={e => e.target === e.currentTarget && closeWizard()}>
          <div className="wiz-card">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
              <h2 style={{ margin: 0, fontSize: 17, fontWeight: 600, color: '#111827' }}>
                {editingCrit !== null ? 'Редактировать критерий' : 'Новый критерий'}
              </h2>
              <button className="icon-btn" onClick={closeWizard}><X size={16} /></button>
            </div>

            <StepBar current={wizardStep} />

            {/* Step 0 — Name */}
            {wizardStep === 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div>
                  <label className="field-label">Название критерия <span style={{ color: '#ef4444' }}>*</span></label>
                  <input type="text" value={critName} onChange={e => setCritName(e.target.value)}
                    placeholder="Например: Полнота ответа..." autoFocus
                    onKeyDown={e => e.key === 'Enter' && nextStep()} />
                </div>
                <div style={{ padding: '10px 12px', borderRadius: 8, background: '#f8fafc', border: '1px solid #e5e7eb', display: 'flex', gap: 8 }}>
                  <Info size={14} style={{ color: '#6b7280', marginTop: 1, flexShrink: 0 }} />
                  <p style={{ margin: 0, fontSize: 12, color: '#6b7280', lineHeight: 1.6 }}>
                    Название должно чётко описывать, что именно оценивается. На следующем шаге вы выберете тип и вес.
                  </p>
                </div>
              </div>
            )}

            {/* Step 1 — Weight & type */}
            {wizardStep === 1 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <label className="toggle-row" style={{ cursor: 'pointer' }}>
                  <input type="checkbox" checked={critCritical}
                    onChange={e => { setCritCritical(e.target.checked); if (e.target.checked) setCritWeight(''); }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 500, color: '#374151' }}>Критичный критерий</div>
                    <div style={{ fontSize: 12, color: '#6b7280' }}>При ошибке автоматически выставляет оценку 0, без учёта веса</div>
                  </div>
                </label>

                {!critCritical && (
                  <div>
                    <label className="field-label">
                      Вес <span style={{ color: '#ef4444' }}>*</span>
                      <span style={{ fontWeight: 400, color: '#6b7280' }}> — доступно: {100 - totalWeight(selectedDir) + (editingCrit !== null && !directions[selectedDir].criteria[editingCrit]?.isCritical ? directions[selectedDir].criteria[editingCrit]?.weight || 0 : 0)}%</span>
                    </label>
                    <input type="number" value={critWeight} onChange={e => setCritWeight(e.target.value)}
                      placeholder="0 – 100" min="1" max="100" autoFocus />
                  </div>
                )}

                <label className="toggle-row" style={{ cursor: 'pointer' }}>
                  <input type="checkbox" checked={critHasDef}
                    onChange={e => { setCritHasDef(e.target.checked); if (!e.target.checked) { setDefWeight(''); setDefDesc(''); } }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 500, color: '#374151' }}>Есть недочёт</div>
                    <div style={{ fontSize: 12, color: '#6b7280' }}>Частичная ошибка с пониженным весом</div>
                  </div>
                </label>

                {critHasDef && (
                  <div style={{ paddingLeft: 12, borderLeft: '2px solid #fed7aa', display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <div>
                      <label className="field-label">Вес недочёта</label>
                      <input type="number" value={defWeight} onChange={e => setDefWeight(e.target.value)}
                        placeholder={`1 – ${critWeight || '...'}`} min="1" />
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Step 2 — Description */}
            {wizardStep === 2 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div>
                  <label className="field-label">Описание критерия</label>
                  <textarea value={critValue} onChange={e => setCritValue(e.target.value)}
                    placeholder="Подробное описание того, что проверяется и как оценивается..."
                    style={{ minHeight: 120 }} autoFocus />
                  <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>Необязательно, но рекомендуется для длинных критериев</div>
                </div>

                {critHasDef && (
                  <div style={{ paddingLeft: 12, borderLeft: '2px solid #fed7aa' }}>
                    <label className="field-label">Описание недочёта</label>
                    <textarea value={defDesc} onChange={e => setDefDesc(e.target.value)}
                      placeholder="В чём именно выражается недочёт..."
                      style={{ minHeight: 80 }} />
                  </div>
                )}
              </div>
            )}

            {/* Footer */}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24, paddingTop: 16, borderTop: '1px solid #e5e7eb' }}>
              <button className="btn-ghost" onClick={wizardStep === 0 ? closeWizard : () => setWizardStep(s => s - 1)}>
                {wizardStep === 0 ? <><X size={14} /> Отмена</> : <><ArrowLeft size={14} /> Назад</>}
              </button>
              {wizardStep < STEPS.length - 1 ? (
                <button className="btn-primary" onClick={nextStep}>
                  Далее <ArrowRight size={14} />
                </button>
              ) : (
                <button className="btn-primary" onClick={saveCriterion}>
                  <Check size={14} /> {editingCrit !== null ? 'Сохранить' : 'Добавить'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      <Toast toasts={toasts} remove={remove} />

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </>
  );
};

export default MonitoringScaleSection;
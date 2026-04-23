import React, { useEffect, useRef } from 'react';

/**
 * DisputeModal — модалка запроса на переоценку.
 * Полностью inline-стили для независимости от CSS-скопа.
 */
const DisputeModal = ({
  selectedEvaluation,
  disputeText,
  setDisputeText,
  handleSubmitDispute,
  isLoading,
  setShowDisputeModal,
}) => {
  const textareaRef = useRef(null);
  const MAX_CHARS = 500;
  const charCount = (disputeText || '').length;
  const charLeft = MAX_CHARS - charCount;
  const isOverLimit = charCount > MAX_CHARS;
  const canSubmit = !isLoading && charCount > 0 && !isOverLimit;

  const score = selectedEvaluation?.score;
  const scoreNum = score != null ? Number(score) : null;
  const scoreColor =
    scoreNum == null ? '#6b7280'
    : scoreNum >= 90  ? '#16a34a'
    : scoreNum >= 60  ? '#d97706'
    : '#dc2626';

  /* Автофокус */
  useEffect(() => {
    const t = setTimeout(() => textareaRef.current?.focus(), 80);
    return () => clearTimeout(t);
  }, []);

  /* Закрытие по Escape */
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !isLoading) handleClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isLoading]);

  const handleClose = () => {
    if (isLoading) return;
    setShowDisputeModal(false);
    setDisputeText('');
  };

  const onBackdrop = (e) => {
    if (e.target === e.currentTarget) handleClose();
  };

  /* ── styles ── */
  const S = {
    backdrop: {
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.45)',
      backdropFilter: 'blur(4px)',
      WebkitBackdropFilter: 'blur(4px)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 9999,
      padding: 24,
      animation: 'dmFadeIn 0.15s ease',
    },
    box: {
      background: '#ffffff',
      borderRadius: 14,
      width: '100%',
      maxWidth: 460,
      boxShadow: '0 20px 60px rgba(0,0,0,0.18), 0 4px 16px rgba(0,0,0,0.1)',
      border: '1px solid #e5e7eb',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      animation: 'dmSlideUp 0.18s cubic-bezier(0.16,1,0.3,1)',
    },
    header: {
      padding: '16px 20px',
      borderBottom: '1px solid #f3f4f6',
      display: 'flex',
      alignItems: 'flex-start',
      justifyContent: 'space-between',
      gap: 12,
    },
    headerIcon: {
      width: 32,
      height: 32,
      borderRadius: 8,
      background: '#fffbeb',
      color: '#d97706',
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontSize: 14,
      flexShrink: 0,
    },
    headerTitle: {
      margin: 0,
      fontSize: 15,
      fontWeight: 600,
      color: '#111827',
      lineHeight: 1.3,
    },
    headerSub: {
      fontSize: 12,
      color: '#6b7280',
      marginTop: 2,
    },
    closeBtn: {
      width: 28,
      height: 28,
      border: 'none',
      background: 'transparent',
      borderRadius: 6,
      cursor: 'pointer',
      color: '#9ca3af',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontSize: 13,
      flexShrink: 0,
      transition: 'background 0.15s, color 0.15s',
    },
    body: {
      padding: '18px 20px',
      display: 'flex',
      flexDirection: 'column',
      gap: 14,
    },
    infoGrid: {
      background: '#f9fafb',
      border: '1px solid #e5e7eb',
      borderRadius: 10,
      padding: '12px 14px',
      display: 'grid',
      gridTemplateColumns: '1fr 1fr',
      gap: '10px 20px',
    },
    infoLabel: {
      fontSize: 11,
      fontWeight: 600,
      color: '#9ca3af',
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
      marginBottom: 2,
    },
    infoValue: {
      fontSize: 13,
      fontWeight: 500,
      color: '#111827',
    },
    notice: {
      display: 'flex',
      gap: 8,
      alignItems: 'flex-start',
      background: '#fffbeb',
      border: '1px solid #fde68a',
      borderRadius: 8,
      padding: '10px 12px',
      fontSize: 12,
      color: '#92400e',
      lineHeight: 1.5,
    },
    fieldLabel: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 6,
      fontSize: 12,
      fontWeight: 600,
      color: '#374151',
      letterSpacing: '0.01em',
    },
    charCounter: (over, warn) => ({
      fontFamily: 'monospace',
      fontSize: 11,
      color: over ? '#dc2626' : warn ? '#d97706' : '#9ca3af',
      transition: 'color 0.15s',
    }),
    textarea: (over) => ({
      width: '100%',
      padding: '10px 12px',
      background: '#ffffff',
      border: `1px solid ${over ? '#fca5a5' : '#d1d5db'}`,
      borderRadius: 8,
      fontFamily: 'inherit',
      fontSize: 13,
      color: '#111827',
      resize: 'vertical',
      minHeight: 110,
      maxHeight: 240,
      outline: 'none',
      boxShadow: over ? '0 0 0 3px #fee2e2' : 'none',
      transition: 'border-color 0.15s, box-shadow 0.15s',
      lineHeight: 1.6,
      boxSizing: 'border-box',
    }),
    errorText: {
      fontSize: 12,
      color: '#dc2626',
      marginTop: 4,
      display: 'flex',
      alignItems: 'center',
      gap: 4,
    },
    footer: {
      padding: '14px 20px',
      borderTop: '1px solid #f3f4f6',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'flex-end',
      gap: 8,
      background: '#f9fafb',
    },
    btnCancel: {
      padding: '8px 16px',
      border: '1px solid #d1d5db',
      borderRadius: 8,
      background: '#ffffff',
      color: '#374151',
      fontSize: 13,
      fontWeight: 500,
      cursor: 'pointer',
      fontFamily: 'inherit',
      transition: 'background 0.15s',
    },
    btnSubmit: (can) => ({
      padding: '8px 18px',
      border: '1px solid #fde68a',
      borderRadius: 8,
      background: can ? '#f59e0b' : '#fde68a',
      color: can ? '#ffffff' : '#a16207',
      fontSize: 13,
      fontWeight: 600,
      cursor: can ? 'pointer' : 'not-allowed',
      fontFamily: 'inherit',
      display: 'inline-flex',
      alignItems: 'center',
      gap: 6,
      minWidth: 140,
      justifyContent: 'center',
      transition: 'background 0.15s, opacity 0.15s',
      opacity: can ? 1 : 0.7,
    }),
  };

  return (
    <>
      {/* Keyframe injection */}
      <style>{`
        @keyframes dmFadeIn { from { opacity:0 } to { opacity:1 } }
        @keyframes dmSlideUp { from { opacity:0; transform:translateY(16px) scale(0.97) } to { opacity:1; transform:translateY(0) scale(1) } }
      `}</style>

      <div style={S.backdrop} onClick={onBackdrop}>
        <div style={S.box}>

          {/* Header */}
          <div style={S.header}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              <span style={S.headerIcon}>
                <i className="fas fa-flag" />
              </span>
              <div>
                <h2 style={S.headerTitle}>Запрос на переоценку</h2>
                {selectedEvaluation?.id && (
                  <div style={S.headerSub}>Звонок #{selectedEvaluation.id}</div>
                )}
              </div>
            </div>
            <button
              style={S.closeBtn}
              onClick={handleClose}
              disabled={isLoading}
              aria-label="Закрыть"
              onMouseEnter={e => { e.currentTarget.style.background = '#f3f4f6'; e.currentTarget.style.color = '#374151'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#9ca3af'; }}
            >
              <i className="fas fa-times" />
            </button>
          </div>

          {/* Body */}
          <div style={S.body}>

            {/* Инфо-карточка */}
            <div style={S.infoGrid}>
              <InfoCell label="Телефон" value={selectedEvaluation?.phone_number || '—'} />
              <InfoCell
                label="Балл"
                value={
                  scoreNum != null
                    ? <span style={{ color: scoreColor, fontWeight: 700, fontFamily: 'monospace', fontSize: 15 }}>{scoreNum.toFixed(0)}</span>
                    : '—'
                }
              />
              {selectedEvaluation?.month && (
                <InfoCell label="Месяц" value={selectedEvaluation.month} />
              )}
              {selectedEvaluation?.evaluator && (
                <InfoCell label="Оценщик" value={selectedEvaluation.evaluator} />
              )}
            </div>

            {/* Подсказка */}
            <div style={S.notice}>
              <i className="fas fa-info-circle" style={{ marginTop: 1, flexShrink: 0 }} />
              <span>Опишите конкретные критерии, с которыми вы не согласны. Запрос будет передан руководителю.</span>
            </div>

            {/* Textarea */}
            <div>
              <label style={S.fieldLabel}>
                <span>Комментарий к запросу</span>
                <span style={S.charCounter(isOverLimit, charLeft <= 50)}>
                  {charCount}&nbsp;/&nbsp;{MAX_CHARS}
                </span>
              </label>
              <textarea
                ref={textareaRef}
                style={S.textarea(isOverLimit)}
                value={disputeText}
                onChange={(e) => setDisputeText(e.target.value)}
                placeholder="Опишите, с какими критериями вы не согласны и почему..."
                rows={5}
                disabled={isLoading}
                onFocus={e => { if (!isOverLimit) { e.target.style.borderColor = '#2563eb'; e.target.style.boxShadow = '0 0 0 3px #eff6ff'; } }}
                onBlur={e => { e.target.style.borderColor = isOverLimit ? '#fca5a5' : '#d1d5db'; e.target.style.boxShadow = isOverLimit ? '0 0 0 3px #fee2e2' : 'none'; }}
              />
              {isOverLimit && (
                <div style={S.errorText}>
                  <i className="fas fa-exclamation-circle" />
                  Превышен лимит на {Math.abs(charLeft)} симв.
                </div>
              )}
            </div>
          </div>

          {/* Footer */}
          <div style={S.footer}>
            <button
              style={S.btnCancel}
              onClick={handleClose}
              disabled={isLoading}
              onMouseEnter={e => { e.currentTarget.style.background = '#f9fafb'; }}
              onMouseLeave={e => { e.currentTarget.style.background = '#ffffff'; }}
            >
              Отмена
            </button>
            <button
              style={S.btnSubmit(canSubmit)}
              onClick={handleSubmitDispute}
              disabled={!canSubmit}
              onMouseEnter={e => { if (canSubmit) e.currentTarget.style.background = '#d97706'; }}
              onMouseLeave={e => { if (canSubmit) e.currentTarget.style.background = '#f59e0b'; }}
            >
              {isLoading ? (
                <>
                  <span style={{
                    display: 'inline-block', width: 13, height: 13,
                    border: '2px solid rgba(255,255,255,0.35)',
                    borderTopColor: '#fff', borderRadius: '50%',
                    animation: 'spin 0.6s linear infinite',
                  }} />
                  Отправка...
                </>
              ) : (
                <>
                  <i className="fas fa-paper-plane" />
                  Отправить запрос
                </>
              )}
            </button>
          </div>

        </div>
      </div>
    </>
  );
};

const InfoCell = ({ label, value }) => (
  <div>
    <div style={{ fontSize: 11, fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 2 }}>
      {label}
    </div>
    <div style={{ fontSize: 13, fontWeight: 500, color: '#111827' }}>{value}</div>
  </div>
);

export default DisputeModal;

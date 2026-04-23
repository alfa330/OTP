import React, { useEffect, useRef, useState } from 'react';

/**
 * DisputeModal — окно запроса на переоценку.
 * Стилизовано под дизайн-систему сайта (CSS-переменные из call_evaluation/styles.css).
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

  /* Автофокус textarea при открытии */
  useEffect(() => {
    const t = setTimeout(() => textareaRef.current?.focus(), 80);
    return () => clearTimeout(t);
  }, []);

  /* Закрытие по Escape */
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !isLoading) {
        setShowDisputeModal(false);
        setDisputeText('');
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isLoading, setShowDisputeModal, setDisputeText]);

  const score = selectedEvaluation?.score;
  const scoreNum = score != null ? Number(score) : null;
  const scoreColor =
    scoreNum == null
      ? 'var(--text-2)'
      : scoreNum >= 90
      ? 'var(--green)'
      : scoreNum >= 60
      ? 'var(--amber)'
      : 'var(--red)';

  const canSubmit = !isLoading && charCount > 0 && !isOverLimit;

  const handleClose = () => {
    if (isLoading) return;
    setShowDisputeModal(false);
    setDisputeText('');
  };

  const handleBackdropClick = (e) => {
    if (e.target === e.currentTarget) handleClose();
  };

  return (
    <div
      className="modal-backdrop"
      onClick={handleBackdropClick}
      role="dialog"
      aria-modal="true"
      aria-labelledby="dispute-modal-title"
      style={{ zIndex: 9999 }}
    >
      <div className="modal request-modal" style={{ maxWidth: 460 }}>

        {/* ── Header ── */}
        <div className="modal-header">
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 8,
                  background: 'var(--amber-light)',
                  color: 'var(--amber)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 13,
                  flexShrink: 0,
                }}
              >
                <i className="fas fa-flag" />
              </span>
              <h2 id="dispute-modal-title" style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>
                Запрос на переоценку
              </h2>
            </div>
            {selectedEvaluation?.id && (
              <div className="modal-header-sub" style={{ marginTop: 3, paddingLeft: 36 }}>
                Звонок&nbsp;#{selectedEvaluation.id}
              </div>
            )}
          </div>
          <button
            className="close-btn"
            onClick={handleClose}
            disabled={isLoading}
            aria-label="Закрыть"
          >
            <i className="fas fa-times" />
          </button>
        </div>

        {/* ── Body ── */}
        <div className="modal-body" style={{ padding: '18px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* Карточка информации об оценке */}
          <div
            style={{
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              padding: '12px 14px',
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: '8px 16px',
            }}
          >
            <InfoRow label="Телефон" value={selectedEvaluation?.phone_number || '—'} />
            <InfoRow
              label="Балл"
              value={
                scoreNum != null ? (
                  <span style={{ color: scoreColor, fontWeight: 700, fontFamily: 'var(--font-mono)', fontSize: 14 }}>
                    {scoreNum.toFixed(0)}
                  </span>
                ) : (
                  '—'
                )
              }
            />
            {selectedEvaluation?.month && (
              <InfoRow label="Месяц" value={selectedEvaluation.month} />
            )}
            {selectedEvaluation?.evaluator && (
              <InfoRow label="Оценщик" value={selectedEvaluation.evaluator} />
            )}
          </div>

          {/* Заметка */}
          <div
            style={{
              display: 'flex',
              gap: 8,
              alignItems: 'flex-start',
              background: 'var(--amber-light)',
              border: '1px solid #fde68a',
              borderRadius: 'var(--radius)',
              padding: '10px 12px',
              fontSize: 12,
              color: 'var(--amber)',
              lineHeight: 1.5,
            }}
          >
            <i className="fas fa-info-circle" style={{ marginTop: 1, flexShrink: 0 }} />
            <span>
              Ваш запрос будет передан руководителю. Опишите конкретные критерии, с которыми вы не согласны.
            </span>
          </div>

          {/* Поле комментария */}
          <div>
            <label
              htmlFor="dispute-textarea"
              className="label"
              style={{ marginBottom: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
            >
              <span>Комментарий к запросу</span>
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 11,
                  color: isOverLimit ? 'var(--red)' : charLeft <= 50 ? 'var(--amber)' : 'var(--text-3)',
                  transition: 'color 150ms ease',
                }}
              >
                {charCount}&nbsp;/&nbsp;{MAX_CHARS}
              </span>
            </label>
            <textarea
              id="dispute-textarea"
              ref={textareaRef}
              className="textarea"
              value={disputeText}
              onChange={(e) => setDisputeText(e.target.value)}
              placeholder="Опишите, с какими критериями вы не согласны и почему..."
              rows={5}
              disabled={isLoading}
              style={{
                resize: 'vertical',
                minHeight: 110,
                maxHeight: 240,
                borderColor: isOverLimit ? 'var(--red)' : undefined,
                boxShadow: isOverLimit ? '0 0 0 3px var(--red-light)' : undefined,
              }}
            />
            {isOverLimit && (
              <div className="error-text" style={{ marginTop: 4 }}>
                <i className="fas fa-exclamation-circle" style={{ marginRight: 4 }} />
                Превышен лимит символов на {Math.abs(charLeft)}
              </div>
            )}
          </div>
        </div>

        {/* ── Footer ── */}
        <div className="modal-footer">
          <button
            className="btn btn-secondary"
            onClick={handleClose}
            disabled={isLoading}
          >
            Отмена
          </button>
          <button
            className="btn btn-amber"
            onClick={handleSubmitDispute}
            disabled={!canSubmit}
            style={{ minWidth: 130 }}
          >
            {isLoading ? (
              <>
                <span className="spinner" style={{ borderTopColor: 'var(--amber)', borderColor: 'rgba(0,0,0,0.15)' }} />
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
  );
};

/** Вспомогательный компонент — строка информации */
const InfoRow = ({ label, value }) => (
  <div>
    <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 2 }}>
      {label}
    </div>
    <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>{value}</div>
  </div>
);

export default DisputeModal;

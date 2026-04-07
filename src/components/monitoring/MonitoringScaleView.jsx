import React, { useState } from 'react';
import ToastContainer from '../common/ToastContainer';
import FaIcon from '../common/FaIcon';

const MonitoringScaleSection = ({ initialDirections, onSave }) => {
    const [directions, setDirections] = useState(initialDirections || []);
    const [newDirectionName, setNewDirectionName] = useState('');
    const [newDirectionFileUpload, setNewDirectionFileUpload] = useState(true);
    const [editingDirectionIndex, setEditingDirectionIndex] = useState(null);
    const [isEditingDirection, setIsEditingDirection] = useState(false);
    const [selectedDirectionIndex, setSelectedDirectionIndex] = useState(0);
    const [newCriterionName, setNewCriterionName] = useState('');
    const [newCriterionWeight, setNewCriterionWeight] = useState('');
    const [newCriterionValue, setNewCriterionValue] = useState('');
    const [newCriterionCritical, setNewCriterionCritical] = useState(false);
    const [editingCriterionIndex, setEditingCriterionIndex] = useState(null);
    const [isEditingCriterion, setIsEditingCriterion] = useState(false);
    const [activeTab, setActiveTab] = useState('directions');
    const [isLoading, setIsLoading] = useState(false);
    const [newCriterionHasDeficiency, setNewCriterionHasDeficiency] = useState(false);
    const [newDeficiencyWeight, setNewDeficiencyWeight] = useState('');
    const [newDeficiencyDescription, setNewDeficiencyDescription] = useState('');
    const [toasts, setToasts] = useState([]);

    const showToast = (message, type = 'success') => {
        const id = Date.now();
        setToasts(prev => [...prev, { id, message, type, closing: false }]);
        setTimeout(() => {
            setToasts(prev => prev.map(t => t.id === id ? { ...t, closing: true } : t));
            setTimeout(() => removeToast(id), 300);
        }, 5000);
    };

    const removeToast = (id) => {
        setToasts(prev => prev.filter(toast => toast.id !== id));
    };

    const totalWeight = (directionIndex) => {
        return directions[directionIndex]?.criteria
            .filter(criterion => !criterion.isCritical)
            .reduce((sum, c) => sum + Number(c.weight), 0) || 0;
    };

    const handleAddDirection = () => {
        if (!newDirectionName) {
            showToast('Пожалуйста, введите название направления.', 'error');
            return;
        }
        setDirections([...directions, { name: newDirectionName, hasFileUpload: newDirectionFileUpload, criteria: [] }]);
        setNewDirectionName('');
        setNewDirectionFileUpload(true);
        setIsEditingDirection(false);
        setEditingDirectionIndex(null);
        setSelectedDirectionIndex(directions.length);
    };

    const handleEditDirection = () => {
        if (!newDirectionName) {
            showToast('Пожалуйста, введите название направления.', 'error');
            return;
        }
        setDirections(directions.map((direction, index) =>
            index === editingDirectionIndex
                ? { ...direction, name: newDirectionName, hasFileUpload: newDirectionFileUpload }
                : direction
        ));
        setNewDirectionName('');
        setNewDirectionFileUpload(true);
        setIsEditingDirection(false);
        setEditingDirectionIndex(null);
    };

    const handleStartEditDirection = (index) => {
        setNewDirectionName(directions[index].name);
        setNewDirectionFileUpload(directions[index].hasFileUpload);
        setEditingDirectionIndex(index);
        setIsEditingDirection(true);
        setSelectedDirectionIndex(index);
    };

    const handleDeleteDirection = (index) => {
        setDirections(directions.filter((_, i) => i !== index));
        if (selectedDirectionIndex === index) {
            setSelectedDirectionIndex(directions.length - 1 > 0 ? 0 : 0);
        }
        if (editingDirectionIndex === index) {
            setNewDirectionName('');
            setNewDirectionFileUpload(true);
            setIsEditingDirection(false);
            setEditingDirectionIndex(null);
        }
    };

    const handleAddCriterion = () => {
        if (!newCriterionName) {
            showToast('Пожалуйста, введите название критерия.', 'error');
            return;
        }
        if (!newCriterionCritical) {
            if (!newCriterionWeight) {
                showToast('Пожалуйста, введите вес для некритического критерия.', 'error');
                return;
            }
            const weight = Number(newCriterionWeight);
            if (isNaN(weight) || weight <= 0) {
                showToast('Вес должен быть положительным числом.', 'error');
                return;
            }
            if (totalWeight(selectedDirectionIndex) + weight > 100) {
                showToast('Общий вес некритических критериев не может превышать 100.', 'error');
                return;
            }
            if (newCriterionHasDeficiency) {
                const defWeight = Number(newDeficiencyWeight);
                if (isNaN(defWeight) || defWeight <= 0 || defWeight > weight) {
                    showToast('Вес недочета должен быть больше 0 и меньше веса критерия.', 'error');
                    return;
                }
            }
        }
        setDirections(directions.map((direction, index) => {
            if (index === selectedDirectionIndex) {
                return {
                    ...direction,
                    criteria: [...direction.criteria, {
                        name: newCriterionName,
                        weight: newCriterionCritical ? 0 : Number(newCriterionWeight),
                        isCritical: newCriterionCritical,
                        value: newCriterionValue || 'Нет описания',
                        deficiency: newCriterionHasDeficiency ? {
                            weight: Number(newDeficiencyWeight),
                            description: newDeficiencyDescription || 'Нет описания'
                        } : null
                    }]
                };
            }
            return direction;
        }));
        resetCriterionForm();
    };

    const handleEditCriterion = () => {
        if (!newCriterionName) {
            showToast('Пожалуйста, введите название критерия.', 'error');
            return;
        }
        if (!newCriterionCritical) {
            if (!newCriterionWeight) {
                showToast('Пожалуйста, введите вес для некритического критерия.', 'error');
                return;
            }
            const weight = Number(newCriterionWeight);
            if (isNaN(weight) || weight <= 0) {
                showToast('Вес должен быть положительным числом.', 'error');
                return;
            }
            const newTotalWeight = totalWeight(selectedDirectionIndex) -
                (directions[selectedDirectionIndex].criteria[editingCriterionIndex]?.isCritical ? 0 :
                    directions[selectedDirectionIndex].criteria[editingCriterionIndex]?.weight || 0) + weight;
            if (newTotalWeight > 100) {
                showToast('Общий вес некритических критериев не может превышать 100.', 'error');
                return;
            }
        }
        setDirections(directions.map((direction, index) => {
            if (index === selectedDirectionIndex) {
                return {
                    ...direction,
                    criteria: direction.criteria.map((criterion, i) =>
                        i === editingCriterionIndex ? {
                            name: newCriterionName,
                            weight: newCriterionCritical ? 0 : Number(newCriterionWeight),
                            isCritical: newCriterionCritical,
                            value: newCriterionValue || 'Нет описания',
                            deficiency: newCriterionHasDeficiency ? {
                                weight: Number(newDeficiencyWeight),
                                description: newDeficiencyDescription || 'Нет описания'
                            } : null
                        } : criterion
                    )
                };
            }
            return direction;
        }));
        resetCriterionForm();
    };

    const handleStartEditCriterion = (index) => {
        const criterion = directions[selectedDirectionIndex].criteria[index];
        setNewCriterionName(criterion.name);
        setNewCriterionWeight(criterion.isCritical ? '' : criterion.weight);
        setNewCriterionValue(criterion.value);
        setNewCriterionCritical(criterion.isCritical);

        if (criterion.deficiency) {
            setNewCriterionHasDeficiency(true);
            setNewDeficiencyWeight(criterion.deficiency.weight);
            setNewDeficiencyDescription(criterion.deficiency.description);
        } else {
            setNewCriterionHasDeficiency(false);
            setNewDeficiencyWeight('');
            setNewDeficiencyDescription('');
        }

        setEditingCriterionIndex(index);
        setIsEditingCriterion(true);
    };

    const handleDeleteCriterion = (index) => {
        setDirections(directions.map((direction, i) => {
            if (i === selectedDirectionIndex) {
                return {
                    ...direction,
                    criteria: direction.criteria.filter((_, j) => j !== index)
                };
            }
            return direction;
        }));
        if (editingCriterionIndex === index) {
            resetCriterionForm();
        }
    };

    const resetCriterionForm = () => {
        setNewCriterionName('');
        setNewCriterionWeight('');
        setNewCriterionValue('');
        setNewCriterionCritical(false);
        setNewCriterionHasDeficiency(false);
        setNewDeficiencyWeight('');
        setNewDeficiencyDescription('');
        setEditingCriterionIndex(null);
        setIsEditingCriterion(false);
    };

    const handleSave = async () => {
        const invalidDirection = directions.find((direction, index) =>
            direction.criteria.some(c => !c.isCritical) && totalWeight(index) !== 100
        );
        if (invalidDirection) {
            showToast(`Общий вес некритических критериев в "${invalidDirection.name}" должен равняться 100. Текущий: ${totalWeight(directions.indexOf(invalidDirection))}/100`, 'error');
            return;
        }
        setIsLoading(true);
        try {
            await onSave(directions);
            showToast('Мониторинговая шкала успешно сохранена', 'success');
        } catch (error) {
            console.error('Error saving directions:', error);
            showToast('Не удалось сохранить. Пожалуйста, попробуйте снова.', 'error');
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="monitoring-scale-section">
            <style>{`
                @import url('https://fonts.googleapis.com/css2?family=Crimson+Pro:wght@400;600;700&family=Work+Sans:wght@300;400;500;600&display=swap');
                
                .monitoring-scale-section {
                    min-height: 100vh;
                    background: linear-gradient(135deg, #f8f9fc 0%, #eef1f7 100%);
                    font-family: 'Work Sans', sans-serif;
                    color: #1a2332;
                    position: relative;
                    overflow: hidden;
                }
                
                .monitoring-scale-section::before {
                    content: '';
                    position: fixed;
                    top: -50%;
                    right: -20%;
                    width: 800px;
                    height: 800px;
                    background: radial-gradient(circle, rgba(99, 102, 241, 0.08) 0%, transparent 70%);
                    border-radius: 50%;
                    pointer-events: none;
                    animation: float 20s ease-in-out infinite;
                }
                
                .monitoring-scale-section::after {
                    content: '';
                    position: fixed;
                    bottom: -30%;
                    left: -10%;
                    width: 600px;
                    height: 600px;
                    background: radial-gradient(circle, rgba(139, 92, 246, 0.06) 0%, transparent 70%);
                    border-radius: 50%;
                    pointer-events: none;
                    animation: float 25s ease-in-out infinite reverse;
                }
                
                @keyframes float {
                    0%, 100% { transform: translate(0, 0) rotate(0deg); }
                    33% { transform: translate(30px, -30px) rotate(120deg); }
                    66% { transform: translate(-20px, 20px) rotate(240deg); }
                }
                
                .content-wrapper {
                    position: relative;
                    z-index: 1;
                    display: flex;
                    min-height: 100vh;
                }
                
                .sidebar {
                    width: 280px;
                    background: rgba(255, 255, 255, 0.95);
                    backdrop-filter: blur(20px);
                    border-right: 1px solid rgba(99, 102, 241, 0.1);
                    padding: 2rem 0;
                    position: sticky;
                    top: 0;
                    height: 100vh;
                    overflow-y: auto;
                    animation: slideInLeft 0.6s ease-out;
                }
                
                @keyframes slideInLeft {
                    from {
                        opacity: 0;
                        transform: translateX(-30px);
                    }
                    to {
                        opacity: 1;
                        transform: translateX(0);
                    }
                }
                
                .sidebar-header {
                    padding: 0 2rem 2rem;
                    border-bottom: 1px solid rgba(99, 102, 241, 0.1);
                }
                
                .sidebar-title {
                    font-family: 'Crimson Pro', serif;
                    font-size: 1.75rem;
                    font-weight: 700;
                    color: #1a2332;
                    margin-bottom: 0.5rem;
                    letter-spacing: -0.02em;
                }
                
                .sidebar-subtitle {
                    font-size: 0.875rem;
                    color: #64748b;
                    font-weight: 300;
                }
                
                .nav-tabs {
                    padding: 1.5rem 1rem;
                }
                
                .nav-tab {
                    display: flex;
                    align-items: center;
                    padding: 0.875rem 1rem;
                    margin-bottom: 0.5rem;
                    border-radius: 12px;
                    font-size: 0.9375rem;
                    font-weight: 500;
                    color: #64748b;
                    cursor: pointer;
                    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                    border: 1px solid transparent;
                }
                
                .nav-tab:hover {
                    background: rgba(99, 102, 241, 0.05);
                    color: #4f46e5;
                    transform: translateX(4px);
                }
                
                .nav-tab.active {
                    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
                    color: white;
                    border: 1px solid rgba(255, 255, 255, 0.2);
                    box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
                }
                
                .nav-tab-icon {
                    width: 20px;
                    height: 20px;
                    margin-right: 0.75rem;
                }
                
                .main-content {
                    flex: 1;
                    padding: 3rem;
                    animation: fadeInUp 0.8s ease-out 0.2s both;
                }
                
                @keyframes fadeInUp {
                    from {
                        opacity: 0;
                        transform: translateY(20px);
                    }
                    to {
                        opacity: 1;
                        transform: translateY(0);
                    }
                }
                
                .page-header {
                    margin-bottom: 2.5rem;
                }
                
                .page-title {
                    font-family: 'Crimson Pro', serif;
                    font-size: 2.5rem;
                    font-weight: 700;
                    color: #1a2332;
                    margin-bottom: 0.75rem;
                    letter-spacing: -0.03em;
                }
                
                .page-description {
                    font-size: 1rem;
                    color: #64748b;
                    max-width: 600px;
                    line-height: 1.6;
                }
                
                .card {
                    background: white;
                    border-radius: 20px;
                    padding: 2rem;
                    margin-bottom: 1.5rem;
                    border: 1px solid rgba(99, 102, 241, 0.08);
                    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.02), 0 8px 24px rgba(99, 102, 241, 0.04);
                    transition: all 0.3s ease;
                }
                
                .card:hover {
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.04), 0 12px 32px rgba(99, 102, 241, 0.08);
                    transform: translateY(-2px);
                }
                
                .card-title {
                    font-family: 'Crimson Pro', serif;
                    font-size: 1.375rem;
                    font-weight: 600;
                    color: #1a2332;
                    margin-bottom: 1.5rem;
                    display: flex;
                    align-items: center;
                }
                
                .card-title-icon {
                    width: 24px;
                    height: 24px;
                    margin-right: 0.75rem;
                    color: #6366f1;
                }
                
                .form-group {
                    margin-bottom: 1.5rem;
                }
                
                .form-label {
                    display: block;
                    font-size: 0.875rem;
                    font-weight: 600;
                    color: #475569;
                    margin-bottom: 0.5rem;
                    letter-spacing: 0.01em;
                }
                
                .form-input {
                    width: 100%;
                    padding: 0.875rem 1rem;
                    border: 2px solid #e2e8f0;
                    border-radius: 12px;
                    font-size: 0.9375rem;
                    color: #1a2332;
                    transition: all 0.2s ease;
                    background: white;
                }
                
                .form-input:focus {
                    outline: none;
                    border-color: #6366f1;
                    box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.1);
                }
                
                .form-input::placeholder {
                    color: #94a3b8;
                }
                
                .form-textarea {
                    width: 100%;
                    padding: 0.875rem 1rem;
                    border: 2px solid #e2e8f0;
                    border-radius: 12px;
                    font-size: 0.9375rem;
                    color: #1a2332;
                    transition: all 0.2s ease;
                    background: white;
                    resize: vertical;
                    min-height: 100px;
                    font-family: 'Work Sans', sans-serif;
                }
                
                .form-textarea:focus {
                    outline: none;
                    border-color: #6366f1;
                    box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.1);
                }
                
                .checkbox-wrapper {
                    display: flex;
                    align-items: center;
                    padding: 0.75rem;
                    background: #f8fafc;
                    border-radius: 10px;
                    margin-bottom: 1rem;
                    transition: all 0.2s ease;
                }
                
                .checkbox-wrapper:hover {
                    background: #f1f5f9;
                }
                
                .checkbox-input {
                    width: 20px;
                    height: 20px;
                    margin-right: 0.75rem;
                    cursor: pointer;
                    accent-color: #6366f1;
                }
                
                .checkbox-label {
                    font-size: 0.9375rem;
                    color: #475569;
                    cursor: pointer;
                    user-select: none;
                }
                
                .button {
                    padding: 0.875rem 1.75rem;
                    border: none;
                    border-radius: 12px;
                    font-size: 0.9375rem;
                    font-weight: 600;
                    cursor: pointer;
                    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    gap: 0.5rem;
                }
                
                .button-primary {
                    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
                    color: white;
                    box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3);
                }
                
                .button-primary:hover:not(:disabled) {
                    box-shadow: 0 4px 16px rgba(99, 102, 241, 0.4);
                    transform: translateY(-2px);
                }
                
                .button-primary:active:not(:disabled) {
                    transform: translateY(0);
                }
                
                .button-primary:disabled {
                    opacity: 0.6;
                    cursor: not-allowed;
                }
                
                .button-secondary {
                    background: white;
                    color: #6366f1;
                    border: 2px solid #e2e8f0;
                }
                
                .button-secondary:hover {
                    background: #f8fafc;
                    border-color: #6366f1;
                }
                
                .button-ghost {
                    background: transparent;
                    color: #64748b;
                    border: 2px solid #e2e8f0;
                }
                
                .button-ghost:hover {
                    background: #f8fafc;
                    border-color: #cbd5e1;
                }
                
                .button-success {
                    background: linear-gradient(135deg, #10b981 0%, #059669 100%);
                    color: white;
                    box-shadow: 0 2px 8px rgba(16, 185, 129, 0.3);
                }
                
                .button-success:hover {
                    box-shadow: 0 4px 16px rgba(16, 185, 129, 0.4);
                    transform: translateY(-2px);
                }
                
                .button-group {
                    display: flex;
                    gap: 0.75rem;
                    flex-wrap: wrap;
                }
                
                .item-list {
                    display: grid;
                    gap: 0.75rem;
                    max-height: 500px;
                    overflow-y: auto;
                    padding-right: 0.5rem;
                }
                
                .item-list::-webkit-scrollbar {
                    width: 8px;
                }
                
                .item-list::-webkit-scrollbar-track {
                    background: #f1f5f9;
                    border-radius: 10px;
                }
                
                .item-list::-webkit-scrollbar-thumb {
                    background: #cbd5e1;
                    border-radius: 10px;
                }
                
                .item-list::-webkit-scrollbar-thumb:hover {
                    background: #94a3b8;
                }
                
                .list-item {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    padding: 1rem 1.25rem;
                    background: #f8fafc;
                    border: 2px solid #e2e8f0;
                    border-radius: 12px;
                    transition: all 0.3s ease;
                    animation: slideIn 0.4s ease-out;
                }
                
                @keyframes slideIn {
                    from {
                        opacity: 0;
                        transform: translateX(-20px);
                    }
                    to {
                        opacity: 1;
                        transform: translateX(0);
                    }
                }
                
                .list-item:hover {
                    background: white;
                    border-color: #cbd5e1;
                    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
                }
                
                .list-item-content {
                    display: flex;
                    align-items: center;
                    gap: 0.75rem;
                    flex: 1;
                }
                
                .list-item-icon {
                    width: 36px;
                    height: 36px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    border-radius: 10px;
                    flex-shrink: 0;
                }
                
                .icon-file {
                    background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
                    color: white;
                }
                
                .icon-no-file {
                    background: #e2e8f0;
                    color: #64748b;
                }
                
                .icon-critical {
                    background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
                    color: white;
                }
                
                .icon-normal {
                    background: linear-gradient(135deg, #10b981 0%, #059669 100%);
                    color: white;
                }
                
                .list-item-text {
                    flex: 1;
                }
                
                .list-item-title {
                    font-size: 0.9375rem;
                    font-weight: 600;
                    color: #1a2332;
                    margin-bottom: 0.125rem;
                }
                
                .list-item-meta {
                    font-size: 0.8125rem;
                    color: #64748b;
                }
                
                .list-item-actions {
                    display: flex;
                    gap: 0.5rem;
                }
                
                .icon-button {
                    width: 36px;
                    height: 36px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    border: none;
                    border-radius: 10px;
                    cursor: pointer;
                    transition: all 0.2s ease;
                    background: transparent;
                }
                
                .icon-button:hover {
                    transform: scale(1.1);
                }
                
                .icon-button-edit {
                    color: #3b82f6;
                }
                
                .icon-button-edit:hover {
                    background: rgba(59, 130, 246, 0.1);
                }
                
                .icon-button-delete {
                    color: #ef4444;
                }
                
                .icon-button-delete:hover {
                    background: rgba(239, 68, 68, 0.1);
                }
                
                .empty-state {
                    text-align: center;
                    padding: 3rem 2rem;
                    color: #94a3b8;
                }
                
                .empty-state-icon {
                    width: 64px;
                    height: 64px;
                    margin: 0 auto 1rem;
                    opacity: 0.3;
                }
                
                .empty-state-text {
                    font-size: 0.9375rem;
                    font-style: italic;
                }
                
                .weight-indicator {
                    display: inline-flex;
                    align-items: center;
                    padding: 0.375rem 0.875rem;
                    background: white;
                    border: 2px solid #e2e8f0;
                    border-radius: 20px;
                    font-size: 0.875rem;
                    font-weight: 600;
                    gap: 0.5rem;
                    margin-top: 1rem;
                }
                
                .weight-indicator.valid {
                    border-color: #10b981;
                    background: rgba(16, 185, 129, 0.05);
                    color: #059669;
                }
                
                .weight-indicator.invalid {
                    border-color: #ef4444;
                    background: rgba(239, 68, 68, 0.05);
                    color: #dc2626;
                }
                
                .save-bar {
                    position: sticky;
                    bottom: 0;
                    left: 0;
                    right: 0;
                    background: rgba(255, 255, 255, 0.98);
                    backdrop-filter: blur(20px);
                    border-top: 1px solid rgba(99, 102, 241, 0.1);
                    padding: 1.5rem 3rem;
                    margin: 3rem -3rem -3rem;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.04);
                    animation: slideUp 0.4s ease-out;
                }
                
                @keyframes slideUp {
                    from {
                        opacity: 0;
                        transform: translateY(20px);
                    }
                    to {
                        opacity: 1;
                        transform: translateY(0);
                    }
                }
                
                .save-bar-info {
                    font-size: 0.875rem;
                    color: #64748b;
                }
                
                .deficiency-badge {
                    display: inline-flex;
                    align-items: center;
                    padding: 0.25rem 0.625rem;
                    background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
                    color: white;
                    border-radius: 12px;
                    font-size: 0.75rem;
                    font-weight: 600;
                    margin-left: 0.5rem;
                }
                
                .select-input {
                    width: 100%;
                    padding: 0.875rem 1rem;
                    border: 2px solid #e2e8f0;
                    border-radius: 12px;
                    font-size: 0.9375rem;
                    color: #1a2332;
                    background: white;
                    cursor: pointer;
                    transition: all 0.2s ease;
                }
                
                .select-input:focus {
                    outline: none;
                    border-color: #6366f1;
                    box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.1);
                }
                
                .select-input:disabled {
                    background: #f1f5f9;
                    cursor: not-allowed;
                    opacity: 0.6;
                }
            `}</style>

            <div className="content-wrapper">
                {/* Sidebar Navigation */}
                <div className="sidebar">
                    <div className="sidebar-header">
                        <h1 className="sidebar-title">Мониторинг</h1>
                        <p className="sidebar-subtitle">Система оценки качества</p>
                    </div>

                    <div className="nav-tabs">
                        <div
                            className={`nav-tab ${activeTab === 'directions' ? 'active' : ''}`}
                            onClick={() => setActiveTab('directions')}
                        >
                            <svg className="nav-tab-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                            </svg>
                            Направления
                        </div>

                        <div
                            className={`nav-tab ${activeTab === 'criteria' ? 'active' : ''}`}
                            onClick={() => setActiveTab('criteria')}
                        >
                            <svg className="nav-tab-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
                            </svg>
                            Критерии оценки
                        </div>
                    </div>
                </div>

                {/* Main Content */}
                <div className="main-content">
                    {activeTab === 'directions' && (
                        <>
                            <div className="page-header">
                                <h2 className="page-title">Направления мониторинга</h2>
                                <p className="page-description">
                                    Создавайте и управляйте направлениями для структурированной оценки качества работы.
                                    Каждое направление может содержать набор критериев с индивидуальными весами.
                                </p>
                            </div>

                            {/* Direction Form Card */}
                            <div className="card">
                                <h3 className="card-title">
                                    <svg className="card-title-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                                    </svg>
                                    {isEditingDirection ? 'Редактирование направления' : 'Новое направление'}
                                </h3>

                                <div className="form-group">
                                    <label className="form-label">Название направления</label>
                                    <input
                                        type="text"
                                        value={newDirectionName}
                                        onChange={(e) => setNewDirectionName(e.target.value)}
                                        placeholder="Например: Техническая документация"
                                        className="form-input"
                                    />
                                </div>

                                <div className="checkbox-wrapper">
                                    <input
                                        type="checkbox"
                                        checked={newDirectionFileUpload}
                                        onChange={(e) => setNewDirectionFileUpload(e.target.checked)}
                                        className="checkbox-input"
                                        id="fileUploadCheckbox"
                                    />
                                    <label htmlFor="fileUploadCheckbox" className="checkbox-label">
                                        Требуется загрузка файла для этого направления
                                    </label>
                                </div>

                                <div className="button-group">
                                    <button
                                        onClick={isEditingDirection ? handleEditDirection : handleAddDirection}
                                        className={isEditingDirection ? 'button button-primary' : 'button button-success'}
                                    >
                                        {isEditingDirection ? (
                                            <>
                                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                                                </svg>
                                                Сохранить изменения
                                            </>
                                        ) : (
                                            <>
                                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                                                </svg>
                                                Добавить направление
                                            </>
                                        )}
                                    </button>
                                    {isEditingDirection && (
                                        <button
                                            onClick={() => {
                                                setNewDirectionName('');
                                                setNewDirectionFileUpload(true);
                                                setIsEditingDirection(false);
                                                setEditingDirectionIndex(null);
                                            }}
                                            className="button button-ghost"
                                        >
                                            Отмена
                                        </button>
                                    )}
                                </div>
                            </div>

                            {/* Directions List Card */}
                            <div className="card">
                                <h3 className="card-title">
                                    <svg className="card-title-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 6h16M4 10h16M4 14h16M4 18h16" />
                                    </svg>
                                    Список направлений ({directions.length})
                                </h3>

                                {directions.length === 0 ? (
                                    <div className="empty-state">
                                        <svg className="empty-state-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                        </svg>
                                        <p className="empty-state-text">Нет добавленных направлений. Создайте первое направление выше.</p>
                                    </div>
                                ) : (
                                    <div className="item-list">
                                        {directions.map((direction, index) => (
                                            <div key={index} className="list-item">
                                                <div className="list-item-content">
                                                    <div className={`list-item-icon ${direction.hasFileUpload ? 'icon-file' : 'icon-no-file'}`}>
                                                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={direction.hasFileUpload ? 'M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12' : 'M9 12h6m-3-3v6'} />
                                                        </svg>
                                                    </div>
                                                    <div className="list-item-text">
                                                        <div className="list-item-title">{direction.name}</div>
                                                        <div className="list-item-meta">
                                                            {direction.hasFileUpload ? 'Требуется файл' : 'Файл не требуется'} • {direction.criteria.length} критериев
                                                        </div>
                                                    </div>
                                                </div>
                                                <div className="list-item-actions">
                                                    <button
                                                        onClick={() => handleStartEditDirection(index)}
                                                        className="icon-button icon-button-edit"
                                                        title="Редактировать"
                                                    >
                                                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.29 2.29 0 113.232 3.232L6.5 20.5l-3.5 1 1-3.5L17.768 4.232z" />
                                                        </svg>
                                                    </button>
                                                    <button
                                                        onClick={() => handleDeleteDirection(index)}
                                                        className="icon-button icon-button-delete"
                                                        title="Удалить"
                                                    >
                                                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                        </svg>
                                                    </button>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </>
                    )}

                    {activeTab === 'criteria' && (
                        <>
                            <div className="page-header">
                                <h2 className="page-title">Критерии оценки</h2>
                                <p className="page-description">
                                    Добавляйте критерии для выбранного направления. Общий вес некритических критериев должен составлять 100%.
                                </p>
                            </div>

                            {/* Direction Selector Card */}
                            <div className="card">
                                <h3 className="card-title">
                                    <svg className="card-title-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                                    </svg>
                                    Выберите направление
                                </h3>

                                <div className="form-group">
                                    <select
                                        value={selectedDirectionIndex}
                                        onChange={(e) => setSelectedDirectionIndex(Number(e.target.value))}
                                        className="select-input"
                                        disabled={directions.length === 0}
                                    >
                                        {directions.length === 0 ? (
                                            <option value="">Нет доступных направлений</option>
                                        ) : (
                                            directions.map((direction, index) => (
                                                <option key={index} value={index}>{direction.name}</option>
                                            ))
                                        )}
                                    </select>
                                </div>
                            </div>

                            {/* Criterion Form Card */}
                            <div className="card">
                                <h3 className="card-title">
                                    <svg className="card-title-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                                    </svg>
                                    {isEditingCriterion ? 'Редактирование критерия' : 'Новый критерий'}
                                </h3>

                                <div className="form-group">
                                    <label className="form-label">Название критерия</label>
                                    <input
                                        type="text"
                                        value={newCriterionName}
                                        onChange={(e) => setNewCriterionName(e.target.value)}
                                        placeholder="Например: Полнота документации"
                                        className="form-input"
                                        disabled={directions.length === 0}
                                    />
                                </div>

                                {!newCriterionCritical && (
                                    <div className="form-group">
                                        <label className="form-label">Вес критерия (%)</label>
                                        <input
                                            type="number"
                                            value={newCriterionWeight}
                                            onChange={(e) => setNewCriterionWeight(e.target.value)}
                                            placeholder="0-100"
                                            min="1"
                                            max="100"
                                            className="form-input"
                                            disabled={directions.length === 0}
                                        />
                                    </div>
                                )}

                                <div className="form-group">
                                    <label className="form-label">Описание критерия</label>
                                    <textarea
                                        value={newCriterionValue}
                                        onChange={(e) => setNewCriterionValue(e.target.value)}
                                        placeholder="Подробное описание критерия оценки..."
                                        className="form-textarea"
                                        disabled={directions.length === 0}
                                    />
                                </div>

                                <div className="checkbox-wrapper">
                                    <input
                                        type="checkbox"
                                        checked={newCriterionCritical}
                                        onChange={(e) => {
                                            setNewCriterionCritical(e.target.checked);
                                            if (e.target.checked) setNewCriterionWeight('');
                                        }}
                                        className="checkbox-input"
                                        id="criticalCheckbox"
                                        disabled={directions.length === 0}
                                    />
                                    <label htmlFor="criticalCheckbox" className="checkbox-label">
                                        Критичный критерий (устанавливает оценку в 0 при ошибке)
                                    </label>
                                </div>

                                <div className="checkbox-wrapper">
                                    <input
                                        type="checkbox"
                                        checked={newCriterionHasDeficiency}
                                        onChange={(e) => {
                                            setNewCriterionHasDeficiency(e.target.checked);
                                            if (!e.target.checked) {
                                                setNewDeficiencyWeight('');
                                                setNewDeficiencyDescription('');
                                            }
                                        }}
                                        className="checkbox-input"
                                        id="deficiencyCheckbox"
                                        disabled={directions.length === 0}
                                    />
                                    <label htmlFor="deficiencyCheckbox" className="checkbox-label">
                                        Добавить недочет (частичное снижение оценки)
                                    </label>
                                </div>

                                {newCriterionHasDeficiency && (
                                    <>
                                        <div className="form-group">
                                            <label className="form-label">Вес недочета (%)</label>
                                            <input
                                                type="number"
                                                value={newDeficiencyWeight}
                                                onChange={(e) => setNewDeficiencyWeight(e.target.value)}
                                                placeholder="0-100"
                                                min="1"
                                                className="form-input"
                                            />
                                        </div>

                                        <div className="form-group">
                                            <label className="form-label">Описание недочета</label>
                                            <textarea
                                                value={newDeficiencyDescription}
                                                onChange={(e) => setNewDeficiencyDescription(e.target.value)}
                                                placeholder="Описание недочета..."
                                                className="form-textarea"
                                                rows={3}
                                            />
                                        </div>
                                    </>
                                )}

                                <div className="button-group">
                                    <button
                                        onClick={isEditingCriterion ? handleEditCriterion : handleAddCriterion}
                                        className={isEditingCriterion ? 'button button-primary' : 'button button-success'}
                                        disabled={directions.length === 0}
                                    >
                                        {isEditingCriterion ? (
                                            <>
                                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                                                </svg>
                                                Сохранить изменения
                                            </>
                                        ) : (
                                            <>
                                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                                                </svg>
                                                Добавить критерий
                                            </>
                                        )}
                                    </button>
                                    {isEditingCriterion && (
                                        <button
                                            onClick={resetCriterionForm}
                                            className="button button-ghost"
                                        >
                                            Отмена
                                        </button>
                                    )}
                                </div>
                            </div>

                            {/* Criteria List Card */}
                            {directions.length > 0 && (
                                <div className="card">
                                    <h3 className="card-title">
                                        <svg className="card-title-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
                                        </svg>
                                        Список критериев ({directions[selectedDirectionIndex]?.criteria.length || 0})
                                    </h3>

                                    {directions[selectedDirectionIndex]?.criteria.length === 0 ? (
                                        <div className="empty-state">
                                            <svg className="empty-state-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                                            </svg>
                                            <p className="empty-state-text">Нет критериев для этого направления. Добавьте первый критерий выше.</p>
                                        </div>
                                    ) : (
                                        <>
                                            <div className="item-list">
                                                {directions[selectedDirectionIndex].criteria.map((criterion, critIndex) => (
                                                    <div key={critIndex} className="list-item">
                                                        <div className="list-item-content">
                                                            <div className={`list-item-icon ${criterion.isCritical ? 'icon-critical' : 'icon-normal'}`}>
                                                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={criterion.isCritical ? 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z' : 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'} />
                                                                </svg>
                                                            </div>
                                                            <div className="list-item-text">
                                                                <div className="list-item-title">
                                                                    {criterion.name}
                                                                    {criterion.deficiency && (
                                                                        <span className="deficiency-badge">
                                                                            Недочет: {criterion.deficiency.weight}%
                                                                        </span>
                                                                    )}
                                                                </div>
                                                                <div className="list-item-meta">
                                                                    {criterion.isCritical ? 'Критичный критерий' : `Вес: ${criterion.weight}%`}
                                                                </div>
                                                            </div>
                                                        </div>
                                                        <div className="list-item-actions">
                                                            <button
                                                                onClick={() => handleStartEditCriterion(critIndex)}
                                                                className="icon-button icon-button-edit"
                                                                title="Редактировать"
                                                            >
                                                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.29 2.29 0 113.232 3.232L6.5 20.5l-3.5 1 1-3.5L17.768 4.232z" />
                                                                </svg>
                                                            </button>
                                                            <button
                                                                onClick={() => handleDeleteCriterion(critIndex)}
                                                                className="icon-button icon-button-delete"
                                                                title="Удалить"
                                                            >
                                                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                                </svg>
                                                            </button>
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>

                                            <div className={`weight-indicator ${totalWeight(selectedDirectionIndex) === 100 || !directions[selectedDirectionIndex].criteria.some(c => !c.isCritical) ? 'valid' : 'invalid'}`}>
                                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={totalWeight(selectedDirectionIndex) === 100 || !directions[selectedDirectionIndex].criteria.some(c => !c.isCritical) ? 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z' : 'M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'} />
                                                </svg>
                                                Общий вес: {totalWeight(selectedDirectionIndex)}/100
                                            </div>
                                        </>
                                    )}
                                </div>
                            )}
                        </>
                    )}

                    {/* Save Bar */}
                    <div className="save-bar">
                        <div className="save-bar-info">
                            {directions.length} направлений • {directions.reduce((sum, d) => sum + d.criteria.length, 0)} критериев
                        </div>
                        <div className="button-group">
                            <button
                                onClick={handleSave}
                                disabled={isLoading}
                                className="button button-primary"
                            >
                                {isLoading ? (
                                    <>
                                        <FaIcon className="fas fa-spinner fa-spin" />
                                        Сохранение...
                                    </>
                                ) : (
                                    <>
                                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                                        </svg>
                                        Сохранить изменения
                                    </>
                                )}
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <ToastContainer toasts={toasts} removeToast={removeToast} setToasts={setToasts} />
        </div>
    );
};

export default MonitoringScaleSection;
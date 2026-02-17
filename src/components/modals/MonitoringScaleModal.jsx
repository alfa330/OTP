import React, { useState } from 'react';
import ToastContainer from '../common/ToastContainer';

const MonitoringScaleModal = ({ isOpen, onClose, onSave, initialDirections }) => {
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
            alert('Пожалуйста, введите название направления.');
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
            alert('Пожалуйста, введите название направления.');
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
                    showToast('Вес недочета должен быть больше 0 и больше веса критерия.', 'error');
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
    
        // Заполняем deficiency
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
            onClose();
        } catch (error) {
            console.error('Error saving directions:', error);
            showToast('Не удалось сохранить. Пожалуйста, попробуйте снова.', 'error');
        } finally {
            setIsLoading(false);
        }
    };
    
    if (!isOpen) return null;
    
    return (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div className="bg-white p-6 rounded-lg shadow-lg w-full max-w-4xl max-h-[90vh] overflow-y-auto">
                <h2 className="text-xl font-semibold mb-4">Мониторинговая шкала</h2>
                
                <div className="flex mb-4">
                    <button
                        className={`px-4 py-2 mr-2 rounded ${activeTab === 'directions' ? 'bg-blue-500 text-white' : 'bg-gray-200 text-gray-700'}`}
                        onClick={() => setActiveTab('directions')}
                    >
                        Направления
                    </button>
                    <button
                        className={`px-4 py-2 rounded ${activeTab === 'criteria' ? 'bg-blue-500 text-white' : 'bg-gray-200 text-gray-700'}`}
                        onClick={() => setActiveTab('criteria')}
                    >
                        Критерии
                    </button>
                </div>
    
                {activeTab === 'directions' && (
                    <div>
                        <div className="mb-4">
                            <label className="block mb-2 font-medium">
                                {isEditingDirection ? `Редактирование направления: ${directions[editingDirectionIndex]?.name}` : 'Новое направление'}
                            </label>
                            <div className="flex flex-col space-y-2">
                                <input
                                    type="text"
                                    value={newDirectionName}
                                    onChange={(e) => setNewDirectionName(e.target.value)}
                                    placeholder="Название направления"
                                    className="w-full p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                />
                                <div className="flex items-center">
                                    <input
                                        type="checkbox"
                                        checked={newDirectionFileUpload}
                                        title="Требуется загрузка файла"
                                        onChange={(e) => setNewDirectionFileUpload(e.target.checked)}
                                        className="mr-2"
                                    />
                                    <label className="text-sm font-medium">Требуется загрузка файла</label>
                                </div>
                                <div className="flex space-x-2">
                                    <button
                                        onClick={isEditingDirection ? handleEditDirection : handleAddDirection}
                                        className={`p-2 rounded text-white ${
                                            isEditingDirection ? 'bg-blue-500 hover:bg-blue-600' : 'bg-green-500 hover:bg-green-600'
                                        }`}
                                    >
                                        {isEditingDirection ? 'Сохранить изменения' : 'Добавить направление'}
                                    </button>
                                    {isEditingDirection && (
                                        <button
                                            onClick={() => {
                                                setNewDirectionName('');
                                                setNewDirectionFileUpload(true);
                                                setIsEditingDirection(false);
                                                setEditingDirectionIndex(null);
                                            }}
                                            className="p-2 bg-gray-500 text-white rounded hover:bg-gray-600"
                                        >
                                            Отмена
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
    
                        <div className="mb-4">
                            <h3 className="text-lg font-medium mb-2">Направления</h3>
                            {directions.length === 0 ? (
                                <p className="text-gray-500 italic">Нет добавленных направлений.</p>
                            ) : (
                                <ul className="space-y-2 max-h-40 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-300 scrollbar-track-gray-100">
                                    {directions.map((direction, index) => (
                                        <li 
                                            key={index} 
                                            className="flex justify-between items-center bg-white p-2 rounded-md shadow-sm border border-gray-200 animate-fade-in"
                                        >
                                            <div className="flex items-center space-x-2">
                                                <svg
                                                    className={`w-4 h-4 ${direction.hasFileUpload ? 'text-blue-500' : 'text-gray-500'}`}
                                                    fill="none"
                                                    stroke="currentColor"
                                                    viewBox="0 0 24 24"
                                                    xmlns="http://www.w3.org/2000/svg"
                                                >
                                                    <path
                                                        strokeLinecap="round"
                                                        strokeLinejoin="round"
                                                        strokeWidth="2"
                                                        d={direction.hasFileUpload ? 'M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12' : 'M9 12h6m-3-3v6'}
                                                    />
                                                </svg>
                                                <span className="text-sm font-medium text-gray-800">
                                                    {direction.name} {direction.hasFileUpload ? '(Требуется файл)' : '(Файл не требуется)'}
                                                </span>
                                            </div>
                                            <div className="flex space-x-2">
                                                <button
                                                    onClick={() => handleStartEditDirection(index)}
                                                    className="text-blue-500 hover:text-blue-700 p-1"
                                                    title="Редактировать направление"
                                                >
                                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.29 2.29 0 113.232 3.232L6.5 20.5l-3.5 1 1-3.5L17.768 4.232z" />
                                                    </svg>
                                                </button>
                                                <button
                                                    onClick={() => handleDeleteDirection(index)}
                                                    className="text-red-500 hover:text-red-700 p-1"
                                                    title="Удалить направление"
                                                >
                                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                                                    </svg>
                                                </button>
                                            </div>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    </div>
                )}
    
                {activeTab === 'criteria' && (
                    <div>
                        <div className="mb-4">
                            <label className="block mb-2 font-medium">Выберите направление для критериев</label>
                            <select
                                value={selectedDirectionIndex}
                                onChange={(e) => setSelectedDirectionIndex(Number(e.target.value))}
                                className="w-full p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
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
    
                        <div className="mb-4">
                            <label className="block mb-2 font-medium">
                                {isEditingCriterion ? `Редактирование критерия: ${directions[selectedDirectionIndex]?.criteria[editingCriterionIndex]?.name}` : 'Новый критерий'}
                            </label>
                            <div className="flex flex-col space-y-2">
                                <input
                                    type="text"
                                    value={newCriterionName}
                                    onChange={(e) => setNewCriterionName(e.target.value)}
                                    placeholder="Название критерия"
                                    className="w-full p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                    disabled={directions.length === 0}
                                />
                                {!newCriterionCritical && (
                                    <input
                                        type="number"
                                        value={newCriterionWeight}
                                        onChange={(e) => setNewCriterionWeight(e.target.value)}
                                        placeholder="Вес критерия"
                                        min="1"
                                        className="w-full p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                        disabled={directions.length === 0}
                                    />
                                )}
                                <textarea
                                    value={newCriterionValue}
                                    onChange={(e) => setNewCriterionValue(e.target.value)}
                                    placeholder="Описание критерия"
                                    className="w-full p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                    rows={3}
                                    disabled={directions.length === 0}
                                />
                                <div className="flex items-center">
                                    <input
                                        type="checkbox"
                                        checked={newCriterionCritical}
                                        onChange={(e) => {
                                            setNewCriterionCritical(e.target.checked);
                                            if (e.target.checked) setNewCriterionWeight('');
                                        }}
                                        className="mr-2"
                                        disabled={directions.length === 0}
                                    />
                                    <label className="text-sm font-medium">Критичный (устанавливает оценку в 0, если ошибка, без веса)</label>
                                </div>
                                <div className="flex items-center">
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
                                        className="mr-2"
                                        disabled={directions.length === 0}
                                    />
                                    <label className="text-sm font-medium">Недочет</label>
                                </div>

                                {newCriterionHasDeficiency && (
                                    <div className="space-y-2">
                                        <input
                                            type="number"
                                            value={newDeficiencyWeight}
                                            onChange={(e) => setNewDeficiencyWeight(e.target.value)}
                                            placeholder="Вес недочета"
                                            min="1"
                                            className="w-full p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                        />
                                        <textarea
                                            value={newDeficiencyDescription}
                                            onChange={(e) => setNewDeficiencyDescription(e.target.value)}
                                            placeholder="Описание недочета"
                                            className="w-full p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                                            rows={2}
                                        />
                                    </div>
                                )}

                                <div className="flex space-x-2">
                                    <button
                                        onClick={isEditingCriterion ? handleEditCriterion : handleAddCriterion}
                                        className={`w-full p-2 rounded text-white ${
                                            isEditingCriterion ? 'bg-blue-500 hover:bg-blue-600' : 'bg-green-500 hover:bg-green-600'
                                        } ${directions.length === 0 ? 'opacity-50 cursor-not-allowed' : ''}`}
                                        disabled={directions.length === 0}
                                    >
                                        {isEditingCriterion ? 'Сохранить изменения' : 'Добавить критерий'}
                                    </button>
                                    {isEditingCriterion && (
                                        <button
                                            onClick={resetCriterionForm}
                                            className="w-full p-2 bg-gray-500 text-white rounded hover:bg-gray-600"
                                        >
                                            Отмена редактирования
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
    
                        <div className="mb-4 max-h-80 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-300 scrollbar-track-gray-100">
                            <h3 className="text-lg font-medium mb-2">Критерии</h3>
                            {directions.length === 0 || directions[selectedDirectionIndex].criteria.length === 0 ? (
                                <p className="text-gray-500 italic">Нет добавленных критериев для этого направления.</p>
                            ) : (
                                <ul className="space-y-2">
                                    {directions[selectedDirectionIndex].criteria.map((criterion, critIndex) => (
                                        <li 
                                            key={critIndex} 
                                            className="flex justify-between items-center bg-white p-2 rounded-md shadow-sm border border-gray-200 animate-fade-in"
                                        >
                                            <div className="flex items-center space-x-2">
                                                <svg
                                                    className={`w-4 h-4 ${criterion.isCritical ? 'text-red-500' : 'text-green-500'}`}
                                                    fill="none"
                                                    stroke="currentColor"
                                                    viewBox="0 0 24 24"
                                                    xmlns="http://www.w3.org/2000/svg"
                                                >
                                                    <path
                                                        strokeLinecap="round"
                                                        strokeLinejoin="round"
                                                        strokeWidth="2"
                                                        d={criterion.isCritical ? 'M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z' : 'M5 13l4 4L19 7'}
                                                    />
                                                </svg>
                                                <span className="text-sm font-medium text-gray-800">
                                                    {criterion.name} {criterion.isCritical ? '(Critical)' : `(${criterion.weight}%)`}
                                                    {criterion.deficiency && (
                                                        <span className="ml-2 text-xs text-orange-600">
                                                            Deficiency: {criterion.deficiency.weight}%
                                                        </span>
                                                    )}
                                                </span>
                                            </div>
                                            <div className="flex space-x-2">
                                                <button
                                                    onClick={() => handleStartEditCriterion(critIndex)}
                                                    className="text-blue-500 hover:text-blue-700 p-1"
                                                    title="Править критерий"
                                                >
                                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.29 2.29 0 113.232 3.232L6.5 20.5l-3.5 1 1-3.5L17.768 4.232z" />
                                                    </svg>
                                                </button>
                                                <button
                                                    onClick={() => handleDeleteCriterion(critIndex)}
                                                    className="text-red-500 hover:text-red-700 p-1"
                                                    title="Удалить критерий"
                                                >
                                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                                                    </svg>
                                                </button>
                                            </div>
                                        </li>
                                    ))}
                                </ul>
                            )}
                            {directions.length > 0 && (
                                <p className={`mt-2 text-sm ${totalWeight(selectedDirectionIndex) !== 100 && directions[selectedDirectionIndex].criteria.some(c => !c.isCritical) ? 'text-red-500' : 'text-green-500'}`}>
                                    Total Weight (non-critical): {totalWeight(selectedDirectionIndex)}/100
                                </p>
                            )}
                        </div>
                    </div>
                )}
    
                <div className="flex justify-between">
                    <button
                        onClick={handleSave}
                        disabled={isLoading}
                        className={`bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600 transition-all duration-200 flex items-center justify-center gap-2 ${isLoading ? 'opacity-50 cursor-not-allowed' : ''}`}
                    >
                        {isLoading ? (
                            <>
                                <i className="fas fa-spinner fa-spin"></i> Сохранение...
                            </>
                        ) : (
                            'Сохранить'
                        )}
                    </button>
                    <button
                        onClick={onClose}
                        className="bg-gray-500 text-white px-4 py-2 rounded hover:bg-gray-600"
                    >
                        Отмена
                    </button>
                </div>
            </div>
            <ToastContainer toasts={toasts} removeToast={removeToast} setToasts={setToasts} />
        </div>
    );
};

export default MonitoringScaleModal;

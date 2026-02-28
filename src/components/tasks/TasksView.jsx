import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

const TAG_OPTIONS = [
    { value: 'task', label: 'Задача' },
    { value: 'problem', label: 'Проблема' },
    { value: 'suggestion', label: 'Предложение' }
];

const TASK_STATUS_META = {
    assigned: { label: 'Выставлен', className: 'bg-blue-100 text-blue-700 border border-blue-200' },
    in_progress: { label: 'Принят в работу', className: 'bg-amber-100 text-amber-800 border border-amber-200' },
    completed: { label: 'Выполнен', className: 'bg-indigo-100 text-indigo-700 border border-indigo-200' },
    accepted: { label: 'Принят', className: 'bg-emerald-100 text-emerald-700 border border-emerald-200' },
    returned: { label: 'Возвращен', className: 'bg-rose-100 text-rose-700 border border-rose-200' }
};

const HISTORY_STATUS_LABELS = {
    assigned: 'Выставлен',
    in_progress: 'Принят в работу',
    completed: 'Выполнен',
    accepted: 'Принят',
    returned: 'Возвращен на доработку',
    reopened: 'Возобновлен'
};

const TAG_LABELS = TAG_OPTIONS.reduce((acc, item) => {
    acc[item.value] = item.label;
    return acc;
}, {});

const formatDateTime = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('ru-RU');
};

const TasksView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader }) => {
    const [tasks, setTasks] = useState([]);
    const [recipients, setRecipients] = useState([]);
    const [isTasksLoading, setIsTasksLoading] = useState(false);
    const [isRecipientsLoading, setIsRecipientsLoading] = useState(false);
    const [isCreateLoading, setIsCreateLoading] = useState(false);
    const [actionLoadingKey, setActionLoadingKey] = useState('');
    const [selectedFiles, setSelectedFiles] = useState([]);
    const fileInputRef = useRef(null);

    const [form, setForm] = useState({
        subject: '',
        description: '',
        tag: 'task',
        assignedTo: ''
    });

    const notify = useCallback((message, type = 'success') => {
        if (typeof showToast === 'function') {
            showToast(message, type);
        }
    }, [showToast]);

    const buildHeaders = useCallback(() => {
        const headers = {};
        if (user?.id) headers['X-User-Id'] = String(user.id);
        if (user?.apiKey) headers['X-API-Key'] = user.apiKey;
        if (typeof withAccessTokenHeader === 'function') {
            return withAccessTokenHeader(headers);
        }
        return headers;
    }, [user?.id, user?.apiKey, withAccessTokenHeader]);

    const fetchRecipients = useCallback(async () => {
        setIsRecipientsLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/tasks/recipients`, {
                headers: buildHeaders()
            });
            setRecipients(Array.isArray(response?.data?.recipients) ? response.data.recipients : []);
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось загрузить список сотрудников', 'error');
        } finally {
            setIsRecipientsLoading(false);
        }
    }, [apiBaseUrl, buildHeaders, notify]);

    const fetchTasks = useCallback(async () => {
        setIsTasksLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/tasks`, {
                headers: buildHeaders()
            });
            setTasks(Array.isArray(response?.data?.tasks) ? response.data.tasks : []);
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось загрузить задачи', 'error');
        } finally {
            setIsTasksLoading(false);
        }
    }, [apiBaseUrl, buildHeaders, notify]);

    useEffect(() => {
        if (!user || !['admin', 'sv'].includes(user.role)) return;
        fetchRecipients();
        fetchTasks();
    }, [user, fetchRecipients, fetchTasks]);

    const handleCreateTask = async (event) => {
        event.preventDefault();
        if (!form.subject.trim()) {
            notify('Укажите тему задачи', 'error');
            return;
        }
        if (!form.assignedTo) {
            notify('Выберите сотрудника', 'error');
            return;
        }

        const body = new FormData();
        body.append('subject', form.subject.trim());
        body.append('description', form.description.trim());
        body.append('tag', form.tag);
        body.append('assigned_to', String(form.assignedTo));
        selectedFiles.forEach((file) => body.append('files', file));

        setIsCreateLoading(true);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/tasks`, body, {
                headers: buildHeaders()
            });
            notify(response?.data?.message || 'Задача создана', 'success');
            if (response?.data?.warning) {
                notify(response.data.warning, 'error');
            }
            setForm({
                subject: '',
                description: '',
                tag: 'task',
                assignedTo: ''
            });
            setSelectedFiles([]);
            if (fileInputRef.current) {
                fileInputRef.current.value = '';
            }
            await fetchTasks();
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось создать задачу', 'error');
        } finally {
            setIsCreateLoading(false);
        }
    };

    const updateTaskStatus = async (taskId, action) => {
        const comment = action === 'returned'
            ? (window.prompt('Комментарий по доработке (необязательно):', '') || '').trim()
            : '';

        const loadingKey = `${taskId}:${action}`;
        setActionLoadingKey(loadingKey);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/tasks/${taskId}/status`,
                { action, comment },
                { headers: buildHeaders() }
            );
            notify(response?.data?.message || 'Статус обновлен', 'success');
            await fetchTasks();
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось обновить статус задачи', 'error');
        } finally {
            setActionLoadingKey('');
        }
    };

    const downloadAttachment = async (attachment) => {
        try {
            const response = await axios.get(
                `${apiBaseUrl}/api/tasks/attachments/${attachment.id}/download`,
                {
                    headers: buildHeaders(),
                    responseType: 'blob'
                }
            );

            const blobType = attachment.content_type || 'application/octet-stream';
            const blob = new Blob([response.data], { type: blobType });
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = attachment.file_name || `attachment-${attachment.id}`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
        } catch (error) {
            notify(error?.response?.data?.error || 'Не удалось скачать файл', 'error');
        }
    };

    const renderTaskActions = (task) => {
        const assigneeId = Number(task?.assignee?.id || 0);
        const creatorId = Number(task?.creator?.id || 0);
        const currentUserId = Number(user?.id || 0);
        const isAssignee = assigneeId === currentUserId;
        const canReview = !isAssignee && (user?.role === 'admin' || creatorId === currentUserId || user?.role === 'sv');
        const status = task?.status;

        const buttons = [];

        if (isAssignee && (status === 'assigned' || status === 'returned')) {
            buttons.push({ action: 'in_progress', label: 'Принять в работу', className: 'bg-amber-500 hover:bg-amber-600 text-white' });
        }

        if (isAssignee && (status === 'in_progress' || status === 'returned')) {
            buttons.push({ action: 'completed', label: 'Выполнить', className: 'bg-indigo-600 hover:bg-indigo-700 text-white' });
        }

        if (canReview && status === 'completed') {
            buttons.push({ action: 'accepted', label: 'Принять', className: 'bg-emerald-600 hover:bg-emerald-700 text-white' });
            buttons.push({ action: 'returned', label: 'Вернуть', className: 'bg-rose-600 hover:bg-rose-700 text-white' });
        }

        if (canReview && ['accepted', 'completed', 'returned'].includes(status)) {
            buttons.push({ action: 'reopened', label: 'Возобновить', className: 'bg-slate-700 hover:bg-slate-800 text-white' });
        }

        if (buttons.length === 0) {
            return null;
        }

        return (
            <div className="flex flex-wrap gap-2 mt-4">
                {buttons.map((button) => {
                    const key = `${task.id}:${button.action}`;
                    const isLoading = actionLoadingKey === key;
                    return (
                        <button
                            key={key}
                            type="button"
                            onClick={() => updateTaskStatus(task.id, button.action)}
                            disabled={!!actionLoadingKey}
                            className={`px-3 py-1.5 text-sm rounded-lg transition ${button.className} ${actionLoadingKey ? 'opacity-70 cursor-not-allowed' : ''}`}
                        >
                            {isLoading ? 'Сохраняю...' : button.label}
                        </button>
                    );
                })}
            </div>
        );
    };

    if (!user || !['admin', 'sv'].includes(user.role)) {
        return null;
    }

    return (
        <div className="space-y-6">
            <div className="bg-white p-6 rounded-xl shadow-md border border-gray-200">
                <h2 className="text-2xl font-semibold text-gray-800 mb-4">Задачи</h2>
                <form onSubmit={handleCreateTask} className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="md:col-span-2">
                        <label className="block text-sm font-medium text-gray-700 mb-1">Тема</label>
                        <input
                            type="text"
                            value={form.subject}
                            onChange={(e) => setForm((prev) => ({ ...prev, subject: e.target.value }))}
                            placeholder="Введите тему задачи"
                            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                            maxLength={255}
                            disabled={isCreateLoading}
                        />
                    </div>

                    <div className="md:col-span-2">
                        <label className="block text-sm font-medium text-gray-700 mb-1">Описание</label>
                        <textarea
                            value={form.description}
                            onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
                            placeholder="Опишите задачу"
                            rows={4}
                            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
                            disabled={isCreateLoading}
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Тег</label>
                        <select
                            value={form.tag}
                            onChange={(e) => setForm((prev) => ({ ...prev, tag: e.target.value }))}
                            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                            disabled={isCreateLoading}
                        >
                            {TAG_OPTIONS.map((tag) => (
                                <option key={tag.value} value={tag.value}>{tag.label}</option>
                            ))}
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Кому отправить</label>
                        <select
                            value={form.assignedTo}
                            onChange={(e) => setForm((prev) => ({ ...prev, assignedTo: e.target.value }))}
                            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                            disabled={isCreateLoading || isRecipientsLoading}
                        >
                            <option value="">Выберите сотрудника</option>
                            {recipients.map((recipient) => (
                                <option key={recipient.id} value={recipient.id}>
                                    {recipient.name} ({recipient.role === 'sv' ? 'СВ' : 'Оператор'})
                                </option>
                            ))}
                        </select>
                    </div>

                    <div className="md:col-span-2">
                        <label className="block text-sm font-medium text-gray-700 mb-1">Файлы</label>
                        <input
                            ref={fileInputRef}
                            type="file"
                            multiple
                            onChange={(e) => setSelectedFiles(Array.from(e.target.files || []))}
                            className="w-full p-2.5 border border-gray-300 rounded-lg bg-white"
                            disabled={isCreateLoading}
                        />
                        {selectedFiles.length > 0 && (
                            <p className="mt-2 text-xs text-gray-500">
                                Прикреплено файлов: {selectedFiles.length}
                            </p>
                        )}
                    </div>

                    <div className="md:col-span-2 flex justify-end">
                        <button
                            type="submit"
                            disabled={isCreateLoading || isRecipientsLoading}
                            className={`px-5 py-2.5 rounded-lg text-white font-medium transition ${isCreateLoading ? 'bg-blue-400 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700'}`}
                        >
                            {isCreateLoading ? 'Создаю...' : 'Поставить задачу'}
                        </button>
                    </div>
                </form>
            </div>

            <div className="bg-white p-6 rounded-xl shadow-md border border-gray-200">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-xl font-semibold text-gray-800">Список задач</h3>
                    <button
                        type="button"
                        onClick={fetchTasks}
                        disabled={isTasksLoading}
                        className={`px-3 py-1.5 rounded-lg text-sm transition ${isTasksLoading ? 'bg-gray-200 text-gray-500 cursor-not-allowed' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}
                    >
                        {isTasksLoading ? 'Обновляю...' : 'Обновить'}
                    </button>
                </div>

                {isTasksLoading ? (
                    <p className="text-gray-500">Загрузка задач...</p>
                ) : tasks.length === 0 ? (
                    <p className="text-gray-500">Пока задач нет.</p>
                ) : (
                    <div className="space-y-4">
                        {tasks.map((task) => {
                            const statusMeta = TASK_STATUS_META[task.status] || { label: task.status || '—', className: 'bg-gray-100 text-gray-600 border border-gray-200' };
                            const attachments = Array.isArray(task.attachments) ? task.attachments : [];
                            const history = Array.isArray(task.history) ? task.history : [];
                            return (
                                <div key={task.id} className="border border-gray-200 rounded-xl p-4">
                                    <div className="flex flex-wrap gap-2 items-center mb-2">
                                        <h4 className="text-lg font-semibold text-gray-900">{task.subject || 'Без темы'}</h4>
                                        <span className={`px-2 py-1 text-xs rounded-full ${statusMeta.className}`}>{statusMeta.label}</span>
                                        <span className="px-2 py-1 text-xs rounded-full bg-slate-100 text-slate-700 border border-slate-200">
                                            {TAG_LABELS[task.tag] || task.tag || '—'}
                                        </span>
                                    </div>

                                    <div className="text-sm text-gray-600 space-y-1">
                                        <p><strong>Кому:</strong> {task?.assignee?.name || '—'}</p>
                                        <p><strong>Поставил:</strong> {task?.creator?.name || '—'}</p>
                                        <p><strong>Создано:</strong> {formatDateTime(task.created_at)}</p>
                                    </div>

                                    {task.description && (
                                        <p className="mt-3 text-gray-700 whitespace-pre-wrap">{task.description}</p>
                                    )}

                                    {attachments.length > 0 && (
                                        <div className="mt-3">
                                            <p className="text-sm font-medium text-gray-700 mb-2">Файлы:</p>
                                            <div className="flex flex-wrap gap-2">
                                                {attachments.map((attachment) => (
                                                    <button
                                                        key={attachment.id}
                                                        type="button"
                                                        onClick={() => downloadAttachment(attachment)}
                                                        className="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm transition"
                                                    >
                                                        <i className="fas fa-paperclip mr-1"></i>
                                                        {attachment.file_name}
                                                    </button>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    {history.length > 0 && (
                                        <div className="mt-4 bg-gray-50 border border-gray-200 rounded-lg p-3">
                                            <p className="text-sm font-medium text-gray-700 mb-2">История выполнения</p>
                                            <div className="space-y-1.5">
                                                {history.map((item) => (
                                                    <div key={item.id} className="text-sm text-gray-600">
                                                        <span className="font-medium text-gray-800">{HISTORY_STATUS_LABELS[item.status_code] || item.status_code}</span>
                                                        <span className="ml-2">{formatDateTime(item.changed_at)}</span>
                                                        {item.changed_by_name && <span className="ml-2 text-gray-500">({item.changed_by_name})</span>}
                                                        {item.comment && <span className="ml-2 text-rose-700">- {item.comment}</span>}
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    {renderTaskActions(task)}
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
};

export default TasksView;

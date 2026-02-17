import React, { useEffect, useState } from 'react';

const UserEditModal = ({ isOpen, onClose, userToEdit, svList = [], directions = [], onSave, user }) => {
    const [editedUser, setEditedUser] = useState(userToEdit || {});
    const [isLoading, setIsLoading] = useState(false);
    const [modalError, setModalError] = useState("");
    const [createdCredentials, setCreatedCredentials] = useState(null); // { login, password }
    const nameRef = React.useRef(null);

    useEffect(() => {
        // Устанавливаем defaults при открытии для режима создания
        const base = userToEdit || {};
        const defaults = {
        rate: base.rate ?? 1.0,
        direction_id: base.direction_id ?? "",
        supervisor_id: base.supervisor_id ?? (user?.id ?? ""),
        status: base.status ?? "working",
        ...base,
        };
        setEditedUser(defaults);
        setModalError("");
        setCreatedCredentials(null);
    }, [userToEdit, user]);

    useEffect(() => {
        if (isOpen) {
        setTimeout(() => {
            nameRef.current?.focus();
        }, 50);
        }
    }, [isOpen]);

    const copyToClipboard = (text) => {
        if (!text) return;
        navigator.clipboard?.writeText(text).then(
        () => {
            // не критично — можно показать toast если есть реализация
        },
        () => {
            // fallback
        }
        );
    };

    const resetForCreate = () => {
        setEditedUser({
        name: "",
        rate: 1.0,
        hire_date: "",
        direction_id: "",
        supervisor_id: user?.id ?? "",
        status: "working",
        });
        setModalError("");
        setCreatedCredentials(null);
        setTimeout(() => nameRef.current?.focus(), 50);
    };

    const handleSave = async () => {
        // Простая локальная валидация
        if (!editedUser || !editedUser.name || editedUser.name.trim().length === 0) {
        setModalError("Имя обязательно.");
        return;
        }

        if (!editedUser.direction_id && editedUser.role !== 'sv') {
        setModalError("Направление обязательно.");
        return;
        }

        if (!editedUser.hire_date) {
        setModalError("Дата найма обязательна.");
        return;
        }

        setModalError("");
        setIsLoading(true);

        try {
        const result = await onSave(editedUser); // ожидаем, что onSave возвращает результат от бэка при создании

        // Если мы в режиме создания (нет id у редактируемого пользователя) — не закрываем модалку,
        // а показываем логин/пароль, если бэк их вернул
        if (!editedUser.id) {
            // Попытки найти креды в разных форматах ответа
            const login =
            result?.login ?? result?.data?.login ?? result?.credentials?.login ?? null;
            const password =
            result?.password ?? result?.data?.password ?? result?.credentials?.password ?? null;

            if (login || password) {
            setCreatedCredentials({ login: login || "-", password: password || "-" });
            } else {
            // Если бэк не вернул креды — просто закрываем модалку / или можно показать сообщение
            // Здесь оставим сообщение и позволим админу закрыть вручную
            setModalError("Пользователь создан, но бэк не вернул логин/пароль.");
            // Можно вызвать fetchUsers по внешнему коду; предполагается, что onSave делает это.
            }
        } else {
            // режим редактирования — закрываем модалку после успешного onSave
            onClose();
        }
        } catch (error) {
        console.error("Error saving user:", error);
        const serverMsg =
            error?.response?.data?.error || error?.message || "Не удалось сохранить пользователя. Попробуйте ещё раз.";
        setModalError(serverMsg);
        } finally {
        setIsLoading(false);
        }
    };

    if (!isOpen) return null;

    const isCreateMode = !editedUser?.id;

    return (
        <>
        {/* Backdrop */}
        <div
            className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-300"
            onClick={() => {
            setModalError("");
            setCreatedCredentials(null);
            onClose();
            }}
            aria-hidden="true"
        />

        {/* Modal container (catch Escape) */}
        <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            tabIndex={-1}
            onKeyDown={(e) => {
            if (e.key === "Escape") {
                setModalError("");
                setCreatedCredentials(null);
                onClose();
            }
            }}
        >
            <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="edit-user-title"
            className="pointer-events-auto w-full max-w-lg bg-white/95 dark:bg-slate-900/95 rounded-2xl shadow-2xl overflow-hidden transform transition-all duration-300 animate-scale-in"
            onClick={(e) => e.stopPropagation()}
            >
            <div className="px-6 py-5">
                {/* Header */}
                <div className="flex items-start justify-between gap-4">
                <div className="flex flex-col gap-0.5">
                    <h2 id="edit-user-title" className="text-2xl font-bold text-gray-800 dark:text-gray-100 flex items-center gap-2">
                    {isCreateMode ?  <i className="fas fa-user-edit text-blue-600"></i> : <i className="fas fa-pen text-blue-600"></i>}
                    {isCreateMode ? "Добавить оператора" : "Редактировать пользователя"}
                    </h2>
                    {editedUser?.name && !isCreateMode && (
                    <div className="mt-1 text-lg font-semibold text-blue-700 pl-9">{editedUser.name}</div>
                    )}
                </div>

                <button
                    type="button"
                    onClick={() => {
                    setModalError("");
                    setCreatedCredentials(null);
                    onClose();
                    }}
                    aria-label="Закрыть"
                    className="rounded-md p-2 text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-slate-800 transition"
                >
                    <i className="fas fa-times text-lg" />
                </button>
                </div>

                <div className="mt-4 space-y-6">
                {/* Если режим создания — показываем только Имя, Ставка, Направление */}
                {isCreateMode &&(
                <>
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Имя</label>
                    <input
                    ref={nameRef}
                    type="text"
                    value={editedUser?.name || ""}
                    onChange={(e) => setEditedUser({ ...editedUser, name: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                    disabled={isLoading || !!createdCredentials}
                    />
                </div>

                <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Дата найма</label>
                <input
                    type="date"
                    value={editedUser?.hire_date ? editedUser.hire_date.split("T")[0] : ""}
                    onChange={(e) => setEditedUser({ ...editedUser, hire_date: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                    disabled={isLoading || !!createdCredentials}
                />
                </div>
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Ставка</label>
                    <select
                    value={editedUser?.rate ?? 1.0}
                    onChange={(e) => setEditedUser({ ...editedUser, rate: parseFloat(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                    disabled={isLoading || !!createdCredentials}
                    >
                    <option value={1.0}>1.00</option>
                    <option value={0.75}>0.75</option>
                    <option value={0.5}>0.50</option>
                    </select>
                </div>

                {/* Направление */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Направление</label>
                    <select
                    value={editedUser?.direction_id || ""}
                    onChange={(e) => setEditedUser({ ...editedUser, direction_id: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                    disabled={isLoading || !!createdCredentials}
                    >
                    <option value="">Выберите направление</option>
                    {directions.map((dir) => (
                        <option key={dir.id} value={dir.id}>
                        {dir.name}
                        </option>
                    ))}
                    </select>
                </div>
                </>
                )}
                

                {/* --- Режим редактирования: показываем остальные поля как раньше --- */}
                {!isCreateMode && (
                    <>
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Имя</label>
                        <input
                        ref={nameRef}
                        type="text"
                        value={editedUser?.name || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, name: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading}
                        />
                    </div>
                    {/* Hire date */}
                    <div>
                        <label htmlFor="hireDate" className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">
                        Дата найма
                        </label>
                        <div className="flex items-center gap-2">
                        <input
                            type="date"
                            id="hireDate"
                            value={editedUser?.hire_date ? editedUser.hire_date.split("T")[0] : ""}
                            onChange={(e) => setEditedUser({ ...editedUser, hire_date: e.target.value || null })}
                            className="px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                        />
                        {editedUser?.hire_date && (
                            <span className="text-gray-600 text-xs whitespace-nowrap">
                            Текущая: {editedUser.hire_date.split("T")[0]}
                            </span>
                        )}
                        </div>
                    </div>

                    {/* Status */}
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Статус</label>
                        <select
                        value={editedUser?.status || "working"}
                        onChange={(e) => setEditedUser({ ...editedUser, status: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading}
                        >
                        <option value="working">Работает</option>
                        <option value="fired">Уволен</option>
                        <option value="unpaid_leave">Без содержания</option>
                        </select>
                    </div>

                    {/* Supervisor, Rate и Direction (если применимо) */}
                    {userToEdit?.role !== "sv" && (
                        <>
                        {user?.role === "admin" && (
                            <div className="grid grid-cols-1 gap-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Супервайзер</label>
                                <select
                                value={editedUser?.supervisor_id || ""}
                                onChange={(e) => setEditedUser({ ...editedUser, supervisor_id: e.target.value })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading}
                                >
                                <option value="">Выберите супервайзера</option>
                                {(() => {
                                    const all = svList || [];
                                    const active = all.filter(sv => sv.status === 'working' || sv.status === 'unpaid_leave' || !sv.status);
                                    const fired = all.filter(sv => sv.status === 'fired');
                                    return (
                                        <>
                                            {active.map(sv => (
                                            <option key={sv.id} value={sv.id}>{sv.name}</option>
                                            ))}
                                        </>
                                    );
                                })()}
                                </select>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Ставка</label>
                                <select
                                value={editedUser?.rate || 1.0}
                                onChange={(e) => setEditedUser({ ...editedUser, rate: parseFloat(e.target.value) })}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                                disabled={isLoading}
                                >
                                <option value={1.0}>1.00</option>
                                <option value={0.75}>0.75</option>
                                <option value={0.5}>0.50</option>
                                </select>
                            </div>
                            </div>
                        )}

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Направление</label>
                            <select
                            value={editedUser?.direction_id || ""}
                            onChange={(e) => setEditedUser({ ...editedUser, direction_id: e.target.value })}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                            disabled={isLoading}
                            >
                            <option value="">Выберите направление</option>
                            {directions.map((dir) => (
                                <option key={dir.id} value={dir.id}>
                                {dir.name}
                                </option>
                            ))}
                            </select>
                        </div>
                        </>
                    )}

                    {/* New login */}
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Новый логин</label>
                        <input
                        type="text"
                        value={editedUser?.new_login || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, new_login: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading}
                        />
                    </div>

                    {/* New password */}
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1">Новый пароль</label>
                        <input
                        type="password"
                        value={editedUser?.new_password || ""}
                        onChange={(e) => setEditedUser({ ...editedUser, new_password: e.target.value })}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all bg-white/90 dark:bg-slate-800 text-gray-900 dark:text-gray-100"
                        disabled={isLoading}
                        />
                    </div>
                    </>
                )}

                {/* Если создали — показываем креды */}
                {createdCredentials && (
                    <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
                    <div className="font-medium text-sm text-green-800 mb-2">Данные аккаунта</div>
                    <div className="text-sm text-gray-800">Логин: <span className="font-semibold">{createdCredentials.login}</span></div>
                    <div className="text-sm text-gray-800">Пароль: <span className="font-semibold">{createdCredentials.password}</span></div>

                    <div className="mt-3 flex gap-2">
                        <button
                        onClick={() => {
                            copyToClipboard(`Логин: ${createdCredentials.login}\nПароль: ${createdCredentials.password}`);
                        }}
                        className="px-3 py-1 bg-gray-200 rounded-md hover:bg-gray-300 text-sm"
                        >
                        Копировать
                        </button>

                        <button
                        onClick={() => {
                            // Сброс формы для создания ещё одного
                            resetForCreate();
                        }}
                        className="px-3 py-1 bg-blue-600 text-white rounded-md hover:bg-blue-700 text-sm"
                        >
                        Создать ещё
                        </button>

                        <button
                        onClick={() => {
                            setCreatedCredentials(null);
                            onClose();
                        }}
                        className="px-3 py-1 bg-gray-100 rounded-md hover:bg-gray-200 text-sm"
                        >
                        Закрыть
                        </button>
                    </div>
                    </div>
                )}

                {/* Error message */}
                <div aria-live="polite" className="min-h-[1.25rem]">
                    {modalError && <p className="text-sm text-red-600 dark:text-red-400">{modalError}</p>}
                </div>

                {/* Actions */}
                {!createdCredentials && (
                    <div className="flex justify-end items-center gap-3 pt-2">
                    <button
                        onClick={() => {
                        setModalError("");
                        setCreatedCredentials(null);
                        onClose();
                        }}
                        className="px-4 py-2 bg-gray-200 text-gray-800 rounded-lg hover:bg-gray-300 transition-all duration-200 font-medium"
                        disabled={isLoading}
                    >
                        Отмена
                    </button>

                    <button
                        onClick={handleSave}
                        disabled={isLoading}
                        className={`px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-all duration-200 font-medium flex items-center gap-2 ${
                        isLoading ? "opacity-60 cursor-not-allowed" : ""
                        }`}
                    >
                        {isLoading ? (
                        <>
                            <i className="fas fa-spinner fa-spin" /> Сохранение...
                        </>
                        ) : isCreateMode ? (
                        "Создать"
                        ) : (
                        "Сохранить"
                        )}
                    </button>
                    </div>
                )}

                {createdCredentials && (
                    <p className="mt-2 text-xs text-gray-400">
                    Учетные данные показаны выше — обязательно сохраните их, они видны только сейчас.
                    </p>
                )}

                {!createdCredentials && <p className="mt-2 text-xs text-gray-400">Нажмите Esc, кликните вне модалки или крестик вверху, чтобы закрыть.</p>}
                </div>
            </div>
            </div>
        </div>
        </>
    );
    };

export default UserEditModal;

import React, { useEffect, useState } from 'react';
import FaIcon from './FaIcon';
import { getInstallState, promptInstall, requestInstallSheet, subscribeToInstallState } from '../../utils/pwa';

/*
 * Пункт «Установить приложение» в меню аккаунта.
 *
 * Зачем он, если панель с предложением приходит сама. Панель приходит ОДИН раз
 * и после «Позже» замолкает на две недели — а решение поставить портал на
 * телефон обычно приходит позже самого предложения («а как ты открываешь его
 * так, без адресной строки?»). Без этого пункта единственным ответом было бы
 * «найдите в меню браузера», то есть в разных местах на разных телефонах.
 *
 * Компонент ПОДПИСЫВАЕТСЯ САМ и не принимает состояния пропом. Меню сайдбара
 * собрано в useMemo со списком зависимостей на полсотни значений: новое
 * значение, положенное в состояние App, замёрзло бы здесь на первом рендере,
 * и пункт молча перестал бы появляться. Тот же приём, что у окна «Новость дня».
 */
const InstallAppMenuItem = ({ onPicked }) => {
    const [install, setInstall] = useState(getInstallState);

    useEffect(() => subscribeToInstallState(setInstall), []);

    /* Портал уже запущен с иконки — ставить нечего. На компьютере пункт
       появляется только тогда, когда браузер отдал событие установки. */
    const available = !install.standalone && (install.canPrompt || install.platform === 'ios');
    if (!available) return null;

    const handleClick = () => {
        if (onPicked) onPicked();
        /* Есть системное окно — открываем его сразу: лишний экран с кнопкой
           «Установить» перед кнопкой «Установить» никому не нужен. На iPhone
           окна нет, показываем панель с двумя шагами. */
        if (install.canPrompt) promptInstall();
        else requestInstallSheet();
    };

    return (
        <>
            <div className="border-t border-gray-200" />
            <button
                onClick={handleClick}
                className="w-full text-left px-4 py-2 hover:bg-gray-100 text-black"
            >
                <FaIcon className="fas fa-mobile-alt mr-2"></FaIcon> Установить приложение
            </button>
        </>
    );
};

export default InstallAppMenuItem;

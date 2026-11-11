import React, { useCallback, useEffect, useRef, useState } from 'react';
import { RotateCw, Users } from 'lucide-react';
import SyntonyAccessSheet from './SyntonyAccessSheet';
import SyntonyWindow from './SyntonyWindow';
import { SECTIONS } from './syntonyRules';
import './syntony.css';

/*
 * Разделы «Чаты Syntony» и «Заявки Syntony» — окна Syntony во всю высоту страницы
 * (ТЗ Syntony от 09.11.2026, задача 3).
 *
 * ОДИН ХОЗЯИН НА ОБА РАЗДЕЛА, И ОКНА НЕ ЗАКРЫВАЮТСЯ ПРИ УХОДЕ В ДРУГОЙ РАЗДЕЛ. Открытое
 * окно остаётся в дереве спрятанным (display: none), пока человек ходит по порталу:
 * вернулся — тот же открытый чат и недописанный ответ, без новой ссылки и новой
 * загрузки. Иначе каждый заход в «Вики» за справкой сбрасывал бы оператору окно. Так же
 * в портале живёт фрейм «Журнала оценок». Ссылку при этом просим, как и требует ТЗ,
 * только при открытии окна — возвращение к уже открытому открытием не является.
 *
 * Над окном — одна тонкая строка: название раздела, «Открыть заново» (ТЗ, задача 5:
 * окно само пишет «Откройте окно заново» через 8 часов и когда связь сняли) и, у
 * супер-админа, «Доступ».
 *
 * ВЫСОТА — БЕЗ ПРОЦЕНТОВ. На компьютере раздел берёт всю высоту контента (h-full), на
 * телефоне оболочка высоту у контента снимает, и раздел растягивает flex (syntony.css).
 * Поэтому окно стоит в своей области абсолютно: так ему не нужна «определённая» высота
 * родителя, и оно одинаково занимает её в обоих случаях.
 */

const barButton = 'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full px-3 text-[12.5px] font-semibold transition active:scale-[0.97]';

const SyntonySection = ({ sectionKey, hidden, apiBaseUrl, withAccessTokenHeader, canManage, onAccess, registerReload }) => {
    const section = SECTIONS[sectionKey];
    const windowRef = useRef(null);
    useEffect(() => registerReload(sectionKey, () => windowRef.current?.reload()), [registerReload, sectionKey]);
    /* «Открыть заново» над окном — только пока окно открыто: тогда она отвечает на его
       собственное «Откройте окно заново». В ошибке ту же кнопку несёт само сообщение, и
       вторая рядом была бы повтором. */
    const [phase, setPhase] = useState('loading');

    return (
        <section
            className="syntony-section flex h-full min-h-0 flex-col bg-white"
            style={hidden ? { display: 'none' } : undefined}
            aria-hidden={hidden || undefined}
            // По этой отметке syntony.css растягивает на телефоне только видимый раздел:
            // спрятанное окно не должно менять раскладку чужого раздела.
            data-active={hidden ? undefined : ''}
            data-testid={`syntony-section-${section.scope}`}
        >
            {/* Слева на компьютере — запас под кнопку сворачивания сайдбара: она выступает
                за его край на 16 px и легла бы на название. */}
            <header className="syntony-bar flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-200/70 bg-white px-4 sm:pl-8">
                <h1 className="min-w-0 truncate text-[15px] font-semibold text-slate-900">{section.title}</h1>
                <div className="flex shrink-0 items-center gap-1">
                    {phase === 'ready' && (
                        <button type="button" onClick={() => windowRef.current?.reload()}
                            title="Запросить новую ссылку и открыть окно заново" aria-label="Открыть заново"
                            className={`${barButton} text-slate-500 hover:bg-slate-100 hover:text-slate-700`}>
                            <RotateCw size={14} aria-hidden="true" />
                            {/* На телефоне — только значок: строка одна, и ей не во что переноситься. */}
                            <span className="hidden sm:inline">Открыть заново</span>
                        </button>
                    )}
                    {canManage && (
                        <button type="button" onClick={onAccess}
                            className={`${barButton} bg-slate-100 text-slate-600 hover:bg-slate-200`}>
                            <Users size={14} aria-hidden="true" /> Доступ
                        </button>
                    )}
                </div>
            </header>
            {/* Доска заявок уже 768 px не помещается (ТЗ): на узком экране область
                прокручивается вбок, а окно остаётся нужной ширины. */}
            <div className={`relative min-h-0 flex-1 ${section.minWidth ? 'overflow-x-auto overflow-y-hidden' : 'overflow-hidden'}`}>
                <div className="absolute inset-y-0 left-0 w-full" style={section.minWidth ? { minWidth: section.minWidth } : undefined}>
                    <SyntonyWindow
                        ref={windowRef}
                        apiBaseUrl={apiBaseUrl}
                        withAccessTokenHeader={withAccessTokenHeader}
                        scope={section.scope}
                        manager={canManage}
                        title={section.title}
                        onPhase={setPhase}
                        guard
                    />
                </div>
            </div>
        </section>
    );
};

const SyntonyView = ({ active = null, apiBaseUrl, withAccessTokenHeader, showToast, canManage = false, currentUserId = null }) => {
    // Какие окна уже открывали: они остаются жить, пока человек в портале.
    const [opened, setOpened] = useState(() => (SECTIONS[active] ? [active] : []));
    useEffect(() => {
        if (!SECTIONS[active]) return;
        setOpened((list) => (list.includes(active) ? list : [...list, active]));
    }, [active]);

    const [accessOpen, setAccessOpen] = useState(false);
    const reloaders = useRef({});
    const registerReload = useCallback((key, reload) => {
        reloaders.current[key] = reload;
        return () => { delete reloaders.current[key]; };
    }, []);

    /* Свою строку в списке меняли (добавили себя, сменили почту, сняли) — открытые окна
       просят ссылку заново: ответ Syntony теперь будет другим. */
    const closeAccess = useCallback((selfChanged) => {
        setAccessOpen(false);
        if (selfChanged === true) Object.values(reloaders.current).forEach((reload) => reload());
    }, []);

    const shown = SECTIONS[active] && !opened.includes(active) ? [...opened, active] : opened;

    return (
        <>
            {shown.map((key) => (
                <SyntonySection
                    key={key}
                    sectionKey={key}
                    hidden={key !== active}
                    apiBaseUrl={apiBaseUrl}
                    withAccessTokenHeader={withAccessTokenHeader}
                    canManage={canManage}
                    onAccess={() => setAccessOpen(true)}
                    registerReload={registerReload}
                />
            ))}
            {canManage && (
                <SyntonyAccessSheet
                    open={accessOpen}
                    onClose={closeAccess}
                    apiBaseUrl={apiBaseUrl}
                    withAccessTokenHeader={withAccessTokenHeader}
                    showToast={showToast}
                    currentUserId={currentUserId}
                />
            )}
        </>
    );
};

export default SyntonyView;

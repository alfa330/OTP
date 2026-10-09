import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import IcoreMark from '../components/common/IcoreMark.jsx';
import IcoreAssistantMark, { ICORE_ASSISTANT_EFFECTS } from '../components/assistant/IcoreAssistantMark.jsx';
import './preview.css';

function Symbol({ name, ...props }) {
    const paths = {
        sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></>,
        moon: <path d="M20 15.1A8.4 8.4 0 0 1 8.9 4a8.4 8.4 0 1 0 11.1 11.1Z" />,
        grid: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M3 9h18M3 15h18M9 3v18M15 3v18" /></>,
        pause: <path d="M8 5v14M16 5v14" />,
        play: <path d="m8 4 12 8-12 8Z" />,
        check: <path d="m5 12 4 4L19 6" />,
        arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
        close: <path d="m6 6 12 12M6 18 18 6" />,
        book: <><path d="M4 4h12a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3Zm0 12a3 3 0 0 1 3-3h12" /><path d="M8 7h7M8 10h5" /></>,
    };
    return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}

function App() {
    const [selected, setSelected] = useState('aurora');
    const [theme, setTheme] = useState('light');
    const [checker, setChecker] = useState(false);
    const [paused, setPaused] = useState(false);
    const [hidden, setHidden] = useState(document.hidden);
    const [chatOpen, setChatOpen] = useState(false);
    const chosen = ICORE_ASSISTANT_EFFECTS.find(item => item.id === selected);
    const animate = !paused && !hidden;

    useEffect(() => {
        const handleVisibility = () => setHidden(document.hidden);
        document.addEventListener('visibilitychange', handleVisibility);
        return () => document.removeEventListener('visibilitychange', handleVisibility);
    }, []);

    return (
        <main className="icon-lab" data-icon-theme={theme} data-checker={checker ? 'true' : 'false'}>
            <div className="lab-wrap">
                <header className="lab-header">
                    <a href="#top" className="lab-brand" aria-label="iCORE, начало страницы"><IcoreMark /><span>iCORE</span><span className="lab-brand-divider" /><span className="lab-brand-note">Дизайн помощника</span></a>
                    <span className="lab-edition">ИССЛЕДОВАНИЕ ФОРМЫ <span>01—04</span></span>
                </header>

                <section className="lab-intro" id="top">
                    <div className="lab-kicker"><span /> ЗНАКОМЫЙ ЗНАК. НОВОЕ ДВИЖЕНИЕ.</div>
                    <h1>iCORE. Лёгкость в деталях.</h1>
                    <p>Прозрачная иконка помощника с живым светом внутри.<br className="desktop-break" /> Четыре характера — выберите свой.</p>
                </section>

                <section className="lab-variants" aria-labelledby="variants-title">
                    <div className="lab-toolbar">
                        <div className="lab-section-label" id="variants-title">ВАРИАНТЫ <span>04</span></div>
                        <div className="lab-controls">
                            <div className="lab-segment" role="group" aria-label="Фон предпросмотра">
                                <button aria-pressed={theme === 'light'} onClick={() => setTheme('light')}><Symbol name="sun" /><span>Светлый</span></button>
                                <button aria-pressed={theme === 'dark'} onClick={() => setTheme('dark')}><Symbol name="moon" /><span>Тёмный</span></button>
                            </div>
                            <button className="lab-tool" aria-pressed={checker} onClick={() => setChecker(value => !value)} title="Проверить прозрачность"><Symbol name="grid" /><span>Сетка</span></button>
                            <button className="lab-tool" aria-pressed={paused} onClick={() => setPaused(value => !value)} title={paused ? 'Продолжить анимацию' : 'Остановить анимацию'}><Symbol name={paused ? 'play' : 'pause'} /><span>{paused ? 'Продолжить' : 'Пауза'}</span></button>
                        </div>
                    </div>

                    <div className="lab-card-grid" role="group" aria-label="Выбор варианта иконки">
                        {ICORE_ASSISTANT_EFFECTS.map(item => (
                            <button className={`lab-card${selected === item.id ? ' lab-card--selected' : ''}`} key={item.id} aria-pressed={selected === item.id} aria-label={`${item.number}. ${item.name}`} onClick={() => setSelected(item.id)}>
                                <div className="lab-card-top"><span>{item.number}</span><span className="lab-selection-dot">{selected === item.id && <Symbol name="check" width="13" height="13" />}</span></div>
                                <div className="lab-icon-stage"><IcoreAssistantMark effect={item.id} size={132} animated={animate} /></div>
                                <div className="lab-card-description"><span className="lab-card-caption">{item.caption}</span><h2>{item.name}</h2><p>{item.description}</p></div>
                                <div className="lab-card-footer"><span>{selected === item.id ? 'Выбран для просмотра' : 'Посмотреть в интерфейсе'}</span><Symbol name={selected === item.id ? 'check' : 'arrow'} width="16" height="16" /></div>
                            </button>
                        ))}
                    </div>
                    <div className="lab-under-grid"><span><span className="lab-tiny-dot" /> Анимация внутри фирменного контура</span><span>Прозрачный фон · Оригинальная форма iCORE</span></div>
                </section>

                <section className="lab-context" aria-labelledby="context-title">
                    <div className="lab-context-copy">
                        <div className="lab-section-label">В ИНТЕРФЕЙСЕ</div>
                        <h2 id="context-title">Маленькая деталь.<br />Свой характер.</h2>
                        <p>Так знак выглядит на странице портала. Нажмите на иконку в углу, чтобы открыть пример панели помощника.</p>
                        <div className="lab-choice" role="status"><span>{chosen.number}</span><div><small>Сейчас в предпросмотре</small><strong>{chosen.name}</strong></div></div>
                        <div className="lab-sizes">
                            {[{ size: 56, label: 'Кнопка' }, { size: 26, label: 'В шапке' }].map(item => <div key={item.size}><div><IcoreAssistantMark effect={selected} size={item.size} animated={animate} /></div><span>{item.label} <b>{item.size} px</b></span></div>)}
                        </div>
                    </div>

                    <div className="lab-portal">
                        <div className="lab-portal-bar"><span className="lab-window-dots"><i /><i /><i /></span><span>iCORE / База знаний</span><span className="lab-preview-badge">ПРЕДПРОСМОТР</span></div>
                        <div className="lab-portal-body">
                            <aside className="lab-sidebar" aria-hidden="true"><IcoreMark /><span className="lab-nav-square" /><Symbol name="book" /><span className="lab-nav-lines" /><span className="lab-nav-square lab-nav-square--small" /><span className="lab-sidebar-avatar">А</span></aside>
                            <div className="lab-document">
                                <div className="lab-breadcrumb">Рабочее пространство <span>/</span> База знаний</div>
                                <span className="lab-doc-badge"><Symbol name="book" width="14" height="14" /> БАЗА ЗНАНИЙ</span>
                                <h3>Всё нужное — рядом.</h3>
                                <p>Ответы, инструкции и поддержка в одном месте.</p>
                                <div className="lab-doc-tiles"><div><span>01</span><strong>Первые шаги</strong><i /></div><div><span>02</span><strong>Рабочие процессы</strong><i /></div></div>
                                <div className="lab-doc-lines" aria-hidden="true"><i /><i /><i /></div>
                            </div>
                            {chatOpen && <div className="lab-chat" id="preview-chat" role="region" aria-label="Пример панели помощника">
                                <div className="lab-chat-header"><IcoreAssistantMark effect={selected} size={26} animated={animate} /><div><strong>Помощник iCORE</strong><span>Пример оформления</span></div><button aria-label="Закрыть пример панели" onClick={() => setChatOpen(false)}><Symbol name="close" /></button></div>
                                <div className="lab-chat-body"><IcoreAssistantMark effect={selected} size={64} animated={animate} /><h3>Чем помочь?</h3><p>Найду нужную инструкцию<br />и помогу разобраться.</p></div>
                                <div className="lab-chat-input">Ваш вопрос…<Symbol name="arrow" /></div>
                            </div>}
                            <button className="lab-assistant-button" aria-label={chatOpen ? 'Закрыть пример помощника' : 'Открыть пример помощника'} aria-expanded={chatOpen} aria-controls={chatOpen ? 'preview-chat' : undefined} onClick={() => setChatOpen(value => !value)}><IcoreAssistantMark effect={selected} size={56} animated={animate} /></button>
                        </div>
                    </div>
                </section>
                <footer className="lab-footer"><span>iCORE <span className="lab-footer-plus">/</span> ASSISTANT EXPLORATIONS</span><span>Выберите вариант и напишите его номер в чате.</span></footer>
            </div>
        </main>
    );
}

createRoot(document.getElementById('root')).render(<App />);

/* Мост интерфейса клиента с главным процессом. */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('icore', {
    config: () => ipcRenderer.invoke('app:config'),
    login: (payload) => ipcRenderer.invoke('auth:login', payload),
    confirmNews: (payload) => ipcRenderer.invoke('news:confirm', payload),
    passQuiz: (payload) => ipcRenderer.invoke('news:quiz', payload),
    onNewsShow: (fn) => ipcRenderer.on('news:show', (_e, item) => fn(item)),
    onNewsHide: (fn) => ipcRenderer.on('news:hide', () => fn()),
    onAuthExpired: (fn) => ipcRenderer.on('auth:expired', () => fn()),
    onOktellFailed: (fn) => ipcRenderer.on('oktell:failed', (_e, info) => fn(info)),
    onAutologin: (fn) => ipcRenderer.on('oktell:autologin', (_e, info) => fn(info)),
});

/* Адрес API для модулей, которым App.jsx его не передаёт (та же строка, что в App.jsx и embedLaunch.js):
   на своём домене зашивается при сборке через VITE_API_BASE_URL, без него — основной адрес. Токен к
   запросам axios подмешивает перехватчик App.jsx. */
export const API_BASE_URL = import.meta.env?.VITE_API_BASE_URL || 'https://api.icorehub.kz';

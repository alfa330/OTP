/* Шрифты «Задач» (Syne + DM Sans) с Google Fonts. DM Sans набраны и «Курсы».
 *
 * Ссылку ставил модуль «Задач» в момент импорта, а импортировались «Задачи»
 * вместе с главным файлом приложения — поэтому шрифт был у всех и всегда, и
 * «Курсы» держались на нём, своей ссылки не имея. С 14.10.2026 оба раздела
 * грузятся отдельными чанками (ускорение запуска с иконки), и шрифт подключает
 * тот раздел, что открыт первым. При запуске портала запроса к Google больше нет. */
const SECTION_FONTS_HREF = 'https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;1,9..40,300&display=swap';

export const ensureSectionFonts = () => {
    if (typeof document === 'undefined' || !document.head) return;
    if (document.head.querySelector('link[data-otp-section-fonts]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = SECTION_FONTS_HREF;
    link.dataset.otpSectionFonts = '1';
    document.head.appendChild(link);
};

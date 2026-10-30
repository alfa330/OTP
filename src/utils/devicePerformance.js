/* Слабый компьютер: два ядра или не больше 4 ГБ памяти (navigator.deviceMemory
 * есть только в Chromium — где его нет, судим по ядрам). На таких машинах
 * украшения и тыква-помощник стоят без постоянных анимаций: дыхание, мерцание,
 * покачивание и пролёты выключены, а реакции на действия человека (эмоции,
 * взгляд за курсором) остаются — они ничего не стоят, пока человек не двигается.
 */
export const isLowEndDevice = (nav = typeof navigator === 'undefined' ? null : navigator) => {
    if (!nav) return false;
    const cores = Number(nav.hardwareConcurrency) || 8;
    const memory = Number(nav.deviceMemory) || 8;
    return cores <= 2 || memory <= 4;
};

/*
 * Публикация собранного инсталлятора на портал.
 *
 * Запускается на билд-машине после `npm run dist`. Токен берётся из окружения и
 * в приложение не попадает никогда — он даёт право положить файл, который парк
 * машин затем поставит сам.
 *
 *   OKTELL_CLIENT_PUBLISH_TOKEN=… ICORE_API=https://… npm run publish-release
 *
 * Хэш файла считает СЕРВЕР, а не мы: присланному клиентом верить нельзя, и
 * именно серверный sha256 попадает в манифест, по которому программа решает,
 * ставить обновление или отбросить его.
 */
const fs = require('fs');
const path = require('path');

const api = (process.env.ICORE_API || '').replace(/\/+$/, '');
const token = process.env.OKTELL_CLIENT_PUBLISH_TOKEN || '';
const mandatory = /^(1|true|yes|on)$/i.test(process.env.MANDATORY || '');
const notes = process.env.NOTES || '';

function fail(message) {
    console.error(`Публикация не удалась: ${message}`);
    process.exit(1);
}

if (!api) fail('не задан ICORE_API');
if (!token) fail('не задан OKTELL_CLIENT_PUBLISH_TOKEN');

const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
const version = pkg.version;
const file = path.join(__dirname, '..', 'dist', `iCORE-Oktell-Setup-${version}.exe`);
if (!fs.existsSync(file)) fail(`нет собранного файла ${file} — сначала npm run dist`);

(async () => {
    const form = new FormData();
    form.set('version', version);
    form.set('notes', notes);
    form.set('mandatory', mandatory ? '1' : '0');
    form.set('file', new Blob([fs.readFileSync(file)]), path.basename(file));

    const response = await fetch(`${api}/api/oktell_client/publish`, {
        method: 'POST',
        headers: { 'X-Publish-Token': token },
        body: form,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) fail(`${response.status} ${payload.error || ''}`);
    console.log(`Опубликовано: версия ${payload.release?.version}, `
        + `sha256 ${payload.release?.sha256}, `
        + `${mandatory ? 'обязательное' : 'обычное'} обновление`);
})().catch((error) => fail(error.message));

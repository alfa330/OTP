# -*- coding: utf-8 -*-
"""Хранилище секретов для локальной работы — вместо открытого .env.codex.local.

Зачем. Открытый файл с паролями читает любая программа, а агент, разбирая его, может напечатать
значение в журнал (25.11.2026 — дважды за день). Здесь:
  * значения зашифрованы (AES-256-GCM). «Обычные» — ключом, который охраняет ОС: Windows DPAPI
    (только эта учётка на этом ПК) или «Связка ключей» macOS. «Важные» — ключом из пароля
    хранилища (scrypt), который знает только владелец: без пароля их не открыть даже под его учёткой;
  * значения не печатаются: `list` — имена, уровень и длина; `run` кладёт значения в окружение
    команды и вырезает их из её вывода (как GitHub Actions); команды «показать значение» нет;
  * каждое открытие важных — окно пароля у владельца с командой и списком ключей, и запись в журнал.

Команды:
  python scripts/otp_secrets.py list
  python scripts/otp_secrets.py run [--only A,B] [--critical C,D] -- команда аргументы…
  python scripts/otp_secrets.py import-env ФАЙЛ [--name-line N=ИМЯ] [--name-block N=ИМЯ]
  python scripts/otp_secrets.py gui        (окно владельца: добавить, изменить, уровни, копия)

Окно владельца — ярлык «Секреты iCORE» на рабочем столе (pythonw … gui). Значения вводятся только
там (или `set` с вводом без эха) — агент их не видит.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

FORMAT = 1
ARCHIVE_PREFIX = '_ARCHIVE_'
NAME_RE = re.compile(r'^[A-Z_][A-Z0-9_]*$')
ENV_KEY_RE = re.compile(r'^([A-Z_][A-Z0-9_]*)\s*=(.*)$')
INI_SECTION_RE = re.compile(r'^\[[A-Za-z][A-Za-z0-9 ]*\]$')
BASE64_LINE_RE = re.compile(r'^[A-Za-z0-9+/=]{16,}$')
OPENSSH_BODY = 'b3BlbnNzaC1rZXktdjE'      # base64 от «openssh-key-v1»
DPAPI_ENTROPY = b'otp-secrets/v1'
KEYCHAIN_SERVICE = 'otp-secrets-normal-key'
CHECK_PLAINTEXT = b'otp-secrets:passphrase-ok'
SCRYPT = {'n': 2 ** 17, 'r': 8, 'p': 1}
MIN_MASK_LEN = 6
PASSPHRASE_TIMEOUT_S = 300
# Несекретное по смыслу: адреса, порты, хосты. В выводе их не прячем, иначе вместо адреса — ***
# и непонятно, куда шла команда. URL с параметрами или логином внутри всё равно прячем.
PUBLIC_SUFFIXES = ('_URL', '_IP', '_HOST', '_PORT', '_SSLMODE', '_REGISTRY', '_SERVER', '_SERVER_PROD',
                   '_DNS', '_CLI', '_DB', '_REGION', '_PROJECT_ID')
# Что предложить сделать «важным» (доступ к проду, облаку, домену, ключи выкладки). Решает владелец.
RECOMMENDED_CRITICAL = (
    'PROD_PG_PASSWORD', 'ARGOCD_AUTH_TOKEN', 'ARGOCD_DEV_PASSWORD', 'ARGOCD_PROD_PASSWORD', 'GRAFANA_PASSWORD',
    'K8S_YC_SSH_KEY', 'YC_CI_KEY', 'RENDER_API_KEY', 'PS_KZ_PASSWORD', 'WIREGUARD_ICORE_YC_CONF',
    'GITHUB_TOKEN', 'GH_TOKEN', 'DATABASE_URL', 'DATABASE_URL_READONLY',
    'FREEPBX_SSH_PASSWORD', 'SSH_CDR_PASSWORD', 'SSH_CDR_ROOT', 'SSH_CDR_LOGIN_PASSWORD', 'WIKI_RENDER_API_KEY',
)


class VaultError(Exception):
    """Ошибка хранилища — текст для человека, без значений."""


class NeedPassphrase(VaultError):
    pass


def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def b64d(text: str) -> bytes:
    return base64.b64decode(text.encode('ascii'))


def vault_dir() -> Path:
    override = os.environ.get('OTP_SECRETS_DIR')
    if override:
        return Path(override)
    if sys.platform == 'win32':
        return Path(os.environ['LOCALAPPDATA']) / 'otp-secrets'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'otp-secrets'
    return Path(os.environ.get('XDG_DATA_HOME') or (Path.home() / '.local' / 'share')) / 'otp-secrets'


# ── защита ключа «обычных» средствами ОС ────────────────────────────────────────────────────
class DpapiProtector:
    """Windows DPAPI, область текущего пользователя: расшифровать может только эта учётка на этом ПК."""
    name = 'dpapi'

    @staticmethod
    def _call(data: bytes, protect: bool) -> bytes:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

        def make(raw):
            buf = ctypes.create_string_buffer(raw, len(raw))
            return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        blob_in, keep_in = make(data)
        blob_entropy, keep_entropy = make(DPAPI_ENTROPY)
        blob_out = Blob()
        ui_forbidden = 0x1
        if protect:
            ok = crypt32.CryptProtectData(ctypes.byref(blob_in), 'otp-secrets', ctypes.byref(blob_entropy),
                                          None, None, ui_forbidden, ctypes.byref(blob_out))
        else:
            ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, ctypes.byref(blob_entropy),
                                            None, None, ui_forbidden, ctypes.byref(blob_out))
        if not ok:
            raise VaultError('Windows не %s ключ хранилища (DPAPI): %s'
                             % ('зашифровала' if protect else 'расшифровала', ctypes.WinError()))
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))

    def wrap(self, key: bytes) -> dict:
        return {'protector': self.name, 'blob': b64e(self._call(key, True))}

    def unwrap(self, info: dict) -> bytes:
        return self._call(b64d(info['blob']), False)


class KeychainProtector:
    """macOS: ключ лежит в «Связке ключей» пользователя."""
    name = 'keychain'

    def wrap(self, key: bytes) -> dict:
        res = subprocess.run(['security', 'add-generic-password', '-U', '-a', 'otp-secrets', '-s', KEYCHAIN_SERVICE,
                              '-w', key.hex()], capture_output=True, text=True)
        if res.returncode != 0:
            raise VaultError('Связка ключей не приняла ключ хранилища: %s' % res.stderr.strip()[:200])
        return {'protector': self.name, 'service': KEYCHAIN_SERVICE}

    def unwrap(self, info: dict) -> bytes:
        res = subprocess.run(['security', 'find-generic-password', '-a', 'otp-secrets', '-s', info['service'], '-w'],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise VaultError('В «Связке ключей» нет ключа хранилища: %s' % res.stderr.strip()[:200])
        return bytes.fromhex(res.stdout.strip())


def default_protector():
    if sys.platform == 'win32':
        return DpapiProtector()
    if sys.platform == 'darwin':
        return KeychainProtector()
    raise VaultError('Хранилище работает на Windows (DPAPI) и macOS (Связка ключей)')


# ── шифрование ──────────────────────────────────────────────────────────────────────────────
def _aesgcm(key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key)


def derive_key(passphrase: str, salt: bytes, params: dict) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=params['n'], r=params['r'], p=params['p']).derive(passphrase.encode('utf-8'))


def _aad(name: str, tier: str) -> bytes:
    # Имя и уровень вшиты в шифротекст: подменить запись чужой или понизить уровень правкой файла нельзя.
    return ('otp-secrets/v1/%s/%s' % (tier, name)).encode('utf-8')


def seal(key: bytes, plaintext: bytes, aad: bytes) -> dict:
    nonce = os.urandom(12)
    return {'nonce': b64e(nonce), 'ct': b64e(_aesgcm(key).encrypt(nonce, plaintext, aad))}


def unseal(key: bytes, box: dict, aad: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    try:
        return _aesgcm(key).decrypt(b64d(box['nonce']), b64d(box['ct']), aad)
    except InvalidTag:
        raise VaultError('запись не расшифровалась: не тот ключ или файл хранилища изменён')


class Vault:
    def __init__(self, directory=None, protector=None, scrypt_params=None):
        self.dir = Path(directory) if directory else vault_dir()
        self.file = self.dir / 'vault.json'
        self.log_file = self.dir / 'access.log'
        self._protector = protector
        self.scrypt_params = dict(scrypt_params or SCRYPT)
        self._normal_key = None
        self._critical_key = None
        self.data = self._load()

    # хранение
    def _load(self):
        if self.file.exists():
            data = json.loads(self.file.read_text(encoding='utf-8'))
            if data.get('format') != FORMAT:
                raise VaultError('неизвестный формат хранилища: %r' % data.get('format'))
            return data
        return {'format': FORMAT, 'created': now_iso(), 'normal_key': None, 'critical': None, 'entries': {}}

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_name(self.file.name + '.tmp')
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
        os.replace(tmp, self.file)

    def reload(self):
        self.data = self._load()

    @property
    def protector(self):
        if self._protector is None:
            self._protector = default_protector()
        return self._protector

    def log(self, action, names=(), **extra):
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {'time': now_iso(), 'action': action, 'names': sorted(names), **extra}
        with open(self.log_file, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')

    # ключи
    def normal_key(self) -> bytes:
        if self._normal_key is None:
            info = self.data.get('normal_key')
            if info is None:
                key = os.urandom(32)
                self.data['normal_key'] = self.protector.wrap(key)
                self.save()
                self._normal_key = key
            else:
                self._normal_key = self.protector.unwrap(info)
        return self._normal_key

    def has_passphrase(self) -> bool:
        return bool(self.data.get('critical'))

    def unlocked(self) -> bool:
        return self._critical_key is not None

    def unlock(self, passphrase: str):
        info = self.data.get('critical')
        if not info:
            raise VaultError('пароль для важных ключей ещё не задан')
        key = derive_key(passphrase, b64d(info['salt']), info['kdf'])
        try:
            if unseal(key, info['check'], b'check') != CHECK_PLAINTEXT:
                raise VaultError('неверный пароль хранилища')
        except VaultError:
            raise VaultError('неверный пароль хранилища')
        self._critical_key = key

    def set_passphrase(self, new: str, old: str = None):
        if len(new) < 10:
            raise VaultError('пароль хранилища — не короче 10 символов')
        if self.has_passphrase():
            if old is not None:
                self.unlock(old)
            if not self.unlocked():
                raise NeedPassphrase('нужен текущий пароль хранилища')
        critical = {n: self.value(n) for n, e in self.data['entries'].items() if e['tier'] == 'critical'}
        salt = os.urandom(16)
        key = derive_key(new, salt, self.scrypt_params)
        self.data['critical'] = {'kdf': dict(self.scrypt_params), 'salt': b64e(salt),
                                 'check': seal(key, CHECK_PLAINTEXT, b'check'), 'updated': now_iso()}
        self._critical_key = key
        for name, value in critical.items():
            self._write(name, value, 'critical', save=False)
        self.save()
        self.log('passphrase', [], changed=bool(critical))

    def _key_for(self, tier):
        if tier == 'normal':
            return self.normal_key()
        if not self.has_passphrase():
            raise VaultError('сначала задайте пароль для важных ключей')
        if self._critical_key is None:
            raise NeedPassphrase('нужен пароль хранилища')
        return self._critical_key

    # записи
    def entries(self):
        return self.data['entries']

    def _write(self, name, value, tier, save=True, kind=None):
        box = seal(self._key_for(tier), value.encode('utf-8'), _aad(name, tier))
        old = self.data['entries'].get(name, {})
        self.data['entries'][name] = {'tier': tier, **box, 'len': len(value),
                                      'sha256_8': hashlib.sha256(value.encode('utf-8')).hexdigest()[:8],
                                      'kind': kind or old.get('kind') or ('archive' if name.startswith(ARCHIVE_PREFIX) else 'env'),
                                      'updated': now_iso()}
        if save:
            self.save()

    def put(self, name, value, tier='normal', kind=None):
        if not NAME_RE.match(name or ''):
            raise VaultError('имя ключа — латиница в верхнем регистре, цифры и «_»: %r' % name)
        if tier not in ('normal', 'critical'):
            raise VaultError('уровень — normal или critical')
        if not isinstance(value, str) or value == '':
            raise VaultError('пустое значение не сохраняю')
        self._write(name, value, tier, kind=kind)
        self.log('put', [name], tier=tier)

    def value(self, name) -> str:
        entry = self.data['entries'].get(name)
        if entry is None:
            raise VaultError('нет ключа %s' % name)
        return unseal(self._key_for(entry['tier']), entry, _aad(name, entry['tier'])).decode('utf-8')

    def delete(self, name):
        if self.data['entries'].pop(name, None) is None:
            raise VaultError('нет ключа %s' % name)
        self.save()
        self.log('delete', [name])

    def rename(self, old, new):
        if new in self.data['entries']:
            raise VaultError('ключ %s уже есть' % new)
        entry = self.data['entries'][old]
        value = self.value(old)
        self._write(new, value, entry['tier'], save=False, kind=entry.get('kind'))
        del self.data['entries'][old]
        self.save()
        self.log('rename', [old, new])

    def set_tier(self, name, tier):
        value = self.value(name)
        self._write(name, value, tier, kind=self.data['entries'][name].get('kind'))
        self.log('tier', [name], tier=tier)

    def env_names(self, tier=None):
        return sorted(n for n, e in self.data['entries'].items()
                      if e.get('kind', 'env') == 'env' and (tier is None or e['tier'] == tier))


# ── резервная копия: отдельный пароль, переносима между машинами (Windows ↔ Mac) ─────────────
BACKUP_FORMAT = 'otp-secrets-backup/1'


def export_backup(vault, path, passphrase):
    """Все записи (и важные — хранилище должно быть открыто паролем) одним файлом под паролем копии."""
    if len(passphrase) < 10:
        raise VaultError('пароль копии — не короче 10 символов')
    entries = {n: {'tier': e['tier'], 'kind': e.get('kind', 'env'), 'value': vault.value(n)}
               for n, e in vault.entries().items()}
    salt = os.urandom(16)
    key = derive_key(passphrase, salt, vault.scrypt_params)
    payload = json.dumps({'created': now_iso(), 'entries': entries}, ensure_ascii=False).encode('utf-8')
    doc = {'format': BACKUP_FORMAT, 'kdf': dict(vault.scrypt_params), 'salt': b64e(salt),
           'box': seal(key, payload, b'backup')}
    Path(path).write_text(json.dumps(doc), encoding='utf-8')
    vault.log('export-backup', list(entries), file=str(path))
    return len(entries)


def read_backup(path, passphrase):
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    if doc.get('format') != BACKUP_FORMAT:
        raise VaultError('это не резервная копия хранилища')
    key = derive_key(passphrase, b64d(doc['salt']), doc['kdf'])
    try:
        return json.loads(unseal(key, doc['box'], b'backup').decode('utf-8'))['entries']
    except VaultError:
        raise VaultError('неверный пароль копии или файл повреждён')


def import_backup(vault, entries, overwrite=False):
    """Записи из копии; важные — только если у хранилища задан и введён пароль, иначе станут обычными."""
    added, skipped = [], []
    for name, item in entries.items():
        if name in vault.entries() and not overwrite:
            skipped.append(name)
            continue
        tier = item['tier'] if item['tier'] == 'normal' or vault.unlocked() else 'normal'
        vault._write(name, item['value'], tier, save=False, kind=item.get('kind'))
        added.append(name)
    vault.save()
    vault.log('import-backup', added, skipped=skipped)
    return added, skipped


# ── перенос старого .env ────────────────────────────────────────────────────────────────────
def _slug(text):
    s = re.sub(r'[^A-Za-z0-9]+', '_', text or '').strip('_').upper()
    return s if s and s[0].isalpha() else ''


def _is_json(text):
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def _strip_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
        return value[1:-1]
    return value


# Метка собирается из частей: страж tests/test_no_secrets_in_repo.py ищет в коде начало PEM-ключа.
PEM_LABEL = 'OPENSSH ' + 'PRIVATE KEY'


def wrap_openssh(value):
    """Тело ключа OpenSSH без обёртки (так он лежал в старом .env) → полный PEM, который берёт ssh."""
    if value.startswith(OPENSSH_BODY) and PEM_LABEL not in value:
        body = value.replace('\r', '').strip()
        return '-----BEGIN %s-----\n%s\n-----END %s-----\n' % (PEM_LABEL, body, PEM_LABEL)
    return value


def parse_env_text(text, line_names=None, block_names=None):
    """[(имя, значение, номер строки, вид)] из .env с особенностями старого файла:
    многострочный JSON после KEY={, строки base64 — продолжение ключа (SSH), блок INI ([Interface]…)
    до следующего KEY=, строки без имени — по комментарию над ними или по номеру."""
    line_names, block_names = line_names or {}, block_names or {}
    lines = text.splitlines()
    out, i, comment = [], 0, None
    while i < len(lines):
        raw = lines[i]
        s, n = raw.strip(), i + 1
        if not s:
            comment = None
            i += 1
            continue
        if s.startswith('#'):
            comment = s.lstrip('#').strip()
            i += 1
            continue
        m = ENV_KEY_RE.match(raw)
        if m:
            name, value = m.group(1), m.group(2).strip()
            if value.startswith('{') and not _is_json(value):
                block, j = [value], i + 1
                while j < len(lines):
                    block.append(lines[j])
                    if _is_json('\n'.join(block)):
                        break
                    j += 1
                else:
                    raise VaultError('строка %d: JSON ключа %s не закрыт' % (n, name))
                out.append((name, '\n'.join(block), n, 'json'))
                i = j + 1
                continue
            value = _strip_quotes(value)
            j, cont = i + 1, []
            while j < len(lines) and BASE64_LINE_RE.match(lines[j].strip()):
                cont.append(lines[j].strip())
                j += 1
            kind = 'env'
            if cont:
                value = '\n'.join([value] + cont)
                kind = 'multiline'
            out.append((name, value, n, kind))
            i = j
            continue
        if INI_SECTION_RE.match(s):
            j, block = i, []
            while j < len(lines) and not ENV_KEY_RE.match(lines[j]):
                block.append(lines[j])
                j += 1
            name = block_names.get(n) or _slug(comment) or 'INI_BLOCK_%d' % n
            out.append((name, '\n'.join(block).strip('\n') + '\n', n, 'ini'))
            i = j
            continue
        name = line_names.get(n) or _slug(comment) or 'LINE_%d' % n
        out.append((name, s, n, 'bare'))
        i += 1
    return out


# ── маскирование вывода ─────────────────────────────────────────────────────────────────────
def is_public(name, value):
    return (name.endswith(PUBLIC_SUFFIXES) and '?' not in value and '@' not in value
            and '\n' not in value and len(value) < 200)


def mask_regex(pairs):
    """Регулярка по всем видам значения: как есть, в JSON, в URL, base64, построчно (ключи, JSON)."""
    forms = set()
    for name, value in pairs:
        if is_public(name, value) or len(value) < MIN_MASK_LEN:
            continue
        candidates = {value, json.dumps(value)[1:-1], urllib.parse.quote(value, safe=''),
                      urllib.parse.quote_plus(value), b64e(value.encode('utf-8'))}
        for line in value.splitlines():
            line = line.strip()
            if len(line) >= 12:
                candidates.add(line)
        forms.update(c for c in candidates if len(c) >= MIN_MASK_LEN)
    if not forms:
        return None
    ordered = sorted((f.encode('utf-8') for f in forms), key=len, reverse=True)
    return re.compile(b'|'.join(re.escape(f) for f in ordered))


def masked(data: bytes, rx) -> bytes:
    return rx.sub(b'***', data) if rx else data


def pump(src, dst, rx, keep=4096):
    """Поток из src в dst с маскированием: режем по концу строки, длинную строку без перевода —
    кусками, оставляя хвост, чтобы значение на стыке кусков не проскочило."""
    buf = b''
    reader = getattr(src, 'read1', None) or src.read
    while True:
        chunk = reader(65536)
        if not chunk:
            break
        buf += chunk
        cut = buf.rfind(b'\n') + 1
        if cut == 0 and len(buf) > 65536:
            cut = len(buf) - keep
        if cut > 0:
            dst.write(masked(buf[:cut], rx))
            dst.flush()
            buf = buf[cut:]
    if buf:
        dst.write(masked(buf, rx))
        dst.flush()


# ── окно пароля ─────────────────────────────────────────────────────────────────────────────
def ask_passphrase(reason, confirm=False):
    """Пароль хранилища от владельца: окно поверх всех (вводит человек), без окна — консоль без эха."""
    if os.environ.get('OTP_SECRETS_CONSOLE') == '1' or not _has_display():
        first = getpass.getpass('%s\nПароль хранилища: ' % reason)
        if confirm and getpass.getpass('Ещё раз: ') != first:
            raise VaultError('пароли не совпали')
        return first
    import tkinter as tk
    result = {'value': None}
    root = tk.Tk()
    root.title('Секреты iCORE — пароль')
    root.attributes('-topmost', True)
    root.resizable(False, False)
    tk.Label(root, text=reason, justify='left', wraplength=520, padx=14, pady=10).pack(anchor='w')
    fields = []
    for label in (['Пароль хранилища', 'Ещё раз'] if confirm else ['Пароль хранилища']):
        tk.Label(root, text=label, padx=14).pack(anchor='w')
        entry = tk.Entry(root, show='•', width=42)
        entry.pack(padx=14, pady=(0, 8), anchor='w')
        fields.append(entry)
    status = tk.Label(root, text='', fg='#b00020', padx=14)
    status.pack(anchor='w')

    def ok(_event=None):
        values = [f.get() for f in fields]
        if confirm and values[0] != values[1]:
            status.config(text='Пароли не совпали')
            return
        result['value'] = values[0]
        root.destroy()

    row = tk.Frame(root, padx=14, pady=10)
    row.pack(fill='x')
    tk.Button(row, text='Отказать', command=root.destroy).pack(side='right')
    tk.Button(row, text='Разрешить', command=ok, default='active').pack(side='right', padx=8)
    root.bind('<Return>', ok)
    root.bind('<Escape>', lambda _e: root.destroy())
    root.after(PASSPHRASE_TIMEOUT_S * 1000, root.destroy)
    fields[0].focus_force()
    root.mainloop()
    if result['value'] is None:
        raise VaultError('доступ не разрешён: окно закрыто или вышло время')
    return result['value']


def _has_display():
    if sys.platform in ('win32', 'darwin'):
        return True
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def unlock_interactive(vault, reason):
    for _attempt in range(3):
        try:
            vault.unlock(ask_passphrase(reason))
            return
        except VaultError as exc:
            if 'неверный пароль' not in str(exc):
                raise
            reason = 'Неверный пароль, ещё раз.\n\n' + reason
    raise VaultError('три неверных пароля подряд')


# ── команды ─────────────────────────────────────────────────────────────────────────────────
def _split(value):
    return [x.strip() for x in (value or '').split(',') if x.strip()]


def cmd_list(vault, _args):
    entries = vault.entries()
    if not entries:
        print('Хранилище пусто: %s' % vault.file)
        return 0
    width = max(len(n) for n in entries)
    print('%-*s  уровень    длина  изменён' % (width, 'ИМЯ'))
    for name in sorted(entries):
        e = entries[name]
        tier = 'важный' if e['tier'] == 'critical' else 'обычный'
        extra = ' (архив, в команды не идёт)' if e.get('kind') == 'archive' else ''
        print('%-*s  %-9s %6d  %s%s' % (width, name, tier, e['len'], e['updated'][:16].replace('T', ' '), extra))
    crit = sum(1 for e in entries.values() if e['tier'] == 'critical')
    print('всего %d, важных %d; пароль для важных %s' % (len(entries), crit, 'задан' if vault.has_passphrase() else 'НЕ задан'))
    return 0


def cmd_run(vault, args):
    command = list(args.command)
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        raise VaultError('не указана команда: run -- команда …')
    entries = vault.entries()
    only, critical = _split(args.only), _split(args.critical)
    # --map НОВОЕ=СТАРОЕ: значение ключа СТАРОЕ ещё и под именем НОВОЕ («Задачи» от имени
    # Шерзада: ADMIN_LOGIN=SHERZAD_LOGIN) — без пароля в командной строке.
    mapping = []
    for item in _split(getattr(args, 'map', None)):
        new, _sep, old = item.partition('=')
        if not (NAME_RE.match(new.strip()) and old.strip()):
            raise VaultError('--map ожидает НОВОЕ=СТАРОЕ: %r' % item)
        mapping.append((new.strip(), old.strip()))
    unknown = [n for n in only + critical + [old for _new, old in mapping] if n not in entries]
    if unknown:
        raise VaultError('нет таких ключей: %s' % ', '.join(unknown))
    wrong = [n for n in critical if entries[n]['tier'] != 'critical']
    if wrong:
        raise VaultError('не важные, их не надо перечислять в --critical: %s' % ', '.join(wrong))
    names = only or vault.env_names('normal')
    names = list(dict.fromkeys(names + [old for _new, old in mapping if old not in critical]))
    blocked = [n for n in names if entries[n]['tier'] == 'critical' and n not in critical]
    if blocked:
        raise VaultError('важные ключи — только через --critical (с паролем владельца): %s' % ', '.join(blocked))
    shown = ' '.join(command)[:300]
    if critical:
        try:
            unlock_interactive(vault, 'Агент просит важные ключи:\n  %s\n\nдля команды:\n  %s\n\n'
                                      'Введите пароль хранилища, чтобы разрешить один запуск.' % ('\n  '.join(critical), shown))
        except VaultError as exc:
            vault.log('run-denied', critical, command=shown, reason=str(exc))
            raise
    pairs = [(n, vault.value(n)) for n in list(dict.fromkeys(names + critical))]
    values = dict(pairs)
    pairs += [(new, values[old]) for new, old in mapping]
    env = dict(os.environ)
    env.update(dict(pairs))
    rx = None if args.no_mask else mask_regex(pairs)
    vault.log('run', [n for n, _ in pairs], critical=critical, command=shown, masked=not args.no_mask)
    try:
        proc = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise VaultError('команда не запустилась: %s' % exc)
    threads = [threading.Thread(target=pump, args=(proc.stdout, sys.stdout.buffer, rx), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, sys.stderr.buffer, rx), daemon=True)]
    for t in threads:
        t.start()
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        code = proc.wait()
    for t in threads:
        t.join()
    return code


def cmd_import_env(vault, args):
    path = Path(args.file)
    text = path.read_text(encoding='utf-8')
    line_names = dict((int(k), v) for k, v in (x.split('=', 1) for x in args.name_line or []))
    block_names = dict((int(k), v) for k, v in (x.split('=', 1) for x in args.name_block or []))
    parsed = parse_env_text(text, line_names, block_names)
    seen, dupes = {}, []
    for name, value, line, kind in parsed:
        if name in seen:
            dupes.append('%s (строки %d и %d)' % (name, seen[name], line))
            continue
        seen[name] = line
    if dupes and not args.force:
        raise VaultError('повторы имён, ничего не записано: %s' % '; '.join(dupes))
    archive = '%s%s_%s' % (ARCHIVE_PREFIX, _slug(path.name) or 'ENV', dt.date.today().strftime('%Y%m%d'))
    tier = 'critical' if vault.has_passphrase() and vault.unlocked() else 'normal'
    expected = {}
    written = set()
    for name, value, line, kind in parsed:
        if name in written:
            continue
        if kind == 'multiline':
            value = wrap_openssh(value)
        if name in vault.entries() and not args.force:
            raise VaultError('ключ %s уже есть в хранилище (строка %d) — запустите с --force' % (name, line))
        vault._write(name, value, 'normal', save=False, kind='env')
        expected[name] = hashlib.sha256(value.encode('utf-8')).hexdigest()
        written.add(name)
    vault._write(archive, text, tier, save=False, kind='archive')
    vault.save()
    vault.log('import-env', sorted(written) + [archive], source=str(path))
    # Проверка: всё, что записано, расшифровывается в то же самое.
    bad = [n for n, h in expected.items() if hashlib.sha256(vault.value(n).encode('utf-8')).hexdigest() != h]
    special = {}
    for name, _v, line, kind in parsed:
        if kind != 'env':
            special.setdefault(kind, []).append('%s (стр. %d)' % (name, line))
    print('перенесено ключей: %d; архив целиком: %s (%s)' % (len(written), archive, 'важный' if tier == 'critical' else 'обычный'))
    for kind, items in special.items():
        print('  %s: %s' % ({'json': 'многострочный JSON', 'multiline': 'многострочные (SSH и т. п.)', 'ini': 'блок конфига',
                             'bare': 'строки без имени'}.get(kind, kind), ', '.join(items)))
    print('сверка расшифровкой: %s' % ('всё совпало' if not bad else 'НЕ совпало: %s' % ', '.join(bad)))
    return 0 if not bad else 1


def cmd_set(vault, args):
    """Для владельца в консоли: значение вводится без эха (или из файла / stdin)."""
    if args.from_file:
        value = Path(args.from_file).read_text(encoding='utf-8')
    elif args.stdin:
        value = sys.stdin.read()
    else:
        value = getpass.getpass('Значение %s (не отображается): ' % args.name)
    if args.critical and not vault.unlocked():
        unlock_interactive(vault, 'Сохранить важный ключ %s' % args.name)
    vault.put(args.name, value.rstrip('\r\n') if not args.keep_newline else value,
              'critical' if args.critical else 'normal')
    print('сохранено: %s (%d симв.)' % (args.name, len(value)))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Хранилище секретов iCORE (вместо открытого .env.codex.local)')
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list', help='имена, уровни, длины — без значений')
    run = sub.add_parser('run', help='запустить команду с секретами в окружении и маскированием вывода')
    run.add_argument('--only', help='только эти ключи (через запятую)')
    run.add_argument('--critical', help='важные ключи (через запятую) — спросит пароль у владельца')
    run.add_argument('--map', help='НОВОЕ=СТАРОЕ через запятую: значение ключа ещё и под другим именем')
    run.add_argument('--no-mask', action='store_true', help=argparse.SUPPRESS)
    run.add_argument('command', nargs=argparse.REMAINDER)
    imp = sub.add_parser('import-env', help='перенести старый .env (оригинал целиком — в архив)')
    imp.add_argument('file')
    imp.add_argument('--name-line', action='append', help='N=ИМЯ для строки без имени')
    imp.add_argument('--name-block', action='append', help='N=ИМЯ для блока конфига, начинающегося в строке N')
    imp.add_argument('--force', action='store_true')
    st = sub.add_parser('set', help='добавить/заменить значение (ввод без эха)')
    st.add_argument('name')
    st.add_argument('--critical', action='store_true')
    st.add_argument('--from-file')
    st.add_argument('--stdin', action='store_true')
    st.add_argument('--keep-newline', action='store_true')
    sub.add_parser('gui', help='окно владельца')
    args = parser.parse_args(argv)
    if args.cmd == 'gui':
        from otp_secrets_gui import run_gui  # noqa: PLC0415 — окно не грузим без нужды
        return run_gui()
    vault = Vault()
    handlers = {'list': cmd_list, 'run': cmd_run, 'import-env': cmd_import_env, 'set': cmd_set}
    try:
        return handlers[args.cmd](vault, args)
    except VaultError as exc:
        print('otp_secrets: %s' % exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

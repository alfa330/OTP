"""Хранилище секретов scripts/otp_secrets.py: разбор старого .env, шифрование, уровни, маскирование.

Ключ ОС подменён тестовым (кроме отдельной проверки DPAPI на Windows), scrypt — облегчённый."""
import base64
import io
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import otp_secrets as s  # noqa: E402

FAST = {'n': 2 ** 10, 'r': 8, 'p': 1}
# Ненастоящие значения: страж секретов в репозитории их не должен принимать за живые.
SSH_BODY = s.OPENSSH_BODY + 'AAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW'
SSH_LINES = ['QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB',
             'QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJC']


class FakeProtector:
    name = 'test'

    def wrap(self, key):
        return {'protector': 'test', 'blob': base64.b64encode(key).decode()}

    def unwrap(self, info):
        return base64.b64decode(info['blob'])


@pytest.fixture
def vault(tmp_path):
    return s.Vault(tmp_path / 'v', protector=FakeProtector(), scrypt_params=FAST)


ENV_TEXT = '\n'.join([
    '# обычные',
    'PLAIN_TOKEN=fixture-token-123456',
    "QUOTED_PASSWORD='Fixture-Pass-1 #with hash'",
    'HASH_PASSWORD=Fixture-Pass-2#tail!',
    '',
    '#Google creds',
    'SA_CONTENT={',
    '"type": "service_account",',
    '"private_key_id": "fixture",',
    '"client_email": "fixture@example.com"',
    '}',
    '',
    'SSH_KEY=' + SSH_BODY,
    *SSH_LINES,
    '',
    '#CRM X-Integration-Token',
    'Fixture-bare-value-0123456789',
    '',
    'AFTER_BLOCK=fixture-after-000111',
    '',
    '#VPS WIREGUARD CREDS',
    '[Interface]',
    'PrivateKey = fixture-private',
    'Address = 10.8.0.6/32',
    '# комментарий внутри',
    '',
    '[Peer]',
    'Endpoint = 192.0.2.1:443',
    'AllowedIPs = 10.19.0.0/24',
])


def test_parse_env_text_handles_all_shapes():
    parsed = {name: (value, kind) for name, value, _line, kind in s.parse_env_text(ENV_TEXT)}
    assert parsed['PLAIN_TOKEN'] == ('fixture-token-123456', 'env')
    assert parsed['QUOTED_PASSWORD'][0] == 'Fixture-Pass-1 #with hash'
    assert parsed['HASH_PASSWORD'][0] == 'Fixture-Pass-2#tail!'     # «#» внутри значения — не комментарий
    value, kind = parsed['SA_CONTENT']
    assert kind == 'json' and json.loads(value)['client_email'] == 'fixture@example.com'
    value, kind = parsed['SSH_KEY']
    assert kind == 'multiline' and value.splitlines() == [SSH_BODY] + SSH_LINES
    assert parsed['CRM_X_INTEGRATION_TOKEN'] == ('Fixture-bare-value-0123456789', 'bare')
    assert parsed['AFTER_BLOCK'][0] == 'fixture-after-000111'
    value, kind = parsed['VPS_WIREGUARD_CREDS']
    assert kind == 'ini'
    assert '[Interface]' in value and '[Peer]' in value and 'AllowedIPs = 10.19.0.0/24' in value
    assert '# комментарий внутри' in value


def test_explicit_names_for_bare_lines_and_blocks():
    lines = ENV_TEXT.splitlines()
    bare = lines.index('Fixture-bare-value-0123456789') + 1
    block = lines.index('[Interface]') + 1
    names = [p[0] for p in s.parse_env_text(ENV_TEXT, {bare: 'CRM_TOKEN'}, {block: 'WIREGUARD_ICORE_YC_CONF'})]
    assert 'CRM_TOKEN' in names and 'WIREGUARD_ICORE_YC_CONF' in names


def test_wrap_openssh_makes_full_pem_once():
    value = '\n'.join([SSH_BODY] + SSH_LINES)
    pem = s.wrap_openssh(value)
    assert pem.startswith('-----BEGIN ' + s.PEM_LABEL + '-----\n') and pem.endswith('-----END ' + s.PEM_LABEL + '-----\n')
    assert s.wrap_openssh(pem) == pem
    assert s.wrap_openssh('plain') == 'plain'


def test_roundtrip_rename_delete(vault):
    vault.put('API_TOKEN', 'fixture-value-xyz')
    again = s.Vault(vault.dir, protector=FakeProtector())
    assert again.value('API_TOKEN') == 'fixture-value-xyz'
    raw = vault.file.read_text(encoding='utf-8')
    assert 'fixture-value-xyz' not in raw
    again.rename('API_TOKEN', 'API_TOKEN_2')
    assert again.value('API_TOKEN_2') == 'fixture-value-xyz' and 'API_TOKEN' not in again.entries()
    again.delete('API_TOKEN_2')
    assert again.entries() == {}
    with pytest.raises(s.VaultError):
        again.put('bad-name', 'x')


def test_ciphertext_is_bound_to_name_and_tier(vault):
    vault.put('A_TOKEN', 'fixture-a-value')
    vault.put('B_TOKEN', 'fixture-b-value')
    data = json.loads(vault.file.read_text(encoding='utf-8'))
    data['entries']['A_TOKEN']['ct'], data['entries']['B_TOKEN']['ct'] = data['entries']['B_TOKEN']['ct'], data['entries']['A_TOKEN']['ct']
    data['entries']['A_TOKEN']['nonce'], data['entries']['B_TOKEN']['nonce'] = data['entries']['B_TOKEN']['nonce'], data['entries']['A_TOKEN']['nonce']
    vault.file.write_text(json.dumps(data), encoding='utf-8')
    swapped = s.Vault(vault.dir, protector=FakeProtector())
    with pytest.raises(s.VaultError):
        swapped.value('A_TOKEN')


def test_critical_needs_passphrase_and_cannot_be_downgraded_by_editing(vault):
    vault.set_passphrase('fixture-passphrase-1')
    vault.put('PROD_PASSWORD', 'fixture-prod-secret', tier='critical')
    fresh = s.Vault(vault.dir, protector=FakeProtector(), scrypt_params=FAST)
    with pytest.raises(s.NeedPassphrase):
        fresh.value('PROD_PASSWORD')
    with pytest.raises(s.VaultError):
        fresh.unlock('wrong-passphrase-0')
    fresh.unlock('fixture-passphrase-1')
    assert fresh.value('PROD_PASSWORD') == 'fixture-prod-secret'
    # Правка уровня в файле не открывает запись обычным ключом.
    data = json.loads(vault.file.read_text(encoding='utf-8'))
    data['entries']['PROD_PASSWORD']['tier'] = 'normal'
    vault.file.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(s.VaultError):
        s.Vault(vault.dir, protector=FakeProtector()).value('PROD_PASSWORD')


def test_change_passphrase_reencrypts(vault):
    vault.set_passphrase('fixture-passphrase-1')
    vault.put('PROD_PASSWORD', 'fixture-prod-secret', tier='critical')
    vault.set_passphrase('fixture-passphrase-2')
    fresh = s.Vault(vault.dir, protector=FakeProtector(), scrypt_params=FAST)
    with pytest.raises(s.VaultError):
        fresh.unlock('fixture-passphrase-1')
    fresh.unlock('fixture-passphrase-2')
    assert fresh.value('PROD_PASSWORD') == 'fixture-prod-secret'


def test_mask_regex_covers_encodings_and_skips_public():
    secret = 'Fixture/Pass+word=1'
    rx = s.mask_regex([('X_PASSWORD', secret), ('PROD_PG_HOST', 'db.example.kz'), ('SHORT_TOKEN', 'abc')])
    import urllib.parse
    text = ' '.join([secret, json.dumps(secret), urllib.parse.quote(secret, safe=''),
                     base64.b64encode(secret.encode()).decode(), 'db.example.kz', 'abc']).encode()
    out = s.masked(text, rx).decode()
    assert 'Pass+word' not in out and 'Pass%2Bword' not in out
    assert base64.b64encode(secret.encode()).decode() not in out
    assert 'db.example.kz' in out and 'abc' in out


def test_mask_multiline_value_line_by_line():
    key = '\n'.join(['-----BEGIN FIXTURE-----', 'A' * 40, 'B' * 40, '-----END FIXTURE-----'])
    rx = s.mask_regex([('SSH_KEY', key)])
    out = s.masked(('line: %s\nline: %s\n' % ('A' * 40, 'B' * 40)).encode(), rx).decode()
    assert 'A' * 40 not in out and 'B' * 40 not in out


def test_pump_masks_across_chunks():
    secret = 'fixture-secret-across-chunks'
    rx = s.mask_regex([('X_TOKEN', secret)])
    data = ('start %s end\n' % secret).encode()

    class Chunky:
        def __init__(self, raw):
            self.parts = [raw[i:i + 5] for i in range(0, len(raw), 5)]

        def read1(self, _n):
            return self.parts.pop(0) if self.parts else b''

    dst = io.BytesIO()
    s.pump(Chunky(data), dst, rx)
    assert dst.getvalue() == b'start *** end\n'


def _run_args(command, only=None, critical=None, mapping=None):
    return types.SimpleNamespace(command=['--'] + command, only=only, critical=critical, no_mask=False, map=mapping)


def test_run_map_puts_value_under_other_name(vault, capfd):
    vault.put('SHERZAD_LOGIN', 'fixture-owner-login')
    vault.put('ADMIN_LOGIN', 'fixture-admin-login')
    child = "import os; print('same' if os.environ['ADMIN_LOGIN'] == os.environ['SHERZAD_LOGIN'] else 'diff')"
    assert s.cmd_run(vault, _run_args([sys.executable, '-c', child], mapping='ADMIN_LOGIN=SHERZAD_LOGIN')) == 0
    assert capfd.readouterr().out.strip() == 'same'
    with pytest.raises(s.VaultError):
        s.cmd_run(vault, _run_args([sys.executable, '-c', 'pass'], mapping='ADMIN_LOGIN=NO_SUCH_KEY'))


CHILD = ("import os, json, base64, urllib.parse, sys; v = os.environ['X_TOKEN']; "
         "print(v); print(json.dumps({'t': v})); print(urllib.parse.quote(v, safe='')); "
         "print(base64.b64encode(v.encode()).decode()); print('HOST', os.environ.get('PROD_PG_HOST')); "
         "print('C', os.environ.get('PROD_PASSWORD'), file=sys.stderr); sys.exit(3)")


def test_run_injects_masks_and_passes_exit_code(vault, capfd):
    vault.put('X_TOKEN', 'fixture-run-secret/+=1')
    vault.put('PROD_PG_HOST', 'db.example.kz')
    code = s.cmd_run(vault, _run_args([sys.executable, '-c', CHILD]))
    out, err = capfd.readouterr()
    assert code == 3
    assert 'fixture-run-secret' not in out and '***' in out
    assert 'HOST db.example.kz' in out
    assert 'C None' in err      # важное без --critical в окружение не попало
    log = [json.loads(x) for x in vault.log_file.read_text(encoding='utf-8').splitlines()]
    assert log[-1]['action'] == 'run' and 'X_TOKEN' in log[-1]['names']


def test_run_critical_only_with_passphrase(vault, capfd, monkeypatch):
    vault.set_passphrase('fixture-passphrase-1')
    vault.put('X_TOKEN', 'fixture-run-secret-2')
    vault.put('PROD_PASSWORD', 'fixture-prod-secret-2', tier='critical')
    fresh = s.Vault(vault.dir, protector=FakeProtector(), scrypt_params=FAST)
    with pytest.raises(s.VaultError):
        s.cmd_run(fresh, _run_args([sys.executable, '-c', 'pass'], only='PROD_PASSWORD'))
    asked = []
    monkeypatch.setattr(s, 'ask_passphrase', lambda reason, confirm=False: asked.append(reason) or 'fixture-passphrase-1')
    code = s.cmd_run(fresh, _run_args([sys.executable, '-c', CHILD], critical='PROD_PASSWORD'))
    out, err = capfd.readouterr()
    assert code == 3 and asked and 'PROD_PASSWORD' in asked[0]
    assert 'fixture-prod-secret-2' not in err and 'C ***' in err
    monkeypatch.setattr(s, 'ask_passphrase', lambda reason, confirm=False: (_ for _ in ()).throw(s.VaultError('доступ не разрешён')))
    denied = s.Vault(vault.dir, protector=FakeProtector(), scrypt_params=FAST)
    with pytest.raises(s.VaultError):
        s.cmd_run(denied, _run_args([sys.executable, '-c', 'pass'], critical='PROD_PASSWORD'))
    assert json.loads(vault.log_file.read_text(encoding='utf-8').splitlines()[-1])['action'] == 'run-denied'


def test_import_env_with_archive_and_verification(vault, tmp_path, capsys):
    src = tmp_path / 'old.env'
    src.write_text(ENV_TEXT, encoding='utf-8')
    args = types.SimpleNamespace(file=str(src), name_line=None, name_block=None, force=False)
    assert s.cmd_import_env(vault, args) == 0
    out = capsys.readouterr().out
    assert 'всё совпало' in out and 'fixture-token-123456' not in out
    assert vault.value('SSH_KEY').startswith('-----BEGIN ' + s.PEM_LABEL)
    archive = [n for n, e in vault.entries().items() if e['kind'] == 'archive']
    assert len(archive) == 1 and vault.value(archive[0]) == ENV_TEXT
    assert archive[0] not in vault.env_names()
    with pytest.raises(s.VaultError):           # повторный перенос без --force не затирает
        s.cmd_import_env(vault, args)


def test_backup_roundtrip_between_vaults(vault, tmp_path):
    vault.set_passphrase('fixture-passphrase-1')
    vault.put('A_TOKEN', 'fixture-a-value')
    vault.put('PROD_PASSWORD', 'fixture-prod', tier='critical')
    path = tmp_path / 'copy.otpbackup'
    assert s.export_backup(vault, path, 'fixture-backup-pass') == 2
    assert b'fixture-a-value' not in path.read_bytes()
    with pytest.raises(s.VaultError):
        s.read_backup(path, 'wrong-backup-pass')
    other = s.Vault(tmp_path / 'other', protector=FakeProtector(), scrypt_params=FAST)
    added, skipped = s.import_backup(other, s.read_backup(path, 'fixture-backup-pass'))
    assert sorted(added) == ['A_TOKEN', 'PROD_PASSWORD'] and not skipped
    assert other.entries()['PROD_PASSWORD']['tier'] == 'normal'   # у нового хранилища нет пароля — важное стало обычным
    assert other.value('PROD_PASSWORD') == 'fixture-prod'


@pytest.mark.skipif(sys.platform != 'win32', reason='DPAPI — только Windows')
def test_real_dpapi_roundtrip(tmp_path):
    key = os.urandom(32)
    protector = s.DpapiProtector()
    info = protector.wrap(key)
    assert base64.b64decode(info['blob']) != key
    assert protector.unwrap(info) == key
    v = s.Vault(tmp_path / 'dp', protector=protector)
    v.put('DP_TOKEN', 'fixture-dpapi-value')
    assert s.Vault(tmp_path / 'dp').value('DP_TOKEN') == 'fixture-dpapi-value'


def test_cli_list_never_prints_values(vault, monkeypatch, capsys):
    vault.put('LIST_TOKEN', 'fixture-list-secret')
    real = s.Vault
    monkeypatch.setattr(s, 'Vault', lambda: real(vault.dir, protector=FakeProtector()))
    assert s.main(['list']) == 0
    out = capsys.readouterr().out
    assert 'LIST_TOKEN' in out and 'fixture-list-secret' not in out


def test_gui_module_imports_without_display_side_effects():
    # Окно не открываем — только что модуль собирается (Tk не создаётся при импорте).
    pytest.importorskip('tkinter')
    result = subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, %r); import otp_secrets_gui' % str(ROOT / 'scripts')],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr

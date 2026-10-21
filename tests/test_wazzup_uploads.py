"""Synthetic uploads only. Storage and vendor HTTP are local test doubles."""
import functools
import hashlib
import io
import random
import threading
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from urllib.parse import quote, urlsplit

import pytest
import requests
from flask import Flask, g, request
from PIL import Image
from werkzeug.datastructures import FileStorage

from tests.test_wazzup_pilot import CHANNEL, MemoryCursor, MemoryDatabase, user
from wazzup import pilot, uploads

PATH = '/api/wazzup/pilot'
CHAT = '70000000000'
PDF = b'%PDF-1.4\nSynthetic local fixture only\n%%EOF'


class UploadDatabase(MemoryDatabase):
    def __init__(self, actor=None):
        super().__init__(actor)
        self.uploads = {}

    @contextmanager
    def _get_cursor(self):
        with self.lock:
            self.local.transaction = True
            try:
                yield UploadCursor(self)
            finally:
                self.local.transaction = False


class UploadCursor(MemoryCursor):
    def execute(self, query, values=()):
        sql = ' '.join(query.split())
        if 'wazzup_pilot_uploads' not in sql:
            return super().execute(query, values)
        self.row = None
        self.rows = []
        self.db.statements.append((sql, values))
        if sql.startswith('INSERT INTO wazzup_pilot_uploads'):
            identifier, cid, chat, uid, client_id, name, mime, kind, size, digest, bucket, path = values
            key = (uid, client_id)
            if key not in self.db.uploads:
                self.db.uploads[key] = dict(id=identifier, channel_id=cid, chat_id=chat, user_id=uid,
                    original_name=name, content_type=mime, message_type=kind, file_size=size,
                    sha256=digest, bucket=bucket, blob_path=path,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=24), deleted_at=None)
                self.row = (identifier,)
        elif sql.startswith(uploads._SELECT.strip()):
            if 'AND id=%s' in sql:
                identifier, uid, cid, chat = values
                item = next((v for v in self.db.uploads.values() if
                    (v['id'], v['user_id'], v['channel_id'], v['chat_id']) == (identifier, uid, cid, chat)), None)
            else:
                item = self.db.uploads.get(tuple(values))
            self.row = tuple(item[k] for k in uploads._FIELDS) if item else None
        elif sql.startswith('SELECT id,bucket,blob_path'):
            self.rows = [(v['id'], v['bucket'], v['blob_path']) for v in self.db.uploads.values()
                         if v['expires_at'] < datetime.now(timezone.utc) - timedelta(minutes=5)
                         and not v['deleted_at']][:32]
        elif sql.startswith('UPDATE wazzup_pilot_uploads SET deleted_at'):
            for item in self.db.uploads.values():
                if (item['id'], item['bucket'], item['blob_path']) == values:
                    item['deleted_at'] = datetime.now(timezone.utc)
        elif sql.startswith('UPDATE wazzup_pilot_uploads SET link_hash=%s,'):
            digest, minutes, identifier = values
            for item in self.db.uploads.values():
                if item['id'] == identifier:
                    item['link_hash'] = digest
                    item['link_expires_at'] = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        elif sql.startswith('SELECT bucket,blob_path,content_type FROM wazzup_pilot_uploads'):
            item = next((v for v in self.db.uploads.values() if v.get('link_hash') == values[0]
                         and v['link_expires_at'] > datetime.now(timezone.utc) and not v['deleted_at']), None)
            self.row = (item['bucket'], item['blob_path'], item['content_type']) if item else None
        else:
            raise AssertionError('Unexpected upload SQL: ' + sql)

    def fetchall(self):
        return self.rows


class FakeStorage:
    def __init__(self, database):
        self.db, self.objects, self.uploads, self.deletes, self.downloads = database, {}, [], [], []
        self.barrier = None

    @property
    def config(self):
        return {'bucket_name': lambda: 'synthetic-test-bucket', 'client': lambda: self}

    def bucket(self, name):
        assert not getattr(self.db.local, 'transaction', False), 'Storage I/O outside transaction'
        assert name == 'synthetic-test-bucket'
        return self

    def blob(self, path):
        owner = self

        class Blob:
            cache_control = None

            def upload_from_string(self, data, **kwargs):
                assert not getattr(owner.db.local, 'transaction', False)
                assert kwargs['if_generation_match'] == 0
                assert self.cache_control == 'private, no-store'
                owner.objects[path] = data
                owner.uploads.append((path, kwargs))
                if owner.barrier:
                    owner.barrier.wait(timeout=5)

            def delete(self, **kwargs):
                assert not getattr(owner.db.local, 'transaction', False)
                owner.objects.pop(path, None)
                owner.deletes.append(path)

            def download_as_bytes(self, **kwargs):
                assert not getattr(owner.db.local, 'transaction', False)
                owner.downloads.append(path)
                if path not in owner.objects:
                    error = RuntimeError('synthetic 404 for ' + path)
                    error.code = 404
                    raise error
                return owner.objects[path]

        return Blob()


def issued_links(database):
    return [item['link_hash'] for item in database.uploads.values() if item.get('link_hash')]


def fetch_during_post(client, transport, fetched):
    """Wazzup downloads the file while it answers the send: from its own
    connection, so the fetch runs in another thread with its own contexts."""
    def post(url, **kwargs):
        uri = kwargs['json']['contentUri']
        worker = threading.Thread(target=lambda: fetched.append((uri, client.get(urlsplit(uri).path))))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        answer = Mock(status_code=201)
        answer.json.return_value = {'messageId': 'synthetic-media-id'}
        return answer
    transport.post.side_effect = post


def client_for(database, storage, transport=None):
    transport = transport or Mock()
    transport.post.return_value.status_code = 201
    transport.post.return_value.json.return_value = {'messageId': 'synthetic-media-id'}
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(pilot.build_pilot_blueprint(
        db=database, require_api_key=lambda fn: fn, guard=lambda: (42, None),
        channels=lambda account: [{'channelId': CHANNEL, 'state': 'active', 'transport': 'wapi'}],
        preflight=lambda: ('', 204), transport=transport, gcs=storage.config,
    ))
    return app.test_client(), transport


@pytest.fixture
def fixture(monkeypatch):
    monkeypatch.delenv('RENDER_EXTERNAL_URL', raising=False)
    monkeypatch.setattr(pilot.accounts, 'api_key', lambda account: 'synthetic-key')
    monkeypatch.setattr(uploads, 'schedule_cleanup', lambda *args: None)
    uploads._rate.__globals__['_usage'].clear()
    database = UploadDatabase()
    storage = FakeStorage(database)
    client, transport = client_for(database, storage)
    return client, database, storage, transport


def upload(client, *, client_id=None, data=PDF, filename='Тест.pdf', **changes):
    form = dict(account='op', channelId=CHANNEL, chatId=CHAT,
                clientUploadId=client_id or str(uuid.uuid4()))
    form.update(changes)
    form['file'] = (io.BytesIO(data), filename)
    return client.post(PATH + '/uploads', data=form)


def send_body(attachment, **changes):
    return dict(account='op', channelId=CHANNEL, chatId=CHAT, text='',
                attachmentId=attachment['id'], clientMessageId=str(uuid.uuid4())) | changes


def image(format='PNG'):
    stream = io.BytesIO()
    Image.new('RGB', (10, 10), 'white').save(stream, format=format)
    return stream.getvalue()


def office(path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr(path, '<synthetic/>')
    return stream.getvalue()


@pytest.mark.parametrize('filename,data,mime', [
    ('a.pdf', PDF, 'application/pdf'), ('a.png', image(), 'image/png'),
    ('a.jpeg', image('JPEG'), 'image/jpeg'), ('a.webp', image('WEBP'), 'image/webp'),
    ('a.docx', office('word/document.xml'), uploads._TYPES['docx'][0]),
    ('a.xlsx', office('xl/workbook.xml'), uploads._TYPES['xlsx'][0]),
    ('a.pptx', office('ppt/presentation.xml'), uploads._TYPES['pptx'][0]),
    ('a.txt', 'Тест'.encode(), 'text/plain'), ('a.csv', b'one,two\n1,2', 'text/csv'),
    ('a.mp3', b'ID3synthetic', 'audio/mpeg'), ('a.mp4', b'\0\0\0\x18ftypmp42', 'video/mp4'),
    ('a.ogg', b'OggSsynthetic', 'audio/ogg'),
])
def test_format_validation_without_executing_or_converting_bytes(filename, data, mime):
    item = uploads.prepare_file(data, filename)
    assert item['content_type'] == mime and item['file_size'] == len(data)


@pytest.mark.parametrize('filename,data,status', [
    ('a.exe', b'MZexe', 415), ('a.pdf', b'<html>private</html>', 415),
    ('a.png', PDF, 415), ('a.jpg', image(), 415), ('a.docx', office('xl/workbook.xml'), 415),
    ('a.pdf', b'', 400), ('a.txt', b'\x00binary', 415), ('a.pdf\r\nX: secret', PDF, 400),
])
def test_invalid_files_are_rejected(filename, data, status):
    with pytest.raises(uploads.UploadError) as raised:
        uploads.prepare_file(data, filename)
    assert raised.value.status == status


def test_upload_is_private_scoped_immutable_and_does_not_send(fixture):
    client, database, storage, transport = fixture
    identifier = str(uuid.uuid4())
    first = upload(client, client_id=identifier)
    assert first.status_code == 201
    attachment = first.json['attachment']
    assert set(attachment) == {'id', 'name', 'size', 'mime', 'expiresAt'}
    assert attachment['name'] == 'Тест.pdf' and attachment['size'] == len(PDF)
    assert first.headers['Cache-Control'] == 'private, no-store'
    replay = upload(client, client_id=identifier)
    assert replay.status_code == 200 and replay.json == first.json
    assert len(storage.uploads) == 1 and len(database.uploads) == 1
    assert not issued_links(database) and not database.outbox and not database.messages
    transport.post.assert_not_called()
    for changes in ({'data': b'%PDF-different'}, {'chatId': 'other'}, {'filename': 'other.pdf'}):
        rejected = upload(client, client_id=identifier, **changes)
        assert rejected.status_code == 409 and rejected.json['code'] == 'UPLOAD_CONFLICT'
    assert len(storage.uploads) == 1


def test_upload_auth_global_size_and_type_limits_precede_storage(fixture, monkeypatch):
    client, database, storage, transport = fixture
    for fields, status in [({'account': 'potok'}, 403),
                           ({'channelId': next(iter(pilot.EXCLUDED_CHANNELS))}, 403),
                           ({'client_id': 'bad'}, 400), ({'filename': 'script.html'}, 415)]:
        assert upload(client, **fields).status_code == status
    database.actor = user('someone-else')
    assert upload(client).status_code == 403
    database.actor = user()
    monkeypatch.setattr(uploads, 'MAX_BODY', 64)
    assert upload(client).status_code == 413
    monkeypatch.setattr(uploads, 'MAX_IMAGE', 4)
    with pytest.raises(uploads.UploadError) as error:
        uploads.prepare_file(image(), 'image.png')
    assert error.value.status == 413
    assert not storage.uploads
    transport.post.assert_not_called()


@pytest.mark.parametrize('size', [128 * 1024, 1024 * 1024])
def test_multipart_stream_accepts_valid_files_larger_than_parser_buffer(fixture, size):
    client, _, storage, _ = fixture
    data = b'%PDF-1.4\n' + b'a' * size + b'\n%%EOF'
    response = upload(client, data=data)
    assert response.status_code == 201, response.json
    assert next(iter(storage.objects.values())) == data


def test_multipart_large_png_is_preserved_and_streams_close_on_rejection(fixture, monkeypatch):
    client, _, storage, _ = fixture
    buffer = io.BytesIO()
    Image.frombytes('RGB', (256, 256), random.Random(0).randbytes(256 * 256 * 3)).save(buffer, format='PNG')
    data = buffer.getvalue()
    assert len(data) > 64 * 1024
    closed = []
    close = FileStorage.close
    def capture(file):
        close(file)
        closed.append(file.stream.closed)
    monkeypatch.setattr(FileStorage, 'close', capture)
    response = upload(client, data=data, filename='noise.png')
    assert response.status_code == 201, response.json
    assert next(iter(storage.objects.values())) == data
    assert closed and all(closed)
    closed.clear()
    assert upload(client, data=b'not a PDF').status_code == 415
    assert closed and all(closed)


def test_send_gives_wazzup_a_short_link_it_downloads_during_the_post(fixture):
    client, database, storage, transport = fixture
    attachment = upload(client).json['attachment']
    body = send_body(attachment)
    fetched = []
    fetch_during_post(client, transport, fetched)
    response = client.post(PATH + '/send', json=body)
    assert response.status_code == 201
    payload = transport.post.call_args.kwargs['json']
    assert 'text' not in payload and payload['crmMessageId'] == body['clientMessageId']
    # Wazzup refused every signed storage URL and took a short link without a query.
    uri = payload['contentUri']
    parts = urlsplit(uri)
    assert uri.startswith('http://localhost' + PATH + '/files/') and not parts.query and len(uri) < 200
    token, name = parts.path[len(PATH + '/files/'):].split('/')
    assert name == quote('Тест.pdf', safe='')
    [(fetched_uri, download)] = fetched
    assert fetched_uri == uri and download.status_code == 200 and download.data == PDF
    assert download.headers['Content-Type'] == 'application/pdf'
    assert download.headers['Cache-Control'] == 'private, max-age=300'
    assert 'Content-Disposition' not in download.headers
    # Only the token's hash is stored; the link reaches neither the browser nor the archive.
    item = next(iter(database.uploads.values()))
    assert item['link_hash'] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in response.text and token not in str(database.outbox) + str(database.messages)
    fallback = database.messages['synthetic-media-id']
    assert fallback['type'] == 'document' and fallback['text'] == 'Тест.pdf'
    assert 'contentUri' not in fallback
    item['expires_at'] = datetime.now(timezone.utc) - timedelta(hours=1)
    item['deleted_at'] = datetime.now(timezone.utc)
    assert client.get(parts.path).status_code == 404
    assert client.post(PATH + '/send', json=body).status_code == 200
    assert issued_links(database) == [item['link_hash']]
    transport.post.assert_called_once()
    conflict = client.post(PATH + '/send', json=body | {'attachmentId': str(uuid.uuid4())})
    assert conflict.status_code == 409 and conflict.json['code'] == 'REQUEST_CONFLICT'


def test_file_link_needs_no_session_and_dies_with_its_key(fixture):
    client, database, storage, transport = fixture
    attachment = upload(client, data=image(), filename='photo.png').json['attachment']
    fetched = []
    fetch_during_post(client, transport, fetched)
    assert client.post(PATH + '/send', json=send_body(attachment)).status_code == 201
    [(uri, _)] = fetched
    path = urlsplit(uri).path
    # Wazzup has no portal session: another app whose every guard refuses still serves the file.
    def refuse(*_args, **_kwargs):
        raise AssertionError('the file link must not ask for a session')
    def locked(view):
        return functools.wraps(view)(refuse)
    outsider = Flask(__name__)
    outsider.register_blueprint(pilot.build_pilot_blueprint(
        db=database, require_api_key=locked, guard=refuse, channels=lambda account: [],
        preflight=refuse, transport=Mock(), gcs=storage.config))

    @outsider.after_request
    def monolith_no_store(response):
        # bot_schedule2.after_request: no-store on /api/ unless the route opts out.
        if request.path.startswith('/api/') and not getattr(g, 'allow_public_cache', False):
            response.headers['Cache-Control'] = 'no-store'
        return response

    stranger = outsider.test_client()
    served = stranger.get(path)
    assert served.status_code == 200 and served.data == image()
    assert served.headers['Content-Type'] == 'image/png'
    # The refused storage URLs answered no-store, the accepted link a cacheable file.
    assert served.headers['Cache-Control'] == 'private, max-age=300'
    assert stranger.head(path).status_code == 200
    # The name is only for Wazzup; the token alone opens the file.
    assert stranger.get(path.rsplit('/', 1)[0] + '/other-name.png?filename=x').status_code == 200
    token = path.split('/')[-2]
    for broken in (path.replace(token, token[:-1] + ('A' if token[-1] != 'A' else 'B')),
                   path.replace(token, token[:20]), PATH + '/files/' + 'x' * 43 + '/photo.png'):
        assert stranger.get(broken).status_code == 404
    item = next(iter(database.uploads.values()))
    item['link_expires_at'] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert stranger.get(path).status_code == 404
    item['link_expires_at'] = datetime.now(timezone.utc) + timedelta(minutes=1)
    storage.objects.clear()
    assert stranger.get(path).status_code == 404


def test_file_link_reports_storage_failure_without_the_key(fixture, monkeypatch, caplog):
    client, database, storage, transport = fixture
    attachment = upload(client).json['attachment']
    fetched = []
    fetch_during_post(client, transport, fetched)
    monkeypatch.setattr(storage, 'bucket', lambda _: (_ for _ in ()).throw(RuntimeError('synthetic outage')))
    caplog.set_level('INFO')
    assert client.post(PATH + '/send', json=send_body(attachment)).status_code == 201
    [(uri, download)] = fetched
    assert download.status_code == 503
    token = urlsplit(uri).path.split('/')[-2]
    assert 'file fetch failed status=503' in caplog.text and token not in caplog.text


@pytest.mark.parametrize('filename,expected', [
    ('photo.png', 'photo.png'),
    ('Договор аренды.pdf', quote('Договор аренды.pdf', safe='')),
    ('a/b?c#d.pdf', quote('a/b?c#d.pdf', safe='')),
    ('.pdf', 'file.pdf'),
])
def test_link_name_keeps_the_original_name_in_one_segment(filename, expected):
    assert uploads.link_name(filename) == expected


def test_link_name_cuts_a_long_name_and_keeps_the_extension():
    name = uploads.link_name('Очень длинное имя документа ' * 10 + '.xlsx')
    assert len(name) <= uploads.LINK_NAME_LIMIT and name.endswith('.xlsx') and '/' not in name
    assert uploads.link_name('a' * 300 + '.pdf') == 'a' * 96 + '.pdf'


def test_link_origin_prefers_render_public_address(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setenv('RENDER_EXTERNAL_URL', 'https://otp.example.onrender.com/')
    with app.test_request_context('/', base_url='http://internal:10000'):
        assert uploads.link_origin() == 'https://otp.example.onrender.com'
    monkeypatch.delenv('RENDER_EXTERNAL_URL')
    # Behind a TLS proxy a plain-http link would answer with a redirect, which Wazzup does not follow.
    with app.test_request_context('/', base_url='http://otp.example', headers={'X-Forwarded-Proto': 'https'}):
        assert uploads.link_origin() == 'https://otp.example'
    with app.test_request_context('/', base_url='http://localhost:5000'):
        assert uploads.link_origin() == 'http://localhost:5000'


def test_unknown_file_send_is_never_retried_after_expiry(fixture):
    client, database, storage, transport = fixture
    body = send_body(upload(client).json['attachment'])
    transport.post.side_effect = requests.Timeout('synthetic timeout')
    assert client.post(PATH + '/send', json=body).json['state'] == 'unknown'
    next(iter(database.uploads.values()))['expires_at'] = datetime.now(timezone.utc) - timedelta(hours=1)
    assert client.post(PATH + '/send', json=body).json['state'] == 'unknown'
    transport.post.assert_called_once()
    assert len(issued_links(database)) == 1


def test_attachment_send_rejects_wrong_chat_author_text_and_expiry_without_vendor(fixture):
    client, database, storage, transport = fixture
    body = send_body(upload(client).json['attachment'])
    assert client.post(PATH + '/send', json=body | {'text': 'caption'}).status_code == 400
    assert client.post(PATH + '/send', json=body | {'chatId': 'other'}).status_code == 404
    item = next(iter(database.uploads.values()))
    item['user_id'] = 99
    assert client.post(PATH + '/send', json=body).status_code == 404
    item['user_id'] = 42
    item['expires_at'] = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = client.post(PATH + '/send', json=body)
    assert expired.status_code == 410 and expired.json['state'] == 'failed'
    assert expired.json['code'] == 'ATTACHMENT_EXPIRED'
    assert not database.outbox and not issued_links(database)
    transport.post.assert_not_called()


def test_parallel_same_upload_keeps_one_row_and_deletes_only_loser_blob(fixture):
    _, database, storage, _ = fixture
    clients = [client_for(database, storage)[0] for _ in range(2)]
    storage.barrier = threading.Barrier(2)
    identifier = str(uuid.uuid4())
    responses = []
    workers = [threading.Thread(target=lambda client=client:
        responses.append(upload(client, client_id=identifier))) for client in clients]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=8)
        assert not worker.is_alive()
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert responses[0].json == responses[1].json
    assert len(database.uploads) == 1 and len(storage.objects) == 1
    assert len(storage.deletes) == 1
    assert next(iter(database.uploads.values()))['blob_path'] in storage.objects


def test_bounded_cleanup_only_removes_expired_pilot_prefix(fixture):
    client, database, storage, _ = fixture
    for name in ['expired.pdf', 'active.pdf', 'outside.pdf']:
        assert upload(client, filename=name).status_code == 201
    expired, active, outside = database.uploads.values()
    expired['expires_at'] = outside['expires_at'] = datetime.now(timezone.utc) - timedelta(minutes=6)
    outside['blob_path'] = 'wiki/private.pdf'
    uploads.cleanup_expired(database, storage.config)
    assert storage.deletes == [expired['blob_path']]
    assert expired['deleted_at'] and not active['deleted_at'] and not outside['deleted_at']


def test_storage_failure_is_generic_and_releases_capacity(fixture, monkeypatch):
    client, database, storage, transport = fixture
    monkeypatch.setattr(storage, 'bucket', lambda _: (_ for _ in ()).throw(RuntimeError('secret signed URL')))
    response = upload(client)
    assert response.status_code == 503 and 'secret' not in response.text
    assert uploads._slots.acquire(blocking=False)
    uploads._slots.release()
    assert not database.outbox
    transport.post.assert_not_called()


def test_cleanup_jobs_do_not_overlap_and_release_slot(monkeypatch):
    ready, release = threading.Event(), threading.Event()
    calls = []
    def cleanup(*args):
        calls.append(args)
        ready.set()
        release.wait(timeout=3)
    monkeypatch.setattr(uploads, '_cleanup_after', 0)
    monkeypatch.setattr(uploads, '_cleanup_running', threading.Lock())
    monkeypatch.setattr(uploads, 'cleanup_expired', cleanup)
    uploads.schedule_cleanup('db', 'gcs')
    assert ready.wait(timeout=2)
    monkeypatch.setattr(uploads, '_cleanup_after', 0)
    uploads.schedule_cleanup('db', 'gcs')
    assert len(calls) == 1
    release.set()
    assert uploads._cleanup_running.acquire(timeout=2)
    uploads._cleanup_running.release()

import base64
import io
from contextlib import contextmanager

import pytest
from flask import Blueprint, Flask, jsonify
from PIL import Image

from wazzup import attachments as module


def raster():
    buffer = io.BytesIO()
    Image.new('RGB', (50, 40), 'white').save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def body(**updates):
    return dict(account='op', channelId='channel', chatId='chat', messageId='file', **updates)


class Database:
    def __init__(self, url='https://store.wazzup24.com/file.pdf'):
        self.url, self.queries = url, []

    @contextmanager
    def _get_cursor(self):
        yield self

    def execute(self, sql, params):
        assert "account='op'" in sql and 'NOT is_deleted' in sql
        assert "type IN ('document','image')" in sql
        self.queries.append(params)
        self.row = (self.url,) if params == ('channel', 'chat', 'file') and self.url else None

    def fetchone(self):
        return self.row


def fixture(*, denied=False, db=None, fetch=None, recognize=None, ocr_cache=None):
    module._usage.clear()
    module._ocr_cache.clear()
    database = db or Database()
    app, bp = Flask(__name__), Blueprint('attachments', __name__)
    def actor():
        return (None, (jsonify(error='denied'), 403)) if denied else ([42], None)
    module.register_attachment_routes(bp, actor, lambda fn: fn, lambda: ('', 204), database,
        excluded_channels={'global'}, fetch=fetch or (lambda url: (b'%PDF-example', 'application/pdf')),
        recognize=recognize or (lambda blob: 'Сумма 2500'), ocr_cache=ocr_cache)
    app.register_blueprint(bp)
    return app.test_client(), database


def test_preview_checks_actor_scope_and_stored_attachment_before_fetch():
    calls = []
    fetch = lambda url: calls.append(url) or (b'%PDF-example', 'application/pdf')
    client, db = fixture(denied=True, fetch=fetch)
    assert client.get('/attachment', query_string=body()).status_code == 403
    assert not db.queries and not calls
    client, db = fixture(fetch=fetch)
    for params, status in [(body() | {'account':'potok'},403), (body() | {'channelId':'global'},403),
            (body() | {'chatId':'other'},404), (body() | {'messageId':''},400),
            (body() | {'messageId':'missing'},404)]:
        assert client.get('/attachment', query_string=params).status_code == status
    assert not calls
    response = client.get('/attachment', query_string=body() | {'url':'http://localhost/private'})
    assert response.status_code == 200 and response.data == b'%PDF-example'
    assert calls == ['https://store.wazzup24.com/file.pdf']
    assert response.mimetype == 'application/pdf'
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.parametrize('url', ['http://store.wazzup24.com/f', 'https://store.wazzup24.com.evil.test/f',
    'https://store.wazzup24.com@127.0.0.1/f', 'https://user:pass@store.wazzup24.com/f',
    'https://store.wazzup24.com:22/f', 'file:///file', 'https://127.0.0.1/f', None])
def test_only_exact_https_media_host_is_accepted(url):
    assert not module._allowed_url(url)


def test_deleted_missing_or_unsupported_stored_media_never_fetches():
    calls=[]
    for url,status in [(None,404),('https://other.example/file.pdf',415)]:
        client,_=fixture(db=Database(url), fetch=lambda value: calls.append(value))
        assert client.get('/attachment',query_string=body()).status_code==status
    assert not calls


class Response:
    def __init__(self, content=b'%PDF-file', *, status=200, headers=None, chunks=None):
        self.content, self.status_code = content, status
        self.headers, self.chunks = headers or {}, chunks
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def iter_content(self, size): return iter(self.chunks if self.chunks is not None else [self.content])


def downloader(monkeypatch, responses):
    calls=[]
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,url,**kwargs):
            calls.append(url)
            assert self.trust_env is False and kwargs['allow_redirects'] is False and kwargs['stream'] is True
            return responses.pop(0)
    monkeypatch.setattr(module.requests,'Session',Session)
    return calls


def test_download_follows_only_validated_redirects(monkeypatch):
    calls=downloader(monkeypatch,[Response(status=302,headers={'Location':'/second'}),Response()])
    assert module.download_attachment('https://store.wazzup24.com/first')==(b'%PDF-file','application/pdf')
    assert calls==['https://store.wazzup24.com/first','https://store.wazzup24.com/second']
    calls=downloader(monkeypatch,[Response(status=302,headers={'Location':'http://127.0.0.1/secrets'})])
    with pytest.raises(module.AttachmentError):
        module.download_attachment('https://store.wazzup24.com/first')
    assert len(calls)==1


@pytest.mark.parametrize('response,status', [
    (Response(headers={'Content-Length':str(module.MAX_FILE+1)}),413),
    (Response(chunks=[b'%PDF-',b'x'*30]),413),
    (Response(b'<html>proxy error</html>'),415),
    (Response(headers={'Content-Encoding':'gzip'}),502),
    (Response(status=404),502),
])
def test_download_enforces_actual_bytes_status_and_sniffed_type(monkeypatch,response,status):
    monkeypatch.setattr(module,'MAX_FILE',24)
    downloader(monkeypatch,[response])
    with pytest.raises(module.AttachmentError) as error:
        module.download_attachment('https://store.wazzup24.com/file')
    assert error.value.status==status


def test_download_and_ocr_release_capacity_on_failure_without_leaking_details():
    def failed(*args): raise RuntimeError('private signed URL / secret document')
    client,_=fixture(fetch=failed,recognize=failed)
    assert client.get('/attachment',query_string=body()).status_code==502
    response=client.post('/attachment-text',json=body(imageDataUrl=raster()))
    assert response.status_code==503 and 'secret' not in response.text
    for semaphore,slots in ((module._downloads,module.DOWNLOAD_SLOTS),(module._ocr_slots,module.OCR_SLOTS)):
        assert all(semaphore.acquire(blocking=False) for _ in range(slots)), 'a failure must return its slot'
        for _ in range(slots): semaphore.release()


def test_ocr_verifies_scope_raster_and_page_before_provider():
    calls=[]
    client,_=fixture(recognize=lambda blob:calls.append(blob) or 'Текст 2500')
    for data, status in [({},403),(body(imageDataUrl=raster())|{'chatId':'other'},404),
            (body(imageDataUrl='https://other.example/page'),400),
            (body(imageDataUrl='data:image/png;base64,aW52YWxpZA=='),400),
            (body(imageDataUrl=raster(),page=True),400),
            (body(imageDataUrl=raster(),page=0),400)]:
        assert client.post('/attachment-text',json=data).status_code==status
    assert not calls
    response=client.post('/attachment-text',json=body(imageDataUrl=raster(),page=2))
    assert response.status_code==200 and response.json=={'text':'Текст 2500','source':'ocr','page':2}
    assert calls[0].startswith(b'\xff\xd8\xff')


def test_ocr_needs_authorization_and_rejects_oversized_body_before_parsing():
    client,db=fixture(denied=True)
    assert client.post('/attachment-text',json=body(imageDataUrl=raster())).status_code==403
    assert not db.queries
    client,db=fixture()
    response=client.post('/attachment-text',data='x'*(module.MAX_BODY+1),content_type='application/json')
    assert response.status_code==413 and not db.queries


def test_ocr_capacity_and_frequency_are_bounded():
    client,_=fixture()
    for _ in range(module.OCR_SLOTS): module._ocr_slots.acquire()
    try:
        assert client.post('/attachment-text',json=body(imageDataUrl=raster())).status_code==429
    finally:
        for _ in range(module.OCR_SLOTS): module._ocr_slots.release()
    module._usage.clear()
    # Different pages: a repeated page is answered from the cache and is not a new recognition.
    for page in range(1, 9):
        assert client.post('/attachment-text',json=body(imageDataUrl=raster(),page=page)).status_code==200
    assert client.post('/attachment-text',json=body(imageDataUrl=raster(),page=9)).status_code==429


def test_download_capacity_is_bounded():
    client,_=fixture()
    for _ in range(module.DOWNLOAD_SLOTS): module._downloads.acquire()
    try:
        assert client.get('/attachment',query_string=body()).status_code==429
    finally:
        for _ in range(module.DOWNLOAD_SLOTS): module._downloads.release()
    assert client.get('/attachment',query_string=body()).status_code==200


def test_recognized_page_is_served_again_without_the_provider_or_the_limits():
    calls=[]
    client,_=fixture(recognize=lambda blob:calls.append(blob) or f'Попытка {len(calls)}')
    first=client.post('/attachment-text',json=body(imageDataUrl=raster()))
    assert first.json=={'text':'Попытка 1','source':'ocr','page':1}
    # More repeats than the per-minute limit and with every slot taken: the stored
    # page needs neither. The picture is not even decoded for a stored page.
    for _ in range(module.OCR_SLOTS): module._ocr_slots.acquire()
    try:
        for _ in range(20):
            again=client.post('/attachment-text',json=body(imageDataUrl='not a picture'))
            assert again.status_code==200
            assert again.json=={'text':'Попытка 1','source':'ocr','page':1,'cached':True}
            assert again.headers['Cache-Control']=='private, no-store'
    finally:
        for _ in range(module.OCR_SLOTS): module._ocr_slots.release()
    assert len(calls)==1


def test_stored_page_is_scoped_to_its_attachment_and_page():
    calls=[]
    client,_=fixture(recognize=lambda blob:calls.append(1) or f'Текст {len(calls)}')
    assert client.post('/attachment-text',json=body(imageDataUrl=raster())).json['text']=='Текст 1'
    assert client.post('/attachment-text',json=body(imageDataUrl=raster(),page=2)).json['text']=='Текст 2'
    assert client.post('/attachment-text',json=body(imageDataUrl=raster())).json['text']=='Текст 1'
    # Another message, or a message of another chat, never receives this text —
    # and the access checks run before the lookup.
    for data,status in [(body(imageDataUrl=raster())|{'messageId':'missing'},404),
            (body(imageDataUrl=raster())|{'chatId':'other'},404),
            (body(imageDataUrl=raster())|{'channelId':'global'},403),
            (body(imageDataUrl=raster())|{'account':'potok'},403)]:
        response=client.post('/attachment-text',json=data)
        assert response.status_code==status and 'Текст' not in response.text
    client,_=fixture(denied=True)
    assert client.post('/attachment-text',json=body(imageDataUrl=raster())).status_code==403


def test_refresh_recognizes_again_and_replaces_the_stored_text():
    calls=[]
    client,_=fixture(recognize=lambda blob:calls.append(1) or f'Попытка {len(calls)}')
    client.post('/attachment-text',json=body(imageDataUrl=raster()))
    redo=client.post('/attachment-text',json=body(imageDataUrl=raster(),refresh=True))
    assert redo.json=={'text':'Попытка 2','source':'ocr','page':1}
    assert client.post('/attachment-text',json=body(imageDataUrl=raster())).json['text']=='Попытка 2'
    # Only a literal true forces a new attempt.
    for value in ('true', 1, 'yes'):
        assert client.post('/attachment-text',json=body(imageDataUrl=raster(),refresh=value)).json.get('cached') is True
    assert len(calls)==2


def test_replaced_attachment_does_not_reuse_the_previous_document_text():
    calls=[]
    client,db=fixture(recognize=lambda blob:calls.append(1) or f'Текст {len(calls)}')
    assert client.post('/attachment-text',json=body(imageDataUrl=raster())).json['text']=='Текст 1'
    db.url='https://store.wazzup24.com/replacement.pdf'
    replaced=client.post('/attachment-text',json=body(imageDataUrl=raster()))
    assert replaced.json=={'text':'Текст 2','source':'ocr','page':1}
    assert client.post('/attachment-text',json=body(imageDataUrl=raster())).json['cached'] is True
    assert len(calls)==2


def test_failed_recognition_is_not_stored_and_an_empty_page_is():
    answers=[RuntimeError('provider down'), '']
    def recognize(blob):
        value=answers.pop(0)
        if isinstance(value,Exception): raise value
        return value
    client,_=fixture(recognize=recognize)
    assert client.post('/attachment-text',json=body(imageDataUrl=raster())).status_code==503
    empty=client.post('/attachment-text',json=body(imageDataUrl=raster()))
    assert empty.json=={'text':'','source':'ocr','page':1}
    again=client.post('/attachment-text',json=body(imageDataUrl=raster()))
    assert again.json=={'text':'','source':'ocr','page':1,'cached':True} and not answers


def test_recognized_text_cache_expires_and_stays_within_budget():
    now=[0.0]
    cache=module.RecognizedTextCache(ttl=100,max_items=3,max_chars=10,clock=lambda:now[0])
    cache.put('a','1234');cache.put('b','5678')
    assert cache.get('a')=='1234'                 # touched: the least recently used is now b
    cache.put('c','90')
    cache.put('d','xy')                           # 12 chars > 10: the oldest goes
    assert cache.get('b') is None and cache.get('a')=='1234' and len(cache)==3
    cache.put('huge','x'*11)                      # larger than the whole budget: not stored, evicts nothing
    assert cache.get('huge') is None and len(cache)==3
    now[0]=50.0
    cache.put('a','ab')                           # replacing does not double the size
    assert cache._chars==6
    now[0]=100.0
    assert cache.get('c')=='90'                   # reading does not extend the life of a page
    now[0]=100.1
    assert cache.get('c') is None and cache.get('a')=='ab'
    now[0]=150.1
    assert cache.get('a') is None and len(cache)==1 and cache.get('d') is None and len(cache)==0
    assert cache._chars==0
    disabled=module.RecognizedTextCache(ttl=0)
    disabled.put('a','text')
    assert disabled.get('a') is None


def test_stored_text_never_reaches_the_database_or_the_logs(caplog):
    client,db=fixture(recognize=lambda blob:'Паспорт 123456789')
    with caplog.at_level('DEBUG'):
        client.post('/attachment-text',json=body(imageDataUrl=raster()))
        client.post('/attachment-text',json=body(imageDataUrl=raster()))
    assert 'Паспорт' not in caplog.text
    assert all('Паспорт' not in str(query) for query in db.queries)


def test_raster_rejects_decompression_size_before_loading():
    buffer=io.BytesIO()
    Image.new('1',(4000,4000)).save(buffer,format='PNG')
    with pytest.raises(module.AttachmentError) as error:
        module._raster('data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii'))
    assert error.value.status==413


def test_ocr_incomplete_response_is_rejected_and_document_not_returned(monkeypatch):
    from wiki.ai import providers
    monkeypatch.setattr(providers,'_call_vertex_file',lambda *args,**kwargs:{'text':'partial-secret','finish':'MAX_TOKENS'})
    with pytest.raises(module.AttachmentError) as error:
        module.recognize_page(b'fake')
    assert error.value.status==422


def test_ocr_keeps_literal_document_content(monkeypatch):
    from wiki.ai import providers
    text = 'User Safety: safe\n<think>Текст документа 2500</think>'
    monkeypatch.setattr(providers,'_call_vertex_file',lambda *args,**kwargs:{'text':text,'finish':'STOP'})
    assert module.recognize_page(b'fake') == text


def test_redirects_share_elapsed_budget(monkeypatch):
    times=iter([0,0,26])
    monkeypatch.setattr(module.time,'monotonic',lambda:next(times))
    calls=downloader(monkeypatch,[Response(status=302,headers={'Location':'/second'})])
    with pytest.raises(module.AttachmentError) as error:
        module.download_attachment('https://store.wazzup24.com/first')
    assert error.value.status==504 and len(calls)==1

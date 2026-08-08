"""Эндпоинты импорта документов и загрузки файлов.

Сессий импорта нет намеренно. В оригинале документ складывался в
document_import_sessions, правился во внешнем ONLYOFFICE и только потом
становился статьёй; ONLYOFFICE у нас нет (в проде вики он тоже выключен —
переменные не заданы), а джобы очистки сессий не существовало вовсе: на момент
дампа семь из восьми висели в статусе active с конца июля.

Здесь файл разбирается в HTML и сразу отдаётся в редактор. Сохраняет человек.
"""

from flask import jsonify, request

from . import articles as wiki_articles
from . import importer as wiki_importer
from . import queries


def register(bp, wiki_route, db, log_ip, gcs):

    def _store_file(cursor, *, data, filename, content_type, uploaded_by,
                    article_id=None):
        """Кладёт файл в GCS и заводит запись, возвращая постоянный адрес.

        Постоянный, а не подписанный: подпись живёт часы, а статья — годами.
        Именно поэтому картинки внутри уроков LMS со временем перестают
        открываться.
        """
        bucket = gcs['bucket_name']() if gcs.get('bucket_name') else None
        if not bucket:
            return None

        blob_path = wiki_importer.blob_path_for(filename, content_type)
        client = gcs['client']()
        client.bucket(bucket).blob(blob_path).upload_from_string(
            data, content_type=content_type or 'application/octet-stream')

        file_id = wiki_articles.register_file(
            cursor, article_id=article_id, bucket=bucket, blob_path=blob_path,
            original_name=str(filename or 'file')[:255],
            content_type=content_type, file_size=len(data),
            width=None, height=None, uploaded_by=uploaded_by,
        )
        return '/api/wiki/file/%s' % file_id

    # ── Импорт документа ─────────────────────────────────────────────────
    @wiki_route('/import', methods=('POST',), capability='can_create')
    def wiki_import(cursor, ctx):
        uploaded = request.files.get('file')
        if not uploaded or not uploaded.filename:
            return jsonify({"error": "Файл не выбран"}), 400

        data = uploaded.read()
        try:
            result = wiki_importer.convert(
                uploaded.filename, data,
                store_image=lambda blob, content_type: _store_file(
                    cursor, data=blob, filename='image', content_type=content_type,
                    uploaded_by=ctx['user_id']),
            )
        except wiki_importer.ImportError_ as exc:
            return jsonify({"error": str(exc), "code": "WIKI_IMPORT_FAILED"}), 400

        queries.log_action(cursor, actor_id=ctx['user_id'], action='article.import',
                           entity_type='article', entity_id=None,
                           details={'file': uploaded.filename, 'kind': result['kind'],
                                    'images': len(result['images'])},
                           ip_address=log_ip())
        return jsonify(result)

    # ── Загрузка картинки из редактора ───────────────────────────────────
    @wiki_route('/upload', methods=('POST',), capability='can_create')
    def wiki_upload(cursor, ctx):
        uploaded = request.files.get('file')
        if not uploaded or not uploaded.filename:
            return jsonify({"error": "Файл не выбран"}), 400

        data = uploaded.read()
        if len(data) > wiki_importer.MAX_FILE_BYTES:
            return jsonify({
                "error": "Файл больше %d МБ" % (wiki_importer.MAX_FILE_BYTES // (1024 * 1024)),
            }), 400

        content_type = (uploaded.mimetype or '').strip()
        if not content_type.startswith('image/'):
            return jsonify({"error": "Можно загружать только изображения"}), 400

        url = _store_file(cursor, data=data, filename=uploaded.filename,
                          content_type=content_type, uploaded_by=ctx['user_id'],
                          article_id=None)
        if not url:
            return jsonify({"error": "Хранилище файлов не настроено"}), 503
        return jsonify({"url": url}), 201

# -*- coding: utf-8 -*-
"""Загрузка роликов вики: /api/wiki/videos…

Путь файла и проверки — те же, что у материалов «Продакшна»
(task_production/routes.py: start_upload, upload_state, complete_upload):
одни и те же функции хранилища и та же сверка размера, CRC32C и подписи
формата на завершении. И та же очередь в браузере
(src/components/production/productionUpload.js) — поэтому ответы здесь
повторяют её поля (material_id, session_url, offset, finished).

Отличия вики:
  * право — то же, что у картинки в редакторе (can_create): ролик вставляют в
    статью тем же жестом;
  * файл — строка wiki_files, а не материал задачи. Пока статья не сохранена,
    ролик видит только загрузивший (то же правило, что у картинки), дальше —
    все, кому видна статья (edit.link_content_files привязывает файл по адресу
    в тексте);
  * обложка — обычная картинка вики (WebP через wiki/storage.py): её подпишет
    витрина и привяжет к статье тот же код, что и любую картинку.

Курсор держит wiki_route на весь запрос, и обращения к хранилищу идут под ним
— так же, как у загрузки картинки (/upload). На ролик их единицы: открыть
сессию, спросить точку докачки после обрыва, проверить объект на завершении.
"""

import logging
import uuid

from flask import jsonify, request

from task_production import materials as production_materials
from task_production import storage as production_storage

from . import storage as wiki_storage
from . import videos as wiki_videos


def register(bp, wiki_route, db, log_ip, gcs, allowed_origin=None):

    def _error(message, status, code):
        return jsonify({'error': message, 'code': code}), status

    def _unavailable(exc):
        logging.warning('Вики: хранилище роликов не ответило: %s', exc)
        return _error('Хранилище файлов сейчас недоступно — попробуйте ещё раз через минуту',
                      503, 'WIKI_VIDEO_STORAGE')

    def _body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def _own(cursor, ctx, file_id, *, lock=False):
        """Своя загрузка ролика. Чужая и несуществующая отвечают одинаково —
        id не должен подсказывать, что такой файл есть."""
        row = wiki_videos.upload_row(cursor, file_id, lock=lock)
        if (row is None or row.get('uploaded_by') != ctx['user_id']
                or not str(row.get('content_type') or '').startswith('video/')):
            return None
        return row

    def _not_found():
        return _error('Загрузка не найдена', 404, 'WIKI_VIDEO_NOT_FOUND')

    def _drop_objects(client, rows):
        """Объекты брошенной или отклонённой загрузки — без ошибки, если их нет."""
        by_bucket = {}
        for row in rows:
            if row and row.get('bucket') and row.get('blob_path'):
                by_bucket.setdefault(row['bucket'], []).append(row['blob_path'])
        for bucket, paths in by_bucket.items():
            production_storage.delete_objects(client, bucket, paths)

    def _sweep_stale(cursor):
        """Брошенные загрузки старше недели: строки, обложки и то, что успело лечь
        в бакет. Своим савепоинтом: уборка не должна ронять загрузку нового ролика."""
        cursor.execute('SAVEPOINT wiki_video_sweep')
        try:
            stale = wiki_videos.stale_uploads(cursor)
            posters = [wiki_videos.poster_row(cursor, row['poster_file_id'])
                       for row in stale if row.get('poster_file_id')]
            # Обложку, уже привязанную к статье, не трогаем: её кто-то вставил.
            posters = [row for row in posters if row and not row.get('article_id')]
            wiki_videos.delete_rows(cursor, [row['id'] for row in stale]
                                    + [row['id'] for row in posters])
            cursor.execute('RELEASE SAVEPOINT wiki_video_sweep')
        except Exception:  # noqa: BLE001
            cursor.execute('ROLLBACK TO SAVEPOINT wiki_video_sweep')
            logging.warning('Вики: уборка брошенных загрузок роликов не прошла', exc_info=True)
            return
        if not stale:
            return
        try:
            _drop_objects(production_storage.client_of(gcs), stale + posters)
        except Exception:  # noqa: BLE001 — объекты без строк безвредны, уберутся вручную
            logging.warning('Вики: объекты брошенных роликов не удалены', exc_info=True)

    # ── Начать или продолжить ────────────────────────────────────────────
    @wiki_route('/videos', methods=('POST',), capability='can_create')
    def wiki_video_start(cursor, ctx):
        data = _body()
        try:
            name, ext, content_type = wiki_videos.classify(data.get('name'), data.get('size'))
        except ValueError as exc:
            code = str(exc)
            return _error(wiki_videos.error_message(code),
                          413 if code == wiki_videos.TOO_LARGE else 400, code)
        size = int(data.get('size'))
        fingerprint = str(data.get('fingerprint') or '').strip()[:64] or None

        # Origin страницы, которая просит загрузку: с ним хранилище добавит CORS
        # к ответам сессии (task_production/storage.py). Чужой сайт сессию не
        # получит — тот же список, что у CORS портала.
        origin = (request.headers.get('Origin') or '').strip().rstrip('/') or None
        if origin and not (callable(allowed_origin) and allowed_origin(origin)):
            return _error('Загрузка с этого сайта не разрешена', 403, 'WIKI_VIDEO_ORIGIN')

        try:
            client = production_storage.client_of(gcs)
            bucket = production_storage.bucket_of(gcs)
        except production_storage.StorageUnavailable as exc:
            return _unavailable(exc)

        # С блокировкой: см. pending_for — отмена той же загрузки, идущая в эту
        # секунду, должна договорить раньше, чем мы решим её продолжать.
        pending = wiki_videos.pending_for(cursor, user_id=ctx['user_id'], name=name,
                                          size=size, fingerprint=fingerprint, lock=True)
        if pending is not None and pending.get('upload_session'):
            # Тот же файл уже начинали грузить: продолжаем с того места, где встали.
            try:
                state = production_storage.session_offset(
                    pending['upload_session'], pending['file_size'],
                    origin=pending.get('upload_origin'))
            except production_storage.StorageUnavailable as exc:
                return _unavailable(exc)
            if state is not None:
                offset, finished = state
                return jsonify({
                    'material_id': pending['id'], 'session_url': pending['upload_session'],
                    'offset': offset, 'finished': finished, 'size': pending['file_size'],
                    'chunk_bytes': wiki_videos.CHUNK_BYTES, 'resumed': True,
                })
            # Сессия истекла — заготовку убираем и открываем новую.
            wiki_videos.delete_rows(cursor, [pending['id']])

        _sweep_stale(cursor)

        file_id = uuid.uuid4()
        path = wiki_videos.blob_path(file_id, ext)
        try:
            session_url = production_storage.open_session(
                client, bucket, path, content_type=content_type, size=size, origin=origin)
        except production_storage.StorageUnavailable as exc:
            return _unavailable(exc)
        wiki_videos.insert_upload(
            cursor, file_id=file_id, bucket=bucket, blob_path=path, name=name,
            content_type=content_type, size=size, session_url=session_url, origin=origin,
            uploaded_by=ctx['user_id'], fingerprint=fingerprint)
        return jsonify({
            'material_id': str(file_id), 'session_url': session_url, 'offset': 0,
            'finished': False, 'size': size, 'chunk_bytes': wiki_videos.CHUNK_BYTES,
            'resumed': False,
        }), 201

    # ── Состояние и отмена ───────────────────────────────────────────────
    @wiki_route('/videos/<file_id>', methods=('GET', 'DELETE'))
    def wiki_video_state(cursor, ctx, file_id):
        row = _own(cursor, ctx, file_id, lock=request.method == 'DELETE')
        if row is None:
            return _not_found()

        if request.method == 'DELETE':
            # Отменить можно то, что ещё едет, и то, что догрузилось, но ещё
            # не стоит ни в одной сохранённой статье. Второе — не роскошь:
            # «Отменить» нажали, пока сервер проверял файл, «завершить» успело
            # раньше, и ролик до 2 ГБ остался бы ничьим навсегда — уборка
            # смотрит только на недогруженные (поймано разбором 18.11.2026).
            # Ролик, привязанный к статье, отсюда не удаляется: его убирают из
            # текста, а файл вики, как и картинка, остаётся.
            if not row.get('upload_state') and row.get('article_id'):
                return _error('Ролик уже стоит в статье', 409, 'WIKI_VIDEO_READY')
            poster = wiki_videos.poster_row(cursor, row.get('poster_file_id'))
            if poster and poster.get('article_id'):
                poster = None
            wiki_videos.delete_rows(cursor, [row['id']] + ([poster['id']] if poster else []))
            if row.get('upload_session'):
                production_storage.cancel_session(row['upload_session'],
                                                  origin=row.get('upload_origin'))
            try:
                # Объект есть, только если файл догрузили, а «завершить» не дошло.
                _drop_objects(production_storage.client_of(gcs), [row] + ([poster] if poster else []))
            except Exception:  # noqa: BLE001
                logging.warning('Вики: объект отменённого ролика не удалён', exc_info=True)
            return jsonify({'status': 'canceled'})

        if not row.get('upload_state'):
            body = wiki_videos.describe(row)
            body.update({'offset': body['size'], 'finished': True})
            return jsonify(body)
        try:
            state = production_storage.session_offset(
                row['upload_session'], row['file_size'], origin=row.get('upload_origin'))
        except production_storage.StorageUnavailable as exc:
            return _unavailable(exc)
        if state is None:
            return _error('Загрузка устарела — начните заново', 410, 'WIKI_VIDEO_EXPIRED')
        offset, finished = state
        return jsonify({'offset': offset, 'finished': finished, 'status': 'uploading'})

    # ── Обложка ──────────────────────────────────────────────────────────
    @wiki_route('/videos/<file_id>/poster', methods=('POST',), capability='can_create')
    def wiki_video_poster(cursor, ctx, file_id):
        """Кадр из ролика, снятый браузером (productionUpload.capturePoster).

        Ложится обычной картинкой вики — через wiki/storage.py, то есть в WebP и
        со строкой wiki_files. Номер обложки запоминает строка ролика: из неё
        редактор возьмёт poster для узла, когда ролик догрузится.
        """
        row = _own(cursor, ctx, file_id, lock=True)
        if row is None:
            return _not_found()
        uploaded = request.files.get('file')
        if not uploaded:
            return _error('Кадр не приложен', 400, 'WIKI_VIDEO_POSTER_EMPTY')
        data = uploaded.read(wiki_videos.MAX_POSTER_BYTES + 1)
        if not data or len(data) > wiki_videos.MAX_POSTER_BYTES:
            return _error('Кадр больше 2 МБ', 413, 'WIKI_VIDEO_POSTER_SIZE')
        if not production_materials.content_matches('jpg', data[:16]):
            return _error('Кадр должен быть картинкой JPEG', 400, 'WIKI_VIDEO_POSTER_TYPE')
        stem = wiki_videos.title_from_name(row.get('original_name')) or 'video'
        try:
            poster_id, _url = wiki_storage.store_file(
                cursor, gcs, data=data, filename='%s.jpg' % stem[:60],
                content_type='image/jpeg', uploaded_by=ctx['user_id'])
        except Exception as exc:  # noqa: BLE001 — без обложки ролик всё равно загрузится
            return _unavailable(exc)
        if not poster_id:
            return _error('Хранилище файлов не настроено', 503, 'WIKI_VIDEO_STORAGE')
        wiki_videos.set_poster(cursor, row['id'], poster_id)
        return jsonify({'poster_url': wiki_videos.file_url(poster_id)}), 201

    # ── Завершение ───────────────────────────────────────────────────────
    @wiki_route('/videos/<file_id>/complete', methods=('POST',), capability='can_create')
    def wiki_video_complete(cursor, ctx, file_id):
        data = _body()
        row = _own(cursor, ctx, file_id, lock=True)
        if row is None:
            return _not_found()
        if not row.get('upload_state'):
            return jsonify(wiki_videos.describe(row))

        try:
            client = production_storage.client_of(gcs)
            info = production_storage.object_info(client, row['bucket'], row['blob_path'])
        except production_storage.StorageUnavailable as exc:
            return _unavailable(exc)
        if info is None:
            return _error('Ролик ещё не догрузился', 409, 'WIKI_VIDEO_INCOMPLETE')

        ext = production_materials.extension_of(row['blob_path'])
        problem = message = None
        if info['size'] != int(row['file_size'] or 0):
            problem = 'размер не совпал'
        client_crc = production_materials.crc32c_from_client(data.get('crc32c'))
        stored_crc = production_materials.crc32c_from_gcs(info.get('crc32c'))
        if problem is None and None not in (client_crc, stored_crc) and client_crc != stored_crc:
            problem = 'контрольная сумма не совпала'
        if problem is not None:
            message = 'Ролик повредился при загрузке — загрузите его ещё раз'
        else:
            try:
                head = production_storage.object_head(client, row['bucket'], row['blob_path'])
            except production_storage.StorageUnavailable as exc:
                return _unavailable(exc)
            if not production_materials.content_matches(ext, head):
                problem = 'содержимое не похоже на %s' % ext.upper()
                message = ('Это не %s — внутри файл другого формата. '
                           'Загрузите сам ролик, без переименования' % ext.upper())
        if problem is not None:
            logging.warning('Вики: ролик %s отклонён — %s', row['blob_path'], problem)
            poster = wiki_videos.poster_row(cursor, row.get('poster_file_id'))
            if poster and poster.get('article_id'):
                poster = None
            wiki_videos.delete_rows(cursor, [row['id']] + ([poster['id']] if poster else []))
            _drop_objects(client, [row] + ([poster] if poster else []))
            return _error(message, 422, 'WIKI_VIDEO_CORRUPT')

        duration = wiki_videos.positive_float(data.get('duration'), 24 * 3600)
        width = wiki_videos.positive_int(data.get('width'), 100000)
        height = wiki_videos.positive_int(data.get('height'), 100000)
        wiki_videos.mark_ready(cursor, row['id'], duration=duration, width=width, height=height)
        row.update({'upload_state': None, 'duration_seconds': duration,
                    'width': width, 'height': height})
        return jsonify(wiki_videos.describe(row))

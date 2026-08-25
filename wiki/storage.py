"""Файл в бакет и запись в wiki_files.

Одно место на всех, кто грузит. Тот же код жил замыканием в routes_import и
знал ровно один случай — картинку из редактора статьи; логотип парка приезжает
другим роутом с другим гейтом, а кладётся туда же. Второй экземпляр этих
десяти строк разошёлся бы с первым на первой же правке пути в бакете — и
разошёлся бы молча.
"""

from . import articles as wiki_articles
from . import importer as wiki_importer


def store_file(cursor, gcs, *, data, filename, content_type, uploaded_by,
               article_id=None):
    """Кладёт файл и заводит запись: (file_id, постоянный адрес).

    (None, None) — хранилище не настроено (нет бакета); отвечать на это должен
    вызывающий, потому что текст ошибки у каждой двери свой.

    Адрес постоянный, а не подписанный: подпись живёт часы, а статья и
    справочник — годами. Подпись выдаёт роут /file/<id> на каждый запрос.
    """
    bucket = gcs['bucket_name']() if gcs.get('bucket_name') else None
    if not bucket:
        return None, None

    blob_path = wiki_importer.blob_path_for(filename, content_type)
    gcs['client']().bucket(bucket).blob(blob_path).upload_from_string(
        data, content_type=content_type or 'application/octet-stream')

    file_id = wiki_articles.register_file(
        cursor, article_id=article_id, bucket=bucket, blob_path=blob_path,
        original_name=str(filename or 'file')[:255],
        content_type=content_type, file_size=len(data),
        width=None, height=None, uploaded_by=uploaded_by,
    )
    return file_id, '/api/wiki/file/%s' % file_id

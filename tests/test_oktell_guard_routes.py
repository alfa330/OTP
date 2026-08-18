"""Каркас роутов раздела: авторизация агента и выбор бакета для файла версии."""

import os

import pytest

from oktell_guard import routes


def test_release_bucket_prefers_dedicated_env(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLOUD_STORAGE_BUCKET', 'общий')
    monkeypatch.setenv('GOOGLE_CLOUD_STORAGE_BUCKET_TASKS', 'задачи')
    monkeypatch.setenv('GOOGLE_CLOUD_STORAGE_BUCKET_AGENTS', 'агенты')
    assert routes.release_bucket_name() == 'агенты'


def test_release_bucket_falls_back(monkeypatch):
    monkeypatch.delenv('GOOGLE_CLOUD_STORAGE_BUCKET_AGENTS', raising=False)
    monkeypatch.setenv('GOOGLE_CLOUD_STORAGE_BUCKET_TASKS', 'задачи')
    assert routes.release_bucket_name() == 'задачи'


def test_release_bucket_absent(monkeypatch):
    for name in routes.RELEASE_BUCKET_ENV:
        monkeypatch.delenv(name, raising=False)
    assert routes.release_bucket_name() == ''


def test_download_link_ttl_is_longer_than_avatar_ttl():
    """Ссылку получает и агент на медленной машине: 15 минут, как у аватаров,
    ему может не хватить, и обновление молча не доедет."""
    assert routes.DOWNLOAD_URL_TTL_MINUTES >= 60

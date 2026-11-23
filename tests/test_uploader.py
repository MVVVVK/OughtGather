import pytest
from unittest.mock import MagicMock, patch
import os
from src.uploader.webdav_uploader import WebDavUploader
from src.config import WebDavConfig

def test_webdav_uploader_success(tmp_path):
    from tests.test_webdav_delivery import FakeDAV
    import httpx
    config = WebDavConfig(enabled=True, url="https://dav.example", username="user", password="password", remote_path="/test")
    fake = FakeDAV()
    book = tmp_path / "test.epub"
    book.write_bytes(b"content")
    with patch("src.uploader.webdav_uploader.get_webdav_config", return_value=config), \
         httpx.Client(transport=httpx.MockTransport(fake)) as client:
        assert WebDavUploader(client=client).upload_epub(str(book))
        assert fake.files["/test/latest.epub"] == b"content"

@patch("src.uploader.webdav_uploader.get_webdav_config")
def test_webdav_uploader_disabled(mock_get_config):
    mock_get_config.return_value = WebDavConfig(enabled=False)
    uploader = WebDavUploader()
    uploader.logger = MagicMock()
    result = uploader.upload_epub("test.epub")
    assert result is False
    uploader.logger.info.assert_called_once_with("WebDAV is not enabled, skip pushing.")

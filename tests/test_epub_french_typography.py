import re
import unicodedata
from datetime import datetime, timezone
from zipfile import ZIP_STORED, ZipFile
from xml.etree import ElementTree as ET

from scripts.epub_font_specimens import CHINESE, ENGLISH, FRENCH_1, FRENCH_2, PUNCTUATION, build, results
from src.epub.typography import article_language, language_tag
from src.fetchers.base import Article


def test_language_precedence_and_unknown():
    article = Article("", "", "")
    assert article_language(article, None) is None
    article.metadata = {"language": 'fr" onclick="x', "quality": {"language": "fr-CA"}}
    assert article_language(article, "en") == "fr"
    article.metadata["language"] = "en-GB"
    assert article_language(article, "fr") == "en"
    assert language_tag("zh-hant-tw") == "zh-Hant-TW"
    assert language_tag("auto") is None


def test_epub_font_opt_in_and_text_parity(tmp_path, monkeypatch):
    monkeypatch.setenv("EPUB_LANGUAGE", "zh-CN")
    # The two builds can cross a wall-clock second in CI; only compare typography.
    monkeypatch.setattr("src.epub.generator.get_now", lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    font_dir = tmp_path / "font-pack"
    font_dir.mkdir()
    (font_dir / "OFL.txt").write_text("Test license", encoding="utf-8")
    # Placeholder bytes exercise packaging. The distributed specimens use real fonts and EPUBCheck.
    for face in ("regular", "bold", "italic", "bold-italic"):
        (font_dir / f"{face}.ttf").write_bytes(face.encode())

    with (
        ZipFile(build(tmp_path / "baseline.epub")) as baseline,
        ZipFile(build(tmp_path / "candidate.epub", font_dir)) as candidate,
    ):
        assert baseline.infolist()[0].filename == "mimetype"
        assert baseline.infolist()[0].compress_type == ZIP_STORED
        for path in baseline.namelist():
            if path.endswith(".xhtml"):
                assert baseline.read(path) == candidate.read(path)
        for filename, language in (
            ("chapter_0", "fr"), ("chapter_1", "en"), ("chapter_2", "zh-CN"),
            ("divider_0", "fr"), ("nav", "zh-CN"), ("overview", "zh-CN"),
        ):
            root = ET.fromstring(baseline.read(f"EPUB/{filename}.xhtml"))
            assert root.get("lang") == root.get("{http://www.w3.org/XML/1998/namespace}lang") == language
            assert root.find("./{*}head/{*}link").get("href") == "style/default.css"
        for filename in ("divider_0", "nav", "overview"):
            root = ET.fromstring(baseline.read(f"EPUB/{filename}.xhtml"))
            link = root.find('.//{*}a[@href="chapter_0.xhtml"]')
            assert link.get("lang") == "fr"
            assert link.text == "Économie, cœur et œuvre"

        french = ET.fromstring(baseline.read("EPUB/chapter_0.xhtml"))
        paragraphs = french.findall(".//{*}div/{*}p")
        assert paragraphs[0].text == f"NFC · {FRENCH_1}"
        assert paragraphs[1].text == f"NFD · {unicodedata.normalize('NFD', FRENCH_1)}"
        assert paragraphs[2].find("{*}strong").text == FRENCH_2
        assert paragraphs[3].find("{*}em").text == FRENCH_2
        assert paragraphs[4].find("{*}strong/{*}em").text == PUNCTUATION
        assert [span.text for span in paragraphs[5]] == [ENGLISH, CHINESE]

        css = candidate.read("EPUB/style/default.css").decode()
        opf = ET.fromstring(candidate.read("EPUB/content.opf"))
        assert opf.get("version") == "3.0"
        assert [node.text for node in opf.findall("./{*}metadata/{*}language")] == ["zh-CN", "fr", "en"]
        assert opf.find("./{*}metadata/{*}rights").text == "Test license"
        assert "@font-face" not in baseline.read("EPUB/style/default.css").decode()
        assert "EPUB/fonts/regular.ttf" not in baseline.namelist()
        refs = set(re.findall(r'url\("\.\./(fonts/[^"]+)"\)', css))
        manifest = {item.get("href") for item in opf.findall(".//{*}item[@media-type='font/ttf']")}
        assert refs == manifest and len(refs) == 4
        assert all(f"EPUB/{href}" in candidate.namelist() for href in refs)


def test_unknown_article_does_not_inherit_interface_language(tmp_path, monkeypatch):
    specimen = results()
    specimen[0].source.metadata.clear()
    specimen[0].articles[0].metadata["language"] = "auto"
    monkeypatch.setattr("scripts.epub_font_specimens.results", lambda: specimen)
    monkeypatch.setenv("EPUB_LANGUAGE", "en-US")

    with ZipFile(build(tmp_path / "unknown.epub")) as book:
        chapter = ET.fromstring(book.read("EPUB/chapter_0.xhtml"))
        assert chapter.get("lang") == chapter.get("{http://www.w3.org/XML/1998/namespace}lang") == "und"
        assert chapter.find(".//{*}h1").text == "Économie, cœur et œuvre"
        assert chapter.find(".//{*}p[@class='toc-link']").get("lang") == "en-US"
        for page in ("nav", "overview", "divider_0"):
            root = ET.fromstring(book.read(f"EPUB/{page}.xhtml"))
            assert root.find('.//{*}a[@href="chapter_0.xhtml"]').get("lang") == "und"

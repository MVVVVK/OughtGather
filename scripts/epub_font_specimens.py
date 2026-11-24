"""Build two synthetic EPUBs through the production generator, without network access."""

import argparse
import io
import os
import unicodedata
from pathlib import Path
from unittest.mock import patch

from ebooklib import epub
from PIL import Image

from src.config import Config, ContentSource, TitleConfig
from src.epub.generator import EPUBGenerator
from src.fetchers.base import Article, FetchResult


FRENCH_1 = "L'été à Paris : économie, éducation, français, cœur, œuvre, où êtes-vous ?"
FRENCH_2 = "Ça va ? Noël, naïve, île, août, élève, français."
PUNCTUATION = "«\u00a0L’œuvre\u202f» — cœur, œuvre, Æsop, æ."
ENGLISH = "English typography: The quick brown fox jumps over the lazy dog."
CHINESE = "中文排版对照：新闻、字体与标点。"


def results():
    source = ContentSource(
        type="rss", src="https://example.org/feed", title="Édition française · OughtGather",
        metadata={"epub_language": "fr-FR"},
    )
    french = Article(
        title="Économie, cœur et œuvre",
        content=(
            f"<p>NFC · {FRENCH_1}</p>"
            f"<p>NFD · {unicodedata.normalize('NFD', FRENCH_1)}</p>"
            f"<p><strong>{FRENCH_2}</strong></p>"
            f"<p><em>{FRENCH_2}</em></p>"
            f"<p><strong><em>{PUNCTUATION}</em></strong></p>"
            f'<p><span lang="en" xml:lang="en">{ENGLISH}</span> '
            f'<span lang="zh-CN" xml:lang="zh-CN">{CHINESE}</span></p>'
        ),
        url="",
    )
    english = Article(
        title="English typography", content=f"<p>{ENGLISH}</p>", url="",
        metadata={"language": "en-GB"},
    )
    chinese = Article(
        title="中文排版对照", content=f"<p>{CHINESE}</p>", url="",
        metadata={"language": "zh-CN"},
    )
    return [FetchResult(source=source, articles=[french, english, chinese])]


def build(output, font_dir=None):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    specimen = results()
    config = Config(
        title=TitleConfig(text="OughtGather · French Font Test"),
        body=[specimen[0].source],
        load_images="N",
    )
    cover = io.BytesIO()
    Image.new("RGB", (64, 96), "white").save(cover, "PNG")
    generator = EPUBGenerator(config)
    # Keep the same visible book title and layout in both files.
    with (
        patch.dict(os.environ, {"EPUB_FRENCH_FONT_DIR": str(font_dir) if font_dir else ""}),
        patch.object(generator.cover_generator, "generate", return_value=("cover.png", cover.getvalue())),
        patch.object(generator, "_save_book", side_effect=lambda book: epub.write_epub(str(output), book)),
    ):
        generator.generate(specimen)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("font_dir", type=Path)
    args = parser.parse_args()
    with patch.dict(os.environ, {"EPUB_LANGUAGE": "zh-CN"}):
        build(args.output / "A-baseline.epub")
        build(args.output / "B-font-fix-candidate.epub", args.font_dir)


if __name__ == "__main__":
    main()

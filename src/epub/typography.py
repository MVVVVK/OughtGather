"""Explicit language metadata and opt-in French typography."""

import os
import re
from pathlib import Path

from ebooklib import epub


def language_tag(value):
    if isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{4})?(?:-[A-Za-z]{2}|-\d{3})?", value):
            parts = value.split("-")
            primary = parts[0].lower()
            if primary in {"fr", "en"}:
                return primary
            return "-".join([primary, *(p.title() if len(p) == 4 else p.upper() for p in parts[1:])])
    return None


def language_attributes(language):
    return f' lang="{language}" xml:lang="{language}"' if language else ""


def article_language(article, source_language):
    quality = article.metadata.get("quality")
    return (
        language_tag(article.metadata.get("language"))
        or language_tag(quality.get("language") if isinstance(quality, dict) else None)
        or source_language
    )


def add_french_fonts(book):
    directory = os.getenv("EPUB_FRENCH_FONT_DIR")
    if not directory:
        return ""

    path = Path(directory)
    rights = (path / "OFL.txt").read_text(encoding="utf-8").strip()
    faces = [
        ("regular", "normal", "normal"),
        ("bold", "bold", "normal"),
        ("italic", "normal", "italic"),
        ("bold-italic", "bold", "italic"),
    ]
    css = []
    for name, weight, style in faces:
        data = (path / f"{name}.ttf").read_bytes()
        book.add_item(epub.EpubItem(
            uid=f"french-font-{name}",
            file_name=f"fonts/{name}.ttf",
            media_type="font/ttf",
            content=data,
        ))
        css.append(
            '@font-face { font-family: "OughtGather French"; '
            f'src: url("../fonts/{name}.ttf") format("truetype"); '
            f"font-weight: {weight}; font-style: {style}; }}"
        )
    book.add_metadata("DC", "rights", rights)
    css.extend([
        'body [lang|="fr"] { font-family: "OughtGather French", serif; }',
        'body [lang|="fr"] [lang|="zh"], body [lang|="fr"] [lang|="en"] { font-family: serif; }',
        'body [lang|="fr"] code, body [lang|="fr"] pre { font-family: monospace; }',
    ])
    return "\n" + "\n".join(css) + "\n"

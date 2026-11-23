"""Conservative checks for extracted news and RSS fallbacks."""
import re
from bs4 import BeautifulSoup

BLOCKS = {
    "javascript_disabled": r"javascript (?:is )?disabled|enable javascript (?:to proceed|in your browser)",
    "access_blocked": r"access denied|\b403 forbidden\b|verify (?:that )?you are human|captcha|robot verification|checking your browser",
    "cookie_prompt": r"(?:accept|allow|enable) (?:all )?cookies|cookie consent|consent to cookies|accepter (?:tous )?les cookies",
    "player_placeholder": r"(?:enable|activate|loading|watch|play) (?:the |this |your )?(?:video|audio|player)|video player|lecteur vid[ée]o",
}
TRUNCATED = re.compile(r"\b(?:lire la suite|read more|continue reading)\b", re.I)
MEDIA = re.compile(r"(?:^|[\s\[(:-])(?:video|vidéo|audio|podcast|live|en direct)(?:[\s\]): -]|$)", re.I)


def inspect(content, *, full=False):
    soup = BeautifulSoup(content or "", "html.parser")
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'figure', 'figcaption']):
        tag.decompose()
    text = soup.get_text(' ', strip=True)
    for reason, pattern in BLOCKS.items():
        if re.search(pattern, text, re.I):
            return False, reason, len(text)
    if not text:
        return False, 'empty_content', 0
    if full:
        paragraphs = [p.get_text(' ', strip=True) for p in soup.find_all('p')]
        substantive = [p for p in paragraphs if len(p) >= 50]
        if TRUNCATED.search(text):
            return False, 'truncated_content', len(text)
        if not ((len(substantive) >= 2 and len(text) >= 180) or
                (len(substantive) >= 1 and len(text) >= 350)):
            return False, 'insufficient_article_text', len(text)
    return True, '', len(text)

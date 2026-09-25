"""Conservative, deterministic EPUB source filtering and script contamination signals."""

import re
from pathlib import PurePosixPath

from bs4 import BeautifulSoup

from .models import SourceFilterRecord


SOURCE_FILTER_VERSION = 'epub-source-filter-v1'
_CONTACT = re.compile(r'微信号|微信公众号|公众号|QQ(?:号|群)?|加小编', re.I)
_URL = re.compile(r'https?://|www\.|[a-z0-9.-]+\.(?:com|cn|net|org)\b', re.I)
_PRODUCTION = re.compile(r'^(?:打字|录入|校对|电子书制作)\s*[:：]\s*\S.{0,78}$')
_STRUCTURAL_NAMES = {
    'cover': ('cover', 'cover_resource'),
    'frontcover': ('cover', 'cover_resource'),
    'backcover': ('cover', 'cover_resource'),
    'titlepage': ('cover', 'title_page_resource'),
    'title_page': ('cover', 'title_page_resource'),
    'title-page': ('cover', 'title_page_resource'),
    'toc': ('table_of_contents', 'toc_resource'),
    'nav': ('table_of_contents', 'nav_resource'),
    'contents': ('table_of_contents', 'toc_resource'),
    'copyright': ('publisher_notice', 'copyright_resource'),
    'colophon': ('publisher_notice', 'colophon_resource'),
    'imprint': ('publisher_notice', 'imprint_resource'),
    'advertisement': ('advertisement', 'advertisement_resource'),
    'ads': ('advertisement', 'advertisement_resource'),
}


def _compact(text: str) -> str:
    return ' '.join(text.split())


def promotion_rule(text: str) -> str | None:
    """Require a call to action or a download context alongside contact/site data."""
    value = _compact(text)
    if not value:
        return None
    lower = value.lower()
    if 'ireadweek.com' in lower and any(word in value for word in ('网址', '网站', '下载', '电子书', '关注')):
        return 'download_site_promotion'
    if '免费电子书' in value and (_CONTACT.search(value) or any(x in value for x in ('下载', '获取', '网站'))):
        return 'free_ebook_promotion'
    if ('关注' in value or '扫码' in value or '加小编' in value) and _CONTACT.search(value):
        return 'wechat_qq_promotion'
    if any(word in value for word in ('下载网站', '电子书资源站', '电子书下载网站')) and _URL.search(value):
        return 'download_site_promotion'
    return None


def script_contamination_rule(text: str) -> str | None:
    """Block unmistakable promotions in dialogue without rejecting ordinary URLs or numbers."""
    value = _compact(text)
    if 'ireadweek.com' in value.lower():
        return 'download_site_promotion'
    if re.search(r'QQ(?:号|群)?\s*[:：]?\s*\d{5,}', value, re.I):
        return 'wechat_qq_promotion'
    if re.search(r'微信号\s*(?:[:：]|[A-Za-z0-9_-]{5,})', value):
        return 'wechat_qq_promotion'
    return promotion_rule(value)


def source_record(unit: str, classification: str, removed: bool, reason: str,
                  matched_rule: str, text: str) -> SourceFilterRecord:
    return SourceFilterRecord(unit=unit, classification=classification, removed=removed,
                              reason=reason, matched_rule=matched_rule,
                              preview=_compact(text)[:120])


def structural_rule(name: str, properties: list[str], soup: BeautifulSoup) -> tuple[str, str] | None:
    if 'nav' in properties:
        return 'table_of_contents', 'epub_nav_property'
    if 'cover-image' in properties:
        return 'cover', 'epub_cover_property'
    stem = PurePosixPath(name).stem.lower()
    if stem in _STRUCTURAL_NAMES:
        return _STRUCTURAL_NAMES[stem]
    text = _compact(soup.get_text(' ', strip=True))
    links = soup.find_all('a', href=True)
    if (len(links) >= 5 and re.match(r'^(?:table of contents|目录)(?:\b|\s)', text, re.I)):
        return 'table_of_contents', 'linked_contents_page'
    return None


def advertisement_unit_rule(soup: BeautifulSoup, text: str) -> str | None:
    """Only remove a whole unit when it is short and densely promotional."""
    if len(text) > 1200:
        return None
    # A promotional paragraph alongside actual prose is a mixed document, not
    # an advertisement page. Remove that paragraph later and retain the prose.
    for paragraph in soup.find_all('p'):
        line = _compact(paragraph.get_text(' ', strip=True))
        if (len(line) >= 35 and line.endswith(('。', '！', '？'))
                and not promotion_rule(line) and not re.match(r'^\d+[、.]', line)):
            return None
    rule = promotion_rule(text)
    signals = sum(bool(pattern) for pattern in (
        _CONTACT.search(text), _URL.search(text),
        re.search(r'免费电子书|下载网站|电子书资源站', text),
        re.search(r'关注|扫码|加小编', text),
    ))
    paragraphs = [_compact(p.get_text(' ', strip=True)) for p in soup.find_all('p')]
    if (rule and paragraphs and all(promotion_rule(line) or line.startswith('如果你不知道读什么书')
                                    for line in paragraphs)):
        return rule
    return rule if rule and signals >= 3 else None


def filter_epub_body(soup: BeautifulSoup, unit: str) -> list[SourceFilterRecord]:
    records = []
    for number, paragraph in enumerate(soup.find_all('p'), 1):
        text = paragraph.get_text(' ', strip=True)
        rule = promotion_rule(text)
        classification = 'advertisement'
        if not rule and _PRODUCTION.fullmatch(_compact(text)):
            rule, classification = 'production_credit', 'production_note'
        if rule:
            records.append(source_record(f'{unit}#p:{number}', classification, True, rule, rule, text))
            paragraph.decompose()
    return records

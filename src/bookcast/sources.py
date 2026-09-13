"""Official catalog and user-supplied source adapters; never general web search."""

import csv
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET

from .errors import BookCastError
from .models import utc_now
from .source_api import BookIdentity, EditionCandidate, SearchResult, SourceOffer, BookSourceProvider
from .source_http import SafeHTTP, public_url
from .storage import artifact_path, fingerprint, job_lock, sha256_file, write_json

MIRROR = "https://gutenberg.pglaf.org"
CATALOG_URL = MIRROR + "/cache/epub/feeds/pg_catalog.csv"
NS = {"pg": "http://www.gutenberg.org/2009/pgterms/", "dc": "http://purl.org/dc/terms/",
      "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}


def words(value: str) -> list[str]:
    return re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold())


class GutenbergSourceProvider:
    name = "gutenberg"

    def __init__(self, cache_dir: Path, http: SafeHTTP | None = None, *, refresh: bool = False):
        self.cache_dir, self.http, self.refresh = cache_dir.resolve(), http or SafeHTTP(), refresh

    def _catalog(self) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_path(self.cache_dir, "pg_catalog.csv")
        receipt_path = artifact_path(self.cache_dir, "catalog.json")
        with job_lock(self.cache_dir):
            valid = False
            try:
                receipt = json.loads(receipt_path.read_text())
                valid = receipt["url"] == CATALOG_URL and sha256_file(path) == receipt["sha256"]
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if self.refresh or not valid:
                def validate_csv(temporary):
                    with temporary.open(encoding="utf-8-sig", newline="") as stream:
                        header = next(csv.reader(stream), [])
                    if not {"Text#", "Type", "Title", "Language", "Authors", "Issued"}.issubset(header):
                        raise BookCastError("Gutenberg CSV 目录字段不匹配。")
                receipt = self.http.download(CATALOG_URL, path, max_bytes=64 * 1024 * 1024,
                    allowed_mimes={"text/csv", "text/plain", "application/octet-stream"}, validate=validate_csv)
                write_json(receipt_path, {**receipt, "timestamp": utc_now()})
        return path

    def search(self, title, *, author=None, language=None) -> SearchResult:
        tokens = words(title)
        if not tokens:
            raise BookCastError("书名必须包含可搜索的文字。")
        candidates, complete = [], True
        try:
            with self._catalog().open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    if row["Type"] != "Text" or not all(token in words(row["Title"]) for token in tokens):
                        continue
                    if author and not all(token in words(row["Authors"]) for token in words(author)):
                        continue
                    languages = [part.strip() for part in row["Language"].split(";")]
                    if language and language.casefold() not in [part.casefold() for part in languages]:
                        continue
                    number = row["Text#"]
                    if not re.fullmatch(r"[1-9][0-9]*", number):
                        raise ValueError()
                    if len(candidates) == 50:
                        complete = False
                        break
                    candidates.append(EditionCandidate(id=f"gutenberg:{number}", provider=self.name,
                        identity=BookIdentity(title=row["Title"], authors=[a.strip() for a in row["Authors"].split(";") if a.strip()],
                                              language=";".join(languages) or None),
                        catalog_release_date=row["Issued"] or None,
                        metadata_url=f"{MIRROR}/cache/epub/{number}/pg{number}.rdf"))
        except (OSError, ValueError, csv.Error, KeyError, TypeError):
            raise BookCastError("无法读取有效 Gutenberg 目录。") from None
        return SearchResult(candidates=candidates, complete=complete, warnings=[
            "目录记录不等同于印刷版次；ISBN、原版出版年份和版次未知时为 null。",
            "本地目录缓存需用 --refresh-catalog 显式更新。",
            *([] if complete else ["候选超过 50 项；请补充 --author、--language 或更具体的书名。"])])

    def sources(self, candidate) -> list[SourceOffer]:
        if candidate.provider != self.name or not re.fullmatch(r"gutenberg:[1-9][0-9]*", candidate.id):
            raise BookCastError("Gutenberg 候选标识无效。")
        number = int(candidate.id.split(":")[1])
        url = f"{MIRROR}/cache/epub/{number}/pg{number}.rdf"
        raw, receipt = self.http.fetch(url, max_bytes=1024 * 1024,
            allowed_mimes={"application/rdf+xml", "application/xml", "text/xml", "application/octet-stream"})
        try:
            if re.search(br"<!DOCTYPE|<!ENTITY", raw.replace(b"\x00", b""), re.I):
                raise ValueError()
            root = ET.fromstring(raw)
            ebook = root.find("pg:ebook", NS)
            if ebook is None or ebook.get(f"{{{NS['rdf']}}}about") not in {
                    f"ebooks/{number}", f"http://www.gutenberg.org/ebooks/{number}", f"https://www.gutenberg.org/ebooks/{number}"}:
                raise ValueError()
            if words(ebook.findtext("dc:title", default="", namespaces=NS)) != words(candidate.identity.title):
                raise BookCastError("目录与 RDF 书名不一致，请刷新目录后重新选择版本。")
            rights = ebook.findtext("dc:rights", default="", namespaces=NS).strip()
            # cc:Work's CC0 license applies to METADATA, not the ebook.
            eligible = rights == "Public domain in the USA."
            formats = ebook.findall("dc:hasFormat/pg:file", NS)
            has_txt = any(file.get(f"{{{NS['rdf']}}}about") == f"https://www.gutenberg.org/ebooks/{number}.txt.utf-8"
                          and any((value.text or "").startswith("text/plain")
                                  for value in file.findall("dc:format/rdf:Description/rdf:value", NS)) for file in formats)
        except (ET.ParseError, ValueError):
            raise BookCastError("Gutenberg RDF 身份或格式无效。") from None
        evidence = artifact_path(self.cache_dir, f"pg{number}-{receipt['sha256']}.rdf")
        from .storage import atomic_target
        with atomic_target(evidence) as temporary:
            temporary.write_bytes(raw)
        write_json(artifact_path(self.cache_dir, f"pg{number}.json"), {**receipt, "timestamp": utc_now()})
        if not has_txt:
            return []
        return [SourceOffer(candidate_id=candidate.id, provider=self.name, format="txt",
            url=f"{MIRROR}/cache/epub/{number}/pg{number}.txt", eligible=eligible,
            rights_category="public_domain" if eligible else "unknown", rights_statement=rights or "无书籍版权声明",
            rights_url=url, jurisdiction="US" if eligible else None, expected_book_id=number,
            evidence_sha256=receipt["sha256"])]


class UserSourceProvider:
    """User attribution is retained as an assertion, not an independently verified license."""
    name = "user"

    def __init__(self, *, url: str | None = None, local_path: Path | None = None,
                 fmt: str = "txt", rights_confirmed: bool = False):
        if (url is None) == (local_path is None):
            raise BookCastError("请选择且只选择 --url 或 --file。")
        if url:
            public_url(url)
        self.url, self.local_path, self.fmt, self.rights_confirmed = url, local_path, fmt, rights_confirmed

    def search(self, title, *, author=None, language=None):
        identity = BookIdentity(title=title, authors=[author] if author else [], language=language)
        key = fingerprint({"identity": identity.model_dump(), "url": self.url,
                           "file": str(self.local_path.resolve()) if self.local_path else None})[:24]
        return SearchResult(candidates=[EditionCandidate(id=f"user:{key}", provider=self.name,
                                                         identity=identity, metadata_origin="user")],
                            warnings=["书名与版本信息由用户提供，未独立核实；文件格式会另行校验。"])

    def sources(self, candidate):
        return [SourceOffer(candidate_id=candidate.id, provider=self.name, format=self.fmt,
            url=self.url, local_path=str(self.local_path.resolve()) if self.local_path else None,
            rights_category="user_provided", eligible=self.local_path is not None or self.rights_confirmed,
            rights_statement="用户提供本地文件" if self.local_path else "用户确认有权从该 URL 获取并处理文件；未独立核实授权")]


class SourceRegistry:
    def __init__(self):
        self.factories = {}

    def register(self, name, factory):
        if name in self.factories:
            raise BookCastError("来源 Provider 已注册。")
        self.factories[name] = factory

    def create(self, name, **kwargs) -> BookSourceProvider:
        if name not in self.factories:
            raise BookCastError("未知来源 Provider。")
        return self.factories[name](**kwargs)


def source_registry() -> SourceRegistry:
    registry = SourceRegistry()
    registry.register("gutenberg", GutenbergSourceProvider)
    registry.register("user", UserSourceProvider)
    return registry

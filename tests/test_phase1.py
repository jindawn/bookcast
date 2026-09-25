import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ebooklib import epub
import pymupdf

from bookcast.errors import BookCastError
from bookcast.models import BookMetadata, Chapter, ChapterAnalysis, PodcastScript
from bookcast.parsers import parse_book, parse_epub, parse_pdf, parse_txt
from bookcast.pipeline import Pipeline, job_status
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.storage import sha256_file
from typer.testing import CliRunner
from bookcast.cli import app


class FailingTTS(MockTTSProvider):
    cache_key = MockTTSProvider.cache_key

    def __init__(self):
        self.calls = 0

    def synthesize(self, script, destination):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated crash")
        return super().synthesize(script, destination)


class Phase1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def metadata(self, path, fmt):
        return BookMetadata(book_id="test-book", title="Test", source_name=path.name,
                            source_sha256="a" * 64, source_format=fmt)

    def test_txt_parser_preserves_headings_and_line_locator(self):
        path = self.root / "book.txt"
        path.write_text("第一章 开始\n你好，世界。\n\n第二章 继续\n继续阅读。\n", encoding="utf-8")
        book = parse_txt(path, self.metadata(path, "txt"))
        self.assertEqual([c.title for c in book.chapters], ["第一章 开始", "第二章 继续"])
        self.assertEqual(book.chapters[0].source_locator, "lines:1-3")
        self.assertEqual(book.metadata.chapter_ids, ["0001", "0002"])

    def test_txt_parser_rejects_invalid_encoding(self):
        path = self.root / "broken.txt"
        path.write_bytes(b"\xff\xfe\x00\xd8")
        with self.assertRaises(BookCastError):
            parse_txt(path, self.metadata(path, "txt"))

    def test_epub_parser_uses_spine_order_and_html_text(self):
        path = self.root / "book.epub"
        book = epub.EpubBook()
        book.set_identifier("test-id")
        book.set_title("测试 EPUB")
        book.set_language("zh")
        first = epub.EpubHtml(title="第一章", file_name="first.xhtml", lang="zh")
        first.set_content("<html><body><h1>第一章</h1><p>第一段。</p><script>bad()</script></body></html>")
        second = epub.EpubHtml(title="第二章", file_name="second.xhtml", lang="zh")
        second.set_content("<html><body><h1>第二章</h1><p>第二段。</p></body></html>")
        book.add_item(first)
        book.add_item(second)
        book.spine = [first, second]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        epub.write_epub(str(path), book)
        parsed = parse_epub(path, self.metadata(path, "epub"))
        self.assertEqual([c.title for c in parsed.chapters], ["第一章", "第二章"])
        self.assertIn("第一段。", parsed.chapters[0].text)
        self.assertNotIn("bad", parsed.chapters[0].text)

    def test_epub_source_filter_removes_real_style_promotions_before_mock_analysis(self):
        source = self.root / 'promoted.epub'
        book = epub.EpubBook()
        book.set_identifier('source-filter-test')
        book.set_title('狂人日记')
        book.set_language('zh')
        pages = [
            ('cover.xhtml', '<h1>封面</h1><p>封面文字</p>'),
            ('toc.xhtml', '<p>Table of Contents</p>' + ''.join(
                f'<a href="chapter.xhtml#{n}">第{n}节</a>' for n in range(6))),
            ('1.html', '<p>如果你不知道读什么书，</p><p>就关注这个微信号。</p>'
             '<p>免费电子书请加小编微信或QQ：2338856113</p>'
             '<p>周读网址：www.ireadweek.com，电子书下载网站。</p>'),
            ('preface.xhtml', '<h1>自序</h1><p>我写这篇序言，是要说明写作时见到的生活和人物。</p>'),
            ('chapter.xhtml', '<h1>狂人日记</h1>'
             '<p>我翻开历史一查，这历史没有年代。歪歪斜斜的每页上都写着仁义道德几个字。</p>'
             '<p>本书由行行整理，如果你不知道读什么书，就关注微信公众号，'
             '免费电子书请加小编微信或QQ：2338856113，周读网址：www.ireadweek.com。</p>'
             '<p>我横竖睡不着，仔细看了半夜，才从字缝里看出字来。</p>'),
            ('discussion.xhtml', '<h1>讨论</h1><p>文中讨论微信这样的通讯工具，'
             '数字2338856113和网址https://example.org都是普通资料，不构成推广。</p>'),
            ('back.xhtml', '<p>如果你不知道读什么书，就关注这个微信号。</p>'
             '<p>免费电子书请加小编微信或QQ：2338856113。</p>'
             '<p>周读网址：www.ireadweek.com，电子书下载网站。</p>'),
            ('single-ad.xhtml', '<p>如果你不知道读什么书，就关注这个微信号。</p>'),
            ('copyright.xhtml', '<h1>版权信息</h1><p>出版制作说明。</p>'),
        ]
        spine = []
        for name, html in pages:
            page = epub.EpubHtml(title=name, file_name=name, lang='zh')
            page.media_type = 'application/xhtml+xml'
            page.set_content(f'<html><body>{html}</body></html>')
            book.add_item(page)
            spine.append(page)
        book.spine = spine
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        epub.write_epub(str(source), book)

        metadata = BookMetadata(book_id='fixture', title='fixture', source_name=source.name,
                                source_sha256=sha256_file(source), source_format='epub')
        parsed = parse_epub(source, metadata)
        self.assertEqual(len(parsed.chapters), 3)
        self.assertEqual(parsed.metadata.chapter_ids, ['0001', '0002', '0003'])
        self.assertEqual([c.source_locator for c in parsed.chapters],
                         ['epub:preface.xhtml', 'epub:chapter.xhtml', 'epub:discussion.xhtml'])
        self.assertIn('我写这篇序言', parsed.chapters[0].text)
        self.assertIn('我翻开历史一查', parsed.chapters[1].text)
        self.assertNotIn('ireadweek', parsed.chapters[1].text)
        self.assertIn('https://example.org', parsed.chapters[2].text)
        self.assertIn('2338856113', parsed.chapters[2].text)
        self.assertTrue(any(r.removed and r.classification == 'table_of_contents' for r in parsed.source_filter))
        self.assertTrue(any(r.removed and r.classification == 'cover' for r in parsed.source_filter))
        self.assertTrue(any(r.removed and r.matched_rule == 'epub_nav_document' for r in parsed.source_filter))
        self.assertTrue(any(r.removed and r.classification == 'publisher_notice' for r in parsed.source_filter))
        self.assertTrue(any(not r.removed and r.unit == 'epub:preface.xhtml' for r in parsed.source_filter))
        self.assertTrue(any(r.removed and r.matched_rule == 'download_site_promotion'
                            for r in parsed.source_filter))

        job = Pipeline(MockLLMProvider(), MockTTSProvider(), self.root / 'output').generate(source,
                                                                                           mode='two_host', minutes=2)
        audit = json.loads((job / 'source_filter.json').read_text())
        self.assertEqual(audit['schema_version'], 1)
        self.assertTrue(any(r['unit'] == 'epub:1.html' and r['classification'] == 'advertisement'
                            and r['removed'] for r in audit['units']))
        self.assertTrue(any(r['unit'] == 'epub:single-ad.xhtml' and r['classification'] == 'advertisement'
                            and r['removed'] for r in audit['units']))
        self.assertTrue(any(r['unit'] == 'epub:chapter.xhtml#p:2' and r['removed']
                            for r in audit['units']))
        for folder in ('chapters', 'analysis', 'synthesis', 'scripts'):
            for artifact in (job / folder).rglob('*.json'):
                self.assertNotIn('ireadweek', artifact.read_text())
        self.assertTrue((job / 'podcast.mp3').is_file())
        old_parse = json.loads((job / 'manifest.json').read_text())['steps']['parse']['fingerprint']
        with patch('bookcast.source_sanitation.SOURCE_FILTER_VERSION', 'epub-source-filter-v2'):
            Pipeline(MockLLMProvider(), MockTTSProvider(), self.root / 'output').resume_job(job)
        new_parse = json.loads((job / 'manifest.json').read_text())['steps']['parse']['fingerprint']
        self.assertNotEqual(old_parse, new_parse)

    def test_pdf_parser_extracts_pages_and_locator(self):
        path = self.root / "book.pdf"
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((72, 72), "First page\nExtractable text")
        document.save(path)
        document.close()
        parsed = parse_pdf(path, self.metadata(path, "pdf"))
        self.assertEqual(len(parsed.chapters), 1)
        self.assertIn("Extractable text", parsed.chapters[0].text)
        self.assertEqual(parsed.chapters[0].source_locator, "page:1")

    def test_mock_providers_return_two_speakers_and_valid_tone(self):
        chapter = Chapter(id="0001", title="测试", text="一些内容", source_locator="lines:1-2")
        llm = MockLLMProvider()
        analysis = llm.analyze(chapter)
        script = llm.script(chapter, analysis)
        self.assertTrue(analysis.is_mock)
        self.assertEqual({turn.speaker for turn in script.turns}, {"主持人", "嘉宾"})
        wav = self.root / "tone.wav"
        MockTTSProvider().synthesize(script, wav)
        import wave
        with wave.open(str(wav), "rb") as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 24000))
            self.assertGreater(audio.getnframes(), 0)

    def test_pipeline_writes_expected_output_and_is_idempotent(self):
        source = self.root / "book.txt"
        source.write_text("第一章 开始\n你好。\n第二章 结束\n再见。", encoding="utf-8")
        output = self.root / "output"
        pipeline = Pipeline(MockLLMProvider(), MockTTSProvider(), output)
        job_root = pipeline.generate(source)
        expected = {"source", "metadata.json", "chapters", "analysis", "scripts", "audio", "manifest.json", "podcast.mp3"}
        self.assertTrue(expected.issubset({p.name for p in job_root.iterdir()}))
        manifest_before = json.loads((job_root / "manifest.json").read_text())
        self.assertEqual(manifest_before["status"], "completed")
        chapter_mtime = (job_root / "analysis/0001.json").stat().st_mtime_ns
        self.assertEqual(pipeline.generate(source), job_root)
        self.assertEqual((job_root / "analysis/0001.json").stat().st_mtime_ns, chapter_mtime)
        self.assertEqual(job_status(job_root.name, output)["integrity"], "ok")

    def test_epub_runs_through_complete_pipeline(self):
        source = self.root / "pipeline.epub"
        book = epub.EpubBook()
        book.set_identifier("pipeline-id")
        book.set_title("Pipeline EPUB")
        book.set_language("en")
        chapter = epub.EpubHtml(title="Chapter One", file_name="chapter.xhtml", lang="en")
        chapter.set_content("<html><body><h1>Chapter One</h1><p>Pipeline text.</p></body></html>")
        book.add_item(chapter)
        book.spine = [chapter]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        epub.write_epub(str(source), book)
        job = Pipeline(MockLLMProvider(), MockTTSProvider(), self.root / "output").generate(source)
        self.assertTrue((job / "podcast.mp3").is_file())
        manifest = json.loads((job / "manifest.json").read_text())
        self.assertEqual(manifest["source_format"], "epub")
        self.assertEqual(manifest["status"], "completed")

    def test_resume_reuses_completed_steps_after_provider_failure(self):
        source = self.root / "book.txt"
        source.write_text("第一章 开始\n你好。\n第二章 结束\n再见。", encoding="utf-8")
        output = self.root / "output"
        failing = FailingTTS()
        with self.assertRaises(BookCastError):
            Pipeline(MockLLMProvider(), failing, output).generate(source)
        job = next(output.iterdir())
        failed = json.loads((job / "manifest.json").read_text())
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["steps"]["analysis:0001"]["status"], "completed")
        completed_mtime = (job / "analysis/0001.json").stat().st_mtime_ns
        recovered = Pipeline(MockLLMProvider(), MockTTSProvider(), output).generate(source, resume=True)
        self.assertEqual(recovered.resolve(), job.resolve())
        self.assertEqual((job / "analysis/0001.json").stat().st_mtime_ns, completed_mtime)
        self.assertEqual(job_status(job.name, output)["status"], "completed")

    def test_damaged_artifact_is_rebuilt_on_resume(self):
        source = self.root / "book.txt"
        source.write_text("第一章\n内容", encoding="utf-8")
        output = self.root / "output"
        pipeline = Pipeline(MockLLMProvider(), MockTTSProvider(), output)
        job = pipeline.generate(source)
        (job / "scripts/0001.json").write_text("corrupt", encoding="utf-8")
        self.assertEqual(job_status(job.name, output)["integrity"], "damaged")
        pipeline.generate(source, resume=True)
        self.assertEqual(job_status(job.name, output)["integrity"], "ok")

    def test_cli_generate_and_status(self):
        source = self.root / "cli.txt"
        source.write_text("第一章\n命令行测试", encoding="utf-8")
        output = self.root / "cli-output"
        runner = CliRunner()
        result = runner.invoke(app, ["generate", str(source), "--output-dir", str(output)])
        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertIn("任务完成", result.stdout)
        job = next(output.iterdir())
        result = runner.invoke(app, ["status", job.name, "--output-dir", str(output), "--json"])
        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["status"], "completed")


if __name__ == "__main__":
    unittest.main()

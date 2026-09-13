import json
from pathlib import Path
import tempfile
import unittest

from ebooklib import epub
import pymupdf

from bookcast.errors import BookCastError
from bookcast.models import BookMetadata, Chapter, ChapterAnalysis, PodcastScript
from bookcast.parsers import parse_book, parse_epub, parse_pdf, parse_txt
from bookcast.pipeline import Pipeline, job_status
from bookcast.providers import MockLLMProvider, MockTTSProvider
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

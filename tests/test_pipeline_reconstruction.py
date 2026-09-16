from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import kenkui as kk

from kenkui_server.jobs.models import JobSpec, OutputSpec, SingleVoiceCasting, TtsSettings
from kenkui_server.jobs.pipeline import pipeline_from_job


def write_epub(path: Path) -> Path:
    container = """<?xml version=\"1.0\"?>
<container xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\" version=\"1.0\">
 <rootfiles><rootfile full-path=\"OPS/package.opf\"
 media-type=\"application/oebps-package+xml\"/></rootfiles>
</container>"""
    package = """<?xml version=\"1.0\"?>
<package xmlns=\"http://www.idpf.org/2007/opf\" version=\"3.0\">
 <metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\"><dc:title>Tiny</dc:title><dc:creator>Ada</dc:creator></metadata>
 <manifest>
 <item id=\"one\" href=\"text/one.xhtml\" media-type=\"application/xhtml+xml\"/>
 <item id=\"two\" href=\"text/two.xhtml\" media-type=\"application/xhtml+xml\"/>
 </manifest>
 <spine><itemref idref=\"one\"/><itemref idref=\"two\"/></spine>
</package>"""
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OPS/package.opf", package)
        archive.writestr("OPS/text/one.xhtml", "<html><body><p>One</p></body></html>")
        archive.writestr("OPS/text/two.xhtml", "<html><body><p>Two</p></body></html>")
    return path


def test_job_spec_reconstructs_single_voice_pipeline(tmp_path: Path) -> None:
    source_path = write_epub(tmp_path / "book.epub")
    stable_chapter_id = kk.epub(source_path).inspect().chapters[0].id
    spec = JobSpec(
        source_id="asset-1",
        chapters=(stable_chapter_id,),
        casting=SingleVoiceCasting(voice_id="en_US-amy"),
        tts=TtsSettings(normalize_text=True),
        output=OutputSpec(path=str(tmp_path / "book.m4b")),
    )

    pipeline = pipeline_from_job(spec, source_path)

    assert [chapter.id for chapter in pipeline.inspect().chapters] == [stable_chapter_id]
    assert pipeline.validate().is_valid


def test_character_job_preserves_analysis_and_voice_assignments(tmp_path: Path) -> None:
    from kenkui._domain.operations import AssignVoices, AttributeQuotes, InferCharacters

    from kenkui_server.jobs.models import CharacterCasting

    source = write_epub(tmp_path / "cast.epub")
    chapter = kk.epub(source).inspect().chapters[0].id
    spec = JobSpec(
        source_id="asset-cast",
        chapters=(chapter,),
        casting=CharacterCasting(
            narrator_voice_id="vivienne", unknown_voice_id="rex",
            cast=(("speaker-a", "beatrix"),), method="gendered",
            model_id="openrouter/deepseek/deepseek-v4-flash",
        ),
        tts=TtsSettings(), output=OutputSpec(path=str(tmp_path / "cast.m4b")),
    )
    pipeline = pipeline_from_job(spec, source)
    analysis = [op for op in pipeline.operations if isinstance(
        op, (InferCharacters, AttributeQuotes, AssignVoices)
    )]
    assert analysis == [
        InferCharacters("openrouter/deepseek/deepseek-v4-flash", identity_model_id=None),
        AttributeQuotes("openrouter/deepseek/deepseek-v4-flash"),
        AssignVoices(narrator_voice_id="vivienne", unknown_voice_id="rex",
                     cast=(("speaker-a", "beatrix"),), method="gendered"),
    ]
    assert pipeline.validate().is_valid

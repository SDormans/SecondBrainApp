from pathlib import Path

import pytest

from conftest import make_pdf
from second_brain.capture.audio import Segment, Transcript
from second_brain.capture.pdf import PdfImportError
from second_brain.capture.service import CaptureService, UnsupportedFileType


def test_capture_pasted_text(vault):
    note = CaptureService(vault).capture_text(
        "First line title\n\nSome article text.", url="https://example.com/a"
    )
    saved = vault.read_note(note.path)
    assert saved.status == "inbox"
    assert saved.source_url == "https://example.com/a"
    assert saved.source_type == "url"
    assert "Some article text." in saved.body


def test_capture_bare_url_without_fetching(vault):
    note = CaptureService(vault, fetch_url_content=False).capture_text("https://example.com/x")
    assert note.source_url == "https://example.com/x"
    assert vault.read_note(note.path).status == "inbox"


def test_capture_bare_url_fetch_failure_is_graceful(vault, monkeypatch):
    import second_brain.capture.service as service

    def fail(url):
        raise OSError("offline")

    monkeypatch.setattr(service, "fetch_article", fail)
    note = CaptureService(vault).capture_text("https://example.com/y")
    assert "Could not fetch" in note.body


def test_import_pdf(vault, tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf", ["Hello PDF world", "Second page"], title="A Paper")
    [note] = CaptureService(vault).import_file(pdf)
    assert note.title == "A Paper"
    assert note.source_type == "pdf" and note.status == "inbox"
    assert "Hello PDF world" in note.body and "Second page" in note.body
    assert (vault.attachments_dir / "paper.pdf").exists() and pdf.exists()


def test_import_broken_pdf(vault, tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(PdfImportError):
        CaptureService(vault).import_pdf(bad)


class FakeTranscriber:
    def __init__(self):
        self.calls = 0

    def transcribe(self, path: Path, progress=None) -> Transcript:
        self.calls += 1
        if progress:
            progress(1.0)
        return Transcript(
            language="en",
            duration=65.0,
            segments=[Segment(0.0, 4.0, "Welcome to the show."), Segment(62.0, 65.0, "Goodbye.")],
        )


def test_import_audio_with_fake_transcriber(vault, tmp_path):
    audio = tmp_path / "episode.mp3"
    audio.write_bytes(b"ID3")
    transcriber = FakeTranscriber()
    service = CaptureService(vault, transcriber_factory=lambda: transcriber)
    [note] = service.import_file(audio)
    assert note.source_type == "audio" and note.status == "inbox"
    assert "Welcome to the show." in note.body and "01:02" in note.body
    service.import_file(audio)
    assert transcriber.calls == 2  # model loaded once, reused


def test_unsupported_file(vault, tmp_path):
    f = tmp_path / "x.exe"
    f.write_bytes(b"")
    with pytest.raises(UnsupportedFileType):
        CaptureService(vault).import_file(f)

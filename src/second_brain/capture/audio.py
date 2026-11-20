"""Transcribe audio/podcast files with faster-whisper (NVIDIA GPU by default)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..vault.note import Note, NoteStatus, SourceType

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".wav",
    ".flac",
    ".ogg",
    ".opus",
    ".aac",
    ".wma",
    ".webm",
    ".mp4",
    ".mkv",
}


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    segments: list[Segment] = field(default_factory=list)
    language: str | None = None
    duration: float | None = None

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()


class Transcriber(Protocol):
    def transcribe(
        self, path: Path, progress: Callable[[float], None] | None = None
    ) -> Transcript: ...


class WhisperTranscriber:
    """Lazy-loading faster-whisper wrapper. Falls back to CPU (int8) if CUDA is unavailable."""

    def __init__(
        self, model_size: str = "large-v3", device: str = "cuda", compute_type: str = "float16"
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            from faster_whisper import WhisperModel

            try:
                self._model = WhisperModel(
                    self.model_size, device=self.device, compute_type=self.compute_type
                )
            except (RuntimeError, ValueError) as exc:
                if self.device == "cpu":
                    raise
                log.warning("Whisper on %s failed (%s); falling back to CPU int8", self.device, exc)
                self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            return self._model

    def transcribe(self, path: Path, progress: Callable[[float], None] | None = None) -> Transcript:
        model = self._load()
        segments, info = model.transcribe(str(path), vad_filter=True, beam_size=5)
        transcript = Transcript(language=info.language, duration=info.duration)
        for seg in segments:  # generator: transcription happens while iterating
            transcript.segments.append(Segment(seg.start, seg.end, seg.text))
            if progress and info.duration:
                progress(min(seg.end / info.duration, 1.0))
        return transcript


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def note_from_transcript(path: Path, transcript: Transcript, attachment: str | None = None) -> Note:
    lines = []
    if attachment:
        lines += [f"Original file: `{attachment}`", ""]
    lines += ["## Transcript", ""]
    lines += [f"[{format_timestamp(s.start)}] {s.text.strip()}" for s in transcript.segments]
    extra: dict = {"language": transcript.language}
    if transcript.duration:
        extra["duration"] = format_timestamp(transcript.duration)
    if attachment:
        extra["attachment"] = attachment
    return Note.new(
        Path(path).stem.replace("_", " ").strip(),
        "\n".join(lines) + "\n",
        source_type=SourceType.AUDIO.value,
        status=NoteStatus.INBOX.value,
        extra=extra,
    )

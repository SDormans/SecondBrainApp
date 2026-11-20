from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import pytest

from second_brain.index.indexer import Indexer
from second_brain.index.store import IndexStore
from second_brain.vault.vault import Vault


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("SECOND_BRAIN_CONFIG_DIR", str(tmp_path / "config"))


@pytest.fixture
def vault(tmp_path) -> Vault:
    return Vault.open(tmp_path / "vault")


@pytest.fixture
def store(vault):
    s = IndexStore(vault.index_path)
    yield s
    s.close()


class FakeEmbedder:
    """Deterministic bag-of-words embedder: notes sharing words end up close together."""

    model = "fake-embed"
    dim = 64

    def __init__(self, up: bool = True):
        self.up = up
        self.calls = 0

    def available(self) -> bool:
        return self.up

    def embed(self, texts, kind="document"):
        from second_brain.ai.ollama import OllamaUnavailable

        if not self.up:
            raise OllamaUnavailable("fake embedder is down")
        self.calls += 1
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for word in re.findall(r"\w+", text.lower()):
                bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim
                vec[bucket] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def indexer(vault, store, embedder) -> Indexer:
    return Indexer(vault, store, embedder)


def make_pdf(path: Path, pages: list[str], title: str | None = None) -> Path:
    """Write a minimal valid PDF with one line of Helvetica text per page."""
    objects: list[bytes] = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    info_id = font_id + 1
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(f"<< /Title ({title or ''}) >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))
    return path

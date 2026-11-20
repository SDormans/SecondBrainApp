from datetime import datetime

from second_brain.vault.note import Note, NoteStatus, normalize_tags, parse_note, slugify

SAMPLE = """---
id: 20240101120000-abc123
title: Spaced repetition
source_type: url
source_url: https://example.com/srs
author: Piotr Wozniak
captured_at: 2024-01-01T12:00:00+00:00
tags: [learning, memory]
status: processed
custom_field: keep me
---

# Spaced repetition

Review at increasing intervals. See [[Forgetting curve]].
"""


def test_parse_all_fields():
    note = parse_note(SAMPLE)
    assert note.id == "20240101120000-abc123"
    assert note.title == "Spaced repetition"
    assert note.source_type == "url"
    assert note.source_url == "https://example.com/srs"
    assert note.author == "Piotr Wozniak"
    assert note.captured_at == "2024-01-01T12:00:00+00:00"
    assert note.tags == ["learning", "memory"]
    assert note.status == NoteStatus.PROCESSED
    assert note.extra == {"custom_field": "keep me"}
    assert note.body.startswith("# Spaced repetition")


def test_round_trip_is_stable():
    note = parse_note(SAMPLE)
    text = note.to_markdown()
    again = parse_note(text)
    assert again == note
    assert again.to_markdown() == text


def test_new_note_serialises_all_frontmatter_fields():
    note = Note.new("Hello", "Body")
    text = note.to_markdown()
    for field in (
        "id",
        "title",
        "source_type",
        "source_url",
        "author",
        "captured_at",
        "tags",
        "status",
    ):
        assert f"\n{field}:" in text
    assert parse_note(text).status == "inbox"


def test_no_frontmatter_uses_heading_and_path(tmp_path):
    path = tmp_path / "plain.md"
    note = parse_note("# From heading\n\ntext", path=path)
    assert note.title == "From heading"
    assert note.id.startswith("file-")
    assert parse_note("just text", path=path).title == "plain"


def test_invalid_yaml_is_kept_in_body():
    text = "---\ntitle: [unclosed\n---\nbody"
    note = parse_note(text)
    assert note.body == text
    assert "title: [unclosed" in note.to_markdown()


def test_crlf_and_bom():
    text = "\ufeff---\r\ntitle: Windows\r\ntags: a, b\r\n---\r\n\r\nline\r\n"
    note = parse_note(text)
    assert note.title == "Windows"
    assert note.tags == ["a", "b"]
    assert note.body == "line\r\n"


def test_yaml_datetime_and_unknown_status_are_normalised():
    note = parse_note("---\ncaptured_at: 2024-05-01 10:00:00\nstatus: weird\n---\nx")
    assert note.captured_at == datetime(2024, 5, 1, 10).isoformat() + "+00:00"
    assert note.status == "inbox"


def test_empty_frontmatter():
    note = parse_note("---\n---\nbody")
    assert note.body == "body"


def test_normalize_tags():
    assert normalize_tags("#a, b c ,a") == ["a", "b-c"]
    assert normalize_tags(None) == []
    assert normalize_tags(["x", "#y", "x"]) == ["x", "y"]


def test_slugify_is_windows_safe():
    assert slugify('What: is "this"?') == "what-is-this"
    assert slugify("CON") == "con-note"
    assert slugify("???") == "untitled"
    assert slugify("Ünïcödé title") == "unicode-title"

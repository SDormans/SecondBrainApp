from second_brain.capture import kindle
from second_brain.capture.service import CaptureService

CLIPPINGS = """\ufeffThe Pragmatic Programmer (Hunt, Andrew; Thomas, David)
- Your Highlight on page 12 | Location 180-182 | Added on Monday, 1 January 2024 10:00:00

Care about your craft.
==========
The Pragmatic Programmer (Hunt, Andrew; Thomas, David)
- Your Note on page 12 | Location 182 | Added on Monday, 1 January 2024 10:01:00

Applies to writing too.
==========
The Pragmatic Programmer (Hunt, Andrew; Thomas, David)
- Your Bookmark on page 20 | Location 300 | Added on Monday, 1 January 2024 10:02:00


==========
Deep Work (Cal Newport)
- Your Highlight at location 55-57 | Added on Tuesday, 2 January 2024 08:00:00

Clarity about what matters
==========
Deep Work (Cal Newport)
- Your Highlight at location 55-58 | Added on Tuesday, 2 January 2024 08:00:30

Clarity about what matters provides clarity about what does not.
==========
Untitled document without author
- Your Highlight on Location 10 | Added on Wednesday, 3 January 2024 09:00:00

A line
with two lines
==========
"""


def test_parse_clippings():
    clips = kindle.parse_clippings(CLIPPINGS.replace("\n", "\r\n"))
    assert len(clips) == 4  # bookmark dropped, extended highlight de-duplicated
    first = clips[0]
    assert first.book_title == "The Pragmatic Programmer"
    assert first.author == "Hunt, Andrew; Thomas, David"
    assert (first.kind, first.page, first.location) == ("highlight", "12", "180-182")
    assert first.added_on == "Monday, 1 January 2024 10:00:00"
    assert clips[1].kind == "note"
    assert clips[2].text.endswith("does not.")
    assert clips[2].location == "55-58"
    assert clips[3].author is None and clips[3].text == "A line\nwith two lines"


def test_parse_ignores_garbage():
    assert kindle.parse_clippings("") == []
    assert kindle.parse_clippings("==========\n\n==========\nonly one line\n") == []


def test_group_and_render():
    books = kindle.group_by_book(kindle.parse_clippings(CLIPPINGS))
    assert len(books) == 3
    note, added = kindle.merge_into_note(
        None, "Deep Work", "Cal Newport", books[("Deep Work", "Cal Newport")]
    )
    assert added == 1
    assert note.source_type == "kindle" and note.status == "inbox"
    assert note.author == "Cal Newport" and "kindle" in note.tags
    assert "> Clarity about what matters provides" in note.body
    assert note.id == kindle.book_note_id("Deep Work", "Cal Newport")


def test_import_creates_book_notes_and_reimport_is_idempotent(vault, tmp_path):
    src = tmp_path / "My Clippings.txt"
    src.write_text(CLIPPINGS, encoding="utf-8")
    service = CaptureService(vault)
    notes = service.import_kindle(src)
    assert sorted(n.title for n in notes) == [
        "Deep Work",
        "The Pragmatic Programmer",
        "Untitled document without author",
    ]
    assert len(list(vault.iter_note_paths())) == 3
    assert service.import_kindle(src) == []


def test_reimport_appends_only_new_and_keeps_user_edits(vault, tmp_path):
    src = tmp_path / "My Clippings.txt"
    src.write_text(CLIPPINGS, encoding="utf-8")
    service = CaptureService(vault)
    [deep] = [n for n in service.import_kindle(src) if n.title == "Deep Work"]
    edited = vault.read_note(deep.path)
    edited.body += "\nMy own thoughts.\n"
    vault.save_note(edited)
    src.write_text(
        CLIPPINGS
        + """Deep Work (Cal Newport)
- Your Highlight at location 99 | Added on Friday, 5 January 2024 08:00:00

Shallow work is inevitable.
==========
""",
        encoding="utf-8",
    )
    [updated] = service.import_kindle(src)
    body = vault.read_note(updated.path).body
    assert "My own thoughts." in body
    assert body.count("Clarity about what matters provides") == 1
    assert "Shallow work is inevitable." in body


def test_import_file_routes_clippings(vault, tmp_path):
    src = tmp_path / "my clippings.txt"
    src.write_text(CLIPPINGS, encoding="utf-8")
    assert len(CaptureService(vault).import_file(src)) == 3

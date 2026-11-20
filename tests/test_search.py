import pytest

from second_brain.search import SearchMode, SearchService, build_fts_query


@pytest.fixture
def seeded(vault, indexer):
    vault.create_note(
        "Photosynthesis", "Plants convert sunlight into chemical energy.", tags=["biology"]
    )
    vault.create_note(
        "Solar panels", "Photovoltaic cells convert sunlight to electricity.", tags=["energy"]
    )
    vault.create_note("Café culture", "Espresso is served in Parisian cafés.")
    vault.create_note("C++ tips", "Use RAII. Avoid raw new/delete; prefer std::unique_ptr.")
    indexer.rebuild_index()
    return vault


def titles(results):
    return [r.title for r in results]


def test_keyword_search_ranks_and_snippets(seeded, store):
    results = SearchService(store).keyword("sunlight")
    assert set(titles(results)) == {"Photosynthesis", "Solar panels"}
    assert all("[sunlight]" in r.snippet for r in results)


def test_title_matches_rank_first(seeded, store):
    assert titles(SearchService(store).keyword("photosynthesis energy"))[0] == "Photosynthesis"


def test_all_terms_required_by_default(seeded, store):
    service = SearchService(store)
    assert titles(service.keyword("sunlight electricity")) == ["Solar panels"]
    assert len(service.keyword("sunlight electricity", any_term=True)) == 2


def test_prefix_phrase_tags_and_diacritics(seeded, store):
    service = SearchService(store)
    assert set(titles(service.keyword("photo"))) == {"Photosynthesis", "Solar panels"}
    assert titles(service.keyword('"chemical energy"')) == ["Photosynthesis"]
    assert titles(service.keyword("biology")) == ["Photosynthesis"]
    assert titles(service.keyword("cafe")) == ["Café culture"]


@pytest.mark.parametrize(
    "query",
    [
        '"',
        "AND",
        "OR NOT",
        "c++",
        "std::unique_ptr",
        "*",
        "(",
        "title:foo",
        "new/delete",
        "NEAR(a b)",
    ],
)
def test_user_input_never_breaks_fts(seeded, store, query):
    SearchService(store).keyword(query)  # must not raise sqlite3.OperationalError


def test_special_characters_still_find_notes(seeded, store):
    assert titles(SearchService(store).keyword("unique_ptr")) == ["C++ tips"]


def test_build_fts_query():
    assert build_fts_query("") is None
    assert build_fts_query("  ") is None
    assert build_fts_query("hello world") == '"hello" "world"*'
    assert build_fts_query('"exact phrase"') == '"exact phrase"'
    assert build_fts_query('"exact phrase" x', any_term=True) == '"exact phrase" OR "x"*'
    assert build_fts_query("a AND (b") == '"a" "AND" "b"*'


def test_index_updates_are_searchable(vault, indexer, store):
    note = vault.create_note("Temp", "zebra")
    indexer.index_path(note.path)
    service = SearchService(store)
    assert titles(service.keyword("zebra")) == ["Temp"]
    vault.save_note(note.with_changes(body="giraffe"))
    indexer.index_path(note.path)
    assert service.keyword("zebra") == [] and titles(service.keyword("giraffe")) == ["Temp"]
    note.path.unlink()
    indexer.index_path(note.path)
    assert service.keyword("giraffe") == []


def test_semantic_search(seeded, store, embedder):
    outcome = SearchService(store, embedder).search("convert sunlight", SearchMode.SEMANTIC)
    assert outcome.mode == SearchMode.SEMANTIC and outcome.message is None
    assert set(titles(outcome.results[:2])) == {"Photosynthesis", "Solar panels"}


def test_semantic_degrades_to_keyword_when_ollama_down(seeded, store, embedder):
    embedder.up = False
    outcome = SearchService(store, embedder).search("sunlight", SearchMode.SEMANTIC)
    assert outcome.mode == SearchMode.KEYWORD
    assert "unavailable" in outcome.message
    assert len(outcome.results) == 2


def test_semantic_disabled_degrades(seeded, store):
    outcome = SearchService(store, None).search("sunlight", SearchMode.SEMANTIC)
    assert outcome.mode == SearchMode.KEYWORD and len(outcome.results) == 2

# SecondBrainApp

A local-first Windows 10/11 desktop app for learning and research. Everything you capture
(articles, PDFs, podcasts, Kindle highlights) becomes a plain markdown note in a folder you
own — the **vault** — with keyword and semantic search and optional local AI on top.

> Status: v1 scaffold. The core layers below are implemented and tested; the code is split so
> further features can be added module by module.

## Architecture

```
            ┌──────────────────────────── PySide6 UI (ui/) ─────────────────────────────┐
            │ Inbox / All notes / Search tabs · markdown editor · [[wiki-link]] completion │
            │ backlinks · settings · capture dialog · export zip · rebuild index           │
            └──────────────────────────────┬──────────────────────────────────────────────┘
                                           │ background TaskRunner (UI never blocks)
                                   ┌───────▼────────┐
                                   │  AppCore       │  core.py — wires everything per vault
                                   └─┬──────┬─────┬─┘
        ┌────────────────────────────┘      │     └──────────────────────────┐
┌───────▼────────┐  ┌───────────────────────▼──────────┐  ┌──────────────────▼──────────┐
│ capture/       │  │ index/ + search/                 │  │ ai/                          │
│ text/URL, PDF, │  │ SQLite: FTS5 (title, body, tags) │  │ Ollama client + embedder     │
│ audio (whisper)│  │ + sqlite-vec (embeddings)        │  │ summarize / tags / links     │
│ Kindle, drop   │  │ watcher keeps it in sync         │  │ cloud provider hook (off)    │
│ folder watcher │  │ rebuild_index() from vault       │  └──────────────────────────────┘
└───────┬────────┘  └───────────────────────▲──────────┘
        │ writes notes                       │ reads notes
┌───────▼────────────────────────────────────┴──────────┐
│ vault/  — SOURCE OF TRUTH: markdown + YAML frontmatter │
│ atomic writes (temp file + fsync + os.replace)         │
└────────────────────────────────────────────────────────┘
```

**Principles**

- **Files are the truth, the index is a cache.** `.secondbrain/index.db` can be deleted at any
  time; *Tools → Rebuild index* (or the CLI) reconstructs it from the markdown files.
- **No data loss.** Every write goes to a temp file in the same folder, is fsynced, then
  atomically renamed over the target (retried when Windows antivirus/indexer holds a lock). The
  editor detects external edits and saves a conflict copy instead of overwriting. Deleted notes
  go to `.secondbrain/trash/`.
- **Local first, degrade gracefully.** Ollama is optional: if it is not running, semantic search
  falls back to keyword search and AI actions show a clear message. Cloud AI is **off** by
  default and no cloud provider is shipped in v1 (`ai/providers.py` is the extension point).

### Vault layout

```
MyVault/
├── notes/                    # new notes (any *.md anywhere in the vault is indexed)
├── attachments/              # copies of imported PDFs / audio / clippings files
├── _import/                  # drop folder: files placed here are imported automatically
└── .secondbrain/             # app data, safe to delete (except trash you still need)
    ├── index.db              # SQLite: FTS5 + sqlite-vec
    ├── logs/secondbrain.log  # rotating log file
    ├── trash/                # deleted notes
    └── vault.json
```

### Note format

```markdown
---
id: 20240101120000-a1b2c3
title: Spaced repetition
source_type: url          # note | url | pdf | audio | kindle
source_url: https://example.com/srs
author: Piotr Wozniak
captured_at: '2024-01-01T12:00:00+00:00'
tags: [learning, memory]
status: inbox             # inbox | processed
---

Body in markdown. Link other notes with [[Note title]] or [[Note title|label]].
```

Unknown frontmatter keys are preserved; files without frontmatter are still indexed (title from
the first `# heading` or the filename).

### Package layout

| Module | Responsibility |
| --- | --- |
| `second_brain/vault/` | `Note` model + YAML frontmatter, atomic writes, vault folder, wiki-links, zip export |
| `second_brain/capture/` | Importers: pasted text/URL, PDF (pypdf), audio (faster-whisper), Kindle `My Clippings.txt`; drop-folder watcher |
| `second_brain/index/` | `IndexStore` (SQLite FTS5 + sqlite-vec), `Indexer` (`rebuild_index`, incremental sync), filesystem watcher |
| `second_brain/search/` | Keyword (FTS5, safe query building) and semantic search with fallback |
| `second_brain/ai/` | Ollama client/embedder, `Assistant` (summary, tag and link suggestions), provider selection |
| `second_brain/ui/` | PySide6 main window, editor, dialogs, background tasks; `theme.py` holds the light/dark design tokens (from the Lovable/shadcn "slate" palette) and the Qt stylesheet |
| `second_brain/core.py` | `AppCore`: owns one open vault and wires the layers together (UI-independent) |
| `second_brain/config.py` | Per-user settings in `%APPDATA%\SecondBrain\settings.json` |

## Setup (Windows 10/11)

Requirements: Python 3.11+ (64-bit, from python.org — its SQLite supports loadable extensions,
which sqlite-vec needs), optional Ollama, optional NVIDIA GPU with 8 GB+ VRAM for transcription.

```powershell
git clone <this repo> SecondBrainApp
cd SecondBrainApp
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
second-brain            # launches the GUI; pick or create a vault folder on first start
```

### Local AI (optional)

1. Install [Ollama](https://ollama.com) and keep it running (default `http://localhost:11434`).
2. Pull the default models:
   ```powershell
   ollama pull llama3.1:8b        # quantized 8B text model (summaries, tag/link suggestions)
   ollama pull nomic-embed-text   # embedding model for semantic search
   ```
3. "Local AI" and "Semantic search" are on by default (toggle them in *Settings*); run *Tools → Rebuild index*
   once to compute embeddings. Any other Ollama models can be configured in Settings.

### Transcription (optional)

Audio import uses faster-whisper (`large-v3`, `cuda`, `float16` by default). It needs an NVIDIA
GPU with 8 GB+ VRAM plus the CUDA 12 runtime libraries (cuBLAS + cuDNN 9), e.g.:

```powershell
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

The model is downloaded on first use. Without a GPU, set device `cpu` / compute type `int8` and
a smaller model (e.g. `small`) in Settings.

## Usage

- **Capture** (`Ctrl+Shift+N`): paste article text and/or a URL. A bare URL is fetched (can be
  disabled in Settings).
- **Import files**: PDF, audio (`.mp3 .m4a .wav .flac .ogg .opus .mp4 …`), `My Clippings.txt`,
  `.txt`/`.md` — via *File → Import* or by dropping files into the vault's `_import/` folder.
  Re-importing `My Clippings.txt` only appends highlights that are not yet in the book note.
- **Inbox**: new captures have `status: inbox`; mark them processed from the editor.
- **Search**: toggle keyword / semantic next to the search bar.
- **AI** (if enabled): *AI → Summarize / Suggest tags / Suggest links*.
- **Export vault as zip**: *File → Export vault as zip* (excludes the rebuildable index).
- **Rebuild index**: *Tools → Rebuild index* or in Settings; from the command line:
  ```powershell
  second-brain-cli rebuild-index --vault D:\MyVault [--no-embed]
  ```

## Development

```powershell
pytest          # unit tests (no Ollama, GPU or display needed)
ruff check .
ruff format .
```

## v1 non-goals

No mobile or web client, no multi-user support, no built-in cloud sync (use *Export vault as
zip* or put the vault in a folder you sync yourself), no Kindle notebook web scraping (only
`My Clippings.txt` file import).

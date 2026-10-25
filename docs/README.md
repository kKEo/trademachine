# Documentation

The documentation is one static landing page plus three GitBook-compatible books, each written for a different reader. The books are plain CommonMark: GitBook (Git Sync, one `.gitbook.yaml` per book) and mdBook (one `book.toml` per book) render the same files.

| Book | Reader | Start at |
|---|---|---|
| [User guide](guide/user/README.md) | anyone who wants to run the program and read its output | [Quick start](guide/user/quick-start.md) |
| [Operator guide](guide/operator/README.md) | people who run, configure or change the engine | [Architecture](guide/operator/internals/architecture.md), [Configuration reference](guide/operator/install/configuration.md) |
| [Tester guide](guide/tester/README.md) | people checking that a change is safe to merge | [Running the tests](guide/tester/start/running-the-tests.md) |

The landing page is `guide/index.html`; it links the three books and is copied to the root of the published site.

Design notes live in the repository root: `CLAUDE.md` holds the architecture rules and the roadmap; the operator guide's [Known gaps and roadmap](guide/operator/develop/known-gaps.md) tracks their status.

## Build, serve, check

```bash
make docs-check              # scripts/docs-check.py: names in the docs must exist in the code and tests; links must resolve
make docs                    # docs-check, then mdbook build of all three books into site/, plus the landing page
make docs-serve BOOK=user    # live preview of one book (user | operator | tester)
```

`mdbook` is pinned to v0.5.4 in `.github/workflows/docs.yml`, which publishes `site/` to GitHub Pages on pushes to the default branch. Install the same version locally for identical output (`cargo install mdbook --version 0.5.4`, or the release binary).

## Authoring rules

- One H1 on the first line of every page, a lead paragraph under it, no skipped heading levels.
- No YAML frontmatter and no GitBook-only tags, so mdBook renders the same thing. Callouts are blockquotes with a bold label (`> **Warning.**`).
- Relative links stay inside `docs/guide/`; files elsewhere in the repository are named in prose, not linked. Cross-book links use `../<book>/...`.
- Every page is listed in its book's `SUMMARY.md`; an unlisted page is not published.
- Images are SVG under each book's `images/` folder with a sentence-length alt text.

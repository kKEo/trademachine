# tradesignals developer and documentation targets.
# Run with the virtual environment active; CI uses the same targets.

PYTHON    ?= python3
MDBOOK    ?= mdbook
DOCS_ROOT := docs/guide
BOOKS     := user operator tester
BOOK      ?= user

.PHONY: help test lint format docs docs-check docs-serve clean

help:
	@echo "make test        run the test suite (pytest -q)"
	@echo "make lint        ruff check + ruff format --check"
	@echo "make format      ruff format + ruff check --fix"
	@echo "make docs-check  verify the docs against the code (scripts/docs-check.py)"
	@echo "make docs        docs-check, then build all books into site/ with mdBook"
	@echo "make docs-serve  serve one book live: make docs-serve BOOK=operator"

test:
	$(PYTHON) -m pytest -q

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .
	ruff check . --fix

docs-check:
	$(PYTHON) scripts/docs-check.py

docs: docs-check
	@for b in $(BOOKS); do $(MDBOOK) build $(DOCS_ROOT)/$$b || exit 1; done
	@mkdir -p site && cp $(DOCS_ROOT)/index.html site/index.html
	@echo "site/ built: index.html + $(BOOKS)"

docs-serve:
	$(MDBOOK) serve $(DOCS_ROOT)/$(BOOK) --open

clean:
	rm -rf site .pytest_cache .ruff_cache

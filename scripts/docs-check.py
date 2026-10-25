#!/usr/bin/env python3
"""Keep docs/guide honest against the code. Standard library only.

Checks (all findings are printed as path:line: message; exit 1 when any exist):
  - every environment variable the code reads (ALPACA_*, XTB_*, RISK_* string literals and
    os.environ.get / os.getenv / env_bool first arguments) is listed in .env.example and in the
    operator configuration reference, together with its literal default;
  - every CLI flag added in tradesignals/cli.py appears in the operator commands page;
  - every registry name (DATA_PROVIDERS, BROKERS, strategy `name`) appears in the commands page;
  - every `tests/<file>.py::<test>` cited anywhere in the docs exists;
  - each book has README.md, SUMMARY.md and .gitbook.yaml; every SUMMARY target exists; every
    page is listed; every page starts with a single H1 on line 1; relative links, images and
    anchors resolve inside docs/guide; image alt text is at least 12 characters.

Usage: python scripts/docs-check.py [--repo-root DIR] [--quiet]
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

BOOKS = ("user", "operator", "tester")
ENV_PREFIXES = ("ALPACA_", "XTB_", "RISK_")
LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SUMMARY_LINK_RE = re.compile(r"^\s*(?:[-*]\s+)?\[([^\]]+)\]\(([^)]+)\)\s*$")
TEST_REF_RE = re.compile(r"tests/(test_\w+\.py)::(test_\w+)")
TEST_FILE_RE = re.compile(r"tests/(\w+\.py)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


class Checker:
    def __init__(self, repo: Path):
        self.repo = repo
        self.docs = repo / "docs" / "guide"
        self.pkg = repo / "tradesignals"
        self.findings: list[str] = []

    # ------------------------------------------------------------ helpers
    def fail(self, path: Path, line: int | None, msg: str) -> None:
        rel = path.relative_to(self.repo) if path.is_absolute() else path
        loc = f"{rel}:{line}" if line else f"{rel}"
        self.findings.append(f"{loc}: {msg}")

    @staticmethod
    def strip_fences(lines: list[str]) -> list[tuple[int, str]]:
        out, fence = [], None
        for i, raw in enumerate(lines, 1):
            s = raw.rstrip("\n")
            m = re.match(r"^\s*(`{3,}|~{3,})", s)
            if m:
                tok = m.group(1)[0]
                if fence is None:
                    fence = tok
                    out.append((i, ""))
                    continue
                if fence == tok:
                    fence = None
                    out.append((i, ""))
                    continue
            out.append((i, "" if fence else s))
        return out

    @staticmethod
    def slugify(text: str) -> str:
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"[^\w\s-]", "", text.strip().lower())
        return re.sub(r"\s+", "-", text).strip("-")

    def headings(self, path: Path) -> list[tuple[int, int, str]]:
        hs = []
        for ln, text in self.strip_fences(path.read_text(encoding="utf-8").splitlines(True)):
            m = HEADING_RE.match(text)
            if m:
                hs.append((ln, len(m.group(1)), m.group(2)))
        return hs

    def page_text(self, rel: str) -> str:
        return (self.docs / rel).read_text(encoding="utf-8")

    # ------------------------------------------------------------ code facts
    @staticmethod
    def is_env_read(func: ast.expr) -> bool:
        """os.environ.get(...), environ.get(...), os.getenv(...), getenv(...), env_bool(...)."""
        if isinstance(func, ast.Name):
            return func.id in ("getenv", "env_bool")
        if isinstance(func, ast.Attribute):
            if func.attr == "env_bool":
                return True
            if func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os":
                return True
            if func.attr == "get":
                v = func.value
                return (isinstance(v, ast.Name) and v.id == "environ") or (
                    isinstance(v, ast.Attribute)
                    and v.attr == "environ"
                    and isinstance(v.value, ast.Name)
                    and v.value.id == "os"
                )
        return False

    def env_vars_in_code(self) -> dict[str, set[object]]:
        """name -> set of literal defaults seen (None when no default)."""
        found: dict[str, set[object]] = {}
        for py in sorted(self.pkg.rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value.startswith(ENV_PREFIXES)
                ):
                    if re.fullmatch(r"[A-Z][A-Z0-9_]+", node.value):
                        found.setdefault(node.value, set())
                if isinstance(node, ast.Call) and self.is_env_read(node.func):
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        name = node.args[0].value
                        default = (
                            node.args[1].value
                            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                            else None
                        )
                        found.setdefault(name, set()).add(default)
        return found

    def env_vars_in_example(self) -> dict[str, int]:
        out: dict[str, int] = {}
        example = self.repo / ".env.example"
        for ln, line in enumerate(example.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
            if m:
                out[m.group(1)] = ln
        return out

    def risk_defaults(self) -> dict[str, object]:
        tree = ast.parse((self.pkg / "risk.py").read_text(encoding="utf-8"))
        out: dict[str, object] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "RiskLimits":
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.AnnAssign)
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.target, ast.Name)
                    ):
                        out[stmt.target.id] = stmt.value.value
        return out

    def cli_flags(self) -> list[str]:
        tree = ast.parse((self.pkg / "cli.py").read_text(encoding="utf-8"))
        flags: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("--"):
                        flags.append(a.value)
        return flags

    def registry_names(self) -> dict[str, list[str]]:
        names: dict[str, list[str]] = {"DATA_PROVIDERS": [], "BROKERS": [], "strategies": []}
        tree = ast.parse((self.pkg / "providers" / "__init__.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            value = getattr(node, "value", None)
            for t in targets:
                if t in ("DATA_PROVIDERS", "BROKERS") and isinstance(value, ast.Dict):
                    names[t] = [k.value for k in value.keys if isinstance(k, ast.Constant)]
        for py in sorted((self.pkg / "strategies").glob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for stmt in node.body:
                        if (
                            isinstance(stmt, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == "name" for t in stmt.targets)
                            and isinstance(stmt.value, ast.Constant)
                            and stmt.value.value != "strategy"
                        ):
                            names["strategies"].append(stmt.value.value)
        return names

    # ------------------------------------------------------------ checks
    def check_env(self) -> None:
        config_page = self.docs / "operator" / "install" / "configuration.md"
        text = config_page.read_text(encoding="utf-8")
        in_code = self.env_vars_in_code()
        in_example = self.env_vars_in_example()
        for name in sorted(in_code):
            if name not in in_example:
                self.fail(self.repo / ".env.example", None, f"{name} is read by the code but missing here")
            if f"`{name}`" not in text:
                self.fail(config_page, None, f"{name} is read by the code but not documented")
            for default in in_code[name]:
                if default is None:
                    continue
                rendered = str(default).lower() if isinstance(default, bool) else str(default)
                if rendered not in text:
                    self.fail(config_page, None, f"default {rendered!r} of {name} not mentioned")
        for name, ln in sorted(in_example.items(), key=lambda kv: kv[1]):
            if name not in in_code:
                self.fail(self.repo / ".env.example", ln, f"{name} is not read anywhere in tradesignals/")
        for field, default in self.risk_defaults().items():
            rendered = str(int(default)) if isinstance(default, float) and float(default).is_integer() else str(default)
            if rendered not in text:
                self.fail(config_page, None, f"RiskLimits.{field} default {rendered} not mentioned")

    def check_cli(self) -> None:
        page = self.docs / "operator" / "install" / "commands.md"
        text = page.read_text(encoding="utf-8")
        for flag in self.cli_flags():
            if f"`{flag}`" not in text:
                self.fail(page, None, f"flag {flag} exists in cli.py but is not documented")
        for kind, names in self.registry_names().items():
            for n in names:
                if f"`{n}`" not in text:
                    self.fail(page, None, f"{kind} name '{n}' is not documented")

    def check_test_refs(self) -> None:
        for md in sorted(self.docs.rglob("*.md")):
            for ln, text in self.strip_fences(md.read_text(encoding="utf-8").splitlines(True)):
                for fname, tname in TEST_REF_RE.findall(text):
                    tf = self.repo / "tests" / fname
                    if not tf.exists():
                        self.fail(md, ln, f"{fname} does not exist")
                    elif not re.search(rf"^(?:async )?def {re.escape(tname)}\(", tf.read_text(encoding="utf-8"), re.M):
                        self.fail(md, ln, f"{fname} has no test named {tname}")
                for fname in TEST_FILE_RE.findall(text):
                    if not (self.repo / "tests" / fname).exists():
                        self.fail(md, ln, f"tests/{fname} does not exist")

    def check_books(self) -> None:
        if not (self.docs / "index.html").exists():
            self.fail(self.docs / "index.html", None, "landing page missing")
        for book in BOOKS:
            bdir = self.docs / book
            for required in ("README.md", "SUMMARY.md", ".gitbook.yaml"):
                if not (bdir / required).exists():
                    self.fail(bdir / required, None, "missing")
            summary = bdir / "SUMMARY.md"
            listed: set[str] = set()
            if summary.exists():
                for ln, text in self.strip_fences(summary.read_text(encoding="utf-8").splitlines(True)):
                    m = SUMMARY_LINK_RE.match(text)
                    if not m:
                        continue
                    target = m.group(2).split("#")[0]
                    if target in listed:
                        self.fail(summary, ln, f"{target} listed twice")
                    listed.add(target)
                    if not (bdir / target).exists():
                        self.fail(summary, ln, f"{target} does not exist")
            for md in sorted(bdir.rglob("*.md")):
                rel = md.relative_to(bdir).as_posix()
                if rel == "SUMMARY.md":
                    continue
                if rel not in listed:
                    self.fail(md, None, "not listed in SUMMARY.md, so it is never published")
                self.check_page(md)

    def check_page(self, md: Path) -> None:
        raw = md.read_text(encoding="utf-8").splitlines(True)
        if not raw or not raw[0].startswith("# "):
            self.fail(md, 1, "first line must be the page's single H1")
        hs = self.headings(md)
        if sum(1 for _, lvl, _ in hs if lvl == 1) > 1:
            self.fail(md, None, "more than one H1")
        prev = 1
        for ln, lvl, text in hs:
            if lvl > prev + 1:
                self.fail(md, ln, f"heading level jumps to H{lvl}: {text}")
            prev = lvl
        own_slugs = {self.slugify(t) for _, _, t in hs}
        for ln, text in self.strip_fences(raw):
            for bang, alt, target in LINK_RE.findall(text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                if target.startswith("#"):
                    if self.slugify(target[1:]) not in own_slugs:
                        self.fail(md, ln, f"anchor {target} has no heading on this page")
                    continue
                path, _, anchor = target.partition("#")
                dest = (md.parent / path).resolve()
                if not dest.exists():
                    self.fail(md, ln, f"{'image' if bang else 'link'} target {target} does not exist")
                    continue
                try:
                    dest.relative_to(self.docs.resolve())
                except ValueError:
                    self.fail(md, ln, f"link {target} leaves docs/guide; name repository files in prose instead")
                    continue
                if bang and len(alt.strip()) < 12:
                    self.fail(md, ln, f"image {path} needs a descriptive alt text")
                if anchor and dest.suffix == ".md":
                    if self.slugify(anchor) not in {self.slugify(t) for _, _, t in self.headings(dest)}:
                        self.fail(md, ln, f"anchor #{anchor} not found in {path}")

    def run(self) -> int:
        if not self.docs.is_dir():
            print(f"docs root {self.docs} not found", file=sys.stderr)
            return 2
        self.check_env()
        self.check_cli()
        self.check_test_refs()
        self.check_books()
        return 1 if self.findings else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--quiet", action="store_true", help="print findings only")
    args = ap.parse_args(argv)
    checker = Checker(Path(args.repo_root).resolve())
    code = checker.run()
    for f in checker.findings:
        print(f)
    if not args.quiet:
        print(f"docs-check: {len(checker.findings)} finding(s)" if checker.findings else "docs-check: ok")
    return code


if __name__ == "__main__":
    sys.exit(main())

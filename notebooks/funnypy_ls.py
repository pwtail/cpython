#!/usr/bin/env python3
"""Funny Python language server.

Runs on the Funny Python interpreter (the CPython 3.16 fork), so the standard
library already understands the funnypy syntax extensions:

  * multiline lambdas:            ``fib = def(n): ...``
  * pattern destructuring:        ``match {'x': x} = d``
  * pipe / placeholder / match:   ``[1,2] ..map(f)``, ``run(..) def(): ...``,
                                  ``x ..match: case 1: ...``

Because the server uses the *same* parser as the kernel, a diagnostic here is
byte-for-byte what the kernel will report when the cell runs.

Features
--------

* ``textDocument/publishDiagnostics`` — ``compile`` in ``exec`` mode.
* ``textDocument/documentSymbol``       — functions/classes/variables from AST.
* ``textDocument/foldingRange``         — compound-statement blocks from AST.
* ``textDocument/semanticTokens/full``  — funnypy-specific constructs
  (``def(``, ``..``, soft keyword ``match``).

Note: jupyterlab-lsp currently renders only diagnostics and document symbols
(it has no semantic-token or folding-range UI); the other two features are for
clients such as VS Code or Neovim.

Wire it into jupyter-lsp with::

    "argv": ["<funnypy-venv>/bin/python", "<path>/funnypy_ls.py"]

Protocol transport is stdio JSON-RPC (``start_io``).  All logging goes to
stderr so stdout stays clean for the protocol.

Self-test (no LSP client needed)::

    python funnypy_ls.py --selftest
"""
from __future__ import annotations

import ast
import io
import logging
import sys
import tokenize

from lsprotocol import types
from pygls.lsp.server import LanguageServer

SERVER_NAME = "funnypy-language-server"
SERVER_VERSION = "0.2.0"

log = logging.getLogger(SERVER_NAME)
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

server = LanguageServer(
    SERVER_NAME,
    SERVER_VERSION,
    text_document_sync_kind=types.TextDocumentSyncKind.Incremental,
)

_PARSE_ERRORS = (SyntaxError, ValueError, OverflowError, MemoryError, RecursionError)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def mask_ipython_magics(source: str) -> str:
    """Turn whole-line IPython magics / shell escapes into comments.

    ``%matplotlib inline``, ``%%time`` and ``!pip install ...`` are valid in a
    notebook cell but not in Python.  Replacing the leading magic character
    with ``#`` keeps every line's length and line number identical, so error
    positions reported on the *masked* source map 1:1 onto the original.
    """
    out = []
    for line in source.splitlines(keepends=True):
        stripped = line.lstrip(" \t")
        if stripped[:1] in ("%", "!"):
            i = line.index(stripped[0])
            line = line[:i] + "#" + line[i + 1:]
        out.append(line)
    return "".join(out)


def _utf16_col(line: str, cp_col: int) -> int:
    """Convert a 0-based codepoint column into UTF-16 code units (LSP default)."""
    if cp_col <= 0:
        return 0
    if cp_col >= len(line):
        return len(line.encode("utf-16-le")) // 2
    return len(line[:cp_col].encode("utf-16-le")) // 2


def _position(lines: list[str], lineno: int, offset: int) -> types.Position:
    """Map CPython's 1-based ``lineno``/``offset`` to a 0-based LSP position."""
    line_idx = max(0, lineno - 1)
    text = lines[line_idx] if line_idx < len(lines) else ""
    cp_col = max(0, (offset or 1) - 1)
    return types.Position(line=line_idx, character=_utf16_col(text, cp_col))


def _diagnostic_range(lines: list[str], e: SyntaxError) -> types.Range:
    """Build an LSP range for a ``SyntaxError``.

    ``offset`` is 1-based inclusive (first offending char), ``end_offset`` is
    1-based exclusive (one past the last offending char).  When CPython leaves
    ``end_offset`` at 0 (e.g. an unclosed bracket), underline to end of line.
    """
    start = _position(lines, e.lineno, e.offset)
    end_lineno = e.end_lineno
    end_offset = e.end_offset
    if (
        end_lineno
        and end_offset
        and (end_lineno, end_offset) > (e.lineno, e.offset or 1)
    ):
        end = _position(lines, end_lineno, end_offset)
        if end.line < start.line or (
            end.line == start.line and end.character <= start.character
        ):
            end = types.Position(line=start.line, character=start.character + 1)
    else:
        end_text = lines[start.line] if start.line < len(lines) else ""
        end_char = _utf16_col(end_text, len(end_text))
        if end_char <= start.character:
            end_char = start.character + 1
        end = types.Position(line=start.line, character=end_char)
    return types.Range(start=start, end=end)


def compute_diagnostics(source: str) -> list[types.Diagnostic]:
    """Return diagnostics for one notebook cell / document.

    Uses ``compile`` in ``exec`` mode, exactly like ``exec``-based cell
    execution: it catches not only parser errors but also symbol-table errors
    the fork adds (e.g. an unresolved ``..`` placeholder), duplicate
    arguments, ``return`` outside a function, and so on.
    """
    lines = source.splitlines()
    try:
        compile(mask_ipython_magics(source), "<cell>", "exec")
    except SyntaxError as e:
        return [
            types.Diagnostic(
                range=_diagnostic_range(lines, e),
                message=e.msg or "invalid syntax",
                severity=types.DiagnosticSeverity.Error,
                source="funnypy",
            )
        ]
    except (ValueError, OverflowError, MemoryError):
        # NUL bytes, recursion limit, ... — not user-facing syntax errors.
        return []
    return []


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(mask_ipython_magics(source))
    except _PARSE_ERRORS:
        return None


def _ast_position(lines: list[str], lineno: int, col_offset: int) -> types.Position:
    """AST positions: 1-based line, 0-based *UTF-8 byte* column."""
    line_idx = max(0, lineno - 1)
    text = lines[line_idx] if line_idx < len(lines) else ""
    prefix = text.encode("utf-8")[:col_offset].decode("utf-8", errors="replace")
    return types.Position(line=line_idx, character=_utf16_col(text, len(prefix)))


def _ast_range(lines: list[str], node: ast.AST) -> types.Range:
    return types.Range(
        start=_ast_position(lines, node.lineno, node.col_offset),
        end=_ast_position(lines, node.end_lineno, node.end_col_offset),
    )


# ---------------------------------------------------------------------------
# Document symbols
# ---------------------------------------------------------------------------

def _name_selection(lines: list[str], node: ast.AST, name: str) -> types.Range:
    """Range of the identifier ``name`` on the node's first line."""
    line_idx = node.lineno - 1
    if line_idx >= len(lines):
        return _ast_range(lines, node)
    text = lines[line_idx]
    prefix = text.encode("utf-8")[:node.col_offset].decode("utf-8", errors="replace")
    idx = text.find(name, len(prefix))
    if idx < 0:
        return _ast_range(lines, node)
    return types.Range(
        start=types.Position(line=line_idx, character=_utf16_col(text, idx)),
        end=types.Position(line=line_idx, character=_utf16_col(text, idx + len(name))),
    )


def _make_symbol(name, kind, node, lines, children) -> types.DocumentSymbol:
    return types.DocumentSymbol(
        name=name,
        kind=kind,
        range=_ast_range(lines, node),
        selection_range=_name_selection(lines, node, name),
        children=children,
    )


def _collect_symbols(node: ast.AST, lines: list[str], collect_vars: bool):
    symbols: list[types.DocumentSymbol] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # `return def(x): ...` / `def(x): ...` desugar to a synthetic
            # `<lambda>` FunctionDef — not a name worth showing in an outline.
            if not child.name.isidentifier():
                continue
            kind = (
                types.SymbolKind.Class
                if isinstance(child, ast.ClassDef)
                else types.SymbolKind.Function
            )
            symbols.append(
                _make_symbol(
                    child.name, kind, child, lines,
                    _collect_symbols(child, lines, collect_vars=False),
                )
            )
        elif collect_vars and isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    symbols.append(
                        _make_symbol(
                            target.id, types.SymbolKind.Variable, target, lines, []
                        )
                    )
            symbols.extend(_collect_symbols(child, lines, collect_vars))
        else:
            symbols.extend(_collect_symbols(child, lines, collect_vars))
    return symbols


def compute_document_symbols(source: str) -> list[types.DocumentSymbol]:
    tree = _parse(source)
    if tree is None:
        return []
    return _collect_symbols(tree, source.splitlines(), collect_vars=True)


# ---------------------------------------------------------------------------
# Folding ranges
# ---------------------------------------------------------------------------

_FOLDABLE_NODES: tuple[type, ...] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
)
if hasattr(ast, "TryStar"):  # Python 3.11+
    _FOLDABLE_NODES += (ast.TryStar,)
if hasattr(ast, "Match"):  # Python 3.10+
    _FOLDABLE_NODES += (ast.Match,)


def compute_folding_ranges(source: str) -> list[types.FoldingRange]:
    tree = _parse(source)
    if tree is None:
        return []
    ranges: list[types.FoldingRange] = []
    seen: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _FOLDABLE_NODES):
            continue
        start = node.lineno - 1
        end = (node.end_lineno or node.lineno) - 1
        if end > start and (start, end) not in seen:
            seen.add((start, end))
            ranges.append(
                types.FoldingRange(
                    start_line=start,
                    end_line=end,
                    kind=types.FoldingRangeKind.Region,
                )
            )
    ranges.sort(key=lambda r: (r.start_line, r.end_line))
    return ranges


# ---------------------------------------------------------------------------
# Semantic tokens
# ---------------------------------------------------------------------------

_SEMANTIC_TOKEN_TYPES = ["keyword", "operator"]
_KEYWORD = 0
_OPERATOR = 1

_SEMANTIC_LEGEND = types.SemanticTokensLegend(
    token_types=_SEMANTIC_TOKEN_TYPES,
    token_modifiers=[],
)


def _logical_lines(tokens) -> list[list[tokenize.TokenInfo]]:
    """Split a token stream at logical-newline boundaries (statement granularity)."""
    lines: list[list[tokenize.TokenInfo]] = []
    current: list[tokenize.TokenInfo] = []
    for tok in tokens:
        if tok.type in (
            tokenize.NL, tokenize.COMMENT, tokenize.ENCODING,
            tokenize.INDENT, tokenize.DEDENT,
        ):
            continue
        if tok.type == tokenize.NEWLINE:
            if current:
                lines.append(current)
                current = []
            continue
        if tok.type == tokenize.ENDMARKER:
            break
        current.append(tok)
    if current:
        lines.append(current)
    return lines


def _is_adjacent_dots(a: tokenize.TokenInfo, b: tokenize.TokenInfo) -> bool:
    return (
        a.type == tokenize.OP and a.string == "."
        and b.type == tokenize.OP and b.string == "."
        and a.end == b.start
    )


def _pattern_assign_keyword(line: list[tokenize.TokenInfo]) -> bool:
    """Does this logical line look like `match <pattern> = <expr>`?

    Distinguishes funnypy's soft-keyword form from ``match = 1``,
    ``match.x = 1``, ``match: int = 1`` and a plain ``match x:`` statement.
    """
    if len(line) < 2:
        return False
    if line[0].type != tokenize.NAME or line[0].string != "match":
        return False
    if line[1].type == tokenize.OP and line[1].string in ("=", ".", ":"):
        return False
    depth = 0
    seen = 0
    for tok in line[1:]:
        if tok.type == tokenize.OP:
            if tok.string in "([{":
                depth += 1
            elif tok.string in ")]}":
                depth -= 1
            elif tok.string == "=" and depth == 0:
                return seen > 0
            elif tok.string == ":" and depth == 0:
                return False
        seen += 1
    return False


def compute_semantic_tokens(source: str) -> types.SemanticTokens:
    """Highlight funnypy-specific constructs.

    CodeMirror's Python mode already knows hard keywords; what it cannot know
    is the funnypy dialect.  Emitted here:

    * ``def`` immediately followed by ``(``  -> keyword
    * ``..`` (two adjacent dots)             -> operator
    * soft-keyword ``match`` in ``match p = v`` / ``..match:`` -> keyword
    """
    masked = mask_ipython_magics(source)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(masked).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return types.SemanticTokens(data=[])

    raw: list[tuple[int, int, int, int]] = []  # (line0, char0, length, type)

    for line in _logical_lines(tokens):
        i = 0
        while i < len(line):
            tok = line[i]
            nxt = line[i + 1] if i + 1 < len(line) else None

            # `def(` — funnypy multiline lambda (a plain `def foo(` is not).
            if (
                tok.type == tokenize.NAME and tok.string == "def"
                and nxt is not None and nxt.type == tokenize.OP and nxt.string == "("
            ):
                raw.append((tok.start[0] - 1, tok.start[1], 3, _KEYWORD))
                i += 1
                continue

            # `..` — pipe stage / placeholder / pipe-match.
            if nxt is not None and _is_adjacent_dots(tok, nxt):
                raw.append((tok.start[0] - 1, tok.start[1], 2, _OPERATOR))
                i += 2
                continue

            # `match` as a funnypy soft keyword.
            if tok.type == tokenize.NAME and tok.string == "match":
                pipe_match = i >= 2 and _is_adjacent_dots(line[i - 2], line[i - 1])
                if pipe_match or _pattern_assign_keyword(line):
                    raw.append((tok.start[0] - 1, tok.start[1], 5, _KEYWORD))
                i += 1
                continue

            i += 1

    if not raw:
        return types.SemanticTokens(data=[])

    raw.sort(key=lambda t: (t[0], t[1]))
    data: list[int] = []
    prev_line = 0
    prev_char = 0
    for line0, char0, length, token_type in raw:
        if line0 == prev_line:
            delta_line = 0
            delta_char = char0 - prev_char
        else:
            delta_line = line0 - prev_line
            delta_char = char0
        data.extend([delta_line, delta_char, length, token_type, 0])
        prev_line, prev_char = line0, char0
    return types.SemanticTokens(data=data)


# ---------------------------------------------------------------------------
# LSP handlers
# ---------------------------------------------------------------------------

def _get_source(uri: str) -> str | None:
    try:
        return server.workspace.get_text_document(uri).source
    except Exception:  # noqa: BLE001 - document not in store (race on close)
        return None


def _publish(uri: str) -> None:
    source = _get_source(uri)
    if source is None:
        return
    server.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=compute_diagnostics(source))
    )


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(params: types.DidOpenTextDocumentParams) -> None:
    _publish(params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(params: types.DidChangeTextDocumentParams) -> None:
    _publish(params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
def did_close(params: types.DidCloseTextDocumentParams) -> None:
    server.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=[])
    )


@server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbol(params: types.DocumentSymbolParams) -> list[types.DocumentSymbol]:
    source = _get_source(params.text_document.uri)
    return compute_document_symbols(source) if source is not None else []


@server.feature(types.TEXT_DOCUMENT_FOLDING_RANGE)
def folding_range(params: types.FoldingRangeParams) -> list[types.FoldingRange]:
    source = _get_source(params.text_document.uri)
    return compute_folding_ranges(source) if source is not None else []


@server.feature(types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL, _SEMANTIC_LEGEND)
def semantic_tokens_full(params: types.SemanticTokensParams) -> types.SemanticTokens:
    source = _get_source(params.text_document.uri)
    if source is None:
        return types.SemanticTokens(data=[])
    return compute_semantic_tokens(source)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

_STRUCTURE_DEMO = (
    "square = def(x):\n"
    "    return x ** 2\n"
    "\n"
    "class Point:\n"
    "    def __init__(self, x):\n"
    "        self.x = x\n"
    "\n"
    "match {'a': a} = data\n"
    "\n"
    "total = 0\n"
    "\n"
    "[1, 2, 3]\n"
    "..map(square)\n"
    "..filter(lambda v: v > 1)\n"
)


def _decode_tokens(data: list[int]):
    out = []
    line = char = 0
    for i in range(0, len(data), 5):
        delta_line, delta_char, length, token_type, _mods = data[i:i + 5]
        line += delta_line
        char = char + delta_char if delta_line == 0 else delta_char
        out.append((line, char, length, _SEMANTIC_TOKEN_TYPES[token_type]))
    return out


def _selftest() -> int:
    diag_cases = {
        "lambda ok": "fib = def(n):\n    return n\n",
        "pattern ok": "match {'x': x} = d\n",
        "pipe ok": "square = def(x): return x ** 2\n[1, 2, 3]\n..map(square)\n",
        "unclosed paren": "x = (1 +\n",
        "indent error": "def f():\nreturn 1\n",
        "ipython magic": "%matplotlib inline\nprint(1)\n",
    }
    for name, src in diag_cases.items():
        diags = compute_diagnostics(src)
        print(f"[{name}] -> {len(diags)} diagnostic(s)")
        for d in diags:
            r = d.range
            print(
                f"    {d.message!r} @ "
                f"{r.start.line}:{r.start.character}-{r.end.line}:{r.end.character}"
            )

    print("\n--- structure demo ---")
    print("symbols:", [
        (s.name, s.kind.name) for s in compute_document_symbols(_STRUCTURE_DEMO)
    ])
    print("folds:  ", [
        (f.start_line, f.end_line) for f in compute_folding_ranges(_STRUCTURE_DEMO)
    ])
    print("tokens: ", _decode_tokens(compute_semantic_tokens(_STRUCTURE_DEMO).data))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    server.start_io()

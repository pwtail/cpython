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

Known limits: IPython shell escapes embedded in an expression (``x = !ls``)
are not masked, and a cell magic whose body is Python (``%%time``) keeps its
body diagnosable whereas foreign ones (``%%bash``) mask the whole cell.

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
import re
import sys
import tokenize

from lsprotocol import types
from pygls.lsp.server import LanguageServer

SERVER_NAME = "funnypy-language-server"
SERVER_VERSION = "0.3.0"

log = logging.getLogger(SERVER_NAME)
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

server = LanguageServer(
    SERVER_NAME,
    SERVER_VERSION,
    text_document_sync_kind=types.TextDocumentSyncKind.Incremental,
)

_PARSE_ERRORS = (SyntaxError, ValueError, OverflowError, MemoryError, RecursionError)

# The line model shared by the tokenizer, CPython positions and the LSP:
# only LF, CR and CRLF end a line (unlike `str.splitlines()`, which also
# splits on \v, \f, \x1c-\x1e, \x85, U+2028 and U+2029).
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_LINE_BREAK_CAPTURE = re.compile(r"(\r\n|\r|\n)")


def _split_lines(source: str) -> list[str]:
    return _LINE_BREAK.split(source)


# ---------------------------------------------------------------------------
# Position encoding
# ---------------------------------------------------------------------------

# Set from the client's negotiated `positionEncoding`; LSP's default is UTF-16.
_POSITION_ENCODING = "utf-16"


def _sync_encoding() -> None:
    """Pick up the position encoding negotiated during initialize."""
    global _POSITION_ENCODING
    try:
        raw = server.workspace.position_encoding
    except Exception:  # noqa: BLE001 - workspace not created before initialize
        return
    value = getattr(raw, "value", raw)
    if isinstance(value, str):
        _POSITION_ENCODING = value


def _encoded_col(line: str, cp_col: int) -> int:
    """Convert a 0-based codepoint column to the client's position encoding."""
    if cp_col <= 0:
        return 0
    if cp_col > len(line):
        cp_col = len(line)
    prefix = line[:cp_col]
    if _POSITION_ENCODING == "utf-8":
        return len(prefix.encode("utf-8"))
    if _POSITION_ENCODING == "utf-32":
        return len(prefix)
    return len(prefix.encode("utf-16-le")) // 2


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

# Cell magics whose body is not Python at all.
_FOREIGN_CELL_MAGICS = frozenset({
    "bash", "sh", "script", "html", "javascript", "js", "latex", "markdown",
    "md", "perl", "ruby", "sql", "cmd", "powershell", "svg",
})


def _neutralize(line: str) -> str:
    """Replace a foreign line with a same-length no-op (`pass` + padding)."""
    indent_len = len(line) - len(line.lstrip(" \t"))
    indent, body = line[:indent_len], line[indent_len:]
    if len(body) >= 4:
        return indent + "pass" + "#" * (len(body) - 4)
    return indent + "#" * len(body)


def mask_ipython_magics(source: str) -> str:
    """Neutralise IPython magics so the cell parses as the kernel runs it.

    ``%time f()``, ``!pip install x`` and ``%%bash`` blocks are valid in a
    notebook cell but not in Python.  Every such line is rewritten in place to
    a no-op statement of *exactly* the same length (``pass`` padded with
    ``#``), so line numbers and columns of everything else are preserved.
    A cell magic known to contain foreign code masks the rest of the cell;
    other ``%%`` magics (``%%time``, ...) keep their Python body diagnosable.
    """
    parts = _LINE_BREAK_CAPTURE.split(source)  # [text, sep, text, sep, ..., text]
    out: list[str] = []
    foreign_cell = False
    for index, part in enumerate(parts):
        if index % 2:  # a line separator
            out.append(part)
            continue
        stripped = part.lstrip(" \t")
        if foreign_cell:
            out.append(_neutralize(part))
        elif stripped.startswith("%%"):
            name = stripped[2:].split(None, 1)[0] if stripped[2:].strip() else ""
            foreign_cell = name in _FOREIGN_CELL_MAGICS
            out.append(_neutralize(part))
        elif stripped[:1] in ("%", "!"):
            out.append(_neutralize(part))
        else:
            out.append(part)
    return "".join(out)


def _position(lines: list[str], lineno: int, offset: int) -> types.Position:
    """Map CPython's 1-based ``lineno``/``offset`` to a 0-based LSP position."""
    line_idx = max(0, (lineno or 1) - 1)
    text = lines[line_idx] if line_idx < len(lines) else ""
    cp_col = max(0, (offset or 1) - 1)
    return types.Position(line=line_idx, character=_encoded_col(text, cp_col))


def _diagnostic_range(lines: list[str], e: SyntaxError) -> types.Range:
    """Build an LSP range for a ``SyntaxError``.

    ``offset`` is 1-based inclusive (first offending char), ``end_offset`` is
    1-based exclusive (one past the last offending char).  When CPython leaves
    ``end_offset`` at 0 (e.g. an unclosed bracket), underline to end of line.
    """
    if e.lineno is None or e.offset is None:
        # e.g. "source code string cannot contain null bytes" — no position.
        return types.Range(
            start=types.Position(line=0, character=0),
            end=types.Position(line=0, character=1),
        )
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
        end_char = _encoded_col(end_text, len(end_text))
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
    lines = _split_lines(source)
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
    except (ValueError, OverflowError, MemoryError, RecursionError):
        # Recursion limit, ... — not user-facing syntax errors.
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
    return types.Position(line=line_idx, character=_encoded_col(text, len(prefix)))


def _ast_range(lines: list[str], node: ast.AST) -> types.Range:
    return types.Range(
        start=_ast_position(lines, node.lineno, node.col_offset),
        end=_ast_position(lines, node.end_lineno, node.end_col_offset),
    )


# ---------------------------------------------------------------------------
# Document symbols
# ---------------------------------------------------------------------------

def _name_selection(lines: list[str], node: ast.AST, name: str) -> types.Range:
    """Range of the identifier ``name`` on the node's first line.

    Falls back to the whole node when the source spelling does not match the
    AST name (identifiers are NFKC-normalised, so ``ﬁle = file`` has the name
    ``file``): the selection range must stay inside the symbol range.
    """
    node_range = _ast_range(lines, node)
    line_idx = node.lineno - 1
    if line_idx >= len(lines):
        return node_range
    text = lines[line_idx]
    prefix = text.encode("utf-8")[:node.col_offset].decode("utf-8", errors="replace")
    idx = text.find(name, len(prefix))
    if idx < 0:
        return node_range
    candidate = types.Range(
        start=types.Position(line=line_idx, character=_encoded_col(text, idx)),
        end=types.Position(line=line_idx, character=_encoded_col(text, idx + len(name))),
    )
    if (
        (candidate.start.line, candidate.start.character)
        < (node_range.start.line, node_range.start.character)
        or (candidate.end.line, candidate.end.character)
        > (node_range.end.line, node_range.end.character)
    ):
        return node_range
    return candidate


def _make_symbol(name, kind, node, lines, children) -> types.DocumentSymbol:
    return types.DocumentSymbol(
        name=name,
        kind=kind,
        range=_ast_range(lines, node),
        selection_range=_name_selection(lines, node, name),
        children=children,
    )


def _collect_symbols(root: ast.AST, lines: list[str], collect_vars: bool):
    """Collect symbols iteratively: expression trees can be far too deep to walk recursively."""
    symbols: list[types.DocumentSymbol] = []
    stack: list[tuple[ast.AST, list, bool]] = [
        (child, symbols, collect_vars)
        for child in reversed(list(ast.iter_child_nodes(root)))
    ]
    while stack:
        node, out, vars_here = stack.pop()
        target_out, child_vars = out, vars_here
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # `return def(x): ...` / `def(x): ...` desugar to a synthetic
            # `<lambda>` FunctionDef — not a name worth showing in an outline.
            if node.name.isidentifier():
                kind = (
                    types.SymbolKind.Class
                    if isinstance(node, ast.ClassDef)
                    else types.SymbolKind.Function
                )
                children: list[types.DocumentSymbol] = []
                out.append(_make_symbol(node.name, kind, node, lines, children))
                target_out, child_vars = children, False
        elif vars_here and isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.append(
                        _make_symbol(
                            target.id, types.SymbolKind.Variable, target, lines, []
                        )
                    )
        stack.extend(
            (child, target_out, child_vars)
            for child in reversed(list(ast.iter_child_nodes(node)))
        )
    return symbols


def compute_document_symbols(source: str) -> list[types.DocumentSymbol]:
    tree = _parse(source)
    if tree is None:
        return []
    return _collect_symbols(tree, _split_lines(source), collect_vars=True)


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
    ``match.x = 1``, ``match: int = 1``, a plain ``match x:`` statement and
    ordinary tuple un/repacking such as ``match, x = 1, 2``.
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
            elif tok.string in (",", ";") and depth == 0:
                # A bare tuple target list is ordinary unpacking, not a pattern.
                return False
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
    masked_lines = _split_lines(masked)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(masked).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return types.SemanticTokens(data=[])

    raw: list[tuple[int, int, int, int]] = []  # (line0, char0, length, type)

    def emit(tok, length, token_type):
        line0 = tok.start[0] - 1
        text = masked_lines[line0] if line0 < len(masked_lines) else ""
        raw.append((line0, _encoded_col(text, tok.start[1]), length, token_type))

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
                emit(tok, len(tok.string), _KEYWORD)
                i += 1
                continue

            # `..` — pipe stage / placeholder / pipe-match.
            if nxt is not None and _is_adjacent_dots(tok, nxt):
                emit(tok, 2, _OPERATOR)
                i += 2
                continue

            # `match` as a funnypy soft keyword.
            if tok.type == tokenize.NAME and tok.string == "match":
                pipe_match = i >= 2 and _is_adjacent_dots(line[i - 2], line[i - 1])
                if pipe_match or _pattern_assign_keyword(line):
                    emit(tok, len(tok.string), _KEYWORD)
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
    _sync_encoding()
    _publish(params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(params: types.DidChangeTextDocumentParams) -> None:
    _sync_encoding()
    _publish(params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
def did_close(params: types.DidCloseTextDocumentParams) -> None:
    server.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=[])
    )


@server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbol(params: types.DocumentSymbolParams) -> list[types.DocumentSymbol]:
    _sync_encoding()
    source = _get_source(params.text_document.uri)
    return compute_document_symbols(source) if source is not None else []


@server.feature(types.TEXT_DOCUMENT_FOLDING_RANGE)
def folding_range(params: types.FoldingRangeParams) -> list[types.FoldingRange]:
    _sync_encoding()
    source = _get_source(params.text_document.uri)
    return compute_folding_ranges(source) if source is not None else []


@server.feature(types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL, _SEMANTIC_LEGEND)
def semantic_tokens_full(params: types.SemanticTokensParams) -> types.SemanticTokens:
    _sync_encoding()
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

    print("\n--- edge cases ---")
    print("nul byte:", [
        (d.message, d.range.start.line, d.range.start.character)
        for d in compute_diagnostics("x = 1\u0000\n")
    ])
    deep = "x = " + "+".join(["1"] * 2000) + "\n"
    print("deep expr symbols:", [s.name for s in compute_document_symbols(deep)])
    print("form feed:", [
        (d.message, d.range.start.line, d.range.start.character, d.range.end.character)
        for d in compute_diagnostics("x = 1\x0cy = (1 +\n")
    ])
    print("magic in block:", compute_diagnostics("if True:\n    %time x = 1\n"))
    print("cell magic:", compute_diagnostics("%%bash\necho hi\n"))
    print("magic body kept:", [
        (d.message, d.range.start.line)
        for d in compute_diagnostics("%%time\nx = (1 +\n")
    ])
    print("match tuple:", _decode_tokens(compute_semantic_tokens("match, x = 1, 2\n").data))
    print("non-ascii tok:", _decode_tokens(compute_semantic_tokens("𝕏 = def(x): return x\n").data))
    print("nfkc sel:", [
        (s.name, (s.range.start.character, s.range.end.character),
         (s.selection_range.start.character, s.selection_range.end.character))
        for s in compute_document_symbols("ﬁle = file\n")
    ])
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    server.start_io()

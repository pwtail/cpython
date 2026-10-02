#!/usr/bin/env python3
"""Funny Python language server.

Runs on the Funny Python interpreter (the CPython 3.16 fork), so the standard
library already understands the funnypy syntax extensions:

  * multiline lambdas:            ``fib = def(n): ...``
  * pattern destructuring:        ``match d case {'x': x}``
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
* ``textDocument/hover``               — signatures of names defined in the cell,
  plus short help for funnypy-only constructs.
* ``textDocument/completion``          — keywords, builtins and names bound in the cell.

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
import builtins
import io
import keyword
import logging
import re
import sys
import tokenize

from lsprotocol import types
from pygls.lsp.server import LanguageServer

SERVER_NAME = "funnypy-language-server"
SERVER_VERSION = "0.4.0"

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
    """Does this logical line look like `match <value> case <pattern>`?

    Distinguishes funnypy's soft-keyword form from ``match = 1``,
    ``match.x = 1``, ``match: int = 1``, a plain ``match x:`` statement and
    ordinary tuple un/repacking such as ``match, x = 1, 2``.
    """
    if len(line) < 3:
        return False
    if line[0].type != tokenize.NAME or line[0].string != "match":
        return False
    if line[1].type == tokenize.OP and line[1].string in ("=", ".", ":"):
        return False
    depth = 0
    for i, tok in enumerate(line[1:], start=1):
        if tok.type == tokenize.OP:
            if tok.string in "([{":
                depth += 1
            elif tok.string in ")]}":
                depth -= 1
            elif tok.string == ":" and depth == 0:
                return False
        if tok.type == tokenize.NAME and tok.string == "case" and depth == 0:
            # A value before `case` and a pattern after it.
            return 1 < i < len(line) - 1
    return False


_CONSTRUCT_LAMBDA = "lambda"
_CONSTRUCT_PIPE = "pipe"
_CONSTRUCT_MATCH = "match"

# Short help shown when hovering over a funnypy-only construct.
_CONSTRUCT_DOCS = {
    _CONSTRUCT_LAMBDA: (
        "**Funny Python:** multiline lambda\n\n"
        "`def(parameters):` followed by a full suite — an anonymous function with "
        "the semantics of `def` (closures, `yield` turns it into a generator, the "
        "usual parameter forms). Allowed as an assignment RHS, a `return` value or "
        "an expression statement.\n\n"
        "```python\n"
        "fib = def(n):\n"
        "    a, b = 0, 1\n"
        "    for _ in range(n):\n"
        "        yield a\n"
        "        a, b = b, a + b\n"
        "```"
    ),
    _CONSTRUCT_PIPE: (
        "**Funny Python:** pipeline operator `..`\n\n"
        "* `value` followed by `..f(args)` on the next line passes `value` as the "
        "**last** positional argument: `[1, 2] ..map(f)` is `map(f, [1, 2])`.\n"
        "* `..` inside a call is a placeholder filled by the next `def(...)` or "
        "expression: `run(..) def(): ...`.\n"
        "* `x ..match:` is `match x:`.\n\n"
        "```python\n"
        "[1, 2, 3]\n"
        "..map(square)\n"
        "..filter(is_even)\n"
        "```"
    ),
    _CONSTRUCT_MATCH: (
        "**Funny Python:** pattern destructuring\n\n"
        "`match <value> case <pattern>` binds the pattern, or raises `MatchError` when "
        "it does not match. Any pattern accepted by `match..case` works (mapping, "
        "sequence, class, or, as, capture, literal, wildcard).\n\n"
        "```python\n"
        "match point case {'x': x}\n"
        "```"
    ),
}


def find_funnypy_constructs(source: str) -> list[tuple[int, int, int, str]]:
    """Locate funnypy-only constructs as ``(line0, start_col, end_col, kind)``.

    Columns are 0-based codepoints.  Single source of truth for semantic tokens
    and for hover help.
    """
    masked = mask_ipython_magics(source)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(masked).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return []

    found: list[tuple[int, int, int, str]] = []
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
                found.append(
                    (tok.start[0] - 1, tok.start[1], tok.start[1] + 3, _CONSTRUCT_LAMBDA)
                )
                i += 1
                continue

            # `..` — pipe stage / placeholder / pipe-match.
            if nxt is not None and _is_adjacent_dots(tok, nxt):
                found.append(
                    (tok.start[0] - 1, tok.start[1], tok.start[1] + 2, _CONSTRUCT_PIPE)
                )
                i += 2
                continue

            # `match` as a funnypy soft keyword.
            if tok.type == tokenize.NAME and tok.string == "match":
                pipe_match = i >= 2 and _is_adjacent_dots(line[i - 2], line[i - 1])
                if pipe_match or _pattern_assign_keyword(line):
                    found.append(
                        (tok.start[0] - 1, tok.start[1], tok.start[1] + 5, _CONSTRUCT_MATCH)
                    )
                i += 1
                continue

            i += 1
    return found


_CONSTRUCT_TOKEN_TYPE = {
    _CONSTRUCT_LAMBDA: _KEYWORD,
    _CONSTRUCT_MATCH: _KEYWORD,
    _CONSTRUCT_PIPE: _OPERATOR,
}


def compute_semantic_tokens(source: str) -> types.SemanticTokens:
    """Highlight funnypy-specific constructs (see :func:`find_funnypy_constructs`)."""
    masked_lines = _split_lines(mask_ipython_magics(source))
    raw: list[tuple[int, int, int, int]] = []  # (line0, char0, length, type)
    for line0, start, end, kind in find_funnypy_constructs(source):
        text = masked_lines[line0] if line0 < len(masked_lines) else ""
        raw.append(
            (line0, _encoded_col(text, start), end - start, _CONSTRUCT_TOKEN_TYPE[kind])
        )

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
# Hover
# ---------------------------------------------------------------------------

def _format_arg(arg: ast.arg) -> str:
    text = arg.arg
    if arg.annotation is not None:
        text += ": " + ast.unparse(arg.annotation)
    return text


def _signature(node) -> str:
    """Render ``def name(...) -> ret`` for a function definition."""
    args = node.args
    parts: list[str] = []
    positional = [*args.posonlyargs, *args.args]
    defaults = list(args.defaults)
    first_default = len(positional) - len(defaults)
    for index, arg in enumerate(positional):
        text = _format_arg(arg)
        if index >= first_default:
            text += "=" + ast.unparse(defaults[index - first_default])
        parts.append(text)
    if args.posonlyargs:
        parts.insert(len(args.posonlyargs), "/")
    if args.vararg is not None:
        parts.append("*" + _format_arg(args.vararg))
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        text = _format_arg(arg)
        if default is not None:
            text += "=" + ast.unparse(default)
        parts.append(text)
    if args.kwarg is not None:
        parts.append("**" + _format_arg(args.kwarg))
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({', '.join(parts)}){returns}"


def _definition_hover(node) -> str | None:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _signature(node)
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        value = node.value
        if value is None:
            return None
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        text = f"{', '.join(ast.unparse(t) for t in targets)} = {ast.unparse(value)}"
        return text if len(text) <= 100 else text[:97] + "..."
    return None


def _definitions(source: str) -> dict[str, ast.AST]:
    """Map every name bound in the cell to its defining node (first wins)."""
    tree = _parse(source)
    if tree is None:
        return {}
    found: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.isidentifier():
                found.setdefault(node.name, node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for inner in ast.walk(target):
                    if isinstance(inner, ast.Name):
                        found.setdefault(inner.id, node)
    return found


def _token_at(source: str, line0: int, cp_col: int):
    """The token covering a 0-based codepoint position, or None."""
    target = (line0 + 1, cp_col)
    try:
        tokens = tokenize.generate_tokens(
            io.StringIO(mask_ipython_magics(source)).readline
        )
        for tok in tokens:
            if tok.type in (tokenize.ENDMARKER, tokenize.ENCODING):
                continue
            if tok.start <= target <= tok.end:
                return tok
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return None
    return None


def compute_hover(source: str, line0: int, cp_col: int) -> types.Hover | None:
    """Help for a funnypy construct, or the signature of a bound name."""
    for c_line, start, end, kind in find_funnypy_constructs(source):
        if c_line == line0 and start <= cp_col < end:
            return types.Hover(
                contents=types.MarkupContent(
                    kind=types.MarkupKind.Markdown, value=_CONSTRUCT_DOCS[kind]
                )
            )
    tok = _token_at(source, line0, cp_col)
    if tok is None or tok.type != tokenize.NAME:
        return None
    node = _definitions(source).get(tok.string)
    if node is None:
        return None
    detail = _definition_hover(node)
    if detail is None:
        return None
    return types.Hover(
        contents=types.MarkupContent(
            kind=types.MarkupKind.Markdown, value=f"```python\n{detail}\n```"
        )
    )


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def _add_target_names(target, add) -> None:
    for node in ast.walk(target):
        if isinstance(node, ast.Name):
            add(node.id, types.CompletionItemKind.Variable)


def _document_names(source: str) -> dict[str, tuple[types.CompletionItemKind, str | None]]:
    """Names bound anywhere in the cell, with a detail string where useful."""
    tree = _parse(source)
    if tree is None:
        return {}
    names: dict[str, tuple[types.CompletionItemKind, str | None]] = {}

    def add(name: str, kind, detail: str | None = None) -> None:
        if name:
            names.setdefault(name, (kind, detail))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, types.CompletionItemKind.Function, _signature(node))
        elif isinstance(node, ast.ClassDef):
            add(node.name, types.CompletionItemKind.Class, f"class {node.name}")
        elif isinstance(node, ast.arguments):
            for arg in (*node.posonlyargs, *node.args, *node.kwonlyargs):
                add(arg.arg, types.CompletionItemKind.Variable)
            if node.vararg is not None:
                add(node.vararg.arg, types.CompletionItemKind.Variable)
            if node.kwarg is not None:
                add(node.kwarg.arg, types.CompletionItemKind.Variable)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _add_target_names(target, add)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            _add_target_names(node.target, add)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            _add_target_names(node.target, add)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                _add_target_names(node.optional_vars, add)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                add(node.name, types.CompletionItemKind.Variable)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                add(name, types.CompletionItemKind.Variable)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.asname or alias.name.split(".")[0],
                    types.CompletionItemKind.Module)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    add(alias.asname or alias.name, types.CompletionItemKind.Variable)
    return names


def compute_completions(source: str, line0: int, cp_col: int):
    """Keywords, builtins and names bound in the cell.

    Attribute access (``obj.|``) is left to the kernel, which knows real types.
    """
    tok = _token_at(source, line0, cp_col)
    if tok is not None and tok.type in (tokenize.STRING, tokenize.COMMENT):
        return []
    lines = _split_lines(source)
    text = lines[line0] if line0 < len(lines) else ""
    if cp_col > 0 and text[cp_col - 1:cp_col] == ".":
        return []

    items: dict[str, types.CompletionItem] = {}
    for name, (kind, detail) in _document_names(source).items():
        items[name] = types.CompletionItem(label=name, kind=kind, detail=detail)
    for name in (*keyword.kwlist, *getattr(keyword, "softkwlist", ())):
        items.setdefault(
            name, types.CompletionItem(label=name, kind=types.CompletionItemKind.Keyword)
        )
    for name in dir(builtins):
        if name.startswith("_"):
            continue
        kind = (
            types.CompletionItemKind.Function
            if callable(getattr(builtins, name, None))
            else types.CompletionItemKind.Variable
        )
        items.setdefault(name, types.CompletionItem(label=name, kind=kind))
    return sorted(items.values(), key=lambda item: item.label)


# ---------------------------------------------------------------------------
# LSP handlers
# ---------------------------------------------------------------------------

def _get_doc(uri: str):
    try:
        return server.workspace.get_text_document(uri)
    except Exception:  # noqa: BLE001 - document not in store (race on close)
        return None


def _get_source(uri: str) -> str | None:
    doc = _get_doc(uri)
    return doc.source if doc is not None else None


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


@server.feature(types.TEXT_DOCUMENT_HOVER)
def hover(params: types.HoverParams) -> types.Hover | None:
    _sync_encoding()
    doc = _get_doc(params.text_document.uri)
    if doc is None:
        return None
    try:
        position = doc.position_from_client_units(params.position)
    except Exception:  # noqa: BLE001
        return None
    return compute_hover(doc.source, position.line, position.character)


@server.feature(
    types.TEXT_DOCUMENT_COMPLETION,
    types.CompletionOptions(trigger_characters=["."]),
)
def completion(params: types.CompletionParams) -> list[types.CompletionItem]:
    _sync_encoding()
    doc = _get_doc(params.text_document.uri)
    if doc is None:
        return []
    try:
        position = doc.position_from_client_units(params.position)
    except Exception:  # noqa: BLE001
        return []
    return compute_completions(doc.source, position.line, position.character)


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
    "match data case {'a': a}\n"
    "\n"
    "total = 0\n"
    "\n"
    "[1, 2, 3]\n"
    "..map(square)\n"
    "..filter(lambda v: v > 1)\n"
)


def _hover_text(hover) -> str | None:
    if hover is None:
        return None
    return hover.contents.value


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
        "pattern ok": "match d case {'x': x}\n",
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

    print("\n--- hover / completion ---")
    hover_src = (
        "square = def(n: int):\n"
        "    return n * n\n"
        "\n"
        "def area(w: int) -> int:\n"
        "    return w * w\n"
        "\n"
        "class Point:\n"
        "    pass\n"
        "\n"
        "result = square(3)\n"
    )
    print("hover square:", _hover_text(compute_hover(hover_src, 9, 10)))
    print("hover result:", _hover_text(compute_hover(hover_src, 9, 2)))
    print("hover Point: ", _hover_text(compute_hover(hover_src, 6, 8)))
    print("hover area:  ", _hover_text(compute_hover(hover_src, 3, 5)))
    print("hover def(  :", (_hover_text(compute_hover(hover_src, 0, 9)) or "").splitlines()[0])
    print("hover ..    :", (
        _hover_text(compute_hover("x\n..match:\n    case 1:\n        pass\n", 1, 1)) or ""
    ).splitlines()[0])
    print("hover blank :", compute_hover(hover_src, 2, 0))

    names = {i.label: i.kind.name for i in compute_completions(hover_src, 9, 10)}
    print("completions :", {
        k: names.get(k) for k in ("square", "area", "Point", "result", "n", "w", "match")
    })
    print("completion detail:", next(
        i.detail for i in compute_completions(hover_src, 9, 10) if i.label == "area"
    ))
    print("in string   :", compute_completions("s = 'print'", 0, 6))
    print("after dot   :", compute_completions("obj.x", 0, 4))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    server.start_io()

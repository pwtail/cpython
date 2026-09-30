#!/usr/bin/env python3
"""Funny Python language server — Phase 1: diagnostics.

Runs on the Funny Python interpreter (the CPython 3.16 fork), so `compile`
(parse + symtable + codegen) already understands the funnypy syntax extensions:

  * multiline lambdas:            ``fib = def(n): ...``
  * pattern destructuring:        ``match {'x': x} = d``
  * pipe / placeholder / match:   ``[1,2] ..map(f)``, ``run(..) def(): ...``,
                                  ``x ..match: case 1: ...``

Because the server uses the *same* parser as the kernel, a diagnostic here is
byte-for-byte what the kernel will report when the cell runs.

Wire it into jupyter-lsp with (see Phase 4)::

    "argv": ["<funnypy-venv>/bin/python", "<path>/funnypy_ls.py"]

Protocol transport is stdio JSON-RPC (``start_io``).  All logging goes to
stderr so stdout stays clean for the protocol.

Self-test (no LSP client needed)::

    python funnypy_ls.py --selftest
"""
from __future__ import annotations

import logging
import sys

from lsprotocol import types
from pygls.lsp.server import LanguageServer

SERVER_NAME = "funnypy-language-server"
SERVER_VERSION = "0.1.0"

log = logging.getLogger(SERVER_NAME)
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

server = LanguageServer(
    SERVER_NAME,
    SERVER_VERSION,
    text_document_sync_kind=types.TextDocumentSyncKind.Incremental,
)


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
    ``end_offset`` at 0 (e.g. unclosed bracket), underline to end of line.
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
# LSP handlers
# ---------------------------------------------------------------------------

def _publish(uri: str) -> None:
    try:
        doc = server.workspace.get_text_document(uri)
    except Exception:  # noqa: BLE001 - document not in store (race on close)
        return
    server.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=compute_diagnostics(doc.source))
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


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest() -> int:
    cases = {
        "lambda ok": "fib = def(n):\n    return n\n",
        "pattern ok": "match {'x': x} = d\n",
        "pipe ok": "square = def(x): return x ** 2\n[1, 2, 3]\n..map(square)\n",
        "unclosed paren": "x = (1 +\n",
        "indent error": "def f():\nreturn 1\n",
        "ipython magic": "%matplotlib inline\nprint(1)\n",
    }
    for name, src in cases.items():
        diags = compute_diagnostics(src)
        print(f"[{name}] -> {len(diags)} diagnostic(s)")
        for d in diags:
            r = d.range
            print(
                f"    {d.message!r} @ "
                f"{r.start.line}:{r.start.character}-{r.end.line}:{r.end.character}"
            )
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    server.start_io()

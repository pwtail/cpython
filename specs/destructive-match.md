---
id: funny-python-destructive-match
type: module-design
status: draft
title: Деструктивный match
parent: funny-python-architecture
---

## Responsibility

Любая форма `match`, у которой ни один кейс не совпал, бросает `MatchError`.
Покрывает требование R8 из `funny-python-goal` и является вторым (после R5)
документированным исключением из суперсета R1.

## Syntax

Синтаксис форм не меняется — меняется только исход при отсутствии совпадения:

```python
match x:
    case 1:
        ...
# x != 1 -> MatchError (было — молчаливое проваливание)

y = match x:
    case 1:
        "one"
# x != 1 -> MatchError (было — None)
```

## Semantics

- `match`-statement: нет совпадения (включая провал guard'а у trailing
  `case _ if cond`) → `MatchError`.
- `match`-выражение (R7): нет совпадения → `MatchError` (было `None`).
- `match v case PATTERN` (R3): деструктивен изначально — без изменений.
- Trailing unguarded `case _` (или иррефутабельный `case x:`) — catch-all:
  диспатч полный, ошибки нет.
- `..match:` (R4) — обычный compound `match`, покрыт как statement.

## Boundary

- **Механизм — codegen** (`Python/codegen.c`), AST и грамматику не трогает:
  - `codegen_match_inner` (statement): метка `no_match`; провал guard'а у
    trailing default → `no_match`; после тела default — `JUMP end`; в конце
    `no_match: codegen_raise_match_error(...)`.
  - `codegen_match_expr` (R7): `no_match` → `codegen_raise_match_error(...)`
    вместо `LOAD_CONST None`.
  - `codegen_raise_match_error`: `LOAD_GLOBAL`/`LOAD_NAME MatchError` +
    `PUSH_NULL` + `CALL 0` + `RAISE_VARARGS 1` (= `raise MatchError()`).
- **Имя резолвится вручную.** У `MatchError` нет узла в AST, поэтому нет записи
  в symtable, а `codegen_nameop` требует её (debug-assert). Опкод выбирается
  по `_PyST_IsFunctionLike`: function-like scope → `LOAD_GLOBAL` (oparg со
  сдвигом на 1 — NULL-флаг вызова), иначе `LOAD_NAME`. Оставшийся на стеке
  subject не мешает — raise разматывает кадр.
- **Почему не грамматический десахаринг** (синтетический `case _: raise`):
  он попал бы в `ast.parse`/`unparse` каждого неисчерпывающего match и сломал
  бы AST-интроспекцию. Codegen-уровень держит AST чистым — как R5.
- **Адаптация stdlib:** каждый неисчерпывающий `match` в `Lib/`, `Tools/`,
  `Parser/` получил явный `case _: pass` — поведение fall-through сохранено
  (аналог explicit-`return` адаптации R5). ~300 вставок в 20 файлах;
  инструмент — `.thinkrail/context/adapt_match.py` (AST-based, идемпотентный).
- **Адаптированные тесты:** `test_patma.TestTracing.test_no_default` (ждёт
  `MatchError` вместо fall-through), `test_funnypy.MatchExprTests`
  (no-match → `MatchError`).

## Инвариант

Каждый путь, достигающий `end` match-выражения, кладёт ровно одно значение;
путь «ничего не совпало» завершается raise'ом `MatchError`.

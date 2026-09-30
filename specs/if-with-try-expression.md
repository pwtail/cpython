---
id: funny-python-if-with-try-expr
type: module-design
status: draft
title: "`if`/`with`/`try` как выражения"
parent: funny-python-architecture
---

## Responsibility

`if`, `with` и `try` в позиции выражения возвращают значение последнего
выражения выбранной ветки. `for` и `while` выражениями **не** становятся
(намеренно: циклы — statement-конструкции, значения не производят). Покрывает
требование R11 из `funny-python-goal` и расширяет filler оператора `..` из
`funny-python-pipeline` до многострочных блок-выражений.

## Syntax

```python
y = if x > 0:          # if-выражение: последнее выражение ветки
    x
else:
    -x

z = with open(path) as fh:   # with-выражение
    fh.read()

r = try:                     # try-выражение
    do_work()
except ValueError:
    "bad"
else:
    "ok"

..myfunc(..) try:            # filler оператора `..` — блок-выражение
    'smth'                   # (стадия пайплайна: значение-вход — строкой выше)
except:
    'other'
```

Разрешённые позиции (v1, подтверждено для match-expr и переносится сюда):

- правая часть присваивания (одиночная или множественная цель, атрибут,
  индекс) — через statement-уровневые правила `if_expr_stmt` /
  `with_expr_stmt` / `try_expr_stmt`;
- filler оператора `..` (в `pipe_stage` и в hole-вызове `f(..) …`);
- выражение-инструкция (значение отбрасывается);
- `r =` на отдельной строке + блок ниже (`funnypy_assign_stmt`,
  `funny-python-line-continuation`).

НЕ доступно в `return`, аргументах вызовов и прочих произвольных
expression-позициях: блок съедает финальный `NEWLINE`/`DEDENT`, и
statement-уровневые контексты потребовали бы отдельных правил — ровно та же
причина, что у `funny-python-match-expression`.

## Semantics

- **Значение блока** — последнее выражение блока; если блок не заканчивается
  выражением (присваивание, `pass`, `return`, пусто) или заканчивается `...`
  — `None`. Та же логика, что у R5 и match-expr: переиспользуется один
  codegen-хелпер `codegen_block_value` (обобщение `codegen_match_expr_body`).
- **Вложенный блок — это statement.** `if`/`with`/`try` в теле ветки (на
  позиции statement) разбираются как свои statement-формы и значения не дают:
  `r = if a:` + вложенный `if b: …` вернёт `None`, если `a` истинно — ровно
  как R5 (`def f(): if …` возвращает `None`). Чтобы вложить блок-выражение
  внутрь блок-выражения, значение проносят через присваивание. Это следствие
  грамматики: в `statement` compound-формы идут раньше expression-форм.
- **if** — значение взятой ветки (`then`/`elif`/`else`). `elif` десахаривается
  в вложенный `IfExpr`, обёрнутый `Expr`-стейтментом в `orelse` (тот же
  приём, что у statement-`If`). Нет ветки `else` и ни одна не взята — `None`.
- **with** — значение тела. `as`-цели связывают имена в текущей области.
- **try** — значение тела `try` (если нет `else`), тела `else` (если `else`
  есть и исключения не было), или тела сработавшего `except`-обработчика.
  `finally` выполняется ради побочных эффектов, **значение не даёт**: его
  хвостовое выражение отбрасывается, результат берётся из `try`/`else`/`except`.
- **Scope** — новый scope не создаётся (как у match-expr); имена из `as`
  (`with ... as`, `except ... as`) связываются в текущей области.
- **`async with` и `except*`** — вне скоупа v1 (деферы, см. Boundary).

## Boundary

- **Новые AST-узлы** `IfExpr(expr test, stmt* body, stmt* orelse)`,
  `WithExpr(withitem* items, stmt* body)`,
  `TryExpr(stmt* body, excepthandler* handlers, stmt* orelse, stmt* finalbody)`
  в `Parser/Python.asdl` (категория `expr`; поля — те же, что у statement-узлов
  `If`/`With`/`Try`; генерация `Python/Python-ast.c`,
  `Include/internal/pycore_ast*.h` через `make regen-ast`).
- **Грамматика (`Grammar/python.gram`):** правила `if_expr`/`elif_expr`
  (переиспользуют `block` и `else_block`), `with_expr` (переиспользует
  `with_item`), `try_expr` (переиспользует `except_block`, `else_block`,
  `finally_block`) — как альтернативы `expression` (рядом с `match_expr`).
  Клаузы (`elif`/`else`/`except`/`finally`) — **сиблинги на уровне
  statement'а**: дедентятся после тела блока, как в statement-аналогах
  (`try_stmt`/`if_stmt`), а НЕ вкладываются в `INDENT…DEDENT` как `case`
  у `match_expr`. Statement-уровневые правила
  `(star_targets '=')+ if_expr|with_expr|try_expr`, подключённые в
  `statement` после `simple_stmts` (после `DEDENT` нет `NEWLINE`, который
  требует `simple_stmts` — та же причина, что у `match_expr_stmt`). `elif`
  оборачивается в `Expr`-стейтмент прямо в action грамматики (одно
  C-выражение, без хелпера). Блок-выражение как RHS на следующей строке
  поддержано и в `funnypy_assign_stmt` — без завершающего `NEWLINE`
  (`r =` + `INDENT` + `if_expr|with_expr|try_expr` + `DEDENT`;
  см. `funny-python-line-continuation`).
- **Кодген (`Python/codegen.c`):** `codegen_block_value(c, body)` — обобщение
  `codegen_match_expr_body` (компилирует блок, оставляя значение хвостового
  выражения или `None`). `codegen_if_expr` — по образцу statement-`codegen_if`
  с `codegen_block_value` для веток; `codegen_with_expr` — по образцу
  `codegen_with`; `codegen_try_expr` — по образцу `codegen_try_except`/
  `codegen_try_finally` с инвариантом match-expr: **каждый путь к `end`
  кладёт ровно одно значение**. `try` — самая сложная часть: при
  `finally` значение должно сохраняться на стеке поперёк finally-блока.
- **Symtable (`Python/symtable.c`):** кейсы для трёх узлов — visit body/orelse
  как conditional/try block, `as`-цели биндят имена в текущем scope.
  **Валидация AST (`Python/ast.c`):** `validate_expr` — test/items/body/
  handlers/orelse/finalbody, непустые body/handlers где требуются.
- **Filler `..`:** в `Grammar/python.gram` filler'ы `pipe_stage` и
  hole-вызова принимают `if_expr | with_expr | try_expr` (наряду с
  `funnypy_lambda_def` и `star_expressions`). Резолюция hole — прежняя
  (`funnypy_find_hole`), меняется только тип filler-выражения.
- **Не трогаем:** `ast_unparse`, байткод-оптимизатор, рантайм `ceval`
  (новых opcode нет — всё через существующие `JUMP_IF_*`/`SETUP_*` и
  exception-table, как у statement-аналогов). Следствие (как у `MatchExpr`):
  `ast.unparse()` не знает новых узлов и выдаёт для них мусор (не ошибку) —
  известное ограничение v1, вне скоупа.
- **Деферы v1 / остаются statement'ами:** `async with`-выражение,
  `except*` (exception groups), `async def`/`async for` (согласовано с
  `for`/`while`), `class` (тело класса значения не производит), а также все
  statement'ы без значения: `return`, `raise`, `pass`, `del`, `assert`,
  `break`, `continue`, `global`, `nonlocal`, `import`/`from`, `type`-alias.
  Произвольные expression-позиции (`return`, аргументы вызовов) и REPL-форма —
  та же оговорка, что у pipeline/match-expr.
- **Тесты:** `Lib/test/test_funnypy_expr.py` (или классы в
  `Lib/test/test_funnypy.py`): if (then/elif/else, без else, пустая ветка →
  None, вложенность), with (`as`-биндинг, значение тела, `__enter__`/`__exit__`),
  try (try/except/else/finally, значение на каждом пути, finally не
  перекрывает значение, повторный raise), filler `..` с блок-выражением,
  неизменность statement-`if`/`with`/`try`, R1-суперсет.

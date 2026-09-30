---
id: funny-python-pipeline
type: module-design
status: draft
title: "Оператор `..`: пайплайн, placeholder, pipe-match"
parent: funny-python-architecture
---

## Responsibility

Оператор `..` в трёх ролях: пайплайн значения в вызов, placeholder для
statement-лямбды/выражения, pipe-форма `match`. Покрывает требование R4 из
`funny-python-goal`. Здесь же — смежная форма R10, блок-аргумент без `(..)`:
`run def(): …` использует ту же резолюцию филлера, что и placeholder.

## Syntax

```python
square = def(x): return x ** 2

[1, 2, 3]              # пайплайн: значение идёт ПОСЛЕДНИМ позиционным аргументом
..map(square)          # = map(square, [1, 2, 3])
..filter(is_even)      # цепочка: = filter(is_even, map(square, [1, 2, 3]))

[1, 2, 3]              # placeholder `..` заполняется следующей def-лямбдой
..map(..) def(x):
    return x ** 2      # = map(def(x): return x**2, [1, 2, 3])

run(..) def():         # без пайплайна: hole заполняется def или выражением
    print('Running')   # = def <lambda>(): print('Running'); run(<lambda>)

run def():             # R10: неявный hole — def идёт аргументом вызова
    print('Running')   # = run(..) def(): … = run(<lambda>)

res = run def():       # R10 в RHS присваивания (одиночная цель)
    return 42          # = res = run(<lambda>)

run(..) compute_x() + 1   # filler — любое выражение: = run(compute_x() + 1)

run(..) try:              # filler — блок-выражение (R11)
    'smth'               # = run(try: 'smth' except: 'other')
except:
    'other'

res =                     # значение пайплайна в RHS присваивания: любое
    21                    # выражение, блочное выражение (match/if/with/try)
    ..double()            # или def-блок; = res = double(21)

res =                     # def-блок хоистится как FunctionDef <lambda>
    def():                # = def <lambda>(): 42
        42                #   res = run(<lambda>)
    ..run()

def():                    # standalone: пайплайн на анонимной функции
    print('Running')      # = def <lambda>(): print('Running')
..run()                   #   <lambda> ..run()

res =                     # блочное значение в RHS
    match x:
        case 1: "one"
    ..str()               # = res = str(match x: …)

x                    # pipe-match: обычный compound match с piped-субъектом
..match:
    case 1:
        print(1)
    case 2:
        print(2)
```

Правила позиций (подтверждено):

- Пайплайн-звено — только с новой строки; каждое звено на своей строке.
  Inline-`..` в выражениях нет.
- Pipeline собирается data-last: значение — последним позиционным аргументом
  каждого звена. Голая ссылка без вызова (`..len`) трактуется как вызов без
  аргументов (`len(value)`). Голая ссылка с def-лямбдой
  (`..map def(x): …`) ≡ `..map(..) def(x): …` → `map(def, value)`: имя —
  функция одного аргумента, def подставляется первым, value — последним.
- Placeholder `..` — в аргументах вызова, заполняется filler'ом:
  `def(параметры): <suite>` (statement-лямбда, см. `funny-python-lambda`) или
  выражением. def-filler может идти со следующей строки (с отступом) —
  `..map` / `..myfunc(1, 2, ..)` затем `def(x): …`; выражение-заполнитель —
  только на той же строке (включая блок-выражения `if`/`with`/`try`, R11).
  Ровно один placeholder на стейтмент, только в прямых позиционных
  аргументах внешнего вызова.
- Неявный блок-аргумент (R10): `callee def(…): suite` — `callee` это голая
  ссылка (`primary`, не `Call`: имя, атрибут, subscript, …) или вызов с прямым
  `(..)`; `def` — на той же строке и становится единственным позиционным
  аргументом (голая ссылка) либо заполняет hole (вызов). Перенос строки между
  `callee` и `def` — не эта форма: `run` и `def(): …` остаются двумя
  statement'ами. `Call` без hole + def (`f(a) def(): …`) — SyntaxError.
  Позиции: statement и RHS присваивания с одиночной целью-`NAME`.
  Голая ссылка `match` — не callee этой формы: `match def` — введение
  match-def (R9), поэтому `match def(x): …` без `case` остаётся SyntaxError;
  для функции с именем `match` пишут `(match) def(…): …` или
  `match(..) def(…): …`.
- `x ..match:` = `match x:`; имена связываются в текущей области. Стадии до
  `..match:` применяются по цепочке, итог — субъект match.
- Значение пайплайна (то, что идёт до звеньев) — не только плоское
  выражение: допустимы def-блок (`def(): …`, хоистится как `<lambda>`) и
  блочные выражения `match`/`if`/`with`/`try`. В RHS присваивания пайплайн
  записывается как `цель =` с новой строки, значением ниже и звеньями на
  его уровне (`res =` / ‹значение› / `..run()`); множественные цели
  (`a = b = …`) поддержаны.

## Semantics

- Pipeline: `[v] ..f(a) ..g(b)` → `g(f(a, v), b)` — десахаринг, промежуточные
  значения не сохраняются в переменные.
- Значение пайплайна: def-блок десахарится как у filler'а (хоист
  `FunctionDef <lambda>` перед стейтментом, значением — `Name("<lambda>")`);
  блочное выражение — уже `expr`-узел (`MatchExpr`/`IfExpr`/`WithExpr`/
  `TryExpr`) и подставляется как есть. Результат заворачивается в `Expr`, а
  в RHS-форме — в `Assign(targets, результат)`.
- Placeholder: hole — маркерный узел; filler подставляется в AST до
  компиляции. def-filler хоистится отдельным statement'ом FunctionDef с
  синтетическим именем `<lambda>` перед стейтментом (как у лямбд).
- Неявный блок-аргумент: тот же десахар, что у def-filler'а, — `FunctionDef`
  `<lambda>` перед стейтментом, итог `Expr(call)` или `Assign(target, call)`;
  отдельного hole-узла нет — голая ссылка оборачивается в `callee(<lambda>)`.
- Незаполненный placeholder — compile-ошибка (см. Boundary): hole без
  filler'а, hole во вложенном вызове или keyword-аргументе, `x = f(..)` вне
  форм этой спеки.

## Boundary

- **Механизм — десахаринг на уровне парсера**, как лямбды и деструктуризация:
  без новых AST-узлов, без правок `codegen.c`. `..` токенизируется как два
  DOT-токена (`.` `.`); `...` (Ellipsis) и формы вида `1..2` не затрагиваем.
- **Hole** — `Name("<pipe>")`: идентификатор, который пользователь не может
  набрать, поэтому не пересекается с пользовательскими именами.
- **Точки правки (реализовано):**
  - `Grammar/python.gram` — `pipe_hole` (`..` в позиционных аргументах →
    `Name("<pipe>")`), `pipe_stage`/`pipe_stages` (леворекурсивный список
    стадий — gather-разделитель не может быть токеном NEWLINE),
    `hole_call_expr` (выражение + action-валидация «это вызов с hole» —
    placeholder-стейтменты падают быстро, не перетягивая farthest-token на
    чужих синтаксических ошибках; регрессионный сигнал — `test_exceptions`
    на offset'ах), `funnypy_pipe_stmt` (pipe-match / pipeline / hole-def /
    hole-expr), `funnypy_block_arg_stmt` (R10: statement + RHS, lookahead
    `&'def'`). `funnypy_pipe_stmt` подключено в `statement` и
    `statement_newline`; `funnypy_block_arg_stmt` — только в `statement`
    (после `funnypy_pipe_stmt`), чтобы не задеть REPL. `funnypy_pipe_assign_stmt`
    (пайплайн в RHS: плоское/блочное/def-значение) подключено в `statement`
    после `simple_stmts`, но перед `match_expr_stmt`/`if_expr_stmt`/… — те
    иначе проглотят блок и оставят звенья; def-значение добавлено также
    альтернативами `funnypy_lambda_def pipe_stages` в `funnypy_lambda_stmt`
    перед голой def-альтернативой. Оба правила — **после** `compound_stmt`,
    иначе их блок-выражения перехватывают generic `invalid_block` вместо
    `invalid_if_stmt`/`invalid_try_stmt`/… (см. ограничения).
  - `Parser/action_helpers.c` — `FunnypyPipeStage`, `funnypy_find_hole`
    (один hole в прямых позиционных аргументах внешнего вызова, >1 →
    SyntaxError), `funnypy_fill_def` (резолюция филлера: hole → ссылка,
    голая ссылка → обёртка в вызов; `Call` без hole — откат),
    `stage_def`/`stage_expr`, `pipe_apply`/`pipeline`/`pipeline_match`
    (data-last сборка), `pipe_hole_def`/`pipe_hole_expr`,
    `block_expr`/`block_assign` (R10: `[FunctionDef, Expr|Assign]`),
    `funnypy_def_value_ref` (`Name("<lambda>")` с позицией def-блока),
    `funnypy_pipe_build` (общая сборка: хоист value-def + `Expr`/`Assign`),
    `pipeline_def` (standalone на def-блоке), `pipe_assign` (RHS-форма);
    `pipeline` отрефакторен в обёртку над `pipe_build`.
  - `Python/symtable.c` — compile-ошибка на `Name("<pipe>")` в Load-контексте
    (незаполненный placeholder) с локацией через `SET_ERROR_LOCATION`.
- **Ограничения v1 (проверено):**
  - Значение пайплайна в **standalone**-форме — плоское выражение или
    def-блок; standalone-форма блочного значения (`match …` / `if …` /
    `try …`, затем `..`) **не поддержана (дефер)**. Правило перед
    `compound_stmt` перехватывало generic `invalid_block` вместо
    `invalid_if_stmt`/`invalid_try_stmt`/… и ломало сообщения об ошибках
    (`test_exceptions`: «expected an indented block after 'if' statement»);
    все funnypy statement-правила стоят после `compound_stmt` именно поэтому.
    Блочное значение работает в RHS-форме (`res =` / ‹блок› / `..f()`),
    которая этой коллизии не имеет.
  - def-filler и filler-блок-выражение (`if`/`with`/`try`, R11) всегда
    последние: блок съедает финальный NEWLINE/DEDENT, разделителя для
    следующей стадии нет. Стадии после такого filler'а — SyntaxError.
  - Первая величина пайплайна — на своей строке; hole-подстановка — целостный
    стейтмент, дальше пайплайн не продолжается.
  - Hole резолвится только в прямых позиционных аргументах внешнего вызова;
    вложенный/keyword hole — compile-ошибка.
  - **Терминальный REPL не поддерживает пайплайн (проверено).**
    `funnypy_pipe_stmt` НЕ подключён в `statement_newline`: интерактивный
    токенайзер печатает `... ` и блокируется на любом запросе токена за
    пределами текущей строки, а пайплайн по определению заглядывает на
    следующую строку — в лоб это давало продолжение на каждом выражении
    и вешало `test_cmd_line_script`. Работают файлы, `-c` и Jupyter
    (ячейка исполняется exec-парсом; последнее выражение компилируется
    из уже готового AST).
  - **Неявный блок-аргумент (R10) в терминальном REPL не работает**:
    `funnypy_block_arg_stmt` подключён только в `statement`; exec-режим
    (файлы, `-c`, Jupyter) форму поддерживает, `compile(…, 'single')` —
    SyntaxError.
  - **Лямбда-присваивание в REPL требует лишнего Enter** при однострочном
    теле (`sq = def(x): return x` + Enter + пустая строка) — ровно как
    upstream-однострочный `if True: pass` (обе ветки требуют завершающий
    NEWLINE после блока).
- **Тесты:** `Lib/test/test_funnypy_pipe.py`: примеры из запроса, цепочка,
  bare-звено (в т.ч. с def-filler той же и следующей строкой), hole-filler на
  следующей строке, def/expr-filler, def/expr/блок-выражение-filler
  (`if`/`with`/`try`, класс `BlockFillerTests`), pipe-match
  (совпадение/несовпадение, стадии перед match), SyntaxError на
  незаполненный/вложенный/keyword hole, на `Call` без hole + def и на
  inline-`..`; неизменность Ellipsis `f(...)` и распаковки. R10 — класс
  `BlockArgumentTests`: statement/RHS, атрибут и subscript, def с параметрами,
  эквивалентность `(..)`, неявный return результата вызова, two-statement
  поведение при переносе строки, негативы `f(a) def()`, вложенный и двойной
  hole. Значение пайплайна — класс `PipeValueTests`: форма из запроса и её
  эквивалентность форме B, хоист def-значения как `<lambda>`, standalone def,
  def с параметрами, плоское выражение, голая стадия, цепочка стадий,
  блочные значения `match`/`if`/`with`/`try` в RHS, множественные цели,
  неявный return результата пайплайна.

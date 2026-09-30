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
`funny-python-goal`.

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

run(..) compute_x() + 1   # filler — любое выражение: = run(compute_x() + 1)

run(..) try:              # filler — блок-выражение (R8)
    'smth'               # = run(try: 'smth' except: 'other')
except:
    'other'

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
  только на той же строке (включая блок-выражения `if`/`with`/`try`, R8).
  Ровно один placeholder на стейтмент, только в прямых позиционных
  аргументах внешнего вызова.
- `x ..match:` = `match x:`; имена связываются в текущей области. Стадии до
  `..match:` применяются по цепочке, итог — субъект match.

## Semantics

- Pipeline: `[v] ..f(a) ..g(b)` → `g(f(a, v), b)` — десахаринг, промежуточные
  значения не сохраняются в переменные.
- Placeholder: hole — маркерный узел; filler подставляется в AST до
  компиляции. def-filler хоистится отдельным statement'ом FunctionDef с
  синтетическим именем `<lambda>` перед стейтментом (как у лямбд).
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
    hole-expr). Подключено в `statement` и `statement_newline`.
  - `Parser/action_helpers.c` — `FunnypyPipeStage`, `funnypy_find_hole`
    (один hole в прямых позиционных аргументах внешнего вызова, >1 →
    SyntaxError), `stage_def`/`stage_expr` (резолюция hole),
    `pipe_apply`/`pipeline`/`pipeline_match` (data-last сборка),
    `pipe_hole_def`/`pipe_hole_expr`. Голая стадия без вызова (`..len`)
    оборачивается в вызов с одним аргументом; `stage_def` при голом имени
    (`..map def`) оборачивает имя в `name(<lambda>)` (hole — только для
    `Call` с placeholder'ом; `Call` без hole + def — откат).
  - `Python/symtable.c` — compile-ошибка на `Name("<pipe>")` в Load-контексте
    (незаполненный placeholder) с локацией через `SET_ERROR_LOCATION`.
- **Ограничения v1 (проверено):**
  - def-filler и filler-блок-выражение (`if`/`with`/`try`, R8) всегда
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
  - **Лямбда-присваивание в REPL требует лишнего Enter** при однострочном
    теле (`sq = def(x): return x` + Enter + пустая строка) — ровно как
    upstream-однострочный `if True: pass` (обе ветки требуют завершающий
    NEWLINE после блока).
- **Тесты:** `Lib/test/test_funnypy_pipe.py`: примеры из запроса, цепочка,
  bare-звено (в т.ч. с def-filler той же и следующей строкой), hole-filler на
  следующей строке, def/expr/блок-выражение-filler (`if`/`with`/`try`, класс
  `BlockFillerTests`), pipe-match (совпадение/несовпадение, стадии перед
  match), SyntaxError на незаполненный/вложенный/keyword hole, на `Call` без
  hole + def и на inline-`..`; неизменность Ellipsis `f(...)` и распаковки.

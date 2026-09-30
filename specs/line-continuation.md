---
id: funny-python-line-continuation
type: module-design
title: "Неявные переносы строк: RHS после `=`, leading-dot"
parent: funny-python-architecture
---

## Responsibility

Два неявных переноса, которые делают функциональные цепочки читаемее:
правая часть присваивания на следующей строке и метод-чейнинг со строки,
начинающейся с `.`. Дополняет R2/R4, но живёт отдельно — трогает токенизатор.

## Syntax

```python
f =                     # RHS на следующей строке (с отступом)
    1 + 2               # f == 3

objects = MyModel.objects
    .filter(active)     # leading-dot: = MyModel.objects.filter(active)
    .map(name)          # цепочка продолжается
```

Правила:

- После `=` на конце строки на следующей строке (обязательно с отступом)
  может начинаться RHS: любое выражение или блочная def-лямбда
  (`f =` + `def(): …`, см. `funny-python-lambda`). `f = 1` затем отступом
  `+ 2` остаётся ошибкой — между `=` и NEWLINE стоит `1`.
- Строка, начинающаяся с `.name` (`.` и старт идентификатора), — продолжение
  предыдущей строки: токенизатор не выдаёт NEWLINE/INDENT, атрибут-доступ
  применяется к выражению предыдущей строки. Исключения: `.5` (float) и
  `..`/`...` (пайплайн/Ellipsis) — это не `.name`, переноса нет.

## Semantics

- `f =` + `1 + 2` — обычный `Assign(f, BinOp(1 + 2))`, никакой новой
  семантики.
- Leading-dot — чисто лексический склей: `a.b` + строка `.c()` токенизируется
  как `a.b.c()`, поэтому работает в любой позиции выражения (правая часть
  `=`, `return`, выражение-инструкция, цепочки методов).

## Boundary

- **Точки правки:**
  - `Parser/lexer/lexer.c` — `tok_next_line_starts_with_dot` (peek следующей
    физической строки на `.name`), флаг `at_line_continuation` в
    `Parser/lexer/state.h` (подавляет INDENT/DEDENT строки-продолжения), ветка
    в NEWLINE-пути, перенаправляющая `.name` в текущую логическую строку.
  - `Grammar/python.gram` — statement-правило `funnypy_assign_stmt`
    (`star_targets '=' NEWLINE INDENT annotated_rhs NEWLINE DEDENT`),
    подключённое в `statement` (не в `statement_newline`).
- **Peek безопасен:** не выполняется для интерактивных токенизаторов
  (`tok->prompt != NULL` / `tok->readline != NULL`) — иначе readline
  зависнет на запросе следующей строки. Файлы, `-c`, Jupyter работают;
  терминальный REPL — нет (как у пайплайна).
- **Суперсет R1:** обе формы раньше были SyntaxError; валидный Python
  неизменён (leading `.` в валидном Python невозможен; `.5`/`...` не задеты).
- **Тесты:** `Lib/test/test_funnypy_line_cont.py` — RHS-выражение и
  RHS-лямбда на следующей строке, цепочка `.`, продолжение после вызова,
  неизменность `.5`/`...`, ошибка на `.5` после `x = 1`.

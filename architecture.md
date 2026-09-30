---
id: funny-python-architecture
type: architecture-design
status: draft
title: Funny Python — архитектура форка
parent: funny-python-goal
---

## Strategy

Funny Python — форк CPython (апстрим — ветка 3.15, `cpython-main/`). Новый синтаксис
внедряется насквозь через стандартный пайплайн компилятора, отдельных
транспиляторов и препроцессоров нет. Дифф к апстриму держим минимальным и
точечным, чтобы сохранить возможность синка с веткой `3.15` CPython.

## Where features hook in

Пайплайн и реальные точки вмешательства (версия 3.15: компилятор разнесён —
генерация байткода живёт в `codegen.c`, а не в `compile.c`):

```
Grammar/python.gram ──► Parser/ (pegen, генерация parser.c) ──► AST
(Parser/Python.asdl, Python/ast.c, Python/ast_preprocess.c, symtable.c)
──► Python/codegen.c ──► байткод ──► рантайм (Objects/, Python/ceval.c)
```

- **Грамматика** — `Grammar/python.gram`; после правки парсер перегенерируется
  через `Tools/peg_generator`.
- **AST** — новые узлы/поля описываются в `Parser/Python.asdl`; код
  `Python/Python-ast.c` генерируется из него (`Parser/asdl_c.py`).
- **Pattern-matching codegen** — уже существующий `codegen_pattern*` в
  `Python/codegen.c`; деструктуризация переиспользует его, а не пишет свой.
- **MatchError** — нового встроенного исключения пока нет; добавляется в
  `Objects/exceptions.c` (+ документация, `Lib/test/exception_hierarchy.txt`).
- **Токенизатор** — `Parser/lexer/lexer.c` (+ поле в `Parser/lexer/state.h`):
  leading-dot продолжение строки (см. `funny-python-line-continuation`).

## Module edges

- `funny-python-lambda` — depends-on: грамматика, AST/codegen. Рантайм не
  трогает: лямбда компилируется в обычный код функции. R6 добавляет
  expression-форму (`def_expr` → `Lambda`).
- `funny-python-pattern-assign` — depends-on: грамматика, codegen
  (`codegen_pattern*`), рантайм (новый `MatchError`).
- `funny-python-implicit-return` — depends-on: codegen (`Python/codegen.c`,
  `compiler_unit`); трогает семантику возврата всех функций — см. R5.
- `funny-python-match-expression` — depends-on: грамматика, AST
  (`Parser/Python.asdl` → `MatchExpr`), codegen (`codegen_pattern*`),
  symtable и валидация AST.
- `funny-python-line-continuation` — depends-on: токенизатор
  (`Parser/lexer/lexer.c`), грамматика (`funnypy_assign_stmt`). Рантайм и
  `codegen.c` не трогает.
- `funny-python-if-with-try-expr` — depends-on: грамматика, AST
  (`Parser/Python.asdl` → `IfExpr`/`WithExpr`/`TryExpr`), codegen
  (обобщение `codegen_match_expr_body` → `codegen_block_value`; по образцу
  statement-`codegen_if`/`codegen_with`/`codegen_try_*`), symtable и
  валидация AST; расширяет filler оператора `..` из `funny-python-pipeline`.

## Invariants

- **Суперсет (R1) проверяется тестами:** вся штатная `Lib/test` CPython должна
  проходить после каждой фичи. Единственное исключение — правило R5
  (неявный return): тесты, кодирующие старое поведение хвостового выражения
  или старого байткода, адаптируются явно и перечислены в
  `funny-python-implicit-return`; молчаливых расхождений быть не должно.
  Новая семантика закреплена `Lib/test/test_funnypy.py`.
- **Минимальный дифф:** изменения только в перечисленных выше файлах + новые
  тесты; правки смежной механики (tokenizer, ceval) — только с отдельным
  обоснованием в спеке фичи. Токенизатор задействован однажды — leading-dot
  (`funny-python-line-continuation`).
- **Стиль — как в CPython:** следуем `cpython-main/AGENTS.md` (минимальные
  сфокусированные изменения, существующий стиль, тесты на каждое изменение).
- **Решения живут в спеках:** рационале синтаксиса/семантики — здесь и в
  спеках фич, в коде — максимум однострочный указатель.
- **Сгенерированные файлы не правятся руками:** после изменения
  `Grammar/python.gram` — `make regen-pegen`, после `Parser/Python.asdl` —
  `make regen-ast` (или прямые вызовы `Tools/peg_generator` и
  `Parser/asdl_c.py` тем же `python3.14`).

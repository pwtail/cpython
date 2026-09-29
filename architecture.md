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

## Module edges

- `funny-python-lambda` — depends-on: грамматика, AST/codegen. Рантайм не
  трогает: лямбда компилируется в обычный код функции.
- `funny-python-pattern-assign` — depends-on: грамматика, codegen
  (`codegen_pattern*`), рантайм (новый `MatchError`).

## Invariants

- **Суперсет (R1) проверяется тестами:** вся штатная `Lib/test` CPython должна
  проходить без изменений после каждой фичи — это регрессионный инвариант
  форка.
- **Минимальный дифф:** изменения только в перечисленных выше файлах + новые
  тесты; никаких правок смежной механики (tokenizer, ceval) без отдельного
  обоснования в спеке фичи.
- **Стиль — как в CPython:** следуем `cpython-main/AGENTS.md` (минимальные
  сфокусированные изменения, существующий стиль, тесты на каждое изменение).
- **Решения живут в спеках:** рационале синтаксиса/семантики — здесь и в
  спеках фич, в коде — максимум однострочный указатель.

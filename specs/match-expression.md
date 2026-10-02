---
id: funny-python-match-expression
type: module-design
status: draft
title: "`match` как выражение"
parent: funny-python-architecture
---

## Responsibility

`match` в позиции правой части присваивания возвращает значение
сработавшего кейса. Покрывает требование R7 из `funny-python-goal`.

## Syntax

```python
s = match 2:
  case 2:
     "hello"        # s == "hello"
```

Правило действует только как правая часть присваивания (любая `star_targets`,
включая множественные цели, атрибуты и индексы). В других позициях
(`return match ...`, аргументы вызовов) форма недоступна: блок кейсов
съедает финальный `NEWLINE`/`DEDENT`, и statement-уровневые контексты
потребовали бы отдельных правил.

## Semantics

- Значение выражения — последнее выражение тела сработавшего кейса (та же
  логика, что у R5); если тело не заканчивается выражением — `None`,
  `...` — тоже `None`.
- Если ни один кейс не совпал — `MatchError` (деструктивный match R8; до
  R8 возвращался `None`).
- Guard у `case _` работает как обычно: провалившийся guard дефолта даёт
  `MatchError` (тоже R8).
- Subject вычисляется один раз; имена паттернов связываются в текущей области.

## Boundary

- **Новый AST-узел** `MatchExpr(expr subject, match_case* cases)` в
  `Parser/Python.asdl` (генерация `Python/Python-ast.c`,
  `Include/internal/pycore_ast*.h` через `make regen-ast`). Поля — те же,
  что у statement-узла `Match`.
- **Грамматика:** правило `match_expr` (переиспользует `subject_expr` и
  `case_block`) в альтернативах `expression`. Дополнительно —
  **statement-уровневое** правило `match_expr_stmt`:
  `(star_targets '=')+ match_expr`, подключённое в `statement` после
  `simple_stmts`. Причина: после закрывающих `DEDENT` токенизатор не выдаёт
  `NEWLINE`, который требует `simple_stmts`, поэтому без отдельного правила
  `s = match ...` падает на следующем statement. `match_expr_stmt` идёт
  после `match_assign` (`match value case PATTERN`, R3) и не конфликтует с ним.
- **Кодген:** `codegen_match_expr` (`Python/codegen.c`) по образцу
  `codegen_match_inner`: тот же обход кейсов через `codegen_pattern*`, но
  тело кейса компилируется с оставлением последнего выражения на стеке
  (`codegen_match_expr_body`), путь «ничего не совпало» и провалившийся guard
  дефолта ведут на метку `no_match` (R8: `codegen_raise_match_error`).
  Инвариант: каждый путь, достигающий `end`, кладёт ровно одно значение;
  путь «ничего не совпало» завершается raise'ом.
- **Symtable:** кейсы visit'ятся как conditional block, паттерны биндят имена
  в текущем scope. **Валидация AST:** `validate_expr` проверяет subject,
  непустой список кейсов, паттерны/guard/тела.
- **Не трогаем:** `ast_unparse` (в дереве нет поддержки и statement-`match`),
  байткод-оптимизатор. Появление узла в `ast`/`compile()` работает
  автоматически после `make regen-ast`.
- **Тесты:** `Lib/test/test_funnypy.py` (класс `MatchExprTests`): базовый
  случай, guards, захваты, дефолт, отсутствие совпадения → `MatchError`,
  множественные цели, attr/subscript, вложенность, class-паттерн,
  AST-roundtrip, `match` как имя, неизменность statement-`match`,
  неизменность R3/R4.

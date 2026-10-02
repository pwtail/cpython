---
id: funny-python-pattern-assign
type: module-design
status: draft
title: Паттерн-деструктуризация
parent: funny-python-architecture
---

## Responsibility

Инструкция `match value case pattern`, где после `case` — паттерн в смысле
`match..case`. Покрывает требование R3 из `funny-python-goal`.

## Syntax

```python
match my_dict case {'x': x}
match p case Point(x=px, y=py)
match items case [first, *rest]
match value case {'a': a} | {'b': a}   # or-паттерн: обязан связывать одни имена
```

Разрешён любой вид паттерна из `match..case`: mapping, sequence, class, or,
as, capture, literal, value, wildcard. Guards (`if`) не поддерживаются — в
синтаксисе инструкции им нет места.

**Префикс `match` (мягкое ключевое слово) делает инструкцию однозначной.**
Коллизии с существующей распаковкой нет: `a, b = v` и `[a, b] = v` без
префикса — прежняя распаковка с `ValueError` (инвариант R1 сохраняется
тривиально). С префиксом — всегда семантика паттерна, даже для форм,
совпадающих по виду с целями распаковки: `match v case [a, b]` отличается от
`[a, b] = v` тем, что sequence-паттерн не принимает произвольные итераторы
(нужен Sized, `str`/`bytes`/`dict` отвергаются) и при несовпадении даёт
`MatchError`, а не `ValueError`.

**Примечание.** Паттерн не обязан связывать хотя бы одно имя:
`match x case 1` является легальной формой, если переменная x определена.

**Субъект — `subject_expr`**, как у match-выражения: допускает кортеж
(`match 1, 2 case a, b`). Связывающая цель паттерна больше не ограничена
позицией перед `=`: `match value case x` и `match value case _ as y` выразимы
напрямую (в прежней форме `match pattern = value` они требовали скобок).

## Semantics

Эквивалент (поверхностный синтаксис буквально отражает десахаринг):

```python
match value:
    case pattern if True:
        pass
    case _:
        raise MatchError(...)
```

`if True` — несущая часть десахаринга, не украшение (см. Boundary).

- `value` вычисляется один раз (subject match-формы).
- Совпадение → имена паттерна связаны, исполнение продолжается.
- Несовпадение → `MatchError` (подтверждено). Новое встроенное исключение;
  базовый класс — `Exception` (не подтверждено; альтернатива — `ValueError`,
  чтобы ловилось существующим `except ValueError`).

## Boundary

- **Механизм — десахаринг на уровне парсера** (реализовано), как и лямбды
  (`funny-python-lambda`): правило `match_assign` вызывает C-хелпер
  `_PyPegen_funnypy_match_assign` (`Parser/action_helpers.c`, объявление в
  `Parser/pegen.h`), который строит `_PyAST_Match(value, [case(pattern,
  guard=True, pass), case(wildcard, raise MatchError)])` — subject держит
  выражение, синтетический temp не нужен. Хелпер, а не inline-action: pegen
  генерирует `_res = <action>;`, т.е. action обязан быть ОДНИМ C-выражением.
  `Python.asdl` и `Python/codegen.c` не тронуты — вся валидация паттернов
  (связываемость имён в or-альтернативах, irrefutable-проверки) достаётся от
  существующего match-codegen.
- **Правило грамматики:** `match_assign` = `"match" v=subject_expr "case"
  pat=patterns { _PyPegen_funnypy_match_assign(p, pat, v) }` — порядок
  value/pattern обратный прежней форме `match PATTERN = value`.
- **`if True` несущий:** кодогенератор разрешает irrefutable-паттерн только у
  последнего case или у case с guard'ом (`allow_irrefutable = guard != NULL ||
  i == cases - 1`, `Python/codegen.c`). Без guard'а `match v case _` падал бы
  с `wildcard makes remaining patterns unreachable`.
- **Подключение — в двух местах** (проверено): `simple_stmt` первой
  альтернативой `&"match" match_assign` (нужна для REPL — `simple_stmts`
  потребляет финальный NEWLINE) и `statement` первой альтернативой
  `match_assign NEWLINE` — ДО `compound_stmt`. Второе размещение обязательно:
  иначе во втором проходе парсера (error-pass с активными `invalid_*`)
  разбор входит в compound `match_stmt`, где `invalid_named_expression`
  аварийно завершает проход — и чужая синтаксическая ошибка репортится на
  строке pattern-assign. Регрессионный тест —
  `test_error_elsewhere_reported_at_its_own_line`.
  `"match"` — именно двойные кавычки (мягкое ключевое слово); `match = 1`
  остаётся присваиванием имени, `match.x = 1` — тоже (покрыто тестами).
- **Запасной путь** (не понадобился): отдельный AST-узел `MatchAssign` через
  `codegen_pattern*` — если десахаринг упрётся в диагностику. Решение
  фиксируется здесь при смене.
- **Тесты:** `Lib/test/test_funnypy_pattern_assign.py`: все виды паттернов,
  `MatchError` при несовпадении, `if True`-случай (`match v case _`,
  `match v case _ as y`), кортежный субъект, голый capture (`match v case x`),
  однократность RHS, различие `match v case [a, b]` и `[a, b] = v`,
  легальность `match = 1` / `match.x = 1`, неизменность compound `match_stmt`,
  граничные SyntaxError (в т.ч. отвержение прежней формы `match PATTERN =
  value`) и локация чужой ошибки.

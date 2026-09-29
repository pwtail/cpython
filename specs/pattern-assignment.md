---
id: funny-python-pattern-assign
type: module-design
status: draft
title: Паттерн-деструктуризация
parent: funny-python-architecture
---

## Responsibility

Инструкция `match pattern = value`, где после `match` — паттерн в смысле
`match..case`. Покрывает требование R3 из `funny-python-goal`.

## Syntax

```python
match {'x': x} = my_dict
match Point(x=px, y=py) = p
match [first, *rest] = items
match {'a': a} | {'b': a} = value   # or-паттерн: обязан связывать одни имена
```

Разрешён любой вид паттерна из `match..case`: mapping, sequence, class, or,
as, capture, literal, value, wildcard. Guards (`if`) не поддерживаются — в
синтаксисе присваивания им нет места.

**Префикс `match` (мягкое ключевое слово) делает инструкцию однозначной.**
Коллизии с существующей распаковкой нет: `a, b = v` и `[a, b] = v` без
префикса — прежняя распаковка с `ValueError` (инвариант R1 сохраняется
тривиально). С префиксом — всегда семантика паттерна, даже для форм,
совпадающих по виду с целями распаковки: `match [a, b] = v` отличается от
`[a, b] = v` тем, что sequence-паттерн не принимает произвольные итераторы
(нужен Sized, `str`/`bytes`/`dict` отвергаются) и при несовпадении даёт
`MatchError`, а не `ValueError`.

**Примечание.** Паттерн не обязан связывать хотя бы одно имя:
`match 1 = x` является легальной формой, если переменная x определена.

**Ограничение грамматики (проверено).** Паттерн, оканчивающийся связывающей
целью прямо перед `=`, невыразим: существующее правило
`pattern_capture_target` запрещает `NAME` перед `=`, чтобы `case x = 1`
остался ошибкой. Поэтому `match x = 5` и `match _ as y = 5` — SyntaxError;
эквиваленты — `match (x) = 5`, `match (_ as y) = 5` или обычное присваивание.

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
- **`if True` несущий:** кодогенератор разрешает irrefutable-паттерн только у
  последнего case или у case с guard'ом (`allow_irrefutable = guard != NULL ||
  i == cases - 1`, `Python/codegen.c`). Без guard'а `match _ = v` падал бы с
  `wildcard makes remaining patterns unreachable`.
- **Подключение — в двух местах** (проверено): `simple_stmt` первой
  альтернативой `&"match" match_assign` (нужна для REPL — `simple_stmts`
  потребляет финальный NEWLINE) и `statement` первой альтернативой
  `match_assign NEWLINE` — ДО `compound_stmt`. Второе размещение обязательно:
  иначе во втором проходе парсера (error-pass с активными `invalid_*`)
  разбор входит в compound `match_stmt`, где `invalid_named_expression`
  аварийно завершает проход — и чужая синтаксическая ошибка репортится на
  строке pattern-assign с сообщением `cannot assign to dict literal`.
  Регрессионный тест — `test_error_elsewhere_reported_at_its_own_line`.
  `"match"` — именно двойные кавычки (мягкое ключевое слово); `match = 1`
  остаётся присваиванием имени, `match.x = 1` — тоже (покрыто тестами).
- **Запасной путь** (не понадобился): отдельный AST-узел `MatchAssign` через
  `codegen_pattern*` — если десахаринг упрётся в диагностику. Решение
  фиксируется здесь при смене.
- **Тесты:** `Lib/test/test_funnypy_pattern_assign.py` (23 теста): все виды
  паттернов, `MatchError` при несовпадении, `if True`-случай (`match _ = v`,
  `match (_ as y) = v`), однократность RHS, различие `match [a, b] = v` и
  `[a, b] = v`, легальность `match = 1` / `match.x = 1`, неизменность
  compound `match_stmt`, граничные SyntaxError и локация чужой ошибки.

---
id: funny-python-match-def
type: module-design
status: draft
title: match def — функция с кейсами
parent: funny-python-architecture
---

## Responsibility

Определение функции, за которым следуют `case`-кейсы: диспатч по значениям
аргументов (функциональные клаузы / multiple dispatch, вдохновлено Gleam).
Покрывает требование R9 из `funny-python-goal`.

## Syntax

```python
match def mufun(x: mytype, y, z):
    case [head, *rest], y, z:
        do_smth()
    case [], y, z:
        do_another()

mufun([1, 2, 3], 0, 1)   # первый case: head=1, rest=[2,3]
mufun([], 0, 1)          # второй case
mufun([1], 0, 1)         # MatchError
```

Функция вызывается как обычная. Параметры — только позиционные (можно `/`),
с аннотациями и defaults; `*args`, `**kwargs` и keyword-only отвергаются
SyntaxError (у них нет subject фиксированной арности). Ноль параметров —
ошибка. Декораторы, `async`, type-параметры и анонимная форма не поддержаны.

## Semantics

Десахаринг:

```python
match def f(p1, p2, …):
    case <pat>: <body>
    …
```
эквивалентно
```python
def f(p1, p2, …):
    return match (p1, p2, …):
        case <pat>: <body>
        …
```

- **Subject** — кортеж позиционных параметров в порядке объявления. Один
  параметр используется напрямую (не 1-кортеж), как в `subject_expr`.
- **Имена параметров остаются в scope** как целые значения; case-паттерны
  добавляют деструктуризацию и ограничения. У метода `self` — первый элемент
  subject'а, поэтому диспатч по аргументу пишется `case _, x:`.
- **`x: mytype`** — обычная аннотация (`__annotations__`), не паттерн.
- **Defaults** разрешены: параметры всегда связаны, subject полной арности.
- **Возврат** — `Return(MatchExpr(...))`: значение trailing-выражения
  сработавшего кейса, no-match → `MatchError` (R8). Синтетический `case _`
  не нужен.

## Boundary

- **Десахаринг на уровне парсера**, как у лямбд. Новых AST-узлов нет:
  переиспользуются `FunctionDef`, `Return`, `MatchExpr`. `Parser/Python.asdl`,
  `Python/codegen.c` и рантайм не тронуты.
- **Грамматика** (`Grammar/python.gram`): правило `funnypy_match_def`
  (`"match" 'def' NAME '(' params ')' ['->' expression] ':' NEWLINE INDENT
  case_block+ DEDENT`), подключено в `compound_stmt` альтернативой
  `&("match" 'def')` **перед** `match_stmt`. Конфликтов нет: `def` не начинает
  паттерн (`match_assign`), `def NAME` не парсится как `def_expr` (тот требует
  `def (`). `"match"` — мягкое ключевое слово (двойные кавычки).
- **Хелпер** `_PyPegen_funnypy_match_def` (`Parser/action_helpers.c`,
  объявление в `Parser/pegen.h`): валидирует параметры, строит subject
  (`_PyAST_Name` для одного параметра / `_PyAST_Tuple` через
  `_PyPegen_seq_append_to_end` иначе), собирает `FunctionDef` с телом
  `[Return(MatchExpr)]`. Action — одно C-выражение, поэтому логика вынесена
  в хелпер.
- **Тесты:** `Lib/test/test_funnypy_match_def.py` (38 тестов: диспатч, возврат,
  MatchError, guards, defaults, аннотации, рекурсия, метод, class/mapping
  паттерны, AST-форма, rejection'ы, отсутствие регрессий R3/R4/R6/R7 и
  `match`-как-имя).

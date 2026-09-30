---
id: funny-python-match-def
type: module-design
status: draft
title: match def — функция с кейсами
parent: funny-python-architecture
---

## Responsibility

Определение функции, за которым следуют `case`-кейсы: диспатч по значениям
позиционных аргументов (функциональные клаузы / multiple dispatch,
вдохновлено Gleam). Две формы: именованная `match def name(…)` и анонимная
`match def(…)`. Покрывает требование R9 из `funny-python-goal`.

## Syntax

Именованная форма:

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

Анонимная форма — в statement-позициях R2 (см. `funny-python-lambda`):

```python
f = match def(x, y):       # 1. RHS присваивания (в т.ч. на следующей строке)
    case [head, *rest], y:
        ...
    case [], y:
        ...

def apply(g, x):
    return match def(y):   # 2. значение return
        case 0:
            g(x)
        case _:
            g(x + y)

match def(x):              # 3. выражение-инструкция
    case n:
        print(n)

run(..) match def():       # 4. филлер плейсхолдера `..` (R4)
    case _:
        print("Running")
```

Функция вызывается как обычная. Параметры — только позиционные (можно `/`),
с аннотациями и defaults; `*args`, `**kwargs` и keyword-only отвергаются
SyntaxError (у них нет subject фиксированной арности). Ноль параметров —
ошибка. Декораторы, `async`, type-параметры и expression-позиции анонимной
формы не поддержаны.

## Semantics

Десахаринг:

```python
match def f(p1, p2, …):        # именованная
match def(p1, p2, …):          # анонимная
    case <pat>: <body>
```
эквивалентно
```python
def f(p1, p2, …):              # <lambda> — для анонимной формы
    return match (p1, p2, …):
        case <pat>: <body>
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
- **`__name__`:** в присваивании — имя цели (`f.__name__ == 'f'`), в остальных
  позициях — `<lambda>`.

## Boundary

- **Десахаринг на уровне парсера**. Новых AST-узлов нет: переиспользуются
  `FunctionDef`, `Return`, `MatchExpr`. `Parser/Python.asdl`,
  `Python/codegen.c` и рантайм не тронуты.
- **Грамматика** (`Grammar/python.gram`):
  - именованная форма — правило `funnypy_match_def`, подключено в
    `compound_stmt` альтернативой `&("match" 'def')` **перед** `match_stmt`;
  - анонимная форма — альтернатива в общем правиле `funnypy_lambda_def`
    (`"match" 'def' '(' params ')' ['->' expr] ':' NEWLINE INDENT
    case_block+ DEDENT`) плюс три альтернативы в `funnypy_lambda_stmt`
    (присваивание, присваивание с переносом на след. строку, `return`).
    Через `funnypy_lambda_def` анонимная форма доступна и как филлер стадии
    пайплайна (`pipe_stage`), и как филлер плейсхолдера `..`.
  - Конфликты снимает PEG-порядок: именованная форма требует `NAME` и стоит
    раньше; match-statement на def-expr требует второго `:`
    (`match def(x): <expr> :`).
- **Хелпер** `_PyPegen_funnypy_match_def` (`Parser/action_helpers.c`,
  объявление в `Parser/pegen.h`): принимает готовый `identifier` (имя цели или
  `<lambda>`) и локацию через `EXTRA`; валидирует параметры, строит subject и
  `FunctionDef` с телом `[Return(MatchExpr)]`. Обёртка
  `_PyPegen_funnypy_match_def_stmt` заворачивает результат в
  singleton-последовательность с NULL-check: хелпер возвращает NULL после
  валидационной ошибки, а `_PyPegen_singleton_seq` требует non-NULL.
  `_PyPegen_funnypy_lambda_return` принимает NULL (возвращает NULL).
- **Тесты:** `Lib/test/test_funnypy_match_def.py` (58 тестов: обе формы,
  statement-позиции R2, филлеры пайпа и `..`, defaults/аннотации, guards,
  рекурсия, метод, class/mapping паттерны, AST-форма, rejection'ы,
  отсутствие регрессий R3/R4/R6/R7).

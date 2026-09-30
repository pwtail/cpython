---
id: funny-python-lambda
type: module-design
status: draft
title: Многострочные лямбды
parent: funny-python-architecture
---

## Responsibility

Синтаксис анонимной функции с полноценным блоком: `def(аргументы):` + suite.
Покрывает требование R2 из `funny-python-goal`. Дополнительно R6 —
однострочная форма `def(параметры): выражение` в произвольных позициях
выражений (аргументы вызовов и т.п.).

## Syntax

Разрешена ровно в трёх statement-позициях (подтверждено):

```python
fib = def(n):              # 1. правая часть присваивания (одиночная цель)
    a, b = 0, 1
    for _ in range(n):
        yield a
        a, b = b, a + b

def apply(f, x):
    return def(y):         # 2. значение return
        return f(x) + y

def(x):                    # 3. выражение-инструкция
    print(x)
```

В позиции 1 `def` может начинаться на следующей строке (с отступом):

```python
f =
    def(x):
        return x * 2      # = f = def(x): return x * 2
```

Нигде больше: аргументы вызовов, элементы коллекций, правая часть с
несколькими целями (`a = b = def(...):`), аннотированное присваивание и
aug-assign не поддерживаются **для блочной формы**.

Форма R6 (expression-позиции) — только тело-выражение, без suite:

```python
map(def(x): x + 1, xs)        # lambda
sorted(xs, key=def(x): -x)    # lambda
```

Она допустима всюду, где грамматика допускает `expression`. В
statement-позициях (R2) всегда выигрывает блочная форма `funnypy_lambda_stmt`
(она идёт раньше в `statement`), поэтому `f = def(x): x + 1` — это обычный
`FunctionDef` с именем `f` (потом сработает неявный return R5), а не lambda.

## Semantics

Семантика — обычного `def` с тем же телом: замыкания по стандартным правилам,
`yield`/`yield from` внутри делают её генератором, `async def(...)`:
не поддерживается в v1. Параметры — те же, что у `lambda`/обычного `def`
(positional, keyword, defaults, `*args`, `**kwargs`, annotations).

`__name__` функции: в позиции присваивания — имя цели (`fib.__name__ == 'fib'`),
в остальных позициях — синтетическое имя `<lambda>` (не подтверждено).

## Boundary

- **Механизм — десахаринг на уровне парсера** (реализовано). Позиция
  присваивания разворачивается прямо в `FunctionDef` с именем цели
  (`fib = def(n): ...` ≡ `def fib(n): ...`). Позиции `return`/выражения — в
  `FunctionDef` с синтетическим именем + подстановку имени в исходный
  statement. Следствие: новых AST-узлов нет, `Python.asdl` и `codegen.c` не
  трогаем — дифф живёт в `Grammar/python.gram`, `Parser/action_helpers.c` и
  `Parser/pegen.h`.
- **Ограничение action'ов:** pegen генерирует `_res = <action>;`, поэтому
  action обязан быть одним C-выражением. Позиции присваивания и
  выражения-инструкции укладываются в одно выражение; для `return` (два
  statement'а) добавлен хелпер `_PyPegen_funnypy_lambda_return`
  (`Parser/action_helpers.c`): принимает готовый `FunctionDef`, возвращает
  последовательность `[FunctionDef, Return(Name(<lambda>))]`.
- **Точки правки грамматики:** отдельное правило `funnypy_lambda_stmt` с
  четырьмя лямбда-альтернативами (присваивание — в т.ч. `NAME '=' NEWLINE
  INDENT 'def' … DEDENT` для def на следующей строке — / `return` /
  выражение-инструкция; правило также несёт match-def-альтернативы R9 — см.
  `funny-python-match-def`), подключённое как альтернатива в `statement`
  (между `compound_stmt` и `simple_stmts`) и в `statement_newline`. Параметры — существующее правило
  `params`. Почему не альтернативы внутри `assignment`/`return_stmt`:
  формы несут блок (NEWLINE…DEDENT), а `simple_stmts` требует финальный
  NEWLINE, который блок уже поглотил — statement-уровень ведёт себя как
  compound_stmt. Разбора с lookahead `function_def` (`&('def' | '@' |
  'async')`) конфликта не возникло: `function_def_raw` требует `NAME` после
  `def`, а `invalid_def_raw` — тоже (оба откатываются на `def(`).
- **Тесты:** `Lib/test/test_funnypy_lambda.py` (15 тестов) — генератор,
  `__name__` цели, return-позиция, замыкание, выражение-инструкция,
  однострочное тело, присваивание с def на следующей строке, параметры с
  аннотациями/defaults, тело в классе, запрещённые позиции (аргумент вызова,
  коллекция, `a = b =`, атрибут, `async`).
- **Expression-форма (R6):** правило `def_expr` в `Grammar/python.gram`
  (`'def' '(' params ')' ':' expression`), десахар в `_PyAST_Lambda`;
  параметры — `params` (а не `lambda_params`: те ожидают `:`/`,` после
  параметра и не примут `)`). Новых AST-узлов и правок `codegen.c` не
  требует. `__name__` — `<lambda>`; возвратная аннотация (`def(x) -> int:`)
  не поддержана — в узле `Lambda` нет поля `returns`. Тесты — класс
  `DefExprTests` в `Lib/test/test_funnypy.py`.

# Funny Python — план реализации (as-built)

Инструкция-отчёт по реализации фич Funny Python в форке CPython (база — 3.15)
(`cpython-main/`). Все фазы выполнены и проверены; сниппеты ниже — фактические,
взяты из дерева, а не эскизы.

**Спецификации** (источник истины): `goal-and-requirements.md`,
`architecture.md`, `specs/multiline-lambda.md`,
`specs/pattern-assignment.md`.

## Три правила, без которых ничего не соберётся

1. **Action в грамматике — ровно одно C-выражение.** pegen генерирует
   `_res = <action>;`, поэтому многосоставный блок с локальными переменными
   невозможен. Всё, что требует нескольких шагов, выносится в C-хелпер
   (`Parser/action_helpers.c` + объявление в `Parser/pegen.h`).
2. **`"match"` — только двойные кавычки.** Мягкое ключевое слово (у `'def'` —
   одинарные, оно жёсткое). Перепутанные кавычки = правило молча не сработает.
3. **После правки `Grammar/python.gram` обязательно `make regen-pegen`,**
   затем `make -j$(nproc)`. `Parser/parser.c` руками не трогать.

## Фаза 0. Базовая сборка — выполнено

```bash
cd cpython-main
./configure --with-pydebug
make -j$(nproc)
./python -m test test_grammar test_syntax test_patma test_unpack_ex test_unpack -q
```

Базовая линия: 659 тестов с `test_exceptions`, SUCCESS. (`test_lambda` в дереве
нет — синтаксис лямбд покрывают `test_grammar` и `test_syntax`.)

## Фаза 1. Исключение `MatchError` — выполнено

Три файла:

1. `Include/pyerrors.h` — объявление (рядом с прочими `PyExc_*`):
   ```c
   PyAPI_DATA(PyObject *) PyExc_MatchError;
   ```
2. `Objects/exceptions.c`:
   - рядом с `StopAsyncIteration`:
     ```c
     SimpleExtendsException(PyExc_Exception, MatchError,
                            "Pattern matching failed in destructuring assignment.");
     ```
   - в таблице `static_exceptions[]` (секция подклассов `Exception`) —
     `ITEM(MatchError),` (она же добавляет тип в builtins).
3. `Lib/test/exception_hierarchy.txt` — строка `  ├── MatchError` в ветке
   `Exception` (файл отсортирован по алфавиту, после `LookupError`).

Проверка: `./python -c "raise MatchError('boom')"` →
`MatchError: boom`; `./python -m test test_exception_hierarchy test_exceptions -q`.

## Фаза 2. Многострочные лямбды — выполнено

### 2.1. Хелпер для `return`-позиции

`Parser/action_helpers.c` (в конец файла):

```c
/* Funny Python: `return def(...): ...` desugars into a FunctionDef with the
   synthetic name "<lambda>" immediately followed by `return <lambda>`.
   Locations are copied from the already built FunctionDef node. */
asdl_stmt_seq *
_PyPegen_funnypy_lambda_return(Parser *p, stmt_ty function_def)
{
    assert(function_def != NULL);
    assert(function_def->kind == FunctionDef_kind);
    expr_ty ref = _PyAST_Name(
        function_def->v.FunctionDef.name, Load,
        function_def->lineno, function_def->col_offset,
        function_def->end_lineno, function_def->end_col_offset, p->arena);
    if (ref == NULL) {
        return NULL;
    }
    stmt_ty ret = _PyAST_Return(
        ref,
        function_def->lineno, function_def->col_offset,
        function_def->end_lineno, function_def->end_col_offset, p->arena);
    if (ret == NULL) {
        return NULL;
    }
    asdl_stmt_seq *body = (asdl_stmt_seq *)_PyPegen_singleton_seq(p, ret);
    if (body == NULL) {
        return NULL;
    }
    return (asdl_stmt_seq *)_PyPegen_seq_insert_in_front(
        p, function_def, (asdl_seq *)body);
}
```

`Parser/pegen.h` — рядом с `_PyPegen_seq_append_to_end`:

```c
asdl_stmt_seq *_PyPegen_funnypy_lambda_return(Parser *, stmt_ty);
```

### 2.2. Правило в `Grammar/python.gram`

Сразу после `simple_stmts`:

```
# Funny Python: anonymous functions with a full suite, allowed in statement
# positions only (assignment RHS, return value, expression statement).
funnypy_lambda_stmt[asdl_stmt_seq*]:
    | n=NAME '=' 'def' '(' a=[params] ')' ':' body=block {
        (asdl_stmt_seq*)_PyPegen_singleton_seq(p, CHECK(stmt_ty, _PyAST_FunctionDef(
            n->v.Name.id,
            (a) ? a : CHECK(arguments_ty, _PyPegen_empty_arguments(p)),
            body, NULL, NULL, NULL, NULL, EXTRA))) }
    | 'return' 'def' '(' a=[params] ')' ':' body=block {
        _PyPegen_funnypy_lambda_return(p, CHECK(stmt_ty, _PyAST_FunctionDef(
            _PyPegen_new_identifier(p, "<lambda>"),
            (a) ? a : CHECK(arguments_ty, _PyPegen_empty_arguments(p)),
            body, NULL, NULL, NULL, NULL, EXTRA))) }
    | 'def' '(' a=[params] ')' ':' body=block {
        (asdl_stmt_seq*)_PyPegen_singleton_seq(p, CHECK(stmt_ty, _PyAST_FunctionDef(
            _PyPegen_new_identifier(p, "<lambda>"),
            (a) ? a : CHECK(arguments_ty, _PyPegen_empty_arguments(p)),
            body, NULL, NULL, NULL, NULL, EXTRA))) }
```

Подключение: в `statement` — между `compound_stmt` и `simple_stmts`:

```
    | a[asdl_stmt_seq*]=funnypy_lambda_stmt { _PyPegen_register_stmts(p, a) }
```

и в `statement_newline` (REPL) — после `single_compound_stmt`:

```
    | a[asdl_stmt_seq*]=funnypy_lambda_stmt NEWLINE { a }
```

### 2.3. Проверка

```bash
make regen-pegen && make -j$(nproc)
```

Работает: генератор (`yield` внутри), `fib.__name__ == 'fib'`, return-позиция,
замыкание, однострочное тело, параметры с аннотациями/defaults, метод в классе.
SyntaxError: лямбда аргументом/в коллекции, `a = b = def(...)`, `obj.attr =
def(...)`, `async def(...)`. Тесты: `Lib/test/test_funnypy_lambda.py` (14).

## Фаза 3. `match pattern = value` — выполнено

### 3.1. Хелпер

`Parser/action_helpers.c`:

```c
/* Funny Python: `match PATTERN = value` desugars into
   `match value: case PATTERN if True: pass; case _: raise MatchError`.
   The `if True` guard is load-bearing: it makes the first case refutable for
   the compiler, so irrefutable user patterns (`match _ = ...`) stay legal
   instead of triggering "makes remaining patterns unreachable". */
stmt_ty
_PyPegen_funnypy_match_assign(Parser *p, pattern_ty pattern, expr_ty value)
{
    if (pattern == NULL || value == NULL) {
        return NULL;
    }
    int lineno = value->lineno;
    int col_offset = value->col_offset;
    int end_lineno = value->end_lineno;
    int end_col_offset = value->end_col_offset;

    stmt_ty pass_stmt = _PyAST_Pass(
        lineno, col_offset, end_lineno, end_col_offset, p->arena);
    if (pass_stmt == NULL) {
        return NULL;
    }
    asdl_stmt_seq *ok_body = (asdl_stmt_seq *)_PyPegen_singleton_seq(p, pass_stmt);
    if (ok_body == NULL) {
        return NULL;
    }
    expr_ty ok_guard = _PyAST_Constant(
        Py_True, NULL, lineno, col_offset, end_lineno, end_col_offset, p->arena);
    if (ok_guard == NULL) {
        return NULL;
    }
    match_case_ty ok_case = _PyAST_match_case(pattern, ok_guard, ok_body, p->arena);
    if (ok_case == NULL) {
        return NULL;
    }

    PyObject *exc_id = _PyPegen_new_identifier(p, "MatchError");
    if (exc_id == NULL) {
        return NULL;
    }
    expr_ty exc_name = _PyAST_Name(
        exc_id, Load, lineno, col_offset, end_lineno, end_col_offset, p->arena);
    if (exc_name == NULL) {
        return NULL;
    }
    expr_ty exc_call = _PyAST_Call(
        exc_name, NULL, NULL, lineno, col_offset, end_lineno, end_col_offset, p->arena);
    if (exc_call == NULL) {
        return NULL;
    }
    stmt_ty raise_stmt = _PyAST_Raise(
        exc_call, NULL, lineno, col_offset, end_lineno, end_col_offset, p->arena);
    if (raise_stmt == NULL) {
        return NULL;
    }
    asdl_stmt_seq *err_body = (asdl_stmt_seq *)_PyPegen_singleton_seq(p, raise_stmt);
    if (err_body == NULL) {
        return NULL;
    }
    pattern_ty wildcard = _PyAST_MatchAs(
        NULL, NULL, lineno, col_offset, end_lineno, end_col_offset, p->arena);
    if (wildcard == NULL) {
        return NULL;
    }
    match_case_ty err_case = _PyAST_match_case(wildcard, NULL, err_body, p->arena);
    if (err_case == NULL) {
        return NULL;
    }

    asdl_seq *err_cases = _PyPegen_singleton_seq(p, err_case);
    if (err_cases == NULL) {
        return NULL;
    }
    asdl_match_case_seq *cases = (asdl_match_case_seq *)_PyPegen_seq_insert_in_front(
        p, ok_case, err_cases);
    if (cases == NULL) {
        return NULL;
    }
    return _PyAST_Match(
        value, cases, lineno, col_offset, end_lineno, end_col_offset, p->arena);
}
```

`Parser/pegen.h`:

```c
stmt_ty _PyPegen_funnypy_match_assign(Parser *, pattern_ty, expr_ty);
```

Примечания:
- `asdl_seq_new` из `action_helpers.c` недоступен — последовательности
  собираются через `_PyPegen_singleton_seq` + `_PyPegen_seq_insert_in_front`.
- Нулевые аргументы вызова: `_PyAST_Call(func, NULL, NULL, ...)` — штатно
  (так же делает грамматика для `f()`).

### 3.2. Грамматика

Правило — сразу после `assignment`:

```
# Funny Python: pattern destructuring `match PATTERN = value`; desugars in a
# helper into `match value: case PATTERN if True: pass; case _: raise MatchError`.
match_assign[stmt_ty]:
    | "match" pat=patterns '=' v=annotated_rhs {
        _PyPegen_funnypy_match_assign(p, pat, v) }
```

Подключение — **в двух местах** (оба обязательны):

1. `simple_stmt` — первой альтернативой (нужна для REPL, где финальный
   NEWLINE потребляет `simple_stmts`):
   ```
   simple_stmt[stmt_ty] (memo):
       | &"match" match_assign
       | assignment
   ```
   Первой, а не после `assignment`: в `assignment` есть `invalid_assignment`,
   который бросил бы SyntaxError раньше нашего правила.
2. `statement` — первой альтернативой, **до** `compound_stmt`, со съеданием
   NEWLINE:
   ```
   statement[asdl_stmt_seq*]:
       | a=match_assign NEWLINE {
           _PyPegen_register_stmts(p,
               (asdl_stmt_seq*)_PyPegen_singleton_seq(p, a)) }
       | a=compound_stmt { ... }
   ```
   Это не украшение: без него во втором проходе парсера (error-pass, где
   активны `invalid_*`-правила) разбор уходит в compound `match_stmt`, там
   `invalid_named_expression` аварийно завершает проход — и чужая
   синтаксическая ошибка репортится на строке pattern-assign с сообщением
   `cannot assign to dict literal here`. С правилом в `statement` локация
   ошибки снова верная (регрессионный тест
   `test_error_elsewhere_reported_at_its_own_line`).

### 3.3. Проверка

```bash
make regen-pegen && make -j$(nproc)
```

Работает: mapping/class/sequence/or-паттерны, `MatchError` при несовпадении,
`match _ = v` и `match (_ as y) = v` (irrefutable), однократное вычисление RHS,
`match = 1` и `match.x = 1` как раньше (R1), старая распаковка с `ValueError`,
compound `match_stmt` не изменился, REPL. Тесты:
`Lib/test/test_funnypy_pattern_assign.py` (23).

Ограничение грамматики: `match x = 5` и `match _ as y = 5` — SyntaxError
(`pattern_capture_target` запрещает NAME перед `=`); эквиваленты —
`match (x) = 5`, `match (_ as y) = 5`.

## Фаза 4. Оператор `..`: пайплайн, placeholder, pipe-match — выполнено

Спека: `specs/pipeline-operator.md`. Три роли `..` (все — парсер-десахаринг):
пайплайн значения в вызов (data-last), placeholder в аргументах
(def-лямбда или любое выражение), pipe-форма `match`.

### 4.1. Грамматика (`Grammar/python.gram`)

```
# Funny Python: `..` placeholder inside call arguments (filled by a def
# lambda or an expression following the call statement).
pipe_hole[expr_ty]:
    | '.' '.' { _PyAST_Name(_PyPegen_new_identifier(p, "<pipe>"), Load, EXTRA) }
```

`pipe_hole` добавляется альтернативой в позиционные аргументы `args`:
`starred_expression | pipe_hole | (assignment_expression | expression !':=') !'='`.

```
# Funny Python: one pipeline stage `..call(...)` with an optional filler for
# the `..` placeholder (a def lambda or an expression).  A bare stage never
# ends with ':' so that `..match:` is not mistaken for a call stage.
pipe_stage[void *]:
    | '.' '.' e=primary f=funnypy_lambda_def { _PyPegen_funnypy_stage_def(p, e, f) }
    | '.' '.' e=primary v=star_expressions { _PyPegen_funnypy_stage_expr(p, e, v) }
    | '.' '.' e=primary !':' { _PyPegen_funnypy_stage_expr(p, e, NULL) }

pipe_stages[asdl_seq*]:
    | s=pipe_stages NEWLINE e=pipe_stage { _PyPegen_seq_append_to_end(p, s, e) }
    | e=pipe_stage { _PyPegen_singleton_seq(p, e) }

# Funny Python: an expression that is guaranteed to be a call with a `..`
# placeholder (validated in the action, so that placeholder statements fail
# fast without fetching the filler on unrelated syntax errors).
hole_call_expr[expr_ty]:
    | e=star_expressions { _PyPegen_funnypy_require_hole_call(p, e) }

# Funny Python: pipeline / pipe-match / placeholder statements.
funnypy_pipe_stmt[asdl_stmt_seq*]:
    | a=star_expressions NEWLINE s=[ps=pipe_stages NEWLINE { ps }] '.' '.' "match" ':' NEWLINE INDENT cases[asdl_match_case_seq*]=case_block+ DEDENT {
        _PyPegen_funnypy_pipeline_match(p, a, s, cases) }
    | a=star_expressions NEWLINE s=pipe_stages NEWLINE {
        _PyPegen_funnypy_pipeline(p, a, s) }
    | a=star_expressions NEWLINE s=pipe_stages {
        _PyPegen_funnypy_pipeline(p, a, s) }
    | c=hole_call_expr f=funnypy_lambda_def {
        _PyPegen_funnypy_pipe_hole_def(p, c, f) }
    | c=hole_call_expr v=star_expressions NEWLINE {
        _PyPegen_funnypy_pipe_hole_expr(p, c, v) }
```

Плюс факторизация: третья альтернатива `funnypy_lambda_stmt` и filler'ы берут
общее правило `funnypy_lambda_def` (тот же FunctionDef `<lambda>`).
Подключение — `funnypy_pipe_stmt` в `statement` и в `statement_newline`
(в двух вариантах: с хвостовым NEWLINE и без — разные альтернативы
заканчиваются по-разному).

### 4.2. Хелперы (`Parser/action_helpers.c`, объявления в `Parser/pegen.h`)

- `funnypy_find_hole` — индекс единственного `Name("<pipe>")` среди ПРЯМЫХ
  позиционных аргументов вызова (−1 нет, −2 ошибка «больше одного»).
- `_PyPegen_funnypy_require_hole_call` — гейт для `hole_call_expr` (без
  него ломаются offset'ы чужих синтаксических ошибок — см. ниже).
- `_PyPegen_funnypy_stage_def` / `_PyPegen_funnypy_stage_expr` — стадия:
  замена hole на ссылку `Name("<lambda>")` (def хоистится) или на выражение.
- `funnypy_pipe_apply` — data-last: последним позиционным аргументом вызова;
  голая `Name`/`Attribute` оборачивается в вызов с одним аргументом.
- `_PyPegen_funnypy_pipeline` / `_PyPegen_funnypy_pipeline_match` — цепочка
  стадий + `Expr(result)` / `_PyAST_Match(result, cases)`; порядок
  statement'ов: сначала хоитнутые def'ы, затем итоговый.
- `_PyPegen_funnypy_pipe_hole_def` / `_PyPegen_funnypy_pipe_hole_expr` —
  hole-стейтменты (`f(..) def(): ...` / `f(..) expr`).

### 4.3. Валидация (`Python/symtable.c`)

`case Name_kind`: `Name("<pipe>")` в Load → SyntaxError
`'..' placeholder used outside a pipe placeholder position` +
`SET_ERROR_LOCATION(st->st_filename, LOCATION(e))` (без локации
`check_syntax_error` падает на `err.lineno is None`).

### 4.4. Ограничения v1 (проверено)

- def-filler — всегда последняя стадия (блок съедает финальный NEWLINE/DEDENT,
  разделителя для следующей стадии нет).
- Первая величина пайплайна — на своей строке; hole-стейтмент целиком
  завершает конструкцию.
- hole резолвится только в прямых позиционных аргументах внешнего вызова;
  вложенный и keyword-hole — compile-ошибка.
- **Терминальный REPL пайплайн не поддерживает**: `funnypy_pipe_stmt` не
  подключён в `statement_newline`, потому что интерактивный токенайзер печатает
  `... ` и блокируется на запросе токена за пределами строки — а пайплайн всегда
  смотрит на следующую строку. В лоб это дало регрессию: каждое выражение в
  REPL просило продолжение (зависал `test_cmd_line_script`).
  Файлы, `-c` и Jupyter (ячейка) работают: там парс идёт в exec-режиме, а
  последнее выражение ячейки компилируется из AST без грамматики.
- **Лямбда-присваивание в терминальном REPL требует лишнего Enter** при
  однострочном теле (`sq = def(x): return x` + Enter + пустая строка) — ровно
  как upstream-`if True: pass` (следствие ветки `funnypy_lambda_stmt NEWLINE`).

### 4.5. Тесты

`Lib/test/test_funnypy_pipe.py` (20 тестов): data-last, цепочка, bare-стадия,
pipe-match (+ стадии перед ним), def/expr-filler, hole-стейтменты, негативы
(незаполненный/вложенный/keyword/двойной hole, inline-пайплайн) и
неизменность Ellipsis/лямбд/деструктуризации.

## Фаза 5. Неявный return, expression-форма `def` и `match`-выражение

Три фичи поверх R2–R4; спеки — `specs/implicit-return.md`,
`specs/match-expression.md`, `specs/multiline-lambda.md`.

1. **R5, неявный return.** `compiler_unit.u_returns_last_expr`
   (`Python/compile.c`) + аксессоры в `pycore_compile.h` (структура
   `_PyCompiler` непрозрачна для `codegen.c`). В `codegen_function_body`
   (`Python/codegen.c`) последний `Expr` компилируется без `POP_TOP`;
   `_PyCodegen_AddReturnAtEnd` не добавляет `LOAD_CONST None`. Исключения:
   `...` — placeholder (`is_ellipsis_placeholder`), генераторы/корутины,
   docstring-only, а также `__init__`/`__exit__` (None-контракт несёт
   семантику). Патчи stdlib под None-контракт — в спеке.
2. **R6, `def(...)` в выражениях.** Правило `def_expr` в грамматике
   (`'def' '(' params ')' ':' expression` → `_PyAST_Lambda`,
   `regen-pegen`). В statement-позициях выигрывает блочная форма R2
   (`funnypy_lambda_stmt` идёт раньше в `statement`), так что
   `f = def(x): x + 1` — это `def f` + неявный return; в выражениях —
   `Lambda` (`__name__ == '<lambda>'`).
3. **R7, `match`-выражение.** Новый AST-узел `MatchExpr` в
   `Parser/Python.asdl` (`regen-ast`), правила `match_expr` и
   statement-уровневое `match_expr_stmt` (после `simple_stmts`: после
   `DEDENT` нет `NEWLINE`), кодген `codegen_match_expr` по образцу
   `codegen_match_inner` с инвариантом «на каждом пути к `end` — ровно одно
   значение», symtable и `validate_expr`. No-match → `None`.

Порт с 3.16 на 3.15: в 3.15 нет `codegen_emit_function_body` — хук сделан
inline в `codegen_function_body`; `compiler_unit` имеет дополнительное поле
`u_in_inlined_comp`; сгенерированные файлы перегенерированы туловской 3.15
(`pegen` и `Parser/asdl_c.py` под `python3.14`).

## Фаза 6. Полная регрессия (инвариант суперсета)

```bash
./python -m test -j$(nproc) -q
```

Ожидание: вся штатная `Lib/test` зелёная. Исключение — правило R5
(неявный return): тесты, кодирующие старое поведение хвостового выражения
или старый байткод, адаптированы (`test_dis`, `test_code`,
`test_sys_setprofile`, `test_interpreters/test_api`, `test_crossinterp`,
`test_imaplib`, `test_pdb`, `test.support.gc_collect`). Падение в тесте, не
связанном с новым синтаксисом/R5, — нарушение суперсета (R1), разбираться до
продолжения. Доброкачественные падения, зафиксированные на чистом дереве,
исключаются из сравнения. Новая семантика закреплена
`Lib/test/test_funnypy.py`.

## Если что-то пошло не так

- **`make regen-pegen` падает** — читать первую ошибку pegen: опечатка в
  правиле или одинарные кавычки вместо двойных у `"match"`.
- **SyntaxError на валидном примере** — проверить, что `Parser/parser.c`
  перегенерирован, и что альтернатива стоит в нужном правиле в нужной
  позиции (порядок альтернатив значим).
- **`error: implicit declaration of function 'asdl_seq_new'`** — из
  `action_helpers.c` собирать последовательности через
  `_PyPegen_singleton_seq` / `_PyPegen_seq_insert_in_front`.
- **`error: assignment to 'asdl_stmt_seq *' from incompatible pointer type`** —
  объявление в `Parser/pegen.h` не совпало с определением в
  `action_helpers.c`; `asdl_stmt_seq*` и `asdl_seq*` — разные типы, нужны
  явные касты.
- **`wildcard makes remaining patterns unreachable`** — потерян `if True`
  guard в первом case десахаринга `match_assign`.
- **Чужая ошибка репортится на строке `match ... = ...`** — потеряна
  альтернатива `match_assign NEWLINE` в `statement` (см. 3.2).
- **Segmentation fault при компиляции примера** — почти всегда NULL там, где
  ждали узел: проверить `CHECK(...)` и что все последовательности ненулевые.
- **Traceback укажет на строку синтетического `raise`** — так задумано
  (EXTRA-локации); запасной путь через отдельный AST-узел `MatchAssign`
  описан в спеке `funny-python-pattern-assign`, но в v1 не понадобился.
- **`error: syntax error near ...` при `make regen-pegen`** — разделитель
  gather не может быть токеном: `NEWLINE.pipe_stage+` не парсится, нужна
  леворекурсивная замена (см. `pipe_stages` в фазе 4).
- **Segfault, если опциональная группа из нескольких элементов без action**
  (`s=[a b]`) — pegen возвращает `dummy_name` (мусор), а не значение
  элемента: писать `s=[x=a b { x }]`.
- **`check_syntax_error` падает на `err.lineno is None`** — новая
  compile-ошибка из `symtable.c` без `SET_ERROR_LOCATION`.
- **Поехали offset'ы в `test_exceptions` на формах вроде `f{a + b + c}`** —
  новая альтернатива стейтмента успела вычитать лишний токен и сдвинула
  farthest-token; лечится гейтом `hole_call_expr`, который падает до
  чтения filler'а.
- **REPL просит продолжение (`... `) на каждом выражении / зависает тест** —
  новая альтернатива в `statement_newline` читает токен за пределами строки;
  в интерактивном токенайзере (`next_interactive` в Parser/tokenizer/reader.c)
  это печатает ps2 и блокирует ввод. Многострочный синтаксис в REPL добавлять
  нельзя — только в `statement` (файлы/exec).

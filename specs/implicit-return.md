---
id: funny-python-implicit-return
type: module-design
status: draft
title: Неявный возврат последнего выражения
parent: funny-python-architecture
---

## Responsibility

Значение последнего выражения в теле функции становится значением функции.
Покрывает требование R5 из `funny-python-goal` и является единственным
документированным исключением из суперсета R1.

## Syntax

```python
def add1(x):
    x + 1          # add1(1) == 2

f = def(x): x + 1  # statement-форма R2: FunctionDef f + неявный return
f(1)               # 2
```

## Semantics

- Неявный возврат срабатывает только для **последнего statement верхнего
  уровня** тела обычной синхронной функции и только если это выражение:
  `def f(): 1; 2` возвращает `2`, `def f(): x = 1` — `None`.
- Генераторы, корутины и `async def` не затронуты (у них собственный
  stopiteration-handler); `lambda` возвращает тело и без правила.
- Тело из одного docstring возвращает `None`.
- `...` (Ellipsis) в позиции statement — no-op placeholder, а не значение:
  `def f(): ...` возвращает `None` (иначе ломались бы заглушки вида
  `def __radd__(self, other): ...`). В теле кейса match-выражения — то же.
- `__init__` и `__exit__` исключены: Python требует `None` от `__init__`
  (`TypeError: __init__() should return None`), а truthy-результат `__exit__`
  молча подавляет исключение. Явный `return` в обоих работает как обычно;
  `lambda`-семантика (`__exit__ = lambda ...`) правилом не затрагивается.
- Явный `return` любого вида всегда побеждает: правило применяется только к
  падению в конец тела.

## Boundary

- **Механизм:** `compiler_unit.u_returns_last_expr` в `Python/compile.c`
  (аксессоры `_PyCompile_SetReturnsLastExpr` / `_PyCompile_ReturnsLastExpr` —
  `struct _PyCompiler` непрозрачен для `Python/codegen.c`). В
  `codegen_function_body` последний `Expr` компилируется без `POP_TOP`;
  `_PyCodegen_AddReturnAtEnd` в этом случае не добавляет
  `LOAD_CONST None; RETURN_VALUE`.
- **Carve-outs живут в кодогене:** `is_ellipsis_placeholder()` и вычисление
  `allow_implicit_return` по имени функции (`__init__`/`__exit__`) —
  в `Python/codegen.c`; AST/грамматика не трогаются.
- **Патчи stdlib под None-контракт** (функции, обязанные возвращать `None`,
  получили явный `return`): `contextlib._create_cb_wrapper` (иначе
  `ExitStack` глотает исключения), `cmd.Cmd.default`,
  `_pydecimal.setcontext`, `xml.etree.XMLPullParser.close`,
  `_weakrefset.{difference,intersection,symmetric_difference}_update`,
  `importlib.util._incompatible_extension_module_restrictions.__exit__`,
  `test.support.gc_collect`.
- **Адаптированные тесты** (кодировали старый байткод или старый `None`):
  `test_dis`, `test_code`, `test_sys_setprofile`,
  `test_interpreters/test_api`, `test_crossinterp` + `_code_definitions`,
  `test_imaplib`, `test_pdb`.
- **Эксплуатационное:** после правок кодогена обязательно чистить
  `__pycache__` — magic number `.pyc` не меняется, иначе тесты подхватывают
  модули, скомпилированные старым интерпретатором.
- **Известные следствия:** сторонний код, полагавшийся на неявный `None`
  (`__exit__`-классы, врапперы), может изменить поведение; это цена
  исключения из R1.
- **Тесты:** `Lib/test/test_funnypy.py` (класс `ImplicitReturnTests` и
  страховки на `__init__`/`__exit__`/`ExitStack`).

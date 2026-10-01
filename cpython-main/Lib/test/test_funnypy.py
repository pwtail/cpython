"""Tests for the funnypy language extensions:

1. the value of a trailing expression in a function body is returned implicitly;
2. ``def(x): expr`` is an anonymous function expression;
3. ``match`` can be used as an expression.
"""

import ast
import textwrap
import unittest


class ImplicitReturnTests(unittest.TestCase):

    def test_trailing_expression(self):
        def f(x):
            y = x * 2
            y + 1
        self.assertEqual(f(2), 5)

    def test_trailing_constant(self):
        def f():
            5
        self.assertEqual(f(), 5)

    def test_trailing_call(self):
        def f():
            "a string".upper()
        self.assertEqual(f(), "A STRING")

    def test_explicit_return_wins(self):
        def f(x):
            if x > 0:
                return 10
            x
        self.assertEqual(f(1), 10)
        self.assertEqual(f(-1), -1)

    def test_docstring_only_returns_none(self):
        def f():
            "docstring"
        self.assertIsNone(f())

    def test_docstring_then_expression(self):
        def f():
            "docstring"
            7
        self.assertEqual(f(), 7)

    def test_no_trailing_expression_returns_none(self):
        def f():
            x = 1
        self.assertIsNone(f())

    def test_non_final_expression_is_discarded(self):
        def f():
            1
            2
        self.assertEqual(f(), 2)

    def test_generator_unchanged(self):
        def gen(x):
            yield x
            x + 1
        self.assertEqual(list(gen(5)), [5])

    def test_async_function_unchanged(self):
        async def coro(x):
            return x + 1
        self.assertEqual(__import__("asyncio").run(coro(1)), 2)

    def test_lambda_unchanged(self):
        self.assertEqual((lambda x: x + 1)(1), 2)

    def test_nested_functions(self):
        def outer(x):
            def inner(y):
                y * 2
            inner(x) + 1
        self.assertEqual(outer(3), 7)

    def test_ellipsis_is_a_placeholder(self):
        def f():
            ...
        self.assertIsNone(f())

    def test_init_returns_none(self):
        # Python requires __init__ to return None, so it never gets the
        # implicit trailing-expression return.
        class C:
            def __init__(self):
                [1, 2, 3]
        c = C()  # would raise TypeError without the carve-out
        self.assertIsInstance(c, C)
        self.assertIsNone(C.__init__(c))

    def test_exit_does_not_suppress_implicitly(self):
        # a truthy __exit__ suppresses the exception, so the implicit
        # trailing-expression return does not apply to it.
        class CM:
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                self.cleaned = True
                [1]  # would be truthy if returned
        cm = CM()
        with self.assertRaises(ValueError):
            with cm:
                raise ValueError("boom")
        self.assertTrue(cm.cleaned)

    def test_exit_explicit_return_suppresses(self):
        class CM:
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return True
        with CM():
            raise ValueError("boom")

    def test_def_expr_is_a_lambda(self):
        # a lambda body always evaluates to a value, including '...'
        self.assertIs((def(): ...)(), Ellipsis)

    def test_exitstack_callbacks_do_not_suppress(self):
        # guard for the funnypy stdlib patch: ExitStack.callback callbacks
        # cannot suppress exceptions, even if the callback returns a value.
        import contextlib
        with self.assertRaises(ValueError):
            with contextlib.ExitStack() as stack:
                stack.callback(lambda: True)
                raise ValueError("boom")


class DefExprTests(unittest.TestCase):

    def test_basic(self):
        f = def(x): x + 1
        self.assertEqual(f(1), 2)

    def test_name(self):
        # statement position: funpy's multiline-lambda desugars to FunctionDef
        f = def(x): x
        self.assertEqual(f.__name__, "f")
        # expression position: def_expr desugars to Lambda
        self.assertEqual((def(x): x).__name__, "<lambda>")

    def test_no_arguments(self):
        self.assertEqual((def(): 42)(), 42)

    def test_defaults_varargs_kwargs(self):
        g = def(a, b=2, *args, **kw): (a, b, args, kw)
        self.assertEqual(g(1, 3, 4, k=5), (1, 3, (4,), {"k": 5}))

    def test_positional_only_and_kwonly(self):
        g = def(a, /, b, *, c): (a, b, c)
        self.assertEqual(g(1, 2, c=3), (1, 2, 3))

    def test_closure(self):
        def make(n):
            return def(x): x + n
        self.assertEqual(make(10)(1), 11)

    def test_passed_as_argument(self):
        def apply(f, x):
            return f(x)
        self.assertEqual(apply(def(x): x * 3, 4), 12)

    def test_annotations_are_accepted(self):
        f = def(x: int): x + 1
        self.assertEqual(f(1), 2)

    def test_return_annotation_not_supported_in_expr_position(self):
        # The expression form desugars to a Lambda node, which has no
        # `returns` field; a `->` return annotation is a SyntaxError here.
        # (The block form `f = def(x) -> int: ...` DOES support it -- see
        # test_funnypy_lambda.MultilineLambdaTests.test_return_annotation.)
        with self.assertRaises(SyntaxError):
            compile("list(map(def(x) -> int: x + 1, []))", "<test>", "exec")

    def test_def_statement_still_works(self):
        def named(x):
            return x
        self.assertEqual(named(1), 1)


class MatchExprTests(unittest.TestCase):

    def test_basic(self):
        s = match 2:
            case 2:
                "hello"
        self.assertEqual(s, "hello")

    def test_first_matching_case_wins(self):
        s = match 1:
            case 1:
                "one"
            case _:
                "other"
        self.assertEqual(s, "one")

    def test_no_match_raises(self):
        with self.assertRaises(MatchError):
            s = match 5:
                case 1:
                    "one"

    def test_guard(self):
        u = match (2, 3):
            case (a, b) if a < b:
                a + b
        self.assertEqual(u, 5)

    def test_captures_bind_names(self):
        match (1, 2):
            case (a, b):
                pass
            case _: pass
        self.assertEqual((a, b), (1, 2))

    def test_default_case(self):
        v = match 9:
            case 1:
                "one"
            case _:
                "default"
        self.assertEqual(v, "default")

    def test_guarded_default_raises(self):
        with self.assertRaises(MatchError):
            v = match 9:
                case 1:
                    "one"
                case _ if False:
                    "never"

    def test_body_without_trailing_expression_is_none(self):
        w = match 1:
            case 1:
                x = 42
        self.assertIsNone(w)
        self.assertEqual(x, 42)

    def test_ellipsis_case_value_is_none(self):
        s = match 1:
            case 1:
                ...
        self.assertIsNone(s)

    def test_multiple_targets(self):
        a = b = match 1:
            case 1:
                "multi"
        self.assertEqual((a, b), ("multi", "multi"))

    def test_attribute_target(self):
        class C:
            pass
        obj = C()
        obj.x = match 3:
            case 3:
                "attr"
        self.assertEqual(obj.x, "attr")

    def test_subscript_target(self):
        lst = [0]
        lst[0] = match 4:
            case 4:
                "sub"
        self.assertEqual(lst, ["sub"])

    def test_inside_function(self):
        def fn(x):
            y = match x:
                case 5:
                    "five"
            y
        self.assertEqual(fn(5), "five")
        with self.assertRaises(MatchError):
            fn(6)

    def test_nested_match_expression(self):
        s = match 1:
            case 1:
                x = match 2:
                    case 2:
                        "nested"
                x
        self.assertEqual(s, "nested")

    def test_class_pattern(self):
        class Point:
            __match_args__ = ("x", "y")

            def __init__(self, x, y):
                self.x = x
                self.y = y

        s = match Point(1, 2):
            case Point(x=1, y=y):
                y
        self.assertEqual(s, 2)

    def test_match_statement_unchanged(self):
        result = None
        match 3:
            case 3:
                result = "stmt"
            case _: pass
        self.assertEqual(result, "stmt")

    def test_match_is_still_a_valid_name(self):
        match = 10
        self.assertEqual(match + 1, 11)

    def test_ast_roundtrip(self):
        source = textwrap.dedent("""\
            s = match 2:
              case 2:
                 "hello"
            """)
        tree = ast.parse(source)
        self.assertIsInstance(tree.body[0].value, ast.MatchExpr)
        namespace = {}
        exec(compile(tree, "<test>", "exec"), namespace)
        self.assertEqual(namespace["s"], "hello")

    def test_syntax_error_without_cases(self):
        with self.assertRaises(SyntaxError):
            compile("s = match 2:\n", "<test>", "exec")


if __name__ == "__main__":
    unittest.main()

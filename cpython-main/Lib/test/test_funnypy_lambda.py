"""Tests for Funny Python multiline lambdas: ``def(params): <suite>``."""

import unittest

from test.support import check_syntax_error


class MultilineLambdaTests(unittest.TestCase):

    def test_generator_lambda(self):
        fib = def(n):
            a, b = 0, 1
            for _ in range(n):
                yield a
                a, b = b, a + b
        self.assertEqual(list(fib(7)), [0, 1, 1, 2, 3, 5, 8])

    def test_assignment_takes_target_name(self):
        fib = def(n):
            return n
        self.assertEqual(fib.__name__, "fib")
        self.assertEqual(fib(3), 3)

    def test_return_position(self):
        def apply(f, x):
            return def(y):
                return f(x) + y
        self.assertEqual(apply(abs, -3)(10), 13)
        self.assertEqual(apply(abs, -3).__name__, "<lambda>")

    def test_expression_statement(self):
        def discard():
            def(x):
                return x
            # The desugaring binds a synthetic name in the enclosing scope.
            return locals()
        scope = discard()
        self.assertIn("<lambda>", scope)
        self.assertTrue(callable(scope["<lambda>"]))

    def test_closure(self):
        def make_adder(k):
            return def(x):
                return x + k
        self.assertEqual(make_adder(100)(1), 101)

    def test_one_line_body(self):
        f = def(x): return x + 1
        self.assertEqual(f(1), 2)

    def test_assignment_rhs_on_next_line(self):
        f =
            def(x):
                return x * 2
        self.assertEqual(f.__name__, "f")
        self.assertEqual(f(21), 42)

    def test_parameters(self):
        f = def(a, /, b=2, *args, c: int = 3, **kw):
            return (a, b, args, c, kw)
        self.assertEqual(f(1, 2, 3, c=4, d=5), (1, 2, (3,), 4, {"d": 5}))

    def test_class_body(self):
        class C:
            method = def(self):
                return 42
        self.assertEqual(C().method(), 42)

    def test_body_is_a_real_block(self):
        # try/except and nested defs must work inside the suite.
        f = def(x):
            try:
                return 1 / x
            except ZeroDivisionError:
                return "inf"
        self.assertEqual(f(0), "inf")
        self.assertEqual(f(2), 0.5)


class MultilineLambdaSyntaxErrorTests(unittest.TestCase):

    def test_not_allowed_as_call_argument(self):
        check_syntax_error(self, "map(def(x): return x, [1])")

    def test_not_allowed_in_collection(self):
        check_syntax_error(self, "x = [def(a): return a]")

    def test_single_target_only(self):
        check_syntax_error(self, "a = b = def(x):\n    pass")

    def test_attribute_target_not_supported(self):
        check_syntax_error(self, "obj.attr = def(x):\n    pass")

    def test_async_not_supported(self):
        check_syntax_error(self, "async def(x):\n    pass")


if __name__ == "__main__":
    unittest.main()

"""Tests for Funny Python line continuations: next-line assignment RHS and
leading-dot continuation."""

import unittest

from test.support import check_syntax_error


class NextLineRHSTests(unittest.TestCase):

    def test_expression_on_next_line(self):
        f =
            1 + 2
        self.assertEqual(f, 3)

    def test_lambda_on_next_line(self):
        f =
            def(x):
                return x + 1
        self.assertEqual(f(4), 5)
        self.assertEqual(f.__name__, "f")


class LeadingDotTests(unittest.TestCase):

    def test_method_chain(self):
        class Box:
            def __init__(self, v=0):
                self.v = v
            def add(self, n):
                return Box(self.v + n)
        objects = Box(1)
            .add(2)
            .add(3)
        self.assertEqual(objects.v, 6)

    def test_continuation_after_expression(self):
        result = [1, 2, 3]
            .count(2)
        self.assertEqual(result, 1)

    def test_continuation_after_call(self):
        class C:
            def get(self):
                return [10, 20]
        total = C().get()
            .index(20)
        self.assertEqual(total, 1)

    def test_float_and_ellipsis_unchanged(self):
        a = 1
        b = .5
        x = ...
        self.assertEqual(b, 0.5)
        self.assertIs(x, Ellipsis)

    def test_leading_dot_requires_identifier(self):
        # `.5` and `..`/`...` at line start are not continuations.
        check_syntax_error(self, "x = 1\n    .5")


if __name__ == "__main__":
    unittest.main()

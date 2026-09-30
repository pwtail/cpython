"""Tests for Funny Python ``match def`` — a function defined by case clauses.

``match def f(x, y, ...):`` + ``case`` blocks dispatches on the tuple of the
function's positional arguments; see specs/match-def.md.
"""

import ast
import inspect
import unittest

from test.support import check_syntax_error


class MatchDefTests(unittest.TestCase):

    def test_basic_dispatch(self):
        match def mufun(x, y, z):
            case [head, *rest], y, z:
                ("first", head, rest)
            case [], y, z:
                ("empty", y, z)
        self.assertEqual(mufun([1, 2, 3], 0, 1), ("first", 1, [2, 3]))
        self.assertEqual(mufun([], 5, 6), ("empty", 5, 6))

    def test_first_matching_case_wins(self):
        match def f(x):
            case 1:
                "one"
            case _:
                "other"
        self.assertEqual(f(1), "one")
        self.assertEqual(f(2), "other")

    def test_returns_trailing_expression(self):
        match def f(x):
            case n:
                n * 2
        self.assertEqual(f(21), 42)

    def test_body_without_trailing_expression_is_none(self):
        match def f(x):
            case n:
                y = n + 1
        self.assertIsNone(f(1))

    def test_no_match_raises(self):
        match def f(x):
            case 0:
                "zero"
        with self.assertRaises(MatchError):
            f(1)

    def test_guards(self):
        match def f(x, y):
            case a, b if a < b:
                "lt"
            case a, b if a > b:
                "gt"
            case _:
                "eq"
        self.assertEqual((f(1, 2), f(3, 2), f(1, 1)), ("lt", "gt", "eq"))

    def test_guarded_default_can_raise(self):
        match def f(x):
            case n if n > 10:
                "big"
        with self.assertRaises(MatchError):
            f(1)

    def test_trailing_wildcard_is_catch_all(self):
        match def f(x):
            case 0:
                "zero"
            case _:
                "other"
        self.assertEqual(f(9), "other")

    def test_single_parameter_subject_is_not_a_tuple(self):
        match def f(x):
            case 1:
                "one"
            case [a, b]:
                (a, b)
        self.assertEqual(f(1), "one")
        self.assertEqual(f([3, 4]), (3, 4))

    def test_two_and_three_parameters(self):
        match def two(a, b):
            case 1, 2:
                "one-two"
            case a, b:
                ("capture", a, b)
        self.assertEqual(two(1, 2), "one-two")
        self.assertEqual(two(3, 4), ("capture", 3, 4))

        match def three(a, b, c):
            case _, _, _:
                (a, b, c)
        self.assertEqual(three(1, 2, 3), (1, 2, 3))

    def test_pattern_captures_and_parameters_are_in_scope(self):
        match def f(x, y):
            case [head, *rest], y:
                (x, y, head, rest)
        self.assertEqual(f([1, 2], 9), ([1, 2], 9, 1, [2]))

    def test_positional_only_parameters(self):
        match def f(x, /, y):
            case a, b:
                (a, b)
        self.assertEqual(f(1, 2), (1, 2))

    def test_defaults(self):
        match def f(x, y=10):
            case a, 10:
                ("default", a)
            case a, b:
                ("explicit", a, b)
        self.assertEqual(f(1), ("default", 1))
        self.assertEqual(f(1, 2), ("explicit", 1, 2))

    def test_annotations_preserved(self):
        class T:
            pass

        match def f(x: T, y: str = "d") -> bool:
            case a, b:
                True
        self.assertEqual(f.__annotations__,
                         {"x": T, "y": str, "return": bool})
        sig = inspect.signature(f)
        self.assertEqual(list(sig.parameters), ["x", "y"])
        self.assertIs(sig.parameters["y"].default, "d")

    def test_function_name_and_signature(self):
        match def f(x):
            case n:
                n
        self.assertEqual(f.__name__, "f")
        self.assertEqual(f(7), 7)
        self.assertEqual(list(inspect.signature(f).parameters), ["x"])

    def test_recursion(self):
        match def fib(n):
            case 0:
                0
            case 1:
                1
            case n:
                fib(n - 1) + fib(n - 2)
        self.assertEqual(fib(10), 55)

    def test_closure(self):
        def outer(k):
            match def f(x):
                case n:
                    n + k
            return f
        self.assertEqual(outer(10)(5), 15)

    def test_method_in_class(self):
        class C:
            match def m(self, x):
                case _, 0:
                    "zero"
                case _, n:
                    "n=" + str(n)
        self.assertEqual(C().m(0), "zero")
        self.assertEqual(C().m(3), "n=3")

    def test_self_is_part_of_the_subject(self):
        # The subject is every positional parameter, so a method's patterns
        # must match `self` first (here: anything) before the argument.
        class C:
            match def m(self, x):
                case 0:
                    "whole tuple is zero"
                case _, 0:
                    "x is zero"
        self.assertEqual(C().m(0), "x is zero")

    def test_class_pattern(self):
        class Point:
            __match_args__ = ("x", "y")

            def __init__(self, x, y):
                self.x, self.y = x, y

        match def f(p):
            case Point(x=0, y=yy):
                ("zero-x", yy)
            case Point(x=xx):
                ("x", xx)
        self.assertEqual(f(Point(0, 5)), ("zero-x", 5))
        self.assertEqual(f(Point(3, 5)), ("x", 3))

    def test_mapping_pattern(self):
        match def f(d):
            case {"k": v}:
                v
            case _:
                "none"
        self.assertEqual(f({"k": 42}), 42)
        self.assertEqual(f({}), "none")

    def test_only_matching_case_body_runs(self):
        calls = []

        match def f(x):
            case 1:
                calls.append("one")
                "one"
            case 2:
                calls.append("two")
                "two"
        f(2)
        self.assertEqual(calls, ["two"])

    def test_nested_in_block(self):
        if True:
            match def f(x):
                case 0:
                    "zero"
                case _:
                    "other"
        self.assertEqual(f(0), "zero")

    def test_match_error_message_is_not_silent(self):
        match def f(x):
            case 0:
                "zero"
        try:
            f(1)
        except MatchError as exc:
            self.assertIsInstance(exc, Exception)


class MatchDefAstTests(unittest.TestCase):

    def test_ast_shape(self):
        tree = ast.parse("match def f(x, y):\n    case 1, 2:\n        'a'\n")
        fn = tree.body[0]
        self.assertIsInstance(fn, ast.FunctionDef)
        self.assertEqual(fn.name, "f")
        self.assertEqual([a.arg for a in fn.args.args], ["x", "y"])
        self.assertEqual(len(fn.body), 1)
        ret = fn.body[0]
        self.assertIsInstance(ret, ast.Return)
        self.assertIsInstance(ret.value, ast.MatchExpr)
        self.assertIsInstance(ret.value.subject, ast.Tuple)
        self.assertEqual([e.id for e in ret.value.subject.elts], ["x", "y"])
        self.assertEqual(len(ret.value.cases), 1)

    def test_ast_single_parameter_subject(self):
        tree = ast.parse("match def f(x):\n    case 1:\n        'a'\n")
        subject = tree.body[0].body[0].value.subject
        self.assertIsInstance(subject, ast.Name)
        self.assertEqual(subject.id, "x")


class MatchDefSyntaxTests(unittest.TestCase):

    def test_star_args_rejected(self):
        check_syntax_error(
            self,
            "match def f(*args):\n    case _: pass\n",
            "match def requires plain positional parameters")

    def test_kwargs_rejected(self):
        check_syntax_error(
            self,
            "match def f(**kw):\n    case _: pass\n",
            "match def requires plain positional parameters")

    def test_keyword_only_rejected(self):
        check_syntax_error(
            self,
            "match def f(*, k):\n    case _: pass\n",
            "match def requires plain positional parameters")

    def test_no_parameters_rejected(self):
        check_syntax_error(
            self,
            "match def f():\n    case _: pass\n",
            "match def requires at least one parameter")

    def test_requires_at_least_one_case(self):
        check_syntax_error(self, "match def f(x):\n    pass\n")

    def test_missing_body(self):
        check_syntax_error(self, "match def f(x):\n")


class MatchDefRegressionTests(unittest.TestCase):

    def test_match_def_expression_subject_still_parses(self):
        # `match def(x): expr:` is a match statement whose subject is a
        # def-expression (R6); it must not be taken for a `match def` header.
        result = []
        match def(x): x + 1:
            case f:
                result.append(f(41))
        self.assertEqual(result, [42])

    def test_match_is_still_a_valid_name(self):
        match = 10
        self.assertEqual(match + 1, 11)

    def test_match_attribute(self):
        class Box:
            pass

        b = Box()
        b.match = 2
        self.assertEqual(b.match, 2)

    def test_plain_match_statement_unchanged(self):
        result = []
        match 3:
            case 3:
                result.append("three")
            case _: pass
        self.assertEqual(result, ["three"])

    def test_pattern_assign_still_works(self):
        match [first, *rest] = [1, 2, 3]
        self.assertEqual((first, rest), (1, [2, 3]))

    def test_match_expression_still_works(self):
        s = match 2:
            case 2:
                "hello"
        self.assertEqual(s, "hello")


if __name__ == "__main__":
    unittest.main()

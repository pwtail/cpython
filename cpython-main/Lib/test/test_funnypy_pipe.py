"""Tests for the Funny Python `..` operator: pipeline, placeholder, pipe-match,
and the implicit block argument `callable def(): …`."""

import textwrap
import unittest

from test.support import check_syntax_error


class PipelineTests(unittest.TestCase):

    def test_pipeline_data_last(self):
        square = def(x): return x ** 2
        [1, 2, 3]
        ..map(square)
        ..list
        ..print
        # map(square, [1, 2, 3]) -> list -> print  (prints "[1, 4, 9]")
        self.assertEqual(list(map(square, [1, 2, 3])), [1, 4, 9])

    def test_chain(self):
        inc = def(x): return x + 1
        dbl = def(x): return x * 2
        [1, 2, 3]
        ..map(inc)
        ..map(dbl)
        ..list
        ..print
        self.assertEqual([dbl(inc(v)) for v in [1, 2, 3]], [4, 6, 8])

    def test_bare_stage_is_called(self):
        [1, 2, 3]
        ..len
        ..print
        self.assertEqual(len([1, 2, 3]), 3)

    def test_bare_stage_def_filler(self):
        # `..name def(x): ...` is `name(def, value)` (data-last).
        out = []
        for_each = def(f, data):
            for x in data:
                f(x)
        [1, 2, 3]
        ..for_each def(x): out.append(x * 10)
        self.assertEqual(out, [10, 20, 30])

    def test_bare_stage_def_filler_next_line(self):
        out = []
        for_each = def(f, data):
            for x in data:
                f(x)
        [1, 2, 3]
        ..for_each
            def(x): out.append(x * 10)
        self.assertEqual(out, [10, 20, 30])

    def test_hole_filler_next_line(self):
        seen = []
        show3 = def(a, b, f, data):
            seen.append((f(0), a, b, list(data)))
        [1]
        ..show3(10, 20, ..)
            def(x): return x + 100
        self.assertEqual(seen, [(100, 10, 20, [1])])

    def test_pipeline_result_in_expression_statement(self):
        # A pipeline is an expression statement: its value is discarded in a
        # script, displayed in the REPL/notebook.
        [1, 2]
        ..sum
        ..print  # prints 3


class PipeMatchTests(unittest.TestCase):

    def test_pipe_match(self):
        out = []
        x = 2
        x
        ..match:
            case 1:
                out.append("one")
            case 2:
                out.append("two")
        self.assertEqual(out, ["two"])

    def test_stages_before_match(self):
        out = []
        square = def(x): return x ** 2
        [1, 2, 3]
        ..map(square)
        ..match:
            case m:
                out.append(list(m))
        self.assertEqual(out, [[1, 4, 9]])

    def test_bare_match_as_stage_is_a_call(self):
        # `x ..match` without ':' is a call of the builtin/name `match`.
        out = []
        match = lambda v: out.append(("called", v))
        5
        ..match
        self.assertEqual(out, [("called", 5)])


class PlaceholderTests(unittest.TestCase):

    def test_def_filler(self):
        seen = []
        run = def(f): return f()
        run(..) def():
            seen.append("Running")
        self.assertEqual(seen, ["Running"])

    def test_def_filler_takes_arguments(self):
        square = def(x): return x ** 2
        self.assertEqual(square(4), 16)
        # `f(..) def(...)` hoists the def as <lambda> and substitutes it.
        apply5 = def(f): return f(5)
        apply5(..) def(x):
            return x * 10

    def test_expr_filler(self):
        seen = []
        show = def(v): seen.append(v)
        show(..) 2 + 3
        self.assertEqual(seen, [5])

    def test_pipe_stage_def_filler(self):
        out = []
        for_each = def(f, data):
            for x in data:
                f(x)
        [1, 2, 3]
        ..for_each(..) def(x):
            out.append(x ** 2)
        self.assertEqual(out, [1, 4, 9])

    def test_pipe_stage_expr_filler(self):
        seen = []
        show2 = def(f, tag): seen.append((tag, f))
        [1]
        ..show2(..) "tag"
        # hole <- "tag", piped value appended last: show2("tag", [1])
        self.assertEqual(seen, [([1], "tag")])


class BlockArgumentTests(unittest.TestCase):
    """`callable def(): …` — a block def as an implicit call argument."""

    def test_statement_without_placeholder(self):
        seen = []
        run = def(f): f()
        run def():
            seen.append("Running")
        self.assertEqual(seen, ["Running"])

    def test_equivalent_to_explicit_hole(self):
        seen = []
        run = def(f): f()
        run(..) def():
            seen.append("explicit")
        run def():
            seen.append("implicit")
        self.assertEqual(seen, ["explicit", "implicit"])

    def test_one_line_body(self):
        seen = []
        run = def(f): f()
        run def(): seen.append("one")
        self.assertEqual(seen, ["one"])

    def test_attribute_callee(self):
        seen = []

        class Runner:
            def go(self, f):
                return f()

        Runner().go def():
            seen.append("attr")
        self.assertEqual(seen, ["attr"])

    def test_subscript_callee(self):
        seen = []
        table = {"run": def(f): f()}
        table["run"] def():
            seen.append("sub")
        self.assertEqual(seen, ["sub"])

    def test_def_takes_arguments(self):
        apply5 = def(f): f(5)
        res = apply5 def(x):
            return x * 10
        self.assertEqual(res, 50)

    def test_assign_rhs(self):
        seen = []
        run = def(f): f()
        res = run def():
            seen.append("R")
            return 42
        self.assertEqual((res, seen), (42, ["R"]))

    def test_assign_rhs_with_hole(self):
        call2 = def(f, x, y): f(x, y)
        res = call2(.., 1, 2) def(a, b):
            return a + b
        self.assertEqual(res, 3)

    def test_implicit_return_of_call_result(self):
        # The desugared Expr is the last statement of `outer`, so R5 returns
        # the call's value.
        run = def(f): f()

        def outer():
            run def(): return 7

        self.assertEqual(outer(), 7)

    def test_def_on_next_line_is_two_statements(self):
        # `run` and the anonymous `def(): …` are separate statements: R1.
        seen = []
        run = def(f): seen.append(("called", f))
        run
        def(): seen.append("body")
        self.assertEqual(seen, [])

    def test_assign_rhs_on_next_line_is_two_statements(self):
        f = def(): return 1
        value = f
        def(): pass
        self.assertIs(value, f)


class PipeSyntaxErrorTests(unittest.TestCase):

    def test_unfilled_placeholder_in_assignment(self):
        check_syntax_error(self, "x = f(..)")

    def test_unfilled_placeholder_alone(self):
        check_syntax_error(self, "f(..)")

    def test_nested_placeholder(self):
        check_syntax_error(self, "print(g(..))")

    def test_keyword_placeholder(self):
        check_syntax_error(self, "f(init=..)")

    def test_two_placeholders(self):
        check_syntax_error(self, "f(.., ..)")

    def test_inline_pipeline_rejected(self):
        check_syntax_error(self, "x .. f(y)")

    def test_def_filler_on_call_without_hole(self):
        check_syntax_error(self, "[1]\n..map(len) def(x): return x")

    def test_def_filler_on_call_with_args(self):
        check_syntax_error(self, "f(1) def(): pass")

    def test_def_filler_on_call_with_args_rhs(self):
        check_syntax_error(self, "x = f(1) def(): pass")

    def test_def_filler_on_nested_placeholder(self):
        check_syntax_error(self, "print(f(..)) def(): pass")

    def test_two_placeholders_with_def(self):
        check_syntax_error(self, "f(.., ..) def(): pass")

    def test_ellipsis_call_unchanged(self):
        compile("f(...)", "<t>", "exec")

    def test_lambda_and_match_assign_unchanged(self):
        compile("fib = def(n): return n", "<t>", "exec")
        compile("match {'x': x} = {'x': 1}", "<t>", "exec")
        compile("match = 1", "<t>", "exec")


if __name__ == "__main__":
    unittest.main()

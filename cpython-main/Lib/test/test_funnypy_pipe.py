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
            case _: pass
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


class BlockFillerTests(unittest.TestCase):
    """if/with/try expressions as `..` fillers (R8)."""

    def test_hole_call_try_filler(self):
        seen = []
        take = def(v): seen.append(v)
        take(..) try:
            "ok"
        except:
            "exc"
        self.assertEqual(seen, ["ok"])

    def test_hole_call_try_except_path(self):
        seen = []
        take = def(v): seen.append(v)
        take(..) try:
            raise ValueError("boom")
        except ValueError:
            "caught"
        self.assertEqual(seen, ["caught"])

    def test_hole_call_if_filler(self):
        seen = []
        take = def(v): seen.append(v)
        take(..) if True:
            "yes"
        else:
            "no"
        self.assertEqual(seen, ["yes"])

    def test_hole_call_with_filler(self):
        seen = []
        take = def(v): seen.append(v)
        take(..) with open("/dev/null"):
            "read"
        self.assertEqual(seen, ["read"])

    def test_stage_try_filler_is_data_last(self):
        seen = []
        take = def(v, data): seen.append((v, data))
        5
        ..take(..) try:
            "stage"
        except:
            "e"
        self.assertEqual(seen, [("stage", 5)])

    def test_stage_if_filler(self):
        seen = []
        take = def(v, data): seen.append((v, data))
        7
        ..take(..) if True:
            "yes"
        else:
            "no"
        self.assertEqual(seen, [("yes", 7)])

    def test_stage_with_filler(self):
        seen = []
        take = def(v, data): seen.append((v, data))
        9
        ..take(..) with open("/dev/null"):
            "w"
        self.assertEqual(seen, [("w", 9)])

    def test_stage_try_exception_path_filler(self):
        seen = []
        take = def(v, data): seen.append((v, data))
        5
        ..take(..) try:
            raise ValueError("boom")
        except ValueError:
            "caught"
        self.assertEqual(seen, [("caught", 5)])

    def test_hole_call_block_filler_in_function(self):
        seen = []

        def f(x):
            take = def(v): seen.append(v)
            take(..) try:
                x + 1
            except:
                "bad"

        f(1)
        self.assertEqual(seen, [2])

    def test_hole_call_with_suppressing_filler(self):
        seen = []
        take = def(v): seen.append(v)

        class Suppress:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return True

        take(..) with Suppress():
            raise ValueError("boom")
        self.assertEqual(seen, [None])


class PipeValueTests(unittest.TestCase):
    """`..` pipelines on a def block, a block expression or a plain expression,
    including the assignment form `res =` + value + stages."""

    def test_def_block_value(self):
        # res =\n    def():\n        42\n    ..run()
        #   == res = run(def(): 42) == 42
        run = def(f): return f()
        res =
            def():
                42
            ..run()
        self.assertEqual(res, 42)

    def test_equivalent_to_block_argument(self):
        run = def(f): return f()
        res =
            def():
                42
            ..run()
        res2 = run def():
            return 42
        self.assertEqual(res, res2)

    def test_def_block_value_standalone(self):
        seen = []
        run = def(f): f()
        def():
            seen.append("ran")
        ..run()
        self.assertEqual(seen, ["ran"])

    def test_def_block_value_takes_arguments(self):
        apply5 = def(f): return f(5)
        res =
            def(x):
                return x * 10
            ..apply5()
        self.assertEqual(res, 50)

    def test_plain_expression_value(self):
        double = def(x): x * 2
        res =
            21
            ..double()
        self.assertEqual(res, 42)

    def test_bare_stage(self):
        # A bare stage with no arguments is `str(value)`.
        res =
            5
            ..str
        self.assertEqual(res, "5")

    def test_multiple_stages(self):
        inc = def(x): x + 1
        dbl = def(x): x * 2
        res =
            5
            ..inc()
            ..dbl()
        self.assertEqual(res, 12)

    def test_block_expression_match(self):
        res =
            match 5:
                case x:
                    x * 2
            ..str()
        self.assertEqual(res, "10")

    def test_block_expression_if(self):
        res =
            if True:
                1
            else:
                2
            ..str()
        self.assertEqual(res, "1")

    def test_block_expression_with(self):
        res =
            with open("/dev/null"):
                "x"
            ..str()
        self.assertEqual(res, "x")

    def test_block_expression_try(self):
        res =
            try:
                "ok"
            except ValueError:
                "bad"
            ..str()
        self.assertEqual(res, "ok")

    def test_multiple_targets(self):
        run = def(f): return f()
        a = b =
            def():
                7
            ..run()
        self.assertEqual((a, b), (7, 7))

    def test_def_block_value_is_hoisted(self):
        # The def block is hoisted as a <lambda> FunctionDef before the
        # assignment it feeds.
        names = []
        spy = def(f):
            names.append(f.__name__)
            return f()
        res =
            def():
                42
            ..spy()
        self.assertEqual((res, names), (42, ["<lambda>"]))

    def test_pipeline_result_is_implicit_return(self):
        run = def(f): return f()

        def outer():
            def():
                7
            ..run()

        self.assertEqual(outer(), 7)


class BlockValueStandaloneTests(unittest.TestCase):
    """A `..` pipeline can follow a block expression (`match`/`if`/`with`/
    `try`) at statement level."""

    def test_match_value(self):
        seen = []
        show = def(v): seen.append(v)
        match 5:
            case x:
                x * 2
        ..show()
        self.assertEqual(seen, [10])

    def test_if_else_value(self):
        seen = []
        show = def(v): seen.append(v)
        if False:
            1
        else:
            2
        ..show()
        self.assertEqual(seen, [2])

    def test_if_elif_else_value(self):
        seen = []
        show = def(v): seen.append(v)
        if False:
            1
        elif True:
            2
        else:
            3
        ..show()
        self.assertEqual(seen, [2])

    def test_try_value(self):
        seen = []
        show = def(v): seen.append(v)
        try:
            5
        except ValueError:
            "bad"
        ..show()
        self.assertEqual(seen, [5])

    def test_try_except_path(self):
        seen = []
        show = def(v): seen.append(v)
        try:
            raise ValueError("boom")
        except ValueError:
            7
        ..show()
        self.assertEqual(seen, [7])

    def test_with_value(self):
        seen = []
        show = def(v): seen.append(v)
        with open("/dev/null") as fh:
            "read"
        ..show()
        self.assertEqual(seen, ["read"])

    def test_chained_stages(self):
        inc = def(x): x + 1
        seen = []
        show = def(v): seen.append(v)
        match 5:
            case x:
                x * 2
        ..inc()
        ..show()
        self.assertEqual(seen, [11])

    def test_multi_statement_body_takes_trailing_expression(self):
        seen = []
        show = def(v): seen.append(v)
        if True:
            unused = 1
            unused + 41
        ..show()
        self.assertEqual(seen, [42])

    def test_data_last_order(self):
        out = []
        record = def(tag, v): out.append((tag, v))
        if True:
            "yes"
        ..record("tag")
        self.assertEqual(out, [("tag", "yes")])


class BlockValueStandaloneSyntaxTests(unittest.TestCase):
    """`for`/`while`/`def` produce no value, and malformed blocks keep their
    statement-specific errors (the invalid rules are mirrored)."""

    def test_for_is_not_a_value(self):
        check_syntax_error(self, "for x in [1]:\n    x\n..print()")

    def test_while_is_not_a_value(self):
        check_syntax_error(self, "while False:\n    1\n..print()")

    def test_def_is_not_a_value(self):
        check_syntax_error(self, "def f():\n    1\n..print()")

    def test_class_is_not_a_value(self):
        check_syntax_error(self, "class C:\n    pass\n..print()")

    def test_missing_block_messages_are_specific(self):
        cases = [
            ('if True:\nprint "No indent"',
             "expected an indented block after 'if' statement on line 1"),
            ('if False:\n    1\nelif True:\nprint(1)',
             "expected an indented block after 'elif' statement on line 3"),
            ('with open("/dev/null"):\nprint(1)',
             "expected an indented block after 'with' statement on line 1"),
            ('try:\nprint(1)',
             "expected an indented block after 'try' statement on line 1"),
            ('match 1:\nprint(1)',
             "expected an indented block after 'match' statement on line 1"),
        ]
        for src, msg in cases:
            with self.subTest(src=src):
                check_syntax_error(self, src, msg)


class PipeSyntaxErrorTests(unittest.TestCase):

    def test_stage_after_block_filler_rejected(self):
        # A block filler (like a def-filler) consumes the trailing NEWLINE,
        # so no further stage can follow it.
        check_syntax_error(self, textwrap.dedent("""\
            5
            ..print(..) try:
                'a'
            except:
                'b'
            ..print
        """))

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

    def test_bare_match_def_is_not_a_call(self):
        # `match def` is the anonymous match-def introducer (R9).
        check_syntax_error(self, "match def(x): pass")
        check_syntax_error(self, "y = match def(x): pass")

    def test_ellipsis_call_unchanged(self):
        compile("f(...)", "<t>", "exec")

    def test_lambda_and_match_assign_unchanged(self):
        compile("fib = def(n): return n", "<t>", "exec")
        compile("match {'x': 1} case {'x': x}", "<t>", "exec")
        compile("match = 1", "<t>", "exec")


if __name__ == "__main__":
    unittest.main()

"""Tests for the funnypy if/with/try expressions (requirement R8).

An ``if``/``with``/``try`` used as an expression evaluates to the trailing
expression of the branch that runs (``None`` when that branch does not end in
an expression).  ``for`` and ``while`` deliberately stay statements.  The
clause keywords are siblings at statement level, so ``else``/``elif``/
``except``/``finally`` are dedented like in the statement forms.
"""

import ast
import unittest


class _CM:
    """A context manager recording its protocol calls."""

    def __init__(self, value=42):
        self.value = value
        self.exit_args = None

    def __enter__(self):
        return self.value

    def __exit__(self, *args):
        self.exit_args = args
        return False


class _SuppressingCM(_CM):
    def __exit__(self, *args):
        self.exit_args = args
        return True


class _Watch:
    def __init__(self):
        self.seen = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.seen.append(args)
        return False


class IfExprTests(unittest.TestCase):

    def test_then_branch(self):
        x = 5
        r = if x > 0:
            x * 2
        else:
            -x
        self.assertEqual(r, 10)

    def test_else_branch(self):
        x = -5
        r = if x > 0:
            x * 2
        else:
            -x
        self.assertEqual(r, 5)

    def test_elif_chain(self):
        for x, expected in [(10, "big"), (5, "small"), (-1, "neg")]:
            with self.subTest(x=x):
                r = if x > 7:
                    "big"
                elif x > 0:
                    "small"
                else:
                    "neg"
                self.assertEqual(r, expected)

    def test_no_else_not_taken_is_none(self):
        r = if False:
            "taken"
        self.assertIsNone(r)

    def test_non_expression_branch_is_none(self):
        r = if True:
            x = 1
        self.assertIsNone(r)
        self.assertEqual(x, 1)

    def test_ellipsis_branch_is_none(self):
        r = if True:
            ...
        else:
            "no"
        self.assertIsNone(r)

    def test_inline_body(self):
        r = if True: "inline"
        self.assertEqual(r, "inline")

    def test_nested_via_assignment(self):
        a = True
        b = False
        inner = if b:
            "ab"
        else:
            "a-not-b"
        r = if a:
            inner
        else:
            "not-a"
        self.assertEqual(r, "a-not-b")

    def test_nested_bare_if_is_a_statement(self):
        # As in a function body (R5), only a trailing *expression* provides a
        # branch value: a bare nested `if` is a statement, so the value is
        # None.  Nest block expressions through an assignment.
        a = True
        b = False
        r = if a:
            if b:
                "ab"
            else:
                "a-not-b"
        else:
            "not-a"
        self.assertIsNone(r)

    def test_multiple_targets(self):
        a = b = if True:
            "both"
        else:
            "no"
        self.assertEqual((a, b), ("both", "both"))

    def test_attribute_and_subscript_targets(self):
        class Box:
            pass
        box = Box()
        box.v = if True:
            "attr"
        else:
            "no"
        d = {}
        d["k"] = if False:
            "no"
        else:
            "sub"
        self.assertEqual((box.v, d["k"]), ("attr", "sub"))

    def test_in_function_with_closure(self):
        def f(n):
            m = n + 1
            r = if m > 2:
                m * 10
            else:
                0
            return r
        self.assertEqual(f(2), 30)
        self.assertEqual(f(-5), 0)

    def test_test_expression_evaluated_once(self):
        calls = []

        def probe():
            calls.append(1)
            return True
        r = if probe():
            "yes"
        else:
            "no"
        self.assertEqual(r, "yes")
        self.assertEqual(len(calls), 1)

    def test_statement_if_unchanged(self):
        out = []
        if True:
            out.append(1)
        else:
            out.append(2)
        self.assertEqual(out, [1])

    def test_ternary_unchanged(self):
        self.assertEqual((1 if True else 2), 1)
        self.assertEqual((1 if False else 2), 2)


class WithExprTests(unittest.TestCase):

    def test_body_value(self):
        r = with _CM() as v:
            v + 1
        self.assertEqual(r, 43)

    def test_as_binding_is_visible_in_body(self):
        r = with _CM(10) as v:
            v
        self.assertEqual(r, 10)

    def test_without_as(self):
        r = with _CM():
            7
        self.assertEqual(r, 7)

    def test_multiple_items(self):
        a = _CM(1)
        b = _CM(2)
        r = with (a as x, b as y):
            x + y
        self.assertEqual(r, 3)

    def test_parenthesized_single_item(self):
        r = with (_CM(3) as v):
            v * 2
        self.assertEqual(r, 6)

    def test_exit_called_with_nones(self):
        cm = _CM()
        r = with cm:
            "value"
        self.assertEqual(r, "value")
        self.assertEqual(cm.exit_args, (None, None, None))

    def test_suppressed_exception_is_none(self):
        r = with _SuppressingCM():
            raise ValueError("boom")
        self.assertIsNone(r)

    def test_unhandled_exception_propagates(self):
        with self.assertRaises(ValueError):
            with _CM():
                raise ValueError("boom")

    def test_exit_sees_exception(self):
        watch = _Watch()
        with self.assertRaises(KeyError):
            r = with watch:
                raise KeyError("k")
        self.assertEqual(len(watch.seen), 1)
        self.assertIs(watch.seen[0][0], KeyError)

    def test_non_expression_body_is_none(self):
        r = with _CM():
            x = 1
        self.assertIsNone(r)
        self.assertEqual(x, 1)

    def test_nested_via_assignment(self):
        r = with _CM(1) as a:
            inner = with _CM(2) as b:
                a + b
            inner
        self.assertEqual(r, 3)

    def test_inside_function(self):
        def f(v):
            r = with _CM(v) as x:
                x * 2
            return r
        self.assertEqual(f(5), 10)

    def test_break_inside_body(self):
        seen = None
        for i in (1, 2, 3):
            seen = i
            r = with _CM(i):
                if i == 2:
                    break
                i * 10
        self.assertEqual(seen, 2)

    def test_statement_with_unchanged(self):
        cm = _CM()
        with cm as v:
            out = v
        self.assertEqual(out, 42)
        self.assertEqual(cm.exit_args, (None, None, None))


class TryExprTests(unittest.TestCase):

    def test_try_body_value(self):
        r = try:
            "ok"
        except ValueError:
            "bad"
        self.assertEqual(r, "ok")

    def test_except_value(self):
        r = try:
            raise ValueError("boom")
        except ValueError:
            "caught"
        self.assertEqual(r, "caught")

    def test_else_value_overrides_try_body(self):
        r = try:
            "body"
        except ValueError:
            "exc"
        else:
            "else"
        self.assertEqual(r, "else")

    def test_else_skipped_on_exception(self):
        r = try:
            raise ValueError("boom")
        except ValueError:
            "exc"
        else:
            "else"
        self.assertEqual(r, "exc")

    def test_finally_value_is_discarded(self):
        r = try:
            "tryval"
        finally:
            "finallyval"
        self.assertEqual(r, "tryval")

    def test_except_and_finally(self):
        r = try:
            raise KeyError("k")
        except KeyError:
            "k"
        finally:
            "fin"
        self.assertEqual(r, "k")

    def test_else_and_finally(self):
        r = try:
            "body"
        except ValueError:
            "exc"
        else:
            "else"
        finally:
            "fin"
        self.assertEqual(r, "else")

    def test_finally_only(self):
        r = try:
            "body"
        finally:
            pass
        self.assertEqual(r, "body")

    def test_finally_runs(self):
        ran = []
        r = try:
            "body"
        finally:
            ran.append(True)
        self.assertEqual(r, "body")
        self.assertEqual(ran, [True])

    def test_as_name_is_deleted(self):
        r = try:
            raise ValueError("v")
        except ValueError as e:
            str(e)
        self.assertEqual(r, "v")
        self.assertNotIn("e", dir())

    def test_multiple_handlers(self):
        r = try:
            raise TypeError()
        except ValueError:
            "v"
        except TypeError:
            "t"
        except:
            "other"
        self.assertEqual(r, "t")

    def test_bare_except(self):
        r = try:
            raise RuntimeError()
        except:
            "any"
        self.assertEqual(r, "any")

    def test_unhandled_exception_propagates(self):
        with self.assertRaises(ZeroDivisionError):
            r = try:
                1 / 0
            except ValueError:
                "no"

    def test_non_expression_branch_is_none(self):
        r = try:
            x = 1
        except ValueError:
            "no"
        self.assertIsNone(r)
        self.assertEqual(x, 1)

    def test_return_in_except(self):
        def f():
            r = try:
                raise ValueError()
            except ValueError:
                return "excret"
        self.assertEqual(f(), "excret")

    def test_return_expression_in_except(self):
        def f():
            r = try:
                raise ValueError()
            except ValueError:
                return 1 + 1
        self.assertEqual(f(), 2)

    def test_return_in_finally(self):
        def f():
            r = try:
                "body"
            finally:
                return "finret"
        self.assertEqual(f(), "finret")

    def test_return_expression_in_finally(self):
        def f():
            r = try:
                "body"
            finally:
                return 1 + 1
        self.assertEqual(f(), 2)

    def test_break_in_finally(self):
        def f(seq):
            for i in seq:
                r = try:
                    i
                finally:
                    if i == 2:
                        break
            return i
        self.assertEqual(f([1, 2, 3]), 2)

    def test_continue_in_finally(self):
        def f(seq):
            out = []
            for i in seq:
                r = try:
                    i
                finally:
                    if i == 2:
                        continue
                out.append(r)
            return out
        self.assertEqual(f([1, 2, 3]), [1, 3])

    def test_exception_in_finally_propagates(self):
        with self.assertRaises(RuntimeError):
            r = try:
                "body"
            finally:
                raise RuntimeError("fin")

    def test_nested(self):
        inner = try:
            1
        except:
            2
        outer = try:
            inner + 10
        except:
            0
        self.assertEqual(outer, 11)

    def test_combined_with_if_and_with(self):
        r = with _CM(1) as x:
            inner = try:
                x + 1
            except:
                "bad"
            inner
        self.assertEqual(r, 2)

    def test_combined_if_inside_try(self):
        r = try:
            v = if True:
                1
            else:
                2
            v
        except:
            0
        self.assertEqual(r, 1)

    def test_generator(self):
        def gen():
            r = try:
                1
            except:
                2
            yield r
            yield 3
        self.assertEqual(list(gen()), [1, 3])

    def test_implicit_return_of_assigned_try_expr(self):
        def f():
            r = try:
                "implicit"
            except:
                "no"
            r
        self.assertEqual(f(), "implicit")

    def test_bare_trailing_try_is_a_statement(self):
        # A bare trailing `try:` is a try *statement*, so R5's implicit return
        # does not apply and the function returns None.
        def f():
            try:
                "implicit"
            except:
                "no"
        self.assertIsNone(f())

    def test_statement_try_unchanged(self):
        out = []
        try:
            out.append(1)
        except ValueError:
            out.append(2)
        else:
            out.append(3)
        finally:
            out.append(4)
        self.assertEqual(out, [1, 3, 4])


class ExprAstTests(unittest.TestCase):

    def test_if_expr_node(self):
        tree = ast.parse("r = if a:\n    1\nelse:\n    2\n")
        self.assertIsInstance(tree.body[0].value, ast.IfExpr)

    def test_with_expr_node(self):
        tree = ast.parse("r = with cm():\n    1\n")
        self.assertIsInstance(tree.body[0].value, ast.WithExpr)

    def test_try_expr_node(self):
        tree = ast.parse("r = try:\n    1\nexcept:\n    2\n")
        self.assertIsInstance(tree.body[0].value, ast.TryExpr)

    def test_elif_is_nested_if_expr(self):
        tree = ast.parse("r = if a:\n    1\nelif b:\n    2\nelse:\n    3\n")
        outer = tree.body[0].value
        self.assertIsInstance(outer, ast.IfExpr)
        self.assertEqual(len(outer.orelse), 1)
        self.assertIsInstance(outer.orelse[0], ast.Expr)
        self.assertIsInstance(outer.orelse[0].value, ast.IfExpr)

    def test_if_statement_unchanged(self):
        tree = ast.parse("if a:\n    1\nelse:\n    2\n")
        self.assertIsInstance(tree.body[0], ast.If)

    def test_with_statement_unchanged(self):
        tree = ast.parse("with cm() as v:\n    1\n")
        self.assertIsInstance(tree.body[0], ast.With)

    def test_try_statement_unchanged(self):
        tree = ast.parse("try:\n    1\nexcept:\n    2\n")
        self.assertIsInstance(tree.body[0], ast.Try)

    def test_ternary_unchanged(self):
        tree = ast.parse("r = 1 if a else 2\n")
        self.assertIsInstance(tree.body[0].value, ast.IfExp)

    def test_match_as_name_is_unchanged(self):
        tree = ast.parse("match = 1\n")
        self.assertIsInstance(tree.body[0], ast.Assign)


class ExprGrammarTests(unittest.TestCase):

    def assertSyntaxError(self, source):
        with self.assertRaises(SyntaxError):
            compile(source, "<test>", "exec")

    def test_for_is_not_an_expression(self):
        self.assertSyntaxError("r = for x in y:\n    x\n")

    def test_while_is_not_an_expression(self):
        self.assertSyntaxError("r = while x:\n    1\n")

    def test_class_is_not_an_expression(self):
        self.assertSyntaxError("r = class C:\n    pass\n")

    def test_except_star_not_supported(self):
        self.assertSyntaxError("r = try:\n    1\nexcept* ValueError:\n    2\n")

    def test_async_with_not_supported(self):
        self.assertSyntaxError("r = async with cm():\n    1\n")

    def test_return_position_not_supported(self):
        self.assertSyntaxError(
            "def f():\n    return if x:\n        1\n    else:\n        2\n")

    def test_yield_position_not_supported(self):
        self.assertSyntaxError(
            "def gen():\n    yield try:\n        1\n    except:\n        2\n")

    def test_call_argument_position_not_supported(self):
        self.assertSyntaxError("f(if x:\n    1\nelse:\n    2\n)\n")

    def test_binary_operand_position_not_supported(self):
        self.assertSyntaxError("r = (if x:\n    1\nelse:\n    2\n) + 1\n")


if __name__ == "__main__":
    unittest.main()

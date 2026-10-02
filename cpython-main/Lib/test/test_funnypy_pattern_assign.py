"""Tests for Funny Python pattern destructuring: ``match VALUE case PATTERN``."""

import unittest

from test.support import check_syntax_error


class PatternAssignTests(unittest.TestCase):

    def test_mapping_pattern(self):
        match {'x': 42, 'y': 0} case {'x': x}
        self.assertEqual(x, 42)

    def test_class_pattern(self):
        class Point:
            def __init__(self, x, y):
                self.x, self.y = x, y
        match Point(1, 2) case Point(x=px, y=py)
        self.assertEqual((px, py), (1, 2))

    def test_sequence_pattern(self):
        match [1, 2, 3] case [first, *rest]
        self.assertEqual((first, rest), (1, [2, 3]))

    def test_or_pattern(self):
        match {'b': 9} case {'a': a} | {'b': a}
        self.assertEqual(a, 9)

    def test_literal_pattern_mismatch(self):
        with self.assertRaises(MatchError):
            match 2 case 1

    def test_literal_pattern_match(self):
        match 1 case 1  # no error

    def test_wildcard_is_irrefutable(self):
        match 99 case _  # must not raise "unreachable pattern"

    def test_as_pattern(self):
        match 5 case _ as y
        self.assertEqual(y, 5)

    def test_bare_capture(self):
        match 5 case x
        self.assertEqual(x, 5)

    def test_tuple_subject(self):
        match 1, 2 case p, q
        self.assertEqual((p, q), (1, 2))

    def test_rhs_evaluated_once(self):
        calls = []

        def side():
            calls.append(1)
            return {'x': 1}

        match side() case {'x': _}
        self.assertEqual(len(calls), 1)

    def test_mismatch_does_not_bind(self):
        def probe():
            try:
                match {} case {'x': x}
            except MatchError:
                pass
            return locals()
        self.assertNotIn('x', probe())

    def test_mismatch_is_catchable_in_loop(self):
        seen = []
        for value in ({'y': 1}, {'x': 2}):
            try:
                match value case {'x': x}
            except MatchError:
                seen.append(None)
            else:
                seen.append(x)
        self.assertEqual(seen, [None, 2])

    def test_body_continues_after_success(self):
        events = []
        match {'x': 1} case {'x': _}
        events.append("after")
        self.assertEqual(events, ["after"])


class PatternAssignSupersetTests(unittest.TestCase):
    """Existing syntax must keep its old meaning (invariant R1)."""

    def test_soft_keyword_still_a_name(self):
        match = 1
        self.assertEqual(match, 1)

    def test_attribute_assignment_on_name_match(self):
        class NS:
            pass
        match = NS()
        match.x = 5
        self.assertEqual(match.x, 5)

    def test_legacy_unpack_unchanged(self):
        a, b = 'ab'
        self.assertEqual((a, b), ('a', 'b'))
        [c, d] = [1, 2]
        self.assertEqual((c, d), (1, 2))
        with self.assertRaises(ValueError):
            e, f = [1]

    def test_pattern_semantics_differ_from_unpack(self):
        # A sequence pattern rejects str, plain unpacking accepts it.
        with self.assertRaises(MatchError):
            match 'ab' case [a, b]

    def test_compound_match_unchanged(self):
        def classify(v):
            match v:
                case 1:
                    return "one"
                case {'k': k}:
                    return f"k={k}"
                case _: pass
            return "other"
        self.assertEqual(classify(1), "one")
        self.assertEqual(classify({'k': 7}), "k=7")
        self.assertEqual(classify(2), "other")


class PatternAssignSyntaxErrorTests(unittest.TestCase):

    def test_old_equals_syntax_rejected(self):
        # `match PATTERN = value` was replaced by `match VALUE case PATTERN`.
        check_syntax_error(self, "match x = 5")
        check_syntax_error(self, "match {'x': x} = {'x': 1}")

    def test_guard_not_supported(self):
        check_syntax_error(self, "match d case {'x': x} if x")

    def test_error_elsewhere_reported_at_its_own_line(self):
        # Regression: the error-recovery pass must not blame the (valid)
        # pattern-assign statement for a syntax error further down.
        src = "match {'x': 1} case {'x': x}\ndef broken(:\n    pass\n"
        with self.assertRaises(SyntaxError) as cm:
            compile(src, "<t>", "exec")
        self.assertEqual(cm.exception.lineno, 2)

    def test_missing_pattern(self):
        check_syntax_error(self, "match {'x': 1} case")

    def test_missing_value(self):
        check_syntax_error(self, "match case {'x': x}")


if __name__ == "__main__":
    unittest.main()

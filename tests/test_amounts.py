"""Tests for the front-end amount parser (static/app.js: evalAmount).

Money boxes accept pasted "$12,500" and quick math like "5200/2"; a typo
must come back as NaN (the box turns red) rather than silently as zero.
The parser is plain JS, so these tests hand it to node — they are skipped
where node isn't installed.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "static" / "app.js"


def eval_amounts(texts):
    src = APP_JS.read_text(encoding="utf-8")
    m = re.search(r"^function evalAmount\(text\) \{.*?^\}\n", src, re.S | re.M)
    assert m, "evalAmount not found in app.js"
    script = (m.group(0)
              + "const out = JSON.parse(process.argv[1]).map((t) => {"
              + "  const v = evalAmount(t);"
              + "  return Number.isNaN(v) ? 'NaN' : v; });"
              + "process.stdout.write(JSON.stringify(out));")
    res = subprocess.run(["node", "-e", script, json.dumps(texts)],
                         capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class EvalAmountTest(unittest.TestCase):
    def check(self, cases):
        got = eval_amounts([c[0] for c in cases])
        for (text, want), value in zip(cases, got):
            if want is not None and want != "NaN":
                self.assertAlmostEqual(value, want, places=6, msg=repr(text))
            else:
                self.assertEqual(value, want, repr(text))

    def test_plain_numbers_with_formatting(self):
        self.check([
            ("2600", 2600), ("2600.50", 2600.5), (".5", 0.5),
            ("12,500", 12500), ("$12,500.25", 12500.25), (" 1 234 ", 1234),
            ("-300", -300), ("+300", 300),
        ])

    def test_math(self):
        self.check([
            ("5200/2", 2600), ("2600*1.03", 2678), ("1000+250-50", 1200),
            ("2*(300+200)", 1000), ("$52,000/12", 52000 / 12),
            ("-(100)", -100), ("2*-3", -6), ("10/4*2", 5), ("1-2-3", -4),
        ])

    def test_empty_is_unset_not_zero(self):
        self.check([("", None), ("  ", None), ("$", None)])

    def test_garbage_is_flagged_not_zeroed(self):
        self.check([
            ("abc", "NaN"), ("12abc", "NaN"), ("1.2.3", "NaN"), ("5/", "NaN"),
            ("(5", "NaN"), ("5)", "NaN"), ("1/0", "NaN"), ("2**3", "NaN"),
            ("1e3", "NaN"), ("--", "NaN"), ("()", "NaN"), ("5 %", "NaN"),
        ])


if __name__ == "__main__":
    unittest.main()

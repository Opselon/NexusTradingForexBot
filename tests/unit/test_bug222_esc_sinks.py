"""BUG-222 regression probe (P1 XSS hardening, UIX program).

Fails-before / passes-after for the esc() routing of API-sourced reason
strings and error.message into innerHTML sinks in Web/app.js.

The test reads app.js as text (same approach as tests/js/*) and asserts:
1. the esc() helper is defined;
2. every innerHTML template-literal sink that interpolates an API-sourced
   string (m.reason / c.reason / ps.reason / ex.reason / ms.reason /
   lq.reason / err.message / ct.status / ct.model_status) routes the value
   through esc();
3. no innerHTML sink interpolates err.message unescaped anywhere.
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "Web" / "app.js"


def app_src() -> str:
    return APP.read_text(encoding="utf-8")


class TestBug222EscapedReasonSinks(unittest.TestCase):
    def test_esc_helper_defined(self):
        self.assertRegex(app_src(), r"function esc\(s\)")

    def test_reason_sinks_escaped(self):
        src = app_src()
        # every `...${X.reason}...` inside an innerHTML template must be esc()
        for m in re.finditer(r"\.innerHTML\s*=\s*`(?P<body>[^`]*)`", src, re.DOTALL):
            body = m.group("body")
            line = src[: m.start()].count("\n") + 1
            for interp in re.findall(r"\$\{(?P<expr>[^}]+)\}", body):
                if re.search(r"\b(reason|message)\b", interp):
                    self.assertIn(
                        "esc(",
                        interp,
                        f"app.js:{line}: unescaped API string '{interp}' into innerHTML",
                    )
                    self.assertNotIn("err.message", interp.replace("esc(err.message)", ""))

    def test_err_message_never_raw(self):
        src = app_src()
        for m in re.finditer(r"\.innerHTML\s*=\s*`(?P<body>[^`]*)`", src, re.DOTALL):
            line = src[: m.start()].count("\n") + 1
            body = m.group("body")
            # err.message interpolated without esc( wrapper
            for interp in re.findall(r"\$\{([^}]*err\.message[^}]*)\}", body):
                self.assertIn("esc(", interp, f"app.js:{line}: raw err.message into innerHTML")

    def test_status_and_model_status_escaped(self):
        src = app_src()
        # app.js debug banner interpolates ct.status / ct.model_status —
        # both must route through esc() (status values come from the API).
        for m in re.finditer(r"\.innerHTML\s*=\s*`(?P<body>[^`]*)`", src, re.DOTALL):
            body = m.group("body")
            line = src[: m.start()].count("\n") + 1
            for interp in re.findall(
                r"\$\{([^}]*\b(?:ct\.status|ct\.model_status)\b[^}]*)\}", body
            ):
                self.assertIn(
                    "esc(",
                    interp,
                    f"app.js:{line}: unescaped status value '{interp}' into innerHTML",
                )


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from psc_agent.core.env import load_env


class LoadEnv(unittest.TestCase):
    def test_reads_values_and_never_overrides_the_environment(self):
        f = Path(tempfile.mkdtemp()) / ".env"
        f.write_text('# comment\nPSC_T_A=one\nexport PSC_T_B="two words"\nPSC_T_C=\nPSC_T_KEEP=file\nnot a line\n', encoding="utf-8")
        with mock.patch.dict(os.environ, {"PSC_T_KEEP": "shell"}, clear=False):
            for k in ("PSC_T_A", "PSC_T_B", "PSC_T_C"):
                os.environ.pop(k, None)
            self.assertEqual(load_env(f, f.parent / "missing.env"), [f])
            self.assertEqual((os.environ["PSC_T_A"], os.environ["PSC_T_B"], os.environ["PSC_T_KEEP"]), ("one", "two words", "shell"))
            self.assertNotIn("PSC_T_C", os.environ)  # an empty value is not a value
            for k in ("PSC_T_A", "PSC_T_B"):
                os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()

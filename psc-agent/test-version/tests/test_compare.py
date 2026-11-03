import unittest

from psc_agent.recheck.compare import canonical_unit, compare_values, parse_quantities, unit_spec


def status(rec, ru, pv, pu, tol=0.001):
    v = compare_values(rec, ru, pv, pu, tol=tol)
    return None if v is None else v.status


class Units(unittest.TestCase):
    def test_canonical_forms(self):
        cases = {
            "mg mL-1": "mg/mL", "mg / mL": "mg/mL", "mg/ml": "mg/ml", "mAcm-2": "mA/cm2", "mA cm−2": "mA/cm2",
            "mA/cm-2": "mA/cm2", "mgL-1": "mg/L", "Ω·cm2": "Ωcm2", "Ohm cm²": "Ωcm2",
            "cm2 V-1 s-1": "cm2/Vs", "cm2/Vs": "cm2/Vs", "cm-3": "1/cm3", "°C": "C", "℃": "C", "sec": "s", "hr": "h",
            "Μl": "ul", "µL": "uL", "M (0.5 mmol in 5 mL)": "M", "mol% (Cu/Ni)": "mol%",
        }
        for raw, want in cases.items():
            self.assertEqual(canonical_unit(raw), want, raw)

    def test_specs(self):
        self.assertEqual(unit_spec("RPM")[0], "spin")
        self.assertEqual(unit_spec("mg/ml")[0], "mass_conc")
        self.assertEqual(unit_spec("mm")[0], "length")
        self.assertEqual(unit_spec("mM")[0], "molarity")
        self.assertIsNone(unit_spec("g per 10 mL"))


class Quantities(unittest.TestCase):
    def test_parse(self):
        q = parse_quantities("3000, 40", "RPM, s")
        self.assertEqual([(x.lo, x.unit) for x in q], [(3000, "RPM"), (40, "s")])
        self.assertEqual(parse_quantities("1.5 × 10⁻³", "M")[0].lo, 1.5e-3)
        self.assertEqual(parse_quantities("0.2–1.0", None)[0].hi, 1.0)
        self.assertEqual(parse_quantities("4,000", "rpm")[0].lo, 4000)
        self.assertEqual(parse_quantities("100 C", None)[0].unit, "C")
        self.assertIsNone(parse_quantities("3000 rpm for 30 s", None))
        self.assertIsNone(parse_quantities("ITO", None))
        self.assertIsNone(parse_quantities("2-propanol", None))
        self.assertIsNone(parse_quantities("50:50", None))
        self.assertIsNone(parse_quantities("3000, 40, 5", "RPM, s"))


class Compare(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(status(1.124, "V", "1.12", "V"), "match")  # rounding
        self.assertEqual(status(22.93, "%", "22.73", "%"), "mismatch")
        self.assertEqual(status(1.5, "µm", "1500", "nm"), "match")
        self.assertEqual(status(100, "C", "373", "K"), "match")
        self.assertEqual(status(1.65, "M", "1650", "mM"), "match")
        self.assertEqual(status(1.5e-3, "M", "1.5", "mM"), "match")
        self.assertEqual(status(24.01, "mA cm-2", "24.01", "mA/cm2"), "match")

    def test_multi_value_cells(self):
        self.assertEqual(status("3000, 40", "RPM, s", "3000, 40", "rpm, s"), "match")
        self.assertEqual(status("3000, 40", "RPM, s", "40, 3000", "s, rpm"), "match")
        self.assertEqual(status("120, 10", "C, min", "120, 600", "C, s"), "match")
        self.assertEqual(status("3000, 40", "RPM, s", "4000, 40", "rpm, s"), "mismatch")
        self.assertEqual(status("3000, 40", "RPM, s", "3000", "rpm"), "mismatch")

    def test_units(self):
        self.assertEqual(status(10, "mg/mL", "10", "mM"), "unit_mismatch")
        self.assertEqual(status(10, None, "10", "mg/mL"), "unit_mismatch")
        self.assertEqual(status(10, "mg/mL", "10", None), "match")
        self.assertEqual(status(10, "g per 10 mL", "10", "mg/mL"), "review")

    def test_figure_tolerance(self):
        self.assertEqual(status(85, "%", "83", "%", tol=0.05), "match")
        self.assertEqual(status(85, "%", "83", "%"), "mismatch")

    def test_ranges(self):
        self.assertEqual(status("50-60", "%", "50–60", "%"), "match")
        self.assertEqual(status("50-60", "%", "55", "%"), "review")
        self.assertEqual(status("50-60", "%", "70", "%"), "mismatch")

    def test_text_and_blanks(self):
        self.assertEqual(status("Sigma-Aldrich", None, "Sigma Aldrich", None), "match")
        self.assertEqual(status("N", None, "no", None), "match")
        v = compare_values("DI water", None, "deionized water", None, tol=0.001)
        self.assertEqual((v.status, v.judgeable), ("review", True))
        self.assertIsNone(status(None, "%", None, "%"))
        self.assertIsNone(status("N/A", None, "not reported", None))
        self.assertEqual(status(5, "nm", None, "nm"), "not_found")
        self.assertEqual(status(None, "nm", "5", "nm"), "missing_in_record")


if __name__ == "__main__":
    unittest.main()

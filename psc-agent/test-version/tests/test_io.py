import tempfile
import unittest
from pathlib import Path

from _fixtures import LAYER, PROCESS, PROPERTIES, write_pdf, write_workbook

from psc_agent import load_paper, load_workbook, pair_folders


class Workbook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = write_workbook(self.tmp / "10.1002_smtd.202300241_Sample2.xlsx",
                                   {"Layer": LAYER, "Process": PROCESS, "Properties": PROPERTIES,
                                    "NiOx synthesis": [PROCESS[0] + ("Remarks",), (2, 1, "Calcination", None, 300, "C", "air")]})

    def test_rows_keys_units(self):
        d = load_workbook(self.path)
        self.assertEqual((d.name, d.doi_raw, d.sample_no), ("10.1002_smtd.202300241_Sample2", "10.1002_smtd.202300241", 2))
        layer = d.sheets["Layer"]
        self.assertEqual([r.excel_row for r in layer], [2, 3, 4])
        self.assertEqual(layer[1].keys, {"LayerID": 2, "LayerType": "HTL", "Material": "NiOx"})
        self.assertEqual(layer[1].value_columns, ["Thickness (nm)"])
        self.assertEqual(layer[1].unit_of("Thickness (nm)"), "nm")
        spin = d.sheets["Process"][1]
        self.assertEqual(spin.value_columns, ["Descriptions", "Parameter"])
        self.assertEqual(spin.unit_of("Parameter"), "RPM, s")
        self.assertEqual(spin.unit_of("Parameter", {"Unit": "rpm, s"}), "rpm, s")
        self.assertIsNone(spin.unit_of("Parameter", {}))
        self.assertIn("Remarks", d.sheets["Properties"][0].key_columns)
        self.assertEqual(d.sheets["NiOx synthesis"][0].key_columns, ("LayerID", "StepOrder", "ProcessType", "Remarks"))
        self.assertEqual(d.layer_stack(), "Anode: ITO / HTL: NiOx")

    def test_bytes_need_name(self):
        with self.assertRaises(ValueError):
            load_workbook(self.path.read_bytes())
        self.assertEqual(load_workbook(self.path.read_bytes(), name=self.path.name).sample_no, 2)


class Pairing(unittest.TestCase):
    def test_pairs_by_wrapped_doi(self):
        tmp = Path(tempfile.mkdtemp())
        raw, pdf = tmp / "raw", tmp / "pdf"
        raw.mkdir(), pdf.mkdir()
        for name in ("10.1016_j.test.2020.123_Sample1", "10.1016_j.test.2020.123_Sample2", "10.9999_nopdf.1_Sample1",
                     "10.1016.test.2020.999_Sample1"):  # last one: "j" missing from the DOI
            write_workbook(raw / f"{name}.xlsx", {"Layer": LAYER})
        write_pdf(pdf / "7 Some title.pdf", "Journal header https://doi.org/10.1\n016/j.test.2020.123", pages=3)
        write_pdf(pdf / "7 copy.pdf", "https://doi.org/10.1016/j.test.2020.123", image=False, pages=1)
        write_pdf(pdf / "typo.pdf", "doi:10.1016/j.test.2020.999")
        write_pdf(pdf / "other.pdf", "https://doi.org/10.5555/unrelated.42")

        p = pair_folders(raw, pdf)
        self.assertEqual([(x.key, x.pdf.name, x.pdf_doi) for x in p.pairs],
                         [("101016jtest2020123", "7 Some title.pdf", None),
                          ("101016test2020999", "typo.pdf", "10.1016/j.test.2020.999")])
        pair = p.pairs[0]
        self.assertEqual(len(pair.workbooks), 2)
        self.assertEqual([d.name for d in pair.duplicate_pdfs], ["7 copy.pdf"])
        self.assertEqual(list(p.workbooks_without_pdf), ["109999nopdf1"])
        self.assertEqual([x.name for x in p.unmatched_pdfs], ["other.pdf"])

    def test_paper_images_only_figure_pages(self):
        path = write_pdf(Path(tempfile.mkdtemp()) / "x.pdf", "doi 10.1016/j.test.2020.123", pages=3)
        paper = load_paper(path)
        self.assertEqual(paper.doi, "10.1016/j.test.2020.123")
        self.assertEqual([i for i, _ in paper.page_images()], [0])
        self.assertEqual(len(paper.page_images(all_pages=True)), 3)
        self.assertTrue(paper.page_images()[0][1].startswith(b"\xff\xd8"))  # JPEG


if __name__ == "__main__":
    unittest.main()

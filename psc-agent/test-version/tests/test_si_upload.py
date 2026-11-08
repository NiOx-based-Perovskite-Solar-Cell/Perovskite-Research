"""Uploading supporting information from the web page: saved where pairing looks, listed, removed, refused when wrong."""
import io
import tempfile
import unittest
from pathlib import Path

from _fixtures import write_pdf, write_workbook

from psc_agent.web import WebConfig, create_app

KEY = "101234test5678"


class UploadSi(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "Raw Data").mkdir()
        (self.root / "original paper").mkdir()
        write_workbook(self.root / "Raw Data" / "10.1234_test.5678_Sample1.xlsx", {"Layer": [("LayerID", "LayerType", "Material"), (1, "Anode", "ITO")]})
        write_pdf(self.root / "original paper" / "paper.pdf", "doi 10.1234/test.5678 main text")
        self.si_bytes = write_pdf(self.root / "si_src.pdf", "Supporting Information. Table S1.", pages=3).read_bytes()
        app = create_app(WebConfig(root=self.root, require_key=False))
        app.testing = True
        self.c = app.test_client()

    def post(self, key, data=None, name="si.pdf"):
        return self.c.post("/api/si", data={"key": key, "files": (io.BytesIO(self.si_bytes if data is None else data), name)},
                           content_type="multipart/form-data")

    def pair(self):
        return next(p for p in self.c.get("/api/pairs").get_json()["pairs"] if p["key"] == KEY)

    def test_saved_under_the_name_pairing_looks_for(self):
        self.assertEqual(self.pair()["si"], 0)
        r = self.post(KEY)
        body = r.get_json()
        self.assertEqual((r.status_code, body["paired"], body["files"][0]["name"], body["files"][0]["pages"]), (201, True, f"{KEY}_SI.pdf", 3))
        self.assertTrue((self.root / "original paper" / "SI" / f"{KEY}_SI.pdf").is_file())
        self.assertEqual((self.pair()["si"], self.pair()["si_files"]), (1, [f"{KEY}_SI.pdf"]))  # pairing sees it at once

    def test_a_doi_is_accepted_as_the_key_and_more_files_are_numbered(self):
        self.post("10.1234/test.5678")
        second = self.post("10.1234/test.5678").get_json()
        self.assertEqual(second["files"][0]["name"], f"{KEY}_SI2.pdf")
        self.assertEqual(self.pair()["si"], 2)

    def test_the_uploaded_si_is_read_with_the_paper(self):
        from psc_agent import load_paper
        from psc_agent.sources.pairing import pair_folders

        self.post(KEY)
        (pair,) = pair_folders(self.root / "Raw Data", self.root / "original paper").pairs
        paper = load_paper(pair.pdf, si=pair.si)
        self.assertEqual(paper.si_pages, 3)
        self.assertIn("Table S1", paper.pages[-1] + paper.pages[-2] + paper.pages[-3])

    def test_a_key_for_no_known_paper_is_saved_but_flagged(self):
        body = self.post("109999unknownpaper").get_json()
        self.assertFalse(body["paired"])

    def test_refusals(self):
        self.assertEqual(self.post(KEY, data=b"<html>not a pdf</html>").status_code, 400)
        self.assertEqual(self.post(KEY, data=b"%PDF-1.4 broken").status_code, 400)  # starts like a PDF, cannot be read
        self.assertEqual(self.post("x").status_code, 400)  # not a DOI key
        self.assertEqual(self.c.post("/api/si", data={"key": KEY}, content_type="multipart/form-data").status_code, 400)
        self.assertFalse((self.root / "original paper" / "SI" / f"{KEY}_SI.pdf").exists())

    def test_list_and_remove(self):
        self.post(KEY)
        self.assertEqual([f["name"] for f in self.c.get("/api/si").get_json()["files"]], [f"{KEY}_SI.pdf"])
        self.assertEqual(self.c.delete(f"/api/si/{KEY}_SI.pdf").status_code, 200)
        self.assertEqual(self.pair()["si"], 0)
        self.assertEqual(self.c.delete(f"/api/si/{KEY}_SI.pdf").status_code, 404)
        self.assertEqual(self.c.delete("/api/si/..%2F..%2Fpaper.pdf").status_code, 404)  # only SI files can be removed
        self.assertTrue((self.root / "original paper" / "paper.pdf").is_file())


if __name__ == "__main__":
    unittest.main()

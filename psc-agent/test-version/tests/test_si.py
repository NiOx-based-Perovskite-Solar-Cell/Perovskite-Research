"""Supporting information: appended to the paper, found on disk, searched on the web by an agent."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import fitz
from _fake import last_user_text, runtime, tool_call
from _fixtures import write_pdf

from psc_agent import Extractor, load_paper, write_workbook
from psc_agent.core.web import Web, WebError
from psc_agent.sources.paper import paper_parts
from psc_agent.sources.pairing import local_si, pair_folders
from psc_agent.sources.si import SiFinder, si_hint, supplement_links
from psc_agent.evaluate import evaluate_pairs

MAIN = "doi: 10.1234/test.5678\nTarget device used a PEAI interlayer on NiOx."
SI_TEXT = "Supporting Information. Table S1: PEAI (Greatcellsolar) was spin coated at 4000 rpm for 30 s."


def si_pdf(path: Path, pages: int = 2) -> Path:
    return write_pdf(path, SI_TEXT, pages=pages)


class Appended(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.main = write_pdf(self.tmp / "main.pdf", MAIN, pages=2)
        self.si = si_pdf(self.tmp / "si.pdf", pages=3)

    def test_si_pages_follow_the_main_pages(self):
        paper = load_paper(self.main, si=[self.si])
        self.assertEqual((len(paper.pages), paper.n_main, paper.si_pages), (5, 2, 3))
        self.assertIn("4000 rpm", paper.pages[2])
        text = paper_parts(paper, dpi=72, all_pages=False)[-1]["text"]
        self.assertIn("=== Page 2 ===", text)
        self.assertIn("=== Page 3 (supporting information) ===", text)
        self.assertEqual(paper.doi, "10.1234/test.5678")  # the DOI still comes from the main text

    def test_si_pages_are_text_only_and_the_pdf_bytes_carry_both(self):
        paper = load_paper(self.main, si=[self.si])
        self.assertEqual([i for i, _ in paper.page_images(dpi=72)], [0])  # SI page 1 has a picture too, but is not sent
        with fitz.open(stream=paper.data, filetype="pdf") as doc:
            self.assertEqual(len(doc), 5)

    def test_no_si_means_nothing_changes(self):
        paper = load_paper(self.main)
        self.assertEqual((paper.n_main, paper.si_pages, len(paper.pages)), (None, 0, 2))
        self.assertNotIn("supporting information", paper_parts(paper, dpi=72, all_pages=False)[-1]["text"])

    def test_a_huge_si_is_cut(self):
        big = si_pdf(self.tmp / "big.pdf", pages=100)
        paper = load_paper(self.main, si=[big, self.si])
        self.assertEqual(paper.si_pages, 60)


class Links(unittest.TestCase):
    def test_publisher_pages(self):
        self.assertEqual(supplement_links("10.1021/acsami.5c13836"),
                         ["https://pubs.acs.org/doi/suppl/10.1021/acsami.5c13836", "https://doi.org/10.1021/acsami.5c13836"])
        self.assertEqual(supplement_links("10.1002/smtd.202200787")[0], "https://onlinelibrary.wiley.com/doi/suppl/10.1002/smtd.202200787")
        self.assertEqual(supplement_links("10.1039/d3lf00258f"), ["https://doi.org/10.1039/d3lf00258f"])
        self.assertEqual(supplement_links(None), [])


class Hint(unittest.TestCase):
    def test_link_and_file_name_for_a_paper_without_si(self):
        tmp = Path(tempfile.mkdtemp())
        main = write_pdf(tmp / "m.pdf", "doi: 10.1021/acsami.5c13836 text")
        self.assertEqual(si_hint(load_paper(main)),
                         ("https://pubs.acs.org/doi/suppl/10.1021/acsami.5c13836", "101021acsami5c13836_SI.pdf"))
        self.assertEqual(si_hint(load_paper(main, si=[si_pdf(tmp / "s.pdf")])), ("", ""))  # loaded
        self.assertEqual(si_hint(load_paper(write_pdf(tmp / "n.pdf", "no doi here"))), ("", ""))

    def test_extract_writes_the_link_into_review_csv_and_result(self):
        import csv

        tmp = Path(tempfile.mkdtemp())
        pdf = write_pdf(tmp / "p.pdf", "doi: 10.1002/smtd.202200787 The champion PCE was 20.1%.")
        rows = [{"device": "S1", "sheet": "Properties", "page": 1, "source": "text", "evidence": "The champion PCE was 20.1%",
                 "note": None, "values": [{"column": "Properties", "value": "Champion PCE"}, {"column": "Values", "value": "20.1"}]}]
        rt, _ = runtime({"mapper": lambda b: {"devices": [{"sample": "S1", "paper_name": "c", "description": "d"}], "rows": []},
                         "extract": lambda b: {"rows": rows} if 'SHEET "Properties"' in last_user_text(b) else {"rows": []}})
        res = Extractor(rt, decide_rows=False).extract(pdf, tmp / "out")
        self.assertEqual((res.si_link, res.si_save_as),
                         ("https://onlinelibrary.wiley.com/doi/suppl/10.1002/smtd.202200787", "101002smtd202200787_SI.pdf"))
        review = list(csv.DictReader(open(tmp / "out" / "review.csv", encoding="utf-8-sig")))
        self.assertTrue(review and {r["si_link"] for r in review} == {res.si_link})


class OnDisk(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.raw, self.pdfs = self.tmp / "raw", self.tmp / "pdf"
        self.raw.mkdir(), self.pdfs.mkdir(), (self.pdfs / "SI").mkdir()
        write_workbook({"Layer": [{"LayerID": 1, "LayerType": "Anode", "Material": "ITO"}]}, self.raw / "10.1234_test.5678_Sample1.xlsx")
        write_pdf(self.pdfs / "a paper.pdf", MAIN)

    def test_pairing_attaches_the_si_by_doi_key(self):
        si_pdf(self.pdfs / "SI" / "101234test5678_SI.pdf")
        si_pdf(self.pdfs / "SI" / "109999other_SI.pdf")  # another paper's
        (pair,) = pair_folders(self.raw, self.pdfs).pairs
        self.assertEqual([p.name for p in pair.si], ["101234test5678_SI.pdf"])
        self.assertEqual(local_si(self.pdfs, "101234test5678"), pair.si)
        self.assertEqual(local_si(self.pdfs, ""), [])

    def test_no_si_folder_is_fine(self):
        (self.pdfs / "SI").rmdir()
        (pair,) = pair_folders(self.raw, self.pdfs).pairs
        self.assertEqual(pair.si, [])

    def test_the_extractor_reads_the_si_and_can_be_told_not_to(self):
        si_pdf(self.pdfs / "SI" / "101234test5678_SI.pdf")
        (pair,) = pair_folders(self.raw, self.pdfs).pairs

        def mapper(body):
            return {"devices": [{"sample": "S1", "paper_name": "control", "description": "d"}], "rows": []}

        for use_si, expect in ((True, True), (False, False)):
            rt, fake = runtime({"mapper": mapper, "extract": lambda b: {"rows": []}})
            evaluate_pairs(Extractor(rt, decide_rows=False), [pair], self.tmp / f"out{use_si}", use_si=use_si)
            sent = "\n".join(last_user_text(b) for b in fake.by_role("mapper"))
            self.assertEqual("4000 rpm" in sent, expect)
            self.assertEqual("(supporting information)" in sent, expect)


class Handler(BaseHTTPRequestHandler):
    pdf_bytes = b""

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def do_POST(self):  # the search page
        base = f"http://127.0.0.1:{self.server.server_port}"
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send(200, f'<a rel="nofollow" class="result__a" href="//duck.example/l/?uddg={base}/article&rut=x">The <b>article</b></a>'
                       '<a class="result__snippet" href="x">About &amp; around</a>'
                       f'<a class="result__a" href="{base}/blocked">Blocked</a><a class="result__snippet" href="x">no</a>')

    def do_GET(self):
        if self.path == "/article":
            self.send(200, '<html><title>The article</title><body><script>var x=1</script><p>Main text.</p>'
                           '<a href="/about">About us</a><a href="/files/si.pdf">Supporting Information</a>'
                           '<a href="/fake.pdf">Download</a></body></html>')
        elif self.path == "/files/si.pdf":
            self.send(200, self.pdf_bytes, "application/pdf")
        elif self.path == "/fake.pdf":
            self.send(200, "<html>please log in</html>")
        elif self.path == "/go":
            self.send_response(302)
            self.send_header("Location", "http://10.255.255.1/secret")
            self.end_headers()
        else:
            self.send(403, "no")


class WebTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        Handler.pdf_bytes = si_pdf(cls.tmp / "served.pdf", pages=2).read_bytes()
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def web(self):
        return Web(search_url=self.base + "/search", allow_private=True, timeout=5)

    def test_search_page_and_download(self):
        web = self.web()
        results = web.search("anything")
        self.assertEqual(results[0], {"title": "The article", "url": self.base + "/article", "snippet": "About & around"})
        page = web.page(self.base + "/article")
        self.assertEqual((page["title"], "var x" in page["text"], "Main text." in page["text"]), ("The article", False, True))
        self.assertEqual(page["links"][0]["text"], "Supporting Information")  # supporting files first
        info = web.download_pdf(self.base + "/files/si.pdf", self.tmp / "dl" / "x_SI.pdf")
        self.assertEqual((info["pages"], info["saved"]), (2, "x_SI.pdf"))
        self.assertIn("Supporting Information", info["first_page_text"])
        self.assertTrue((self.tmp / "dl" / "x_SI.pdf").exists())

    def test_failures_come_back_as_messages(self):
        web = self.web()
        with self.assertRaisesRegex(WebError, "HTTP 403.*blocks scripts"):
            web.page(self.base + "/blocked")
        with self.assertRaisesRegex(WebError, "not a PDF"):
            web.download_pdf(self.base + "/fake.pdf", self.tmp / "no.pdf")
        self.assertFalse((self.tmp / "no.pdf").exists())
        with self.assertRaisesRegex(WebError, "only http"):
            web.page("file:///etc/passwd")

    def test_private_addresses_are_refused_also_after_a_redirect(self):
        with self.assertRaisesRegex(WebError, "private"):
            Web(timeout=5).page(self.base + "/article")  # allow_private is off
        redirecting = Web(timeout=5, allow_private=False)
        redirecting.check = lambda url: None if url.startswith(self.base) else Web.check(redirecting, url)
        with self.assertRaisesRegex(WebError, "private"):
            redirecting.page(self.base + "/go")


class Finder(WebTools):
    def finder(self, script):
        steps = iter(script)
        rt, fake = runtime({"si_finder": lambda body: next(steps)})
        return SiFinder(rt, web=self.web()), fake

    def test_agent_reads_the_article_and_downloads_the_si(self):
        paper = load_paper(write_pdf(self.tmp / "m.pdf", MAIN))
        finder, fake = self.finder([
            {"tool_calls": [tool_call("read_page", {"url": self.base + "/article"})]},
            {"tool_calls": [tool_call("download_pdf", {"url": self.base + "/files/si.pdf"})]},
            json.dumps({"status": "found", "notes": "saved the SI pdf"})])
        res = finder.find(paper, self.tmp / "found", key="101234test5678")
        self.assertEqual((res.status, [f.name for f in res.files]), ("found", ["101234test5678_SI.pdf"]))
        self.assertTrue(res.files[0].exists())
        self.assertEqual(local_si(self.tmp / "found" / "..", "x"), [])  # the SI folder is the caller's choice
        first = last_user_text(fake.requests[0])
        self.assertIn("10.1234/test.5678", first)
        self.assertIn("[T1]", json.dumps(fake.requests[1]))  # tool results are numbered for the model

    def test_blocked_site_is_reported_not_raised(self):
        paper = load_paper(write_pdf(self.tmp / "m2.pdf", MAIN))
        finder, _ = self.finder([
            {"tool_calls": [tool_call("read_page", {"url": self.base + "/blocked"})]},
            json.dumps({"status": "blocked", "notes": "the publisher answered 403"})])
        res = finder.find(paper, self.tmp / "none", key="k")
        self.assertEqual((res.status, res.files, res.notes), ("blocked", [], "the publisher answered 403"))

    def test_links_for_a_person_keep_only_addresses_that_were_seen(self):
        paper = load_paper(write_pdf(self.tmp / "m4.pdf", MAIN))
        seen, invented = self.base + "/files/si.pdf", "https://example.org/made-up-si.pdf"
        finder, _ = self.finder([
            {"tool_calls": [tool_call("read_page", {"url": self.base + "/article"})]},
            json.dumps({"status": "blocked", "notes": "could not save", "links": [seen, invented]})])
        res = finder.find(paper, self.tmp / "links", key="k")
        self.assertEqual(res.links, [seen, "https://doi.org/10.1234/test.5678"])  # the invented one is dropped, the DOI link added

    def test_a_wrong_download_does_not_count(self):
        paper = load_paper(write_pdf(self.tmp / "m3.pdf", MAIN))
        finder, _ = self.finder([
            {"tool_calls": [tool_call("download_pdf", {"url": self.base + "/fake.pdf"})]},
            json.dumps({"status": "not_found", "notes": "only a login page"})])
        res = finder.find(paper, self.tmp / "wrong", key="k")
        self.assertEqual((res.status, res.files), ("not_found", []))


if __name__ == "__main__":
    unittest.main()

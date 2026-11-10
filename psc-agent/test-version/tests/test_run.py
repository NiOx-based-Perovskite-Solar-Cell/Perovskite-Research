"""`psc-agent run`: the SI check on the paper's own text, the cheapest-first SI steps, and one reading by extract / recheck."""
import csv
import json
import re
import tempfile
import unittest
from pathlib import Path

from _fake import last_user_text, runtime
from _fixtures import write_pdf

from psc_agent import Extractor, Rechecker, load_paper, write_workbook
from psc_agent.runner import collect, run_items
from psc_agent.sources.si import SiFinder, SiReader, SiResult, has_pattern, mentions_si, si_stage


def paper(tmp: Path, text: str, name: str = "p.pdf"):
    return load_paper(write_pdf(tmp / name, text))


class MentionsSi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_the_words(self):
        for text in ("See the Supporting Information for details.", "Details are in the supplementary material.",
                     "Electronic Supplementary Information (ESI) is available.", "Data in the ESI."):
            with self.subTest(text):
                self.assertTrue(mentions_si(paper(self.tmp, text)))

    def test_two_different_figure_references_are_enough_one_is_not(self):
        self.assertTrue(mentions_si(paper(self.tmp, "Shown in Figure S1 and Table S2.")))
        self.assertTrue(mentions_si(paper(self.tmp, "See Fig. S3, and also Figure S4.")))
        self.assertFalse(mentions_si(paper(self.tmp, "See Figure S1 (and Figure S1 again).")))  # the same one twice
        self.assertTrue(mentions_si(paper(self.tmp, "Shown in Figures S1 and S2.")))  # a list under one "Figures"
        self.assertTrue(mentions_si(paper(self.tmp, "Listed in Table S1, S3.")))
        self.assertFalse(mentions_si(paper(self.tmp, "See Figure S1, 2020 data.")))  # a year after a comma is not a second reference

    def test_a_paper_that_never_refers_to_one(self):
        self.assertFalse(mentions_si(paper(self.tmp, "A complete paper with Figure 1 and Table 2. The SI unit is volt. Pressure in Pa.")))

    def test_an_appended_si_does_not_count_as_the_paper_mentioning_it(self):
        main = write_pdf(self.tmp / "m.pdf", "No reference here.")
        si = write_pdf(self.tmp / "s.pdf", "Supporting Information. Figure S1 Figure S2.")
        self.assertFalse(mentions_si(load_paper(main, si=[si])))


class Stage(unittest.TestCase):
    class Finder:
        def __init__(self):
            self.calls = []

        def find(self, paper, dest, key=None):
            self.calls.append(key)
            return SiResult(key, "links", links=["https://found.example/si.pdf"])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.finder = self.Finder()

    def stage(self, text, have=()):
        return si_stage(paper(self.tmp, text), "k", self.tmp / "SI", list(have), self.finder)

    def test_cheapest_answer_first(self):
        self.assertEqual(self.stage("doi: 10.1016/j.x.1 Supporting Information.", have=[self.tmp / "a.pdf"]).status, "have")
        self.assertEqual(self.stage("doi: 10.1016/j.x.1 nothing about it").status, "not_referenced")
        acs = self.stage("doi: 10.1021/acsami.5c13836 See the Supporting Information.")
        self.assertEqual((acs.status, acs.links[0]), ("pattern", "https://pubs.acs.org/doi/suppl/10.1021/acsami.5c13836"))
        self.assertEqual(self.finder.calls, [])  # none of the above called the agent
        other = self.stage("doi: 10.1016/j.x.1 See the Supporting Information.")
        self.assertEqual((other.status, self.finder.calls), ("links", ["k"]))  # only here

    def test_without_an_agent_the_doi_link_is_given(self):
        r = si_stage(paper(self.tmp, "doi: 10.1016/j.x.1 Supporting Information."), "k", self.tmp, [], None)
        self.assertEqual((r.status, r.links), ("manual", ["https://doi.org/10.1016/j.x.1"]))

    def test_publishers_with_a_known_pattern(self):
        self.assertTrue(has_pattern("10.1002/admi.202500231") and has_pattern("10.1021/x"))
        self.assertFalse(has_pattern("10.1016/j.x.1") or has_pattern(None))


def reading(refers=True, evidence="", address="", holds=""):
    return lambda body: {"refers_to_si": refers, "evidence": evidence, "address": address, "holds": holds}


class ReaderStage(unittest.TestCase):
    """A model reads the paper's text instead of the text rule: it decides, and it pulls out an address the paper prints."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.finder = Stage.Finder()

    def stage(self, text, answer, have=()):
        rt, self.fake = runtime({"si_reader": answer})
        return si_stage(paper(self.tmp, text), "k", self.tmp / "SI", list(have), self.finder, SiReader(rt))

    def test_the_model_can_say_there_is_none_where_the_rule_sees_the_words(self):
        said = "No additional information is available for this paper."
        res = self.stage("doi: 10.1016/j.x.1 See the Supporting Information of the cited work.", reading(False, said))
        self.assertEqual(res.status, "not_referenced")
        self.assertIn(said, res.notes)
        self.assertEqual(self.finder.calls, [])
        self.assertGreater(res.usage.cost_usd, 0)  # the reading is billed to the paper

    def test_the_model_can_find_one_the_rule_misses_and_the_agent_follows(self):
        text = "doi: 10.1016/j.x.1 The supplement accompanies this article online."  # no phrase, no S-numbers
        self.assertFalse(mentions_si(paper(self.tmp, text)))
        res = self.stage(text, reading(True, "The supplement accompanies this article online."))
        self.assertEqual((res.status, self.finder.calls), ("links", ["k"]))
        self.assertGreater(res.usage.cost_usd, 0)  # the agent's result carries the reading's cost too

    def test_an_address_printed_in_the_paper_is_the_link_even_when_a_pdf_line_break_split_it(self):
        text = "doi: 10.1016/j.x.1 Supplementary data can be found online at https://doi. org/10.1016/j.x.1.s1 ok."
        res = self.stage(text, reading(True, "Supplementary data can be found ...", "https://doi. org/10.1016/j.x.1.s1", "Table S1"))
        self.assertEqual((res.status, res.links[0], res.notes), ("printed", "https://doi.org/10.1016/j.x.1.s1", "Table S1"))
        self.assertIn("https://doi.org/10.1016/j.x.1", res.links)  # then the DOI link
        self.assertEqual(self.finder.calls, [])

    def test_an_address_the_paper_does_not_print_is_dropped(self):
        res = self.stage("doi: 10.1021/acsami.5c13836 See the Supporting Information.", reading(True, "x", "https://invented.example/si.pdf"))
        self.assertEqual(res.status, "pattern")  # the publisher pattern, not the model's guess
        self.assertNotIn("https://invented.example/si.pdf", res.links)

    def test_a_model_failure_falls_back_to_the_text_rule(self):
        res = self.stage("doi: 10.1016/j.x.1 See the Supporting Information.", lambda body: "this is not json")
        self.assertEqual(res.status, "links")  # the rule saw the words, the agent followed
        self.assertIn("model failed", res.notes)
        skipped = self.stage("doi: 10.1016/j.x.1 A complete paper.", lambda body: "this is not json")
        self.assertEqual(skipped.status, "not_referenced")

    def test_files_already_saved_need_no_reading_and_an_appended_si_is_not_read_as_the_paper(self):
        self.stage("doi: 10.1016/j.x.1 See the Supporting Information.", reading(), have=[self.tmp / "a.pdf"])
        self.assertEqual(self.fake.by_role("si_reader"), [])
        main = write_pdf(self.tmp / "m.pdf", "The main text.")
        si = write_pdf(self.tmp / "s.pdf", "Supporting Information. Figure S1 is the extra page.")
        rt, fake = runtime({"si_reader": reading(False)})
        SiReader(rt).read(load_paper(main, si=[si]))
        self.assertNotIn("extra page", last_user_text(fake.by_role("si_reader")[0]))


def mapper(body):
    return {"devices": [{"sample": "S1", "paper_name": "control", "description": "d"}], "rows": []}


def recheck_all_missing(body):
    ids = re.findall(r"^(D\d+:[^|]+?:\d+) \|", last_user_text(body), re.M)
    return {"devices": [{"device_id": "D1", "paper_device": "c"}],
            "rows": [{"row_id": i, "found": False, "values": [], "page": 1, "source": "text", "evidence": None, "note": None,
                      "key_issue": None} for i in ids]}


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.raw, self.pdfs, self.out = self.tmp / "raw", self.tmp / "pdf", self.tmp / "out"
        self.raw.mkdir(), self.pdfs.mkdir()
        write_pdf(self.pdfs / "a other publisher.pdf", "doi: 10.1016/j.test.2024.1 Methods. See the Supporting Information.")
        write_pdf(self.pdfs / "b no si.pdf", "doi: 10.1016/j.test.2024.2 A paper that is complete in itself.")
        write_pdf(self.pdfs / "c acs.pdf", "doi: 10.1021/acsami.5c13836 Methods in Figure S1 and Table S2.")
        write_workbook({"Layer": [{"LayerID": 1, "LayerType": "Anode", "Material": "ITO"}]},
                       self.raw / "10.1021_acsami.5c13836_Sample1.xlsx")

    def test_collect(self):
        keys = lambda mode: sorted(i.key for i in collect(self.raw, self.pdfs, mode=mode))  # noqa: E731
        self.assertEqual(keys("auto"), ["101016jtest20241", "101016jtest20242", "101021acsami5c13836"])
        self.assertEqual(keys("extract"), ["101016jtest20241", "101016jtest20242"])
        self.assertEqual(keys("recheck"), ["101021acsami5c13836"])
        self.assertEqual([i.key for i in collect(self.raw, self.pdfs, only=["acsami"])], ["101021acsami5c13836"])
        self.assertEqual(len(collect(self.raw, self.pdfs, limit=1)), 1)
        mine = next(i for i in collect(self.raw, self.pdfs, mode="recheck"))
        self.assertEqual(len(mine.workbooks), 1)

    def test_every_paper_takes_the_cheapest_path_and_is_read_once(self):
        asked = []

        def si_finder(body):
            asked.append(1)
            return json.dumps({"status": "not_found", "notes": "", "links": []})

        rt, fake = runtime({"si_finder": si_finder, "mapper": mapper, "extract": lambda b: {"rows": []}, "recheck": recheck_all_missing})
        finder = SiFinder(rt, download=False)
        outcomes = run_items(collect(self.raw, self.pdfs), finder=finder, extractor=Extractor(rt, decide_rows=False),
                             rechecker=Rechecker(runtime=rt, judge=False), out=self.out)
        by = {o.item.key: o for o in outcomes}
        self.assertEqual(len(asked) > 0, True)
        self.assertEqual({k: (o.mode, o.si.status) for k, o in by.items()}, {
            "101016jtest20241": ("extract", "not_found"),  # another publisher: the SI agent searched
            "101016jtest20242": ("extract", "not_referenced"),  # never mentions an SI: skipped
            "101021acsami5c13836": ("recheck", "pattern")})  # ACS: the link follows from the DOI
        self.assertEqual(len(fake.by_role("si_finder")) >= 1, True)
        self.assertEqual(all("Supporting Information" in last_user_text(b) or "supporting" in last_user_text(b).lower()
                             for b in fake.by_role("si_finder")), True)  # only paper 1 was ever searched
        self.assertEqual(len(fake.by_role("mapper")), 2)  # both extract papers read once; the ACS paper went to recheck
        self.assertEqual(len(fake.by_role("recheck")) >= 1, True)

        summary = {r["paper"]: r for r in csv.DictReader(open(self.out / "run_summary.csv", encoding="utf-8-sig"))}
        self.assertEqual(summary["101021acsami5c13836"]["si_links"], "https://pubs.acs.org/doi/suppl/10.1021/acsami.5c13836\nhttps://doi.org/10.1021/acsami.5c13836")
        links = (self.out / "si_links.md").read_text(encoding="utf-8")
        self.assertIn("101021acsami5c13836_SI.pdf", links)  # the ACS paper is on the download list
        self.assertNotIn("101016jtest20242", links)  # the paper without any SI reference is not
        self.assertTrue((self.out / "extract" / "101016jtest20241").is_dir())
        self.assertTrue((self.out / "papers" / "101021acsami5c13836.json").is_file())
        self.assertTrue((self.out / "findings.csv").is_file())

    def test_an_si_file_already_in_the_folder_is_read_with_the_paper_and_no_agent_is_called(self):
        (self.pdfs / "SI").mkdir()
        write_pdf(self.pdfs / "SI" / "101016jtest20241_SI.pdf", "Supporting Information. Table S1: spin at 4000 rpm.")
        rt, fake = runtime({"si_finder": lambda b: json.dumps({"status": "not_found", "notes": "", "links": []}),
                            "mapper": mapper, "extract": lambda b: {"rows": []}})
        items = collect(self.raw, self.pdfs, only=["test20241"])
        (o,) = run_items(items, finder=SiFinder(rt, download=False), extractor=Extractor(rt, decide_rows=False),
                         rechecker=Rechecker(runtime=rt, judge=False), out=self.out)
        self.assertEqual(o.si.status, "have")
        self.assertEqual(fake.by_role("si_finder"), [])
        self.assertIn("(supporting information)", "\n".join(last_user_text(b) for b in fake.by_role("mapper")))  # one reading, with the SI

    def test_a_model_reader_replaces_the_text_rule(self):
        def si_reader(body):  # says "no" where the words appear, "yes" for a paper the rule would skip
            if "Supporting Information" in last_user_text(body):
                return {"refers_to_si": False, "evidence": "Only the SI of another paper.", "address": "", "holds": ""}
            return {"refers_to_si": True, "evidence": "e", "address": "", "holds": ""}

        rt, fake = runtime({"si_reader": si_reader, "si_finder": lambda b: json.dumps({"status": "not_found", "notes": "", "links": []}),
                            "mapper": mapper, "extract": lambda b: {"rows": []}, "recheck": recheck_all_missing})
        outcomes = run_items(collect(self.raw, self.pdfs), finder=SiFinder(rt, download=False), reader=SiReader(rt),
                             extractor=Extractor(rt, decide_rows=False), rechecker=Rechecker(runtime=rt, judge=False), out=self.out)
        by = {o.item.key: o.si.status for o in outcomes}
        self.assertEqual(by, {"101016jtest20241": "not_referenced",  # the rule said yes, the model read it and said no
                              "101016jtest20242": "not_found",  # the rule said no, the model said yes: the agent searched
                              "101021acsami5c13836": "pattern"})  # yes again: ACS follows from the DOI
        self.assertEqual(len(fake.by_role("si_reader")), 3)  # once per paper
        self.assertEqual(len(fake.by_role("si_finder")), 1)  # only the paper no cheaper step could answer
        self.assertGreater(next(o for o in outcomes if o.item.key == "101016jtest20241").cost, 0)  # the reading is in the paper's cost

    def test_no_si_ignores_it_all(self):
        (self.pdfs / "SI").mkdir()
        write_pdf(self.pdfs / "SI" / "101016jtest20241_SI.pdf", "Supporting Information.")
        rt, fake = runtime({"mapper": mapper, "extract": lambda b: {"rows": []}})
        items = collect(self.raw, self.pdfs, only=["test20241"])
        (o,) = run_items(items, finder=None, extractor=Extractor(rt, decide_rows=False), rechecker=Rechecker(runtime=rt, judge=False),
                         out=self.out, use_si=False)
        self.assertEqual(o.si.status, "manual")  # no files loaded, no agent: the link to open
        self.assertNotIn("(supporting information)", "\n".join(last_user_text(b) for b in fake.by_role("mapper")))

    def test_one_paper_failing_does_not_stop_the_others(self):
        def boom(body):
            raise RuntimeError("model fell over")

        rt, _ = runtime({"mapper": mapper, "extract": lambda b: {"rows": []}, "recheck": boom})
        outcomes = run_items(collect(self.raw, self.pdfs), finder=None, extractor=Extractor(rt, decide_rows=False),
                             rechecker=Rechecker(runtime=rt, judge=False), out=self.out)
        by = {o.item.key: o for o in outcomes}
        self.assertTrue(by["101021acsami5c13836"].errors)
        self.assertEqual(by["101016jtest20241"].errors, [])


if __name__ == "__main__":
    unittest.main()

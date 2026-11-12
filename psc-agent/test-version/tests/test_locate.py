from _fixtures import write_pdf

from psc_agent.sources.locate import find_quote, value_terms

TEXT = "The NiOx film was annealed at 300 C. Then 0.01 M NiCl2 (99.9985 %, Merck, Darmstadt, GR) was spin coated at 4000 rpm."


def test_the_whole_quote_is_boxed(tmp_path):
    pdf = write_pdf(tmp_path / "p.pdf", TEXT, image=False)
    hit = find_quote(pdf, 1, "0.01 M NiCl2 (99.9985 %, Merck, Darmstadt, GR)")
    assert hit["how"] == "quote" and hit["rects"]
    x0, y0, x1, y1 = hit["rects"][0]
    assert 0 <= x0 < x1 <= hit["width"] and 0 <= y0 < y1 <= hit["height"]


def test_spacing_and_case_do_not_matter(tmp_path):
    pdf = write_pdf(tmp_path / "p.pdf", TEXT, image=False)
    assert find_quote(pdf, 1, "0.01M  nicl2 (99.9985%, MERCK")["how"] == "quote"


def test_a_trimmed_quote_falls_back_to_runs_of_words(tmp_path):
    pdf = write_pdf(tmp_path / "p.pdf", TEXT, image=False)
    hit = find_quote(pdf, 1, "the NiOx film was annealed at 300 C and then cooled slowly in air for an hour")
    assert hit["how"] == "part of the quote" and hit["rects"]


def test_last_the_extracted_number(tmp_path):
    pdf = write_pdf(tmp_path / "p.pdf", TEXT, image=False)
    hit = find_quote(pdf, 1, "a sentence that is not there at all", value_terms('{"Values": "4000", "Unit": "rpm"}'))
    assert hit["how"] == "value" and hit["rects"]
    assert find_quote(pdf, 1, "nothing like it here", ["123456"]) ["how"] == ""


def test_value_terms():
    assert value_terms('{"Material": "NiCl2", "%Purity": "99.9985"}') == ["99.9985", "2"]

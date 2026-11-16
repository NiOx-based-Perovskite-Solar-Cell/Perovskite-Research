from psc_agent.sources.paper import locate_page, si_layout
from _fixtures import write_pdf


def test_a_page_number_goes_back_to_its_file(tmp_path):
    main = write_pdf(tmp_path / "m.pdf", "main", pages=4)
    si1, si2 = write_pdf(tmp_path / "a_SI.pdf", "si one", pages=3), write_pdf(tmp_path / "a_SI2.pdf", "si two", pages=2)
    lay = si_layout(main, [si1, si2])
    assert lay == {"main_pages": 4, "si": [{"name": "a_SI.pdf", "pages": 3}, {"name": "a_SI2.pdf", "pages": 2}]}
    assert [locate_page(lay, n) for n in (1, 4, 5, 7, 8, 9, 10, 0)] == [(0, 1), (0, 4), (1, 1), (1, 3), (2, 1), (2, 2), None, None]


def test_the_cut_matches_load_paper(tmp_path):
    from psc_agent.sources.paper import MAX_SI_PAGES, load_paper
    main = write_pdf(tmp_path / "m.pdf", "main", pages=2)
    big = write_pdf(tmp_path / "big_SI.pdf", "x", pages=MAX_SI_PAGES + 10)
    lay = si_layout(main, [big])
    assert lay["si"][0]["pages"] == MAX_SI_PAGES
    assert len(load_paper(main, si=[big]).pages) == 2 + MAX_SI_PAGES

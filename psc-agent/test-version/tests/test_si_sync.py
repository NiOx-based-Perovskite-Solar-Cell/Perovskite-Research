from psc_agent.sources.pairing import local_si, sync_si_folders


def test_si_mirrored_both_ways_never_overwritten(tmp_path):
    raw, pdf = tmp_path / "raw", tmp_path / "pdf"
    (raw / "SI").mkdir(parents=True)
    pdf.mkdir()
    (raw / "SI" / "101039d3lf00258f_SI.pdf").write_bytes(b"aaa")
    made = sync_si_folders(raw, pdf)
    assert len(made) == 1
    assert [p.name for p in local_si(pdf, "101039d3lf00258f")] == ["101039d3lf00258f_SI.pdf"]
    (pdf / "SI" / "101002x_SI.pdf").write_bytes(b"b")  # saved on the other side
    (pdf / "SI" / "101039d3lf00258f_SI.pdf").write_bytes(b"different")  # conflict: kept
    sync_si_folders(raw, pdf)
    assert (raw / "SI" / "101002x_SI.pdf").exists()
    assert (raw / "SI" / "101039d3lf00258f_SI.pdf").read_bytes() == b"aaa"
    assert sync_si_folders(raw, pdf) == []


def test_missing_folders_ok(tmp_path):
    assert sync_si_folders(tmp_path / "nope", tmp_path / "nope2") == []

"""Citations: BibTeX from the reference list, the app's own entry, CITATION.cff."""

from __future__ import annotations

import datetime
from pathlib import Path

import openprocess
from openprocess import citation, references


def test_every_reference_becomes_a_bibtex_entry():
    for reference in references.REFERENCES:
        entry = citation.entry(reference)
        assert entry.key == reference.key and entry.fields["title"]
        assert entry.kind in ("article", "inproceedings", "book", "phdthesis", "techreport", "misc")
        text = entry.bibtex()
        assert text.startswith(f"@{entry.kind}{{{reference.key},") and text.endswith("}")
        if reference.key != "cpntools":                      # a tool, undated
            assert entry.fields["year"].isdigit()


def test_the_kinds_and_fields_follow_the_citation():
    article = citation.entry(references.reference("aalst2004"))
    assert article.kind == "article" and article.fields["journal"] == "IEEE Transactions on Knowledge and Data Engineering"
    assert (article.fields["volume"], article.fields["number"], article.fields["pages"]) == ("16", "9", "1128–1142")
    assert article.fields["author"] == "W.M.P. van der Aalst and A.J.M.M. Weijters and L. Maruster"
    assert article.fields["doi"] == "10.1109/TKDE.2004.47" and article.fields["year"] == "2004"
    paper = citation.entry(references.reference("leemans2013"))
    assert paper.kind == "inproceedings" and paper.fields["publisher"] == "Springer" and paper.fields["pages"] == "311–329"
    book = citation.entry(references.reference("aalst2016"))
    assert book.kind == "book" and book.fields["edition"] == "2" and book.fields["publisher"] == "Springer"
    thesis = citation.entry(references.reference("adriansyah2014"))
    assert thesis.kind == "phdthesis" and "Eindhoven" in thesis.fields["school"]
    standard = citation.entry(references.reference("pnml"))
    assert standard.fields["organization"] == "ISO/IEC 15909-2:2011"
    assert "\\&" in citation.entry(references.reference("leemans2019")).bibtex()   # escaped for BibTeX


def test_the_algorithms_cite_the_works_they_follow():
    keys = [e.key for e in citation.entries_for_module("openprocess.mining.discovery.alpha")]
    assert "aalst2004" in keys
    assert citation.entries_for_module("openprocess.nowhere") == []


def test_the_app_cites_itself_and_the_cff_file_is_current():
    text = citation.app_bibtex("0.10.0", 2026)
    assert text.startswith("@software{openprocess,") and "version = {0.10.0}" in text and "Struthers, Sam" in text
    assert "0.10.0" in citation.app_text("0.10.0", 2026)
    assert citation.app_text("0.10.0", 2026).endswith("https://doi.org/" + citation.concept_doi())   # archived on Zenodo
    cff = Path(__file__).resolve().parents[1] / "CITATION.cff"
    assert cff.exists()
    assert f"version: {openprocess.__version__}" in cff.read_text(encoding="utf-8"), \
        "CITATION.cff names another version than openprocess.__version__: regenerate it with citation.cff_text"
    generated = citation.cff_text("1.2.3", datetime.date(2030, 1, 2))
    assert "cff-version: 1.2.0" in generated and "date-released: 2030-01-02" in generated


def test_the_doi_comes_from_citation_cff_when_there_is_one(tmp_path, monkeypatch):
    without = citation.cff_text("1.0.0", datetime.date(2030, 1, 2))
    assert "doi:" not in without
    with_doi = citation.cff_text("1.0.0", datetime.date(2030, 1, 2), doi="10.5281/zenodo.1234567")
    assert "doi: 10.5281/zenodo.1234567\n" in with_doi
    assert "doi" not in citation.app_entry("1.0.0", 2030, doi="").fields or not citation.app_entry("1.0.0", 2030, doi="").fields["doi"]
    entry = citation.app_entry("1.0.0", 2030, doi="10.5281/zenodo.1234567")
    assert "doi     = {10.5281/zenodo.1234567}" in entry.bibtex()
    assert citation.app_text("1.0.0", 2030, doi="10.5281/zenodo.1234567").endswith("https://doi.org/10.5281/zenodo.1234567")
    assert citation.app_text("1.0.0", 2030, doi="").endswith(citation.APP_URL)
    assert citation.concept_doi() == "10.5281/zenodo.23295550"   # the concept DOI in CITATION.cff, for all versions


def test_citation_cff_is_valid_yaml_despite_the_colon_in_the_title():
    """Zenodo's archive of v0.10.5 failed on CITATION.cff: the title's colon
    was unquoted.  String values with a colon are quoted now."""
    text = citation.cff_text("1.0.0", datetime.date(2030, 1, 2))
    title_line = next(line for line in text.splitlines() if line.startswith("title:"))
    assert title_line == 'title: "' + citation.APP_TITLE + '"'
    try:
        import yaml  # noqa: F401 - only when it happens to be installed
    except ImportError:
        return
    data = yaml.safe_load(text)
    assert data["title"] == citation.APP_TITLE and data["cff-version"] == "1.2.0"

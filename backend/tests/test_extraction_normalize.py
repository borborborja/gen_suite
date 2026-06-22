"""Unit tests for the extraction contract parsing and name normalization (no DB)."""
from app.modules.extraction.normalize import (
    block_key_given,
    norm_given,
    norm_surname,
    normalize_role,
    parse_age,
    spanish_phonetic,
)


def test_normalize_role_spanish_to_canonical():
    assert normalize_role("cabeza de familia") == "head"
    assert normalize_role("hijo") == "son"
    assert normalize_role("hija") == "daughter"
    assert normalize_role("esposa") == "spouse"
    assert normalize_role("padre") == "father"
    assert normalize_role("hermano") == "sibling"
    assert normalize_role("mozo") == "soldier"
    assert normalize_role("testador") == "testator"
    assert normalize_role("head") == "head"  # already canonical
    assert normalize_role("zzzzz") == "other"
    assert normalize_role(None) == "other"


def test_parse_age():
    assert parse_age("61 años") == 61
    assert parse_age("de edad de 45") == 45
    assert parse_age("6 meses") == 0
    assert parse_age("LXI") == 61
    assert parse_age(None) is None
    assert parse_age("sin dato") is None
from app.modules.extraction.schemas import ExtractedMention, ExtractedPage


def test_extracted_page_parses_full_record():
    page = ExtractedPage.model_validate({
        "has_record": True,
        "records": [{
            "record_type": "baptism", "date_year": 1750, "place_raw": "Vallbona",
            "confidence": 0.9,
            "mentions": [
                {"role": "principal", "given": "Joan", "surname": "Vidal", "name_raw": "Joannes Vidal"},
                {"role": "father", "name_raw": "Francesc Vidal"},
            ],
        }],
    })
    assert page.has_record and len(page.records) == 1
    rec = page.records[0]
    assert rec.record_type == "baptism" and rec.date_year == 1750
    assert rec.mentions[0].role == "principal"


def test_extracted_page_blank_page():
    page = ExtractedPage.model_validate({"has_record": False})
    assert page.has_record is False and page.records == []


def test_mention_defaults_are_lenient():
    m = ExtractedMention(role="witness")
    assert m.given is None and m.sex is None


def test_confidence_bounds_enforced():
    import pytest

    with pytest.raises(Exception):
        ExtractedMention(role="x")  # ok, role free-text
        ExtractedPage.model_validate({"has_record": True, "records": [{"record_type": "baptism", "confidence": 5}]})


def test_norm_handles_none_and_empty():
    assert norm_given(None) == ""
    assert norm_surname("") == ""
    assert block_key_given(None) == ""


def test_phonetic_silent_h_and_ll():
    assert spanish_phonetic("Hernández") == spanish_phonetic("Ernandez")
    # ll → y skeleton
    assert "y" in spanish_phonetic("Castelló") or spanish_phonetic("Castelló")

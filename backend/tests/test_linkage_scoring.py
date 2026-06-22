"""Unit tests for the pure normalization + scoring logic (no DB)."""
from app.modules.extraction.normalize import (
    block_key_surname,
    compute_keys,
    norm_given,
    spanish_phonetic,
    split_name,
)
from app.modules.linkage.scoring import Candidate, Seed, score_candidate


def test_latin_fold_given():
    assert norm_given("Joannes") == "joan"
    assert norm_given("Petrus") == "pere"
    assert norm_given("María") == "maria"


def test_phonetic_collapses_iberico_variants():
    # b↔v and the silent h / x↔j variation should collapse to the same block key
    assert block_key_surname("Vidal") == block_key_surname("Bidal")
    assert spanish_phonetic("Ginés") == spanish_phonetic("Xinés")


def test_split_name():
    assert split_name("Francesc Vidal Roca") == ("Francesc", "Vidal Roca")
    assert split_name("Joan") == ("Joan", "")
    assert split_name(None) == ("", "")


def test_compute_keys_shape():
    keys = compute_keys("Joannes", "Vidal")
    assert set(keys) == {"norm_given", "norm_surname", "block_key_given", "block_key_surname"}
    assert keys["norm_given"] == "joan"
    assert keys["norm_surname"] == "vidal"


def _seed():
    return Seed(
        given="Joan", surname="Vidal", birth_year=1750, place_key="vallbona",
        parent_names={"francesc", "vidal", "maria", "soler"},
    )


def test_strong_match_when_parents_and_name_align():
    # Joannes Vidal, baptism 1750, father Francesc Vidal + mother Maria Soler in the same act
    cand = Candidate(
        given="Joannes", surname="Vidal", role="principal", record_year=1750,
        record_place_key="vallbona", co_mention_names={"francesc", "soler"},
    )
    res = score_candidate(_seed(), cand)
    assert res["score"] >= 0.8
    assert res["signals"]["relational"]["value"] >= 0.8


def test_weak_match_for_homonym_with_wrong_parents_and_date():
    cand = Candidate(
        given="Pere", surname="Vidal", role="principal", record_year=1690,
        record_place_key="barcelona", co_mention_names={"garcia", "lopez"},
    )
    res = score_candidate(_seed(), cand)
    assert res["score"] < 0.6


def test_latin_given_still_scores_high_on_name():
    cand = Candidate(given="Joannes", surname="Vidal", role="principal", record_year=1750)
    res = score_candidate(_seed(), cand)
    assert res["signals"]["name"]["value"] >= 0.85


def test_stated_age_pins_birth_year():
    from app.modules.linkage.scoring import date_plausibility
    seed = Seed(given="Francesc", surname="Vidal", birth_year=1718)
    # death record 1779 stating age 61 → born ~1718 → strong
    good = Candidate(given="Francesc", surname="Vidal", role="principal", record_year=1779, stated_age=61)
    bad = Candidate(given="Francesc", surname="Vidal", role="principal", record_year=1779, stated_age=20)
    assert date_plausibility(seed, good)[0] >= 0.9
    assert date_plausibility(seed, bad)[0] <= 0.2


def test_same_address_is_strong_place():
    from app.modules.linkage.scoring import place_proximity
    seed = Seed(given="Juan", surname="Perez", address="Calle Nava 27")
    same = Candidate(given="Juan", surname="Perez", role="head", address="calle nava 27")
    diff = Candidate(given="Juan", surname="Perez", role="head", address="Plaza Mayor 3")
    assert place_proximity(seed, same)[0] == 1.0
    assert place_proximity(seed, diff)[0] < 0.5


# ── M4: within-corpus co-reference scoring ──
from app.modules.linkage.scoring import MentionView, coref_score


def test_coref_same_person_shared_relatives():
    # "Francesc Vidal" appears in two acts, both naming Maria Soler → same person
    a = MentionView(given="Francesc", surname="Vidal", year=1745, co_names={"soler", "maria"})
    b = MentionView(given="Francesc", surname="Vidal", year=1779, co_names={"soler", "joan"})
    res = coref_score(a, b)
    assert res["same"] and res["score"] >= 0.7


def test_coref_latin_variant_same():
    a = MentionView(given="Joannes", surname="Vidal", year=1750, co_names={"soler"})
    b = MentionView(given="Joan", surname="Vidal", year=1812, co_names={"soler"})
    assert coref_score(a, b)["same"]


def test_coref_different_name_not_same():
    a = MentionView(given="Francesc", surname="Vidal", year=1745)
    b = MentionView(given="Pau", surname="Camps", year=1750)
    res = coref_score(a, b)
    assert not res["same"] and res["score"] == 0.0


def test_coref_same_name_no_corroboration_is_weak():
    # identical common name but nothing else shared and far apart → not confidently the same
    a = MentionView(given="Joan", surname="Vidal", year=1700, co_names=set())
    b = MentionView(given="Joan", surname="Vidal", year=1850, co_names=set())
    res = coref_score(a, b)
    assert res["score"] < 0.7

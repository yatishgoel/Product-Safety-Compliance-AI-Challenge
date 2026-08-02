"""Formula and name handling, including the regressions found while building this."""

from app.chem import clean, is_formula, is_plausible, normalize, ocr_variants, to_hill
from app.match import candidate_keys


def keys(s: str) -> set[tuple[str, str]]:
    return set(candidate_keys(s))


class TestFormulaDetection:
    def test_real_formulas(self):
        for s in ("C6H6", "H2O2", "NaCl", "C8H10N4O2", "CH3COOH"):
            assert is_formula(s), s

    def test_abbreviations_are_names(self):
        for s in ("BHT", "BHA", "EDTA", "SLS", "PEG"):
            assert not is_formula(s), f"{s} has no valid element parse"

    def test_names_are_names(self):
        for s in ("Coumarin", "Urea", "Sodium Lauryl Sulfate", "1,4-Dioxane"):
            assert not is_formula(s), s


class TestHillNotation:
    def test_unifies_equivalent_spellings(self):
        assert to_hill("C2H5OH") == to_hill("C2H6O") == "C2H6O"
        assert to_hill("CH3COOH") == to_hill("C2H4O2") == "C2H4O2"

    def test_keeps_distinct_molecules_apart(self):
        assert to_hill("H2O") != to_hill("H2O2")


class TestCleaning:
    def test_strips_decorations(self):
        assert clean("Coumarin (0.05%)") == "Coumarin"
        assert clean("Benzene (71-43-2)") == "Benzene"
        assert clean("Coumarin*") == "Coumarin"

    def test_keeps_parentheses_that_are_part_of_the_name(self):
        """Regression: the decoration regex used to turn Ca(OH)2 into 'Ca 2'."""
        assert clean("Ca(OH)2") == "Ca(OH)2"
        assert clean("5-chloro-2-(2,4-dichlorophenoxy)phenol") == (
            "5-chloro-2-(2,4-dichlorophenoxy)phenol"
        )

    def test_never_strips_trailing_digits(self):
        """Regression: stripping them collapsed H2O2 onto H2O, so water read as peroxide."""
        assert clean("H2O2") == "H2O2"
        assert keys("H2O") != keys("H2O2")
        assert clean("Quaternium-15") == "Quaternium-15"

    def test_folds_cyrillic_lookalikes(self):
        assert keys("С6Н6") == keys("C6H6")


class TestNormalisation:
    def test_drops_punctuation(self):
        assert normalize("1,4-Dioxane") == "14dioxane"
        assert normalize("METHYLPARABEN") == "methylparaben"

    def test_drops_colons(self):
        """Regression: OCR emits 'Coumarin:' and the colon used to survive, so it missed."""
        assert normalize("Coumarin:") == normalize("Coumarin")

    def test_near_misses_stay_distinct(self):
        assert normalize("Sodium Laureth Sulfate") != normalize("Sodium Lauryl Sulfate")


class TestPlausibility:
    def test_flags_ocr_damage(self):
        for s in ("H202", "C8H10N402", "C20H4002", "€12H24012"):
            assert not is_plausible(s), f"{s} is chemically impossible"

    def test_passes_real_formulas(self):
        for s in ("H2O2", "C8H10N4O2", "C20H40O2", "C12H26", "NaCl", "C2H5OH"):
            assert is_plausible(s), s


class TestOcrVariants:
    def test_generates_the_measured_corruption(self):
        assert normalize("H202") in ocr_variants("H2O2")

    def test_names_produce_nothing(self):
        assert ocr_variants("Coumarin") == set()

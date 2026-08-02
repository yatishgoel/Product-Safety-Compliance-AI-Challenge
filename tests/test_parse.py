"""Ingredient block parsing, especially the layouts that punish naive implementations."""

from app.parse import parse


def ingredients(text: str) -> list[str]:
    return list(parse(text).ingredients)


class TestNegation:
    """A label listing what a product does NOT contain must not be read as contents.

    Any system that searches the whole document for banned words rejects the safest
    products on the shelf: the paraben-free ones that advertise it.
    """

    def test_formulated_without_is_excluded(self):
        text = (
            "Ingredients:\n- Aqua\n- Glycerin\n- Tocopherol\n"
            "Formulated without: Formaldehyde, Coumarin\n"
        )
        assert ingredients(text) == ["Aqua", "Glycerin", "Tocopherol"]

    def test_free_from_claim_is_excluded(self):
        text = (
            "Description:\nThis product is free from BHT and Triclosan.\n\n"
            "Ingredients:\n- Aqua\n- Panthenol\n"
        )
        assert ingredients(text) == ["Aqua", "Panthenol"]

    def test_negation_before_the_list_is_excluded(self):
        text = "Free from: Coumarin\nIngredients:\n- Aqua\n- Glycerin\n"
        assert ingredients(text) == ["Aqua", "Glycerin"]


class TestLayouts:
    def test_inline_panel(self):
        assert ingredients("INGREDIENTS: Aqua, Glycerin, Coumarin, Parfum.") == [
            "Aqua", "Glycerin", "Coumarin", "Parfum",
        ]

    def test_comma_inside_a_chemical_name_is_not_a_separator(self):
        assert "1,4-Dioxane" in ingredients("Ingredients: Aqua, 1,4-Dioxane, Glycerin")

    def test_header_variants(self):
        for header in ("Composition", "Contains", "INCI", "Ingredient List"):
            assert "Methylparaben" in ingredients(f"{header}: Aqua, Methylparaben"), header

    def test_bare_bullet_list_with_no_header(self):
        assert ingredients("Product Name: X\n\n- Aqua\n- Propylparaben\n") == [
            "Aqua", "Propylparaben",
        ]

    def test_stops_at_directions(self):
        text = "Ingredients:\n- Aqua\n- Glycerin\n\nDirections: apply daily.\n"
        assert ingredients(text) == ["Aqua", "Glycerin"]

    def test_metadata_lines_are_not_ingredients(self):
        text = "Product ID: P-1\nProduct Name: Thing\nSKU: ABC\n\nIngredients:\n- Aqua\n"
        assert ingredients(text) == ["Aqua"]


class TestProductName:
    def test_reads_it(self):
        assert parse("Product Name: Pro Moisturizer\nSKU: X\n").name == "Pro Moisturizer"

    def test_missing_is_none(self):
        assert parse("Ingredients:\n- Aqua\n").name is None

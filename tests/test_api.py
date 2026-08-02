import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api import api, as_json, extractors, policy_json
from app.index import Index
from app.models import Status, Verdict


@pytest.fixture(scope="module")
def client():
    with TestClient(api) as started:
        yield started


def test_policy_json_reports_entry_coverage():
    index = Index(entries=("Benzene", "Unknown"), degraded=("Unknown",), version="2e-demo")
    index.add("literal", "benzene", "Benzene", "certain")
    index.add("literal", "c6h6", "Benzene", "strong")
    index.add("hill", "C6H6", "Benzene", "certain")
    index.add("inchikey", "UHOVQNZJYSORNB-UHFFFAOYSA-N", "Benzene", "certain")

    payload = policy_json(index)

    assert payload["summary"] == {
        "entries": 2,
        "resolved": 1,
        "unresolved": 1,
        "match_keys": 4,
    }
    assert payload["entries"] == [
        {
            "name": "Benzene",
            "status": "resolved",
            "name_keys": 2,
            "formula_keys": 1,
            "structure_keys": 1,
        },
        {
            "name": "Unknown",
            "status": "unresolved",
            "name_keys": 0,
            "formula_keys": 0,
            "structure_keys": 0,
        },
    ]


def test_verdict_json_includes_extracted_ingredients_and_source():
    payload = as_json(Verdict(
        product_name="Cleanser",
        status=Status.ACCEPTED,
        ingredients=("Aqua", "Glycerin"),
        extraction_source="pdf",
    ))

    assert payload["ingredients"] == ["Aqua", "Glycerin"]
    assert payload["extraction_source"] == "pdf"


def test_extractor_catalog_has_automatic_and_selectable_engines():
    payload = asyncio.run(extractors())
    ids = {item["id"] for item in payload["extractors"]}

    assert ids == {"auto", "native", "tesseract", "gemini"}
    assert payload["extractors"][0]["available"] is True


def test_unavailable_engine_says_how_to_turn_it_on():
    """An engine the UI must grey out has to explain itself, or it reads as broken."""
    for entry in asyncio.run(extractors())["extractors"]:
        assert bool(entry["hint"]) is not entry["available"], entry["id"]


def test_batch_screening_honours_the_chosen_extractor(client, tmp_path):
    label = tmp_path / "label.txt"
    label.write_text("Ingredients: Aqua, Glycerin, Benzene")

    with open(label, "rb") as handle:
        response = client.post(
            "/evaluate_products",
            files={"files": ("label.txt", handle, "text/plain")},
            data={"extractor": "text"},
        )

    assert response.status_code == 200
    assert response.json()["results"][0]["extraction_source"] == "text"


@pytest.mark.parametrize("route", ["/evaluate_product", "/evaluate_products"])
def test_unknown_extractor_is_rejected_rather_than_silently_ignored(client, route, tmp_path):
    label = tmp_path / "label.txt"
    label.write_text("Ingredients: Aqua")
    field = "files" if route.endswith("s") else "file"

    with open(label, "rb") as handle:
        response = client.post(
            route,
            files={field: ("label.txt", handle, "text/plain")},
            data={"extractor": "definitely-not-an-engine"},
        )

    assert response.status_code == 400
    assert "unknown extractor" in response.json()["detail"]

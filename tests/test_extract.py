"""Extractor selection. The rule under test is which engine's reading the pipeline trusts."""

from app.chem import is_plausible, normalize
from app.extract import EXTRACTORS, chain, suspect_tokens
from app.index import build_cached
from app.match import best_hit
from app.models import Blob

IMAGE = Blob("label.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)


class TestEngineSelection:
    def test_tesseract_leads_by_default(self):
        assert [e.name for e in chain(IMAGE)][0] == "tesseract"

    def test_only_vetted_engines_read_images(self):
        """EasyOCR was removed for dropping a banned ingredient without saying so.

        Any engine added here has to survive the corruption test below: its mistakes must be
        visible to the plausibility gate, or it will lose ingredients in silence.
        """
        assert {e.name for e in chain(IMAGE)} <= {"tesseract", "gemini"}

    def test_every_extractor_declares_whether_it_is_automatic(self):
        """GeminiVision satisfies the Protocol without inheriting the base class."""
        for name, extractor in EXTRACTORS.items():
            assert isinstance(extractor.auto, bool), name


class TestCorruptionVisibility:
    def test_tesseract_corruption_is_detectable_and_repairable(self):
        assert is_plausible("C8H10N402") is False
        hit = best_hit("C8H10N402", build_cached())
        assert hit is not None and hit.entry == "C8H10N4O2"

    def test_plausible_corruption_is_the_dangerous_kind(self):
        """Why EasyOCR is gone, kept as the standard any future engine must clear.

        It read C8H10N4O2 as C8HION4O2. That parses as a real iodine compound, so the gate
        sees nothing wrong, no escalation happens, and the caffeine is simply gone.
        """
        assert is_plausible("C8HION4O2") is True
        assert suspect_tokens(["C8HION4O2"]) == ()
        assert normalize("C8HION4O2") != normalize("C8H10N4O2")

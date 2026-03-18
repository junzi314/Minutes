"""Unit tests for src/glossary.py."""

from __future__ import annotations

from src.glossary import apply_glossary
from src.transcriber import Segment


def _seg(text: str, speaker: str = "Alice") -> Segment:
    return Segment(start=0.0, end=1.0, text=text, speaker=speaker)


class TestApplyGlossary:

    def test_empty_glossary_passthrough(self) -> None:
        """Empty glossary returns input list unchanged (identity)."""
        segments = [_seg("hello")]
        result = apply_glossary(segments, {})
        assert result is segments  # same object

    def test_single_replacement(self) -> None:
        segments = [_seg("ツーニックの会議")]
        result = apply_glossary(segments, {"ツーニック": "TOONIQ"})
        assert result[0].text == "TOONIQの会議"

    def test_multiple_replacements(self) -> None:
        segments = [_seg("figmaでリアクトのデザイン")]
        glossary = {"figma": "Figma", "リアクト": "React"}
        result = apply_glossary(segments, glossary)
        assert "Figma" in result[0].text
        assert "React" in result[0].text

    def test_case_insensitive_default(self) -> None:
        segments = [_seg("FIGMA is great")]
        result = apply_glossary(segments, {"figma": "Figma"})
        assert result[0].text == "Figma is great"

    def test_case_sensitive_mode(self) -> None:
        segments = [_seg("FIGMA is great")]
        result = apply_glossary(
            segments, {"figma": "Figma"}, case_sensitive=True,
        )
        assert result[0].text == "FIGMA is great"  # no change

    def test_no_match_returns_original(self) -> None:
        seg = _seg("unrelated text")
        result = apply_glossary([seg], {"ツーニック": "TOONIQ"})
        assert result[0] is seg  # same object

    def test_segment_immutability(self) -> None:
        seg = _seg("ツーニック")
        result = apply_glossary([seg], {"ツーニック": "TOONIQ"})
        assert result[0] is not seg
        assert result[0].text == "TOONIQ"
        assert result[0].start == seg.start
        assert result[0].end == seg.end
        assert result[0].speaker == seg.speaker

    def test_regex_special_chars_escaped(self) -> None:
        segments = [_seg("Use C++ and node.js")]
        glossary = {"C++": "C Plus Plus", "node.js": "Node.js"}
        result = apply_glossary(segments, glossary)
        assert result[0].text == "Use C Plus Plus and Node.js"

    def test_multiple_segments(self) -> None:
        segments = [
            _seg("ツーニックの話", "Alice"),
            _seg("ツーニックについて", "Bob"),
        ]
        result = apply_glossary(segments, {"ツーニック": "TOONIQ"})
        assert result[0].text == "TOONIQの話"
        assert result[1].text == "TOONIQについて"

    def test_preserves_segment_count(self) -> None:
        segments = [_seg("a"), _seg("b"), _seg("c")]
        result = apply_glossary(segments, {"x": "y"})
        assert len(result) == 3

    def test_empty_segments_list(self) -> None:
        result = apply_glossary([], {"foo": "bar"})
        assert result == []

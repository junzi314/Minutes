"""Tests for src/transcriber.py.

GPU tests are skipped automatically when no CUDA device is available.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import WhisperConfig
from src.errors import TranscriptionError
from src.transcriber import Segment, Transcriber

SAMPLE_AAC = Path("samples/1-shake344.aac")

# Default config for unit tests (CPU, tiny model for speed)
_UNIT_CFG = WhisperConfig(
    model="tiny",
    language="ja",
    device="cpu",
    compute_type="float32",
    beam_size=1,
    vad_filter=False,
)


# ---------------------------------------------------------------------------
# Unit tests (no GPU required)
# ---------------------------------------------------------------------------


class TestTranscriberUnit:
    def test_not_loaded_by_default(self) -> None:
        t = Transcriber(_UNIT_CFG)
        assert t.is_loaded is False

    def test_transcribe_file_before_load_raises(self, tmp_path: Path) -> None:
        t = Transcriber(_UNIT_CFG)
        dummy = tmp_path / "dummy.aac"
        dummy.write_bytes(b"")
        with pytest.raises(TranscriptionError, match="not loaded"):
            t.transcribe_file(dummy, "speaker")

    def test_transcribe_file_missing_file(self) -> None:
        t = Transcriber(_UNIT_CFG)
        # Manually set model to bypass load check
        t._model = MagicMock()
        with pytest.raises(TranscriptionError, match="not found"):
            t.transcribe_file(Path("/nonexistent/audio.aac"), "speaker")

    def test_load_model_idempotent(self) -> None:
        t = Transcriber(_UNIT_CFG)
        with patch("src.transcriber.WhisperModel") as mock_cls:
            t.load_model()
            t.load_model()  # second call should be no-op
            mock_cls.assert_called_once()

    def test_transcribe_file_with_mock(self, tmp_path: Path) -> None:
        t = Transcriber(_UNIT_CFG)

        # Create a mock model
        mock_segment = MagicMock()
        mock_segment.start = 0.0
        mock_segment.end = 2.5
        mock_segment.text = "  テスト  "

        mock_info = MagicMock()
        mock_info.language = "ja"
        mock_info.language_probability = 0.95

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], mock_info)
        t._model = mock_model

        dummy = tmp_path / "test.aac"
        dummy.write_bytes(b"\x00")

        result = t.transcribe_file(dummy, "Alice")
        assert len(result) == 1
        assert result[0].speaker == "Alice"
        assert result[0].text == "テスト"  # stripped
        assert result[0].start == 0.0
        assert result[0].end == 2.5

    def test_transcribe_all_with_mock(self, tmp_path: Path) -> None:
        from src.audio_source import SpeakerAudio, SpeakerInfo

        t = Transcriber(_UNIT_CFG)

        mock_seg1 = MagicMock(start=0.0, end=1.0, text="Hello")
        mock_seg2 = MagicMock(start=1.0, end=2.0, text="World")
        mock_info = MagicMock(language="ja", language_probability=0.9)

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_seg1, mock_seg2], mock_info)
        t._model = mock_model

        f1 = tmp_path / "1-alice.aac"
        f1.write_bytes(b"\x00")
        f2 = tmp_path / "2-bob.aac"
        f2.write_bytes(b"\x00")

        tracks = [
            SpeakerAudio(speaker=SpeakerInfo(track=1, username="alice", user_id=1), file_path=f1),
            SpeakerAudio(speaker=SpeakerInfo(track=2, username="bob", user_id=2), file_path=f2),
        ]

        result = t.transcribe_all(tracks)
        assert len(result) == 4  # 2 segments per track, 2 tracks
        assert mock_model.transcribe.call_count == 2

    def test_empty_text_segments_filtered(self, tmp_path: Path) -> None:
        t = Transcriber(_UNIT_CFG)

        mock_seg1 = MagicMock(start=0.0, end=1.0, text="  ")  # blank after strip
        mock_seg2 = MagicMock(start=1.0, end=2.0, text="有効")
        mock_info = MagicMock(language="ja", language_probability=0.9)

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_seg1, mock_seg2], mock_info)
        t._model = mock_model

        f = tmp_path / "test.aac"
        f.write_bytes(b"\x00")

        result = t.transcribe_file(f, "speaker")
        assert len(result) == 1
        assert result[0].text == "有効"


# ---------------------------------------------------------------------------
# GPU integration tests (skipped if no CUDA)
# ---------------------------------------------------------------------------

_has_cuda = False
try:
    import ctranslate2
    _has_cuda = ctranslate2.get_cuda_device_count() > 0
except Exception:
    pass

_GPU_CFG = WhisperConfig(
    model="large-v3",
    language="ja",
    device="cuda",
    compute_type="float16",
    beam_size=5,
    vad_filter=True,
)


@pytest.mark.skipif(not _has_cuda, reason="No CUDA device available")
@pytest.mark.skipif(not SAMPLE_AAC.exists(), reason="Sample AAC not found")
class TestTranscriberGPU:
    @pytest.fixture(scope="class")
    def gpu_transcriber(self) -> Transcriber:
        t = Transcriber(_GPU_CFG)
        t.load_model()
        return t

    def test_model_loads_on_gpu(self, gpu_transcriber: Transcriber) -> None:
        assert gpu_transcriber.is_loaded

    def test_transcribe_sample_aac(self, gpu_transcriber: Transcriber) -> None:
        segments = gpu_transcriber.transcribe_file(SAMPLE_AAC, "shake344")
        assert len(segments) > 0

        # Check segments have valid structure
        for seg in segments:
            assert isinstance(seg, Segment)
            assert seg.start >= 0.0
            assert seg.end > seg.start
            assert len(seg.text) > 0
            assert seg.speaker == "shake344"

    def test_transcribe_produces_japanese(self, gpu_transcriber: Transcriber) -> None:
        segments = gpu_transcriber.transcribe_file(SAMPLE_AAC, "shake344")
        all_text = " ".join(s.text for s in segments)
        # Check for CJK characters (Japanese text expected)
        has_cjk = any("\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef" for ch in all_text)
        assert has_cjk, f"Expected Japanese text, got: {all_text[:200]}"

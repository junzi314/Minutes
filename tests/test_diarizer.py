"""Unit tests for src/diarizer.py."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config import DiarizationConfig
from src.diarizer import DiarSegment, Diarizer, DiariZenDiarizer
from src.errors import DiarizationError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def diar_cfg() -> DiarizationConfig:
    return DiarizationConfig(enabled=True, model="test-model", device="cpu")


def _make_annotation(tracks: list[tuple[float, float, str]]) -> MagicMock:
    """Create mock pyannote Annotation from (start, end, speaker) tuples."""
    annotation = MagicMock()

    def itertracks(yield_label: bool = False):
        for start, end, speaker in tracks:
            seg = SimpleNamespace(start=start, end=end)
            if yield_label:
                yield (seg, "track", speaker)
            else:
                yield (seg, "track")

    annotation.itertracks = itertracks
    return annotation


def _patch_diarizen_import(mock_pipeline_cls: MagicMock):
    """Patch sys.modules so that ``from diarizen.pipelines.inference import ...`` works."""
    inference_mod = MagicMock(DiariZenPipeline=mock_pipeline_cls)
    return patch.dict(
        "sys.modules",
        {
            "diarizen": MagicMock(),
            "diarizen.pipelines": MagicMock(),
            "diarizen.pipelines.inference": inference_mod,
        },
    )


def _patch_diarizen_import_error():
    """Patch sys.modules so that importing diarizen raises ImportError."""
    return patch.dict(
        "sys.modules",
        {
            "diarizen": None,
            "diarizen.pipelines": None,
            "diarizen.pipelines.inference": None,
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_diarizer_protocol_compliance(self, diar_cfg: DiarizationConfig) -> None:
        """DiariZenDiarizer satisfies the Diarizer Protocol."""
        diarizer = DiariZenDiarizer(diar_cfg)
        assert isinstance(diarizer, Diarizer)


class TestLoadModel:
    def test_load_model_success(self, diar_cfg: DiarizationConfig) -> None:
        """load_model calls from_pretrained and sets pipeline."""
        mock_pipeline_cls = MagicMock()
        mock_pipeline_instance = MagicMock()
        mock_pipeline_cls.from_pretrained.return_value = mock_pipeline_instance

        diarizer = DiariZenDiarizer(diar_cfg)
        assert not diarizer.is_loaded

        with _patch_diarizen_import(mock_pipeline_cls):
            diarizer.load_model()

        assert diarizer.is_loaded
        mock_pipeline_cls.from_pretrained.assert_called_once_with("test-model")

    def test_load_model_import_error(self, diar_cfg: DiarizationConfig) -> None:
        """load_model raises DiarizationError when diarizen is not installed."""
        diarizer = DiariZenDiarizer(diar_cfg)

        with _patch_diarizen_import_error():
            with pytest.raises(DiarizationError, match="DiariZen is not installed"):
                diarizer.load_model()

        assert not diarizer.is_loaded

    def test_load_model_model_error(self, diar_cfg: DiarizationConfig) -> None:
        """load_model raises DiarizationError when from_pretrained fails."""
        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.from_pretrained.side_effect = OSError("download failed")

        diarizer = DiariZenDiarizer(diar_cfg)

        with _patch_diarizen_import(mock_pipeline_cls):
            with pytest.raises(DiarizationError, match="Failed to load DiariZen model"):
                diarizer.load_model()

        assert not diarizer.is_loaded

    def test_load_model_idempotent(self, diar_cfg: DiarizationConfig) -> None:
        """Calling load_model twice only invokes from_pretrained once."""
        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.from_pretrained.return_value = MagicMock()

        diarizer = DiariZenDiarizer(diar_cfg)

        with _patch_diarizen_import(mock_pipeline_cls):
            diarizer.load_model()
            diarizer.load_model()

        mock_pipeline_cls.from_pretrained.assert_called_once()


class TestDiarize:
    def test_diarize_success(
        self, diar_cfg: DiarizationConfig, tmp_path: Path
    ) -> None:
        """diarize returns sorted DiarSegments from pipeline output."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00" * 100)

        annotation = _make_annotation([
            (0.0, 1.5, "Speaker_0"),
            (1.5, 3.0, "Speaker_1"),
            (3.0, 4.0, "Speaker_0"),
        ])

        mock_pipeline_cls = MagicMock()
        mock_pipeline_instance = MagicMock()
        mock_pipeline_cls.from_pretrained.return_value = mock_pipeline_instance
        mock_pipeline_instance.return_value = annotation

        diarizer = DiariZenDiarizer(diar_cfg)

        with _patch_diarizen_import(mock_pipeline_cls):
            diarizer.load_model()

        result = diarizer.diarize(audio_file)

        assert len(result) == 3
        assert result[0] == DiarSegment(start=0.0, end=1.5, speaker="Speaker_0")
        assert result[1] == DiarSegment(start=1.5, end=3.0, speaker="Speaker_1")
        assert result[2] == DiarSegment(start=3.0, end=4.0, speaker="Speaker_0")

        mock_pipeline_instance.assert_called_once_with(
            str(audio_file), sess_name="test"
        )

    def test_diarize_not_loaded(
        self, diar_cfg: DiarizationConfig, tmp_path: Path
    ) -> None:
        """diarize raises DiarizationError when model is not loaded."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00" * 100)

        diarizer = DiariZenDiarizer(diar_cfg)

        with pytest.raises(DiarizationError, match="not loaded"):
            diarizer.diarize(audio_file)

    def test_diarize_file_not_found(
        self, diar_cfg: DiarizationConfig, tmp_path: Path
    ) -> None:
        """diarize raises DiarizationError when audio file doesn't exist."""
        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.from_pretrained.return_value = MagicMock()

        diarizer = DiariZenDiarizer(diar_cfg)
        with _patch_diarizen_import(mock_pipeline_cls):
            diarizer.load_model()

        missing = tmp_path / "nonexistent.wav"
        with pytest.raises(DiarizationError, match="Audio file not found"):
            diarizer.diarize(missing)

    def test_diarize_oom(
        self, diar_cfg: DiarizationConfig, tmp_path: Path
    ) -> None:
        """diarize raises DiarizationError with GPU message on CUDA OOM."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00" * 100)

        mock_pipeline_cls = MagicMock()
        mock_pipeline_instance = MagicMock()
        mock_pipeline_cls.from_pretrained.return_value = mock_pipeline_instance
        mock_pipeline_instance.side_effect = RuntimeError(
            "CUDA out of memory. Tried to allocate 256 MiB"
        )

        diarizer = DiariZenDiarizer(diar_cfg)
        with _patch_diarizen_import(mock_pipeline_cls):
            diarizer.load_model()

        with pytest.raises(DiarizationError, match="GPU out of memory"):
            diarizer.diarize(audio_file)

    def test_diarize_generic_error(
        self, diar_cfg: DiarizationConfig, tmp_path: Path
    ) -> None:
        """diarize wraps non-CUDA RuntimeError in DiarizationError."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00" * 100)

        mock_pipeline_cls = MagicMock()
        mock_pipeline_instance = MagicMock()
        mock_pipeline_cls.from_pretrained.return_value = mock_pipeline_instance
        mock_pipeline_instance.side_effect = RuntimeError("some tensor error")

        diarizer = DiariZenDiarizer(diar_cfg)
        with _patch_diarizen_import(mock_pipeline_cls):
            diarizer.load_model()

        with pytest.raises(DiarizationError, match="runtime error"):
            diarizer.diarize(audio_file)

    def test_diarize_sorted_output(
        self, diar_cfg: DiarizationConfig, tmp_path: Path
    ) -> None:
        """diarize sorts segments by start time even when itertracks is unordered."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00" * 100)

        # Out-of-order segments
        annotation = _make_annotation([
            (5.0, 6.0, "Speaker_1"),
            (0.0, 2.0, "Speaker_0"),
            (3.0, 4.0, "Speaker_1"),
        ])

        mock_pipeline_cls = MagicMock()
        mock_pipeline_instance = MagicMock()
        mock_pipeline_cls.from_pretrained.return_value = mock_pipeline_instance
        mock_pipeline_instance.return_value = annotation

        diarizer = DiariZenDiarizer(diar_cfg)
        with _patch_diarizen_import(mock_pipeline_cls):
            diarizer.load_model()

        result = diarizer.diarize(audio_file)

        starts = [s.start for s in result]
        assert starts == sorted(starts)
        assert result[0].start == 0.0
        assert result[1].start == 3.0
        assert result[2].start == 5.0


class TestUnloadModel:
    def test_unload_model(self, diar_cfg: DiarizationConfig) -> None:
        """unload_model clears pipeline and calls torch.cuda.empty_cache."""
        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.from_pretrained.return_value = MagicMock()

        diarizer = DiariZenDiarizer(diar_cfg)
        with _patch_diarizen_import(mock_pipeline_cls):
            diarizer.load_model()
        assert diarizer.is_loaded

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True

        with patch.dict("sys.modules", {"torch": mock_torch}):
            diarizer.unload_model()

        assert not diarizer.is_loaded
        mock_torch.cuda.empty_cache.assert_called_once()

    def test_unload_model_not_loaded(self, diar_cfg: DiarizationConfig) -> None:
        """unload_model is a no-op when pipeline is None."""
        diarizer = DiariZenDiarizer(diar_cfg)
        assert not diarizer.is_loaded

        # Should not raise
        diarizer.unload_model()
        assert not diarizer.is_loaded


class TestVramLogging:
    def test_vram_logging(self) -> None:
        """_log_vram_usage calls torch.cuda functions when available."""
        from src.diarizer import _log_vram_usage

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.memory_allocated.return_value = 350 * 1024**2
        mock_torch.cuda.memory_reserved.return_value = 512 * 1024**2

        with patch.dict("sys.modules", {"torch": mock_torch}):
            _log_vram_usage("test context")

        mock_torch.cuda.is_available.assert_called_once()
        mock_torch.cuda.memory_allocated.assert_called_once()
        mock_torch.cuda.memory_reserved.assert_called_once()

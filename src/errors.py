"""Exception hierarchy for Discord Minutes Bot."""


class MinutesBotError(Exception):
    """Base exception for all bot errors."""

    def __init__(self, message: str, stage: str = "unknown") -> None:
        self.stage = stage
        super().__init__(message)


class DetectionError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="detection")


class AudioAcquisitionError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="audio_acquisition")


class CookTimeoutError(AudioAcquisitionError):
    pass


class ProcessingTimeoutError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="processing")


class TranscriptionError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="transcription")


class GenerationError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="generation")


class PostingError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="posting")


class DriveWatchError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="drive_watch")


class ConfigError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="config")

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DownloadResult:
    file_path: Path
    external_id: str
    platform: str
    duration_sec: float | None
    width: int | None
    height: int | None
    size_bytes: int
    sha256: str
    format: str
    strategy_used: str


class BaseDownloader(ABC):
    """Контракт стратегии: получить URL → положить файл в downloads_dir → вернуть метаданные."""

    name: str = "base"

    @abstractmethod
    async def download(self, url: str, *, downloads_dir: Path, quality: str) -> DownloadResult:
        ...

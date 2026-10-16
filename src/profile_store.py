"""Local, versioned user-profile storage kept outside Wiki and vector indexes."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4

from document_loader import LoadedDocument

ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = Path(os.getenv("PROFILE_DIR", str(ROOT / "data" / "private" / "profile"))).expanduser()


@dataclass(frozen=True)
class ProfileVersion:
    id: str
    source_name: str
    file_type: str
    text: str
    directory: Path
    created_at: float


class ProfileStore:
    def __init__(self, root: Path | None = None):
        self.root = (root or PROFILE_ROOT).expanduser()
        self.versions = self.root / "versions"
        self.current_path = self.root / "current.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.versions.mkdir(parents=True, exist_ok=True)

    def _current(self) -> dict | None:
        try:
            return json.loads(self.current_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def current(self) -> ProfileVersion | None:
        metadata = self._current()
        if not metadata:
            return None
        return self._read_version(metadata.get("id"))

    def _read_version(self, version_id: str | None) -> ProfileVersion | None:
        if not isinstance(version_id, str):
            return None
        directory = self.versions / version_id
        try:
            metadata = json.loads((directory / "profile.json").read_text(encoding="utf-8"))
            text = (directory / "resume.txt").read_text(encoding="utf-8")
            return ProfileVersion(version_id, metadata["source_name"], metadata["file_type"], text, directory, float(metadata["created_at"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, document: LoadedDocument, original: bytes) -> ProfileVersion:
        text = "\n\n".join(segment.text for segment in document.segments).strip()
        if not text:
            raise ValueError("简历未解析出可用文本")
        version_id = hashlib.sha256(original + text.encode("utf-8")).hexdigest()[:24]
        directory = self.versions / version_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "resume.txt").write_text(text, encoding="utf-8")
        (directory / ("original." + document.file_type)).write_bytes(original)
        metadata = {"id": version_id, "source_name": document.source, "file_type": document.file_type, "created_at": time.time(), "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        (directory / "profile.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        previous = self._current()
        self.current_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        if previous and previous.get("id") != version_id:
            self._prune_except({version_id, str(previous.get("id"))})
        return ProfileVersion(version_id, document.source, document.file_type, text, directory, metadata["created_at"])

    def snapshot(self, version_id: str | None = None) -> dict | None:
        version = self._read_version(version_id) if version_id else self.current()
        if not version:
            return None
        return {"id": version.id, "source_name": version.source_name, "file_type": version.file_type, "text": version.text, "created_at": version.created_at}

    def delete_current(self) -> None:
        current = self._current()
        if current:
            self._prune_except(set())
        try:
            self.current_path.unlink()
        except FileNotFoundError:
            pass

    def _prune_except(self, keep: set[str]) -> None:
        for directory in self.versions.iterdir():
            if directory.is_dir() and directory.name not in keep:
                shutil.rmtree(directory, ignore_errors=True)


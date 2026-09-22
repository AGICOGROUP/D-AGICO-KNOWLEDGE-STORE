import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    storage_root: Path
    schema: str = "public"
    max_upload_bytes: int = 512 * 1024 * 1024
    # Baseline parse timeout. Large/scanned PDFs get more via timeout_for(): small text
    # documents stay fast, a hung parser is still bounded.
    parse_timeout_seconds: float = 120
    parse_timeout_max_seconds: float = 600
    worker_max_attempts: int = 3
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    model_cache: Path = Path(".local/models")
    model_offline: bool = False
    expected_model_identity: str | None = None
    query_timeout_seconds: float = 5
    mcp_allowed_hosts: tuple[str, ...] = ("127.0.0.1:*", "localhost:*", "testserver")

    def timeout_for(self, size_bytes: int) -> float:
        """Adaptive parse budget: ~1s per 512 KB on top of the baseline, capped.

        A 5 MB scanned PDF gets ~130s+; a 50 MB monster gets the cap instead of hanging forever.
        """
        if size_bytes <= 0:
            return self.parse_timeout_seconds
        scaled = self.parse_timeout_seconds + size_bytes / (512 * 1024)
        return min(scaled, self.parse_timeout_max_seconds)

    @classmethod
    def from_env(cls):
        return cls(
            os.environ["AGICO_KB_DATABASE_URL"],
            Path(os.environ["AGICO_KB_STORAGE_ROOT"]),
            schema=os.environ.get("AGICO_KB_SCHEMA", "public"),
            model_offline=os.environ.get("AGICO_KB_MODEL_OFFLINE", "false").lower() == "true",
            expected_model_identity=os.environ.get("AGICO_KB_EXPECTED_MODEL_IDENTITY") or None,
            embedding_model=os.environ.get("AGICO_KB_EMBEDDING_MODEL", cls.embedding_model),
            model_cache=Path(os.environ.get("AGICO_KB_MODEL_CACHE", ".local/models")),
            mcp_allowed_hosts=tuple(
                os.environ.get("AGICO_KB_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*").split(",")
            ),
        )

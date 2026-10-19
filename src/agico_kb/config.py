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
    worker_concurrency: int = 5
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    model_cache: Path = Path(".local/models")
    model_offline: bool = False
    expected_model_identity: str | None = None
    query_timeout_seconds: float = 5
    mcp_allowed_hosts: tuple[str, ...] = ("127.0.0.1:*", "localhost:*", "testserver")
    public_url: str | None = None
    # Vision is opt-in and runs only during document/image ingestion.
    vision_config_file: Path | None = None
    vision_fallback_config_file: Path | None = None
    vision_fallback_api_url: str = 'https://cephalon.cloud/user-center/v1/model/chat/completions'
    vision_max_calls_per_day: int = 0  # Zero means unlimited.
    vision_max_images_per_file: int = 64
    vision_fallback_model: str = "glm-5.3-flashx"
    vision_pdf_model: str = ""  # Optional PDF-only route; other formats keep their configured model.
    naming_fallback_models: tuple[str, ...] = ()  # Text-only optional pool, independent of vision.

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
            public_url=os.environ.get('AGICO_KB_PUBLIC_URL') or None,
            worker_concurrency=max(1, min(5, int(os.environ.get("AGICO_KB_WORKER_CONCURRENCY", "5")))),
            model_offline=os.environ.get("AGICO_KB_MODEL_OFFLINE", "false").lower() == "true",
            expected_model_identity=os.environ.get("AGICO_KB_EXPECTED_MODEL_IDENTITY") or None,
            embedding_model=os.environ.get("AGICO_KB_EMBEDDING_MODEL", cls.embedding_model),
            model_cache=Path(os.environ.get("AGICO_KB_MODEL_CACHE", ".local/models")),
            mcp_allowed_hosts=tuple(
                os.environ.get("AGICO_KB_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*").split(",")
            ),
            vision_config_file=(
                Path(os.environ["AGICO_KB_VISION_CONFIG_FILE"])
                if os.environ.get("AGICO_KB_VISION_CONFIG_FILE") else None
            ),
            vision_max_calls_per_day=int(os.environ.get("AGICO_KB_VISION_MAX_CALLS_PER_DAY", "0")),
            vision_fallback_config_file=(Path(os.environ['AGICO_KB_VISION_FALLBACK_CONFIG_FILE'])
                if os.environ.get('AGICO_KB_VISION_FALLBACK_CONFIG_FILE') else None),
            vision_fallback_api_url=os.environ.get('AGICO_KB_VISION_FALLBACK_API_URL', cls.vision_fallback_api_url),
            vision_max_images_per_file=int(os.environ.get("AGICO_KB_VISION_MAX_IMAGES_PER_FILE", "64")),
            vision_fallback_model=os.environ.get("AGICO_KB_VISION_FALLBACK_MODEL", cls.vision_fallback_model),
            vision_pdf_model=os.environ.get("AGICO_KB_VISION_PDF_MODEL", ""),
            naming_fallback_models=tuple(dict.fromkeys(model.strip() for model in
                os.environ.get('AGICO_KB_NAMING_FALLBACK_MODELS','').split(',') if model.strip())),
        )

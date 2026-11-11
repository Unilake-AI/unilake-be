"""Cấu hình ứng dụng được nạp từ biến môi trường."""

from __future__ import annotations

import socket

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Cấu hình ứng dụng có hỗ trợ biến môi trường."""

    model_config = SettingsConfigDict(
        env_file=(
            ".env",
            ".env.bronze-accounts.local",
            ".env.silver-backend.local",
            ".env.source-reader.local",
            ".env.upload-backend.local",
        ),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "UniLake AI"
    app_env: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = Field(default=False, alias="DEBUG")
    api_v1_prefix: str = Field(default="/api/v1", alias="API_V1_PREFIX")  # MỚI
    cors_origins: str = Field(
        default=(
            "http://localhost:3000,http://localhost:3001,"
            "http://127.0.0.1:3000,http://127.0.0.1:3001,"
            "https://unilake-fe.vercel.app"
        ),
        alias="CORS_ORIGINS",
    )  # MỚI
    cors_origin_regex: str = Field(default="", alias="CORS_ORIGIN_REGEX")

    db_host: str = Field(default="localhost", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_user: str = Field(default="unilake", alias="DB_USER")
    db_password: str = Field(default="unilake", alias="DB_PASSWORD")
    db_name: str = Field(default="unilake", alias="DB_NAME")
    db_echo: bool = Field(default=False, alias="DB_ECHO")
    # NEW: role chỉ đọc schema gold, database nguồn giả lập
    db_readonly_user: str = Field(default="unilake_reader", alias="DB_READONLY_USER")
    db_readonly_password: str = Field(default="unilake_reader", alias="DB_READONLY_PASSWORD")
    db_statement_timeout_ms: int = Field(default=5000, alias="DB_STATEMENT_TIMEOUT_MS")
    db_source_name: str = Field(default="unilake_source", alias="DB_SOURCE_NAME")

    minio_endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
    # Credentials quản trị chỉ dùng cho provisioning/CLI, không dùng trong runtime.
    minio_access_key: str = Field(default="", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="", alias="MINIO_SECRET_KEY")
    bronze_writer_access_key: str = Field(default="", alias="BRONZE_WRITER_ACCESS_KEY")
    bronze_writer_secret_key: str = Field(default="", alias="BRONZE_WRITER_SECRET_KEY", repr=False)
    bronze_verifier_access_key: str = Field(default="", alias="BRONZE_VERIFIER_ACCESS_KEY")
    bronze_verifier_secret_key: str = Field(
        default="", alias="BRONZE_VERIFIER_SECRET_KEY", repr=False
    )
    bronze_reader_access_key: str = Field(default="", alias="BRONZE_READER_ACCESS_KEY")
    bronze_reader_secret_key: str = Field(default="", alias="BRONZE_READER_SECRET_KEY", repr=False)
    silver_writer_access_key: str = Field(default="", alias="SILVER_WRITER_ACCESS_KEY")
    silver_writer_secret_key: str = Field(default="", alias="SILVER_WRITER_SECRET_KEY", repr=False)
    source_minio_access_key: str = Field(default="", alias="SOURCE_MINIO_ACCESS_KEY")
    source_minio_secret_key: str = Field(default="", alias="SOURCE_MINIO_SECRET_KEY", repr=False)
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")
    # NEW: region và bucket cho Bronze / Silver / Gold
    minio_region: str = Field(default="us-east-1", alias="MINIO_REGION")
    minio_bucket_bronze: str = Field(default="bronze", alias="MINIO_BUCKET_BRONZE")
    minio_bucket_silver: str = Field(default="silver", alias="MINIO_BUCKET_SILVER")
    silver_profiles_path: str = Field(
        default="config/silver/profiles.json", alias="SILVER_PROFILES_PATH"
    )

    minio_bucket_uploads: str = Field(default="ingestion-uploads", alias="MINIO_BUCKET_UPLOADS")
    upload_writer_access_key: str = Field(default="", alias="UPLOAD_WRITER_ACCESS_KEY")
    upload_writer_secret_key: str = Field(default="", alias="UPLOAD_WRITER_SECRET_KEY", repr=False)
    upload_reader_access_key: str = Field(default="", alias="UPLOAD_READER_ACCESS_KEY")
    upload_reader_secret_key: str = Field(default="", alias="UPLOAD_READER_SECRET_KEY", repr=False)
    upload_cleanup_access_key: str = Field(default="", alias="UPLOAD_CLEANUP_ACCESS_KEY")
    upload_cleanup_secret_key: str = Field(
        default="", alias="UPLOAD_CLEANUP_SECRET_KEY", repr=False
    )
    upload_max_bytes: int = Field(default=50 * 1024 * 1024, gt=0, alias="UPLOAD_MAX_BYTES")
    upload_max_records: int = Field(default=1_000_000, gt=0, alias="UPLOAD_MAX_RECORDS")
    upload_max_columns: int = Field(default=500, gt=0, alias="UPLOAD_MAX_COLUMNS")
    upload_max_cell_chars: int = Field(default=1_000_000, gt=0, alias="UPLOAD_MAX_CELL_CHARS")
    upload_timeout_seconds: int = Field(default=60, gt=0, alias="UPLOAD_TIMEOUT_SECONDS")
    upload_max_expanded_bytes: int = Field(
        default=200 * 1024 * 1024, gt=0, alias="UPLOAD_MAX_EXPANDED_BYTES"
    )
    ingestion_worker_id: str = Field(
        default_factory=socket.gethostname, alias="INGESTION_WORKER_ID"
    )
    worker_heartbeat_seconds: int = Field(default=10, gt=0, alias="WORKER_HEARTBEAT_SECONDS")
    worker_health_stale_seconds: int = Field(default=45, gt=0, alias="WORKER_HEALTH_STALE_SECONDS")
    ingestion_retry_max_attempts: int = Field(default=3, ge=1, alias="INGESTION_RETRY_MAX_ATTEMPTS")
    ingestion_retry_first_delay_seconds: int = Field(
        default=10, gt=0, alias="INGESTION_RETRY_FIRST_DELAY_SECONDS"
    )
    ingestion_retry_second_delay_seconds: int = Field(
        default=30, gt=0, alias="INGESTION_RETRY_SECOND_DELAY_SECONDS"
    )
    upload_staging_retention_hours: int = Field(
        default=168, gt=0, alias="UPLOAD_STAGING_RETENTION_HOURS"
    )

    minio_bucket_gold: str = Field(default="gold", alias="MINIO_BUCKET_GOLD")

    # MỚI: DuckDB
    duckdb_path: str = Field(default=":memory:", alias="DUCKDB_PATH")
    duckdb_threads: int = Field(default=4, alias="DUCKDB_THREADS")
    duckdb_memory_limit: str = Field(default="2GB", alias="DUCKDB_MEMORY_LIMIT")

    jwt_secret_key: str = Field(
        default="change-me-in-production-use-openssl-rand-hex-32",
        alias="JWT_SECRET_KEY",
    )
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(
        default=60 * 24,
        alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    )

    llm_provider: str = Field(default="mock", alias="LLM_PROVIDER")  # đổi default openai -> mock
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="", alias="LLM_MODEL")
    llm_timeout_seconds: int = Field(default=30, alias="LLM_TIMEOUT_SECONDS")  # MỚI

    @property
    def database_url(self) -> str:
        """Tạo URL kết nối PostgreSQL bất đồng bộ."""
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def sync_database_url(self) -> str:
        """Tạo URL kết nối PostgreSQL đồng bộ."""
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def readonly_database_url(self) -> str:  # MỚI
        """Tạo URL bất đồng bộ cho vai trò Gold chỉ đọc mà NLQ và Dashboard sử dụng."""
        return (
            f"postgresql+asyncpg://{self.db_readonly_user}:{self.db_readonly_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def duckdb_postgres_dsn(self) -> str:  # MỚI
        """Tạo DSN libpq để DuckDB ghi Gold qua `ATTACH ... (TYPE postgres)`."""
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={self.db_password}"
        )

    @property
    def minio_endpoint_url(self) -> str:  # MỚI
        """Tạo URL MinIO đầy đủ (kèm giao thức) cho tùy chọn lưu trữ deltalake."""
        scheme = "https" if self.minio_secure else "http"
        return f"{scheme}://{self.minio_endpoint}"

    @property
    def cors_origin_list(self) -> list[str]:  # MỚI
        """Tách CORS_ORIGINS được phân cách bằng dấu phẩy thành danh sách."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cors_origin_regex_pattern(self) -> str | None:
        """Trả về regex CORS origin nếu được cấu hình, bỏ qua chuỗi rỗng."""
        pattern = self.cors_origin_regex.strip()
        return pattern or None


settings = Settings()

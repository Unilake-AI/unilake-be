"""Cấu hình ứng dụng được nạp từ biến môi trường."""

from __future__ import annotations

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
        default="http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001",
        alias="CORS_ORIGINS",
    )  # MỚI

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


settings = Settings()

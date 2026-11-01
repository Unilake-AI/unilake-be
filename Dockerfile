# syntax=docker/dockerfile:1
# ==============================================================================
# Giai đoạn 1: Dựng ứng dụng và cài thư viện phụ thuộc
# ==============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Thiết lập các biến môi trường
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Cài công cụ biên dịch của hệ thống
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Tạo môi trường ảo
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Worker chọn danh sách gọn; backend vẫn dùng requirements.txt mặc định.
ARG REQUIREMENTS_FILE=requirements.txt
COPY ${REQUIREMENTS_FILE} ./requirements.txt

# Cache tải package tồn tại qua các lần build, không được đưa vào runtime image.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --no-deps -e .

# ==============================================================================
# Giai đoạn 2: Môi trường chạy production
# ==============================================================================
FROM python:3.11-slim AS runtime

WORKDIR /app

# Thiết lập các biến môi trường
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# Cài thư viện hệ thống cần khi chạy và tạo người dùng không có quyền root
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash appuser

# Sao chép thư viện đã cài từ giai đoạn dựng ứng dụng
COPY --from=builder /opt/venv /opt/venv

# Sao chép mã nguồn ứng dụng, migration và cấu hình
COPY app ./app
COPY docs ./docs
COPY data/standardization ./data/standardization
COPY alembic ./alembic
COPY alembic.ini pyproject.toml ./

# Chuyển quyền sở hữu cho người dùng không có quyền root
RUN chown -R appuser:appuser /app

USER appuser

# PostgreSQLReader dùng LOAD postgres; cài bằng đúng người dùng và phiên bản của môi trường chạy.
RUN python -c "import duckdb; connection = duckdb.connect(); connection.execute('INSTALL postgres'); connection.close()"

EXPOSE 8000

# Cấu hình kiểm tra tình trạng hoạt động
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-8000}/health" || exit 1

# Khởi chạy máy chủ ASGI Uvicorn
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]

FROM python:3.12-slim

# uv 本体（ローカルと同じバージョンに固定）
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/srv/.venv/bin:$PATH"

# 日本語フォント（明朝・ゴシック）と、Claude Code のインストールに使う curl
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-cjk curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Claude Code（使用量の取得用）。公式インストーラでネイティブ版をバージョン固定で入れる
ARG CLAUDE_CODE_VERSION=2.1.311
ENV DISABLE_AUTOUPDATER=1 \
    DISABLE_TELEMETRY=1
RUN curl -fsSL https://claude.ai/install.sh | bash -s ${CLAUDE_CODE_VERSION} \
 && ln -sf /root/.local/bin/claude /usr/local/bin/claude \
 && claude --version

WORKDIR /srv

# 1) 依存だけ先に入れる（コード変更時にこの層をキャッシュで再利用できる）
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev \
 && playwright install --with-deps chromium

# 2) アプリ本体
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# タイムゾーンの既定。.env の TZ で上書きできる（重い層のキャッシュを壊さないよう最後に置く）
ENV TZ=Asia/Tokyo

EXPOSE 8080
CMD ["kindle-dash-server", "--host", "0.0.0.0", "--port", "8080"]

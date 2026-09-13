# Build from the parent workspace so the library and server remain siblings.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.12.10
WORKDIR /app
COPY kenkui/pyproject.toml kenkui/README.md kenkui/LICENSE ./kenkui/
COPY kenkui/src ./kenkui/src
COPY kenkui-server/pyproject.toml kenkui-server/uv.lock kenkui-server/README.md kenkui-server/LICENSE ./kenkui-server/
COPY kenkui-server/src ./kenkui-server/src
COPY kenkui-server/migrations ./kenkui-server/migrations
WORKDIR /app/kenkui-server
RUN uv sync --frozen --extra hosted --no-dev --no-cache
ENV PATH="/app/kenkui-server/.venv/bin:$PATH"
RUN chmod -R a+rX /app/kenkui/src /app/kenkui-server/src /app/kenkui-server/migrations && useradd --create-home --uid 10001 kenkui
USER kenkui
EXPOSE 8000
CMD ["python", "-m", "kenkui_server.hosted", "serve"]

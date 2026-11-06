# The Sidekick needs Python for the app, Node for its MCP servers, and Chromium for the
# browser server. This runs unchanged under Docker locally and on a Hugging Face Docker Space.
FROM python:3.12-slim-bookworm

COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /usr/local/bin/uv

# Hugging Face runs containers as uid 1000, so everything is set up for that user
RUN useradd -m -u 1000 user && mkdir -p /app/sandbox && chown -R user:user /app
WORKDIR /app

COPY --chown=user:user package.json package-lock.json pyproject.toml uv.lock ./
RUN npm ci --omit=dev \
 && npx --no-install playwright install-deps chromium \
 && chown -R user:user /app

USER user
ENV HOME=/home/user \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    SIDEKICK_HEADLESS=true

RUN npx --no-install playwright install chromium \
 && uv sync --frozen --no-dev

COPY --chown=user:user app.py sidekick.py sidekick_tools.py styles.py ./

EXPOSE 7860
CMD ["python", "app.py"]

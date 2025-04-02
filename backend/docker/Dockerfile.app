FROM ubuntu:22.04

RUN apt-get update -y && apt-get install -y curl libzmq3-dev
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="$PATH:/root/.local/bin"

WORKDIR /app
RUN uv venv --python 3.10
COPY pyproject.toml uv.lock ./
RUN uv sync --extra fastapi
EXPOSE 8000
COPY backend ./backend
# Old Javascript frontend
# RUN mkdir assets
# COPY assets ./assets
CMD ["uv", "run", "backend/app.py", "--docker"]

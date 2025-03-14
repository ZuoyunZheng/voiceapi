FROM nvcr.io/nvidia/cuda:11.8.0-devel-ubuntu22.04

RUN apt-get update -y && apt-get install -y python3 python3-pip libasound2 libcublas-12-6 libcudnn8-dev curl
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="$PATH:/root/.local/bin"

WORKDIR /app
RUN uv venv --python 3.10
COPY pyproject.toml uv.lock ./
RUN uv sync
RUN uv pip install sherpa-onnx==1.10.27+cuda -f https://k2-fsa.github.io/sherpa/onnx/cuda.html
COPY voiceapi ./voiceapi
RUN mkdir assets
COPY assets ./assets
COPY app.py ./
CMD ["uv", "run", "app.py", "--docker"]

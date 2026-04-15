FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
# Skip runtime Rust compilation; extensions are prebuilt at image build time
ENV SKIP_RUST_COMPILE=true
ENV PYTHONUNBUFFERED=1
ENV PASSIVBOT_CONFIG=configs/config_aster.json
ENV PASSIVBOT_ARGS=""

# Install system build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        pkg-config \
        libssl-dev \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app

# Copy all source code
COPY . .

# Install Python dependencies (including Aster runtime deps and maturin for Rust builds)
RUN pip install --no-cache-dir -r requirements-live.txt -r requirements-rust.txt

# Build Rust extensions for backtesting and optimization
RUN cd passivbot-rust \
    && maturin build --release \
    && pip install target/wheels/*.whl

# Default command to run the bot; override PASSIVBOT_CONFIG / PASSIVBOT_ARGS as needed
CMD ["sh", "-lc", "python src/main.py \"${PASSIVBOT_CONFIG}\" ${PASSIVBOT_ARGS}"]

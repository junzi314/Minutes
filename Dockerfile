# ============================================================
# Discord Minutes Bot - GPU-enabled Docker image
# ============================================================
# Base: NVIDIA CUDA 12.6 + cuDNN runtime on Ubuntu 24.04
# Python 3.12 ships natively with Ubuntu 24.04.
#
# The NVIDIA base image provides CUDA/cuDNN libraries at the
# system level, so the LD_LIBRARY_PATH workaround used by
# start.sh (for nvidia pip packages) is NOT needed here.
# ============================================================

FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
# Ensure Python output is sent straight to the container logs
ENV PYTHONUNBUFFERED=1
# Store Whisper model cache under /app (works regardless of user)
ENV HF_HOME=/app/.cache/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for runtime
RUN useradd -r -m -s /bin/false botuser

WORKDIR /app

# Install Python dependencies first (Docker layer caching)
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy application code
COPY bot.py ./
COPY src/ src/
COPY prompts/ prompts/

# Create runtime directories and set ownership
RUN mkdir -p logs .cache/huggingface && chown -R botuser:botuser /app

USER botuser

CMD ["python3", "bot.py"]

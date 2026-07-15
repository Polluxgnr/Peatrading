# PEA Sniper Terminal V-Prime - single image, two roles (daemon + dashboard).
# Python 3.11 (x64) is required: streamlit's pyarrow has no 3.13/arm64 wheel.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Paris

WORKDIR /app

# System deps: tzdata for Paris scheduling, build tools for wheels that need them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching).
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the application code.
COPY . .

# Persisted state + Streamlit UI port.
VOLUME ["/app/database"]
EXPOSE 8501

# Default role is the daemon; docker-compose overrides the command for the UI.
CMD ["python", "main_scheduler.py"]

# Minimal runner for charge_log.py
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OUTPUT_DIR=/data

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "gspread>=6.0.0" \
        "google-auth>=2.0.0"

COPY charge_log.py /app/charge_log.py

RUN mkdir -p /data /secrets

# Run as root inside container (LXC-owned bind mounts; secrets not in image).
ENTRYPOINT ["python", "/app/charge_log.py"]
CMD ["rebuild", "--sheet"]

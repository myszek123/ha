FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Warsaw \
    STATE_DIR=/data

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "requests>=2.31.0"

COPY presence_sim.py /app/presence_sim.py

RUN mkdir -p /data /secrets

ENTRYPOINT ["python", "/app/presence_sim.py"]
CMD ["run"]

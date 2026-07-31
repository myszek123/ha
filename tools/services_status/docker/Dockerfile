FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Warsaw
WORKDIR /app
COPY services_status.py /app/services_status.py
ENTRYPOINT ["python", "/app/services_status.py"]

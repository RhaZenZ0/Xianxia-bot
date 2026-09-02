FROM python:3.12-slim

LABEL org.opencontainers.image.title="Jade Meridian Realm Xianxia Discord Bot" \
      org.opencontainers.image.version="0.19.9"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

CMD ["python", "-m", "app.bot"]

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && mkdir /data && chown 65532:65532 /data
COPY relay ./relay
USER 65532:65532
EXPOSE 8011
CMD ["uvicorn", "relay.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8011"]

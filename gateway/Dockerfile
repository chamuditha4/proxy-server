# Dockerfile for the single-endpoint proxy gateway
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV GATEWAY_PORT 8899
# Bind inside the container; the host publishes these on chosen interfaces.
ENV GATEWAY_HOST 0.0.0.0
ENV STATUS_HOST 0.0.0.0

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway.py registry.py dns_sync.py ./
COPY discovery ./discovery

EXPOSE 8899
EXPOSE 9000

CMD ["python", "gateway.py"]

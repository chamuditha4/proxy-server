# Dockerfile for Python-based authenticated proxy server
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PROXY_PORT 8899
ENV PROXY_USER user
ENV PROXY_PASS pass

# Set work directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the proxy script
COPY proxy_server.py .

# Expose the proxy port
EXPOSE 8899

# Run the proxy server
CMD ["python", "proxy_server.py"]

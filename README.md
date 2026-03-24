# Dockerized Authenticated Proxy Server

Turn any data center server into a secure, authenticated proxy server instantly with zero configuration hassles.

## Purpose

This project is designed to easily convert a standard server into a high-performance HTTP/HTTPS proxy. It is ideal for developers and privacy-conscious users who need a dedicated proxy using their own server's IP address.

## Features

- **Instant Deployment**: Get up and running in seconds using Docker or Docker Compose.
- **Secure Authentication**: Built-in support for Basic Authentication (User/Password).
- **HTTPS Tunneling**: Full support for `CONNECT` requests, allowing secure HTTPS traffic.
- **Lightweight**: Minimal resource footprint, powered by Python and `proxy.py`.

## Quick Start

### 1. Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/) (optional but recommended)

### 2. Configure Credentials

Create a `.env` file from the example:
```bash
cp .env.example .env
```
Edit the `.env` file and set your desired `PROXY_USER` and `PROXY_PASS`.

### 3. Start the Proxy

Use Docker Compose for the simplest setup:
```bash
docker-compose up -d
```
The proxy will now be listening on port **8899**.

## Browser Configuration

1. Go to your browser's **Proxy Settings**.
2. Set the **HTTP Proxy** to your **Server's IP Address**.
3. Set the **Port** to `8899`.
4. When you browse for the first time, you will be prompted for the username and password you set in the `.env` file.

## Troubleshooting

- **Firewall**: Ensure port **8899** is open on your server.
  - On Ubuntu/Debian: `sudo ufw allow 8899/tcp`
  - In cloud console (Hetzner, AWS, etc.): Add an inbound TCP rule for port 8899.

## License

MIT

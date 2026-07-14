# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

# Use the official Python image based on Alpine
FROM python:3.14-alpine

LABEL maintainer="kogeler"
LABEL description="Cloudflare DDNS Updater"
LABEL version="1.1.0"

# Use environment variables so that Python won't create .pyc files
# and the output is written directly to stdout (unbuffered).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Create a non-privileged user and group (pyuser)
# -D: do not create a home directory, -G: set the group
RUN addgroup -g 1000 pyuser && adduser -D -G pyuser -u 1000 pyuser

# Define the working directory inside the container
WORKDIR /app

# Copy the Python script (or any other necessary files) into the container
COPY cf_ddns.py .
COPY requirements.txt .

# Install dependencies without caching
RUN pip install --no-cache-dir -r requirements.txt

# Switch to the non-privileged user
USER pyuser

# Prometheus metrics endpoint (default port)
EXPOSE 9101

# Process-liveness check only: proves the metrics endpoint answers, not that
# DNS is synchronized. Shell form so the env var expands.
HEALTHCHECK CMD wget -q -O /dev/null "http://127.0.0.1:${CF_DDNS_METRICS_PORT:-9101}/metrics" || exit 1

# By default, run the script
CMD ["python", "cf_ddns.py"]

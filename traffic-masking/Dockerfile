# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

# Use the official Python image based on Alpine
FROM python:3.13-alpine

LABEL maintainer="kogeler"
LABEL description="UDP Traffic Masking System"
LABEL version="1.0.2"

# Optimize Python for containers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Create a non-privileged user and group (pyuser)
# -D: do not create a home directory, -G: set the group
RUN addgroup -g 1000 pyuser && adduser -D -G pyuser -u 1000 pyuser

WORKDIR /app

# Install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY masking_lib.py traffic_masking_server.py traffic_masking_client.py /app/
COPY enhanced/ /app/enhanced/

# Copy documentation
COPY *.md /app/

# Set ownership
RUN chown -R pyuser:pyuser /app

# Switch to the non-privileged user
USER pyuser

# Expose UDP port
EXPOSE 8888/udp

# Default entrypoint
ENTRYPOINT ["python"]

# Default command shows usage
CMD ["-c", "print('Usage:\\n  Server: python traffic_masking_server.py --min-mbps 2 --max-mbps 8 --advanced\\n  Client: python traffic_masking_client.py --server <IP> --response 0.3 --advanced')"]

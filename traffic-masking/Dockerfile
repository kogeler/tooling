FROM python:3.11-slim

LABEL maintainer="Traffic Masking Project"
LABEL description="UDP Traffic Masking System"
LABEL version="1.0.0"

# Optimize Python for containers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Create non-root user
RUN useradd -m -u 10001 -s /bin/bash app

WORKDIR /app

# Install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY masking_lib.py traffic_masking_server.py traffic_masking_client.py test_traffic_masking.py /app/
COPY enhanced/ /app/enhanced/

# Copy documentation
COPY *.md /app/

# Set ownership
RUN chown -R app:app /app

USER app

# Expose UDP port
EXPOSE 8888/udp

# Default entrypoint
ENTRYPOINT ["python"]

# Default command shows usage
CMD ["-c", "print('Usage:\\n  Server: python traffic_masking_server.py --min-mbps 2 --max-mbps 8 --advanced\\n  Client: python traffic_masking_client.py --server <IP> --response 0.3 --advanced\\n  Test:   python test_traffic_masking.py')"]

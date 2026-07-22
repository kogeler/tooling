# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

FROM python:3.14-alpine

ARG VERSION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup -g 1000 pyuser \
    && adduser -D -G pyuser -u 1000 pyuser \
    && install -d -o pyuser -g pyuser -m 0700 /run/secrets

WORKDIR /app

COPY .version /app/.version
RUN test -n "${VERSION}" && test "${VERSION}" = "$(cat /app/.version)"

LABEL org.opencontainers.image.authors="kogeler" \
      org.opencontainers.image.description="Experimental authenticated UDP cover-traffic generator" \
      org.opencontainers.image.source="https://github.com/kogeler/tooling/tree/main/traffic-masking" \
      org.opencontainers.image.version="${VERSION}"

COPY control_protocol.py masking_lib.py observer_metrics.py traffic_masking_server.py traffic_masking_client.py /app/

USER 1000:1000

EXPOSE 8888/udp
STOPSIGNAL SIGTERM

ENTRYPOINT ["python"]
CMD ["-c", "print('Usage (mount a mode 0600 PSK at /run/secrets/traffic-masking.psk):\\n  Server: python traffic_masking_server.py --min-mbps 2 --max-mbps 8 --psk-file /run/secrets/traffic-masking.psk\\n  Client: python traffic_masking_client.py --server <IP> --psk-file /run/secrets/traffic-masking.psk')"]

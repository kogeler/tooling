# Cloudflare DDNS Updater

A Python script that dynamically updates a Cloudflare DNS A record with your external IP. The script also exposes Prometheus metrics for monitoring DDNS updates and errors.

---

## Features

- **Dynamic DNS Update:** Automatically updates the specified Cloudflare DNS record when your external IP changes.
- **Prometheus Metrics:** Exposes key metrics:
  - Total number of IP updates.
  - Gauge indicating the current IP (value 1) and previously used IPs (value 0).
  - Counter tracking external IP retrieval errors with a label indicating the service domain.
- **Environment Variable Configuration:** All settings are controlled via environment variables.

---

## Prerequisites

- Python 3.6 or higher.
- Python packages:
  - `requests`
  - `prometheus_client`

Install dependencies via pip:

    pip install requests prometheus_client

---

## Usage

Make sure the script is executable:

    chmod +x tooling/cf-ddns/cf-ddns.py

Then run the script:

    ./tooling/cf-ddns/cf-ddns.py

---

## Environment Variables

The following environment variables must be set:

- **CF_DDNS_TOKEN**  
  Cloudflare API token with sufficient permissions to manage DNS records.

- **CF_DDNS_ZONE_ID**  
  Cloudflare zone ID where your DNS record is hosted.

- **CF_DDNS_HOST**  
  The fully qualified domain name (FQDN) to update (e.g., `subdomain.example.com`).

Optional variables:

- **CF_DDNS_INTERVAL**  
  Interval in seconds between IP checks. Default: `10`.

- **CF_DDNS_TTL**  
  Time To Live (TTL) for the DNS record. Default: `120`.

- **CF_DDNS_PROXIED**  
  Set to `"True"` (case-insensitive) if the record should be proxied by Cloudflare. Default: `"False"`.

- **CF_DDNS_LOGLEVEL**  
  Logging level. Acceptable values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Default: `INFO`.

- **CF_DDNS_METRICS_PORT**  
  Port where the Prometheus metrics endpoint is exposed. Default: `9101`.

---

## Prometheus Metrics

The following metrics are exposed under `/metrics`:

- **cf_ddns_ip_updates_total**  
  A counter that increments each time the IP address is updated.

- **cf_ddns_ip_info{domain, ip}**  
  A gauge indicating IP usage. The current IP is set to `1`, and previous IP values remain at `0`.

- **cf_ddns_ip_retrieval_errors_total{service_domain}**  
  A counter that increments when an error occurs while retrieving the external IP from a specific service (e.g., `checkip.amazonaws.com` or `api.ipify.org`).

Access the metrics at:

    http://<HOST>:<CF_DDNS_METRICS_PORT>/metrics

---

## License

This project is licensed under the [Apache License 2.0](../LICENSE).

---

## Notes

- Ensure that your Cloudflare API token, zone ID, and host are correctly provided.
- The script fetches the external IP via HTTPS from two services: `checkip.amazonaws.com` and `api.ipify.org`.
- Monitor Prometheus metrics to stay aware of DNS update activity and potential errors.

#!/usr/bin/env python3

import os
import time
import logging
import requests

def configure_logging():
    """
    Configures the logging level based on the CF_DDNS_LOGLEVEL environment variable.
    Defaults to INFO if not set or invalid.
    """
    log_level_str = os.environ.get("CF_DDNS_LOGLEVEL", "INFO").upper()
    valid_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    log_level = valid_levels.get(log_level_str, logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    logging.info(f"Log level set to {log_level_str}.")

def get_external_ip():
    """
    Tries to retrieve the external IP address from checkip.amazonaws.com.
    If that fails, it attempts to retrieve it from ipify (api.ipify.org).
    Returns the IP address as a string or None if both attempts fail.
    """
    # First attempt: checkip.amazonaws.com
    try:
        response = requests.get("http://checkip.amazonaws.com/", timeout=10)
        response.raise_for_status()
        return response.text.strip()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving external IP from checkip.amazonaws.com: {e}")
        # Second attempt: ipify.org
        try:
            response = requests.get("https://api.ipify.org?format=text", timeout=10)
            response.raise_for_status()
            return response.text.strip()
        except requests.exceptions.RequestException as e2:
            logging.error(f"Error retrieving external IP from ipify.org: {e2}")
            return None

def update_cloudflare_record(token, zone_id, record_id, host, new_ip, ttl, proxied):
    """
    Updates an A record in Cloudflare via the API.
    Returns True on success and False on failure.
    """
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = {
        "type": "A",
        "name": host,
        "content": new_ip,
        "ttl": ttl,        # Configurable TTL
        "proxied": proxied # Whether to enable Cloudflare proxy
    }

    try:
        response = requests.put(url, json=data, headers=headers, timeout=10)
        response.raise_for_status()

        result = response.json()
        if result.get("success"):
            logging.info(f"DNS record for host {host} successfully updated to IP {new_ip}.")
            return True
        else:
            logging.error(f"Cloudflare API returned an error: {result}")
            return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Error contacting the Cloudflare API: {e}")
        return False

def main():
    # Configure logging first
    configure_logging()

    # Read parameters from environment variables
    CF_DDNS_TOKEN = os.environ.get("CF_DDNS_TOKEN")
    CF_DDNS_ZONE_ID = os.environ.get("CF_DDNS_ZONE_ID")
    CF_DDNS_RECORD_ID = os.environ.get("CF_DDNS_RECORD_ID")
    CF_DDNS_HOST = os.environ.get("CF_DDNS_HOST")
    
    # Interval defaults to 120 if not specified
    CF_DDNS_INTERVAL = os.environ.get("CF_DDNS_INTERVAL", "10")
    
    # Cloudflare proxy setting defaults to "False"
    CF_DDNS_PROXIED_STR = os.environ.get("CF_DDNS_PROXIED", "False")
    
    # TTL defaults to 120 if not specified
    CF_DDNS_TTL = os.environ.get("CF_DDNS_TTL", "120")

    # Check if all required environment variables are set
    if not all([CF_DDNS_TOKEN, CF_DDNS_ZONE_ID, CF_DDNS_RECORD_ID, CF_DDNS_HOST]):
        logging.error(
            "Not all required environment variables are set: "
            "CF_DDNS_TOKEN, CF_DDNS_ZONE_ID, CF_DDNS_RECORD_ID, CF_DDNS_HOST"
        )
        return

    try:
        interval = int(CF_DDNS_INTERVAL)
    except ValueError:
        logging.error(f"Invalid value for CF_DDNS_INTERVAL: {CF_DDNS_INTERVAL}.")
        return
    
    # Convert TTL to int
    try:
        ttl = int(CF_DDNS_TTL)
    except ValueError:
        logging.error(f"Invalid value for CF_DDNS_TTL: {CF_DDNS_TTL}.")
        return
    
    # Convert proxied to boolean
    proxied = CF_DDNS_PROXIED_STR.lower() == "true"

    last_ip = None

    # Main infinite loop for checking
    while True:
        current_ip = get_external_ip()
        if current_ip is None:
            logging.error("Could not retrieve external IP from any service. Waiting until the next attempt...")
        else:
            # If this is the first check, update DNS record right away
            if last_ip is None:
                logging.info("First check. Updating DNS record...")
                success = update_cloudflare_record(
                    CF_DDNS_TOKEN, CF_DDNS_ZONE_ID, CF_DDNS_RECORD_ID,
                    CF_DDNS_HOST, current_ip, ttl, proxied
                )
                if success:
                    last_ip = current_ip
            else:
                # If the IP has changed, update
                if current_ip != last_ip:
                    logging.info(f"IP address changed from {last_ip} to {current_ip}. Updating DNS record...")
                    success = update_cloudflare_record(
                        CF_DDNS_TOKEN, CF_DDNS_ZONE_ID, CF_DDNS_RECORD_ID,
                        CF_DDNS_HOST, current_ip, ttl, proxied
                    )
                    if success:
                        last_ip = current_ip
                else:
                    logging.debug("External IP address has not changed. No update needed.")

        # Wait the specified interval before the next check
        logging.debug(f"Waiting {interval} seconds until the next check...")
        time.sleep(interval)

if __name__ == "__main__":
    main()

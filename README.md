# tooling

Collection of small, self-contained utilities for infrastructure automation,
monitoring and networking. Each project lives in its own
directory and includes its own configuration and usage documentation.

## Utilities

| Utility | Description |
|---|---|
| [cf-ddns](cf-ddns/) | Keeps a Cloudflare DNS A record synchronized with the host's public IPv4 address and exposes Prometheus metrics. |
| [one-t-exporter](one-t-exporter/) | Exports TurboFlakes ONE-T performance metrics for Polkadot and Kusama validators to Prometheus. |
| [sms-to-telegram](sms-to-telegram/docs/) | Reliably forwards SMS messages from a USB GSM modem to one or more Telegram chats. |
| [traffic-masking](traffic-masking/) | Generates configurable bidirectional UDP cover traffic to obscure traffic patterns inside encrypted tunnels. |

## Prebuilt Artifacts

Ready-to-use multi-architecture Docker images (`linux/amd64` and `linux/arm64`)
are published to GitHub Container Registry for `cf-ddns`, `one-t-exporter`,
`sms-to-telegram`, and `traffic-masking`:

```text
ghcr.io/kogeler/tooling/<utility>:latest
ghcr.io/kogeler/tooling/<utility>:<version>
```

Prebuilt Linux binaries for Go utilities are published as
[GitHub Release assets](https://github.com/kogeler/tooling/releases). The
current `sms-to-telegram` release includes `amd64` and `arm64` binaries with
SHA-256 checksum files.

## License

This project is licensed under the [Apache License 2.0](LICENSE).

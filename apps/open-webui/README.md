# Open WebUI

The private UI is at `https://open-webui.fruitiex.org`, with state on the
`open-webui-nfs` PVC. The upstream Helm release uses the encrypted
`open-webui-secrets` for its Hermes API key and browser-session signing key.

## Hermes host migration — 2026-10-02

Hermes now runs on Honeydew at `http://192.168.1.158:8642/v1`. The Helm value and
persisted `openai.api_base_urls` setting were updated; the existing API key and
other connection settings were retained. Honeydew's firewall permits port 8642
only from the Talos management network (`192.168.10.0/23`); the native Hermes
dashboard remains loopback-only. Authenticated model discovery from the WebUI
pod returned 200. A consistent private pre-change database backup is retained
under `/app/backend/data/backups/before-honeydew-20261002T120452Z.db`.

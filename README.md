# Ubiquiti Ops Console

A local Dockerized operations dashboard for a Ubiquiti Dream Machine environment.

This tool is separate from the threat monitor. The threat monitor focuses on security detections from UniFi logs. This console focuses on everyday network operations: device health, WAN reachability, DNS resolution, website checks, and recent status changes.

## Features

- Watch critical local devices such as the UDM, NAS, APs, servers, and printers
- Check ICMP reachability and optional TCP service ports
- Track WAN targets such as public DNS providers
- Test DNS lookups and external HTTP endpoints
- Pull Site Manager inventory, hosts, sites, and ISP metric data with the API key that works today
- Optionally pull connected clients and traffic counters from a local UniFi Network Integration API key
- Search and filter connected clients in a dedicated client inventory page
- Discover reachable local devices by scanning configured LAN subnets
- Run a manual WAN speed test from the dashboard and keep the latest result
- Watch control-plane reliability with classified UniFi API failures, monitor-cycle health, and next-step hints
- Persist check history in SQLite
- Provide a local dashboard and JSON API
- Run beside the existing threat monitor on port `8090`

## Quick start

```powershell
cd C:\Users\hollowayps\Documents\Codex\UbiquitiOpsConsole
Copy-Item .env.example .env
notepad .env
docker compose up -d --build
```

Open:

```text
http://localhost:8090
```

## Configuration

Edit `.env` locally. Do not commit `.env`.

### Watched devices

```env
WATCHED_DEVICES=192.168.1.1=UDM Gateway:critical:443,80;192.168.1.20=Storage NAS:critical:443,445
```

Format:

```text
ip=name[:sensitivity[:ports]]
```

Examples:

```env
192.168.1.1=UDM Gateway:critical:443,80
192.168.1.20=Storage NAS:critical:443,445
192.168.1.50=Main AP:high:443
192.168.1.75=Printer:normal:80
```

The console first tries ICMP ping. If ping fails and ports are configured, it tries TCP connections to those ports. A device is considered online if either ping or one configured service port responds.

### WAN checks

```env
WAN_TARGETS=1.1.1.1=Cloudflare DNS;8.8.8.8=Google DNS
DNS_LOOKUPS=ui.com;github.com
HTTP_CHECKS=https://ui.com=UniFi Website;https://github.com=GitHub
```

### LAN discovery

LAN discovery is optional and disabled by default. It helps find phones, laptops, IoT devices, printers, and other local clients even when the UniFi client API is unavailable.

```env
LAN_DISCOVERY_ENABLED=true
LAN_DISCOVERY_SUBNETS=192.168.1.0/24
LAN_DISCOVERY_PORTS=22,53,80,443,445,8080,8443
LAN_DISCOVERY_MAX_HOSTS=256
```

Notes:

- Keep the subnet scoped to networks you own and manage.
- Discovery uses ping plus quick TCP probes against the configured ports.
- MAC addresses are shown when the container can read them from the local neighbor table.
- Increase `LAN_DISCOVERY_MAX_HOSTS` only if you intentionally want to scan larger ranges.

### WAN speed test

The dashboard includes a manual speed test on the main page. It is intentionally not part of the normal health-check interval so the console does not consume bandwidth unless you click `Run speed test`.

```env
SPEED_TEST_ENABLED=true
SPEED_TEST_DOWNLOAD_URL=https://speed.cloudflare.com/__down?bytes=10000000
SPEED_TEST_UPLOAD_ENABLED=false
SPEED_TEST_UPLOAD_URL=https://speed.cloudflare.com/__up
SPEED_TEST_DOWNLOAD_BYTES=10000000
SPEED_TEST_UPLOAD_BYTES=1000000
SPEED_TEST_TIMEOUT_SECONDS=20
SPEED_TEST_MIN_DOWNLOAD_MBPS=100
SPEED_TEST_MIN_UPLOAD_MBPS=10
```

Notes:

- Download testing is enabled by default and uses a public Cloudflare speed-test endpoint.
- Upload testing is disabled by default. Set `SPEED_TEST_UPLOAD_ENABLED=true` if you want upload measurements too.
- The result is stored in SQLite and shown on the summary page until the next manual run.
- Adjust the minimum Mbps values to match your internet plan so the result card can flag slow runs.

### UniFi API collectors

The UniFi API collector is optional and disabled by default. There are two API paths:

- Site Manager API: account-wide hosts, sites, devices, and ISP metric records from `https://api.ui.com`
- Local Network API: connected clients, local adopted devices, and traffic counters from the UDM Network application

Add these settings to your local `.env`:

```env
UNIFI_API_ENABLED=true

# Optional local Network API. This requires a Network Integration API key.
UNIFI_API_BASE_URL=https://192.168.1.1/proxy/network/integration
UNIFI_API_KEY=
UNIFI_SITE_ID=replace-with-your-site-id
UNIFI_VERIFY_TLS=false
UNIFI_TIMEOUT_SECONDS=10
UNIFI_LEGACY_STATS_ENABLED=true

# Site Manager API. This works with keys from https://unifi.ui.com/settings/api-keys.
UNIFI_SITE_MANAGER_ENABLED=true
UNIFI_SITE_MANAGER_BASE_URL=https://api.ui.com
UNIFI_SITE_MANAGER_API_KEY=

# Optional guarded operation requests.
# Leave disabled unless you are intentionally testing write-action workflows.
UNIFI_WRITE_ACTIONS_ENABLED=false
UNIFI_WRITE_ACTIONS_CONFIRMATION=APPLY

# Optional trusted/watched client labels.
# Format: mac=name[:category];mac=name[:category]
TRUSTED_CLIENTS=aa:bb:cc:dd:ee:ff=Work Laptop:trusted;11:22:33:44:55:66=Storage NAS:critical
```

Notes:

- Keep API keys only in `.env`; never commit them.
- A Site Manager key is not the same as a local Network Integration API key. If the local Network API returns `401 Unauthorized`, keep Site Manager enabled and leave `UNIFI_API_KEY` blank until you find/create the local Network key.
- Site Manager currently populates sites, hosts, infrastructure devices, and ISP metric records.
- The local Network API populates connected clients, local device statistics, and traffic counters when available.
- Set `UNIFI_SITE_ID` to the local Network API site ID returned by `GET /proxy/network/integration/v1/sites`. The local site ID may differ from the Site Manager site ID.
- Set `UNIFI_VERIFY_TLS=false` for the default self-signed UDM certificate.
- `UNIFI_LEGACY_STATS_ENABLED=true` attempts older local stats endpoints for richer traffic counters. If your console rejects those endpoints, the dashboard will still use the official API data it can collect.
- `UNIFI_SITE_MANAGER_ENABLED=true` calls Site Manager endpoints including `GET /v1/sites`, `GET /v1/hosts`, `GET /v1/devices`, and `GET /v1/isp-metrics`.
- `UNIFI_WRITE_ACTIONS_ENABLED=false` keeps controller-changing operations blocked by default. The Device Ops page can record local reboot, firmware, PoE, port, client, VLAN, firewall, Wi-Fi, and ACL operation requests for audit/planning.
- If you intentionally enable write-action workflows for testing, requests must include the `UNIFI_WRITE_ACTIONS_CONFIRMATION` token. This project records the request locally; controller-side write execution should only be wired with explicit maintenance-window controls.
- `TRUSTED_CLIENTS` lets the Clients page label known devices and filter unknown/untrusted clients. Keep real MAC addresses only in local `.env`, especially for a public repo.
- The Control Plane page classifies common failures like `401 Unauthorized`, `403 Forbidden`, `404 Not Found`, timeouts, and interrupted connections. This is intended to help explain when the UniFi Network app is starting, restarting, missing API permissions, or using the wrong API path/key.

UniFi documents local Network API access under UniFi Network > Integrations. The official local Network API includes endpoints for sites, adopted devices, connected clients, and latest device statistics when the local Network API key is available.

Useful manual API checks:

```powershell
$apiKey = "replace-with-your-site-manager-api-key"

Invoke-RestMethod `
  -Uri "https://api.ui.com/v1/sites?pageSize=10" `
  -Headers @{ "X-API-Key" = $apiKey; "Accept" = "application/json" }

Invoke-RestMethod `
  -Uri "https://api.ui.com/v1/devices?pageSize=200" `
  -Headers @{ "X-API-Key" = $apiKey; "Accept" = "application/json" }
```

Local Network API check, if you later find a Network Integration API key:

```powershell
$apiKey = "replace-with-local-network-api-key"
$baseUrl = "https://192.168.1.1/proxy/network/integration"
$siteId = "replace-with-site-id"

Invoke-RestMethod `
  -Uri "$baseUrl/v1/sites/$siteId/clients?offset=0&limit=200" `
  -Headers @{ "X-API-Key" = $apiKey; "Accept" = "application/json" }

Invoke-RestMethod `
  -Uri "$baseUrl/v1/sites/$siteId/devices?offset=0&limit=200" `
  -Headers @{ "X-API-Key" = $apiKey; "Accept" = "application/json" }

```

## API

```text
GET /api/summary
GET /api/history?target=192.168.1.1&limit=50
GET /api/unifi
GET /api/unifi/actions
POST /api/unifi/action
GET /api/timeline
GET /api/control-plane
GET /api/discovery
GET /api/speed-test
GET /api/speed-test/run
GET /health
```

## Local development

```powershell
python -m unittest discover -s tests -v
python -m ubiquiti_ops
```

## Suggested next enhancements

- Email/webhook notifications when critical devices go offline
- Daily summary of new, missing, and unstable devices
- Watched or trusted client labels
- Maintenance window support
- Exportable CSV reports

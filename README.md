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
UNIFI_SITE_MANAGER_API_KEY=replace-with-your-site-manager-api-key
```

Notes:

- Keep API keys only in `.env`; never commit them.
- A Site Manager key is not the same as a local Network Integration API key. If the local Network API returns `401 Unauthorized`, keep Site Manager enabled and leave `UNIFI_API_KEY` blank until you find/create the local Network key.
- Site Manager currently populates sites, hosts, infrastructure devices, and ISP metric records.
- The local Network API populates connected clients, local device statistics, and traffic counters when available.
- Set `UNIFI_SITE_ID` to the Site Manager site ID for your UDM. You can discover it from `GET https://api.ui.com/v1/sites`.
- Set `UNIFI_VERIFY_TLS=false` for the default self-signed UDM certificate.
- `UNIFI_LEGACY_STATS_ENABLED=true` attempts older local stats endpoints for richer traffic counters. If your console rejects those endpoints, the dashboard will still use the official API data it can collect.
- `UNIFI_SITE_MANAGER_ENABLED=true` calls Site Manager endpoints including `GET /v1/sites`, `GET /v1/hosts`, `GET /v1/devices`, and `GET /v1/isp-metrics`.

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
GET /health
```

## Local development

```powershell
python -m unittest discover -s tests -v
python -m ubiquiti_ops
```

## Suggested next enhancements

- Email/webhook notifications when critical devices go offline
- UniFi local API collector for client/AP metadata
- Daily summary of new, missing, and unstable devices
- Maintenance window support
- Exportable CSV reports

# Ubiquiti Ops Console

A local Dockerized operations dashboard for a Ubiquiti Dream Machine environment.

This tool is separate from the threat monitor. The threat monitor focuses on security detections from UniFi logs. This console focuses on everyday network operations: device health, WAN reachability, DNS resolution, website checks, and recent status changes.

## Features

- Watch critical local devices such as the UDM, NAS, APs, servers, and printers
- Check ICMP reachability and optional TCP service ports
- Track WAN targets such as public DNS providers
- Test DNS lookups and external HTTP endpoints
- Pull UniFi devices, connected clients, and traffic insights from the UniFi Network API
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

### UniFi Network API collector

The UniFi API collector is optional and disabled by default. It can pull:

- adopted UniFi devices
- connected clients / client activity
- latest device statistics
- traffic insight summaries such as top clients and top device rates

Add these settings to your local `.env`:

```env
UNIFI_API_ENABLED=true
UNIFI_API_BASE_URL=https://192.168.1.1/proxy/network/integration
UNIFI_API_KEY=replace-with-your-read-only-api-key
UNIFI_SITE_ID=
UNIFI_VERIFY_TLS=false
UNIFI_TIMEOUT_SECONDS=10
UNIFI_LEGACY_STATS_ENABLED=true
```

Notes:

- Keep `UNIFI_API_KEY` only in `.env`; never commit it.
- Leave `UNIFI_SITE_ID` blank to use the first site returned by the API.
- Set `UNIFI_VERIFY_TLS=false` for the default self-signed UDM certificate.
- `UNIFI_LEGACY_STATS_ENABLED=true` attempts older local stats endpoints for richer traffic counters. If your console rejects those endpoints, the dashboard will still use the official API data it can collect.

UniFi documents local Network API access under UniFi Network > Integrations. The official API includes local endpoints for sites, adopted devices, connected clients, and latest device statistics.

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

# Ubiquiti Ops Console

A local Dockerized operations dashboard for a Ubiquiti Dream Machine environment.

This tool is separate from the threat monitor. The threat monitor focuses on security detections from UniFi logs. This console focuses on everyday network operations: device health, WAN reachability, DNS resolution, website checks, and recent status changes.

## Features

- Watch critical local devices such as the UDM, NAS, APs, servers, and printers
- Check ICMP reachability and optional TCP service ports
- Track WAN targets such as public DNS providers
- Test DNS lookups and external HTTP endpoints
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

## API

```text
GET /api/summary
GET /api/history?target=192.168.1.1&limit=50
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


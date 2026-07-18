from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def monitor_cycle_event(status: str, message: str, details: dict | None = None) -> dict:
    healthy = status == "healthy"
    return {
        "timestamp": now_iso(),
        "source": "monitor",
        "kind": "cycle_completed" if healthy else "cycle_failed",
        "status": status,
        "severity": "success" if healthy else "critical",
        "title": "Monitor cycle completed" if healthy else "Monitor cycle failed",
        "message": message,
        "details": details or {},
    }


def snapshot_events(snapshot: dict) -> list[dict]:
    checked_at = snapshot.get("checked_at") or now_iso()
    network_error = str(snapshot.get("network_error") or "")
    site_manager_error = str(snapshot.get("site_manager_error") or "")
    has_local_data = bool(snapshot.get("devices") or snapshot.get("clients") or snapshot.get("sites"))
    has_site_manager_data = bool(
        snapshot.get("site_manager_sites")
        or snapshot.get("site_manager_hosts")
        or snapshot.get("site_manager_devices")
        or snapshot.get("site_manager_isp_metrics")
    )

    events = [
        classify_source(
            source="unifi_collector",
            timestamp=checked_at,
            error=str(snapshot.get("error") or ""),
            has_data=has_local_data or has_site_manager_data,
            ok=bool(snapshot.get("ok")),
            success_title="UniFi collector completed",
            failure_title="UniFi collector is failing",
        ),
    ]

    if network_error or has_local_data:
        events.append(classify_source(
            source="local_network_api",
            timestamp=checked_at,
            error=network_error,
            has_data=has_local_data,
            ok=not network_error,
            success_title="Local Network API responded",
            failure_title="Local Network API is not healthy",
            details={
                "site_id": snapshot.get("site_id", ""),
                "device_count": len(snapshot.get("devices", []) or []),
                "client_count": len(snapshot.get("clients", []) or []),
            },
        ))

    if site_manager_error or has_site_manager_data:
        events.append(classify_source(
            source="site_manager_api",
            timestamp=checked_at,
            error=site_manager_error,
            has_data=has_site_manager_data,
            ok=not site_manager_error,
            success_title="Site Manager API responded",
            failure_title="Site Manager API is not healthy",
            details={
                "site_count": len(snapshot.get("site_manager_sites", []) or []),
                "host_count": len(snapshot.get("site_manager_hosts", []) or []),
                "device_count": len(snapshot.get("site_manager_devices", []) or []),
                "errors": snapshot.get("site_manager_errors", {}) or {},
            },
        ))

    return events


def classify_source(
    *,
    source: str,
    timestamp: str,
    error: str,
    has_data: bool,
    ok: bool,
    success_title: str,
    failure_title: str,
    details: dict | None = None,
) -> dict:
    classification = classify_error(error)
    status = "healthy"
    severity = "success"
    title = success_title
    message = "Collection source responded successfully."
    kind = "source_healthy"

    if error:
        status = "degraded" if has_data else "failing"
        severity = "warning" if has_data else "critical"
        title = failure_title
        message = classification["hint"]
        kind = classification["kind"]
    elif not ok:
        status = "failing"
        severity = "critical"
        title = failure_title
        message = "The source did not report a specific error, but it did not complete successfully."
        kind = "unknown_failure"

    payload = {
        "timestamp": timestamp,
        "source": source,
        "kind": kind,
        "status": status,
        "severity": severity,
        "title": title,
        "message": message,
        "details": {
            "error": error,
            "has_data": has_data,
            "classification": classification["kind"],
            **(details or {}),
        },
    }
    return payload


def classify_error(error: str) -> dict:
    text = str(error or "").strip()
    lower = text.lower()
    if not text:
        return {
            "kind": "source_healthy",
            "hint": "Collection source responded successfully.",
        }
    if "401" in lower or "unauthorized" in lower:
        return {
            "kind": "api_unauthorized",
            "hint": "Authentication failed. Check that the API key is current, the key type matches the endpoint, and the value in .env has no quotes or extra spaces.",
        }
    if "403" in lower or "forbidden" in lower:
        return {
            "kind": "api_forbidden",
            "hint": "The API key was accepted but lacks access. Check API scopes, selected UniFi applications, and site permissions.",
        }
    if "404" in lower or "not found" in lower:
        return {
            "kind": "endpoint_not_found",
            "hint": "The endpoint was not found. This often means the Network app is still starting, the API path/version is unsupported, or the configured site ID/base URL is wrong.",
        }
    if "timed out" in lower or "timeout" in lower:
        return {
            "kind": "api_timeout",
            "hint": "The request timed out. The UDM, Network app, or Docker network path may be slow or restarting.",
        }
    if "connection" in lower and ("closed" in lower or "reset" in lower or "refused" in lower):
        return {
            "kind": "connection_interrupted",
            "hint": "The connection was interrupted. This is consistent with the UniFi Network app restarting, not listening yet, or briefly dropping TLS/API sessions.",
        }
    if "no unifi api key" in lower or "not configured" in lower:
        return {
            "kind": "collector_not_configured",
            "hint": "The collector is enabled but a required API key or configuration value is missing.",
        }
    return {
        "kind": "unknown_failure",
        "hint": f"Unhandled collector failure: {text}",
    }


def recommendations(latest_by_source: dict[str, dict]) -> list[dict]:
    output: list[dict] = []
    for source, event in latest_by_source.items():
        status = event.get("status")
        kind = event.get("kind")
        if status == "healthy":
            continue
        if kind == "api_unauthorized":
            output.append({
                "source": source,
                "priority": "high",
                "title": "Validate API key type and formatting",
                "detail": "Use the local Network Integration key for local Network API calls and the Site Manager key for api.ui.com. Remove quotes around the key in .env.",
            })
        elif kind == "api_forbidden":
            output.append({
                "source": source,
                "priority": "high",
                "title": "Review API scopes and selected site",
                "detail": "Confirm the key includes Network/Site Manager scope and the UDM site is selected.",
            })
        elif kind == "endpoint_not_found":
            output.append({
                "source": source,
                "priority": "medium",
                "title": "Check Network app state and endpoint path",
                "detail": "If the Network app shows Starting, wait for it to finish or restart it from UniFi OS. Verify UNIFI_API_BASE_URL and UNIFI_SITE_ID.",
            })
        elif kind in {"connection_interrupted", "api_timeout"}:
            output.append({
                "source": source,
                "priority": "medium",
                "title": "Watch for Network app restarts",
                "detail": "Repeated connection interruptions suggest the control plane or Network application is restarting or not staying ready.",
            })
        else:
            output.append({
                "source": source,
                "priority": "medium",
                "title": "Inspect latest collector error",
                "detail": str(event.get("message") or "Review the event timeline for the latest failure details."),
            })
    return output

"""Calendar (.ics) events for auction lots, so buyers get a reminder from their own phone calendar."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# Auction times are published in South African time (UTC+2, no daylight saving).
SAST = timezone(timedelta(hours=2), "SAST")


def _utc(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SAST)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line with a leading space (RFC 5545)."""
    out, b = [], line.encode()
    while len(b) > 75:
        cut = 75
        while cut and (b[cut] & 0xC0) == 0x80:   # don't split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode())
        b = b" " + b[cut:]
    out.append(b.decode())
    return "\r\n".join(out)


def lot_event(item: dict, base_url: str = "", now: Optional[datetime] = None) -> Optional[str]:
    live = item.get("auction_type") == "live" and item.get("auction_start")
    start = item.get("auction_start") if live else item.get("auction_end")
    if not start:
        return None
    car = item.get("title") or item["source_lot_id"]
    house = item.get("source_name") or item["source"]
    lot = f" (lot {item['lot_number']})" if live and item.get("lot_number") else ""
    summary = f"{'Live sale' if live else 'Closes'}: {car}{lot}"
    dt_start = datetime.fromisoformat(start)
    dt_end = dt_start + (timedelta(hours=4) if live else timedelta(minutes=15))
    where = ", ".join(x for x in (house, item.get("branch"), item.get("province")) if x)
    desc = [f"{car} {item.get('variant') or ''}".strip(), f"Lot {item['source_lot_id']} at {house}."]
    if item.get("est_all_in_cost"):
        desc.append(f"All-in estimate R {int(item['est_all_in_cost']):,}.".replace(",", " "))
    if base_url:
        desc.append(f"{base_url.rstrip('/')}/?lot={item['source']}/{item['source_lot_id']}")
    elif item.get("url") and not str(item["url"]).startswith("demo://"):
        desc.append(item["url"])
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Okshun//Auction reminders//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{item['source']}-{item['source_lot_id']}@okshun",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{_utc(start)}",
        f"DTEND:{_utc(dt_end.isoformat())}",
        f"SUMMARY:{_esc(summary)}",
        f"LOCATION:{_esc(where)}",
        f"DESCRIPTION:{_esc(chr(10).join(desc))}",
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(summary)}", "TRIGGER:-PT30M", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"

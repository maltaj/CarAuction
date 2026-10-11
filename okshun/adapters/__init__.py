"""
Source registry: which adapters the public app runs, and what to show about each house.

Add a house here only once it has agreed in writing to share its listings.
"""

from __future__ import annotations

from okshun.adapters.demo import HOUSES, DemoAdapter

# Display info per Listing.source. `demo` marks fictional houses in the UI.
SOURCES: dict[str, dict] = {
    hid: {
        "id": hid, "name": name, "branch": branch, "demo": True,
        "commission_pct": comm, "fixed_fees": fees, "website": None,
    }
    for hid, name, _prov, branch, _profile, _atype, comm, fees, _n in HOUSES
}

# Adapter factories by name, as used by `python -m okshun.ingest --sources ...`
ADAPTERS = {
    "demo": DemoAdapter,
}

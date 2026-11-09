"""Which plans the web app shows (config/scope.yaml). Storage and crawling are never scoped."""

from __future__ import annotations

import functools

import yaml

from radar.settings import get_settings


@functools.lru_cache(maxsize=1)
def config() -> dict:
    path = get_settings().config_dir / "scope.yaml"
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    name = (data or {}).get("region", "florida")
    region = ((data or {}).get("regions") or {}).get(name) or {"label": "Florida", "description": "every plan"}
    counties = region.get("counties") or {}
    return {
        "name": name,
        "label": region.get("label", name),
        "description": region.get("description", ""),
        "counties": counties,
        "city_county": {c: county for county, cities in counties.items() for c in cities},
        "statewide": set(region.get("statewide") or []),
        "all": not counties,
    }


def contains(allocator: dict) -> bool:
    c = config()
    if c["all"]:
        return True
    return allocator.get("allocator_id") in c["statewide"] or allocator.get("jurisdiction_city") in c["city_county"]


def county(allocator: dict) -> str | None:
    c = config()
    if allocator.get("allocator_id") in c["statewide"]:
        return "Statewide"
    return c["city_county"].get(allocator.get("jurisdiction_city"))


def ids(con) -> set[str] | None:
    """allocator_ids in scope, or None when everything is in scope."""
    if config()["all"]:
        return None
    rows = con.execute("SELECT allocator_id, jurisdiction_city FROM allocators").fetchall()
    return {i for i, city in rows if contains({"allocator_id": i, "jurisdiction_city": city})}

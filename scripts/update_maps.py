"""Build eftjourney/data/maps.json from tarkov.dev's open map data.

Sources (fetched unless local files are given):
  maps.json  https://raw.githubusercontent.com/the-hideout/tarkov-dev/main/src/data/maps.json  (MIT)
             transforms, bounds, floors, labels and the SVG/tile URLs of each interactive map
  JSON API   https://json.tarkov.dev/regular/maps  (+ maps_en, maps_ko translations)
             the game's location ids (nameId), extracts, transits, spawns
  ALIASES    below: location ids the game plays on a map that tarkov.dev does not link to it

The SVG map art (assets.tarkov.dev; the-hideout/tarkov-dev-svg-maps by Shebuka and
contributors, CC BY-NC-SA 4.0) and the raster tiles are not bundled: the viewer loads
them at runtime and credits them.

usage: py scripts/update_maps.py [--maps-json FILE] [--api-json FILE] [--en-json FILE] [--ko-json FILE] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "eftjourney" / "data" / "maps.json"
SOURCES = {
    "maps": "https://raw.githubusercontent.com/the-hideout/tarkov-dev/main/src/data/maps.json",
    "api": "https://json.tarkov.dev/regular/maps",
    "en": "https://json.tarkov.dev/regular/maps_en",
    "ko": "https://json.tarkov.dev/regular/maps_ko",
}
FACTIONS = ("pmc", "scav", "shared")
KIND_KO = {"extracts": "탈출구", "transits": "트랜짓", "spawns": "스폰"}
BOUNDS_PAD = 75.0  # same slack the builder allows around a map's bounds
INHERIT_WARN = 1.0  # metres; a variant extract farther than this from the one it inherits from is reported
# Location ids the game plays on a map's geometry that tarkov.dev's maps.json does not link to
# that map (map key -> id -> variant names). An id the API lists becomes a variant with the API's
# names and lists, like Ground Zero 21+; one it does not list gets these names and no lists, so
# the viewer shows the map's own markers. "event": the event maps of client 1.3.0 (2026-10-30),
# whose raids log these ids in UserConfirmed, TRACE and the scene preset alike (the API added
# them on 2026-10-29 as infected-factory / infected-lab, which maps.json does not show).
ALIASES = {
    "factory": {"factory_event": {"name": "Infected Factory", "nameKo": "오염된 공장", "event": True}},
    "the-lab": {"laboratory_event": {"name": "Infected Lab", "nameKo": "오염된 연구소", "event": True}},
    # the tutorial (scene Sandbox_SL): the API's spawns sit at Ground Zero's ground heights
    "ground-zero": {"sandbox_start": {"name": "Ground Zero Tutorial", "nameKo": "Ground Zero 튜토리얼"}},  # tarkov.dev's ko name
}
LAYER_KO = {
    "2nd Floor": "2층",
    "3rd Floor": "3층",
    "4th Floor": "4층",
    "5th Floor": "5층",
    "Second Level": "2층",
    "Underground": "지하",
    "Bunkers": "벙커",
    "Tunnels": "터널",
    "Garage": "주차장",
    "Technical": "기술층",
    "Infirmary": "의무실",
    "Helipad": "헬기장",
    "Gym/Canteen": "체육관/식당",
    "Accommodation (lower)": "선실 (아래)",
    "Accommodation (mid)": "선실 (중간)",
    "Accommodation (upper)": "선실 (위)",
    "Officers' Deck": "사관 갑판",
    "Stairs (blocked)": "계단 (막힘)",
    "Bridge": "함교",
    "Bridge Roof": "함교 지붕",
    "Control Room": "통제실",
    "Engine Room": "기관실",
    "Engine Room (upper)": "기관실 (위)",
    "Fuel Pumps": "연료 펌프",
    "Fuel Pumps (lower)": "연료 펌프 (아래)",
    "Storage/Security": "창고/보안실",
}


def load(source: str):
    if source.startswith("https://"):
        req = urllib.request.Request(source, headers={"User-Agent": "eftjourney-update-maps"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return json.loads(Path(source).read_text(encoding="utf-8"))


def point(p: dict) -> dict:
    return {"x": round(float(p["x"]), 2), "y": round(float(p["y"]), 2), "z": round(float(p["z"]), 2)}


def localized(key: str | None, en: dict, ko: dict) -> dict:
    name = en.get(key) or key or ""
    out = {"name": name}
    if ko.get(key) and ko[key] != name:
        out["nameKo"] = ko[key]
    return out


def label(raw: dict) -> dict:
    x, z = raw["position"][:2]
    out = {"x": x, "z": z, "text": raw.get("text", "")}
    for k in ("rotation", "size", "top", "bottom"):
        if k in raw:
            out[k] = raw[k]
    return out


def layer(raw: dict) -> dict:
    out = {"name": raw["name"], "show": bool(raw.get("show")), "extents": raw.get("extents") or []}
    if LAYER_KO.get(raw["name"]):
        out["nameKo"] = LAYER_KO[raw["name"]]
    if raw.get("svgLayer"):
        out["svgLayer"] = raw["svgLayer"]
    if raw.get("tilePath"):
        out["tileUrl"] = raw["tilePath"]
    return out


def markers(loc: dict, en: dict, ko: dict, parent: dict | None = None,
            where: str = "", warnings: list[str] | None = None) -> dict:
    """Extracts, transits and PMC player spawns of one API location.

    Variant locations (night Factory) carry no factions; each extract inherits the
    faction of a parent extract with the same name (Factory has a PMC and a scav
    "Gate 3", both under one id). Pairs are matched closest first and each parent
    extract passes its faction on once, so the list order does not matter.
    """
    extracts = [ex for ex in loc.get("extracts") or [] if ex.get("position")]
    parents = [ex for ex in (parent or {}).get("extracts") or [] if ex.get("position") and ex.get("faction") in FACTIONS]
    factions = {i: ex["faction"] for i, ex in enumerate(extracts) if ex.get("faction") in FACTIONS}
    pairs = sorted(
        (math.hypot(p["position"]["x"] - ex["position"]["x"], p["position"]["z"] - ex["position"]["z"]), i, j)
        for i, ex in enumerate(extracts) if i not in factions
        for j, p in enumerate(parents) if p.get("name") == ex.get("name")
    )
    used = set()
    for dist, i, j in pairs:
        if i in factions or j in used:
            continue
        factions[i] = parents[j]["faction"]
        used.add(j)
        if dist > INHERIT_WARN and warnings is not None:
            item = {**localized(extracts[i].get("name"), en, ko), **point(extracts[i]["position"])}
            warnings.append(f"{where}: 탈출구 '{item['name']}' ({item['x']}, {item['z']}) 의 진영({factions[i]})을 "
                            f"{dist:.1f} m 떨어진 같은 이름 탈출구에서 물려받았습니다 (맞는지 확인 필요)")

    return {
        "extracts": [
            {**localized(ex.get("name"), en, ko), "faction": factions.get(i, "shared"), **point(ex["position"])}
            for i, ex in enumerate(extracts)
        ],
        "transits": [
            {**localized(tr.get("description"), en, ko), **point(tr["position"])}
            for tr in loc.get("transits") or [] if tr.get("position")
        ],
        "spawns": [
            point(sp["position"])
            for sp in loc.get("spawns") or []
            if sp.get("position")
            and {"pmc", "all"} & set(sp.get("sides") or [])
            and "player" in (sp.get("categories") or [])
        ],
    }


def outside_by(bounds: list, p: dict) -> float:
    """How far (metres) game point p lies outside bounds [[x1, z1], [x2, z2]]; 0 when inside."""
    (x1, z1), (x2, z2) = bounds
    dx = max(min(x1, x2) - p["x"], 0.0, p["x"] - max(x1, x2))
    dz = max(min(z1, z2) - p["z"], 0.0, p["z"] - max(z1, z2))
    return max(dx, dz)


def drop_strays(where: str, bounds: list, lists: dict, warnings: list[str]) -> dict:
    """Drop markers far outside the map (the API lists two Woods extracts under Ground Zero)."""
    for kind, items in lists.items():
        kept = []
        for item in items:
            off = outside_by(bounds, item)
            if off > BOUNDS_PAD:
                name = f" '{item['name']}'" if item.get("name") else ""
                warnings.append(f"{where}: {KIND_KO[kind]}{name} ({item['x']}, {item['z']}) 의 위치가 "
                                f"맵 범위에서 {off:.0f} m 벗어나 있어 뺐습니다")
                continue
            kept.append(item)
        lists[kind] = kept
    return lists


def misaligned(d: dict, lists: dict) -> bool:
    """Do most of a tile-only map's own markers project off its zoom-0 tile?

    tarkov.dev's Icebreaker transform does not match its tile art: its own extract
    and spawns land outside the image, so positions drawn there are only approximate.
    """
    if d.get("svgPath") or not d.get("tilePath"):
        return False
    t0, t1, t2, t3 = d["transform"]
    rot = math.radians(d.get("coordinateRotation", 0))
    size = d.get("tileSize") or 256
    margin = 0.05 * size
    points = lists["extracts"] + lists["transits"] + lists["spawns"]
    off = 0
    for p in points:
        # the viewer's CRS: LatLng(z, x), rotated, then Transformation(t0, t1, -t2, t3) at zoom 0
        rx = p["x"] * math.cos(rot) - p["z"] * math.sin(rot)
        rz = p["x"] * math.sin(rot) + p["z"] * math.cos(rot)
        px, py = t0 * rx + t1, -t2 * rz + t3
        if not (-margin <= px <= size + margin and -margin <= py <= size + margin):
            off += 1
    return bool(points) and off * 2 > len(points)


def shown_on(d: dict, api_maps: list) -> list[dict]:
    """The API locations tarkov.dev shows on interactive map d (normalizedName is its key or in altMaps)."""
    names = {d["key"], *(d.get("altMaps") or [])}
    return [m for m in api_maps if m.get("normalizedName") in names]


def build(maps_json: list, api_json: dict, en_json: dict, ko_json: dict) -> tuple[list[dict], list[str]]:
    api_maps = list(api_json["data"]["maps"].values())
    en, ko = en_json.get("data", {}), ko_json.get("data", {})
    entries, warnings = [], []
    linked_ids = set()
    # location id -> the map tarkov.dev itself shows it on (an alias must not add it to another map)
    shown = {m["nameId"].lower(): d["key"] for group in maps_json for d in group.get("maps", [])
             if d.get("projection") == "interactive" for m in shown_on(d, api_maps)}
    for group in maps_json:
        for d in group.get("maps", []):
            if d.get("projection") != "interactive":
                continue
            key = d["key"]
            linked = shown_on(d, api_maps)
            if not linked:
                warnings.append(f"{key}: API 데이터에 이 맵의 게임 위치 id가 없어 건너뛰었습니다")
                continue
            primary = next((m for m in linked if m["normalizedName"] == key), linked[0])
            ids = [primary["nameId"].lower()]
            ids += sorted({m["nameId"].lower() for m in linked} - set(ids))
            aliases = {}
            for alias_id, alias in ALIASES.get(key, {}).items():
                if shown.get(alias_id, key) != key:
                    warnings.append(f"{key}/{alias_id}: tarkov.dev가 이 위치를 {shown[alias_id]} 지도에 "
                                    f"연결하므로 별칭을 쓰지 않습니다")
                    continue
                aliases[alias_id] = alias
            # alias ids come after tarkov.dev's own, so ids[0] stays the canonical id
            ids += [i for i in aliases if i not in ids]
            linked += [m for m in api_maps if m["nameId"].lower() in aliases and m["nameId"].lower() not in shown]
            linked_ids.update(ids)
            own = drop_strays(key, d["bounds"], markers(primary, en, ko), warnings)
            # alternate locations sharing this map (night Factory, Labs dark, Ground Zero 21+);
            # they keep their own extract/transit/spawn lists when the API has different ones
            variants = {}
            for m in linked:
                if m is primary:
                    continue
                variant = localized(m.get("name"), en, ko)
                if aliases.get(m["nameId"].lower(), {}).get("event"):
                    variant["event"] = True
                where = f"{key}/{m['nameId']}"
                lists = drop_strays(where, d["bounds"], markers(m, en, ko, primary, where, warnings), warnings)
                for kind, items in lists.items():
                    if items and items != own[kind]:
                        variant[kind] = items
                variants[m["nameId"].lower()] = variant
            for alias_id, alias in aliases.items():
                variants.setdefault(alias_id, dict(alias))  # not in the API: the map's own lists
            entry = {
                "key": key,
                "ids": ids,
                **localized(primary.get("name"), en, ko),
                "variants": variants,
                "svgUrl": d.get("svgPath"),
                "svgLayer": d.get("svgLayer"),
                "svgBounds": d.get("svgBounds"),
                "tileUrl": d.get("tilePath"),
                "tileSize": d.get("tileSize") or 256,
                "transform": d["transform"],
                "coordinateRotation": d.get("coordinateRotation", 0),
                "bounds": d["bounds"],
                "heightRange": d.get("heightRange"),
                "minZoom": d.get("minZoom"),
                "maxZoom": d.get("maxZoom"),
                "author": d.get("author"),
                "authorLink": d.get("authorLink"),
                "layers": [layer(raw) for raw in d.get("layers") or []],
                "labels": [label(raw) for raw in d.get("labels") or [] if raw.get("position")],
                **own,
            }
            if misaligned(d, own):
                entry["unreliable"] = True
                warnings.append(f"{key}: 이 맵의 탈출구·트랜짓·스폰 대부분이 지도 그림 밖에 찍혀 위치를 참고용으로 표시합니다 "
                                f"(좌표 보정값이 그림과 맞지 않음)")
            if not entry["svgUrl"] and not entry["tileUrl"]:
                warnings.append(f"{key}: 지도 그림(SVG, 타일)이 없습니다")
            entries.append(entry)
    for m in api_maps:
        if m["nameId"].lower() not in linked_ids:
            warnings.append(f"위치 {m['nameId']} ({m.get('normalizedName')}): 이 위치를 보여 주는 tarkov.dev 지도가 없어 "
                            f"넣지 않았습니다 (다른 맵과 같은 지형이면 ALIASES에 추가)")
    entries.sort(key=lambda e: e["name"])
    return entries, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tarkov.dev 맵 데이터로 eftjourney/data/maps.json을 만듭니다.")
    parser.add_argument("--maps-json", default=SOURCES["maps"], help="tarkov.dev maps.json 파일 또는 URL (기본: %(default)s)")
    parser.add_argument("--api-json", default=SOURCES["api"], help="맵 API 파일 또는 URL (기본: %(default)s)")
    parser.add_argument("--en-json", default=SOURCES["en"], help="영어 이름 파일 또는 URL (기본: %(default)s)")
    parser.add_argument("--ko-json", default=SOURCES["ko"], help="한국어 이름 파일 또는 URL (기본: %(default)s)")
    parser.add_argument("--out", default=str(OUT), help="만들 파일 (기본: %(default)s)")
    args = parser.parse_args(argv)

    sources = {"maps": args.maps_json, "api": args.api_json, "en": args.en_json, "ko": args.ko_json}
    entries, warnings = build(load(args.maps_json), load(args.api_json), load(args.en_json), load(args.ko_json))
    doc = {
        "generated": date.today().isoformat(),
        "sources": {k: v if v.startswith("https://") else "local file" for k, v in sources.items()},
        "attribution": {
            "data": "tarkov.dev (MIT) https://github.com/the-hideout/tarkov-dev",
            "svg": "Map art by Shebuka and tarkov-dev-svg-maps contributors, CC BY-NC-SA 4.0",
            "svgLicense": "https://github.com/the-hideout/tarkov-dev-svg-maps",
        },
        "maps": entries,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(errors="replace")
    print(f"{out}: 맵 {len(entries)}개")
    for e in entries:
        image = "svg" if e["svgUrl"] else "타일" if e["tileUrl"] else "-"
        print(f"  {e['key']:<18} {e.get('nameKo') or e['name']:<12} ids={','.join(e['ids'])} {image} "
              f"탈출구 {len(e['extracts'])} 트랜짓 {len(e['transits'])} 스폰 {len(e['spawns'])} 층 {len(e['layers'])}")
        for vid, v in e["variants"].items():
            own = [f"{KIND_KO[kind]} {len(v[kind])}" for kind in ("extracts", "transits", "spawns") if kind in v]
            event = f"이벤트 맵 '{v.get('nameKo') or v['name']}', " if v.get("event") else ""
            if own or event:
                print(f"    {vid}: {event}자체 목록 {', '.join(own) or '없음'}")
    for w in warnings:
        print(f"  주의: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

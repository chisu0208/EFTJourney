import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from eftjourney import mapdata

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "update_maps.py"


def no_network():
    return mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("tests must not fetch anything"))


def load_script():
    # scripts/ is not a package, so load the file itself; importing it must not fetch anything
    spec = importlib.util.spec_from_file_location("update_maps", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    with no_network():
        spec.loader.exec_module(module)
    return module


update_maps = load_script()


def ex(name, faction, x, z, y=0.0):
    return {"name": name, "faction": faction, "position": {"x": x, "y": y, "z": z}}


# the day Factory's two "Gate 3" extracts; both carry one id, so only the position tells them apart
DAY_GATE_3 = [ex("Gate 3", "scav", 58.71, 60.87, 1.59), ex("Gate 3", "pmc", 58.43, 63.3, 3.17)]


def factions(variant, parent=DAY_GATE_3, *args):
    out = update_maps.markers({"extracts": variant}, {}, {}, {"extracts": parent}, *args)
    return {(e["x"], e["z"]): e["faction"] for e in out["extracts"]}


def interactive(key, *alt_maps, **extra):
    """An interactive map of tarkov.dev's maps.json, only what build() reads."""
    return {"key": key, "projection": "interactive", "altMaps": list(alt_maps), "transform": [1, 0, 1, 0],
            "bounds": [[-100, -100], [100, 100]], "svgPath": f"{key}.svg", **extra}


def location(name_id, normalized, name, spawns=()):
    """A JSON API location with PMC player spawns at (x, z)."""
    return {"nameId": name_id, "normalizedName": normalized, "name": name,
            "spawns": [{"position": {"x": x, "y": 0.0, "z": z}, "sides": ["pmc"], "categories": ["player"]}
                       for x, z in spawns]}


def api(*locations):
    return {"data": {"maps": {str(i): m for i, m in enumerate(locations)}}}


# as tarkov.dev has them: no map for the event ids, none for Sandbox_start either
MAPS_JSON = [
    {"maps": [interactive("factory", "night-factory")]},
    {"maps": [interactive("the-lab", "the-lab-dark", svgPath=None, tilePath="lab/{z}/{x}/{y}.png")]},
    {"maps": [interactive("ground-zero", "ground-zero-21")]},
]
LOCATIONS = [
    location("factory4_day", "factory", "Factory", [(1, 1)]),
    location("factory4_night", "night-factory", "Night Factory", [(9, 9)]),
    location("laboratory", "the-lab", "The Lab", [(2, 2)]),
    location("laboratory_dark", "the-lab-dark", "The Lab (Dark)", [(2, 2)]),
    location("Sandbox", "ground-zero", "Ground Zero", [(3, 3)]),
    location("Sandbox_high", "ground-zero-21", "Ground Zero 21+", [(3, 3)]),
    location("Sandbox_start", "ground-zero-tutorial", "Ground Zero Tutorial", [(-45.97, 33.59)]),
]
INFECTED_FACTORY = location("factory_event", "infected-factory", "Infected Factory", [(5, 5)])


class NoNetworkTest(unittest.TestCase):
    def setUp(self):
        patcher = no_network()
        patcher.start()
        self.addCleanup(patcher.stop)


class InheritedFactionTest(NoNetworkTest):
    def test_closest_pairs_first_in_either_list_order(self):
        # the scav gate moved 1.3 m toward the pmc one, which stayed put: still scav and pmc
        moved, pmc_spot = ex("Gate 3", None, 58.6, 62.2), ex("Gate 3", None, 58.43, 63.3)
        for variant in ([moved, pmc_spot], [pmc_spot, moved]):
            self.assertEqual(factions(variant), {(58.6, 62.2): "scav", (58.43, 63.3): "pmc"})

    def test_night_factory_gate_3(self):
        # tarkov.dev's night list: the same gates a few millimetres off, scav first, no factions
        night = [ex("Gate 3", None, 58.709, 60.86811, 1.59233332), ex("Gate 3", None, 58.43222, 63.29811, 3.17233324)]
        for variant in (night, night[::-1]):
            warnings = []
            self.assertEqual(factions(variant, DAY_GATE_3, "factory/factory4_night", warnings),
                             {(58.71, 60.87): "scav", (58.43, 63.3): "pmc"})
            self.assertEqual(warnings, [])

    def test_unmatched_extracts_fall_back_to_shared(self):
        # three night "Gate 3"s for two day ones: the far one is left over, though it comes first
        parent = DAY_GATE_3 + [ex("Gate X", None, 0.0, 0.0)]  # a parent without a faction passes nothing on
        variant = [ex("Gate 3", None, 70.0, 70.0), ex("Gate 3", None, 58.71, 60.87), ex("Gate 3", None, 58.43, 63.3),
                   ex("Gate X", None, 0.0, 0.0), ex("New Gate", None, 5.0, 5.0), ex("Gate 0", "scav", -63.67, 55.88)]
        self.assertEqual(factions(variant, parent), {
            (70.0, 70.0): "shared", (58.71, 60.87): "scav", (58.43, 63.3): "pmc",
            (0.0, 0.0): "shared", (5.0, 5.0): "shared", (-63.67, 55.88): "scav",
        })

    def test_far_match_is_reported(self):
        maps_json = [{"maps": [{"key": "factory", "projection": "interactive", "altMaps": ["night-factory"],
                                "transform": [1, 0, 1, 0], "bounds": [[-80, -80], [80, 80]], "svgPath": "svg"}]}]
        day = {"nameId": "factory4_day", "normalizedName": "factory", "name": "Factory", "extracts": DAY_GATE_3}
        night = {"nameId": "factory4_night", "normalizedName": "night-factory", "name": "Night Factory",
                 "extracts": [ex("Gate 3", None, 58.6, 62.2), ex("Gate 3", None, 58.43, 63.3)]}
        entries, warnings = update_maps.build(maps_json, {"data": {"maps": {"1": day, "2": night}}}, {}, {})
        extracts = entries[0]["variants"]["factory4_night"]["extracts"]
        self.assertEqual([(e["x"], e["z"], e["faction"]) for e in extracts], [(58.6, 62.2, "scav"), (58.43, 63.3, "pmc")])
        # only the moved gate is reported (1.33 m from the extract it inherits from)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertTrue(warnings[0].startswith("factory/factory4_night: "), warnings)
        self.assertIn("'Gate 3' (58.6, 62.2)", warnings[0])
        self.assertIn("1.3 m", warnings[0])


class AliasTest(NoNetworkTest):
    """Location ids the game plays on a map that tarkov.dev does not link to it (client 1.3.0 events, tutorial)."""

    def build(self, maps_json=MAPS_JSON, locations=LOCATIONS):
        entries, warnings = update_maps.build(maps_json, api(*locations), {}, {})
        return {e["key"]: e for e in entries}, warnings

    def test_ids_the_api_does_not_list_come_after_the_maps_own(self):
        maps, warnings = self.build()
        self.assertEqual(maps["factory"]["ids"], ["factory4_day", "factory4_night", "factory_event"])
        self.assertEqual(maps["the-lab"]["ids"], ["laboratory", "laboratory_dark", "laboratory_event"])
        # names only: without lists of their own the viewer draws the map's markers
        self.assertEqual(maps["factory"]["variants"]["factory_event"],
                         {"name": "Infected Factory", "nameKo": "오염된 공장", "event": True})
        self.assertEqual(maps["the-lab"]["variants"]["laboratory_event"],
                         {"name": "Infected Lab", "nameKo": "오염된 연구소", "event": True})
        # tarkov.dev's own variants stay as they were
        self.assertEqual(maps["factory"]["variants"]["factory4_night"],
                         {"name": "Night Factory", "spawns": [{"x": 9.0, "y": 0.0, "z": 9.0}]})
        self.assertEqual(maps["the-lab"]["variants"]["laboratory_dark"], {"name": "The Lab (Dark)"})
        self.assertEqual(warnings, [])
        doc = {"maps": list(maps.values())}
        self.assertEqual(mapdata.entry_for(doc, "Factory_Event")["key"], "factory")
        self.assertEqual(mapdata.canonical_id(doc, "laboratory_event"), "laboratory")

    def test_an_alias_the_api_lists_brings_its_own_lists(self):
        # Ground Zero's tutorial: an API location that maps.json shows on no map ("has no map" before)
        maps, warnings = self.build()
        self.assertEqual(maps["ground-zero"]["ids"], ["sandbox", "sandbox_high", "sandbox_start"])
        self.assertEqual(maps["ground-zero"]["variants"]["sandbox_start"],
                         {"name": "Ground Zero Tutorial", "spawns": [{"x": -45.97, "y": 0.0, "z": 33.59}]})
        self.assertEqual(warnings, [])
        # the event maps as the API lists them since 2026-10-29: its names and spawns, still an event
        maps, warnings = self.build(locations=LOCATIONS + [INFECTED_FACTORY])
        self.assertEqual(maps["factory"]["ids"], ["factory4_day", "factory4_night", "factory_event"])
        self.assertEqual(maps["factory"]["variants"]["factory_event"],
                         {"name": "Infected Factory", "event": True, "spawns": [{"x": 5.0, "y": 0.0, "z": 5.0}]})
        self.assertEqual(warnings, [])

    def test_tarkov_dev_linking_the_id_itself_wins(self):
        # maps.json shows it on Factory: the id once, still marked as an event
        maps_json = [{"maps": [interactive("factory", "night-factory", "infected-factory")]}] + MAPS_JSON[1:]
        maps, warnings = self.build(maps_json, LOCATIONS + [INFECTED_FACTORY])
        self.assertEqual(maps["factory"]["ids"], ["factory4_day", "factory4_night", "factory_event"])
        self.assertTrue(maps["factory"]["variants"]["factory_event"]["event"])
        self.assertEqual(warnings, [])
        # maps.json gives it a map of its own: no alias, so no two maps claim the id
        maps, warnings = self.build(MAPS_JSON + [{"maps": [interactive("infected-factory")]}], LOCATIONS + [INFECTED_FACTORY])
        self.assertEqual(maps["factory"]["ids"], ["factory4_day", "factory4_night"])
        self.assertNotIn("factory_event", maps["factory"]["variants"])
        self.assertEqual(maps["infected-factory"]["ids"], ["factory_event"])
        self.assertEqual(len(warnings), 1, warnings)
        self.assertTrue(warnings[0].startswith("factory/factory_event: "), warnings)

    def test_the_shipped_maps_resolve_the_alias_ids(self):
        # eftjourney/data/maps.json as generated: the raids of these ids get a map, ids[0] is unchanged
        maps = mapdata.load_maps()
        for alias_id, key, canonical in (("factory_event", "factory", "factory4_day"),
                                         ("laboratory_event", "the-lab", "laboratory"),
                                         ("sandbox_start", "ground-zero", "sandbox")):
            entry = mapdata.entry_for(maps, alias_id)
            self.assertIsNotNone(entry, alias_id)
            self.assertEqual(entry["key"], key)
            self.assertEqual(entry["ids"][0], canonical)
            aliases = update_maps.ALIASES[key]
            self.assertEqual(entry["ids"], [i for i in entry["ids"] if i not in aliases] + list(aliases))
            variant = entry["variants"][alias_id]
            self.assertTrue(variant.get("name"), variant)
            self.assertEqual(variant.get("event", False), aliases[alias_id].get("event", False))


class KoreanOutputTest(NoNetworkTest):
    def test_build_warnings(self):
        factory = location("factory4_day", "factory", "Factory")
        factory["extracts"] = [ex("Far Gate", "pmc", 500.0, 0.0)]
        maps_json = [
            {"maps": [interactive("factory")]},
            # tile-only, and its own markers land far off the tile
            {"maps": [interactive("icebreaker", svgPath=None, tilePath="ice/{z}/{x}/{y}.png", transform=[1, 1000, 1, 1000])]},
            {"maps": [interactive("terminal", svgPath=None)]},
            {"maps": [interactive("streets-of-tarkov")]},
        ]
        locations = [factory, location("Icebreaker", "icebreaker", "Icebreaker", [(0, 0)]),
                     location("Terminal", "terminal", "Terminal"), location("Nowhere", "nowhere", "Nowhere")]
        _, warnings = update_maps.build(maps_json, api(*locations), {}, {})
        self.assertEqual(warnings, [
            "factory: 탈출구 'Far Gate' (500.0, 0.0) 의 위치가 맵 범위에서 400 m 벗어나 있어 뺐습니다",
            "icebreaker: 이 맵의 탈출구·트랜짓·스폰 대부분이 지도 그림 밖에 찍혀 위치를 참고용으로 표시합니다 (좌표 보정값이 그림과 맞지 않음)",
            "terminal: 지도 그림(SVG, 타일)이 없습니다",
            "streets-of-tarkov: API 데이터에 이 맵의 게임 위치 id가 없어 건너뛰었습니다",
            "위치 Nowhere (nowhere): 이 위치를 보여 주는 tarkov.dev 지도가 없어 넣지 않았습니다 (다른 맵과 같은 지형이면 ALIASES에 추가)",
        ])

    def test_main_writes_the_aliases_and_prints_korean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name, doc in (("maps", MAPS_JSON), ("api", api(*LOCATIONS)), ("en", {"data": {}}), ("ko", {"data": {}})):
                (tmp / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
            out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")  # main() reconfigures stdout
            with contextlib.redirect_stdout(out):
                code = update_maps.main(["--maps-json", str(tmp / "maps.json"), "--api-json", str(tmp / "api.json"),
                                         "--en-json", str(tmp / "en.json"), "--ko-json", str(tmp / "ko.json"),
                                         "--out", str(tmp / "out" / "maps.json")])
            out.flush()
            text = out.buffer.getvalue().decode("utf-8")
            # a rebuilt file keeps the aliases
            maps = mapdata.load_maps(tmp / "out" / "maps.json")
        self.assertEqual(code, 0)
        self.assertEqual(mapdata.entry_for(maps, "factory_event")["key"], "factory")
        self.assertEqual(mapdata.entry_for(maps, "laboratory_event")["ids"][0], "laboratory")
        self.assertEqual(mapdata.entry_for(maps, "sandbox_start")["ids"][0], "sandbox")
        self.assertIn("    factory4_night: 자체 목록 스폰 1\n", text)
        self.assertIn("    factory_event: 이벤트 맵 '오염된 공장', 자체 목록 없음\n", text)
        self.assertIn("    sandbox_start: 자체 목록 스폰 1\n", text)
        self.assertRegex(text, r"the-lab .* 타일 탈출구 0 ")
        self.assertNotRegex(text, r"\b(extracts|transits|spawns|tiles)\b")


if __name__ == "__main__":
    unittest.main()

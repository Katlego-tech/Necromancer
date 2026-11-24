import copy
import glob
import json
import os
import unittest

from mascot import appearance

ROOT = os.path.join(os.path.dirname(__file__), "..", "mascot")
COSTUMES = sorted(glob.glob(os.path.join(ROOT, "costumes", "*.json")))


def good():
    return {
        "spirit": "enron", "base": "little_necromancer", "renderer": "2d", "costume": "enron_ghost",
        "palette": {"body": "#FFFFFF", "trim": "#8A6ED6", "eyes": "#7DF9FF"},
        "props": ["paper"], "animation": "summon", "reduced_motion": False,
    }


class CostumeFiles(unittest.TestCase):
    def test_expected_costumes_exist(self):
        names = {os.path.basename(p) for p in COSTUMES}
        self.assertTrue({"default.json", "enron.json", "wayback.json", "grid.json"} <= names)

    def test_every_costume_validates(self):
        for p in COSTUMES:
            with self.subTest(p=os.path.basename(p)):
                self.assertEqual(appearance.validate(json.load(open(p))), [])
                appearance.load_costume(p)

    def test_wayback_is_labelled_era_inspired(self):
        cfg = appearance.load_costume(os.path.join(ROOT, "costumes", "wayback.json"))
        self.assertIs(cfg["era_inspired"], True)
        self.assertNotIn("snapshot", cfg)

    def test_grid_is_amber_with_ring_and_glyph(self):
        cfg = appearance.load_costume(os.path.join(ROOT, "costumes", "grid.json"))
        self.assertEqual(cfg["palette"]["eyes"].upper(), "#FFB000")
        self.assertTrue({"electric_ring", "warning_glyph"} <= set(cfg["props"]))

    def test_schema_matches_validator(self):
        schema = json.load(open(os.path.join(ROOT, "appearance.schema.json")))
        self.assertEqual(tuple(schema["required"]), appearance.REQUIRED)
        self.assertEqual(tuple(schema["properties"]["props"]["items"]["enum"]), appearance.PROPS)
        self.assertEqual(tuple(schema["properties"]["animation"]["enum"]), appearance.ANIMATIONS)

    def test_load_costume_raises_on_invalid(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"spirit": "enron"}, f)
        try:
            with self.assertRaises(ValueError):
                appearance.load_costume(f.name)
        finally:
            os.unlink(f.name)


class InvalidConfigs(unittest.TestCase):
    def test_good_config_passes(self):
        self.assertEqual(appearance.validate(good()), [])

    def test_rejections(self):
        def mut(f):
            c = copy.deepcopy(good())
            f(c)
            return c
        cases = {
            "not an object": [],
            "missing field": mut(lambda c: c.pop("palette")),
            "unknown field": mut(lambda c: c.update(extra=1)),
            "bad spirit": mut(lambda c: c.update(spirit="ghost")),
            "bad base": mut(lambda c: c.update(base="other")),
            "bad renderer": mut(lambda c: c.update(renderer="webgl")),
            "bad costume name": mut(lambda c: c.update(costume="Bad Name")),
            "bad hex": mut(lambda c: c["palette"].update(eyes="cyan")),
            "short hex": mut(lambda c: c["palette"].update(body="#FFF")),
            "missing palette key": mut(lambda c: c["palette"].pop("trim")),
            "extra palette key": mut(lambda c: c["palette"].update(glow="#000000")),
            "bad prop": mut(lambda c: c.update(props=["jetpack"])),
            "duplicate prop": mut(lambda c: c.update(props=["paper", "paper"])),
            "props not list": mut(lambda c: c.update(props="paper")),
            "bad animation": mut(lambda c: c.update(animation="explode")),
            "reduced_motion not bool": mut(lambda c: c.update(reduced_motion="yes")),
            "wayback without era label": mut(lambda c: c.update(spirit="wayback")),
            "bad snapshot": mut(lambda c: c.update(spirit="wayback", snapshot={"url": "x", "timestamp": "2001"})),
        }
        for name, cfg in cases.items():
            with self.subTest(name):
                self.assertTrue(appearance.validate(cfg), name)

    def test_wayback_with_snapshot_reference_is_ok(self):
        c = good()
        c.update(spirit="wayback", snapshot={"url": "http://example.com/", "timestamp": "19990101000000"})
        self.assertEqual(appearance.validate(c), [])

    def test_spirit_prompts_do_not_hardwire_appearance(self):
        # spirit/voice code must not embed palette colours or costume names
        base = os.path.join(os.path.dirname(__file__), "..")
        for sub in ("spirits", "voice"):
            for dp, _, fs in os.walk(os.path.join(base, sub)):
                for fn in fs:
                    if fn.endswith(".py"):
                        text = open(os.path.join(dp, fn), encoding="utf-8").read()
                        self.assertNotIn("palette", text, fn)
                        self.assertNotIn("#7DF9FF", text.upper(), fn)


if __name__ == "__main__":
    unittest.main()

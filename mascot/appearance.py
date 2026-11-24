"""Validator for the declarative appearance config (see appearance.schema.json).

Standard library only. `validate(cfg)` returns a list of error strings
(empty = valid); `load_costume(path)` raises ValueError on invalid files.
"""
import json
import re

SPIRITS = ("default", "enron", "wayback", "grid")
RENDERERS = ("2d", "3d")
PROPS = ("paper", "lapels", "browser_halo", "pixel_accents", "electric_ring", "warning_glyph")
ANIMATIONS = ("none", "idle", "summon", "shapeshift", "alert")
REQUIRED = ("spirit", "base", "renderer", "costume", "palette", "props", "animation", "reduced_motion")
OPTIONAL = ("label", "era_inspired", "snapshot")
PALETTE_KEYS = ("body", "trim", "eyes")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_NAME = re.compile(r"^[a-z0-9_]+$")
_TS = re.compile(r"^[0-9]{14}$")


def validate(cfg):
    if not isinstance(cfg, dict):
        return ["config must be an object"]
    errs = []
    for k in REQUIRED:
        if k not in cfg:
            errs.append(f"missing required field: {k}")
    for k in cfg:
        if k not in REQUIRED and k not in OPTIONAL:
            errs.append(f"unknown field: {k}")
    if cfg.get("spirit", SPIRITS[0]) not in SPIRITS:
        errs.append(f"spirit must be one of {SPIRITS}")
    if "base" in cfg and cfg["base"] != "little_necromancer":
        errs.append("base must be 'little_necromancer'")
    if "renderer" in cfg and cfg["renderer"] not in RENDERERS:
        errs.append(f"renderer must be one of {RENDERERS}")
    if "costume" in cfg and not (isinstance(cfg["costume"], str) and _NAME.match(cfg["costume"])):
        errs.append("costume must match [a-z0-9_]+")
    if "label" in cfg and not isinstance(cfg["label"], str):
        errs.append("label must be a string")
    pal = cfg.get("palette")
    if "palette" in cfg:
        if not isinstance(pal, dict):
            errs.append("palette must be an object")
        else:
            for k in PALETTE_KEYS:
                v = pal.get(k)
                if not (isinstance(v, str) and _HEX.match(v)):
                    errs.append(f"palette.{k} must be a #RRGGBB colour")
            for k in pal:
                if k not in PALETTE_KEYS:
                    errs.append(f"unknown palette key: {k}")
    props = cfg.get("props")
    if "props" in cfg:
        if not isinstance(props, list):
            errs.append("props must be an array")
        else:
            for p in props:
                if p not in PROPS:
                    errs.append(f"unknown prop: {p!r}")
            if len(set(map(str, props))) != len(props):
                errs.append("props must be unique")
    if "animation" in cfg and cfg["animation"] not in ANIMATIONS:
        errs.append(f"animation must be one of {ANIMATIONS}")
    if "reduced_motion" in cfg and not isinstance(cfg["reduced_motion"], bool):
        errs.append("reduced_motion must be a boolean")
    if "era_inspired" in cfg and not isinstance(cfg["era_inspired"], bool):
        errs.append("era_inspired must be a boolean")
    if "snapshot" in cfg:
        s = cfg["snapshot"]
        if not (isinstance(s, dict) and set(s) == {"url", "timestamp"}
                and isinstance(s["url"], str) and isinstance(s["timestamp"], str) and _TS.match(s["timestamp"])):
            errs.append("snapshot must be {url, timestamp(14 digits)}")
    # Wayback rule: a palette is either traceable to a snapshot or labelled era-inspired.
    if cfg.get("spirit") == "wayback" and not (cfg.get("era_inspired") is True or "snapshot" in cfg):
        errs.append("wayback costumes need era_inspired=true or a snapshot reference")
    return errs


def load_costume(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    errs = validate(cfg)
    if errs:
        raise ValueError(f"{path}: " + "; ".join(errs))
    return cfg

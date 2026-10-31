"""Bundled star / constellation / Messier catalog and name search."""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from importlib import resources
from typing import Any

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ",
    "nu": "ν", "xi": "ξ", "omicron": "ο", "pi": "π", "rho": "ρ", "sigma": "σ",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().strip()
    words = [GREEK.get(w, w) for w in text.replace("-", " ").split()]
    return " ".join(words)


class Catalog:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.stars: list[dict[str, Any]] = data["stars"]
        self.constellations: list[dict[str, Any]] = data["constellations"]
        self.dsos: list[dict[str, Any]] = data["dsos"]
        self._by_hip = {s["hip"]: s for s in self.stars}
        self._dso = {d["id"].upper(): d for d in self.dsos}
        self._con_names = {c["id"]: c["name"] for c in self.constellations}
        self._index: list[tuple[str, str, dict[str, Any]]] = []
        for s in self.stars:
            for key in filter(None, (s["name"], s["desig"], f"hip {s['hip']}", f"hip{s['hip']}")):
                self._index.append((_norm(key), "star", s))
        for d in self.dsos:
            for key in filter(None, (d["id"], d["name"], d["desig"])):
                self._index.append((_norm(key), "dso", d))

    def star_by_hip(self, hip: int) -> dict[str, Any]:
        return self._by_hip[hip]

    def dso_by_id(self, did: str) -> dict[str, Any] | None:
        return self._dso.get(did.upper())

    def constellation_name(self, cid: str) -> str:
        return self._con_names.get(cid, cid)

    @staticmethod
    def star_label(star: dict[str, Any]) -> str:
        if star["name"] and star["desig"]:
            return f"{star['name']} ({star['desig']})"
        return star["name"] or star["desig"] or f"HIP {star['hip']}"

    def search(self, query: str, limit: int = 20, planets: dict[str, tuple[str, str]] | None = None) -> list[dict[str, Any]]:
        q = _norm(query)
        if not q:
            return []
        results: dict[str, dict[str, Any]] = {}

        def add(rid: str, name: str, kind: str, score: float, mag: float | None, extra: str = "") -> None:
            prev = results.get(rid)
            if prev is None or prev["score"] < score:
                results[rid] = {"id": rid, "name": name, "kind": kind, "score": score, "magnitude": mag, "detail": extra}

        def score_of(key: str) -> float:
            if key == q:
                return 100.0
            if key.startswith(q):
                return 80.0
            if f" {q}" in f" {key}":
                return 60.0
            if q in key:
                return 40.0
            return 0.0

        specials = {"sun": ("Sun", "sun"), "moon": ("Moon", "moon")}
        for pid, (name, _) in (planets or {}).items():
            specials[pid] = (name, "planet")
        for key, (name, kind) in specials.items():
            sc = score_of(key)
            if sc:
                rid = key if kind != "planet" else f"planet:{key}"
                add(rid, name, kind, sc + 5, None, "Solar system")
        for key, kind, obj in self._index:
            sc = score_of(key)
            if not sc:
                continue
            if kind == "star":
                mag = obj["mag"]
                add(f"hip:{obj['hip']}", self.star_label(obj), "star", sc - mag * 0.5, mag,
                    self.constellation_name(obj["con"]) if obj["con"] else "")
            else:
                label = obj["id"] + (f" ({obj['name']})" if obj["name"] else "")
                add(f"dso:{obj['id']}", label, "dso", sc - 3, obj["mag"], obj["type"])
        ranked = sorted(results.values(), key=lambda r: -r["score"])
        return ranked[:limit]

    def client_payload(self, max_mag: float = 6.0) -> dict[str, Any]:
        """Compact arrays for the browser sky map."""
        stars = [s for s in self.stars if s["mag"] <= max_mag]
        return {
            "source": self.data["source"],
            "epoch": self.data["epoch"],
            # [hip, ra, dec, mag, bv, label]
            "stars": [
                [s["hip"], s["ra"], s["dec"], s["mag"], s["bv"], s["name"] or ""] for s in stars
            ],
            "constellations": self.constellations,
            "dsos": self.dsos,
        }


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    raw = (resources.files("openskymount") / "data" / "catalog.json").read_text(encoding="utf-8")
    return Catalog(json.loads(raw))

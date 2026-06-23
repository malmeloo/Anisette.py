from __future__ import annotations

import json
from pathlib import Path

from anisette import AnisetteProvider, LocalADI

_PROV_JSON = Path(__file__).parent / ".prov.json"


def test_init_save():
    ani = AnisetteProvider.init(adi_factory=LocalADI.create())

    assert isinstance(ani.get_headers(), dict)

    with _PROV_JSON.open("w+") as f:
        json.dump(ani.to_json(), f, indent=2)


def test_load():
    with _PROV_JSON.open("r") as f:
        data = json.load(f)

    ani = AnisetteProvider.from_json(data, adi_factory=LocalADI.create())

    assert isinstance(ani.get_headers(), dict)

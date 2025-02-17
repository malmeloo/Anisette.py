from __future__ import annotations

from anisette import Anisette


def test_init_save():
    ani = Anisette.init("applemusic.apk")

    assert isinstance(ani.get_data(), dict)

    ani.save_all("bundle.bin")


def test_load():
    ani = Anisette.load("bundle.bin")

    assert isinstance(ani.get_data(), dict)

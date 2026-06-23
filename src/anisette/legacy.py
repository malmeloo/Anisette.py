"""Functions to facilitate backwards compatibility with older versions of the library."""

import json
import tarfile
from pathlib import Path
from typing import BinaryIO

from ._device import Device
from ._util import open_file


def import_provisioning_binary(file: BinaryIO | Path | str) -> tuple[Device, bytes | None]:
    """Read a legacy provisioning binary and return the device and adi_pb contained within."""
    with open_file(file, "rb") as f, tarfile.open(fileobj=f, mode="r:*") as tf:
        # device.json is mandatory
        device_json_file = tf.extractfile("./device/device.json")
        if device_json_file is None:
            msg = "device.json not found in provisioning binary"
            raise ValueError(msg)
        device_json = json.load(device_json_file)
        device = Device.from_json(device_json)

        # adi_pb may not be present if unprovisioned
        adi_pb: bytes | None = None
        try:
            adi_pb_file = tf.extractfile("./adi/adi.pb")
            if adi_pb_file is not None:
                adi_pb = adi_pb_file.read().strip()
        except KeyError:
            pass

    return device, adi_pb

"""Decode T33 single-touch evdev recordings without guessing physical buttons.

Directions describe transmitted coordinates. Map them to buttons only after
the user confirms a calibration sequence. No pass marks or detector writes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


class TouchGestures:
    def __init__(self, swipe_min=1000, tap_max=250):
        self.swipe_min = swipe_min
        self.tap_max = tap_max
        self.reset()

    def reset(self):
        self.x = self.y = None
        self.contact = None
        self.ending = False
        self.slot = 0

    def update(self, event_type, code, value, mono):
        if event_type == 0 and code == 3:
            self.reset()
            raise ValueError("SYN_DROPPED: gesture stream is incomplete")
        if event_type == 3:
            if code == 47:  # ABS_MT_SLOT
                self.slot = value
            elif self.slot != 0:
                self.reset()
                raise ValueError("Only calibrated single-contact T33 reports are supported")
            elif code == 53:
                self.x = value
            elif code == 54:
                self.y = value
            elif code == 57:  # ABS_MT_TRACKING_ID
                if value >= 0:
                    if self.contact is not None:
                        self.reset()
                        raise ValueError("A contact was replaced without release")
                    self.contact = {"start_mono": mono, "points": []}
                    self.ending = False
                else:
                    self.ending = True
        if event_type != 0 or code != 0 or self.contact is None:
            return None
        # Coordinates persist between reports: Linux omits unchanged values.
        # Commit at SYN_REPORT so X and Y belong to the same report.
        if self.x is not None and self.y is not None:
            self.contact["points"].append((self.x, self.y))
        if not self.ending:
            return None
        contact, self.contact = self.contact, None
        self.ending = False
        points = contact.pop("points")
        result = {"kind": "gesture", **contact, "end_mono": mono,
                  "gesture": "unknown"}
        if not points:
            return {**result, "reason": "coordinates_unavailable"}
        x0, y0 = points[0]
        dx, dy = points[-1][0] - x0, points[-1][1] - y0
        excursion = max(max(abs(x - x0), abs(y - y0)) for x, y in points)
        result.update(dx=dx, dy=dy, excursion=excursion)
        if excursion <= self.tap_max:
            result["gesture"] = "tap"
        elif abs(dx) >= self.swipe_min and abs(dx) >= 2 * abs(dy):
            result["gesture"] = "x+" if dx > 0 else "x-"
        elif abs(dy) >= self.swipe_min and abs(dy) >= 2 * abs(dx):
            result["gesture"] = "y+" if dy > 0 else "y-"
        else:
            result["reason"] = "ambiguous_path"
        return result


def decode_records(records):
    decoders = {}
    for record in records:
        kind, device = record["kind"], record.get("device")
        if kind == "input_ready":
            decoders[device] = TouchGestures()
            absolute = record.get("absolute_state", {})
            decoders[device].x = absolute.get("53")
            decoders[device].y = absolute.get("54")
        elif kind == "input_removed":
            decoders.pop(device, None)  # Never join contacts across reconnections.
        elif kind == "error":
            raise ValueError(record["error"])
        elif kind == "input":
            decoder = decoders.setdefault(device, TouchGestures())
            gesture = decoder.update(record["type"], record["code"],
                                     record["value"], record["input_mono"])
            if gesture is not None:
                yield {**gesture, "device": device, "observed_epoch": record["epoch"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    args = parser.parse_args()
    with args.recording.open(encoding="utf-8") as stream:
        for gesture in decode_records(json.loads(line) for line in stream if line.strip()):
            print(json.dumps(gesture, ensure_ascii=True))


if __name__ == "__main__":
    main()

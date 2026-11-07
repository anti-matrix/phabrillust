"""Check images for the phabrillust signature.

Usage: python -m phabrillust.verify IMAGE [IMAGE ...]

Reports two independent layers:
- pixel watermark: survives re-encoding/resizing (social media uploads);
- metadata inscription: the plain-text signature, lost when platforms strip
  metadata.

Exit code is 0 when every image carries the pixel watermark, 1 otherwise.
"""

import argparse
import sys
from dataclasses import dataclass

from PIL import Image

from phabrillust.engine.signature import SIGNATURE, find_seed
from phabrillust.engine.watermark import WATERMARK_TEXT, read_watermark


@dataclass(frozen=True)
class VerifyReport:
    path: str
    size: tuple[int, int]
    watermark: str | None  # decoded text, None when not found
    confidence: float
    has_metadata: bool
    seed: int | None = None  # from the metadata text; lost when metadata is stripped

    @property
    def watermark_ok(self) -> bool:
        return self.watermark == WATERMARK_TEXT


def check(path: str) -> VerifyReport:
    """Read both signature layers from an image file. Raises OSError/ValueError."""
    with open(path, "rb") as fp:
        data = fp.read()
    has_metadata = SIGNATURE.encode("ascii") in data
    with Image.open(path) as img:
        img.load()
        size = img.size
        result = read_watermark(img)
    return VerifyReport(path, size, result.text, result.confidence, has_metadata, find_seed(data))


def verify(path: str) -> bool:
    report = check(path)
    print(path)
    print(f"  size:      {report.size[0]}x{report.size[1]}")
    if report.watermark is not None:
        note = "" if report.watermark_ok else "  (unexpected text)"
        print(f"  watermark: {report.watermark!r}  (confidence {report.confidence:.2f}){note}")
    else:
        print(f"  watermark: not found  (confidence {report.confidence:.2f})")
    print(f"  metadata:  {'signature present' if report.has_metadata else 'not present (stripped or never signed)'}")
    if report.seed is not None:
        print(f"  seed:      {report.seed}")
    return report.watermark is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m phabrillust.verify",
        description="Check images for the phabrillust watermark and metadata signature.",
    )
    parser.add_argument("images", nargs="+", help="image files to check")
    args = parser.parse_args(argv)

    ok = True
    for path in args.images:
        try:
            ok = verify(path) and ok
        except (OSError, ValueError) as exc:
            print(f"{path}\n  error: {exc}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

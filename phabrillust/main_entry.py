"""Entry point shared by ``python -m phabrillust``."""

import argparse
import sys
from pathlib import Path

from phabrillust.engine.image_state import ImageState
from phabrillust.engine.source_generator import blank
from phabrillust.gui.app import create_app
from phabrillust.gui.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="phabrillust — digital glitch art")
    parser.add_argument("image", nargs="?", help="Optional image to load on startup")
    parser.add_argument("--seed", type=int, default=0, help="Global random seed")
    parser.add_argument("--width", type=int, default=800, help="Default canvas width")
    parser.add_argument("--height", type=int, default=600, help="Default canvas height")
    args = parser.parse_args()

    app = create_app()
    state = ImageState()

    if args.image and Path(args.image).exists():
        state.load(args.image)
    else:
        state.set_source(blank(args.width, args.height))

    window = MainWindow(
        state,
        initial_seed=args.seed,
        default_width=args.width,
        default_height=args.height,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

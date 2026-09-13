import argparse
import sys

from emulator.engine import EmulatorEngine

RESOLUTION_PRESETS = {
    "nokia-96x65": (96, 65),
    "nokia-128x128": (128, 128),
    "nokia-s40-176x208": (176, 208),
    "nokia-176x220": (176, 220),
    "qvga-240x320": (240, 320),
    "240x400": (240, 400),
    "landscape-320x240": (320, 240),
}


def main(argv=None):
    p = argparse.ArgumentParser(description="J2ME (MIDP/CLDC) emulator")
    p.add_argument("jar", help="path to the .jar file to run")
    p.add_argument("--width", type=int, default=240)
    p.add_argument("--height", type=int, default=320)
    p.add_argument("--preset", choices=sorted(RESOLUTION_PRESETS), default=None)
    p.add_argument("--scale", type=int, default=2, help="window zoom factor")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--force-repaint", action="store_true",
                    help="call paint() every frame even if the MIDlet never calls repaint() itself "
                         "(compatibility fallback for poorly-behaved jars)")
    p.add_argument("--midlet-class", default=None,
                    help="internal class name of the MIDlet to run, e.g. com/example/Game "
                         "(auto-detected if omitted)")
    p.add_argument("--locale", default=None,
                    help="override microedition.locale (e.g. 'de', 'fr', 'en-US') -- matters "
                         "for jars that ship locale-specific resource files, since not every "
                         "jar includes every language and the default is just a guess")
    args = p.parse_args(argv)

    width, height = args.width, args.height
    if args.preset:
        width, height = RESOLUTION_PRESETS[args.preset]

    engine = EmulatorEngine(width=width, height=height, scale=args.scale,
                             force_continuous_repaint=args.force_repaint,
                             target_fps=args.fps, locale=args.locale)
    print(f"[loader] loading {args.jar}", flush=True)
    engine.load_jar(args.jar)
    engine.start_midlet(args.midlet_class)
    print(f"[loader] running MIDlet {engine.midlet_class_name}", flush=True)
    engine.run()


if __name__ == "__main__":
    sys.exit(main() or 0)

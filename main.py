#!/usr/bin/env python3
"""
Entry point for the J2ME emulator's PyQt5 control panel.

Run this to open the GUI (load a .jar, browse its classes/sprites, choose
a screen resolution, then launch the pygame emulator window).

For a bare command-line run without the GUI:
    python -m emulator.runner path/to/game.jar --preset qvga-240x320
"""
from gui.main_window import main

if __name__ == "__main__":
    main()

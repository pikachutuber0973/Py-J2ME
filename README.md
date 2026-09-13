J2ME Emulator (pygame + PyQt5)A from-scratch Java 2 Micro Edition (MIDP/CLDC) emulator written in Python:  jvm/ — A real .class file parser and JVM bytecode interpreter that executes constant pools, fields, methods, and Code attributes instruction by instruction.  midp/ — Python implementations of CLDC (java.lang.*, java.util.*) and MIDP (javax.microedition.*) APIs, mapping graphics directly to Pygame surfaces.  emulator/ — The Pygame runtime handling display windows, user input, MIDlet lifecycles, and background threads for game loops.  gui/ — A PyQt5 interface to inspect .jar contents, preview embedded sprites, set emulated screen resolutions, and launch games.  Like real J2ME hardware, game .jar files only contain their own application classes; core classes like Canvas or String are provided by the emulator's runtime in midp/natives.py.  Setup & RunningBashpip install -r requirements.txt
python main.py
```[cite: 1]

Running `python main.py` opens the PyQt5 control panel[cite: 1]. Clicking **Run** launches `emulator/runner.py` in a separate process via `QProcess` so SDL and Qt event loops do not conflict[cite: 1].

**Compatibility:**
PyQt5 is used instead of PyQt6 because Qt6 dropped support for Windows 7 (`ImportError: DLL load failed`)[cite: 1]. PyQt5 supports Windows 7+ and Python 3.8+[cite: 1]. The core emulator has no Qt dependency and can run headlessly via the command line:[cite: 1]

```bash
python -m emulator.runner path/to/game.jar --preset qvga-240x320 --scale 2
```[cite: 1]

## Controls

Default keybindings follow the standard Nokia Series 40 key layout (`UP=-1`, `FIRE=-5`, etc.)[cite: 1]:

| Key | J2ME Action |
|---|---|
| `Q` | Left soft key[cite: 1] |
| `W` | Right soft key[cite: 1] |
| `Space` | Fire / center key[cite: 1] |
| Arrow keys | D-pad (Up / Down / Left / Right)[cite: 1] |
| `0`–`9` | Numeric keypad[cite: 1] |
| `*` | Star key[cite: 1] |
| `/` | Pound / `#` key[cite: 1] |

`Canvas.getGameAction(keyCode)` functions as expected for portable titles[cite: 1].

## Testing

Test the environment using the included test game in `examples/TestGame.jar`:[cite: 1]

```bash
python -m emulator.runner examples/TestGame.jar --width 176 --height 208
```[cite: 1]

Use the arrow keys and Space key to interact and verify real-time counter updates on screen[cite: 1]. To run custom titles, load any `.jar` containing a class that extends `javax.microedition.midlet.MIDlet`[cite: 1].

## Implemented Features

* **Bytecode Engine:** Parses `.class` files completely and executes CLDC-profile opcodes: arithmetic, stack operations, control flow (`if*`, `goto`, switches), object/array creation, field/method access, and exception handling (`try`/`catch`)[cite: 1].
* **Core Libraries:** Supports `java.lang` classes (including Python thread-backed `Thread` execution), `java.util` utilities (`Vector`, `Hashtable`, `Random`), and standard `MIDlet` state management (`startApp`, `pauseApp`, `destroyApp`)[cite: 1].
* **LCDUI Graphics:** Features `Canvas` and `GameCanvas`, shapes, text formatting with anchors, clipping, resource loading (PNG, GIF, JPEG), and `Sprite` transforms[cite: 1].
* **GUI Tools:** Class hierarchy browser and sprite viewer built directly into the PyQt5 interface[cite: 1].

## Limitations

* **No `invokedynamic`:** Modern Java 9+ string concatenation opcodes are unsupported; era-accurate J2ME bytecode from the MIDP era is unaffected[cite: 1].
* **Non-Canvas UI:** High-level UI screens (`Form`, `List`, `TextBox`) track internal state but skip visual rendering[cite: 1].
* **Synchronous Repaints:** `repaint()` executes immediately rather than queuing, which works for most games but may affect timing-sensitive visual tricks[cite: 1].
* **String Equality:** Reference comparison (`==`) on non-null strings falls back to value equality (`.equals()`) for compatibility[cite: 1].
* **Unimplemented APIs:** Media playback, persistence (`RecordStore`), networking, reflection, and MIDP 3.0 APIs are not implemented; unhandled native calls log warnings to the console[cite: 1].

## Extending the Engine

Native API bindings are registered in `midp/natives.py` using `self.m(class_name, method_name, descriptor, fn)`[cite: 1]. Adding support for new methods typically requires adding a single registration line without altering the core interpreter in `jvm/interpreter.py`[cite: 1].

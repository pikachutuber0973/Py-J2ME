# J2ME Emulator (pygame + PyQt5)

A J2ME (MIDP/CLDC) emulator written from scratch in Python:

- **`jvm/`** — a real `.class` file parser and a JVM bytecode interpreter
  (the "JVM parser" — J2ME games are compiled Java bytecode, not
  JavaScript, so this is what actually needs interpreting: constant pool,
  fields, methods, and a `Code` attribute executed instruction-by-instruction).
- **`midp/`** — a from-scratch Python implementation of the CLDC
  (`java.lang.*`, `java.util.*`) and MIDP (`javax.microedition.lcdui.*`,
  `javax.microedition.midlet.MIDlet`) APIs, with all drawing mapped onto
  pygame `Surface`s.
- **`emulator/`** — the pygame runtime: window, MIDlet lifecycle, key
  input, and the background-thread support most J2ME games rely on for
  their main game loop.
- **`gui/`** — a PyQt5 control panel: open a `.jar`, browse its classes,
  preview embedded sprites, set the emulated screen resolution, and
  launch the emulator.

This mirrors how real device-independent J2ME emulators work: a MIDlet
JAR only ever contains the *game's own* classes. `java.lang.String`,
`javax.microedition.lcdui.Canvas`, etc. are provided by the phone's
firmware — here, that firmware is the Python code in `midp/natives.py`.

## Setup

```bash
pip install -r requirements.txt
python main.py
```

`python main.py` opens the PyQt5 control panel. Clicking **Run** launches
the pygame emulator window as a separate process (`emulator/runner.py`) —
this is deliberate: SDL's window/event loop and Qt's don't reliably share
one process across platforms, so QProcess launches and streams the
emulator's console output back into the GUI instead.

**Compatibility note:** the GUI uses PyQt5, not PyQt6, on purpose — Qt6
(and therefore PyQt6) dropped Windows 7 support outright, so PyQt6 fails
to even *import* there (`ImportError: DLL load failed`), independent of
which Python version you're running. PyQt5 supports Windows 7 and Python
3.8+. The core emulator (`jvm/`, `midp/`, `emulator/`) has no PyQt
dependency at all and runs fine via the CLI on anything pygame supports.

You can also run headlessly from the command line:

```bash
python -m emulator.runner path/to/game.jar --preset qvga-240x320 --scale 2
```

## Controls

Fixed for every loaded MIDlet:

| Key | J2ME control |
|---|---|
| `Q` | Left soft key |
| `W` | Right soft key |
| `Space` | Fire / center key |
| Arrow keys | D-pad (Up/Down/Left/Right) |
| `0`-`9` | Numeric keypad |
| `*` | Star key |
| `/` | Pound / `#` key |

Physical key codes follow the Nokia Series 40 convention
(`UP=-1, DOWN=-2, LEFT=-3, RIGHT=-4, FIRE=-5, SOFT1=-6, SOFT2=-7`,
digits/`*`/`#` as their ASCII values), which is what the overwhelming
majority of real-world MIDlet jars were written and hard-coded against.
`Canvas.getGameAction(keyCode)` also works correctly for portable games
that use it instead.

## Testing it

`examples/` contains a small hand-written MIDlet (`TestGame.java` +
minimal MIDP API stub sources it compiles against) used to validate the
interpreter against **real `javac`-compiled bytecode** — see
`examples/TestGame.jar`. It exercises arithmetic, arrays, string
concatenation, exceptions, a `Canvas` with `paint`/`keyPressed`, and a
background game-loop `Thread`. Try it:

```bash
python -m emulator.runner examples/TestGame.jar --width 176 --height 208
```
Press arrow keys and Space; you should see the on-screen `x=`/`score=`
counters change.

To load your own MIDlet, point **Open JAR…** at any `.jar` that contains
compiled classes extending `javax.microedition.midlet.MIDlet`. Only load
JARs you have the legal right to use.

## What's implemented

- Full `.class` file parsing (constant pool, fields, methods, `Code` +
  exception tables).
- The full standard JVM instruction set used by CLDC-profile bytecode:
  all arithmetic/conversion/comparison ops, stack manipulation, control
  flow (`if*`, `goto`, `tableswitch`/`lookupswitch`), arrays, object
  creation, `getfield`/`putfield`/`getstatic`/`putstatic`, and virtual /
  special / static / interface method dispatch across a class hierarchy
  that mixes interpreted user classes with the native API. `try`/`catch`
  works, including catching exceptions the interpreter itself raises
  (`NullPointerException`, `ArrayIndexOutOfBoundsException`,
  `ArithmeticException`, `ClassCastException`, ...).
- `java.lang`: `Object`, `String`, `StringBuffer`/`StringBuilder`,
  `System` (incl. `arraycopy`, `currentTimeMillis`), `Math`, `Thread`
  (backed by real Python threads — this is how the classic
  "spawn a Thread that loops repaint()+sleep()" MIDlet game pattern
  works), `Integer`/`Long`/`Float`/`Double`/`Character` parsing helpers.
- `java.util`: `Vector`, `Hashtable`, `Random`.
- `javax.microedition.midlet.MIDlet` lifecycle (`startApp`/`pauseApp`/
  `destroyApp`, `notifyDestroyed`).
- `javax.microedition.lcdui`: `Display`, `Canvas` + `GameCanvas`
  (including `getKeyStates()`), `Graphics` (lines, rects, round rects,
  arcs, triangles, text with real anchor semantics, clipping,
  translation), `Image` (blank/mutable images, loading PNG/GIF/JPEG
  resources straight out of the JAR, sub-image regions with the standard
  `Sprite` mirror/rotate transforms), `Font`, `Command`.
- Class-file inspection (browse every class/method) and a sprite viewer
  that extracts and previews every image resource in a JAR, all in the
  PyQt5 panel, independent of actually running the game.

## Known limitations (read before filing something as "broken")

This is a real interpreter, not a mock — but a production JVM + full MIDP
stack is a multi-year undertaking. Specific gaps:

- **`invokedynamic` is not supported.** This only matters if a jar was
  compiled with a *modern* JDK (Java 9+) using default settings, because
  that generates `invokedynamic`-based string concatenation. Real J2ME
  jars from the MIDP era never contain this (compile with
  `javac --release 8` or older if you're building your own test jars).
- **`Form`/`List`/`Alert`/`TextBox`** (MIDP's non-`Canvas` UI screens) are
  tracked but not visually rendered — only `Canvas`/`GameCanvas`-based
  screens draw anything. The large majority of actual *games* (as opposed
  to menu-heavy business apps) render everything themselves via `Canvas`,
  so this mainly affects options/menu screens in some titles.
- **`repaint()` runs synchronously** rather than being queued and
  coalesced like the real asynchronous MIDP repaint manager. Visually
  indistinguishable for the vast majority of games; can matter for very
  timing-sensitive effects.
- **No true JVM memory model / class file verification.** Threading
  relies on Python's GIL for safety rather than a modeled `monitorenter`/
  `monitorexit`; those opcodes are accepted but are no-ops. Fine for the
  single-background-thread game loop pattern nearly every MIDlet uses;
  could show races in more exotic multi-threaded code.
- **`if_acmpeq`/`if_acmpne`** (reference equality, `==`) fall back to
  Python's `==` when comparing two non-null values. For plain `String`s
  (which this emulator represents directly as Python `str`, not boxed
  objects) that means `s1 == s2` behaves like `.equals()` rather than true
  reference identity — a deliberate compatibility shim, since it's a
  well-known source of (often unintentional-but-relied-upon) behavior in
  real-world J2ME code, and it only affects `String`, not other objects.
- Reflection, serialization, networking (`javax.microedition.io.*`),
  `RecordStore` persistence, sound/media (`javax.microedition.media`),
  and MIDP 3.0-only APIs are not implemented. Calls to unimplemented
  native methods are logged to the console (rather than crashing the
  emulator) so you can see exactly what a given jar needs next.

## Extending it

Almost everything phone-API-related lives in `midp/natives.py` as a flat
registration list (`self.m(class_name, method_name, descriptor, fn)`).
Adding support for another API method/class is usually just adding one
more line there — no interpreter changes required unless it's genuinely
missing bytecode instruction, which is unlikely since the instruction set
implemented in `jvm/interpreter.py` is essentially complete for the
CLDC bytecode profile (everything except `invokedynamic`).

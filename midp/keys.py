"""
Keyboard -> J2ME (MIDP Canvas) key code mapping.

MIDP guarantees the digit/star/pound key codes are their ASCII values
('0'-'9' -> 48-57, '*' -> 42, '#' -> 35). Navigation/fire/soft keys are
device-specific in the real spec (only reachable in portable code via
Canvas.getGameAction()), but the vast majority of real-world J2ME jars
were written and tested against Nokia Series 40 handsets and hard-code
Nokia's negative key-code convention. We use that same convention so
existing game jars behave correctly:

    UP = -1   DOWN = -2   LEFT = -3   RIGHT = -4   FIRE = -5
    SOFT_LEFT (Q) = -6      SOFT_RIGHT (W) = -7

Requested control scheme:
    Q          -> Left soft key   (KEY_SOFT_LEFT,  -6)
    W          -> Right soft key  (KEY_SOFT_RIGHT, -7)
    Space      -> Fire / center   (KEY_FIRE,       -5)
    Arrow keys -> D-pad           (UP/DOWN/LEFT/RIGHT)
    0-9        -> numeric keypad  (48-57, ASCII)
    *          -> star key        (42, ASCII)
    /          -> pound/hash key  (35, ASCII)
"""
import pygame

KEY_UP = -1
KEY_DOWN = -2
KEY_LEFT = -3
KEY_RIGHT = -4
KEY_FIRE = -5
KEY_SOFT_LEFT = -6
KEY_SOFT_RIGHT = -7
KEY_STAR = 42
KEY_POUND = 35

# javax.microedition.lcdui.Canvas game-action constants (real MIDP values)
GAME_UP = 1
GAME_LEFT = 2
GAME_RIGHT = 5
GAME_DOWN = 6
GAME_FIRE = 8
GAME_A = 9
GAME_B = 10
GAME_C = 11
GAME_D = 12

_GAME_ACTION_FOR_KEYCODE = {
    KEY_UP: GAME_UP, KEY_DOWN: GAME_DOWN, KEY_LEFT: GAME_LEFT, KEY_RIGHT: GAME_RIGHT,
    KEY_FIRE: GAME_FIRE,
}


def game_action_for(keycode: int) -> int:
    return _GAME_ACTION_FOR_KEYCODE.get(keycode, 0)


# pygame key constant -> MIDP key code
PYGAME_TO_MIDP = {
    pygame.K_q: KEY_SOFT_LEFT,
    pygame.K_w: KEY_SOFT_RIGHT,
    pygame.K_SPACE: KEY_FIRE,
    pygame.K_UP: KEY_UP,
    pygame.K_DOWN: KEY_DOWN,
    pygame.K_LEFT: KEY_LEFT,
    pygame.K_RIGHT: KEY_RIGHT,
    pygame.K_ASTERISK: KEY_STAR,
    pygame.K_KP_MULTIPLY: KEY_STAR,
    pygame.K_SLASH: KEY_POUND,
    pygame.K_KP_DIVIDE: KEY_POUND,
    pygame.K_0: 48, pygame.K_1: 49, pygame.K_2: 50, pygame.K_3: 51, pygame.K_4: 52,
    pygame.K_5: 53, pygame.K_6: 54, pygame.K_7: 55, pygame.K_8: 56, pygame.K_9: 57,
    pygame.K_KP0: 48, pygame.K_KP1: 49, pygame.K_KP2: 50, pygame.K_KP3: 51, pygame.K_KP4: 52,
    pygame.K_KP5: 53, pygame.K_KP6: 54, pygame.K_KP7: 55, pygame.K_KP8: 56, pygame.K_KP9: 57,
}

# human-readable labels, e.g. for an on-screen keypad in the PyQt5 tooling
LABELS = {
    KEY_UP: "UP", KEY_DOWN: "DOWN", KEY_LEFT: "LEFT", KEY_RIGHT: "RIGHT",
    KEY_FIRE: "FIRE (Space)", KEY_SOFT_LEFT: "LSK (Q)", KEY_SOFT_RIGHT: "RSK (W)",
    KEY_STAR: "* ", KEY_POUND: "#",
    48: "0", 49: "1", 50: "2", 51: "3", 52: "4", 53: "5", 54: "6", 55: "7", 56: "8", 57: "9",
}


def midp_keycode_for_pygame_key(pg_key) -> int:
    return PYGAME_TO_MIDP.get(pg_key)

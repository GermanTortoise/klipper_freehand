import math
import time
import os
import json
import keyboard_control

class MockReactor:
    NOW = 0.0
    NEVER = math.inf

    def monotonic(self):
        return time.monotonic()

    def register_timer(self, callback, waketime=None):
        return callback

    def update_timer(self, timer_handler, waketime):
        if timer_handler is None or waketime != self.NOW:
            return

        next_waketime = time.monotonic()

        while True:
            now = time.monotonic()
            if next_waketime > now:
                time.sleep(min(next_waketime - now, 0.01))
                continue

            next_waketime = timer_handler(now)
            if next_waketime == self.NEVER:
                break


class MockConfig:
    def __init__(self) -> None:
        self.values = {
            'framerate': 60,
            'max_queue_time': 0.05,
            'layer_height': 0.2,
            'line_width': 0.6,
            'x_min': 10,
            'x_max': 200,
            'y_min': 10,
            'y_max': 200,
            'z_min': 0,
            'z_max': 180,
            'speed': 20,
            'acceleration': 3000,
            'toggle_shift': False,
            'reconcile_gain': 0.15,
            'reconcile_max_step': 1.5,
            'reconcile_deadband': 0.05,
            'reconcile_to_toolhead': False,
            'layer_fade': 0.55,
            'line_px': 3.0,
            'play_keys_file': None,
            'record_keys_file': None,
        }
        self._printer = MockPrinter()
    
    def getint(self, key: str, default=None, **kwargs) -> int:
        if key not in self.values:
            if default is None:
                raise KeyError(key)
            return int(default)
        return int(self.values[key])
    
    def getfloat(self, key: str, default=None, **kwargs) -> float:
        if key not in self.values:
            if default is None:
                raise KeyError(key)
            return float(default)
        return float(self.values[key])

    def getboolean(self, key: str, default=False, **kwargs) -> bool:
        if key not in self.values:
            return bool(default)
        return bool(self.values[key])

    def get(self, key: str, default=None):
        return self.values.get(key, default)
    
    def get_printer(self):
        return self._printer

class MockPrinter:
    def __init__(self):
        self._gcode = MockGcode()
        self._toolhead = MockToolhead()
        self._mcu = MockMcu(self._toolhead)
        self._reactor = MockReactor()
        self._event_handlers = {}

    def lookup_object(self, name: str):
        if name == 'gcode':
            return self._gcode
        if name == 'toolhead':
            return self._toolhead
        if name == 'mcu':
            return self._mcu
        raise KeyError(name)

    def get_reactor(self):
        return self._reactor

    def register_event_handler(self, event: str, callback):
        self._event_handlers.setdefault(event, []).append(callback)
        # Mimic Klipper boot sequence for tests: fire ready immediately.
        if event == "klippy:connect":
            callback()
        if event == "klippy:ready":
            callback()


class MockToolhead:
    def __init__(self):
        self._pos = [0.0, 0.0, 0.0, 0.0]
        self._last_move_time = time.monotonic()

    def manual_move(self, coord, speed):
        for i, val in enumerate(coord):
            if val is not None:
                self._pos[i] = float(val)
        now = time.monotonic()
        self._last_move_time = max(self._last_move_time, now + 0.02)

    def get_position(self):
        return list(self._pos)

    def get_last_move_time(self):
        return self._last_move_time


class MockMcu:
    def __init__(self, toolhead: MockToolhead):
        self._toolhead = toolhead

    def estimated_print_time(self, now):
        return now

class MockGcode:
    def register_command(self, command: str, callback, desc: str = ''):
        pass

    def run_script_from_command(self, command: str):
        print(command)

    def respond_info(self, message: str):
        print(f"gcode message: {message}")
    
class MockGcmd:
    def __init__(self, params=None):
        self.responses = []
        self.params = params or {}

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.params.get(key, default))

    def get(self, key: str, default=None):
        return self.params.get(key, default)

    def respond_info(self, message: str) -> None:
        self.responses.append(message)
        print(f"gcmd message: {message}")
        

if __name__ == "__main__":
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    keypress_dir = os.path.join(root_dir, 'keyboard_control_keypresses')
    os.makedirs(keypress_dir, exist_ok=True)
    playback_path = os.path.join(keypress_dir, 'Test.json')
    with open(playback_path, 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'framerate': 60,
                   'keys': ['w'] * 5 + ['a'] * 5 + ['s'] * 5 + ['d'] * 5 + ['q']},
                  f, indent=2)

    e = keyboard_control.load_config(MockConfig())
    # Use playback input so the test run exits automatically.
    e.cmd_ETCH_START(MockGcmd({}))

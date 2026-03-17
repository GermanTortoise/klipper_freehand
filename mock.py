import math
import time
import keyboard_control

class MockReactor:
    NOW = 0.0
    NEVER = math.inf

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
            'layer_height': 0.2,
            'line_width': 0.6,
            'x_min': 10,
            'x_max': 200,
            'y_min': 10,
            'y_max': 200,
            'z_min': 0,
            'z_max': 180,
            'speed': 100,
            'acceleration': 3000,
        }
    
    def getint(self, key: str) -> int:
        return int(self.values[key])
    
    def getfloat(self, key: str) -> float:
        return float(self.values[key])
    
    def get_printer(self):
        return MockPrinter()

class MockPrinter:
    def __init__(self):
        self._gcode = MockGcode()
        self._reactor = MockReactor()
        self._event_handlers = {}

    def lookup_object(self, name: str):
        return self._gcode

    def get_reactor(self):
        return self._reactor

    def register_event_handler(self, event: str, callback):
        self._event_handlers.setdefault(event, []).append(callback)
        # Mimic Klipper boot sequence for tests: fire ready immediately.
        if event == "klippy:ready":
            callback()

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

    def respond_info(self, message: str) -> None:
        self.responses.append(message)
        print(f"gcmd message: {message}")
        

if __name__ == "__main__":
    e = keyboard_control.load_config(MockConfig())
    e.cmd_ETCH_START(MockGcmd({"MOCK": 0}))

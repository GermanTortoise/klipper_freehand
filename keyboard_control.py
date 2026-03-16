import pygame
from typing import NamedTuple
import vector
import math

"""
[keyboard_control]
framerate: 60
layer_height: 0.2
line_width: 0.6
x_min: 10
x_max: 200
y_min: 10
y_max: 200
z_min: 0
z_max: 180
speed: 100
acceleration: 3000

can you read other config sections?
yes with config.getsection

mode where lines can't overlap already printed lines and instead are snapped to the edge of them
can only cross when not extruding
on new layer, can only extrude when on top of prev by at least a certain overhang %
"""

# copied from a gcode file
startup_command = """
; EXECUTABLE_BLOCK_START
M73 P0 R6
;TYPE:Custom
G90 ; use absolute coordinates
M83 ; extruder relative mode
M204 S5000 T5000
M104 S230 ; set extruder temp
M140 S50 ; set bed temp
G28 ; home all
M190 S50 ; wait for bed temp
G1 Z1.24
"""

class G1(NamedTuple):
    x: float | None
    y: float | None
    z: float | None
    e: float | None

class KeyboardControl:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        self.framerate = config.getint('framerate')
        self.layer_height = config.getfloat('layer_height')
        self.line_width = config.getfloat('line_width')
        self.x_min = config.getfloat('x_min')
        self.x_max = config.getfloat('x_max')
        self.y_min = config.getfloat('y_min')
        self.y_max = config.getfloat('y_max')
        self.z_min = config.getfloat('z_min')
        self.z_max = config.getfloat('z_max')
        self.speed = config.getint('speed')
        self.accel = config.getint('acceleration')
        self.x = (self.x_max - self.x_min) / 2
        self.y = (self.y_max - self.y_min) / 2
        self.z = self.layer_height

        self.running = False
        self.clock = None
        self.screen = None
        self.mock_input = False
        self.frame_count = 0
        self.max_frames = math.inf
        self.test_keys = []
        self.etch_timer = self.reactor.register_timer(
            self._etch_step, self.reactor.NEVER)

        self.gcode.register_command('ETCH_START', self.cmd_ETCH_START,
                                    desc='start etching, MOCK=1 for mock input')
        self.gcode.register_command('ETCH_STOP', self.cmd_ETCH_STOP,
                                    desc='stop etching, can also stop by closing the window')

    def _increment_bounded(self, val: float, move: float, min: float, max: float) -> float:
        if val + move < min:
            return min
        if val + move > max:
            return max
        return val + move

    def _G1_gcode(self, move: G1) -> str:
        out = "G1"
        if move.x is not None:
            out += f" X{move.x:.3f}"
        if move.y is not None:
            out += f" Y{move.y:.3f}"
        if move.z is not None:
            out += f" Z{move.z:.3f}"
        if move.e is not None:
            out += f" E{move.e:.5f}"
        return out

    def _distance(self, x1: float, y1: float, x2: float, y2: float) -> float:
        return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5

    def _lateral_move(self, keys: str) -> G1:
        v = vector.obj(x=0, y=0)
        for char in keys:
            match char:
                case 'w':
                    v.y += 1
                case 'a':
                    v.x -= 1
                case 's':
                    v.y -= 1
                case 'd':
                    v.x += 1

        if v.rho == 0:
            return G1(self.x, self.y, None, 0.0)

        v = v.unit()
        v *= (self.speed / self.framerate)
        self.x = self._increment_bounded(self.x, v.x, self.x_min, self.x_max)
        self.y = self._increment_bounded(self.y, v.y, self.y_min, self.y_max)
        # v.rho = move distance
        # slic3r extrusion formula (___) shaped
        e_length = v.rho * (
            math.pi * (self.layer_height / 2) ** 2
            + (self.line_width - self.layer_height) * self.layer_height
        )
        return G1(self.x, self.y, None, e_length)

    def _vertical_move(self) -> G1:
        self.z += self.layer_height
        return G1(self.x, self.y, self.z, None)

    def _cleanup_etch(self):
        if self.screen is not None:
            pygame.quit()
            self.screen = None
            self.clock = None

    def _read_keys(self) -> str:
        if self.mock_input:
            if self.frame_count < len(self.test_keys):
                return self.test_keys[self.frame_count]
            return ""

        keys = pygame.key.get_pressed()
        keys_pressed = ""
        if keys[pygame.K_q]:
            keys_pressed += 'q'
        if keys[pygame.K_w]:
            keys_pressed += 'w'
        if keys[pygame.K_a]:
            keys_pressed += 'a'
        if keys[pygame.K_s]:
            keys_pressed += 's'
        if keys[pygame.K_d]:
            keys_pressed += 'd'
        if keys[pygame.K_SPACE]:
            keys_pressed += ' '
        return keys_pressed

    def _etch_step(self, eventtime):
        if not self.running:
            self._cleanup_etch()
            self.gcode.respond_info("Finished etching")
            return self.reactor.NEVER

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                break

        if not self.running or self.frame_count >= self.max_frames:
            self.running = False
            self._cleanup_etch()
            self.gcode.respond_info("Finished etching")
            return self.reactor.NEVER

        keys_pressed = self._read_keys()
        if 'q' in keys_pressed:
            self.running = False
            self._cleanup_etch()
            self.gcode.respond_info("Finished etching")
            return self.reactor.NEVER

        if ' ' in keys_pressed:
            vert_move = self._vertical_move()
            keys_pressed = keys_pressed.replace(' ', '')
            self.gcode.run_script_from_command(self._G1_gcode(vert_move))

        lat_move = self._lateral_move(keys_pressed)
        if lat_move.e is not None and lat_move.e > 0:
            self.gcode.run_script_from_command(self._G1_gcode(lat_move))

        self.frame_count += 1
        return eventtime + (1.0 / self.framerate)

    def cmd_ETCH_START(self, gcmd):
        if self.running:
            gcmd.respond_info("Etching already running")
            return

        mock = gcmd.get_int('MOCK', 0)
        self.mock_input = mock > 0
        if self.mock_input:
            gcmd.respond_info("Started etching with mock input")
        else:
            gcmd.respond_info("Started etching!")

        pygame.init()
        self.clock = pygame.time.Clock()
        self.screen = pygame.display.set_mode((100, 100))
        pygame.display.set_caption("Keyboard Control")
        gcmd.respond_info("Finished pygame init")

        self.gcode.run_script_from_command(startup_command)
        center = self._G1_gcode(G1(self.x, self.y, None, None))
        down = self._G1_gcode(G1(None, None, self.z, None))
        self.gcode.run_script_from_command(center)
        self.gcode.run_script_from_command(down)

        # TODO: refactor this less stupidly
        self.test_keys = [
            'w', 'w', 'w', 'w', 'w',
            'a', 'a', 'a', 'a', 'a',
            's', 's', 's', 's', 's',
            'd', 'd', 'd', 'd', 'd', 'q'
        ]
        self.frame_count = 0
        self.max_frames = len(self.test_keys) if self.mock_input else math.inf
        self.running = True
        self.reactor.update_timer(self.etch_timer, self.reactor.NOW)

    def cmd_ETCH_STOP(self, gcmd):
        gcmd.respond_info("Stop etching")
        if self.running:
            self.running = False
            self.reactor.update_timer(self.etch_timer, self.reactor.NOW)
            
def load_config(config):
    return KeyboardControl(config)


class MockReactor:
    NOW = 0.0
    NEVER = math.inf

    def register_timer(self, callback, waketime=None):
        return callback

    def update_timer(self, timer_handler, waketime):
        if timer_handler is not None and waketime == self.NOW:
            timer_handler(waketime)


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
    def lookup_object(self, name: str):
        return MockGcode()

    def get_reactor(self):
        return MockReactor()

class MockGcode:
    def register_command(self, command: str, callback, desc: str = ''):
        pass

    def run_script_from_command(self, command: str):
        print(command)

    def respond_info(self, message: str):
        print(f"gcode message: {message}")
    
class MockGcmd:
    def __init__(self):
        self.responses = []
    
    def get_int(self, key: str, default: int = 0) -> int:
        """Get an integer parameter, return default if not provided"""
        return default
    
    def respond_info(self, message: str) -> None:
        """Log an info response"""
        self.responses.append(message)
        print(f"gcmd message: {message}")
    
if __name__ == "__main__":
    e = load_config(MockConfig())
    e.cmd_ETCH_START(MockGcmd())

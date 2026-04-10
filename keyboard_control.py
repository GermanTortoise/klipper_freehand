import pygame
from typing import NamedTuple
import vector
import math
import os

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
startup_gcode = """
G90 ; toolhead absolute coordinates
M83 ; extruder relative mode
M204 S5000 T5000 ; set acceleration
;M104 S230 ; set extruder temp
;M140 S50 ; set bed temp
G28 ; home all
;M190 S50 ; wait for bed temp
;M109 s230 ; wait for extruder temp
G1 Z1.24
"""

shutdown_gcode = """
;G1 E-1.0 F2100 ; retract
M104 S0 ; extruder heater off
M140 S0 ; bed heater off
M107 ; fan off
M84 ; turn off motors
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
        self.lookahead = config.getfloat('lookahead', 0.10)
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
        self.etch_timer = None
        self.space_pressed = False
        self.queue_time = 0.0
        self.last_eventtime = None
        
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown",
                            self._handle_klippy_shutdown)
        self.printer.register_event_handler("klippy:disconnect",
                            self._handle_klippy_disconnect)
        self.printer.register_event_handler("gcode:request_restart",
                            self._handle_request_restart)
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
        # if move.e is not None:
        #     out += f" E{move.e:.5f}"
        return out

    def _distance(self, x1: float, y1: float, x2: float, y2: float) -> float:
        return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5

    def _lateral_move(self, keys: str, segment_time: float) -> G1:
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
            return G1(self.x, self.y, None, None)

        v = v.unit()
        v *= (self.speed * segment_time)
        prev_x = self.x
        prev_y = self.y
        self.x = self._increment_bounded(self.x, v.x, self.x_min, self.x_max)
        self.y = self._increment_bounded(self.y, v.y, self.y_min, self.y_max)
        moved_dist = self._distance(prev_x, prev_y, self.x, self.y)
        if moved_dist <= 0:
            return G1(self.x, self.y, None, None)
        # v.rho = move distance
        # slic3r extrusion formula (___) shaped
        e_length = moved_dist * (
            math.pi * (self.layer_height / 2) ** 2
            + (self.line_width - self.layer_height) * self.layer_height
        )
        return G1(self.x, self.y, None, e_length)

    def _vertical_move(self) -> G1:
        self.z += self.layer_height
        return G1(self.x, self.y, self.z, None)

    def _stop_etch(self):
        self.running = False
        if self.screen is not None:
            try:
                pygame.display.quit()
            except Exception:
                pass
        try:
            pygame.quit()
        except Exception:
            pass
        self.screen = None
        self.clock = None
        self.queue_time = 0.0
        self.last_eventtime = None

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
    
    def _handle_ready(self):
        self.etch_timer = self.reactor.register_timer(self._etch_step, self.reactor.NEVER)

    def _handle_request_restart(self, _print_time):
        if self.running:
            self.gcode.run_script_from_command(shutdown_gcode)
        self._stop_etch()

    def _handle_klippy_shutdown(self):
        self._stop_etch()

    def _handle_klippy_disconnect(self):
        self._stop_etch()
        
    def _etch_step(self, eventtime):
        if not self.running:
            # stopped by ETCH_STOP at previous frame
            self._stop_etch()
            return self.reactor.NEVER

        if self.last_eventtime is None:
            dt = 0.0
        else:
            dt = max(0.0, eventtime - self.last_eventtime)
        self.last_eventtime = eventtime
        self.queue_time = max(0.0, self.queue_time - dt)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                # stopped by closing Pygame window
                self._stop_etch()
                return self.reactor.NEVER

        if self.frame_count >= self.max_frames:
            # stopped by mock finishing
            self._stop_etch()
            return self.reactor.NEVER

        keys_pressed = self._read_keys()
        if 'q' in keys_pressed:
            # stopped by pressing q
            self._stop_etch()
            return self.reactor.NEVER

        if ' ' in keys_pressed:
            if not self.space_pressed:
                self.space_pressed = True
                vert_move = self._vertical_move()
                # keys_pressed = keys_pressed.replace(' ', '')
                self.gcode.run_script_from_command(self._G1_gcode(vert_move))
                self.queue_time += (1.0 / self.framerate)
        else:
            self.space_pressed = False

        segment_time = 1.0 / self.framerate
        has_lateral_input = any(k in keys_pressed for k in 'wasd')
        while has_lateral_input and self.queue_time < self.lookahead:
            lat_move = self._lateral_move(keys_pressed, segment_time)
            if lat_move.e is None or lat_move.e <= 0:
                break
            self.gcode.run_script_from_command(self._G1_gcode(lat_move))
            self.queue_time += segment_time

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

        self.gcode.run_script_from_command(startup_gcode)
        self.gcode.run_script_from_command(f"G1 F{self.speed * 60.0:.1f}")
        center = self._G1_gcode(G1(self.x, self.y, None, None))
        down = self._G1_gcode(G1(None, None, self.z, None))
        self.gcode.run_script_from_command(center)
        self.gcode.run_script_from_command(down)

        gcmd.respond_info("Starting pygame init")
        # gcmd.respond_info(f"Host={socket.gethostname()} PID={os.getpid()}")
        # gcmd.respond_info(f"SSH_CONNECTION={os.environ.get('SSH_CONNECTION')}")
        # gcmd.respond_info(f"DISPLAY={os.environ.get('DISPLAY')}")
        # gcmd.respond_info(f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY')}")
        # gcmd.respond_info(f"XDG_RUNTIME_DIR={os.environ.get('XDG_RUNTIME_DIR')}")
        # gcmd.respond_info(f"SDL_VIDEODRIVER env={os.environ.get('SDL_VIDEODRIVER')}")
        pygame.init()
        self.clock = pygame.time.Clock()
        self.screen = pygame.display.set_mode((500, 500))
        pygame.display.set_caption("Keyboard Control")
        # gcmd.respond_info(f"pygame display driver={pygame.display.get_driver()}")
        # gcmd.respond_info(f"wm_info={pygame.display.get_wm_info()}")
        gcmd.respond_info("Finished pygame init")
        
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
        self.queue_time = 0.0
        self.last_eventtime = None
        if self.etch_timer is not None:
            self.reactor.update_timer(self.etch_timer, self.reactor.NOW)

    def cmd_ETCH_STOP(self, gcmd):
        gcmd.respond_info("Stop etching")
        if self.etch_timer is not None:
            self.reactor.update_timer(self.etch_timer, self.reactor.NOW)
        self._stop_etch()
        if self.running:
            self.gcode.run_script_from_command(shutdown_gcode)
            
def load_config(config):
    return KeyboardControl(config)

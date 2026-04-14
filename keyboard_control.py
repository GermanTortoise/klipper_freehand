import pygame
from typing import NamedTuple
import vector
import math
import json
import os
import time
import logging
from collections import deque
try:
    import chelper
except Exception:
    chelper = None

"""
[keyboard_control]
framerate: 60
max_queue_time: 0.05
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
toggle_shift: False
reconcile_to_toolhead: False
reconcile_gain: 0.15
reconcile_max_step: 1.5
reconcile_deadband: 0.05
layer_fade: 0.55
line_px: 3

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


class SimSegment(NamedTuple):
    start_time: float
    end_time: float
    start_pos: tuple[float, float, float]
    end_pos: tuple[float, float, float]
    extruding: bool

class KeyboardControl:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.mcu = self.printer.lookup_object('mcu')
        self.reactor = self.printer.get_reactor()
        self.framerate = config.getint('framerate')
        self.max_queue_time = config.getfloat('max_queue_time', 0.05, above=0.)
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
        # Local simulated position of toolhead and pending simulated movement.
        self.sim_x = self.x
        self.sim_y = self.y
        self.sim_z = self.z
        self._sim_segments = deque()
        self.reconcile_gain = config.getfloat('reconcile_gain', 0.15,
                              minval=0., maxval=1.)
        self.reconcile_max_step = config.getfloat('reconcile_max_step', 1.5,
                              above=0.)
        self.reconcile_deadband = config.getfloat('reconcile_deadband', 0.05,
                              minval=0.)
        self.toggle_shift = config.getboolean('toggle_shift', False)
        self._shift_down_last = False
        self._shift_extrude_latched = False
        self._recording_enabled = False
        self._recording_path = None
        self._recorded_keys: list[str] = []
        self.reconcile_to_toolhead = config.getboolean(
            'reconcile_to_toolhead', False)
        # Layer visualization state.
        self._printed_layers: list[list[tuple[float, float, float, float]]] = []
        self._active_layer_index = 0
        self._last_draw_pos = (self.x, self.y, self.z)
        self._layer_fade = config.getfloat('layer_fade', 0.55,
                           minval=0., maxval=1.)
        self._line_px = max(1, int(config.getfloat('line_px', 3.0, above=0.)))
        
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:connect", self._handle_connect)
        self.printer.register_event_handler("klippy:shutdown",
                            self._handle_klippy_shutdown)
        self.printer.register_event_handler("klippy:disconnect",
                            self._handle_klippy_disconnect)
        self.printer.register_event_handler("gcode:request_restart",
                            self._handle_request_restart)
        self.gcode.register_command('ETCH_START', self.cmd_ETCH_START,
                                    desc='start etching; use PLAY=<name> for playback input')
        self.gcode.register_command('ETCH_STOP', self.cmd_ETCH_STOP,
                                    desc='stop etching, can also stop by closing the window')
        self.gcode.register_command('ETCH_LIST',
                        self.cmd_ETCH_LIST_PLAYBACKS,
                        desc='list the 10 most recent playback files')
        
    def _handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')

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

    def _lateral_move(self, keys: str) -> G1:
        prev_x = self.x
        prev_y = self.y
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
        v *= (self.speed / self.framerate)
        self.x = self._increment_bounded(self.x, v.x, self.x_min, self.x_max)
        self.y = self._increment_bounded(self.y, v.y, self.y_min, self.y_max)
        move_dist = self._distance(prev_x, prev_y, self.x, self.y)
        if move_dist <= 1e-9:
            return G1(self.x, self.y, None, None)
        # v.rho = move distance
        # slic3r extrusion formula (___) shaped
        e_length = move_dist * (
            math.pi * (self.layer_height / 2) ** 2
            + (self.line_width - self.layer_height) * self.layer_height
        )
        return G1(self.x, self.y, None, e_length)

    def _vertical_move(self) -> G1:
        self.z += self.layer_height
        return G1(self.x, self.y, self.z, None)

    def _move_duration(self, start: tuple[float, float, float],
                       end: tuple[float, float, float]) -> float:
        dist = self._distance(start[0], start[1], end[0], end[1])
        if abs(end[2] - start[2]) > 0.:
            dist = max(dist, abs(end[2] - start[2]))
        if self.speed <= 0:
            return 0.
        return dist / float(self.speed)

    def _enqueue_sim_segment(self, end_pos: tuple[float, float, float],
                             extruding: bool):
        now = self.reactor.monotonic()
        if self._sim_segments:
            start_time = self._sim_segments[-1].end_time
            start_pos = self._sim_segments[-1].end_pos
        else:
            start_time = now + max(0., self._queue_ahead_time())
            start_pos = (self.sim_x, self.sim_y, self.sim_z)
        dur = self._move_duration(start_pos, end_pos)
        end_time = start_time + max(0., dur)
        self._sim_segments.append(SimSegment(start_time, end_time,
                                             start_pos, end_pos, extruding))

    def _advance_simulation(self, now: float):
        # Return XY segments traversed during this tick with an extrusion flag.
        traversed = []
        prev_pos = (self.sim_x, self.sim_y, self.sim_z)
        while self._sim_segments and now >= self._sim_segments[0].end_time:
            seg = self._sim_segments.popleft()
            self.sim_x, self.sim_y, self.sim_z = seg.end_pos
            cur_pos = (self.sim_x, self.sim_y, self.sim_z)
            if self._distance(prev_pos[0], prev_pos[1], cur_pos[0], cur_pos[1]) > 1e-9:
                traversed.append((prev_pos, cur_pos, seg.extruding))
            prev_pos = cur_pos
        if not self._sim_segments:
            return traversed
        seg = self._sim_segments[0]
        if seg.end_time <= seg.start_time:
            self.sim_x, self.sim_y, self.sim_z = seg.end_pos
            cur_pos = (self.sim_x, self.sim_y, self.sim_z)
            if self._distance(prev_pos[0], prev_pos[1], cur_pos[0], cur_pos[1]) > 1e-9:
                traversed.append((prev_pos, cur_pos, seg.extruding))
            return traversed
        t = min(1., max(0., (now - seg.start_time) / (seg.end_time - seg.start_time)))
        self.sim_x = seg.start_pos[0] + (seg.end_pos[0] - seg.start_pos[0]) * t
        self.sim_y = seg.start_pos[1] + (seg.end_pos[1] - seg.start_pos[1]) * t
        self.sim_z = seg.start_pos[2] + (seg.end_pos[2] - seg.start_pos[2]) * t
        cur_pos = (self.sim_x, self.sim_y, self.sim_z)
        if self._distance(prev_pos[0], prev_pos[1], cur_pos[0], cur_pos[1]) > 1e-9:
            traversed.append((prev_pos, cur_pos, seg.extruding))
        return traversed

    def _reconcile_with_toolhead(self):
        # Toolhead position is the best available authority from python side.
        pos = self.toolhead.get_position()
        dx = pos[0] - self.sim_x
        dy = pos[1] - self.sim_y
        dz = pos[2] - self.sim_z
        err = math.sqrt(dx*dx + dy*dy + dz*dz)
        if err <= self.reconcile_deadband:
            return
        step = min(self.reconcile_max_step, err * self.reconcile_gain)
        if err <= 1e-12:
            return
        s = step / err
        self.sim_x += dx * s
        self.sim_y += dy * s
        self.sim_z += dz * s

    def _current_layer_index(self) -> int:
        idx = int(round((self.sim_z - self.layer_height) / self.layer_height))
        return max(0, idx)

    def _ensure_layer_index(self, idx: int):
        while len(self._printed_layers) <= idx:
            self._printed_layers.append([])

    def _record_extruded_segment(self, x1: float, y1: float, x2: float, y2: float):
        idx = self._active_layer_index
        self._ensure_layer_index(idx)
        self._printed_layers[idx].append((x1, y1, x2, y2))

    def _world_to_screen(self, x: float, y: float) -> tuple[int, int]:
        width = max(1, self.x_max - self.x_min)
        height = max(1, self.y_max - self.y_min)
        sx = int(((x - self.x_min) / width) * 500)
        sy = 500 - int(((y - self.y_min) / height) * 500)
        return sx, sy

    def _draw_layer(self, segments, color):
        if self.screen is None:
            return
        for x1, y1, x2, y2 in segments:
            p1 = self._world_to_screen(x1, y1)
            p2 = self._world_to_screen(x2, y2)
            pygame.draw.line(self.screen, color, p1, p2, self._line_px)

    def _get_actual_toolhead_position(self) -> tuple[float, float, float]:
        # Prefer trapq-sampled execution position at current MCU print time.
        try:
            if chelper is None:
                raise RuntimeError("chelper unavailable")
            trapq = self.toolhead.get_trapq()
            now = self.reactor.monotonic()
            print_time = self.mcu.estimated_print_time(now)
            ffi_main, ffi_lib = chelper.get_ffi()
            if ffi_main is None or ffi_lib is None:
                raise RuntimeError("ffi unavailable")
            trapq_extract_old = getattr(ffi_lib, 'trapq_extract_old', None)
            if trapq_extract_old is None:
                raise RuntimeError("trapq_extract_old unavailable")
            data = ffi_main.new('struct pull_move[1]')
            count = trapq_extract_old(trapq, data, 1, 0., print_time)
            if count:
                move = data[0]
                move_time = max(0., min(move.move_t, print_time - move.print_time))
                dist = (move.start_v + .5 * move.accel * move_time) * move_time
                return (move.start_x + move.x_r * dist,
                        move.start_y + move.y_r * dist,
                        move.start_z + move.z_r * dist)
        except Exception:
            pass
        pos = self.toolhead.get_position()
        return (pos[0], pos[1], pos[2])

    def _draw_scene(self):
        if self.screen is None:
            return
        self.screen.fill((16, 18, 22))
        cur_idx = self._active_layer_index
        for idx, layer in enumerate(self._printed_layers):
            if idx > cur_idx:
                continue
            depth = cur_idx - idx
            if depth == 0:
                color = (245, 199, 66)
            else:
                fade = self._layer_fade ** depth
                c = max(20, min(255, int(175 * fade)))
                color = (c, c, c)
            self._draw_layer(layer, color)
        sim_xy = self._world_to_screen(self.sim_x, self.sim_y)
        pygame.draw.circle(self.screen, (80, 220, 170), sim_xy, 4)
        pos = self._get_actual_toolhead_position()
        actual_xy = self._world_to_screen(pos[0], pos[1])
        pygame.draw.circle(self.screen, (220, 90, 90), actual_xy, 3)
        pygame.display.flip()

    def _queue_ahead_time(self) -> float:
        now = self.reactor.monotonic()
        return self.toolhead.get_last_move_time() - self.mcu.estimated_print_time(now)

    def _get_keypress_dir(self) -> str:
        # Save all recordings/playback files under repository root.
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        keypress_dir = os.path.join(root_dir, 'keyboard_control_keypresses')
        os.makedirs(keypress_dir, exist_ok=True)
        return keypress_dir

    def _resolve_keypress_path(self, file_name: str | None, for_write: bool) -> str:
        if file_name:
            base = os.path.basename(str(file_name).strip())
            if not base:
                raise ValueError("Empty filename")
            # Accept names with or without ".json" and normalize to ".json".
            if base.lower().endswith('.json'):
                base = base[:-5]
            safe_base = ''.join(c if c.isalnum() or c in ('-', '_') else '_'
                                for c in base)
            if not safe_base:
                raise ValueError("Invalid filename")
            safe = safe_base + '.json'
        elif for_write:
            safe = time.strftime('keys_%Y%m%d_%H%M%S.json')
        else:
            raise ValueError("Playback filename is required")
        return os.path.join(self._get_keypress_dir(), safe)

    def _load_keypress_file(self, file_name: str) -> list[str]:
        path = self._resolve_keypress_path(file_name, for_write=False)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(v) for v in data]
        if isinstance(data, dict):
            keys = data.get('keys', [])
            if not isinstance(keys, list):
                raise ValueError("'keys' must be a list")
            return [str(v) for v in keys]
        raise ValueError("Invalid keypress file format")

    def _save_keypress_file(self):
        if not self._recording_enabled:
            return
        try:
            path = self._resolve_keypress_path(self._recording_path,
                                               for_write=True)
            payload = {
                'version': 1,
                'framerate': self.framerate,
                'keys': self._recorded_keys,
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
            self.gcode.respond_info(
                "Saved keypress recording: %s (%d frames)"
                % (path, len(self._recorded_keys)))
        except Exception:
            logging.exception("Failed to save keypress recording")
        finally:
            self._recording_enabled = False
            self._recording_path = None
            self._recorded_keys = []

    def _stop_etch(self):
        self._save_keypress_file()
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
        self._sim_segments.clear()

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
        if keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]:
            keys_pressed += '^'
        return keys_pressed

    def _extrude_enabled(self, keys_pressed: str) -> bool:
        shift_down = '^' in keys_pressed
        if not self.toggle_shift:
            return shift_down
        # Toggle latch on shift key-down edge.
        if shift_down and not self._shift_down_last:
            self._shift_extrude_latched = not self._shift_extrude_latched
        self._shift_down_last = shift_down
        return self._shift_extrude_latched
    
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
        extrude_enabled = self._extrude_enabled(keys_pressed)
        if 'q' in keys_pressed:
            # stopped by pressing q
            self._stop_etch()
            return self.reactor.NEVER

        if self._queue_ahead_time() > self.max_queue_time:
            traversed = self._advance_simulation(eventtime)
            if self.reconcile_to_toolhead:
                self._reconcile_with_toolhead()
            self._active_layer_index = self._current_layer_index()
            for seg_start, seg_end, seg_extruding in traversed:
                if seg_extruding:
                    self._record_extruded_segment(
                        seg_start[0], seg_start[1], seg_end[0], seg_end[1])
            self._draw_scene()
            self.frame_count += 1
            return eventtime + (1.0 / self.framerate)

        moved_this_frame = False
        if ' ' in keys_pressed:
            if not self.space_pressed:
                self.space_pressed = True
                vert_move = self._vertical_move()
                self.toolhead.manual_move([None, None, vert_move.z], self.speed)
                if vert_move.z is not None:
                    self._enqueue_sim_segment((self.x, self.y, vert_move.z),
                                              extruding=False)
                    moved_this_frame = True
        else:
            self.space_pressed = False

        lat_move = self._lateral_move(keys_pressed)
        move_is_xy = lat_move.e is not None and lat_move.e > 0
        extrude_now = move_is_xy and extrude_enabled
        if move_is_xy:
            self.toolhead.manual_move([lat_move.x, lat_move.y, None], self.speed)
            if lat_move.x is not None and lat_move.y is not None:
                self._enqueue_sim_segment((lat_move.x, lat_move.y, self.z),
                                          extruding=extrude_now)
                moved_this_frame = True

        if self._recording_enabled and moved_this_frame:
            self._recorded_keys.append(keys_pressed)

        traversed = self._advance_simulation(eventtime)
        if self.reconcile_to_toolhead:
            self._reconcile_with_toolhead()
        self._active_layer_index = self._current_layer_index()
        for seg_start, seg_end, seg_extruding in traversed:
            if seg_extruding:
                self._record_extruded_segment(
                    seg_start[0], seg_start[1], seg_end[0], seg_end[1])
        self._draw_scene()

        self.frame_count += 1
        return eventtime + (1.0 / self.framerate)

    def cmd_ETCH_START(self, gcmd):
        if self.running:
            gcmd.respond_info("Etching already running")
            return

        play_file = gcmd.get('PLAY', None)
        record_file = gcmd.get('RECORD', None)
        self._recording_enabled = bool(record_file)
        self._recording_path = record_file
        self._recorded_keys = []

        self.mock_input = False
        loaded_play_keys = None
        if play_file:
            try:
                loaded_play_keys = self._load_keypress_file(play_file)
                self.mock_input = True
                play_path = self._resolve_keypress_path(play_file,
                                                        for_write=False)
                gcmd.respond_info(
                    "Loaded keypress playback: %s (%d frames)"
                    % (play_path, len(loaded_play_keys)))
            except Exception as e:
                raise self.printer.command_error(
                    "Unable to load PLAY '%s': %s" % (play_file, e))

        if self._recording_enabled:
            rec_path = self._resolve_keypress_path(record_file,
                                                   for_write=True)
            gcmd.respond_info("Recording keypresses to: %s" % (rec_path,))
        if self.mock_input:
            gcmd.respond_info("Started etching with playback input")
        else:
            gcmd.respond_info("Started etching!")

        self.gcode.run_script_from_command(startup_gcode)
        self.gcode.run_script_from_command(f"G1 f{self.speed*60}")
        self.toolhead.manual_move([self.x, self.y, None], self.speed)
        self.toolhead.manual_move([None, None, self.z], self.speed)
        self.sim_x = self.x
        self.sim_y = self.y
        self.sim_z = self.z
        self._sim_segments.clear()
        self._printed_layers = [[]]
        self._active_layer_index = 0
        self._last_draw_pos = (self.x, self.y, self.z)
        self._shift_down_last = False
        self._shift_extrude_latched = False

        gcmd.respond_info("Starting pygame init")
        pygame.init()
        self.clock = pygame.time.Clock()
        self.screen = pygame.display.set_mode((500, 500))
        pygame.display.set_caption("Keyboard Control")
        gcmd.respond_info("Finished pygame init")
        self._draw_scene()
        
        self.test_keys = loaded_play_keys or []
        self.frame_count = 0
        self.max_frames = len(self.test_keys) if self.mock_input else math.inf
        self.running = True
        if self.etch_timer is not None:
            self.reactor.update_timer(self.etch_timer, self.reactor.NOW)

    def cmd_ETCH_STOP(self, gcmd):
        gcmd.respond_info("Stop etching")
        if self.etch_timer is not None:
            self.reactor.update_timer(self.etch_timer, self.reactor.NOW)
        self._stop_etch()
        if self.running:
            self.gcode.run_script_from_command(shutdown_gcode)

    def cmd_ETCH_LIST_PLAYBACKS(self, gcmd):
        keypress_dir = self._get_keypress_dir()
        try:
            entries = []
            for name in os.listdir(keypress_dir):
                if not name.lower().endswith('.json'):
                    continue
                path = os.path.join(keypress_dir, name)
                if not os.path.isfile(path):
                    continue
                entries.append((os.path.getmtime(path), name))
            if not entries:
                gcmd.respond_info("No playback files found")
                return
            entries.sort(key=lambda item: item[0], reverse=True)
            top = entries[:10]
            lines = ["Recent playback files:"]
            for _, name in top:
                lines.append("- %s" % (name,))
            gcmd.respond_info("\n".join(lines))
        except Exception as e:
            raise self.printer.command_error(
                "Unable to list playback files: %s" % (e,))
            
def load_config(config):
    return KeyboardControl(config)

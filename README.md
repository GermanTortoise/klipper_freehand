# klipper_freehand

This is a Klipper plugin that allows you to control a Klipper 3D printer using your `wasd` keys and make it print, like how an Etch A Sketch works. The Klipper instance must run on a machine with a keyboard and screen, ie, a laptop.

## Installation

1. **Download the code and install the plugin**:
    ```shell
    git clone https://github.com/GermanTortoise/klipper_freehand.git && bash ./klipper_freehand/install.sh
    ```

2. **Add the config to your printer.cfg** and set the values for your printer:
    ```yaml
    [keyboard_control]
    # default values
    framerate: 60
    layer_height: 0.2
    line_width: 0.6
    x_min: 10.0
    x_max: 200.0
    y_min: 10.0
    y_max: 200.0
    z_min: 0.0
    z_max: 180.0
    speed: 100
    acceleration: 3000
    ```

## Running

1. **Stop the Klipper service** so it's not competing for the same hardware:
   ```bash
   sudo systemctl stop klipper
   ```

2. **Activate the virtualenv and run Klipper:**
   ```bash
   source ~/klippy-env/bin/activate
   python ~/klipper/klippy/klippy.py ~/printer_data/config/printer.cfg -l /tmp/klippy.log -a ~/printer_data/comms/klippy.sock
   ```

A Pygame window should appear. Use `wasd` to control x-y movement, `space` to increment a layer, and `q` to quit (or simply close the Pygame window).
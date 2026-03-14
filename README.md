# klipper_freehand

This is a Klipper plugin that allows you to control a Klipper 3D printer using your `wasd` keys and make it print, like how an Etch A Sketch works. The Klipper instance must run on a machine with a keyboard and screen, ie, a laptop.

## Installation

Run:
```shell
git clone https://github.com/GermanTortoise/klipper_freehand.git && bash ./klipper_freehand/install.sh
```

Add the config to your printer.cfg and set the values for your printer:
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
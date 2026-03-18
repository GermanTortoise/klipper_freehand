#!/bin/bash

sudo systemctl stop klipper
source ~/klippy-env/bin/activate
python ~/klipper/klippy/klippy.py ~/printer_data/config/printer.cfg -l /tmp/klippy.log -a ~/printer_data/comms/klippy.sock
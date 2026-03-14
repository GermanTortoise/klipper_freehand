#!/bin/bash

KLIPPER_PATH="${HOME}/klipper"
KLIPPER_ENV="${HOME}/klippy-env"
SRCDIR="$(cd "$(dirname "$0")" && pwd)"

preflight_checks() {
    if [ "$EUID" -eq 0 ]; then
        echo "This script must not run as root"
        exit 1
    fi
}
check_klipper() {
    if [ ! -d "$KLIPPER_PATH/klippy/extras/" ]; then
        echo "ERROR: Klipper not found at ${KLIPPER_PATH}"
        exit 1
    fi
    if [ ! -d "$KLIPPER_ENV/bin/" ]; then
        echo "ERROR: Klipper virtualenv not found at ${KLIPPER_ENV}"
        exit 1
    fi
}

link_extension() {
    echo "Linking extension to Klipper..."
    ln -sf "${SRCDIR}/keyboard_control.py" "${KLIPPER_PATH}/klippy/extras/keyboard_control.py"
}

install_dependencies() {
    echo "Installing Python dependencies..."
    "${KLIPPER_ENV}/bin/pip" install pygame vector
}

restart_klipper() {
    echo "Restarting Klipper..."
    sudo systemctl restart klipper
}

echo "Installing klipper_freehand..."
preflight_checks
check_klipper
link_extension
install_dependencies
restart_klipper
echo "Installation complete!"
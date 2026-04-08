"""IMU API routes."""
from flask import Blueprint, jsonify

from modules.imu_monitor import IMU
from modules import termux_bridge as termux
from modules import settings as cfg

bp = Blueprint('imu', __name__)


@bp.route('/api/imu/status')
def imu_status():
    """Return current IMU status."""
    return jsonify(IMU.get_status())


@bp.route('/api/imu/calibrate', methods=['POST'])
def imu_calibrate():
    """Calibrate IMU: save current quaternion as vertical baseline."""
    success = IMU.calibrate()
    return jsonify({"ok": success, "calibrated": IMU.is_calibrated()})


@bp.route('/api/imu/autodetect', methods=['POST'])
def imu_autodetect():
    """Autodetect best rotation sensor, save to settings, return result."""
    sensors = termux.list_sensors()
    sensor_name = termux.pick_best_rotation_sensor(sensors)
    if sensor_name:
        cfg.update({"imu_sensor_name": sensor_name})
        # Restart IMU monitor so it picks up the new sensor
        try:
            IMU.stop()
            IMU.start()
        except Exception:
            pass
        return jsonify({"ok": True, "imu_sensor_name": sensor_name})
    return jsonify({
        "ok": False,
        "error": "Nessun sensore di rotazione trovato. Verifica termux-sensor -l.",
        "sensors_found": sensors,
    })

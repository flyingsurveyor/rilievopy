"""IMU API routes."""
from flask import Blueprint, jsonify

from modules.imu_monitor import IMU

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

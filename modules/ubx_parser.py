"""
UBX protocol parser and upstream TCP connection to GNSS receiver.
"""

import math
import socket
import time
from typing import Optional

from pyubx2 import UBXReader, UBX_PROTOCOL, VALCKSUM

from .utils import now_iso, m_from_mm, m_from_01mm, rel_from_cm_01mm, hp_posecef, rtk_from_pvt, mode_from_fixtype
from .state import STATE, BytePipe, TCPRelay

# Map NAV-PVT flags3.lastCorrectionAge bucket (0-12) to upper-bound seconds.
# See ZED-F9P interface description for bucket definitions.
_CORR_AGE_MAP = [None, 1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120, 121]


def _corr_age_seconds(bucket):
    """Convert lastCorrectionAge bucket index to approximate seconds (upper bound)."""
    if not isinstance(bucket, int):
        return None
    if 0 <= bucket < len(_CORR_AGE_MAP):
        return _CORR_AGE_MAP[bucket]
    return None


def ubx_parse_loop(pipe: BytePipe):
    """Continuously parse UBX messages from pipe and update STATE."""
    while True:
        try:
            ubr = UBXReader(pipe, protfilter=UBX_PROTOCOL, validate=VALCKSUM, quitonerror=False)
            for raw, msg in ubr:
                try:
                    mid = getattr(msg, "identity", "")

                    if mid == "NAV-PVT":
                        lat = getattr(msg, "lat", None)
                        lon = getattr(msg, "lon", None)
                        hmsl = m_from_mm(getattr(msg, "hMSL", None))
                        hacc = m_from_mm(getattr(msg, "hAcc", None))
                        vacc = m_from_mm(getattr(msg, "vAcc", None))
                        fx = getattr(msg, "fixType", 0)
                        nsat = getattr(msg, "numSV", 0)
                        rtk = rtk_from_pvt(msg)
                        if rtk == "none" and (hacc is not None and hacc < 0.05) and (nsat and nsat >= 18):
                            rtk = "likely fixed"
                        diff_soln = getattr(msg, "diffSoln", None)
                        diff_age = _corr_age_seconds(getattr(msg, "lastCorrectionAge", None))
                        STATE.set("TPV", {
                            "time": now_iso(),
                            "mode": mode_from_fixtype(fx),
                            "fixType": fx,
                            "rtk": rtk,
                            "numSV": nsat,
                            "lat": lat, "lon": lon,
                            "altMSL": hmsl,
                            "hAcc": hacc, "vAcc": vacc,
                            "flags": getattr(msg, "flags", None),
                            "flags2": getattr(msg, "flags2", None),
                            "rtcmAge": diff_age,
                            "diffSoln": bool(diff_soln) if diff_soln is not None else None,
                        })

                    elif mid == "NAV-DOP":
                        STATE.set("DOP", {
                            "time": now_iso(),
                            "gdop": getattr(msg, "gDOP", None),
                            "pdop": getattr(msg, "pDOP", None),
                            "hdop": getattr(msg, "hDOP", None),
                            "vdop": getattr(msg, "vDOP", None),
                            "ndop": getattr(msg, "nDOP", None),
                            "edop": getattr(msg, "eDOP", None),
                            "tdop": getattr(msg, "tDOP", None),
                        })

                    elif mid == "NAV-RELPOSNED":
                        n = rel_from_cm_01mm(getattr(msg, "relPosN", None), getattr(msg, "relPosHPN", 0))
                        e = rel_from_cm_01mm(getattr(msg, "relPosE", None), getattr(msg, "relPosHPE", 0))
                        d = rel_from_cm_01mm(getattr(msg, "relPosD", None), getattr(msg, "relPosHPD", 0))
                        sN = m_from_mm(getattr(msg, "accN", None))
                        sE = m_from_mm(getattr(msg, "accE", None))
                        sD = m_from_mm(getattr(msg, "accD", None))
                        horiz = None; L = None; bearing = None; slope = None; vertSense = None
                        if n is not None and e is not None and d is not None:
                            horiz = math.hypot(n, e)
                            L = math.sqrt(horiz * horiz + d * d)
                            bearing = math.degrees(math.atan2(e, n))
                            bearing += 360.0 if bearing < 0 else 0
                            slope = math.degrees(math.atan2(-d, horiz)) if horiz else 0.0
                            vertSense = "down" if d > 0 else ("up" if d < 0 else "level")
                        STATE.set("RELPOS", {
                            "time": now_iso(),
                            "N": n, "E": e, "D": d,
                            "sN": sN, "sE": sE, "sD": sD,
                            "baseline": L, "horiz": horiz,
                            "bearingDeg": bearing, "slopeDeg": slope, "vertSense": vertSense,
                        })

                    elif mid == "NAV-COV":
                        posValid = getattr(msg, "posCovValid", None)
                        NN = getattr(msg, "posCovNN", None)
                        NE = getattr(msg, "posCovNE", None)
                        ND = getattr(msg, "posCovND", None)
                        EE = getattr(msg, "posCovEE", None)
                        ED = getattr(msg, "posCovED", None)
                        DD = getattr(msg, "posCovDD", None)
                        sN = math.sqrt(NN) if isinstance(NN, (int, float)) and NN >= 0 else None
                        sE = math.sqrt(EE) if isinstance(EE, (int, float)) and EE >= 0 else None
                        sD = math.sqrt(DD) if isinstance(DD, (int, float)) and DD >= 0 else None
                        STATE.set("COV", {
                            "time": now_iso(),
                            "valid": (bool(posValid) if posValid is not None else None),
                            "covNN": NN, "covEE": EE, "covDD": DD,
                            "covNE": NE, "covND": ND, "covED": ED,
                            "sigN": sN, "sigE": sE, "sigD": sD,
                        })

                    elif mid == "NAV-HPPOSECEF":
                        X = hp_posecef(getattr(msg, "ecefX", None), getattr(msg, "ecefXHp", 0))
                        Y = hp_posecef(getattr(msg, "ecefY", None), getattr(msg, "ecefYHp", 0))
                        Z = hp_posecef(getattr(msg, "ecefZ", None), getattr(msg, "ecefZHp", 0))
                        pAcc = m_from_01mm(getattr(msg, "pAcc", None))
                        STATE.set("HPPOSECEF", {"time": now_iso(), "X": X, "Y": Y, "Z": Z, "pAcc": pAcc})

                    elif mid == "NAV-HPPOSLLH":
                        _lat = getattr(msg, "lat", None)
                        _lon = getattr(msg, "lon", None)
                        _h = getattr(msg, "height", None)
                        _hmsl = getattr(msg, "hMSL", None)
                        _latHp = getattr(msg, "latHp", None)
                        _lonHp = getattr(msg, "lonHp", None)
                        _heightHp = getattr(msg, "heightHp", None)
                        _hMSLHp = getattr(msg, "hMSLHp", None)
                        lat = (_lat + (_latHp if _latHp is not None else 0)) if _lat is not None else None
                        lon = (_lon + (_lonHp if _lonHp is not None else 0)) if _lon is not None else None
                        h = (_h / 1000.0 + (_heightHp if _heightHp is not None else 0) / 10000.0) if _h is not None else None
                        hmsl = (_hmsl / 1000.0 + (_hMSLHp if _hMSLHp is not None else 0) / 10000.0) if _hmsl is not None else None
                        hAcc = m_from_mm(getattr(msg, "hAcc", None))
                        vAcc = m_from_mm(getattr(msg, "vAcc", None))
                        STATE.set("HPPOSLLH", {
                            "time": now_iso(),
                            "lat": lat, "lon": lon,
                            "altHAE": h, "altMSL": hmsl,
                            "hAcc": hAcc, "vAcc": vAcc
                        })
                        tpv = STATE.snapshot().get("TPV", {})
                        tpv.update({
                            "lat": lat, "lon": lon,
                            "altHAE": h, "altMSL": hmsl,
                            "hAcc": hAcc, "vAcc": vAcc,
                            "time": now_iso()
                        })
                        STATE.set("TPV", tpv)

                except Exception as e:
                    print(f"# {now_iso()} [parser] msg error ({getattr(msg, 'identity', '?')}): {e}")

        except Exception as e:
            print(f"# {now_iso()} [parser] reader error: {e} (reconnect)")
            time.sleep(0.5)


def upstream_loop(host: str, port: int, pipe: BytePipe,
                  relay: Optional[TCPRelay], retry: float = 3.0):
    """Connect to GNSS TCP stream, feed pipe and relay."""
    while True:
        sock = None
        try:
            print(f"# {now_iso()} [upstream] connect {host}:{port}")
            sock = socket.create_connection((host, port), timeout=15.0)
            sock.settimeout(10.0)
            print(f"# {now_iso()} [upstream] connected")
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("socket closed by peer")
                pipe.feed(chunk)
                if relay is not None:
                    relay.broadcast(chunk)
        except Exception as e:
            print(f"# {now_iso()} [upstream] disconnected: {e}. Retry in {retry}s")
            time.sleep(retry)
        finally:
            try:
                if sock:
                    sock.close()
            except Exception:
                pass

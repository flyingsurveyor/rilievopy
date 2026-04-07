/**
 * alerts.js — Browser Web Audio API beep synthesizer for RilievoPY
 *
 * Beep types:
 *   "confirm"  — 1 short high beep  (800 Hz, 100 ms)
 *   "success"  — 1 medium beep      (600 Hz, 200 ms)
 *   "warning"  — 2 beeps            (500 Hz, 150 ms each, 100 ms gap)
 *   "error"    — 3 rapid beeps      (400 Hz, 100 ms each, 80 ms gap)
 */

(function () {
  'use strict';

  var _ctx = null;

  /** Return (or lazily create) an AudioContext. */
  function _getCtx() {
    if (!_ctx) {
      try {
        _ctx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (e) {
        return null;
      }
    }
    // Resume if suspended (browser autoplay policy)
    if (_ctx.state === 'suspended') {
      _ctx.resume();
    }
    return _ctx;
  }

  /** Play a single beep at `freq` Hz for `durationMs` milliseconds, starting at `startTime`. */
  function _beep(ctx, freq, durationMs, startTime) {
    try {
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, startTime);

      var dur = durationMs / 1000;
      gain.gain.setValueAtTime(0.4, startTime);
      gain.gain.exponentialRampToValueAtTime(0.001, startTime + dur);

      osc.start(startTime);
      osc.stop(startTime + dur);
    } catch (e) {
      // ignore — Web Audio not available
    }
  }

  /**
   * Play an alert sound by kind.
   * @param {string} kind — "confirm" | "success" | "warning" | "error"
   */
  function playAlertSound(kind) {
    var ctx = _getCtx();
    if (!ctx) return;

    var now = ctx.currentTime;

    switch (kind) {
      case 'confirm':
        _beep(ctx, 800, 100, now);
        break;

      case 'success':
        _beep(ctx, 600, 200, now);
        break;

      case 'warning':
        _beep(ctx, 500, 150, now);
        _beep(ctx, 500, 150, now + 0.25);
        break;

      case 'error':
        _beep(ctx, 400, 100, now);
        _beep(ctx, 400, 100, now + 0.18);
        _beep(ctx, 400, 100, now + 0.36);
        break;

      default:
        // unknown kind — play a generic short beep
        _beep(ctx, 600, 100, now);
    }
  }

  // Initialise AudioContext on first user interaction (browser autoplay policy).
  function _initOnInteraction() {
    _getCtx();
    document.removeEventListener('click', _initOnInteraction);
    document.removeEventListener('touchstart', _initOnInteraction);
    document.removeEventListener('keydown', _initOnInteraction);
  }
  document.addEventListener('click', _initOnInteraction);
  document.addEventListener('touchstart', _initOnInteraction);
  document.addEventListener('keydown', _initOnInteraction);

  // Expose globally
  window.playAlertSound = playAlertSound;
})();

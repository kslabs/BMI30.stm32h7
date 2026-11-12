from __future__ import annotations

import importlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
import sys
import threading
import time


def run_rt_detector_process(
    engine_path: str,
    max_samples: int,
    req_q,
    res_q,
    stop_event,
    initial_generation: int,
    initial_stream_token: int,
    ready_event=None,
    shared_generation=None,
    shared_stream_token=None,
    stop_ack_event=None,
):
    """Spawn-safe entry point for the isolated realtime detector.

    The active BMI30 engine is loaded from a timestamped filename, so its
    worker function is not directly importable by ``multiprocessing.spawn``.
    This stable helper is importable in a fresh child and loads that exact
    engine copy there.  A spawned child inherits no live Qt/libusb threads or
    USB descriptors from the service process.
    """
    # Keep the detector child deliberately small.  NumPy/OpenBLAS defaults can
    # otherwise create a pool of native workers in every spawned detector and
    # compete with the latency-sensitive USB reader on the Raspberry Pi.
    detector_threads = str(os.environ.get("BMI30_RT_DET_BLAS_THREADS", "1") or "1").strip()
    if not detector_threads.isdigit() or int(detector_threads) < 1:
        detector_threads = "1"
    for env_name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[env_name] = detector_threads
    path = os.path.abspath(os.path.expanduser(str(engine_path)))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"BMI30 detector engine not found: {path}")
    module_name = f"_bmi30_detector_engine_{os.getpid()}"
    loader = SourceFileLoader(module_name, path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        raise RuntimeError(f"Cannot load BMI30 detector engine: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    worker_main = getattr(module, "_rt_detector_process_main", None)
    if not callable(worker_main):
        raise RuntimeError("BMI30 detector worker entry point is missing")
    if ready_event is not None:
        ready_event.set()
    return worker_main(
        [],
        int(max_samples),
        req_q,
        res_q,
        stop_event,
        True,
        int(initial_generation),
        int(initial_stream_token),
        shared_generation,
        shared_stream_token,
        stop_ack_event,
    )


CONFIG_FILE = os.path.abspath(os.path.expanduser(
    os.getenv("BMI30_CONFIG_JSON", "").strip()
    or os.path.join(os.path.dirname(__file__), "bmi30_config.json")
))


def import_gpio_module(module_name: str):
    """Import GPIO-related module, falling back to system dist-packages from venv."""
    try:
        return importlib.import_module(module_name)
    except Exception:
        pass
    for extra_path in (
        "/usr/lib/python3/dist-packages",
        f"/usr/local/lib/python{sys.version_info.major}.{sys.version_info.minor}/dist-packages",
    ):
        try:
            if os.path.isdir(extra_path) and extra_path not in sys.path:
                sys.path.append(extra_path)
        except Exception:
            pass
    return importlib.import_module(module_name)


class PwmBeeper:
    """Best-effort beeper for RPi PWM0 (GPIO12)."""

    def __init__(self, gpio_pin: int = 12):
        self.gpio_pin = int(gpio_pin)
        self._backend = None
        self._pi = None
        self._lg = None
        self._lg_h = None
        self._gpio = None
        self._pwm = None
        self._lock = threading.Lock()
        self._enabled = str(os.getenv("BMI30_BEEP_ENABLE", "1")).lower() not in ("0", "false", "no")
        self._continuous_on = False
        self._last_freq = 0
        self._pattern_token = 0
        self._repeat_loop_active = False
        self._repeat_loop_sequences = None
        self._repeat_loop_gap = 0.0
        self._repeat_loop_stop_requested = False
        self._init_backend()

    def status(self) -> str:
        """Return backend status for UI/logging."""
        try:
            if not bool(self._enabled):
                return "OFF"
            b = str(self._backend or "none")
            if b == "none":
                return "none"
            try:
                if bool(getattr(self, "_continuous_on", False)) and int(getattr(self, "_last_freq", 0) or 0) > 0:
                    return f"{b}@GPIO{int(self.gpio_pin)} f={int(self._last_freq)}"
            except Exception:
                pass
            return f"{b}@GPIO{int(self.gpio_pin)}"
        except Exception:
            return "unknown"

    def _init_backend(self):
        if not self._enabled:
            return
        try:
            lgpio = import_gpio_module("lgpio")  # type: ignore
            chip = 0
            try:
                chip = int(os.getenv("BMI30_GPIOCHIP", "0") or 0)
            except Exception:
                chip = 0
            h = lgpio.gpiochip_open(chip)
            try:
                lgpio.gpio_claim_output(h, self.gpio_pin, 0)
            except Exception:
                pass
            self._backend = "lgpio"
            self._lg = lgpio
            self._lg_h = h
            return
        except Exception:
            self._lg = None
            self._lg_h = None
        try:
            pigpio = import_gpio_module("pigpio")  # type: ignore
            pi = pigpio.pi()
            if getattr(pi, "connected", False):
                self._backend = "pigpio"
                self._pi = pi
                pi.set_mode(self.gpio_pin, pigpio.OUTPUT)
                pi.write(self.gpio_pin, 0)
                return
        except Exception:
            pass
        try:
            gpio = import_gpio_module("RPi.GPIO")  # type: ignore
            gpio.setwarnings(False)
            gpio.setmode(gpio.BCM)
            gpio.setup(self.gpio_pin, gpio.OUT, initial=gpio.LOW)
            self._backend = "rpi_gpio"
            self._gpio = gpio
            return
        except Exception:
            self._backend = None
            self._gpio = None
            self._pi = None
            self._lg = None
            self._lg_h = None

    def _start(self, freq_hz: float):
        if not self._enabled or self._backend is None:
            return
        freq = int(max(1, float(freq_hz)))
        if self._backend == "pigpio" and self._pi is not None:
            try:
                self._pi.hardware_PWM(self.gpio_pin, freq, 500_000)
            except Exception:
                pass
            return
        if self._backend == "lgpio" and self._lg is not None and self._lg_h is not None:
            try:
                self._lg.tx_pwm(self._lg_h, self.gpio_pin, freq, 50)
            except Exception:
                pass
            return
        if self._backend == "rpi_gpio" and self._gpio is not None:
            try:
                if self._pwm is None:
                    self._pwm = self._gpio.PWM(self.gpio_pin, freq)
                    self._pwm.start(50.0)
                else:
                    self._pwm.ChangeFrequency(freq)
                    self._pwm.ChangeDutyCycle(50.0)
            except Exception:
                pass

    def _stop(self):
        if not self._enabled or self._backend is None:
            return
        if self._backend == "pigpio" and self._pi is not None:
            try:
                self._pi.hardware_PWM(self.gpio_pin, 0, 0)
            except Exception:
                pass
            return
        if self._backend == "lgpio" and self._lg is not None and self._lg_h is not None:
            try:
                self._lg.tx_pwm(self._lg_h, self.gpio_pin, 0, 0)
            except Exception:
                pass
            return
        if self._backend == "rpi_gpio" and self._pwm is not None:
            try:
                self._pwm.ChangeDutyCycle(0.0)
            except Exception:
                pass

    def set_continuous(self, freq_hz: float | None):
        """Enable/disable continuous tone."""
        if not self._enabled or self._backend is None:
            return
        try:
            freq = int(max(0, float(freq_hz or 0.0)))
        except Exception:
            freq = 0
        with self._lock:
            if freq <= 0:
                self._pattern_token = int(getattr(self, "_pattern_token", 0)) + 1
                self._repeat_loop_active = False
                self._repeat_loop_sequences = None
                self._repeat_loop_stop_requested = False
                self._continuous_on = False
                self._last_freq = 0
                self._stop()
                return
            if self._continuous_on and self._last_freq == int(freq):
                return
            self._pattern_token = int(getattr(self, "_pattern_token", 0)) + 1
            self._repeat_loop_active = False
            self._repeat_loop_sequences = None
            self._repeat_loop_stop_requested = False
            self._continuous_on = True
            self._last_freq = int(freq)
            self._start(float(freq))

    def stop_now(self):
        """Stop PWM immediately and cancel pending pattern playback."""
        if not self._enabled or self._backend is None:
            return
        with self._lock:
            self._pattern_token = int(getattr(self, "_pattern_token", 0)) + 1
            self._repeat_loop_active = False
            self._repeat_loop_sequences = None
            self._repeat_loop_stop_requested = False
            self._continuous_on = False
            self._last_freq = 0
            self._stop()

    def _clean_sequence(self, segments):
        clean = []
        try:
            for freq_hz, seconds in list(segments or []):
                dur = max(0.0, float(seconds))
                if dur <= 0.0:
                    continue
                if freq_hz is None:
                    clean.append((None, dur))
                else:
                    freq = float(freq_hz)
                    if freq <= 0.0:
                        clean.append((None, dur))
                    else:
                        clean.append((freq, dur))
        except Exception:
            clean = []
        return tuple(clean)

    def play_sequence(self, segments):
        """Play a non-blocking latest-wins sequence of (freq_hz, seconds)."""
        if not self._enabled or self._backend is None:
            return
        clean = self._clean_sequence(segments)
        if not clean:
            return
        with self._lock:
            self._pattern_token = int(getattr(self, "_pattern_token", 0)) + 1
            token = int(self._pattern_token)
            self._repeat_loop_active = False
            self._repeat_loop_sequences = None
            self._repeat_loop_stop_requested = False
            self._continuous_on = False
            self._last_freq = 0

        def _sleep_or_cancel(seconds: float) -> bool:
            end_t = time.time() + max(0.0, float(seconds))
            while time.time() < end_t:
                with self._lock:
                    if int(getattr(self, "_pattern_token", 0)) != token or bool(getattr(self, "_continuous_on", False)):
                        return False
                time.sleep(min(0.02, max(0.0, end_t - time.time())))
            return True

        def _run():
            try:
                for freq_hz, seconds in clean:
                    with self._lock:
                        if int(getattr(self, "_pattern_token", 0)) != token or bool(getattr(self, "_continuous_on", False)):
                            return
                        if freq_hz is None:
                            self._last_freq = 0
                            self._stop()
                        else:
                            self._last_freq = int(max(1, float(freq_hz)))
                            self._start(float(freq_hz))
                    if not _sleep_or_cancel(seconds):
                        return
            finally:
                with self._lock:
                    if int(getattr(self, "_pattern_token", 0)) == token and not bool(getattr(self, "_continuous_on", False)):
                        self._last_freq = 0
                        self._stop()

        threading.Thread(target=_run, daemon=True).start()

    def play_repeating_sequences(self, sequences, repeat_gap_s: float = 0.0):
        """Repeat one or more sequences until stop_now()/set_continuous()/play_sequence().

        The loop is latest-state, not queued: updating the sequences changes the next
        cycle without interrupting the tone currently being played.
        """
        if not self._enabled or self._backend is None:
            return
        clean_sequences = []
        try:
            for seq in list(sequences or []):
                clean = self._clean_sequence(seq)
                if clean:
                    clean_sequences.append(clean)
        except Exception:
            clean_sequences = []
        if not clean_sequences:
            return
        try:
            gap = max(0.0, float(repeat_gap_s))
        except Exception:
            gap = 0.0

        with self._lock:
            self._repeat_loop_sequences = tuple(clean_sequences)
            self._repeat_loop_gap = gap
            self._repeat_loop_stop_requested = False
            self._continuous_on = False
            if bool(getattr(self, "_repeat_loop_active", False)):
                return
            self._pattern_token = int(getattr(self, "_pattern_token", 0)) + 1
            token = int(self._pattern_token)
            self._repeat_loop_active = True
            self._last_freq = 0

        def _sleep_or_cancel(seconds: float) -> bool:
            end_t = time.time() + max(0.0, float(seconds))
            while time.time() < end_t:
                with self._lock:
                    if int(getattr(self, "_pattern_token", 0)) != token or bool(getattr(self, "_continuous_on", False)):
                        return False
                time.sleep(min(0.01, max(0.0, end_t - time.time())))
            return True

        def _run():
            seq_index = 0
            try:
                while True:
                    with self._lock:
                        if int(getattr(self, "_pattern_token", 0)) != token or bool(getattr(self, "_continuous_on", False)):
                            return
                        sequences_now = tuple(getattr(self, "_repeat_loop_sequences", None) or ())
                        gap_now = float(getattr(self, "_repeat_loop_gap", 0.0) or 0.0)
                    if not sequences_now:
                        return
                    sequence = sequences_now[seq_index % len(sequences_now)]
                    seq_index += 1
                    for freq_hz, seconds in sequence:
                        with self._lock:
                            if int(getattr(self, "_pattern_token", 0)) != token or bool(getattr(self, "_continuous_on", False)):
                                return
                            if freq_hz is None:
                                self._last_freq = 0
                                self._stop()
                            else:
                                self._last_freq = int(max(1, float(freq_hz)))
                                self._start(float(freq_hz))
                        if not _sleep_or_cancel(seconds):
                            return
                    with self._lock:
                        if int(getattr(self, "_pattern_token", 0)) != token or bool(getattr(self, "_continuous_on", False)):
                            return
                        stop_requested = bool(getattr(self, "_repeat_loop_stop_requested", False))
                        self._last_freq = 0
                        self._stop()
                        if stop_requested:
                            return
                    if gap_now > 0.0 and not _sleep_or_cancel(gap_now):
                        return
            finally:
                with self._lock:
                    if int(getattr(self, "_pattern_token", 0)) == token:
                        self._repeat_loop_active = False
                        self._repeat_loop_sequences = None
                        self._repeat_loop_stop_requested = False
                        self._last_freq = 0
                        self._stop()

        threading.Thread(target=_run, daemon=True).start()

    def stop_repeating_after_current_sequence(self) -> bool:
        """Stop a repeating sequence after the current two-tone phrase finishes."""
        if not self._enabled or self._backend is None:
            return False
        with self._lock:
            if not bool(getattr(self, "_repeat_loop_active", False)):
                return False
            self._repeat_loop_stop_requested = True
            return True

    def play_repeating_sequence(self, segments, repeat_gap_s: float = 0.0):
        """Repeat a single sequence until explicitly stopped."""
        self.play_repeating_sequences((segments,), repeat_gap_s=repeat_gap_s)

    def play_pattern(self, f1: float, f2: float, t_on1: float = 0.150, t_gap: float = 0.050, t_on2: float = 0.150):
        """Play: ON(f1) -> OFF -> ON(f2). Latest call cancels older patterns."""
        self.play_sequence(((f1, t_on1), (None, t_gap), (f2, t_on2)))


def save_config(desired_profile, desired_freq=None):
    try:
        data = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        data["desired_profile"] = desired_profile
        if desired_freq is not None:
            data["desired_freq"] = desired_freq
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def load_config():
    """Load desired_profile from config file. Default=1."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        val = int(data.get("desired_profile", 1))
        if val not in (1, 2):
            return 1
        return val
    except Exception:
        return 1


def load_freq():
    """Load desired_freq from config file. Default=200."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return int(data.get("desired_freq", 200))
    except Exception:
        return 200


def save_det_ratio(det_ratio: float):
    """Save detector ratio to config file (best-effort merge)."""
    try:
        data = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        data["det_ratio"] = float(det_ratio)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def load_det_ratio() -> float:
    """Load detector ratio from config/env. Default=2.0."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if "det_ratio" in data:
            return float(data.get("det_ratio", 2.0))
    except Exception:
        pass
    try:
        return float(os.getenv("BMI30_DETECT_RATIO", "2.0"))
    except Exception:
        return 2.0


def load_avg_n():
    """Load avg_n from config file (fallback 20)."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        val = int(data.get("avg_n", 20))
        if val < 2:
            return 20
        return val
    except Exception:
        return 20


def env_bool(name: str, default: bool = False) -> bool:
    """Parse common boolean env var values."""
    try:
        val = os.getenv(name)
        if val is None:
            return bool(default)
        return str(val).strip().lower() in ("1", "true", "yes", "y", "on", "t")
    except Exception:
        return bool(default)


def save_avg_n(avg_n: int):
    """Save avg_n to config file alongside other settings (best-effort merge)."""
    try:
        data = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        data["avg_n"] = int(avg_n)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

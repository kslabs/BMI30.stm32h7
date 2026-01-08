
"""BMI30.200.py — единая точка входа: сразу открывает живую осциллограмму Vendor Bulk двухканального потока."""

from __future__ import annotations
import os
import sys, time, json, os as _os_alias, struct
import tempfile
import threading

# When running as `py -3 HostTools/BMI30.200.py`, Python puts only `HostTools/` on sys.path.
# Add the project root so local packages (e.g. `usb_vendor/`) are importable.
try:
	_this_dir = os.path.dirname(__file__)
	_proj_root = os.path.abspath(os.path.join(_this_dir, os.pardir))
	if _proj_root and _proj_root not in sys.path:
		sys.path.insert(0, _proj_root)
except Exception:
	pass
# Qt env: avoid GTK theme/plugin conflicts on RPi/Linux.
# IMPORTANT: do not force Linux backends on Windows.
if sys.platform.startswith("linux"):
	# Choose backend safely:
	# - if user set QT_QPA_PLATFORM explicitly, respect it
	# - prefer xcb when DISPLAY exists
	# - prefer wayland when WAYLAND_DISPLAY exists
	# - otherwise use offscreen (headless smoke-tests)
	if "QT_QPA_PLATFORM" not in os.environ:
		if os.getenv("DISPLAY"):
			os.environ["QT_QPA_PLATFORM"] = "xcb"
		elif os.getenv("WAYLAND_DISPLAY"):
			os.environ["QT_QPA_PLATFORM"] = "wayland"
		else:
			os.environ["QT_QPA_PLATFORM"] = "offscreen"
	os.environ.setdefault("QT_STYLE_OVERRIDE", "Fusion")
	os.environ.pop("QT_QPA_PLATFORMTHEME", None)
import numpy as np  # type: ignore
import serial, glob

try:
	from usb_vendor.usb_stream import USBStream, CMD_SET_PROFILE, CMD_STOP_STREAM, CMD_START_STREAM, CMD_SOFT_RESET, CMD_DEEP_RESET, CMD_SET_WINDOWS, CMD_SET_STREAM_MODE, CMD_ASYNC  # type: ignore
except Exception:
	from usb_vendor.usb_stream import USBStream  # type: ignore
	CMD_SET_PROFILE = 0x14
	CMD_STOP_STREAM = 0x21
	CMD_START_STREAM = 0x20
	CMD_GET_STATUS = 0x30
	CMD_FULL_MODE = 0x13
	CMD_CHMODE = 0x19
	CMD_ASYNC = 0x18
	CMD_BLOCK_HZ = 0x11
	CMD_SET_WINDOWS = 0x10
	CMD_SET_STREAM_MODE = 0x1A
	CMD_SOFT_RESET = 0x7E
	CMD_DEEP_RESET = 0x7F
	CMD_SET_ALT = 0x31

# Qt/pyqtgraph bootstrap: enforce PyQt5 first to keep binding consistent
PG_IMPORT_ERR = None
try:
	# Tell pyqtgraph which Qt lib to use
	os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
	from PyQt5 import QtWidgets, QtCore  # type: ignore
	import pyqtgraph as pg  # type: ignore
except Exception as e1:  # pragma: no cover
	try:
		os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
		from PySide6 import QtWidgets, QtCore  # type: ignore
		import pyqtgraph as pg  # type: ignore
	except Exception as e2:
		PG_IMPORT_ERR = e1


def load_config():
	config_file = os.path.join(os.path.dirname(__file__), "bmi30_config.json")
	try:
		with open(config_file, "r") as f:
			data = json.load(f)
		return data.get("desired_profile", 1)
	except Exception:
		return 1


def save_config(desired_profile):
	config_file = os.path.join(os.path.dirname(__file__), "bmi30_config.json")
	try:
		with open(config_file, "w") as f:
			json.dump({"desired_profile": desired_profile}, f)
	except Exception:
		pass


def load_avg_n():
	"""Load avg_n from config file (fallback 20)."""
	config_file = os.path.join(os.path.dirname(__file__), "bmi30_config.json")
	try:
		with open(config_file, "r") as f:
			data = json.load(f)
		val = int(data.get("avg_n", 20))
		if val < 2:
			return 20
		return val
	except Exception:
		return 20


def save_avg_n(avg_n: int):
	"""Save avg_n to config file alongside other settings (best-effort merge)."""
	config_file = os.path.join(os.path.dirname(__file__), "bmi30_config.json")
	try:
		data = {}
		if os.path.exists(config_file):
			try:
				with open(config_file, "r") as f:
					data = json.load(f) or {}
			except Exception:
				data = {}
		data["avg_n"] = int(avg_n)
		with open(config_file, "w") as f:
			json.dump(data, f)
	except Exception:
		pass


class ScopeWindow:
	def __init__(self):
		print("[INIT] BMI30 GUI starting...", flush=True)
		if PG_IMPORT_ERR:
			print(f"[ERR] pyqtgraph/Qt import failed: {PG_IMPORT_ERR}")
			sys.exit(2)
		self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
		self.win = QtWidgets.QMainWindow()
		self.win.setWindowTitle("BMI30 Vendor Bulk Oscilloscope - Thread Mode")
		print("[INIT] Window created", flush=True)
		central = QtWidgets.QWidget()
		self.win.setCentralWidget(central)
		layout = QtWidgets.QVBoxLayout(central)
		# Загружаем desired_profile из config
		self.desired_profile = load_config()  # 1=>200 Гц, 2=>300 Гц
		# legend (вместо верхних кнопок)
		self.legend_lbl = QtWidgets.QLabel("--")
		font = self.legend_lbl.font()
		font.setPointSize(font.pointSize()+1)
		self.legend_lbl.setFont(font)
		legend_bar = QtWidgets.QHBoxLayout()
		legend_bar.addWidget(self.legend_lbl, 1)
		# выбор количества усреднённых буферов (avg_n)
		self.avg_n = load_avg_n()
		self.avg_box = QtWidgets.QComboBox()
		avg_items = ["4","8","12","16","20","24","28","32"]
		self.avg_box.addItems(avg_items)
		# Не триггерить обработчик при установке значения по умолчанию
		try:
			self.avg_box.blockSignals(True)
		except Exception:
			pass
		# установим индекс на текущее значение avg_n
		try:
			if str(self.avg_n) in avg_items:
				self.avg_box.setCurrentIndex(avg_items.index(str(self.avg_n)))
			else:
				# если значение не в списке, добавим его и выберем
				self.avg_box.addItem(str(self.avg_n))
				self.avg_box.setCurrentIndex(self.avg_box.count()-1)
		except Exception:
			pass
		try:
			self.avg_box.blockSignals(False)
		except Exception:
			pass
		self.avg_box.currentIndexChanged.connect(self._on_avg_change)
		legend_bar.addWidget(self.avg_box, 0)
		self.btn_reconnect = QtWidgets.QPushButton("↻")
		self.btn_reconnect.setToolTip("Ручное переподключение к устройству")
		self.btn_reconnect.clicked.connect(self._manual_reconnect)
		# уменьшить ширину трёх правых кнопок вдвое
		for _btn in (self.btn_reconnect,):
			try:
				_btn.setFixedSize(21, 21)
			except Exception:
				pass
		legend_bar.addWidget(self.btn_reconnect, 0)
		# Кнопка перезапитки USB-порта (через uhubctl)
		self.btn_power = QtWidgets.QPushButton("⚡")
		self.btn_power.setToolTip("Переподать питание на USB-порту устройства (uhubctl)")
		self.btn_power.clicked.connect(self._power_cycle_usb_port)
		try:
			self.btn_power.setFixedSize(21, 21)
		except Exception:
			pass
		# правый клик по кнопке питания копирует заголовок в буфер обмена
		try:
			self.btn_power.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
			self.btn_power.customContextMenuRequested.connect(self._copy_legend_from_btn)
		except Exception:
			pass
		legend_bar.addWidget(self.btn_power, 0)
		# Кнопка диагностики и мягкого рестарта
		# Диагностика: делаем кнопку переключаемой и используем её для
		# включения/выключения DC-вычитания (фиксация состояния).
		self.btn_diag = QtWidgets.QPushButton("🩺")
		self.btn_diag.setToolTip("Переключатель DC removal (клик: вкл/выкл)")
		self.btn_diag.setCheckable(True)
		# отобразим текущее состояние DC
		try:
			self.btn_diag.setChecked(bool(self.dc_removal_enabled))
		except Exception:
			pass
		self.btn_diag.toggled.connect(self._on_toggle_dc_removal)
		try:
			self.btn_diag.setFixedSize(21, 21)
		except Exception:
			pass
		# синхронизируем визуальный вид кнопки с текущим состоянием DC
		try:
			self._on_toggle_dc_removal(bool(self.dc_removal_enabled))
		except Exception:
			pass
		legend_bar.addWidget(self.btn_diag, 0)
		layout.addLayout(legend_bar)
		# plots
		self.plotw = pg.GraphicsLayoutWidget()
		layout.addWidget(self.plotw, 1)
		self.p0 = self.plotw.addPlot(row=0, col=0, title="ADC0")
		self.p1 = self.plotw.addPlot(row=1, col=0, title="ADC1")
		# Use fast line plots instead of many symbols to reduce CPU and increase FPS
		# Two packets per channel => draw even/odd oscillograms per plot (different colors)
		# ADC0: более контрастные цвета для тёмной темы
		self.curve0_a = self.p0.plot(pen=pg.mkPen('#ffb86b', width=1.5), symbol=None)  # even (яркий оранжевый)
		self.curve0_b = self.p0.plot(pen=pg.mkPen('#00e5ff', width=1.5), symbol=None)  # odd (яркий циановый)
		# ADC1: повышенная яркость для лучшей видимости на тёмной теме
		self.curve1_a = self.p1.plot(pen=pg.mkPen('#ff6b6b', width=1.5), symbol=None)  # even (яркий красный)
		self.curve1_b = self.p1.plot(pen=pg.mkPen('#00ffd5', width=1.5), symbol=None)  # odd (яркий мятный)
		self.p0.showGrid(x=True, y=True, alpha=0.3)
		self.p1.showGrid(x=True, y=True, alpha=0.3)
		# Синхронизируем X-оси между графиками
		try:
			self.p1.setXLink(self.p0)
		except Exception:
			pass
		# отключим авто-растягивание по X (будем сами задавать диапазон)
		self.p0.disableAutoRange(axis=pg.ViewBox.XAxis)
		self.p1.disableAutoRange(axis=pg.ViewBox.XAxis)
		self.p0.enableAutoRange(y=True)
		self.p1.enableAutoRange(y=True)
		# Обновлённые размеры кадров: профиль 1 ~1360 семплов, профиль 2 ~912
		# Не полагаемся на жёсткие размеры: устройство авто-фиксирует total_samples по первому рабочему кадру.
		# Эти значения используются только как "подсказка"/резерв.
		self.expected_len_map = {1: 912, 2: 912}
		self.initial_expected = self.expected_len_map.get(1, 912)
		# Рекомендуемые FRAME_SAMPLES для стабильных ~20 FPS (используем фактические длины кадров)
		self.ns_map = {1: 912, 2: 912}
		# По умолчанию не навязываем Ns устройству (макс. FPS). Включить подсказку Ns: BMI30_SEND_NS=1
		try:
			# Важно: SET_FRAME_SAMPLES ломает профиль 1, поэтому по умолчанию НЕ отправляем.
			self.send_ns = str(os.getenv("BMI30_SEND_NS", "0")).lower() not in ("0","false","no")
		except Exception:
			self.send_ns = True
		# Тестовые кадры как данные выключены по умолчанию (включить: BMI30_TEST_AS_DATA=1)
		try:
			self.test_as_data = str(os.getenv("BMI30_TEST_AS_DATA", "0")).lower() not in ("0","false","no")
		except Exception:
			self.test_as_data = False
		# Показывать ли нулевые сигналы (по умолчанию ВКЛЮЧЕНО, чтобы видеть семплы "как есть")
		try:
			self.show_zero = str(os.getenv("BMI30_SHOW_ZERO", "1")).lower() not in ("0","false","no")
		except Exception:
			self.show_zero = True
		# Управление шкалой Y: по умолчанию авто-ПОДСТРОЙКА ВЫКЛЮЧЕНА — фиксируем диапазон
		try:
			self.y_auto = str(os.getenv("BMI30_Y_AUTO", "0")).lower() not in ("0","false","no")
		except Exception:
			self.y_auto = False
		try:
			self.y_min = float(os.getenv("BMI30_Y_MIN", "0"))
		except Exception:
			self.y_min = 0.0
		try:
			self.y_max = float(os.getenv("BMI30_Y_MAX", "65535"))
		except Exception:
			self.y_max = 65535.0
		# Применим параметры Y сразу
		try:
			self.p0.enableAutoRange(y=self.y_auto)
			self.p1.enableAutoRange(y=self.y_auto)
			if not self.y_auto:
				self.p0.setYRange(self.y_min, self.y_max, padding=0.02)
				self.p1.setYRange(self.y_min, self.y_max, padding=0.02)
		except Exception:
			pass
		# plots идут здесь, нижние элементы добавим после
		# data - shared buffers между reader thread и GUI thread
		self.base_buf_len: int | None = None  # будет 1200 (или иное) после первого кадра
		self.base_buf_len_bytes: int | None = None
		self.freq_hz: int | None = None
		self.ring_factor = 1  # фиксированный один буфер (последний кадр)
		# Shared buffers для двух каналов - инициализируем сразу с максимальным размером
		# Резерв под максимальный буфер (не жёстко завязан на профиль)
		self.max_samples = 2048
		# Two buffers per channel: even/odd packets keyed by seq&1
		self.data0 = np.zeros(self.max_samples, dtype=np.int32)  # совместимость (используем как even)
		self.data1 = np.zeros(self.max_samples, dtype=np.int32)  # совместимость (используем как even)
		self.data0_even = self.data0
		self.data0_odd = np.zeros(self.max_samples, dtype=np.int32)
		self.data1_even = self.data1
		self.data1_odd = np.zeros(self.max_samples, dtype=np.int32)
		# phase diagnostics (even/odd should be same magnitude, opposite phase)
		self.seq0_even = None
		self.seq0_odd = None
		self.seq1_even = None
		self.seq1_odd = None
		
		# Отслеживание текущего STREAM_MODE
		self.stream_mode = 0  # 0 = LATEST (600 семплов, last-buffer-wins), 1 = LOSSLESS_ROI (200 семплов, FIFO)
		
		# DC offset removal: адаптивная коррекция DC по каждому семплу (накопление при STREAM_MODE=1)
		self.dc_removal_enabled = False  # Флаг: применять ли DC removal (вычитание)
		# Усреднение осциллограмм (последние N кадров) поверх DC removal
		self.avg20_enabled = False
		self.avg20_nframes = 20
		# Кольцевые буферы усреднения: 2 канала × even/odd
		self._avg0_even = np.zeros((self.avg20_nframes, self.max_samples), dtype=np.float32)
		self._avg0_odd = np.zeros((self.avg20_nframes, self.max_samples), dtype=np.float32)
		self._avg1_even = np.zeros((self.avg20_nframes, self.max_samples), dtype=np.float32)
		self._avg1_odd = np.zeros((self.avg20_nframes, self.max_samples), dtype=np.float32)
		self._avg0_even_pos = 0
		self._avg0_odd_pos = 0
		self._avg1_even_pos = 0
		self._avg1_odd_pos = 0
		self._avg0_even_cnt = 0
		self._avg0_odd_cnt = 0
		self._avg1_even_cnt = 0
		self._avg1_odd_cnt = 0
		# Массивы DC offset для каждого семпла (инициализируем нулями, будут обновляться адаптивно)
		self.dc_offset_ch0_even = np.zeros(self.max_samples, dtype=np.float32)
		self.dc_offset_ch0_odd = np.zeros(self.max_samples, dtype=np.float32)
		self.dc_offset_ch1_even = np.zeros(self.max_samples, dtype=np.float32)
		self.dc_offset_ch1_odd = np.zeros(self.max_samples, dtype=np.float32)
		self.dc_last_save = time.time()  # Время последнего сохранения DC offset в файл
		try:
			self.dc_save_interval = float(os.getenv('BMI30_DC_SAVE_INTERVAL', '600'))
		except Exception:
			self.dc_save_interval = 600.0
		# Основной файл (по умолчанию рядом со скриптом), можно переопределить через BMI30_DC_FILE
		self.dc_save_file = os.getenv('BMI30_DC_FILE', os.path.join(os.path.dirname(__file__), 'dc_offset_samples.npz'))
		self.dc_save_file_bak = self.dc_save_file + '.bak'
		try:
			self.dc_update_step = float(os.getenv('BMI30_DC_UPDATE_STEP', '1.0'))
		except Exception:
			self.dc_update_step = 1.0
		# В каких STREAM_MODE учим DC (1=LOSSLESS_ROI, 2=AVG_ROI). По умолчанию: 1,2.
		try:
			modes_s = str(os.getenv('BMI30_DC_ADAPT_MODES', '1,2'))
			modes = set()
			for part in modes_s.replace(';', ',').replace(' ', '').split(','):
				if not part:
					continue
				try:
					modes.add(int(part))
				except Exception:
					pass
			self.dc_adapt_modes = modes if modes else {1, 2}
		except Exception:
			self.dc_adapt_modes = {1, 2}
		# Загрузить сохраненные DC offset массивы при старте
		self._load_dc_offset()
		
		# How to split packets into two phases (even/odd).
		# Firmware may encode phase in seq LSB, timestamp LSB, or reserved fields.
		try:
			# IMPORTANT: On RPi we must split even/odd strictly by reserved2&1.
			# (Do NOT use seq&1 or timestamp&1; those can drift or be reused by firmware.)
			requested = str(os.getenv('BMI30_PHASE_KEY', 'reserved2')).strip().lower()
			allow_legacy = str(os.getenv('BMI30_PHASE_ALLOW_LEGACY', '0')).lower() not in ('0','false','no')
			# Normalize common aliases
			if requested in ('r2', 'reserved2_lsb', 'reserved2&1'):
				requested = 'reserved2'
			if (requested not in ('reserved2',)) and not allow_legacy:
				print(f"[PHASE_KEY] forcing reserved2&1 (ignoring BMI30_PHASE_KEY={requested}); set BMI30_PHASE_ALLOW_LEGACY=1 to override", flush=True)
				requested = 'reserved2'
			self.phase_key = requested
			print(f"[PHASE_KEY] using {self.phase_key}&1", flush=True)
		except Exception:
			self.phase_key = 'reserved2'
			try:
				print(f"[PHASE_KEY] using {self.phase_key}&1", flush=True)
			except Exception:
				pass
		self._phase_key_chosen = None
		self._phase_key_stats = {}
		self._phase_key_seen = 0
		# Stable per-channel phase togglers (used when phase_key=='toggle')
		self._phase_last_seq_a = None
		self._phase_last_seq_b = None
		self._phase_last_ts_a = None
		self._phase_last_ts_b = None
		self._phase_toggle_a = 0
		self._phase_toggle_b = 0
		self._phase_last_par_a = None
		self._phase_last_par_b = None
		self._phase_repeat_a = 0
		self._phase_repeat_b = 0
		self._phase_trace_n = 0
		try:
			self.phase_trace = str(os.getenv('BMI30_PHASE_TRACE', '0')).lower() not in ('0','false','no')
		except Exception:
			self.phase_trace = False
		try:
			self.phase_trace_limit = int(os.getenv('BMI30_PHASE_TRACE_LIMIT', '400'))
		except Exception:
			self.phase_trace_limit = 400
		try:
			self.phase_diag = str(os.getenv('BMI30_PHASE_DIAG', '0')).lower() not in ('0','false','no')
		except Exception:
			self.phase_diag = False
		try:
			self.phase_diag_every = int(os.getenv('BMI30_PHASE_DIAG_EVERY', '50'))
		except Exception:
			self.phase_diag_every = 50
		try:
			self.phase_diag_maxlag = int(os.getenv('BMI30_PHASE_DIAG_MAXLAG', '20'))
		except Exception:
			self.phase_diag_maxlag = 20
		self._phase_diag_frames = 0
		self.timestamps = np.zeros(self.max_samples, dtype=np.float64)
		self.data_lock = threading.Lock()  # защита shared buffers
		self.reader_thread = None
		self.reader_running = False
		# Инициализируем view параметры сразу чтобы показывать данные
		self.view_start = 0
		self.view_len = self.max_samples
		try:
			self.initial_view_mult = float(os.getenv("BMI30_INITIAL_VIEW_MULT", "0.25"))  # сколько буферов показывать изначально (0.25 для ~340 семплов, но для осциллографа - последние)
		except Exception:
			self.initial_view_mult = 0.25
		# Диагностику остановки выводим в консоль (а не в GUI) по умолчанию
		self.diag_to_console = str(os.getenv("BMI30_DIAG_TO_CONSOLE", "1")).lower() not in ("0","false","no")
		# Всегда показываем полный кадр (start=0, len=buf)
		self.lock_full_view = True
		# По умолчанию не инвертируем сигнал в GUI (включить старую инверсию можно через BMI30_INVERT=1)
		self.no_invert = str(os.getenv("BMI30_INVERT", "0")).lower() in ("0","false","no")
		# Отладочные маркеры: каждые 100-й семпл отмечаем линейкой [-30000..30000] для проверки целостности буфера
		try:
			self.debug_markers = str(os.getenv("BMI30_DEBUG_MARKERS", "0")).lower() not in ("0","false","no")
		except Exception:
			self.debug_markers = False
		# Режим независимых каналов: по умолчанию ВКЛЮЧЕН — прошивка может вести независимые seq для A/B.
		try:
			self.independent_channels = str(os.getenv('BMI30_INDEPENDENT_CHANNELS', '1')).lower() not in ('0','false','no')
		except Exception:
			self.independent_channels = False
		# Диагностика размеров кадров: включить лог seq/lenA/lenB через BMI30_LOG_FRAME_LEN=1
		try:
			self.log_frame_len = str(os.getenv('BMI30_LOG_FRAME_LEN', '0')).lower() not in ('0','false','no')
		except Exception:
			self.log_frame_len = False
		try:
			self.log_frame_len_limit = int(os.getenv('BMI30_LOG_FRAME_LEN_LIMIT', '200'))
		except Exception:
			self.log_frame_len_limit = 200
		self._log_frame_len_count = 0
		# Трассировка содержимого по блокам (каждые 100 семплов): BMI30_CHUNK_TRACE=1, лимит кадров BMI30_CHUNK_TRACE_LIMIT
		try:
			self.chunk_trace = str(os.getenv('BMI30_CHUNK_TRACE', '0')).lower() not in ('0','false','no')
		except Exception:
			self.chunk_trace = False
		try:
			self.chunk_trace_limit = int(os.getenv('BMI30_CHUNK_TRACE_LIMIT', '50'))
		except Exception:
			self.chunk_trace_limit = 50
		self._chunk_trace_count = 0
		# Явная инициализация по последовательности разработчика (STOP→FULL→PROFILE→CHMODE→ASYNC→BLOCK_HZ→START)
		try:
			self.apply_init_sequence = str(os.getenv('BMI30_INIT_SEQUENCE', '1')).lower() not in ('0','false','no')
		except Exception:
			self.apply_init_sequence = True
		try:
			self.block_hz = int(os.getenv('BMI30_BLOCK_HZ', '200'))
		except Exception:
			self.block_hz = 200
		# Мониторинг конкретного семпла для ловли редких сбоев: BMI30_MON_SAMPLE=1, индекс BMI30_MON_INDEX (по умолчанию 300)
		try:
			self.monitor_sample = str(os.getenv('BMI30_MON_SAMPLE', '0')).lower() not in ('0','false','no')
		except Exception:
			self.monitor_sample = False
		try:
			self.monitor_index = int(os.getenv('BMI30_MON_INDEX', '300'))
		except Exception:
			self.monitor_index = 300
		self._last_mon_a = None
		self._last_mon_b = None
		# Опционально: срезать хвост внутренней очереди ассемблера (независимые каналы) чтобы не было лага
		try:
			self.flush_asm_queue = str(os.getenv('BMI30_FLUSH_ASM_QUEUE', '0')).lower() not in ('0','false','no')
		except Exception:
			self.flush_asm_queue = False
		# Capture diagnostic mode: write a short capture file when the first mismatch occurs
		try:
			cap_env = os.getenv('BMI30_CAPTURE_DIAG', None)
			if cap_env is None:
				self.capture_diag_path = None
			elif isinstance(cap_env, str) and cap_env.strip() == '1':
				self.capture_diag_path = os.path.join(tempfile.gettempdir(), f'bmi30_capture_{int(time.time())}.log')
			else:
				self.capture_diag_path = cap_env
		except Exception:
			self.capture_diag_path = None
		try:
			self.capture_diag_seconds = float(os.getenv('BMI30_CAPTURE_SECONDS', '10'))
		except Exception:
			self.capture_diag_seconds = 10.0
		try:
			self.capture_diag_limit = int(os.getenv('BMI30_CAPTURE_LIMIT', '2000'))
		except Exception:
			self.capture_diag_limit = 2000
		self._capture_diag_fp = None
		self._capture_diag_started = 0.0
		self._capture_diag_lines = 0
		# GAP capture: отдельный лог по событиям delta!=step (в т.ч. реальные пропуски) — включается только по env
		try:
			gcap_env = os.getenv('BMI30_GAP_CAPTURE', None)
			if gcap_env is None:
				self.gap_capture_path = None
			elif isinstance(gcap_env, str) and gcap_env.strip() == '1':
				self.gap_capture_path = os.path.join(tempfile.gettempdir(), f'bmi30_gap_{int(time.time())}.csv')
			else:
				self.gap_capture_path = gcap_env
		except Exception:
			self.gap_capture_path = None
		try:
			self.gap_capture_seconds = float(os.getenv('BMI30_GAP_CAPTURE_SECONDS', '10'))
		except Exception:
			self.gap_capture_seconds = 10.0
		try:
			self.gap_capture_limit = int(os.getenv('BMI30_GAP_CAPTURE_LIMIT', '2000'))
		except Exception:
			self.gap_capture_limit = 2000
		self._gap_capture_fp = None
		self._gap_capture_started = 0.0
		self._gap_capture_lines = 0
		# Скрывать полностью нулевые кадры (если это артефакт отображения/потока): по умолчанию ВКЛ (BMI30_HIDE_ALL_ZERO=1)
		try:
			self.hide_all_zero = str(os.getenv("BMI30_HIDE_ALL_ZERO", "1")).lower() not in ("0","false","no")
		except Exception:
			self.hide_all_zero = True
		# Ось X всегда в семплах (по ТЗ): фиксируем индексную шкалу, без времени
		self.use_time_axis = False
		# Оригинальные форматтеры оси X, чтобы можно было восстанавливать режим времени
		self._axis0_tickStrings_orig = None
		self._axis1_tickStrings_orig = None
		# max_samples, data0, data1, timestamps уже инициализированы выше для shared buffers
		self.last_seq = None
		self.gap_count = 0
		# Оценка шага seq (некоторые прошивки инкрементируют seq не на 1 на стерео-пару).
		self.seq_step = 1
		self._seq_step_hist = {}
		self._seq_step_hist_n = 0
		self.seq_reorder_count = 0
		# Пер-канальные seq/gap (важно, если A/B имеют независимые seq или relaxed-паринг склеивает разные seq).
		self.last_seq_a = None
		self.last_seq_b = None
		self.gap_a = 0
		self.gap_b = 0
		self.step_a = 1
		self.step_b = 1
		self.reord_a = 0
		self.reord_b = 0
		self._step_hist_a = {}
		self._step_hist_b = {}
		self._step_hist_a_n = 0
		self._step_hist_b_n = 0
		try:
			self.auto_independent = str(os.getenv('BMI30_AUTO_INDEPENDENT', '0')).lower() not in ('0','false','no')
		except Exception:
			self.auto_independent = False
		# GAP-логирование может само провоцировать пропуски на FS (console I/O). По умолчанию включено, но с throttling.
		try:
			self.gap_log_enabled = str(os.getenv('BMI30_GAP_LOG', '1')).lower() not in ('0', 'false', 'no')
		except Exception:
			self.gap_log_enabled = True
		try:
			# печатать не чаще, чем раз в N секунд; 0 = без ограничения
			self.gap_log_every = float(os.getenv('BMI30_GAP_LOG_EVERY', '1.0'))
		except Exception:
			self.gap_log_every = 1.0
		self._gap_log_last_t = 0.0
		self._gap_log_pending = 0
		self._gap_log_last_exp = None
		self._gap_log_last_got = None
		self.frames_sec = 0
		self.frames_a = 0  # счетчик кадров канала A (ADC0)
		self.frames_b = 0  # счетчик кадров канала B (ADC1)
		# Per-phase (even/odd) counters to validate rhythm (especially in AVG_ROI)
		self.frames_a_even = 0
		self.frames_a_odd = 0
		self.frames_b_even = 0
		self.frames_b_odd = 0
		self.afps_even = 0.0
		self.afps_odd = 0.0
		self.bfps_even = 0.0
		self.bfps_odd = 0.0
		# Timestamp delta diagnostics per phase (in seconds, last observed)
		self._dt_a_even = None
		self._dt_a_odd = None
		self._dt_b_even = None
		self._dt_b_odd = None
		self._ts_a_even = None
		self._ts_a_odd = None
		self._ts_b_even = None
		self._ts_b_odd = None
		self.zero_blocks = 0  # счётчик полностью нулевых кадров, скрытых из отображения
		self.last_fps_t = time.time()
		self.fps = 0.0  # общая частота кадров (для совместимости)
		self.afps = 0.0  # частота кадров канала A
		self.bfps = 0.0  # частота кадров канала B
		self.last_range_t = 0.0
		self.max_int16_span = 33000  # предельное окно по амплитуде
		self._y_span_smooth = None  # сглаженный спан по Y
		# окно отображения
		self.view_start = 0
		self.view_len = 0  # выставим когда узнаем длину буфера
		self.connect_t = 0.0
		self.last_frame_t = 0.0
		self.no_data_warned = False
		self.last_diag_t = 0.0
		# отслеживание общего приёма данных (по транспорту), чтобы не ругаться на "остановку" при отсутствии пар
		self._last_rx_seen = 0.0
		self._last_sample_ts: float | None = None
		# счётчики диагностики парирования (чтобы не падать при отсутствии mismatch)
		self._pair_mismatch_count = 0
		self._last_pair_mismatch_t = 0.0
		# интервалы можно настроить через переменные окружения
		try:
			self.diag_interval = float(os.getenv("BMI30_DIAG_INTERVAL", "10"))  # не спамить предупреждением чаще, чем раз в 10с
		except Exception:
			self.diag_interval = 10.0
		try:
			self.stop_warn_after = float(os.getenv("BMI30_STOP_WARN_AFTER", "5"))  # порог простоя для предупреждения, сек
		except Exception:
			self.stop_warn_after = 5.0
		self._instr = (
			"Инструкция: 1) Прошивка должна обрабатывать START_STREAM (0x20) и слать кадры vendor bulk на EP IN 0x83. "
			"SET_PROFILE (0x14) 1=200Гц / 2=300Гц используйте по необходимости (переключатель в GUI). "
			"Каждый кадр: заголовок 32 байта (magic 0xA55A LE), флаги 0x01 (ADC0) и 0x02 (ADC1); total_samples авто-фиксируется по первому рабочему кадру, payload = total_samples*2 байт. "
			"Тестовый кадр (flag 0x80) может быть один в начале и пропускается. 4) Проверьте права доступа (udev) если устройство не открывается. 5) Кнопка 1 в GUI запускает поток."
		)
		# статус: удержание сообщений, чтобы не мигали
		self._status_hold_text: str | None = None
		self._status_hold_until: float = 0.0
		self._last_status_text: str | None = None
		self._last_default_update_t: float = 0.0
		# stream (ленивый запуск по кнопке 1)
		self.stream = None
		self._connecting = False
		self.usb_retry_timer = QtCore.QTimer()
		self.usb_retry_timer.setInterval(1500)
		self.usb_retry_timer.timeout.connect(self._try_connect)
		self._set_status("Нажмите кнопку 1 для запуска потока (200 Гц по умолчанию)")
		# Сохраним порт info для power cycle без stream
		self.last_port_info = None
		# timer
		self.timer = QtWidgets.QApplication.instance().thread()  # dummy keep
		self.qtimer = QtCore.QTimer()
		# Настраиваемая частота GUI: BMI30_GUI_FPS (по умолчанию 16 FPS для снижения нагрузки)
		try:
			gui_fps = int(os.getenv("BMI30_GUI_FPS", "16"))
		except Exception:
			gui_fps = 16
		interval = max(10, int(1000 / gui_fps))  # минимум 10мс
		self.qtimer.setInterval(interval)
		self.qtimer.timeout.connect(self._tick)
		self.qtimer.start()
		# авто-кик при зависании
		self.auto_soft_kick = str(os.getenv("BMI30_AUTO_SOFT_KICK", "1")).lower() not in ("0","false","no")
		self.last_soft_kick_t = 0.0
		# нижняя панель: слева слайдеры, справа цифровые кнопки
		bottom = QtWidgets.QHBoxLayout()
		layout.addLayout(bottom)
		sliders_box = QtWidgets.QVBoxLayout()
		bottom.addLayout(sliders_box, 1)
		# Ось X уже зафиксирована как индексы семплов
		# Старт
		row_start = QtWidgets.QHBoxLayout()
		self.lbl_start_value = QtWidgets.QLabel(str(self.view_start))
		self.slider_start = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
		self.slider_start.setEnabled(True)
		self.slider_start.setMinimum(0)
		self.slider_start.setMaximum(max(0, self.max_samples - self.view_len))
		self.slider_start.setValue(self.view_start)
		lbl_start_name = QtWidgets.QLabel("Старт")
		row_start.addWidget(self.lbl_start_value)
		row_start.addWidget(self.slider_start, 1)
		row_start.addWidget(lbl_start_name)
		sliders_box.addLayout(row_start)
		# Семплов
		row_len = QtWidgets.QHBoxLayout()
		self.lbl_len_value = QtWidgets.QLabel(str(self.view_len))
		self.slider_len = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
		self.slider_len.setEnabled(True)
		self.slider_len.setMinimum(1)
		self.slider_len.setMaximum(self.max_samples)
		self.slider_len.setValue(self.view_len)
		lbl_len_name = QtWidgets.QLabel("Семплов")
		row_len.addWidget(self.lbl_len_value)
		row_len.addWidget(self.slider_len, 1)
		row_len.addWidget(lbl_len_name)
		sliders_box.addLayout(row_len)
		# цифровые кнопки справа
		self.state_file = os.path.join(os.path.dirname(__file__), 'bmi30_sel.json')
		self.sel_saved = self._load_saved_sel()
		self.num_group = QtWidgets.QButtonGroup()
		self.num_group.setExclusive(True)
		self.num_buttons = []
		btns_layout = QtWidgets.QHBoxLayout()
		for i in range(8):
			b = QtWidgets.QToolButton()
			b.setText(str(i))
			b.setCheckable(True)
			b.setAutoExclusive(True)
			b.setFixedSize(42, 42)
			b.setStyleSheet("QToolButton {background:#333; color:#ddd; border:1px solid #555; padding:2px;} "
				"QToolButton:checked {background:#ffb400; color:#000; border:2px solid #ffea8a; font-weight:bold;}" )
			self.num_group.addButton(b, i)
			btns_layout.addWidget(b)
			self.num_buttons.append(b)
		# без stretch — слайдеры тянутся до кнопок
		bottom.addLayout(btns_layout)
		# apply saved selection
		if self.sel_saved and 1 <= self.sel_saved <=7:
			self.num_buttons[self.sel_saved].setChecked(True)
		else:
			self.num_buttons[0].setChecked(True)
		self.num_group.idClicked.connect(self._num_clicked)
		self.win.closeEvent = self._on_close  # type: ignore
		# slider signals
		self.slider_start.valueChanged.connect(self._on_slider_start)
		self.slider_len.valueChanged.connect(self._on_slider_len)

		# Сбросим состояние слайдеров, чтобы при переподключениях они переинициализировались под свежую длину буфера
		self._reset_sliders()

		# Применим режим оси X после создания всех элементов
		self._apply_x_axis_mode()

		# Тестовый режим без устройства (нужно знать до автозапуска)
		try:
			_test_mode = str(os.getenv("BMI30_TEST_MODE", "0")).lower() not in ("0","false","no")
		except Exception:
			_test_mode = False

		# Автозапуск потока при старте приложения (по умолчанию включён)
		try:
			_autostart = str(os.getenv("BMI30_AUTOSTART", "1")).lower() not in ("0","false","no")
		except Exception:
			_autostart = True
		if _autostart and not _test_mode:
			# Автозапуск: запускаем тот режим, который выбран/восстановлен из bmi30_sel.json.
			# Это важно, чтобы (например) sel=5 действительно включал AVG_ROI(20) без ручного клика.
			self.view_mode = 0
			try:
				idx = int(self.num_group.checkedId())
			except Exception:
				idx = 3
			if idx != 0:
				self._num_clicked(idx)
		# режим отображения: 0=оба, 1=только канал 1, 2=только канал 2
		self.view_mode = 0
		
		# Тестовый режим без устройства
		if _test_mode:
			print("[TEST] Test mode enabled - simulating data")
			self._test_mode = True
			# Имитируем получение данных исходя из текущего профиля
			self.base_buf_len = self.expected_len_map.get(self.desired_profile, self.initial_expected)
			self.base_buf_len_bytes = self.base_buf_len * 2
			self.freq_hz = 200 if self.desired_profile == 1 else 300 if self.desired_profile == 2 else None
			# Заполняем тестовыми данными
			import math
			for i in range(self.base_buf_len):
				self.data0[i] = int(1000 * math.sin(2 * math.pi * i / 100))
				self.data1[i] = int(800 * math.cos(2 * math.pi * i / 150))
			self._set_status("Тестовый режим - данные сгенерированы")

	# (кнопки управления стримом удалены по ТЗ)

	def _find_cdc_port(self):
		# Explicit override: BMI30_CDC_PORT=COM9 or /dev/ttyACM0
		override = os.getenv("BMI30_CDC_PORT", "").strip()
		if override:
			return override

		# Windows: COM ports
		if sys.platform == "win32":
			try:
				from serial.tools import list_ports  # type: ignore
			except Exception:
				return None
			try:
				ports = [p.device for p in list_ports.comports() if getattr(p, "device", None)]
				ports = sorted(ports)
				return ports[0] if ports else None
			except Exception:
				return None

		# Linux / POSIX
		ports: list[str] = []
		try:
			ports.extend(sorted(glob.glob('/dev/serial/by-id/*')))
		except Exception:
			pass
		try:
			ports.extend(sorted(glob.glob('/dev/ttyACM*')))
		except Exception:
			pass
		try:
			ports.extend(sorted(glob.glob('/dev/ttyUSB*')))
		except Exception:
			pass
		# Dedup but keep order
		seen = set()
		unique = []
		for p in ports:
			if p not in seen:
				seen.add(p)
				unique.append(p)
		return unique[0] if unique else None

	def _send_soft_reset_via_cdc(self):
		try:
			port = self._find_cdc_port()
			if port is None:
				print("[RESET] CDC порт не найден, пропускаем SOFT_RESET")
				return
			with serial.Serial(port, 115200, timeout=1) as ser:
				ser.write(bytes([CMD_SOFT_RESET]))
				time.sleep(0.1)
				print("[RESET] SOFT_RESET sent via CDC")
		except Exception as e:
			print(f"[RESET] SOFT_RESET via CDC failed: {e}")

	def _reset_phase_splitter(self, reason: str = ""):
		"""Reset host-side even/odd splitter state.
		
		Why:
		- Stream restarts (STOP/START, reconnects) can re-anchor the device phase.
		- If we keep toggling across restarts, the displayed even/odd can appear to swap.
		
		We bias the *first* new frame after reset to be EVEN (par=0).
		"""
		try:
			# Make first non-duplicate return par=0:
			# toggle starts at 1, then toggles to 0 on first frame.
			self._phase_toggle_a = 1
			self._phase_toggle_b = 1
			self._phase_last_seq_a = None
			self._phase_last_seq_b = None
			self._phase_last_ts_a = None
			self._phase_last_ts_b = None
			self._phase_last_par_a = None
			self._phase_last_par_b = None
			self._phase_repeat_a = 0
			self._phase_repeat_b = 0
			self._phase_trace_n = 0
			# Also reset validity markers so GUI won't show stale phase buffers.
			self.seq0_even = None
			self.seq0_odd = None
			self.seq1_even = None
			self.seq1_odd = None
			# Clear buffers (optional but helps avoid confusing stale plots)
			try:
				self.data0_even[:] = 0
				self.data0_odd[:] = 0
				self.data1_even[:] = 0
				self.data1_odd[:] = 0
			except Exception:
				pass
			if getattr(self, 'phase_trace', False):
				msg = f"[PHASE_RESET] {reason}".strip()
				print(msg, flush=True)
		except Exception:
			pass

	def _switch_to_latest_mode(self):
		"""Возврат в режим LATEST (STREAM_MODE=0): 600 семплов, допускаются пропуски"""
		if self.stream is None:
			return
		# Если уходим из LOSSLESS_ROI, постараемся сохранить DC offset сразу (на выходе/перезапусках это критично)
		try:
			if getattr(self, 'stream_mode', 0) == 1:
				self._save_dc_offset(force=True)
		except Exception:
			pass
		
		try:
			# Отключаем DC removal если был включен
			if getattr(self, 'dc_removal_enabled', False):
				self.dc_removal_enabled = False
				# Восстанавливаем нормальную частоту GUI
				try:
					gui_fps = int(os.getenv("BMI30_GUI_FPS", "16"))
				except Exception:
					gui_fps = 16
				interval = max(10, int(1000 / gui_fps))
				self.qtimer.setInterval(interval)
				print(f"[LATEST] GUI восстановлен до {gui_fps} FPS")
			
			print("[LATEST] Переключение в режим LATEST (600 семплов, STREAM_MODE=0)...")
			
			# Остановка потока
			self.stream.send_cmd(CMD_STOP_STREAM, b"")
			time.sleep(0.05)
			print("[LATEST] STOP отправлен")
			
			# SET_WINDOWS: (0,0,0,0) - полный буфер
			windows_data = struct.pack('<HHHH', 0, 0, 0, 0)
			self.stream.send_cmd(CMD_SET_WINDOWS, windows_data)
			time.sleep(0.02)
			print("[LATEST] SET_WINDOWS(0, 0, 0, 0) отправлен")
			
			# SET_STREAM_MODE: 0 (LATEST)
			self.stream.send_cmd(CMD_SET_STREAM_MODE, b"\x00")
			time.sleep(0.02)
			print("[LATEST] SET_STREAM_MODE=0 отправлен")
			
			# SET_ASYNC_MODE: 1 (независимые A/B для быстрого режима)
			self.stream.send_cmd(CMD_ASYNC, b"\x01")
			time.sleep(0.02)
			print("[LATEST] SET_ASYNC_MODE=1 отправлен")
			
			# Сбросить параметры буфера для переинициализации с новым размером (600 семплов)
			with self.data_lock:
				self._reset_phase_splitter("switch_to_latest")
				self.base_buf_len = None
				self.base_buf_len_bytes = None
				self.freq_hz = None
				self._sliders_initialized = False
			print("[LATEST] Параметры буфера сброшены для переинициализации с 600 семплами")
			
			# Запуск потока
			self.stream.send_cmd(CMD_START_STREAM, b"")
			time.sleep(0.05)
			print("[LATEST] START отправлен")
			
			# Устанавливаем stream_mode=0
			self.stream_mode = 0
			print("[LATEST] Режим активирован: 600 семплов, STREAM_MODE=0")
			
		except Exception as e:
			print(f"[LATEST] Ошибка переключения: {e}")

	def _switch_to_lossless_roi(self):
		"""Переключение в режим LOSSLESS_ROI (STREAM_MODE=1): строгий FIFO, ROI 280..480 (200 семплов)"""
		# Если поток не запущен - запустить его сначала
		if self.stream is None:
			print("[LOSSLESS_ROI] Поток не запущен, запускаем...")
			self._set_status("Запуск потока для LOSSLESS_ROI...", hold_sec=1.0)
			self._activate_stream()
			time.sleep(0.5)  # Даём время на инициализацию
			if self.stream is None:
				print("[LOSSLESS_ROI] Не удалось запустить поток")
				self._set_status("Ошибка запуска потока", hold_sec=2.0)
				return
		
		try:
			# Отключаем DC removal если был включен (режим LOSSLESS_ROI без DC)
			if getattr(self, 'dc_removal_enabled', False):
				self.dc_removal_enabled = False
				# Восстанавливаем нормальную частоту GUI
				try:
					gui_fps = int(os.getenv("BMI30_GUI_FPS", "16"))
				except Exception:
					gui_fps = 16
				interval = max(10, int(1000 / gui_fps))
				self.qtimer.setInterval(interval)
				print(f"[LOSSLESS_ROI] GUI восстановлен до {gui_fps} FPS")
			
			print("[LOSSLESS_ROI] Переключение в режим LOSSLESS_ROI (200 семплов из окна 280..480)...")
			self._set_status("Переключение в LOSSLESS_ROI...", hold_sec=1.0)
			
			# Остановка потока
			self.stream.send_cmd(CMD_STOP_STREAM, b"")
			time.sleep(0.05)
			print("[LOSSLESS_ROI] STOP отправлен")
			
			# SET_WINDOWS: start0=280, len0=200, start1=0, len1=0 (только канал A)
			# Формат: u16 start0 + u16 len0 + u16 start1 + u16 len1 (little-endian)
			windows_data = struct.pack('<HHHH', 280, 200, 0, 0)
			self.stream.send_cmd(CMD_SET_WINDOWS, windows_data)
			time.sleep(0.02)
			print("[LOSSLESS_ROI] SET_WINDOWS(280, 200, 0, 0) отправлен")
			
			# SET_STREAM_MODE: 1 (LOSSLESS_ROI)
			self.stream.send_cmd(CMD_SET_STREAM_MODE, b"\x01")
			time.sleep(0.02)
			print("[LOSSLESS_ROI] SET_STREAM_MODE=1 отправлен")
			
			# SET_ASYNC_MODE: 0 (строгие пары A/B)
			self.stream.send_cmd(CMD_ASYNC, b"\x00")
			time.sleep(0.02)
			print("[LOSSLESS_ROI] SET_ASYNC_MODE=0 отправлен")
			
			# Запуск потока
			self.stream.send_cmd(CMD_START_STREAM, b"")
			time.sleep(0.05)
			print("[LOSSLESS_ROI] START отправлен")
			
			# Сбросить параметры буфера для переинициализации с новым размером (200 семплов)
			with self.data_lock:
				self._reset_phase_splitter("switch_to_lossless_roi")
				self.base_buf_len = None
				self.base_buf_len_bytes = None
				self.freq_hz = None
				self._sliders_initialized = False
			print("[LOSSLESS_ROI] Параметры буфера сброшены для переинициализации с 200 семплами")
			
			# Переключить на отображение обоих каналов
			self._set_view_mode(0)  # 0 = оба канала
			
			# Устанавливаем stream_mode=1 (накопление DC offset активно)
			self.stream_mode = 1
			
			# Снижаем частоту отрисовки GUI для уменьшения блокировок data_lock
			# В LOSSLESS_ROI не нужна быстрая отрисовка (5 FPS достаточно для накопления DC)
			self.qtimer.setInterval(200)  # 200 мс = 5 FPS
			print("[LOSSLESS_ROI] GUI снижен до 5 FPS для минимизации блокировок")
			
			self._set_status("LOSSLESS_ROI: 2 канала × 2 осциллограммы × 200 семплов", hold_sec=3.0)
			print("[LOSSLESS_ROI] Режим активирован: ROI 280..480, 200 семплов, 2 канала по 2 кривых, без пропусков")
			
		except Exception as e:
			print(f"[LOSSLESS_ROI] Ошибка переключения: {e}")
			self._set_status(f"Ошибка LOSSLESS_ROI: {e}", hold_sec=3.0)

	def _switch_to_dc_removal_mode(self):
		"""Переключение в режим LOSSLESS_ROI с удалением постоянной составляющей (DC removal)"""
		# Если уже в LOSSLESS_ROI (STREAM_MODE=1), не перезапускаем поток/окна — просто включаем отображение.
		# Это важно, чтобы не создавать ощущения "сброса" DC при переключениях 5↔6.
		if getattr(self, 'stream_mode', 0) != 1 or self.stream is None:
			# Сначала переключаемся в LOSSLESS_ROI режим (stream_mode=1 устанавливается там)
			self._switch_to_lossless_roi()
		
		# Включаем режим удаления DC (отображение скорректированных данных)
		self.dc_removal_enabled = True
		# AVG режимы управляются отдельно; здесь не трогаем
		
		self._set_status("DC Removal: вычитание постоянной составляющей, уровень = 32767", hold_sec=3.0)
		print("[DC_REMOVAL] Режим активирован: отображение скорректированных данных без DC")

	def _switch_to_avg_roi(self, avg_n: int = 20):
		"""Переключение в режим усреднения на устройстве (STREAM_MODE=2, AVG_ROI).
		Устройство усредняет ROI по N входным буферам и выдаёт усреднённые ROI-кадры.
		См. HOST_RPI.md: STOP -> SET_WINDOWS -> SET_STREAM_MODE(mode, avg_n) -> SET_ASYNC(0) -> START.
		"""
		# Если поток не запущен - запустить его сначала
		if self.stream is None:
			print("[AVG_ROI] Поток не запущен, запускаем...")
			self._set_status("Запуск потока для AVG_ROI...", hold_sec=1.0)
			self._activate_stream()
			time.sleep(0.5)
			if self.stream is None:
				print("[AVG_ROI] Не удалось запустить поток")
				self._set_status("Ошибка запуска потока", hold_sec=2.0)
				return
		try:
			avg_n = int(avg_n)
			if avg_n < 2:
				avg_n = 2
			if avg_n > 32:
				avg_n = 32
		except Exception:
			avg_n = 20

		try:
			print(f"[AVG_ROI] Переключение в AVG_ROI (STREAM_MODE=2, avg_n={avg_n})...")
			self._set_status(f"Переключение в AVG_ROI (avg_n={avg_n})...", hold_sec=1.0)

			# Остановка потока
			self.stream.send_cmd(CMD_STOP_STREAM, b"")
			time.sleep(0.05)
			print("[AVG_ROI] STOP отправлен")

			# SET_WINDOWS: ROI 280..479 (200) для обоих каналов (A и B)
			windows_data = struct.pack('<HHHH', 280, 200, 280, 200)
			self.stream.send_cmd(CMD_SET_WINDOWS, windows_data)
			time.sleep(0.02)
			print("[AVG_ROI] SET_WINDOWS(280,200, 280,200) отправлен")

			# SET_STREAM_MODE: 2 (AVG_ROI) + avg_n
			# В новой прошивке payload: <B mode> <B avg_n>
			self.stream.send_cmd(CMD_SET_STREAM_MODE, bytes([0x02, avg_n & 0xFF]))
			time.sleep(0.02)
			print("[AVG_ROI] SET_STREAM_MODE=2 (AVG_ROI) отправлен")

			# SET_ASYNC_MODE: 1 (независимые каналы A/B)
			self.stream.send_cmd(CMD_ASYNC, b"\x01")
			time.sleep(0.02)
			print("[AVG_ROI] SET_ASYNC_MODE=1 отправлен")

			# Запуск потока
			self.stream.send_cmd(CMD_START_STREAM, b"")
			time.sleep(0.05)
			print("[AVG_ROI] START отправлен")

			# Сброс параметров буфера для переинициализации
			with self.data_lock:
				self._reset_phase_splitter(f"switch_to_avg_roi avg_n={avg_n}")
				self.base_buf_len = None
				self.base_buf_len_bytes = None
				self.freq_hz = None
				self._sliders_initialized = False
			print("[AVG_ROI] Параметры буфера сброшены для переинициализации")

			# Показать оба канала
			self._set_view_mode(0)

			# stream_mode=2 (AVG_ROI)
			self.stream_mode = 2

			# Можно снизить FPS GUI: в AVG_ROI кадры редкие (для avg_n=20 ~10Гц на канал)
			self.qtimer.setInterval(200)  # 5 FPS
			self._set_status(f"AVG_ROI: усреднение на устройстве avg_n={avg_n}, ROI=200", hold_sec=3.0)
			print("[AVG_ROI] Режим активирован")
		except Exception as e:
			print(f"[AVG_ROI] Ошибка переключения: {e}")
			self._set_status(f"Ошибка AVG_ROI: {e}", hold_sec=3.0)

	def _seed_avg20_buffers_locked(self):
		"""Заполнить буферы AVG20 текущим кадром, чтобы при включении 6 не было 'разгона с нуля'.
		Ожидает, что data_lock уже удерживается.
		"""
		try:
			n = int(self.base_buf_len or 0)
			n = max(0, min(n, int(getattr(self, 'max_samples', 0) or 0)))
			if n <= 0:
				# сброс счётчиков, данные придут и сами заполнится
				self._avg0_even_pos = self._avg0_odd_pos = 0
				self._avg1_even_pos = self._avg1_odd_pos = 0
				self._avg0_even_cnt = self._avg0_odd_cnt = 0
				self._avg1_even_cnt = self._avg1_odd_cnt = 0
				return
			def _dc_out(raw_i32, dc_f32):
				out = raw_i32.astype(np.float32) - dc_f32.astype(np.float32) + np.float32(32767.0)
				if not getattr(self, 'debug_markers', False) and not getattr(self, 'no_invert', False):
					out = np.float32(32767.5) - (out - np.float32(32767.5))
				return out
			# Сформируем 4 кривые
			ch0e = _dc_out(self.data0_even[:n], self.dc_offset_ch0_even[:n])
			ch0o = _dc_out(self.data0_odd[:n], self.dc_offset_ch0_odd[:n])
			ch1e = _dc_out(self.data1_even[:n], self.dc_offset_ch1_even[:n])
			ch1o = _dc_out(self.data1_odd[:n], self.dc_offset_ch1_odd[:n])
			# Заполним все N кадров одним и тем же значением
			for i in range(int(self.avg20_nframes)):
				self._avg0_even[i, :n] = ch0e
				self._avg0_odd[i, :n] = ch0o
				self._avg1_even[i, :n] = ch1e
				self._avg1_odd[i, :n] = ch1o
				if n < self.max_samples:
					self._avg0_even[i, n:] = 0
					self._avg0_odd[i, n:] = 0
					self._avg1_even[i, n:] = 0
					self._avg1_odd[i, n:] = 0
			self._avg0_even_pos = self._avg0_odd_pos = 0
			self._avg1_even_pos = self._avg1_odd_pos = 0
			self._avg0_even_cnt = self._avg0_odd_cnt = int(self.avg20_nframes)
			self._avg1_even_cnt = self._avg1_odd_cnt = int(self.avg20_nframes)
		except Exception as e:
			print(f"[AVG20] seed ошибка: {e}")
	
	def _update_dc_offset_adaptive(self, data_arr, dc_arr, length):
		"""Адаптивное обновление DC offset: для каждого семпла корректировка на ±1"""
		try:
			for i in range(min(length, len(data_arr), len(dc_arr))):
				if data_arr[i] > dc_arr[i]:
					dc_arr[i] += self.dc_update_step  # Увеличиваем DC если данные больше
				elif data_arr[i] < dc_arr[i]:
					dc_arr[i] -= self.dc_update_step  # Уменьшаем DC если данные меньше
				# Если равны - не меняем
		except Exception as e:
			print(f"[DC_REMOVAL] Ошибка адаптивного обновления DC: {e}")
	
	def _save_dc_offset(self, force: bool = False):
		"""Сохранить текущие DC offset массивы в файл.
		Надёжно: атомарная запись + backup, чтобы при обрыве питания не получался битый .npz.
		"""
		try:
			# Подготовим данные под запись (копии — чтобы не держать data_lock/не читать мутирующие массивы)
			snap = {
				'dc_offset_ch0_even': np.array(self.dc_offset_ch0_even, dtype=np.float32, copy=True),
				'dc_offset_ch0_odd': np.array(self.dc_offset_ch0_odd, dtype=np.float32, copy=True),
				'dc_offset_ch1_even': np.array(self.dc_offset_ch1_even, dtype=np.float32, copy=True),
				'dc_offset_ch1_odd': np.array(self.dc_offset_ch1_odd, dtype=np.float32, copy=True),
				'timestamp': np.array([time.time()], dtype=np.float64),
				'max_samples': np.array([int(getattr(self, 'max_samples', 0) or 0)], dtype=np.int32),
				'stream_mode': np.array([int(getattr(self, 'stream_mode', 0) or 0)], dtype=np.int32),
			}
			# Пишем во временный файл и атомарно подменяем основной
			base_dir = os.path.dirname(self.dc_save_file) or '.'
			try:
				os.makedirs(base_dir, exist_ok=True)
			except Exception:
				pass
			# Важно: numpy добавляет расширение .npz автоматически, если его нет.
			# Поэтому временный файл ДОЛЖЕН заканчиваться на .npz, иначе получится "...tmp.npz",
			# а os.replace() будет пытаться заменить несуществующий путь.
			if str(self.dc_save_file).lower().endswith('.npz'):
				tmp_path = self.dc_save_file[:-4] + '.tmp.npz'
			else:
				tmp_path = self.dc_save_file + '.tmp.npz'
			try:
				if os.path.exists(tmp_path):
					os.remove(tmp_path)
			except Exception:
				pass
			np.savez_compressed(tmp_path, **snap)
			# Сохраним предыдущую версию как .bak (если есть)
			try:
				if os.path.exists(self.dc_save_file):
					os.replace(self.dc_save_file, self.dc_save_file_bak)
			except Exception:
				pass
			os.replace(tmp_path, self.dc_save_file)
			# best-effort fsync каталога (не везде поддерживается)
			try:
				fd = os.open(base_dir, os.O_DIRECTORY)
				try:
					os.fsync(fd)
				finally:
					os.close(fd)
			except Exception:
				pass
			# Вычисляем средние значения для логирования
			mean_ch0_even = np.mean(self.dc_offset_ch0_even[:200]) if len(self.dc_offset_ch0_even) >= 200 else 0
			mean_ch0_odd = np.mean(self.dc_offset_ch0_odd[:200]) if len(self.dc_offset_ch0_odd) >= 200 else 0
			mean_ch1_even = np.mean(self.dc_offset_ch1_even[:200]) if len(self.dc_offset_ch1_even) >= 200 else 0
			mean_ch1_odd = np.mean(self.dc_offset_ch1_odd[:200]) if len(self.dc_offset_ch1_odd) >= 200 else 0
			print(f"[DC_REMOVAL] DC offset массивы сохранены в {self.dc_save_file} (bak={self.dc_save_file_bak})")
			print(f"[DC_REMOVAL] Средние значения (первые 200 семплов): ch0_even={mean_ch0_even:.1f}, ch0_odd={mean_ch0_odd:.1f}, ch1_even={mean_ch1_even:.1f}, ch1_odd={mean_ch1_odd:.1f}")
		except Exception as e:
			print(f"[DC_REMOVAL] Ошибка сохранения DC offset: {e}")
	
	def _load_dc_offset(self):
		"""Загрузить сохраненные DC offset массивы из файла"""
		try:
			# кандидаты: основной, backup, и запасной (cwd) на случай запуска из другой папки
			candidates = []
			try:
				candidates.append(self.dc_save_file)
			except Exception:
				pass
			try:
				candidates.append(self.dc_save_file_bak)
			except Exception:
				pass
			try:
				cwd_alt = os.path.join(os.getcwd(), os.path.basename(self.dc_save_file))
				if cwd_alt not in candidates:
					candidates.append(cwd_alt)
			except Exception:
				pass
			loaded_from = None
			payload = None
			last_err = None
			for path in candidates:
				try:
					if not path or not os.path.exists(path):
						continue
					with np.load(path, allow_pickle=False) as data:
						need = ('dc_offset_ch0_even', 'dc_offset_ch0_odd', 'dc_offset_ch1_even', 'dc_offset_ch1_odd')
						if not all(k in data for k in need):
							raise ValueError(f"missing keys in {path}")
						payload = {k: np.array(data[k], dtype=np.float32, copy=True) for k in need}
						ts = float(np.array(data['timestamp'])[0]) if 'timestamp' in data else 0.0
						payload['timestamp'] = ts
					loaded_from = path
					break
				except Exception as e:
					last_err = e
					continue
			if payload is None:
				if last_err is not None:
					print(f"[DC_REMOVAL] Ошибка загрузки DC offset (candidates={candidates}): {last_err}")
				else:
					print(f"[DC_REMOVAL] Файл DC offset не найден (candidates={candidates}), используются нулевые массивы")
				return
			# Валидируем размеры и применяем только если всё ок
			for k in ('dc_offset_ch0_even', 'dc_offset_ch0_odd', 'dc_offset_ch1_even', 'dc_offset_ch1_odd'):
				arr = payload.get(k)
				if arr is None or arr.ndim != 1:
					raise ValueError(f"invalid array {k}")
			# Применяем (обрезаем/расширяем до max_samples)
			self.dc_offset_ch0_even[:] = 0
			self.dc_offset_ch0_odd[:] = 0
			self.dc_offset_ch1_even[:] = 0
			self.dc_offset_ch1_odd[:] = 0
			n = min(len(payload['dc_offset_ch0_even']), self.max_samples)
			self.dc_offset_ch0_even[:n] = payload['dc_offset_ch0_even'][:n]
			n = min(len(payload['dc_offset_ch0_odd']), self.max_samples)
			self.dc_offset_ch0_odd[:n] = payload['dc_offset_ch0_odd'][:n]
			n = min(len(payload['dc_offset_ch1_even']), self.max_samples)
			self.dc_offset_ch1_even[:n] = payload['dc_offset_ch1_even'][:n]
			n = min(len(payload['dc_offset_ch1_odd']), self.max_samples)
			self.dc_offset_ch1_odd[:n] = payload['dc_offset_ch1_odd'][:n]
			saved_time = float(payload.get('timestamp', 0.0) or 0.0)
			age_minutes = (time.time() - saved_time) / 60
			
			# Вычисляем средние значения для логирования
			mean_ch0_even = np.mean(self.dc_offset_ch0_even[:200]) if len(self.dc_offset_ch0_even) >= 200 else 0
			mean_ch0_odd = np.mean(self.dc_offset_ch0_odd[:200]) if len(self.dc_offset_ch0_odd) >= 200 else 0
			mean_ch1_even = np.mean(self.dc_offset_ch1_even[:200]) if len(self.dc_offset_ch1_even) >= 200 else 0
			mean_ch1_odd = np.mean(self.dc_offset_ch1_odd[:200]) if len(self.dc_offset_ch1_odd) >= 200 else 0
			
			print(f"[DC_REMOVAL] DC offset массивы загружены из {loaded_from} (возраст: {age_minutes:.1f} мин)")
			print(f"[DC_REMOVAL] Средние значения (первые 200 семплов): ch0_even={mean_ch0_even:.1f}, ch0_odd={mean_ch0_odd:.1f}, ch1_even={mean_ch1_even:.1f}, ch1_odd={mean_ch1_odd:.1f}")
		except Exception as e:
			print(f"[DC_REMOVAL] Ошибка загрузки DC offset: {e}")

	# --- numeric buttons persistence ---
	def _num_clicked(self, idx: int):
		if idx in (1, 2, 3):
			mode_map = {1: 1, 2: 2, 3: 0}  # 1: канал 1, 2: канал 2, 3: оба
			# Если поток не запущен - запустить его
			if self.stream is None and not self._connecting:
				self._activate_stream()
				# Дать время на инициализацию
				try:
					import time as _t
					_t.sleep(0.5)
				except Exception:
					pass
			# Переключить в режим LATEST (600 семплов, STREAM_MODE=0)
			if self.stream is not None:
				self._switch_to_latest_mode()
			# Выключить режим DC removal / AVG
			self.dc_removal_enabled = False
			self.avg20_enabled = False
			self._set_view_mode(mode_map[idx])
		elif idx == 4:
			# Кнопка 4: переключение в LOSSLESS_ROI режим (STREAM_MODE=1), показ 2 каналов × 2 осциллограммы × 200 семплов
			self.dc_removal_enabled = False  # Выключить DC removal
			self.avg20_enabled = False
			self._switch_to_lossless_roi()
		elif idx == 5:
			# Кнопка 5: DC compensation (LOSSLESS_ROI + DC removal).
			# Просили закрепить DC-компенсацию на "5", а следующие кнопки оставить под будущие алгоритмы.
			self.avg20_enabled = False
			self._switch_to_dc_removal_mode()
			self._set_status("DC compensation: LOSSLESS_ROI + DC removal", hold_sec=3.0)
		elif idx == 6:
			# Кнопка 6+: зарезервировано под будущие алгоритмы.
			# (оставляем как "пустую" команду, чтобы не ломать сохранение sel)
			self._set_status("Режим 6 зарезервирован под будущие алгоритмы", hold_sec=2.0)
		elif self.stream is not None and idx not in (1, 2, 3, 4, 5):
			try:
				self.stream.close()
			except Exception:
				pass
			self.stream = None
			self.base_buf_len = None
			self.base_buf_len_bytes = None
			self.freq_hz = None
			self.data0 = np.zeros(0, dtype=np.int16)
			self.data1 = np.zeros(0, dtype=np.int16)
			self.data0_even = self.data0
			self.data1_even = self.data1
			self.data0_odd = np.zeros(0, dtype=np.int16)
			self.data1_odd = np.zeros(0, dtype=np.int16)
			self.timestamps = np.zeros(0, dtype=np.float64)
			self._last_sample_ts = None
			self.view_len = 0
			self.slider_start.setEnabled(False)
			self.slider_len.setEnabled(False)
			self._set_status("Поток остановлен (нажмите 1,2 или 3 для запуска)", hold_sec=2.0)
		if idx == 0:
			try:
				if os.path.exists(self.state_file):
					os.remove(self.state_file)
			except Exception:
				pass
			print('[sel] 0 (не сохраняется)')
			return
		try:
			with open(self.state_file, 'w', encoding='utf-8') as f:
				json.dump({'sel': idx, 'ts': int(time.time())}, f)
			print(f'[sel] saved {idx}')
		except Exception as e:
			print('[sel] save err', e)

	def _load_saved_sel(self) -> int | None:
		try:
			with open(os.path.join(os.path.dirname(__file__), 'bmi30_sel.json'), 'r', encoding='utf-8') as f:
				obj = json.load(f)
			return int(obj.get('sel'))
		except Exception:
			return None

	def _reader_thread_func(self):
		"""Поток чтения USB: получает пакеты и заполняет data0/data1"""
		def _update_chan_seq(kind: str, seq_val: int):
			"""Обновить шаг/пропуски для одного канала (A/B) по seq."""
			try:
				seq_val = int(seq_val) & 0xFFFFFFFF
			except Exception:
				return
			if kind == 'A':
				last = self.last_seq_a
				hist = self._step_hist_a
				hist_n = '_step_hist_a_n'
				step_attr = 'step_a'
				gap_attr = 'gap_a'
				reord_attr = 'reord_a'
				last_attr = 'last_seq_a'
			else:
				last = self.last_seq_b
				hist = self._step_hist_b
				hist_n = '_step_hist_b_n'
				step_attr = 'step_b'
				gap_attr = 'gap_b'
				reord_attr = 'reord_b'
				last_attr = 'last_seq_b'
			if last is None:
				setattr(self, last_attr, seq_val)
				return
			delta = (seq_val - int(last)) & 0xFFFFFFFF
			# Учим шаг на небольших delta.
			if 0 < delta <= 16:
				try:
					hist[int(delta)] = int(hist.get(int(delta), 0)) + 1
					setattr(self, hist_n, int(getattr(self, hist_n, 0)) + 1)
				except Exception:
					pass
			if int(getattr(self, hist_n, 0)) >= 50:
				try:
					best_step = max(hist.items(), key=lambda kv: kv[1])[0]
					if 1 <= int(best_step) <= 16:
						setattr(self, step_attr, int(best_step))
				except Exception:
					pass
			step = int(getattr(self, step_attr, 1) or 1)
			# Пропуски считаем только для монотонного вперёд (delta small/normal). Остальное -> reord.
			if delta != step:
				if delta > step and delta < 0x80000000:
					if step > 0 and (delta % step) == 0:
						missed = max(1, int(delta // step) - 1)
					else:
						missed = 1
					setattr(self, gap_attr, int(getattr(self, gap_attr, 0)) + int(missed))
				else:
					setattr(self, reord_attr, int(getattr(self, reord_attr, 0)) + 1)
			setattr(self, last_attr, seq_val)
		def _phase_candidates(frame):
			"""Return candidate phase bits from frame fields."""
			out = {}
			try:
				seqv = int(getattr(frame, 'seq', 0))
				# seq may increment by 1, 2, or other step depending on firmware/mode.
				# Keep a few low bits as candidates.
				try:
					adc_id = int(getattr(frame, 'adc_id', 0))
					step = int(self.step_a if adc_id == 0 else self.step_b)
					if step < 1:
						step = 1
					# If step>1 (often step=2 with interleaved windows), seq LSB becomes constant;
					# using (seq//step)&1 restores a 50/50 alternating phase.
					out['seq_div_step_lsb'] = (seqv // step) & 1
				except Exception:
					pass
				out['seq'] = seqv & 1
				out['seq1'] = (seqv >> 1) & 1
				out['seq2'] = (seqv >> 2) & 1
				out['seq3'] = (seqv >> 3) & 1
			except Exception:
				pass
			try:
				tsv = int(getattr(frame, 'timestamp', 0))
				# Timestamp can be a stable cadence marker in AVG_ROI; try a few LSBs.
				out['ts'] = tsv & 1
				out['ts1'] = (tsv >> 1) & 1
				out['ts2'] = (tsv >> 2) & 1
				out['ts3'] = (tsv >> 3) & 1
			except Exception:
				pass
			try:
				rv = int(getattr(frame, 'reserved', 0))
				out['reserved'] = rv & 1
				out['reserved1'] = (rv >> 1) & 1
				out['reserved2'] = (rv >> 2) & 1
				out['reserved3'] = (rv >> 3) & 1
				out['reserved4'] = (rv >> 4) & 1
				out['reserved5'] = (rv >> 5) & 1
				out['reserved6'] = (rv >> 6) & 1
				out['reserved7'] = (rv >> 7) & 1
			except Exception:
				pass
			try:
				r2 = int(getattr(frame, 'reserved2', 0))
				out['reserved2_lsb'] = r2 & 1
				out['reserved2_1'] = (r2 >> 1) & 1
				out['reserved2_2'] = (r2 >> 2) & 1
				out['reserved2_3'] = (r2 >> 3) & 1
				out['reserved2_4'] = (r2 >> 4) & 1
				out['reserved2_5'] = (r2 >> 5) & 1
				out['reserved2_6'] = (r2 >> 6) & 1
				out['reserved2_7'] = (r2 >> 7) & 1
			except Exception:
				pass
			return out

		def _update_phase_key_stats(frame):
			"""Track which bit toggles like a true phase marker (alternating ~50/50)."""
			cands = _phase_candidates(frame)
			for name, bit in cands.items():
				st = self._phase_key_stats.get(name)
				if st is None:
					st = {'n': 0, 'ones': 0, 'toggles': 0, 'last': None, 'mismatch': 0}
					self._phase_key_stats[name] = st
				st['n'] += 1
				st['ones'] += 1 if int(bit) else 0
				if st['last'] is not None and int(bit) != int(st['last']):
					st['toggles'] += 1
				st['last'] = int(bit)

		def _update_phase_key_pair_consistency(a_frame, b_frame):
			"""In paired mode, phase marker should be identical for A and B of the same seq."""
			try:
				ca = _phase_candidates(a_frame)
				cb = _phase_candidates(b_frame)
				for name in set(ca.keys()).intersection(cb.keys()):
					if int(ca[name]) != int(cb[name]):
						st = self._phase_key_stats.get(name)
						if st is None:
							st = {'n': 0, 'ones': 0, 'toggles': 0, 'last': None, 'mismatch': 0}
							self._phase_key_stats[name] = st
						st['mismatch'] = int(st.get('mismatch', 0)) + 1
			except Exception:
				pass

		def _maybe_choose_phase_key():
			if self._phase_key_chosen is not None:
				return
			forced = getattr(self, 'phase_key', 'auto')
			if forced and forced != 'auto':
				self._phase_key_chosen = forced
				print(f"[PHASE_KEY] forced={self._phase_key_chosen}", flush=True)
				return
			# wait for enough samples
			min_n = 60
			bias = {
				# Best default when seq increments by step>1 (e.g. step=2): keeps even/odd balanced.
				'seq_div_step_lsb': 6.0,
				'seq': 5.0,
				'seq1': 3.0,
				'ts1': 1.5,
				'ts2': 1.0,
				'ts3': 0.5,
				'reserved2_lsb': 1.0,
				'reserved2_1': 1.0,
				'reserved': 0.0,
				'reserved1': 0.0,
			}
			best = None
			best_score = None
			for name, st in self._phase_key_stats.items():
				n = int(st.get('n', 0))
				if n < min_n:
					continue
				# phase marker must be consistent between channels in the same stereo pair
				if int(st.get('mismatch', 0)) > 0:
					continue
				ones = int(st.get('ones', 0))
				tog = int(st.get('toggles', 0))
				# prefer frequent toggling and ~50/50 distribution
				balance_penalty = abs(ones - (n / 2.0))
				score = float(tog) - float(balance_penalty) + float(bias.get(name, 0.0))
				# reject degenerate constant bits
				if ones == 0 or ones == n:
					continue
				if best_score is None or score > best_score:
					best_score = score
					best = name
			if best is not None:
				self._phase_key_chosen = best
				try:
					st = self._phase_key_stats.get(best, {})
					print(f"[PHASE_KEY] auto={best} n={st.get('n')} ones={st.get('ones')} toggles={st.get('toggles')}", flush=True)
				except Exception:
					print(f"[PHASE_KEY] auto={best}", flush=True)

		def _phase_toggle_bit(kind: str, frame):
			"""Stable even/odd splitter per channel.
			Default strategy: alternate by arrival order (per channel).
			Why: firmware may reuse the same seq for both phases; relying on seq/timestamp LSB can flip.
			We only suppress toggling on *true duplicates* (same seq AND same timestamp).
			On backward jumps/resets: re-sync.
			"""
			try:
				seqv = int(getattr(frame, 'seq', 0)) & 0xFFFFFFFF
			except Exception:
				seqv = 0
			try:
				tsv = int(getattr(frame, 'timestamp', 0)) & 0xFFFFFFFFFFFFFFFF
			except Exception:
				tsv = 0
			if kind == 'A':
				last_seq_attr = '_phase_last_seq_a'
				last_ts_attr = '_phase_last_ts_a'
				tog_attr = '_phase_toggle_a'
				last_par_attr = '_phase_last_par_a'
				rep_attr = '_phase_repeat_a'
			else:
				last_seq_attr = '_phase_last_seq_b'
				last_ts_attr = '_phase_last_ts_b'
				tog_attr = '_phase_toggle_b'
				last_par_attr = '_phase_last_par_b'
				rep_attr = '_phase_repeat_b'
			last_seq = getattr(self, last_seq_attr, None)
			last_ts = getattr(self, last_ts_attr, None)
			tog = int(getattr(self, tog_attr, 0) or 0)
			# Detect backward jump/resets using seq monotonicity when available
			if last_seq is not None:
				delta = (seqv - int(last_seq)) & 0xFFFFFFFF
				if delta >= 0x80000000:
					# backward jump: reorder/reset
					tog = 0
					setattr(self, tog_attr, tog)
					setattr(self, last_seq_attr, seqv)
					setattr(self, last_ts_attr, tsv)
					setattr(self, last_par_attr, None)
					setattr(self, rep_attr, 0)
					return tog
			# Suppress toggling only on true duplicates
			if last_seq is not None and last_ts is not None and int(last_seq) == int(seqv) and int(last_ts) == int(tsv):
				par = int(tog)
			else:
				tog ^= 1
				par = int(tog)
				setattr(self, tog_attr, tog)
			setattr(self, last_seq_attr, seqv)
			setattr(self, last_ts_attr, tsv)
			# Track repeats (should normally alternate)
			lp = getattr(self, last_par_attr, None)
			if lp is not None and int(lp) == int(par):
				setattr(self, rep_attr, int(getattr(self, rep_attr, 0)) + 1)
			else:
				setattr(self, rep_attr, 0)
			setattr(self, last_par_attr, int(par))
			# Optional tracing
			try:
				if getattr(self, 'phase_trace', False) and int(getattr(self, '_phase_trace_n', 0)) < int(getattr(self, 'phase_trace_limit', 0)):
					self._phase_trace_n = int(getattr(self, '_phase_trace_n', 0)) + 1
					rep = int(getattr(self, rep_attr, 0))
					if rep >= 1:
						print(f"[PHASE_ANOM] {kind} rep={rep} par={par} seq={seqv} ts={tsv} last_seq={last_seq} last_ts={last_ts}", flush=True)
					elif (self._phase_trace_n % 50) == 0:
						print(f"[PHASE] {kind} par={par} seq={seqv} ts={tsv}", flush=True)
			except Exception:
				pass
			return par

		def _phase_bit(kind: str, frame):
			"""Compute phase bit (0/1) using the configured splitter."""
			key = (self._phase_key_chosen or getattr(self, 'phase_key', 'reserved2') or 'reserved2').strip().lower()
			# Preferred/required path: phase = reserved2 & 1
			if key in ('reserved2', 'reserved2_lsb', 'r2', 'reserved2&1'):
				try:
					return int(getattr(frame, 'reserved2', 0)) & 1
				except Exception:
					# If the field is missing for some reason, fall back to 0 (EVEN)
					return 0
			# Legacy/debug paths are only allowed if explicitly enabled
			allow_legacy = str(os.getenv('BMI30_PHASE_ALLOW_LEGACY', '0')).lower() not in ('0','false','no')
			if not allow_legacy:
				try:
					return int(getattr(frame, 'reserved2', 0)) & 1
				except Exception:
					return 0
			if key == 'toggle':
				return int(_phase_toggle_bit(kind, frame))
			# legacy/diagnostic path: auto-select a toggling bit from frame fields
			try:
				_update_phase_key_stats(frame)
			except Exception:
				pass
			try:
				_maybe_choose_phase_key()
			except Exception:
				pass
			key = (self._phase_key_chosen or getattr(self, 'phase_key', 'auto') or 'auto').strip().lower()
			cands = _phase_candidates(frame)
			if key in cands:
				return int(cands[key])
			if 'seq_div_step_lsb' in cands:
				return int(cands.get('seq_div_step_lsb', 0))
			return int(cands.get('seq', 0))

		def _is_true_duplicate(kind: str, frame) -> bool:
			"""Return True if frame repeats exactly (same seq AND same timestamp).
			
			Used to drop duplicate frames before updating even/odd buffers.
			In AVG_ROI mode the device/transport can emit repeats; treating those as new
			frames makes one phase look stale (and previously could trigger PHASE_ANOM).
			"""
			try:
				seqv = int(getattr(frame, 'seq', 0)) & 0xFFFFFFFF
			except Exception:
				seqv = 0
			try:
				tsv = int(getattr(frame, 'timestamp', 0)) & 0xFFFFFFFFFFFFFFFF
			except Exception:
				tsv = 0
			if kind == 'A':
				last_seq_attr = '_dup_last_seq_a'
				last_ts_attr = '_dup_last_ts_a'
				cnt_attr = '_dup_drop_a'
			else:
				last_seq_attr = '_dup_last_seq_b'
				last_ts_attr = '_dup_last_ts_b'
				cnt_attr = '_dup_drop_b'
			last_seq = getattr(self, last_seq_attr, None)
			last_ts = getattr(self, last_ts_attr, None)
			is_dup = (
				last_seq is not None
				and last_ts is not None
				and int(last_seq) == int(seqv)
				and int(last_ts) == int(tsv)
			)
			setattr(self, last_seq_attr, seqv)
			setattr(self, last_ts_attr, tsv)
			if is_dup:
				setattr(self, cnt_attr, int(getattr(self, cnt_attr, 0)) + 1)
			return bool(is_dup)

		print("[READER] Thread started", flush=True)
		while self.reader_running:
			if self.stream is None:
				time.sleep(0.1)
				continue
			try:
				pair = self.stream.get_stereo(timeout=0.1)
				if not pair:
					continue

				# Normalize: get_stereo returns either (a,b) pair (old behavior) or
				# ('A', frame) / ('B', frame) when independent mode active.
				if isinstance(pair, tuple) and isinstance(pair[0], str) and pair[0] in ('A', 'B'):
					chan, frame = pair
					if chan == 'A':
						a = frame; b = None
					else:
						a = None; b = frame
				else:
					a, b = pair

				# Help auto-detect phase key: in paired mode, enforce A/B consistency for candidates.
				# (Only relevant when phase_key=='auto'; default is stable 'toggle'.)
				if (getattr(self, 'phase_key', 'toggle') == 'auto') and a is not None and b is not None:
					_update_phase_key_pair_consistency(a, b)

				# Пер-канальные seq/gap: обновляем всегда (даже если пары склеены relaxed-режимом).
				try:
					if a is not None and getattr(a, 'seq', None) is not None:
						_update_chan_seq('A', int(a.seq))
					if b is not None and getattr(b, 'seq', None) is not None:
						_update_chan_seq('B', int(b.seq))
				except Exception:
					pass

				# При необходимости можно явно сбросить накопленную очередь ассемблера (независимый режим),
				# чтобы не было задержки из старых кадров. Выключено по умолчанию, включается env BMI30_FLUSH_ASM_QUEUE=1.
				if getattr(self, 'flush_asm_queue', False) and getattr(self, 'independent_channels', False):
					try:
						asm = getattr(self.stream, 'asm', None)
						# independent: trim qA/qB; paired: trim q
						if asm is not None and getattr(asm, 'independent', False):
							for tag in ('qA', 'qB'):
								q = getattr(asm, tag, None)
								if q is None or not hasattr(q, 'qsize'):
									continue
								qs = q.qsize()
								if qs <= 1:
									continue
								last_item = None
								while True:
									try:
										item = q.get_nowait()
										last_item = item
									except Exception:
										break
								if last_item is not None:
									try:
										q.put_nowait(last_item)
									except Exception:
										pass
								print(f"[ASM_FLUSH] {tag} trimmed (was {qs}), kept latest. new={q.qsize() if hasattr(q,'qsize') else '?'}", flush=True)
						else:
							q = getattr(asm, 'q', None)
							if q is not None and hasattr(q, 'qsize'):
								qs = q.qsize()
								if qs > 0:
									last_item = None
									while True:
										try:
											item = q.get_nowait()
											last_item = item
										except Exception:
											break
									if last_item is not None:
										try:
											q.put_nowait(last_item)
										except Exception:
											pass
									print(f"[ASM_FLUSH] queue trimmed (was {qs}), kept latest. new_q={q.qsize() if hasattr(q,'qsize') else '?'}", flush=True)
					except Exception:
						pass

				ch0 = np.frombuffer(a.payload, dtype='<u2').astype(np.int32) if a is not None else None
				ch1 = np.frombuffer(b.payload, dtype='<u2').astype(np.int32) if b is not None else None
				
				# --- diagnostics: compute sequence/timestamps early to detect pairing issues ---
				if a is not None and b is not None:
					try:
						seq_a = int(a.seq)
						seq_b = int(b.seq)
					except Exception:
						seq_a = getattr(a, 'seq', None)
						seq_b = getattr(b, 'seq', None)
					try:
						ts_a = a.timestamp / 1_000_000.0
						ts_b = b.timestamp / 1_000_000.0
					except Exception:
						ts_a = ts_b = None

					# If mismatch, record diagnostics and optionally capture to file
					if seq_a != seq_b:
						self._pair_mismatch_count = int(getattr(self, '_pair_mismatch_count', 0)) + 1
						nowp = time.time()
						last_pair_t = float(getattr(self, '_last_pair_mismatch_t', 0.0))
						if (self._pair_mismatch_count % 200) == 0 or (nowp - last_pair_t) > 4.0:
							asm_info = ''
							try:
								asm = getattr(self.stream, 'asm', None)
								if asm is not None:
									asm_info = f" ts_pairs={getattr(asm, '_ts_pair_count', 0)} seq_neigh_pairs={getattr(asm, '_seq_neighbor_pairs', 0)} q={asm.q.qsize()}"
							except Exception:
								asm_info = ''
							print(
								f"[PAIR_MISMATCH] count={self._pair_mismatch_count} last seqA={seq_a} seqB={seq_b} "
								f"lenA={len(ch0)} lenB={len(ch1)} flagsA={getattr(a,'flags',None)} flagsB={getattr(b,'flags',None)}{asm_info}",
								flush=True,
							)
							self._last_pair_mismatch_t = nowp
						# capture to file if configured
						try:
							if getattr(self, 'capture_diag_path', None):
								if self._capture_diag_fp is None:
									self._capture_diag_fp = open(self.capture_diag_path, 'a', encoding='utf-8', errors='replace')
									self._capture_diag_started = time.time()
									self._capture_diag_lines = 0
									self._capture_diag_fp.write('#ts_unix,seqA,seqB,lenA,lenB,flagsA,flagsB,tsA,tsB,asm_ts_pairs,asm_seq_neigh_pairs,last_stat_hex\n')
									self._capture_diag_fp.flush()
								# write one line per mismatch while capture window open
								if self._capture_diag_fp is not None and (time.time() - self._capture_diag_started) <= float(self.capture_diag_seconds) and self._capture_diag_lines < int(self.capture_diag_limit):
									try:
										last_stat = getattr(self.stream, 'last_stat', None)
										stat_hex = last_stat.hex() if isinstance(last_stat, (bytes, bytearray)) else ''
										asm = getattr(self.stream, 'asm', None)
										ts_pairs = getattr(asm, '_ts_pair_count', 0) if asm else 0
										neigh = getattr(asm, '_seq_neighbor_pairs', 0) if asm else 0
										line = f"{time.time():.6f},{seq_a},{seq_b},{len(ch0)},{len(ch1)},{getattr(a,'flags',None)},{getattr(b,'flags',None)},{ts_a if ts_a is not None else ''},{ts_b if ts_b is not None else ''},{ts_pairs},{neigh},\"{stat_hex}\"\n"
										self._capture_diag_fp.write(line)
										self._capture_diag_fp.flush()
										self._capture_diag_lines += 1
									except Exception:
										pass
								# close if time or limit exceeded
								try:
									if self._capture_diag_fp is not None and ((time.time() - self._capture_diag_started) > float(self.capture_diag_seconds) or self._capture_diag_lines >= int(self.capture_diag_limit)):
										try:
											self._capture_diag_fp.close()
										except Exception:
											pass
										self._capture_diag_fp = None
								except Exception:
									pass
						except Exception:
							pass

				# Лог размеров кадров (опционально)
				if getattr(self, 'log_frame_len', False) and getattr(self, '_log_frame_len_count', 0) < getattr(self, 'log_frame_len_limit', 0):
					try:
						seq_a = getattr(a, 'seq', None) if a is not None else None
						seq_b = getattr(b, 'seq', None) if b is not None else None
						len_a = len(ch0) if ch0 is not None else 0
						len_b = len(ch1) if ch1 is not None else 0
						print(f"[FRAME_LEN] seqA={seq_a} seqB={seq_b} lenA={len_a} lenB={len_b} flagsA={getattr(a,'flags',None) if a is not None else None} flagsB={getattr(b,'flags',None) if b is not None else None}", flush=True)
						self._log_frame_len_count += 1
					except Exception:
						pass

				# Трассировка содержимого по блокам (каждые 100 семплов) для диагностики мигания хвоста
				if getattr(self, 'chunk_trace', False) and getattr(self, '_chunk_trace_count', 0) < getattr(self, 'chunk_trace_limit', 0):
					try:
						def _chunk_stats(arr, tag):
							if arr is None or len(arr) == 0:
								return f"{tag}:None"
							# группируем по 100 семплов (12 блоков для 1200)
							chunks = arr.reshape(-1, 100) if len(arr) % 100 == 0 else np.array_split(arr, max(1, len(arr)//100))
							parts = []
							for idx, c in enumerate(chunks):
								try:
									parts.append(f"{idx*100}:{int(np.min(c))}-{int(np.max(c))}")
								except Exception:
									parts.append(f"{idx*100}:err")
							return f"{tag}=[" + ' '.join(parts) + "]"
						msg_a = _chunk_stats(ch0, "A") if ch0 is not None else "A:None"
						msg_b = _chunk_stats(ch1, "B") if ch1 is not None else "B:None"
						print(f"[CHUNK] {msg_a} {msg_b}", flush=True)
						self._chunk_trace_count += 1
					except Exception:
						pass

				# Мониторинг редких сбоев по конкретному семплу (по умолчанию индекс 300)
				if getattr(self, 'monitor_sample', False):
					idx = max(0, int(getattr(self, 'monitor_index', 300)))
					try:
						if ch0 is not None and len(ch0) > idx:
							val = int(ch0[idx])
							if self._last_mon_a is None:
								self._last_mon_a = val
							elif val != self._last_mon_a:
								print(f"[MON] A idx={idx} changed {self._last_mon_a}->{val} seqA={getattr(a,'seq',None)} lenA={len(ch0)}", flush=True)
								self._last_mon_a = val
						if ch1 is not None and len(ch1) > idx:
							valb = int(ch1[idx])
							if self._last_mon_b is None:
								self._last_mon_b = valb
							elif valb != self._last_mon_b:
								print(f"[MON] B idx={idx} changed {self._last_mon_b}->{valb} seqB={getattr(b,'seq',None)} lenB={len(ch1)}", flush=True)
								self._last_mon_b = valb
					except Exception:
						pass

				# Инициализация/обновление base_buf_len при первом кадре или смене длины
				first_len = len(ch0) if ch0 is not None else (len(ch1) if ch1 is not None else None)
				if first_len is not None:
					with self.data_lock:
						if (self.base_buf_len is None) or (self.base_buf_len != first_len):
							old_len = self.base_buf_len
							self.base_buf_len = first_len
							self.base_buf_len_bytes = self.base_buf_len * 2
							self._sliders_initialized = False
							if getattr(self, 'desired_profile', None) == 1:
								self.freq_hz = 200
							elif getattr(self, 'desired_profile', None) == 2:
								self.freq_hz = 300
							else:
								self.freq_hz = None
							print(f"[READER] Buffer size changed: {old_len} -> {self.base_buf_len} семплов, freq={self.freq_hz}Hz", flush=True)
				
				# Копируем данные в shared buffers (поддержка независимых каналов)
				with self.data_lock:
					current_time = time.time()
					if ch0 is not None:
						# Drop exact duplicate frames (same seq+ts) to avoid stale even/odd artifacts
						try:
							if a is not None and _is_true_duplicate('A', a):
								ch0 = None
						except Exception:
							pass
					if ch0 is not None:
						# even/odd selection by detected phase bit
						try:
							par = _phase_bit('A', a) if a is not None else 0
							seqv = int(getattr(a, 'seq', 0)) if a is not None else 0
						except Exception:
							par = 0
							seqv = 0
						tgt = self.data0_odd if par else self.data0_even
						tgt[:len(ch0)] = ch0
						if len(ch0) < self.max_samples:
							tgt[len(ch0):] = 0
						if par:
							self.seq0_odd = seqv
						else:
							self.seq0_even = seqv
						# Per-parity rhythm counters + timestamp cadence
						try:
							ts = int(getattr(a, 'timestamp', 0)) if a is not None else None
						except Exception:
							ts = None
						if par:
							self.frames_a_odd += 1
							if ts is not None and self._ts_a_odd is not None:
								self._dt_a_odd = float((ts - int(self._ts_a_odd)) / 1_000_000.0)
							if ts is not None:
								self._ts_a_odd = ts
						else:
							self.frames_a_even += 1
							if ts is not None and self._ts_a_even is not None:
								self._dt_a_even = float((ts - int(self._ts_a_even)) / 1_000_000.0)
							if ts is not None:
								self._ts_a_even = ts
						
						# Адаптивное обновление DC offset (пошагово, по семплам)
						# По умолчанию включено для STREAM_MODE=1 (LOSSLESS_ROI) и 2 (AVG_ROI)
						if int(getattr(self, 'stream_mode', 0) or 0) in set(getattr(self, 'dc_adapt_modes', {1, 2})) and len(ch0) > 0:
							if par:
								self._update_dc_offset_adaptive(ch0, self.dc_offset_ch0_odd, len(ch0))
							else:
								self._update_dc_offset_adaptive(ch0, self.dc_offset_ch0_even, len(ch0))
							# AVG20: копим уже DC-скорректированные значения для отображения
							if getattr(self, 'avg20_enabled', False):
								try:
									if par:
										dc_view = self.dc_offset_ch0_odd[:len(ch0)]
										out = ch0.astype(np.float32) - dc_view.astype(np.float32) + np.float32(32767.0)
										if not getattr(self, 'debug_markers', False) and not getattr(self, 'no_invert', False):
											out = np.float32(32767.5) - (out - np.float32(32767.5))
										self._avg0_odd[self._avg0_odd_pos, :len(out)] = out
										if len(out) < self.max_samples:
											self._avg0_odd[self._avg0_odd_pos, len(out):] = 0
										self._avg0_odd_pos = (self._avg0_odd_pos + 1) % self.avg20_nframes
										self._avg0_odd_cnt = min(self.avg20_nframes, self._avg0_odd_cnt + 1)
									else:
										dc_view = self.dc_offset_ch0_even[:len(ch0)]
										out = ch0.astype(np.float32) - dc_view.astype(np.float32) + np.float32(32767.0)
										if not getattr(self, 'debug_markers', False) and not getattr(self, 'no_invert', False):
											out = np.float32(32767.5) - (out - np.float32(32767.5))
										self._avg0_even[self._avg0_even_pos, :len(out)] = out
										if len(out) < self.max_samples:
											self._avg0_even[self._avg0_even_pos, len(out):] = 0
										self._avg0_even_pos = (self._avg0_even_pos + 1) % self.avg20_nframes
										self._avg0_even_cnt = min(self.avg20_nframes, self._avg0_even_cnt + 1)
								except Exception as e:
									print(f"[AVG20] ch0 накопление ошибка: {e}")
					
					if ch1 is not None:
						try:
							if b is not None and _is_true_duplicate('B', b):
								ch1 = None
						except Exception:
							pass
					if ch1 is not None:
						try:
							par = _phase_bit('B', b) if b is not None else 0
							seqv = int(getattr(b, 'seq', 0)) if b is not None else 0
						except Exception:
							par = 0
							seqv = 0
						tgt = self.data1_odd if par else self.data1_even
						tgt[:len(ch1)] = ch1
						if len(ch1) < self.max_samples:
							tgt[len(ch1):] = 0
						if par:
							self.seq1_odd = seqv
						else:
							self.seq1_even = seqv
						# Per-parity rhythm counters + timestamp cadence
						try:
							ts = int(getattr(b, 'timestamp', 0)) if b is not None else None
						except Exception:
							ts = None
						if par:
							self.frames_b_odd += 1
							if ts is not None and self._ts_b_odd is not None:
								self._dt_b_odd = float((ts - int(self._ts_b_odd)) / 1_000_000.0)
							if ts is not None:
								self._ts_b_odd = ts
						else:
							self.frames_b_even += 1
							if ts is not None and self._ts_b_even is not None:
								self._dt_b_even = float((ts - int(self._ts_b_even)) / 1_000_000.0)
							if ts is not None:
								self._ts_b_even = ts
						
						# Адаптивное обновление DC offset
						if int(getattr(self, 'stream_mode', 0) or 0) in set(getattr(self, 'dc_adapt_modes', {1, 2})) and len(ch1) > 0:
							if par:
								self._update_dc_offset_adaptive(ch1, self.dc_offset_ch1_odd, len(ch1))
							else:
								self._update_dc_offset_adaptive(ch1, self.dc_offset_ch1_even, len(ch1))
							# AVG20: копим уже DC-скорректированные значения для отображения
							if getattr(self, 'avg20_enabled', False):
								try:
									if par:
										dc_view = self.dc_offset_ch1_odd[:len(ch1)]
										out = ch1.astype(np.float32) - dc_view.astype(np.float32) + np.float32(32767.0)
										if not getattr(self, 'debug_markers', False) and not getattr(self, 'no_invert', False):
											out = np.float32(32767.5) - (out - np.float32(32767.5))
										self._avg1_odd[self._avg1_odd_pos, :len(out)] = out
										if len(out) < self.max_samples:
											self._avg1_odd[self._avg1_odd_pos, len(out):] = 0
										self._avg1_odd_pos = (self._avg1_odd_pos + 1) % self.avg20_nframes
										self._avg1_odd_cnt = min(self.avg20_nframes, self._avg1_odd_cnt + 1)
									else:
										dc_view = self.dc_offset_ch1_even[:len(ch1)]
										out = ch1.astype(np.float32) - dc_view.astype(np.float32) + np.float32(32767.0)
										if not getattr(self, 'debug_markers', False) and not getattr(self, 'no_invert', False):
											out = np.float32(32767.5) - (out - np.float32(32767.5))
										self._avg1_even[self._avg1_even_pos, :len(out)] = out
										if len(out) < self.max_samples:
											self._avg1_even[self._avg1_even_pos, len(out):] = 0
										self._avg1_even_pos = (self._avg1_even_pos + 1) % self.avg20_nframes
										self._avg1_even_cnt = min(self.avg20_nframes, self._avg1_even_cnt + 1)
								except Exception as e:
									print(f"[AVG20] ch1 накопление ошибка: {e}")
					
					# Сохранять DC offset в файл каждые 10 минут (при STREAM_MODE=1)
					if getattr(self, 'stream_mode', 0) == 1 and (current_time - self.dc_last_save >= self.dc_save_interval):
						self._save_dc_offset()
						self.dc_last_save = current_time

					# Phase sync diagnostics: expected even ~= -odd (after DC removal) at lag=0
					if getattr(self, 'phase_diag', False) and self.base_buf_len is not None:
						self._phase_diag_frames += 1
						if self._phase_diag_frames % max(1, int(getattr(self, 'phase_diag_every', 50))) == 0:
							try:
								maxlag = max(0, int(getattr(self, 'phase_diag_maxlag', 20)))
							except Exception:
								maxlag = 20
							def _best_lag(even_arr: np.ndarray, odd_arr: np.ndarray):
								# compare even to inverted odd; return (corr0, best_lag, best_corr)
								if even_arr.size == 0 or odd_arr.size == 0:
									return (None, None, None)
								a0 = even_arr.astype(np.float64)
								b0 = odd_arr.astype(np.float64)
								# DC removal
								a0 = a0 - float(np.mean(a0))
								b0 = b0 - float(np.mean(b0))
								na = float(np.linalg.norm(a0)) + 1e-12
								nb = float(np.linalg.norm(b0)) + 1e-12
								corr0 = float(np.dot(a0, b0) / (na * nb))
								# best lag for a0 ~ -b0 (maximize corr with inverted b)
								best_lag = 0
								best_corr = float(np.dot(a0, -b0) / (na * nb))
								if maxlag > 0 and a0.size > (2 * maxlag + 4):
									for lag in range(-maxlag, maxlag + 1):
										if lag == 0:
											continue
										if lag > 0:
											a = a0[lag:]
											b = -b0[:-lag]
										else:
											a = a0[:lag]
											b = -b0[-lag:]
										nna = float(np.linalg.norm(a)) + 1e-12
										nnb = float(np.linalg.norm(b)) + 1e-12
										c = float(np.dot(a, b) / (nna * nnb))
										if c > best_corr:
											best_corr = c
											best_lag = lag
								return (corr0, best_lag, best_corr)
							try:
								n = int(self.base_buf_len)
								if n > 0:
									ch0e = self.data0_even[:n]
									ch0o = self.data0_odd[:n]
									ch1e = self.data1_even[:n]
									ch1o = self.data1_odd[:n]
									c0, lag0, bc0 = _best_lag(ch0e, ch0o)
									c1, lag1, bc1 = _best_lag(ch1e, ch1o)
									print(
										"[PHASE] "
										f"CH0 se={self.seq0_even} so={self.seq0_odd} corr0={c0:.3f} best_lag={lag0} best_corr={bc0:.3f} | "
										f"CH1 se={self.seq1_even} so={self.seq1_odd} corr0={c1:.3f} best_lag={lag1} best_corr={bc1:.3f}",
										flush=True,
									)
							except Exception:
								pass
					# Отладочные маркеры: линейный рамп по всему буферу [0..65535]
					if getattr(self, 'debug_markers', False):
						try:
							buf_len = len(ch0) if ch0 is not None else (len(ch1) if ch1 is not None else 0)
							buf_len = min(buf_len, self.max_samples)
							if buf_len > 0:
								vals = np.linspace(0, 65535, num=buf_len, dtype=np.int32)
								self.data0_even[:buf_len] = vals
								self.data0_odd[:buf_len] = vals
								self.data1_even[:buf_len] = vals
								self.data1_odd[:buf_len] = vals
								if buf_len < self.max_samples:
									self.data0_even[buf_len:] = 0
									self.data0_odd[buf_len:] = 0
									self.data1_even[buf_len:] = 0
									self.data1_odd[buf_len:] = 0
								try:
									idx_min = int(np.argmin(self.data0[:buf_len]))
									idx_max = int(np.argmax(self.data0[:buf_len]))
									print(f"[MARK] buf_len={buf_len} min_idx={idx_min} max_idx={idx_max}")
								except Exception:
									pass
						except Exception:
							pass
					if self.freq_hz:
						dt = 1.0 / self.freq_hz
						# choose available frame to compute timestamps
						src = a if a is not None else b
						how_many = len(ch0) if ch0 is not None else (len(ch1) if ch1 is not None else 0)
						if how_many > 0 and getattr(src, 'timestamp', None) is not None:
							ts_start = src.timestamp / 1_000_000.0
							ts = ts_start + np.arange(how_many) * dt
							self.timestamps[:len(ts)] = ts
							if len(ts) < self.max_samples:
								self.timestamps[len(ts):] = 0.0
					self.last_frame_t = time.time()
					# Обновим счетчики и проверим разрывы в режиме пар (если оба канала пришли одновременно)
					if a is not None and b is not None:
						if not getattr(self.stream, 'asm', None) or not getattr(self.stream.asm, 'independent', False):
							# ВАЖНО: если seqA!=seqB, это не «пропуск пар», а рассинхрон/независимый счётчик.
							try:
								pair_match = int(a.seq) == int(b.seq)
							except Exception:
								pair_match = False
							if pair_match:
								# Считаем gap только для "настоящих" стерео-пар (seqA==seqB).
								if self.last_seq is None:
									self.last_seq = a.seq
								else:
									delta = (int(a.seq) - int(self.last_seq)) & 0xFFFFFFFF
									if 0 < delta <= 16:
										try:
											self._seq_step_hist[int(delta)] = int(self._seq_step_hist.get(int(delta), 0)) + 1
											self._seq_step_hist_n = int(self._seq_step_hist_n) + 1
										except Exception:
											pass
									if int(getattr(self, '_seq_step_hist_n', 0)) >= 50:
										try:
											best_step = max(self._seq_step_hist.items(), key=lambda kv: kv[1])[0]
											if 1 <= int(best_step) <= 16:
												self.seq_step = int(best_step)
										except Exception:
											pass
									step = int(getattr(self, 'seq_step', 1) or 1)
									if delta != step:
										missed = 0
										if delta > step and delta < 0x80000000:
											if step > 0 and (delta % step) == 0:
												missed = max(1, int(delta // step) - 1)
											else:
												missed = 1
											self.gap_count += int(missed)
											self._gap_log_pending += int(missed)
										else:
											self.seq_reorder_count = int(getattr(self, 'seq_reorder_count', 0)) + 1
										# GAP-capture (опционально) только при реальном missed>0
										if missed > 0 and getattr(self, 'gap_capture_path', None):
											try:
												if self._gap_capture_fp is None:
													self._gap_capture_fp = open(self.gap_capture_path, 'a', encoding='utf-8', errors='replace')
													self._gap_capture_started = time.time()
													self._gap_capture_lines = 0
													self._gap_capture_fp.write('#ts_unix,last_seq,seq,delta,step,missed,pm,stereo_q,bufA,bufB,usb_magic_bad,usb_crc_bad,usb_bytes_per_s\n')
													self._gap_capture_fp.flush()
												# write line if within capture window
												if (time.time() - float(self._gap_capture_started)) <= float(self.gap_capture_seconds) and int(self._gap_capture_lines) < int(self.gap_capture_limit):
													asm = getattr(self.stream, 'asm', None)
													stereo_q = asm.q.qsize() if asm is not None and hasattr(asm, 'q') else 0
													bufA = len(getattr(asm, 'bufA', {}) or {}) if asm is not None else 0
													bufB = len(getattr(asm, 'bufB', {}) or {}) if asm is not None else 0
													pm = int(getattr(self, '_pair_mismatch_count', 0))
													usb_magic_bad = int(getattr(self.stream, 'magic_bad', 0))
													usb_crc_bad = int(getattr(self.stream, 'crc_bad', 0))
													usb_bps = int(getattr(self.stream, 'bytes', 0))
													line = f"{time.time():.6f},{int(self.last_seq)},{int(a.seq)},{int(delta)},{int(step)},{int(missed)},{pm},{stereo_q},{bufA},{bufB},{usb_magic_bad},{usb_crc_bad},{usb_bps}\n"
													self._gap_capture_fp.write(line)
													self._gap_capture_lines = int(self._gap_capture_lines) + 1
													if (int(self._gap_capture_lines) % 10) == 0:
														self._gap_capture_fp.flush()
												# close if time or limit exceeded
												if (time.time() - float(self._gap_capture_started)) > float(self.gap_capture_seconds) or int(self._gap_capture_lines) >= int(self.gap_capture_limit):
													try:
														self._gap_capture_fp.close()
													except Exception:
														pass
													self._gap_capture_fp = None
											except Exception:
												pass
										# throttled GAP log
										exp = (int(self.last_seq) + step) & 0xFFFFFFFF
										self._gap_log_last_exp = exp
										self._gap_log_last_got = int(a.seq)
										if self.gap_log_enabled:
											now_gap = time.time()
											if self.gap_log_every <= 0 or (now_gap - self._gap_log_last_t) >= self.gap_log_every:
												pending = int(self._gap_log_pending)
												self._gap_log_pending = 0
												self._gap_log_last_t = now_gap
												print(f"[GAP] +{pending} step={step} ожидаю {exp}, получил {int(a.seq)} (delta={delta}) total={self.gap_count} reord={int(getattr(self,'seq_reorder_count',0))}", flush=True)
									self.last_seq = a.seq
						self.frames_sec += 2
						self.frames_a += 1
						self.frames_b += 1
					else:
						# independent mode: increment only the channel(s) that arrived
						if a is not None:
							self.frames_sec += 1
							self.frames_a += 1
						if b is not None:
							self.frames_sec += 1
							self.frames_b += 1
					
					# Диагностика каждые 100 кадров
					if not hasattr(self, '_reader_count'):
						self._reader_count = 0
					self._reader_count += 1
					if self._reader_count % 100 == 0:
						extra = ''
						try:
							s0 = ch0[:5] if ch0 is not None else 'None'
						except Exception:
							s0 = 'ERR'
						try:
							s1 = ch1[:5] if ch1 is not None else 'None'
						except Exception:
							s1 = 'ERR'
						print(f"[READER] Received {self._reader_count} frames, ch0[0:5]={s0}, ch1[0:5]={s1}", flush=True)
				
			except Exception as e:
				if "Resource busy" in str(e) or "[Errno" in str(e):
					print(f"[READER] USB error: {e}", flush=True)
					time.sleep(0.5)
					continue
				print(f"[READER] Exception: {e}", flush=True)
				time.sleep(0.1)
		print("[READER] Thread stopped", flush=True)
	
	def _tick(self):
		"""GUI thread: читает из shared buffers и отображает данные ВСЕГДА"""
		# Обработка статуса подключения
		if self.stream is None:
			self._last_sample_ts = None
			if self.num_group.checkedId() == 1:
				if not self._connecting:
					self._set_status("Подключение…", hold_sec=1.5)
			else:
				self._set_status("Нажмите кнопку 1 для запуска потока")
		
		# Проверка disconnected (но продолжаем отображать данные)
		if self.stream is not None and getattr(self.stream, 'disconnected', False):
			try:
				self.stream.close()
			except Exception:
				pass
			self.stream = None
			self._last_sample_ts = None
			self._set_status("USB занят/отключён, переподключение…", hold_sec=2.0)
			self.usb_retry_timer.start()
		
		# Читаем данные из shared buffers с блокировкой (ВСЕГДА, даже если stream=None)
		with self.data_lock:
			# Всегда держим окно на весь кадр
			if self.base_buf_len is not None:
				try:
					self.slider_start.blockSignals(True)
					self.slider_len.blockSignals(True)
				except Exception:
					pass
				# Инициализируем слайдеры только при первой инициализации
				# или если длина буфера изменилась (чтобы не сбрасывать позицию
				# при каждом тике GUI, когда мы только перемещаем ползунки).
				if (not getattr(self, '_sliders_initialized', False)) or getattr(self, '_last_buf_len', None) != self.base_buf_len:
					self._init_sliders(self.base_buf_len)
					self._last_buf_len = self.base_buf_len
			
			# Используем base_buf_len если установлен, иначе max_samples
			buf_len = self.base_buf_len if self.base_buf_len is not None else self.max_samples
			
			# Вычисляем окно отображения
			slider_val = int(self.slider_len.value())
			vlen_calc = buf_len if not hasattr(self, '_sliders_initialized') else slider_val
			vlen = max(1, min(vlen_calc, buf_len))
			if hasattr(self, '_sliders_initialized'):
				self.slider_start.setMaximum(max(0, buf_len - vlen))
			vstart = min(int(self.slider_start.value()) if hasattr(self, '_sliders_initialized') else 0, max(0, buf_len - vlen))
			vlen = min(vlen, self.max_samples - vstart)
			if vlen <= 0:
				vlen = buf_len  # Fallback
			self.view_start = vstart
			self.view_len = vlen
			# NB: реальные сегменты берутся внутри _update_view(); здесь важно лишь вычислить окно.
		now = time.time()
		if now - self.last_fps_t >= 1.0:
			self.fps = self.frames_sec / (now - self.last_fps_t)  # общая FPS (для совместимости)
			self.afps = self.frames_a / (now - self.last_fps_t)   # FPS канала A
			self.bfps = self.frames_b / (now - self.last_fps_t)   # FPS канала B
			self.afps_even = self.frames_a_even / (now - self.last_fps_t)
			self.afps_odd = self.frames_a_odd / (now - self.last_fps_t)
			self.bfps_even = self.frames_b_even / (now - self.last_fps_t)
			self.bfps_odd = self.frames_b_odd / (now - self.last_fps_t)
			self.frames_sec = 0
			self.frames_a = 0
			self.frames_b = 0
			self.frames_a_even = 0
			self.frames_a_odd = 0
			self.frames_b_even = 0
			self.frames_b_odd = 0
			self.last_fps_t = now
		# auto symmetric y-range update (0.5s throttle) — ТОЛЬКО если включено BMI30_Y_AUTO=1
		if self.y_auto and (now - self.last_range_t > 0.5) and (len(self.data0) or len(self.data1)):
			try:
				maxv = 1
				if len(self.data0):
					maxv = max(maxv, int(np.max(self.data0)))
					maxv = max(maxv, abs(int(np.min(self.data0))))
				if len(self.data1):
					maxv = max(maxv, int(np.max(self.data1)))
					maxv = max(maxv, abs(int(np.min(self.data1))))
				# небольшая защита от нуля
				span = max(64, maxv * 1.05)
				# экспоненциальное сглаживание перепадов амплитуды
				alpha = 0.25
				if self._y_span_smooth is None:
					self._y_span_smooth = span
				else:
					self._y_span_smooth = alpha * span + (1 - alpha) * self._y_span_smooth
				self.p0.setYRange(-self._y_span_smooth, self._y_span_smooth, padding=0.02)
				self.p1.setYRange(-self._y_span_smooth, self._y_span_smooth, padding=0.02)
			except Exception:
				pass
			self.last_range_t = now
		buf_info = ""
		if self.base_buf_len is not None:
			freq_part = f" FREQ:{self.freq_hz}Hz" if self.freq_hz else ""
			buf_info = f" BUF:{self.base_buf_len}({self.base_buf_len_bytes}B){freq_part}"
		self.legend_lbl.setWordWrap(True)
		_zero_part = f" ZERO:{self.zero_blocks}" if getattr(self, 'zero_blocks', 0) else ""
		asm_stat = ''
		try:
			asm = getattr(self.stream, 'asm', None)
			if asm is not None:
				bufA = len(getattr(asm, 'bufA', {}) or {})
				bufB = len(getattr(asm, 'bufB', {}) or {})
				qsz = asm.q.qsize() if hasattr(asm, 'q') else 0
				pm = int(getattr(self, '_pair_mismatch_count', 0))
				dp = int(getattr(asm, 'drop_pairs', 0))
				da = int(getattr(asm, 'drop_a', 0))
				db = int(getattr(asm, 'drop_b', 0))
				asm_stat = f" ASM(ts_pairs={getattr(asm, '_ts_pair_count', 0)} neigh={getattr(asm, '_seq_neighbor_pairs', 0)} q={qsz} bufA={bufA} bufB={bufB} PM={pm} dropP={dp} dropA={da} dropB={db})"
		except Exception:
			asm_stat = ''
		_dt_a = ''
		_dt_b = ''
		try:
			if self._dt_a_even is not None or self._dt_a_odd is not None:
				_dt_a = f" dte/dto:{(self._dt_a_even if self._dt_a_even is not None else -1):.3f}/{(self._dt_a_odd if self._dt_a_odd is not None else -1):.3f}s"
		except Exception:
			_dt_a = ''
		try:
			if self._dt_b_even is not None or self._dt_b_odd is not None:
				_dt_b = f" dte/dto:{(self._dt_b_even if self._dt_b_even is not None else -1):.3f}/{(self._dt_b_odd if self._dt_b_odd is not None else -1):.3f}s"
		except Exception:
			_dt_b = ''
		_phase = ''
		try:
			_phase = f" PH:{(getattr(self,'_phase_key_chosen',None) or getattr(self,'phase_key','auto'))}"
		except Exception:
			_phase = ''
		_default_status = f"Afps:{self.afps:.1f} Bfps:{self.bfps:.1f} Aeo:{self.afps_even:.1f}/{self.afps_odd:.1f}{_dt_a} Beo:{self.bfps_even:.1f}/{self.bfps_odd:.1f}{_dt_b}{_phase} CH0:{len(self.data0)} GAP:{self.gap_count} SEQ:{self.last_seq} STEP:{getattr(self,'seq_step',1)} R:{getattr(self,'seq_reorder_count',0)} GA:{getattr(self,'gap_a',0)} GB:{getattr(self,'gap_b',0)} SA:{getattr(self,'step_a',1)} SB:{getattr(self,'step_b',1)} VIEW[{self.view_start}:{self.view_start+self.view_len}]{buf_info}{_zero_part}{asm_stat}"
		# Печатаем дефолтный статус не чаще 1 раза в секунду и только если нет активного hold
		_now_for_default = time.time()
		if (self._status_hold_text is None or _now_for_default >= self._status_hold_until) and (_now_for_default - self._last_default_update_t >= 1.0):
			self._set_status(_default_status)
			self._last_default_update_t = _now_for_default
		# предупреждение если нет данных
		now2 = time.time()
		if self.stream and self.base_buf_len is None and self.connect_t and (now2-self.connect_t)>2.0 and not self.no_data_warned:
			# Не дёргаем устройство бесконечно; просто покажем диагностику и предложим ↻
			try:
				if hasattr(self.stream, 'test_seen') and getattr(self.stream, 'test_seen', 0) > 0:
					# Попробуем вытащить last_stat для отображения ключевых полей
					st = getattr(self.stream, 'last_stat', None)
					if isinstance(st, (bytes, bytearray)) and len(st) >= 16:
						# STAT v1 layout: 0:4='STAT', 4=ver(u8), 5=reserved0, 6:8=cur_samples(u16), 8:10=frame_bytes(u16)
						ver = st[4]
						cur_samples = int.from_bytes(st[6:8],'little')
						frame_bytes = int.from_bytes(st[8:10],'little')
						self._set_status(f"Есть TEST, но нет A/B. STAT v{ver} cur_samples={cur_samples} frame_bytes={frame_bytes}. Нажмите ↻ для ручного пинка.", hold_sec=2.0)
					else:
						self._set_status("Есть TEST, но нет рабочих кадров A/B. Проверьте прошивку: фиксация размера и отправка ADC0/ADC1 после TEST. Нажмите ↻ для ручного пинка.", hold_sec=2.0)
				else:
					self._set_status("Нет данных (нет первых кадров). Нажмите ↻ для повторной попытки. " + self._instr, hold_sec=2.0)
			except Exception:
				self._set_status("Нет данных (нет первых кадров). Нажмите ↻ для повторной попытки. " + self._instr, hold_sec=2.0)
			self.no_data_warned = True
		elif self.stream and (now2 - float(getattr(self.stream, 'last_rx_t', 0.0))) > self.stop_warn_after and (now2 - self.last_diag_t) > self.diag_interval:
			# Совсем нет приёма данных (ни STAT, ни кадров)
			if self.diag_to_console:
				print(f"[diag] Нет приёма данных >{int(self.stop_warn_after)}с. Возможно устройство перестало слать.")
			else:
				self._set_status(f"Нет приёма данных >{int(self.stop_warn_after)}с. Возможно устройство перестало слать. " + self._instr, hold_sec=4.0)
			self.last_diag_t = now2
		elif self.stream and self.base_buf_len is not None and (now2 - self.last_frame_t) > self.stop_warn_after and (now2 - self.last_diag_t) > self.diag_interval:
			# Приём идёт, но нет новых стереопар (например, рассинхрон A/B)
			if self.diag_to_console:
				print(f"[diag] Нет новых стереопар >{int(self.stop_warn_after)}с (приём идёт). Проверьте A/B и seq. Нажмите ↻ для переподключения.")
			else:
				self._set_status(f"Нет новых стереопар >{int(self.stop_warn_after)}с (приём идёт). Проверьте A/B и seq. Нажмите ↻ для переподключения.", hold_sec=3.0)
			self.last_diag_t = now2
			# Попробуем мягко пнуть поток (без STOP), но не чаще чем раз в diag_interval
			if self.auto_soft_kick and (now2 - self.last_soft_kick_t) > max(2.0, self.diag_interval):
				try:
					self._set_status("Мягкий рестарт потока…", hold_sec=1.0)
					self._soft_kick_stream()
					self.last_soft_kick_t = time.time()
				except Exception as e:
					print("[kick] soft restart failed:", e)

		# обновить статус: если hold истёк, очистить его, чтобы отобразить дефолтный FPS-статус
		if self._status_hold_text is not None and time.time() >= self._status_hold_until:
			self._status_hold_text = None
			# немедленно обновим подпись дефолтным
			buf_info = ""
			if self.base_buf_len is not None:
				freq_part = f" FREQ:{self.freq_hz}Hz" if self.freq_hz else ""
				buf_info = f" BUF:{self.base_buf_len}({self.base_buf_len_bytes}B){freq_part}"
			_zero_part = f" ZERO:{self.zero_blocks}" if getattr(self, 'zero_blocks', 0) else ""
			_default_status = f"Afps:{self.afps:.1f} Bfps:{self.bfps:.1f} CH0:{len(self.data0)} GAP:{self.gap_count} SEQ:{self.last_seq} STEP:{getattr(self,'seq_step',1)} R:{getattr(self,'seq_reorder_count',0)} GA:{getattr(self,'gap_a',0)} GB:{getattr(self,'gap_b',0)} SA:{getattr(self,'step_a',1)} SB:{getattr(self,'step_b',1)} VIEW[{self.view_start}:{self.view_start+self.view_len}]{buf_info}{_zero_part}"
			self._set_status(_default_status)
		
		# Обновить view после всех изменений данных
		self._update_view()

	def _update_view(self):
		"""Перерисовать окно по текущим параметрам (без чтения новых данных)."""
		if self.base_buf_len is None or self.max_samples == 0:
			return
		vlen = max(1, min(self.view_len, self.base_buf_len))
		max_start = max(0, len(self.data0) - vlen)
		self.slider_start.setMaximum(max(0, self.base_buf_len - vlen))
		self.view_start = min(self.view_start, max_start)
		vlen = min(vlen, len(self.data0) - self.view_start)  # не больше доступных данных
		self.view_len = vlen
		# Snapshot buffers under lock to avoid tearing/races (can look like swaps/in-phase).
		with self.data_lock:
			seg0 = (self.data0_even if hasattr(self, 'data0_even') else self.data0)[self.view_start:self.view_start+vlen].copy()
			seg1 = (self.data1_even if hasattr(self, 'data1_even') else self.data1)[self.view_start:self.view_start+vlen].copy()
			seg0b = (self.data0_odd if hasattr(self, 'data0_odd') else np.zeros_like(seg0))[self.view_start:self.view_start+vlen].copy()
			seg1b = (self.data1_odd if hasattr(self, 'data1_odd') else np.zeros_like(seg1))[self.view_start:self.view_start+vlen].copy()
			# Validity markers: if we haven't received a phase yet, don't draw stale buffers.
			v0e = (getattr(self, 'seq0_even', None) is not None)
			v0o = (getattr(self, 'seq0_odd', None) is not None)
			v1e = (getattr(self, 'seq1_even', None) is not None)
			v1o = (getattr(self, 'seq1_odd', None) is not None)
		
		# DC removal: вычитание постоянной составляющей и сдвиг на 32767
		if getattr(self, 'dc_removal_enabled', False) and not getattr(self, 'avg20_enabled', False):
			try:
				# Применяем DC removal поэлементно: data - DC_offset[view] + 32767
				dc0_even_view = self.dc_offset_ch0_even[self.view_start:self.view_start+vlen]
				dc0_odd_view = self.dc_offset_ch0_odd[self.view_start:self.view_start+vlen]
				dc1_even_view = self.dc_offset_ch1_even[self.view_start:self.view_start+vlen]
				dc1_odd_view = self.dc_offset_ch1_odd[self.view_start:self.view_start+vlen]
				
				seg0 = seg0 - dc0_even_view[:len(seg0)] + 32767
				seg0b = seg0b - dc0_odd_view[:len(seg0b)] + 32767
				seg1 = seg1 - dc1_even_view[:len(seg1)] + 32767
				seg1b = seg1b - dc1_odd_view[:len(seg1b)] + 32767
			except Exception as e:
				print(f"[DC_REMOVAL] Ошибка применения DC removal: {e}")
		
		# AVG20: вместо текущего кадра показываем среднее по последним N кадрам (уже DC-скорректированные значения)
		if getattr(self, 'avg20_enabled', False):
			try:
				# Берём только реально накопленные кадры, чтобы первые секунды не усреднялись с нулями
				cnt0e = int(getattr(self, '_avg0_even_cnt', 0))
				cnt0o = int(getattr(self, '_avg0_odd_cnt', 0))
				cnt1e = int(getattr(self, '_avg1_even_cnt', 0))
				cnt1o = int(getattr(self, '_avg1_odd_cnt', 0))
				seg0 = (np.mean(self._avg0_even[:cnt0e, self.view_start:self.view_start+vlen], axis=0) if cnt0e > 0 else np.zeros(vlen, dtype=np.float32))
				seg0b = (np.mean(self._avg0_odd[:cnt0o, self.view_start:self.view_start+vlen], axis=0) if cnt0o > 0 else np.zeros(vlen, dtype=np.float32))
				seg1 = (np.mean(self._avg1_even[:cnt1e, self.view_start:self.view_start+vlen], axis=0) if cnt1e > 0 else np.zeros(vlen, dtype=np.float32))
				seg1b = (np.mean(self._avg1_odd[:cnt1o, self.view_start:self.view_start+vlen], axis=0) if cnt1o > 0 else np.zeros(vlen, dtype=np.float32))
			except Exception as e:
				print(f"[AVG20] Ошибка вычисления среднего: {e}")
				# fallback на текущие данные
				pass
		# Инверсию можно включить через BMI30_INVERT=1; для маркеров инверсия не нужна
		if not getattr(self, 'avg20_enabled', False) and not getattr(self, 'debug_markers', False) and not getattr(self, 'no_invert', False):
			# Для беззнаковых данных разворачиваем вокруг середины 32767.5
			seg0 = 32767.5 - (seg0 - 32767.5)
			seg1 = 32767.5 - (seg1 - 32767.5)
			try:
				seg0b = 32767.5 - (seg0b - 32767.5)
				seg1b = 32767.5 - (seg1b - 32767.5)
			except Exception:
				pass
		x = np.arange(vlen)
		# --- режимы отображения ---
		if self.view_mode == 0:
			# оба канала
			if v0e and len(seg0) > 0 and (self.show_zero or not np.all(seg0 == 0)):
				self.curve0_a.setData(x, seg0)
			else:
				self.curve0_a.setData([], [])
			if v0o and len(seg0b) > 0 and (self.show_zero or not np.all(seg0b == 0)):
				self.curve0_b.setData(x, seg0b)
			else:
				self.curve0_b.setData([], [])
			if v1e and len(seg1) > 0 and (self.show_zero or not np.all(seg1 == 0)):
				self.curve1_a.setData(x, seg1)
			else:
				self.curve1_a.setData([], [])
			if v1o and len(seg1b) > 0 and (self.show_zero or not np.all(seg1b == 0)):
				self.curve1_b.setData(x, seg1b)
			else:
				self.curve1_b.setData([], [])
			self.p0.show()
			self.p1.show()
		elif self.view_mode == 1:
			# только канал 1
			if v0e and len(seg0) > 0 and (self.show_zero or not np.all(seg0 == 0)):
				self.curve0_a.setData(x, seg0)
			else:
				self.curve0_a.setData([], [])
			if v0o and len(seg0b) > 0 and (self.show_zero or not np.all(seg0b == 0)):
				self.curve0_b.setData(x, seg0b)
			else:
				self.curve0_b.setData([], [])
			self.curve1_a.setData([], [])
			self.curve1_b.setData([], [])
			self.p0.show()
			self.p1.hide()
		elif self.view_mode == 2:
			# только канал 2
			self.curve0_a.setData([], [])
			self.curve0_b.setData([], [])
			if v1e and len(seg1) > 0 and (self.show_zero or not np.all(seg1 == 0)):
				self.curve1_a.setData(x, seg1)
			else:
				self.curve1_a.setData([], [])
			if v1o and len(seg1b) > 0 and (self.show_zero or not np.all(seg1b == 0)):
				self.curve1_b.setData(x, seg1b)
			else:
				self.curve1_b.setData([], [])
			self.p0.hide()
			self.p1.show()
		self.lbl_start_value.setText(str(self.view_start))
		self.lbl_len_value.setText(str(vlen))
		self._apply_x_range(0, self.view_len or self.initial_expected)
	def _set_view_mode(self, mode:int):
		"""Установить режим отображения: 0=оба, 1=только канал 1, 2=только канал 2"""
		self.view_mode = mode
		self._update_view()

	def _apply_x_range(self, start: float, end: float):
		"""Принудительно зафиксировать диапазон X, чтобы график не 'улетал'."""
		try:
			if end <= start:
				return
			# меняем X-диапазон только если он реально отличается, чтобы избежать мерцания
			vr0 = self.p0.viewRange()[0]
			if abs(vr0[0] - start) > 1e-6 or abs(vr0[1] - end) > 1e-6:
				self.p0.setXRange(start, end, padding=0.0)
				self.p1.setXRange(start, end, padding=0.0)
		except Exception:
			pass

	def _apply_x_axis_mode(self):
		"""Форматирование оси X: только номера семплов (целые)."""
		try:
			ax0 = self.p0.getAxis('bottom')
			ax1 = self.p1.getAxis('bottom')
			# Никаких SI-префиксов и целочисленные метки
			try:
				ax0.enableAutoSIPrefix(False)
				ax1.enableAutoSIPrefix(False)
			except Exception:
				pass
			ax0.setLabel("samples")
			ax1.setLabel("samples")
			def _int_ticks(values, scale, spacing):
				labels = []
				for v in values:
					try:
						labels.append(str(int(round(v))))
					except Exception:
						labels.append(str(v))
				return labels
			ax0.tickStrings = _int_ticks
			ax1.tickStrings = _int_ticks
		except Exception:
			pass

	def _run_init_sequence(self):
		"""Выполнить согласованную с разработчиком последовательность команд USB."""
		if self.stream is None:
			raise RuntimeError("нет активного stream")
		try:
			import time as _t
		except Exception:
			pass
		# STOP
		try:
			self.stream.send_cmd(CMD_STOP_STREAM, b"")
		except Exception as e:
			print("[initseq] STOP err", e)
		try:
			_t.sleep(0.01)
		except Exception:
			pass
		# SET_WINDOWS: (0,0,0,0) - полный буфер для режима LATEST
		try:
			windows_data = struct.pack('<HHHH', 0, 0, 0, 0)
			self.stream.send_cmd(CMD_SET_WINDOWS, windows_data)
			print("[initseq] SET_WINDOWS(0,0,0,0) - полный буфер")
		except Exception as e:
			print("[initseq] SET_WINDOWS err", e)
		# SET_STREAM_MODE: 0 (LATEST) - по умолчанию запускаем в режиме LATEST
		try:
			self.stream.send_cmd(CMD_SET_STREAM_MODE, b"\x00")
			print("[initseq] SET_STREAM_MODE=0 (LATEST)")
		except Exception as e:
			print("[initseq] SET_STREAM_MODE err", e)
		# FULL mode
		try:
			self.stream.send_cmd(0x13 if 'CMD_FULL_MODE' not in globals() else CMD_FULL_MODE, b"\x01")
		except Exception as e:
			print("[initseq] FULL err", e)
		# PROFILE
		try:
			prof = self.desired_profile if self.desired_profile in (1,2) else 1
			self.stream.send_cmd(CMD_SET_PROFILE, bytes([prof]))
		except Exception as e:
			print("[initseq] PROFILE err", e)
		# CHMODE both channels
		try:
			self.stream.send_cmd(0x19 if 'CMD_CHMODE' not in globals() else CMD_CHMODE, b"\x02")
		except Exception as e:
			print("[initseq] CHMODE err", e)
		# ASYNC (независимые A/B)
		try:
			self.stream.send_cmd(0x18 if 'CMD_ASYNC' not in globals() else CMD_ASYNC, b"\x01")
		except Exception as e:
			print("[initseq] ASYNC err", e)
		# BLOCK_HZ (опционально)
		try:
			bhz = int(self.block_hz) if getattr(self, 'block_hz', None) else None
			if bhz and 1 <= bhz <= 1000:
				self.stream.send_cmd(0x11 if 'CMD_BLOCK_HZ' not in globals() else CMD_BLOCK_HZ, int(bhz).to_bytes(2,'little'))
		except Exception as e:
			print("[initseq] BLOCK_HZ err", e)
		# START
		try:
			self.stream.send_cmd(CMD_START_STREAM, b"")
		except Exception as e:
			print("[initseq] START err", e)

 

	def _set_status(self, text: str, hold_sec: float | None = None):
		"""Установить текст статуса с возможностью удержания, чтобы текст не мигал.

		Если hold_sec задано, текст закрепляется на указанное время и не будет перезаписан
		дефолтным FPS-статусом, пока таймер не истечёт. Идентичные тексты не переустанавливаются,
		чтобы не вызывать лишних перерисовок.
		"""
		try:
			# если сейчас активен hold и он ещё не истёк, не перетирать другим текстом без hold
			now = time.time()
			if self._status_hold_text is not None and now < self._status_hold_until:
				# если новый текст также с hold и отличается — обновим
				if hold_sec and text != self._status_hold_text:
					self._status_hold_text = text
					self._status_hold_until = now + max(0.5, hold_sec)
					if text != self._last_status_text:
						self.legend_lbl.setText(text)
						self._last_status_text = text
				return
			# сюда попадаем если hold нет или истёк — применим новый текст
			if hold_sec:
				self._status_hold_text = text
				self._status_hold_until = now + max(0.5, hold_sec)
			if text != self._last_status_text:
				self.legend_lbl.setText(text)
				self._last_status_text = text
		except Exception:
			pass

	def _on_slider_start(self, val:int):
		if self.base_buf_len and val > self.base_buf_len - self.view_len:
			val = max(0, self.base_buf_len - self.view_len)
			self.slider_start.setValue(val)
		self.view_start = val
		self._update_view()

	def _on_slider_len(self, val:int):
		self.view_len = val
		# обновить максимум стартового смещения
		if self.base_buf_len:
			self.slider_start.setMaximum(max(0, self.base_buf_len - self.view_len))
		self._update_view()

	def _on_toggle_dc_removal(self, enabled: bool):
		"""Handler for top-right diagnostic toggle button — enables/disables DC subtraction."""
		try:
			self.dc_removal_enabled = bool(enabled)
		except Exception:
			self.dc_removal_enabled = False
		# Обновим визуальную подсказку (цвет/tooltip) и статус-индикатор
		try:
			if self.dc_removal_enabled:
				style = "QPushButton { background:#b6f0b6; color:#000; border:1px solid #6fb36f; }"
				tt = "DC removal: ВКЛЮЧЕНО"
				self.btn_diag.setToolTip("DC removal включено — вычитание постоянной составляющей")
			else:
				style = "QPushButton { background:#f0b6b6; color:#000; border:1px solid #b36f6f; }"
				tt = "DC removal: ВЫКЛЮЧЕНО"
				self.btn_diag.setToolTip("DC removal выключено — вычитание постоянной составляющей отключено")
			try:
				self.btn_diag.setStyleSheet(style)
			except Exception:
				pass
			# краткий статус
			self._set_status(tt, hold_sec=2.0)
		except Exception:
			pass

	def _copy_legend_from_btn(self, pos):
		"""Handle right-click on `btn_power`: copy `legend_lbl` text to clipboard."""
		try:
			text = self.legend_lbl.text() if hasattr(self, 'legend_lbl') else ''
			cb = QtWidgets.QApplication.clipboard()
			cb.setText(text)
			self._set_status("Заголовок скопирован в буфер обмена", hold_sec=2.0)
		except Exception:
			pass

	def _manual_reconnect(self):
		# Принудительно закрыть и заново начать поиск
		try:
			if self.stream:
				self.stream.close()
		except Exception:
			pass
		self.stream = None
		self.base_buf_len = None
		self.base_buf_len_bytes = None
		self.freq_hz = None
		self.data0 = np.zeros(0, dtype=np.int16)
		self.data1 = np.zeros(0, dtype=np.int16)
		self.timestamps = np.zeros(0, dtype=np.float64)
		self._last_sample_ts = None
		self.view_start = 0
		self.view_len = 0
		self._reset_sliders()
		if self.num_group.checkedId() == 1:
			self._set_status("Переподключение…", hold_sec=1.5)
			self._activate_stream()
		else:
			self.legend_lbl.setText("Нажмите 1 для запуска")

	def _reset_sliders(self):
		"""Вернуть слайдеры в исходное состояние и разрешить повторную инициализацию."""
		try:
			self.slider_start.blockSignals(True)
			self.slider_len.blockSignals(True)
		except Exception:
			pass
		self.slider_start.setEnabled(False)
		self.slider_len.setEnabled(False)
		self.slider_start.setMaximum(0)
		self.slider_len.setMaximum(0)
		self.slider_start.setValue(0)
		self.slider_len.setValue(0)
		self.lbl_start_value.setText("0")
		self.lbl_len_value.setText("0")
		self._sliders_initialized = False
		# forget last buffer length so init will run on next valid buf_len
		self._last_buf_len = None
		try:
			self.slider_start.blockSignals(False)
			self.slider_len.blockSignals(False)
		except Exception:
			pass

	def _init_sliders(self, buf_len: int):
		"""Настроить слайдеры под актуальную длину буфера."""
		if buf_len <= 0:
			return
		try:
			self.slider_start.blockSignals(True)
			self.slider_len.blockSignals(True)
		except Exception:
			pass
		self.view_start = 0
		self.view_len = buf_len
		self.slider_start.setEnabled(True)
		self.slider_len.setEnabled(True)
		self.slider_len.setMinimum(1)
		self.slider_len.setMaximum(buf_len)
		self.slider_len.setValue(buf_len)
		self.slider_start.setMaximum(max(0, buf_len - self.view_len))
		self.slider_start.setValue(0)
		self.lbl_start_value.setText(str(self.view_start))
		self.lbl_len_value.setText(str(self.view_len))
		self._sliders_initialized = True
		try:
			self.slider_start.blockSignals(False)
			self.slider_len.blockSignals(False)
		except Exception:
			pass
		try:
			self.slider_start.blockSignals(False)
			self.slider_len.blockSignals(False)
		except Exception:
			pass

	def _soft_kick_stream(self):
		"""Мягко переинициализировать параметры и запустить START без STOP, чтобы не ронять интерфейс."""
		if self.stream is None:
			raise RuntimeError("нет активного потока")
		# Повторим текущий профиль и Ns и отправим START
		try:
			# Попробуем новый SOFT_RESET, если прошивка его поддерживает
			try:
				if hasattr(self.stream, 'soft_reset'):
					self.stream.soft_reset()
					try:
						import time as _t
						_t.sleep(0.05)
					except Exception:
						pass
			except Exception:
				pass
			# профиль
			self.stream.send_cmd(CMD_SET_PROFILE, bytes([self.desired_profile]))
			try:
				import time as _t
				_t.sleep(0.02)
			except Exception:
				pass
			# размер кадра (SET_FRAME_SAMPLES) — ТОЛЬКО для профиля 2 и только если явно включено
			if getattr(self, 'send_ns', False) and int(getattr(self, 'desired_profile', 2) or 2) == 2:
				try:
					from usb_vendor.usb_stream import CMD_SET_FRAME_SAMPLES  # type: ignore
				except Exception:
					CMD_SET_FRAME_SAMPLES = 0x17
				ns = self.ns_map.get(self.desired_profile, self.initial_expected)
				try:
					self.stream.send_cmd(CMD_SET_FRAME_SAMPLES, int(ns).to_bytes(2,'little'))
					import time as _t
					_t.sleep(0.02)
				except Exception:
					pass
			# частота блока 200/300 Гц
			try:
				if hasattr(self.stream, 'set_block_rate'):
					self.stream.set_block_rate(200 if self.desired_profile == 1 else 300)
					try:
						import time as _t
						_t.sleep(0.1)
					except Exception:
						pass
			except Exception:
				pass
			# старт
			self.stream.send_cmd(CMD_START_STREAM, b"")
		except Exception as e:
			raise

	def _diagnose_and_kick(self):
		"""Проверить EP0, выполнить DEEP_RESET, установить alt=1, проверить готовность и запустить поток.

		Алгоритм восстановления без перезапитки.
		"""
		if self.stream is None:
			self._set_status("Нет активного потока. Нажмите 1 для подключения.", hold_sec=2.0)
			return
		# Шаг 1: Проверить EP0
		try:
			# Получим STAT через EP0 (recipient: interface)
			if hasattr(self.stream, '_get_status_ep0'):
				self.stream._get_status_ep0()
			self._set_status("EP0 проверен, STAT получен…", hold_sec=0.5)
		except Exception as e:
			self._set_status(f"EP0 не отвечает: {e}. Перезапуск интерфейса в ОС или power cycle.", hold_sec=2.0)
			# Сразу попробуем power cycle
			try:
				self._power_cycle_usb_port()
				return
			except Exception as pc_e:
				self._set_status(f"Power cycle не удался: {pc_e}. Требуется аппаратный сброс.", hold_sec=5.0)
			return
		# Шаг 2: DEEP_RESET
		try:
			if hasattr(self.stream, 'deep_reset'):
				self._set_status("DEEP_RESET…", hold_sec=0.8)
				self.stream.deep_reset()
				time.sleep(0.1)  # Пауза после DEEP_RESET
			else:
				self._set_status("DEEP_RESET не поддерживается, пропускаю…", hold_sec=0.5)
		except Exception as e:
			self._set_status(f"DEEP_RESET failed: {e}", hold_sec=1.0)
		# Шаг 3: Установить alt=1
		try:
			if hasattr(self.stream, 'set_alt'):
				self._set_status("Установка alt=1…", hold_sec=0.5)
				self.stream.set_alt(1)
				time.sleep(0.05)
			else:
				self._set_status("set_alt не поддерживается, пропускаю…", hold_sec=0.5)
		except Exception as e:
			self._set_status(f"set_alt failed: {e}", hold_sec=1.0)
		# Шаг 4: Проверить готовность через GET_STATUS
		try:
			if hasattr(self.stream, '_get_status_ep0'):
				self.stream._get_status_ep0()
			time.sleep(0.05)  # Дать время на обновление STAT
			st = getattr(self.stream, 'last_stat', None)
			if isinstance(st, (bytes, bytearray)) and len(st) >= 64 and st[:4] == b'STAT':
				# STAT v1: flags_runtime 48:50 (u16), flags2 50:52 (u16), sending_ch @52 (u8), reserved2 @53 (u8)
				flags_runtime = int.from_bytes(st[48:50], 'little')
				hang_latched = (flags_runtime & 0x0004) != 0
				flags2 = int.from_bytes(st[50:52], 'little')
				alt1 = (flags2 >> 15) & 1
				sending_ch = st[52]
				reserved2 = st[53]
				out_armed = (reserved2 >> 7) & 1
				deep_reset_count_mod4 = reserved2 & 3
				print(f"[diag] STAT parsed: alt1={alt1}, out_armed={out_armed}, hang_latched={hang_latched}, sending_ch={sending_ch}, deep_reset_count_mod4={deep_reset_count_mod4}")
				if alt1 == 1 and out_armed == 1:
					status_msg = f"Готовность подтверждена (alt1=1, out_armed=1"
					if hang_latched:
						status_msg += ", hang_latched=1"
					if deep_reset_count_mod4:
						status_msg += f", deep_resets={deep_reset_count_mod4}"
					status_msg += "). Запуск потока…"
					self._set_status(status_msg, hold_sec=1.0)
					# Шаг 5: Запустить поток
					try:
						self._soft_kick_stream()
						self.last_soft_kick_t = time.time()
						self._set_status("Команды прошли. Ожидание данных…", hold_sec=1.5)
						return
					except Exception as e:
						self._set_status(f"START failed: {e}", hold_sec=1.0)
				else:
					self._set_status(f"Не готово: alt1={alt1}, out_armed={out_armed}. Повторная попытка alt toggle…", hold_sec=1.0)
			else:
				self._set_status("STAT не получен после alt=1. Повторная попытка…", hold_sec=1.0)
		except Exception as e:
			self._set_status(f"Проверка готовности failed: {e}", hold_sec=1.0)
		# Fallback: Alt toggle
		try:
			if hasattr(self.stream, 'set_alt'):
				self._set_status("Alt toggle 0→1…", hold_sec=0.8)
				self.stream.set_alt(0)
				time.sleep(0.03)
				self.stream.set_alt(1)
				time.sleep(0.05)
				self._soft_kick_stream()
				self._set_status("Alt toggle + kick отправлены. Ожидаю данные…", hold_sec=1.2)
				return
		except Exception as e:
			pass
		# Последний шанс: Power cycle
		try:
			self._power_cycle_usb_port()
			return
		except Exception as pc_e:
			self._set_status(f"Все методы восстановления не удались: {pc_e}. Требуется аппаратный сброс.", hold_sec=5.0)

	def _power_cycle_usb_port(self):
		"""Попытаться перезапитать конкретный USB-порт через uhubctl.

		Алгоритм:
		- узнаём топологию у текущего stream (bus, hub_loc, hub_port)
		- пытаемся `uhubctl -l <hub_loc> -p <port> -a off/on` (если установлен)
		- если uhubctl недоступен/не поддерживает PPS – fallback через sysfs:
		  /sys/bus/usb/devices/<port_path>/authorized 0/1, затем unbind/bind
		- после включения запускаем обычное переподключение
		"""
		if sys.platform == "win32":
			self._set_status(
				"Power-cycle недоступен на Windows (uhubctl/sysfs). "
				"Сделайте переподключение USB вручную или используйте device_reset_sequence.py.",
				hold_sec=4.0,
			)
			return
		if self.stream is None and self.last_port_info is None:
			self._set_status("Нет активного потока и сохранённой информации о порте. Нажмите 1 для подключения и повторите.", hold_sec=2.0)
			return
		try:
			info = {}
			try:
				if self.stream is not None:
					info = self.stream.get_port_path_info()
				else:
					info = self.last_port_info
			except Exception:
				info = self.last_port_info if self.last_port_info else {}
			hub_loc = info.get('hub_loc')
			hub_port = info.get('hub_port')
			port_path = info.get('port_path')  # например, "1-1.3.2"
			if not hub_loc or not hub_port:
				self._set_status("Не удалось определить порт хаба. Откройте консоль и выполните: sudo uhubctl", hold_sec=3.0)
				return
			if self.stream is not None:
				# Закрыть текущий поток прежде чем дёргать питание
				try:
					self.stream.close()
				except Exception:
					pass
				self.stream = None
			import subprocess, shlex, shutil, time as _t, os as _os
			# Попытка через uhubctl (если установлен)
			uh_path = shutil.which("uhubctl")
			uh_ok = False
			uh_err = ""
			if uh_path:
				self._set_status(f"Отключаю питание USB {hub_loc} порт {hub_port} через uhubctl…", hold_sec=2.0)
				cmd_off = f"sudo {uh_path} -l {hub_loc} -p {hub_port} -a off"
				cmd_on  = f"sudo {uh_path} -l {hub_loc} -p {hub_port} -a on"
				try:
					res_off = subprocess.run(shlex.split(cmd_off), capture_output=True, text=True, timeout=6)
					_off_rc = res_off.returncode
					_off_out = (res_off.stdout or "") + ("\n" + (res_off.stderr or ""))
					_t.sleep(1.0)
					res_on = subprocess.run(shlex.split(cmd_on), capture_output=True, text=True, timeout=6)
					_on_rc = res_on.returncode
					_on_out = (res_on.stdout or "") + ("\n" + (res_on.stderr or ""))
					uh_ok = (_off_rc == 0 and _on_rc == 0)
					# Типичные признаки отсутствия поддержки: "No compatible devices", "not found", "Permission denied"
					if ("No compatible" in _off_out) or ("No compatible" in _on_out) or ("not found" in _off_out) or ("not found" in _on_out) or ("Permission denied" in _off_out) or ("Permission denied" in _on_out):
						uh_ok = False
					if not uh_ok:
						uh_err = f"uhubctl rc off:{_off_rc} on:{_on_rc}. off_out: {_off_out.strip()} | on_out: {_on_out.strip()}"
				except Exception as e:
					uh_err = f"uhubctl exec error: {e}"
			# Если uhubctl не сработал — fallback через sysfs
			if not uh_ok:
				if not uh_path:
					print("[power] uhubctl не найден в системе. apt install uhubctl для перезапитки PPS-хабов.")
				else:
					print("[power] uhubctl failed:", uh_err)
				# Нужен port_path вида "1-1.3.2" для sysfs
				if not port_path:
					self._set_status("Нет port_path для sysfs fallback. Установите uhubctl или подключите PPS-хаб.", hold_sec=3.0)
					return
				dev_dir = f"/sys/bus/usb/devices/{port_path}"
				auth_file = f"{dev_dir}/authorized"
				self._set_status("Пробую отключить устройство через sysfs authorized…", hold_sec=2.0)
				# authorized 0/1 через tee (чтобы не требовать /bin/sh в sudoers)
				try:
					is_root = False
					try:
						is_root = (os.geteuid() == 0)
					except Exception:
						pass
					if is_root:
						res0 = subprocess.run(["tee", auth_file], input="0\n", text=True, capture_output=True, timeout=6)
					else:
						res0 = subprocess.run(["sudo", "tee", auth_file], input="0\n", text=True, capture_output=True, timeout=6)
					_t.sleep(0.8)
					if is_root:
						res1 = subprocess.run(["tee", auth_file], input="1\n", text=True, capture_output=True, timeout=6)
					else:
						res1 = subprocess.run(["sudo", "tee", auth_file], input="1\n", text=True, capture_output=True, timeout=6)
					if res0.returncode != 0 or res1.returncode != 0:
						raise RuntimeError(f"authorized rc0={res0.returncode} rc1={res1.returncode} out0={res0.stderr or res0.stdout} out1={res1.stderr or res1.stdout}")
					auth_ok = True
				except Exception as e_auth:
					print("[power] sysfs authorized failed:", e_auth)
					# Попробуем unbind/bind через tee
					self._set_status("authorized не сработал, пробую unbind/bind…", hold_sec=2.0)
					try:
						ub = "/sys/bus/usb/drivers/usb/unbind"
						bb = "/sys/bus/usb/drivers/usb/bind"
						is_root = False
						try:
							is_root = (os.geteuid() == 0)
						except Exception:
							pass
						if is_root:
							res_u = subprocess.run(["tee", ub], input=f"{port_path}\n", text=True, capture_output=True, timeout=6)
						else:
							res_u = subprocess.run(["sudo", "tee", ub], input=f"{port_path}\n", text=True, capture_output=True, timeout=6)
						_t.sleep(0.8)
						if is_root:
							res_b = subprocess.run(["tee", bb], input=f"{port_path}\n", text=True, capture_output=True, timeout=6)
						else:
							res_b = subprocess.run(["sudo", "tee", bb], input=f"{port_path}\n", text=True, capture_output=True, timeout=6)
						if res_u.returncode != 0 or res_b.returncode != 0:
							raise RuntimeError(f"unbind/bind rcU={res_u.returncode} rcB={res_b.returncode} outU={res_u.stderr or res_u.stdout} outB={res_b.stderr or res_b.stdout}")
						print("[power] unbind/bind OK")
					except Exception as e_ub:
						self._set_status(f"Не удалось перезапустить порт: {e_ub}", hold_sec=3.0)
						return
			else:
				self._set_status("Питание через uhubctl включено. Жду переэнумерации…", hold_sec=2.0)
			# Небольшая пауза и обычное переподключение
			_t.sleep(1.0)
			self._activate_stream()
		except Exception as e:
			self._set_status(f"Power-cycle ошибка: {e}", hold_sec=3.0)

	def _try_connect(self, first=False):
		if self.num_group.checkedId() not in (1, 3):
			return
		if self._connecting or self.stream is not None:
			return
		self._connecting = True
		msg_prefix = "Подключение" if not first else "Инициализация"
		try:
			self._set_status(f"{msg_prefix} к устройству…", hold_sec=1.5)
			# восстановим глобальный флаг running в модуле usb_stream (после close() он мог стать False)
			try:
				import usb_vendor.usb_stream as _usm  # type: ignore
				if getattr(_usm, 'running', True) is False:
					_usm.running = True
			except Exception:
				pass
			# frame_samples (SET_FRAME_SAMPLES) безопасен только для профиля 2; профиль 1 не трогаем
			fs = self.expected_len_map.get(self.desired_profile, self.initial_expected) if (self.send_ns and int(self.desired_profile or 2) == 2) else None
			# Для 200 Гц некоторые прошивки ожидают явной установки block rate — включим отправку частоты на старте
			try:
				os.environ['BMI30_SEND_BLOCK_RATE'] = '1'
			except Exception:
				pass
			print(f"[CONNECT] Creating USBStream, profile={self.desired_profile}, fs={fs}", flush=True)
			self.stream = USBStream(profile=self.desired_profile, full=True, test_as_data=self.test_as_data, frame_samples=fs, fast_mode=True, assembler_independent=self.independent_channels)
			# Сохраним порт info для power cycle без stream
			self.last_port_info = self.stream.port_info
			# Явная конфигурация устройства по согласованной последовательности
			if self.apply_init_sequence:
				try:
					self._run_init_sequence()
				except Exception as e_init:
					print(f"[initseq] failed: {e_init}")
			self._set_status("Устройство подключено, ожидание данных…", hold_sec=1.5)
			self.connect_t = time.time()
			self.last_frame_t = 0
			self.no_data_warned = False
			# Запускаем reader thread
			print(f"[CONNECT] reader_running={self.reader_running}, starting thread...", flush=True)
			if not self.reader_running:
				self.reader_running = True
				self.reader_thread = threading.Thread(target=self._reader_thread_func, daemon=True)
				self.reader_thread.start()
				print("[GUI] Reader thread started", flush=True)
			else:
				print("[GUI] Reader thread already running", flush=True)
		except SystemExit as se:
			self.stream = None
			print(f"[ERROR] SystemExit: {se}", flush=True)
			self._set_status(str(se), hold_sec=2.0)
		except Exception as e:
			self.stream = None
			print(f"[ERROR] Exception during connect: {e}", flush=True)
			import traceback
			traceback.print_exc()
			self._set_status(f"Нет устройства ({e})", hold_sec=2.0)
		finally:
			self._connecting = False
			if self.stream is None:
				if not self.usb_retry_timer.isActive():
					self.usb_retry_timer.start()
			else:
				self.usb_retry_timer.stop()

	def _activate_stream(self):
		if not self.usb_retry_timer.isActive():
			self.usb_retry_timer.start()
		self._send_soft_reset_via_cdc()
		self._try_connect(first=True)
		# Транспорт при подключении посылает минимальный START сам.
		# Не дублируем SET_PROFILE/START здесь, чтобы не подавить первые A/B кадры.
		if self.stream is not None:
			# Показать ожидаемое число семплов одного буфера по профилю
			expected = self.expected_len_map.get(self.desired_profile, self.initial_expected)
			freq = 200 if self.desired_profile == 1 else 300 if self.desired_profile == 2 else None
			msg = f"Старт потока… ожидание данных… BUF≈{expected} семплов"
			if freq:
				msg += f" (профиль {freq} Гц)"
			self._set_status(msg, hold_sec=1.5)
			try:
				print(f"[start] Профиль={self.desired_profile} ожидаемый BUF={expected} семплов")
			except Exception:
				pass

	def _on_freq_change(self, idx:int):
		# 0 -> 200Hz (profile 1), 1 -> 300Hz (profile 2)
		self.desired_profile = 1 if idx == 0 else 2
		save_config(self.desired_profile)  # сохраняем выбор
		if self.stream is None:
			self._set_status(f"Выбрана частота {200 if idx==0 else 300} Гц (нажмите 1 для запуска)")
			return
		# Переключение на лету: остановим поток, отправим профиль и соответствующий NS и старт
		try:
			# Остановим поток перед переключением
			self.stream.send_cmd(CMD_STOP_STREAM, b"")
			try:
				import time as _t
				_t.sleep(0.05)
			except Exception:
				pass
			self.stream.send_cmd(CMD_SET_PROFILE, bytes([self.desired_profile]))
			# небольшая пауза даёт прошивке переключить профиль
			try:
				import time as _t
				_t.sleep(0.1)
			except Exception:
				pass
			# SET_FRAME_SAMPLES — только профиль 2 и только если явно включено
			if getattr(self, 'send_ns', False) and int(getattr(self, 'desired_profile', 2) or 2) == 2:
				try:
					from usb_vendor.usb_stream import CMD_SET_FRAME_SAMPLES  # type: ignore
				except Exception:
					CMD_SET_FRAME_SAMPLES = 0x17
				ns = self.ns_map.get(self.desired_profile, self.initial_expected)
				try:
					self.stream.send_cmd(CMD_SET_FRAME_SAMPLES, int(ns).to_bytes(2,'little'))
					import time as _t
					_t.sleep(0.1)
				except Exception:
					pass
			# Явно задаём частоту блока под профиль
			try:
				if hasattr(self.stream, 'set_block_rate'):
					self.stream.set_block_rate(200 if self.desired_profile == 1 else 300)
					try:
						import time as _t
						_t.sleep(0.1)
					except Exception:
						pass
			except Exception:
				pass
			self.stream.send_cmd(CMD_START_STREAM, b"")
			self._set_status(f"Переключена частота {200 if idx==0 else 300} Гц, ожидание данных…", hold_sec=1.5)
			self.base_buf_len = None
			self.base_buf_len_bytes = None
			self.freq_hz = None
			self.max_samples = 1200
			self.data0 = np.zeros(self.max_samples, dtype=np.int16)
			self.data1 = np.zeros(self.max_samples, dtype=np.int16)
			self.data0_even = self.data0
			self.data1_even = self.data1
			self.data0_odd = np.zeros(self.max_samples, dtype=np.int16)
			self.data1_odd = np.zeros(self.max_samples, dtype=np.int16)
			self.timestamps = np.zeros(self.max_samples, dtype=np.float64)
			self._last_sample_ts = None
			self.view_len = self.max_samples
			self.slider_start.setEnabled(False)
			self.slider_len.setEnabled(False)
			self.connect_t = time.time()
			self.last_frame_t = 0
			self.no_data_warned = False
		except Exception as e:
			self._set_status(f"Ошибка смены частоты: {e}", hold_sec=2.0)

	def _on_avg_change(self, idx: int):
		"""Handler for avg_n combo box: send AVG_ROI change to device and save setting."""
		try:
			text = self.avg_box.currentText()
			new_n = int(text)
		except Exception:
			new_n = 20
		# save selection
		save_avg_n(new_n)
		self.avg_n = new_n
		# If stream is not running, just inform user
		if self.stream is None:
			self._set_status(f"Выбрано avg_n={new_n} (устройство не подключено)", hold_sec=2.0)
			return
		# If already in AVG_ROI, send only SET_STREAM_MODE with new avg_n; otherwise do full switch
		try:
			if getattr(self, 'stream_mode', 0) == 2:
				self.stream.send_cmd(CMD_SET_STREAM_MODE, bytes([0x02, new_n & 0xFF]))
				self._set_status(f"Отправлен AVG_ROI avg_n={new_n}", hold_sec=2.0)
				print(f"[AVG_ROI] SET_STREAM_MODE=2 (avg_n={new_n}) отправлен по изменению меню")
			else:
				self._switch_to_avg_roi(new_n)
		except Exception as e:
			print(f"[AVG_ROI] Ошибка при применении avg_n={new_n}: {e}")

	def _on_close(self, ev):
		try:
			# Перед выходом сохраним DC offset (best-effort)
			try:
				self._save_dc_offset(force=True)
			except Exception:
				pass
			self.stream.close()
		except Exception:
			pass
		ev.accept()

	def run(self):
		self.win.resize(900, 600)
		self.win.show()
		# Запускаем цикл событий Qt корректно как для PyQt5 (exec_()), так и для PyQt6 (exec())
		_app = QtWidgets.QApplication.instance()
		if hasattr(_app, 'exec_'):
			_app.exec_()
		else:
			_app.exec()


def main():
	if PG_IMPORT_ERR:
		print('[fatal] pyqtgraph/Qt не установлены. pip install pyqtgraph PyQt5')
		return 2
	ScopeWindow().run()
	return 0


if __name__ == '__main__':
	sys.exit(main())

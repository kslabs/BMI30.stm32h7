"""Websplit runtime primitives. Standard library only: no Qt, windows or plots.

The control objects keep the small value/signal interface used by the shared
engine's hardware handlers. Presentation calls are inert. The service loop
services detector pipe readiness before periodic maintenance and API commands.
"""
from __future__ import annotations

import inspect
import selectors
import os
import threading
import time
import traceback
from types import SimpleNamespace


class Signal:
    def __init__(self, owner=None):
        self.owner = owner
        self.slots = []

    def connect(self, slot):
        try:
            params = inspect.signature(slot).parameters.values()
            count = None if any(p.kind == p.VAR_POSITIONAL for p in params) else sum(
                p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in params)
        except (TypeError, ValueError):
            count = None
        self.slots.append((slot, count))

    def emit(self, *args):
        if self.owner is not None and self.owner._blocked:
            return
        for slot, count in tuple(self.slots):
            slot(*args if count is None else args[:count])


class Application:
    _instance = None

    def __init__(self, _args=None):
        if Application._instance is not None:
            raise RuntimeError('A service loop already exists')
        Application._instance = self
        self._thread = threading.current_thread()
        self._selector = selectors.DefaultSelector()
        self._wake_reader, self._wake_writer = os.pipe()
        os.set_blocking(self._wake_reader, False)
        os.set_blocking(self._wake_writer, False)
        self._selector.register(self._wake_reader, selectors.EVENT_READ, self._drain_wake)
        self._lock = threading.RLock()
        self._timers = {}
        self._quit = False

    @classmethod
    def instance(cls):
        return cls._instance

    def thread(self):
        return self._thread

    def wake(self):
        try:
            os.write(self._wake_writer, b'x')
        except (BlockingIOError, OSError):
            pass

    def _drain_wake(self):
        try:
            while os.read(self._wake_reader, 4096):
                pass
        except BlockingIOError:
            pass

    def processEvents(self, max_wait=0.):
        now = time.monotonic()
        with self._lock:
            due = min((t._deadline for t in self._timers), default=now + max_wait)
        timeout = max(0., min(max_wait, due - now))
        # Incoming detector results have priority over due maintenance timers.
        for key, _mask in self._selector.select(timeout):
            key.data()
        with self._lock:
            timers = [(t, t._generation) for t in self._timers if t._deadline <= time.monotonic()]
        for timer, generation in timers:
            with self._lock:
                if timer not in self._timers or generation != timer._generation:
                    continue
                if timer._single:
                    timer.stop()
                else:
                    timer._deadline = time.monotonic() + timer._interval
            timer.timeout.emit()

    def exec_(self):
        while not self._quit:
            try:
                self.processEvents(.05)
            except Exception:
                traceback.print_exc()
        return 0

    exec = exec_

    def quit(self):
        self._quit = True
        self.wake()

    def close(self):
        self.quit()
        with self._lock:
            self._timers.clear()
        self._selector.close()
        os.close(self._wake_reader)
        os.close(self._wake_writer)
        Application._instance = None


class ServiceObject:
    def __init__(self, _parent=None):
        pass

    def deleteLater(self):
        pass


class Timer(ServiceObject):
    def __init__(self, _parent=None):
        self.timeout = Signal()
        self._interval = .001
        self._single = False
        self._generation = 0
        self._deadline = 0.

    def setTimerType(self, _kind):
        pass

    def setInterval(self, milliseconds):
        self._interval = max(.001, float(milliseconds) / 1000.)
        if self.isActive():
            self.start()

    def interval(self):
        return round(self._interval * 1000)

    def setSingleShot(self, enabled):
        self._single = bool(enabled)

    def start(self, milliseconds=None):
        app = Application.instance()
        if app is None:
            raise RuntimeError('Create the service loop before starting a timer')
        with app._lock:
            if milliseconds is not None:
                self._interval = max(.001, float(milliseconds) / 1000.)
            self._generation += 1
            self._deadline = time.monotonic() + self._interval
            app._timers[self] = True
        app.wake()

    def stop(self):
        app = Application.instance()
        self._generation += 1
        if app is not None:
            with app._lock:
                app._timers.pop(self, None)

    def isActive(self):
        app = Application.instance()
        return app is not None and self in app._timers

    @staticmethod
    def singleShot(milliseconds, callback):
        timer = Timer()
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.start(milliseconds)
        return timer


class ReadNotifier(ServiceObject):
    Read = selectors.EVENT_READ

    def __init__(self, fd, _kind, _parent=None):
        self._fd = fd
        self._enabled = False
        self.activated = Signal()
        self.setEnabled(True)

    def setEnabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        app = Application.instance()
        if app is None:
            raise RuntimeError('Create the service loop before watching a descriptor')
        if enabled:
            app._selector.register(self._fd, selectors.EVENT_READ, lambda: self.activated.emit(self._fd))
        else:
            try:
                app._selector.unregister(self._fd)
            except (KeyError, ValueError):
                pass
        self._enabled = enabled


class _Font:
    def pointSize(self): return 10
    def setPointSize(self, _size): pass


class Control:
    """Hardware control state, with no screen representation or painting."""
    _presentation = frozenset(('setWindowTitle', 'setCentralWidget', 'setFont', 'setWordWrap',
        'setSizePolicy', 'setMinimumWidth', 'setMaximumWidth', 'setFixedWidth', 'setFixedHeight',
        'setFixedSize', 'setToolTip', 'setContextMenuPolicy', 'setStyleSheet', 'setAlignment',
        'setAttribute', 'setContentsMargins', 'setSpacing', 'addWidget', 'addLayout', 'addStretch',
        'setHorizontalStretch', 'resize', 'show', 'hide', 'setVisible'))

    def __init__(self, text='', *_args):
        self._text = str(text) if isinstance(text, str) else ''
        self._blocked = False
        self._enabled = True
        self._checked = False
        self._checkable = False
        self._group = None
        self._value = 0
        self._minimum = 0
        self._maximum = 99
        self._items = []
        self._index = -1
        for name in ('clicked', 'toggled', 'valueChanged', 'currentIndexChanged',
                     'currentTextChanged', 'customContextMenuRequested'):
            setattr(self, name, Signal(self))

    def __getattr__(self, name):
        if name in self._presentation:
            return lambda *_args, **_kwargs: None
        raise AttributeError(name)

    def font(self): return _Font()
    def text(self): return self._text
    def setText(self, text): self._text = str(text)
    def blockSignals(self, blocked):
        old = self._blocked; self._blocked = bool(blocked); return old
    def setEnabled(self, enabled): self._enabled = bool(enabled)
    def isEnabled(self): return self._enabled
    def setCheckable(self, enabled): self._checkable = bool(enabled)
    def setAutoExclusive(self, _enabled): pass
    def isChecked(self): return self._checked
    def setChecked(self, checked):
        checked = bool(checked)
        if checked and self._group is not None and self._group._exclusive:
            for button in self._group._buttons.values():
                if button is not self and button._checked:
                    button.setChecked(False)
        if self._checked != checked:
            self._checked = checked
            self.toggled.emit(checked)
    def click(self):
        if not self._enabled: return
        if self._checkable: self.setChecked(True if self._group else not self._checked)
        self.clicked.emit(self._checked)
        if self._group is not None:
            self._group.idClicked.emit(self._group.id(self))
    def value(self): return self._value
    def setValue(self, value):
        value = max(self._minimum, min(self._maximum, int(value)))
        if value != self._value:
            self._value = value; self.valueChanged.emit(value)
    def setMinimum(self, value): self._minimum = int(value); self.setValue(self._value)
    def setMaximum(self, value): self._maximum = int(value); self.setValue(self._value)
    def setRange(self, lo, hi): self._minimum = int(lo); self._maximum = int(hi); self.setValue(self._value)
    def minimum(self): return self._minimum
    def maximum(self): return self._maximum
    def addItem(self, text):
        self._items.append(str(text))
        if self._index < 0: self.setCurrentIndex(0)
    def addItems(self, items):
        for item in items: self.addItem(item)
    def count(self): return len(self._items)
    def currentIndex(self): return self._index
    def currentText(self): return self._items[self._index] if 0 <= self._index < len(self._items) else ''
    def findText(self, text):
        try: return self._items.index(str(text))
        except ValueError: return -1
    def setCurrentIndex(self, index):
        index = int(index)
        if 0 <= index < len(self._items) and index != self._index:
            self._index = index
            self.currentIndexChanged.emit(index)
            self.currentTextChanged.emit(self.currentText())
    def setCurrentText(self, text): self.setCurrentIndex(self.findText(text))


class ButtonGroup:
    def __init__(self):
        self._buttons = {}
        self._exclusive = True
        self._blocked = False
        self.idClicked = Signal(self)
    def setExclusive(self, enabled): self._exclusive = bool(enabled)
    def addButton(self, button, index): self._buttons[int(index)] = button; button._group = self
    def button(self, index): return self._buttons.get(int(index))
    def buttons(self): return list(self._buttons.values())
    def id(self, button): return next((i for i, b in self._buttons.items() if b is button), -1)
    def checkedId(self): return next((i for i, b in self._buttons.items() if b.isChecked()), -1)
    def checkedButton(self): return self.button(self.checkedId())


class NoPlot:
    """Inert compatibility handle for shared hardware handlers; stores no curves."""
    _methods = frozenset(('setVisible', 'setData', 'setXRange', 'setYRange', 'setRange',
        'setLabel', 'setTitle', 'setLimits', 'enableAutoRange', 'disableAutoRange',
        'showAxis', 'hideAxis', 'showGrid', 'setTicks', 'setGeometry', 'setXLink'))
    def __getattr__(self, name):
        if name in self._methods: return lambda *_args, **_kwargs: None
        if name in ('getAxis', 'getViewBox'): return lambda *_args, **_kwargs: self
        raise AttributeError(name)


# Compatibility names let existing hardware handlers use their value interfaces;
# they do not load Qt and are not GUI widgets. New service code uses the classes above.
RuntimeCore = SimpleNamespace(QObject=ServiceObject, QTimer=Timer, QSocketNotifier=ReadNotifier,
    QCoreApplication=Application, QThread=SimpleNamespace(currentThread=threading.current_thread),
    Qt=SimpleNamespace(PreciseTimer=0, CustomContextMenu=0, AlignCenter=0,
        WA_TransparentForMouseEvents=0, Orientation=SimpleNamespace(Horizontal=0)))
class _SizePolicy(Control):
    Expanding = 0
    Preferred = 0
RuntimeControls = SimpleNamespace(QApplication=Application, QButtonGroup=ButtonGroup, QSizePolicy=_SizePolicy,
    **{name: Control for name in ('QMainWindow', 'QWidget', 'QVBoxLayout', 'QHBoxLayout',
        'QLabel', 'QComboBox', 'QPushButton', 'QSlider', 'QToolButton')})

"""Optional STM32 features on the owner's existing USB connection.

Only an explicit unsupported-request STALL is permanent for that connection.
Timeouts, disconnects and malformed replies remain retryable. No USB handle is
opened here and neither capability snapshots nor cached failures perform I/O.
"""
import errno
import threading
_CREATE_LOCK = threading.RLock()


class FeatureUnavailable(RuntimeError):
    def __init__(self, feature):
        self.feature = feature
        super().__init__('STM32 does not support ' + feature.replace('_', ' '))


def unsupported(exc):
    return (getattr(exc, 'errno', None) in (errno.EPIPE, errno.ENOTSUP)
            or getattr(exc, 'backend_error_code', None) == -9)


class Capabilities:
    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.RLock()
        self.generation = None
        self.states = {}

    def _refresh(self):
        device = getattr(self.stream, 'dev', None)
        generation = (id(device), getattr(self.stream, 'connected_t', None))
        if self.generation != generation:
            self.generation = generation
            self._device_ref = device
            self.states.clear()

    def supported(self, feature):
        with self.lock:
            self._refresh()
            return self.states.get(feature)

    def snapshot(self):
        with self.lock:
            self._refresh()
            return {feature: {'supported': self.states.get(feature)} for feature in
                    ('tx_phase', 'relay', 'relay_test', 'communication_recovery')}

    def require(self, feature):
        if self.supported(feature) is False:
            raise FeatureUnavailable(feature)

    def transfer(self, feature, *args, **kwargs):
        self.require(feature)
        with self.lock:
            self._refresh()
            generation = self.generation
        try:
            return self.stream._ep0_ctrl_transfer(*args, **kwargs)
        except Exception as exc:
            if unsupported(exc):
                with self.lock:
                    self._refresh()
                    if self.generation == generation:
                        self.states[feature] = False
                raise FeatureUnavailable(feature) from exc
            raise

    def read(self, feature, parser, *args, **kwargs):
        with self.lock:
            self._refresh()
            generation = self.generation
        value = parser(self.transfer(feature, *args, **kwargs))
        with self.lock:
            self._refresh()
            if self.generation != generation:
                raise RuntimeError('USB connection changed during capability read')
            if self.states.get(feature) is not False:
                self.states[feature] = True
        return value


def for_stream(stream):
    value = getattr(stream, '_optional_capabilities', None)
    if not isinstance(value, Capabilities):
        with _CREATE_LOCK:
            value = getattr(stream, '_optional_capabilities', None)
            if not isinstance(value, Capabilities):
                value = Capabilities(stream)
                stream._optional_capabilities = value
    return value

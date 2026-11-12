"""Limit automatic LED outputs to this acquisition owner's detections."""
import ast,hashlib,json,tarfile
from prepare_led_page import WORK,BASE_VERSION,ROOT,candidate as portal_candidate
ENGINE='host/BMI30.200.py.2026-09-30-1650'
def candidate():
    portal_candidate()
    with tarfile.open(ROOT/BASE_VERSION/('BMI30-RPi-'+BASE_VERSION+'.tar.gz')) as tf:
        source=tf.extractfile(BASE_VERSION+'/project/'+ENGINE).read().decode()
    marker='\tdef _set_det_gpio(self, a_on: bool, b_on: bool):'
    assert source.count(marker)==1
    helper='''\tdef _local_led_detection_active(self) -> bool:
\t\t"""A sound edge or a neighbor's ADC flag never creates a local LED event."""
\t\ttry:
\t\t\treturn bool(self._group_led_detection_allowed() and
\t\t\t\t(self._det_output_active(0) or self._det_output_active(1)))
\t\texcept Exception:
\t\t\treturn False

'''
    source=source.replace(marker,helper+marker)
    marker="\t\t\t\tor bool(requested_a or requested_b))"
    assert source.count(marker)==1
    source=source.replace(marker,"\t\t\t\tor (bool(requested_a or requested_b) and\n\t\t\t\t\t(bool(getattr(self, '_sound_test_enabled', False)) or self._local_led_detection_active())))")
    marker='\t\treturn {\n\t\t\t"enabled": enabled,\n\t\t\t"test_enabled": test_enabled,\n\t\t\t"detection_active": sound_output_active,'
    assert source.count(marker)==1
    source=source.replace(marker,'''\t\tdetection_active = sound_output_active and self._local_led_detection_active()
\t\tled_sound_test_active = sound_output_active and bool(getattr(self, '_sound_test_enabled', False))
\t\treturn {
\t\t\t"enabled": enabled,
\t\t\t"test_enabled": test_enabled,
\t\t\t"detection_active": detection_active,''')
    marker='"level": 1 if (enabled and (test_enabled or sound_output_active)) else 0'
    assert source.count(marker)==1
    source=source.replace(marker,'"level": 1 if (enabled and (test_enabled or led_sound_test_active or detection_active)) else 0')
    marker='''\t\t\tif event.startswith("neighbor_") and not bool(
\t\t\t\tgetattr(self, "_group_led_neighbor_detection_enabled", False)
\t\t\t):
\t\t\t\treturn ""
\t\t\treturn event'''
    assert source.count(marker)==1
    source=source.replace(marker,'''\t\t\t# Temporarily allow automatic detection patterns only from this owner.
\t\t\tif event.startswith("neighbor_") or not self._local_led_detection_active():
\t\t\t\treturn ""
\t\t\treturn event''')
    source=source.replace('''\t\t# GPIO22 is the single non-addressable alarm indicator. It follows the
\t\t# physical PWM output edges; manual LED test always overrides it.''','''\t\t# Automatic alarm LEDs require this owner's accepted ADC detection.
\t\t# PWM still determines timing; explicit manual tests remain available.
\t\tself._group_led_neighbor_detection_enabled = False''')
    ast.parse(source)
    (WORK/'engine-local-led.py').write_text(source,encoding='utf-8',newline='\n')
    print(json.dumps({'engine':str(WORK/'engine-local-led.py'),'sha256':hashlib.sha256(source.encode()).hexdigest()}))
if __name__=='__main__':candidate()

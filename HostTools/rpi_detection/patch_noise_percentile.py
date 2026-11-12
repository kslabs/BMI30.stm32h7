"""Implement max(mean, P95) temporal noise estimation in an audited websplit."""
import ast
from pathlib import Path

from patch_base_width import after_line, replace


STATISTICS = '''def _rt_reference_noise_statistics(values):
	"""Return mean, linear P95, conservative estimate, and valid sample count."""
	arr = np.asarray(values, dtype=np.float64).reshape(-1)
	arr = arr[np.isfinite(arr) & (arr >= 0.0)]
	count = int(arr.size)
	if not count:
		return 0.0, 0.0, 0.0, 0
	mean = float(np.mean(arr))
	p95 = float(np.percentile(arr, 95.0)) if count > 1 else mean
	return mean, p95, max(mean, p95), count


'''


def patch_engine(source):
    source = replace(source, 'def _rt_detector_noise_area_percent() -> int:',
                     STATISTICS + 'def _rt_detector_noise_area_percent() -> int:')
    old = ('\t\t\tvalues = [float(x[1]) for x in hist if np.isfinite(float(x[1])) and float(x[1]) >= 0.0]\n'
           '\t\t\tif values:\n'
           '\t\t\t\tavg = float(sum(values) / float(len(values)))\n'
           '\t\t\telse:\n'
           '\t\t\t\tavg = float(getattr(self, avg_key, 0.0) or 0.0)\n'
           '\t\texcept Exception:\n'
           '\t\t\tavg = float(val)')
    new = ('\t\t\tvalues = [float(x[1]) for x in hist]\n'
           '\t\t\tmean, p95, avg, count = _rt_reference_noise_statistics(values)\n'
           '\t\texcept Exception:\n'
           '\t\t\t# Preserve a conservative floor if history evaluation fails.\n'
           '\t\t\tmean = float(val)\n'
           '\t\t\tp95 = max(float(val), float(getattr(self, avg_key, 0.0) or 0.0))\n'
           '\t\t\tavg = max(mean, p95)\n'
           '\t\t\tcount = 0\n'
           '\t\tsetattr(self, f"_det_noise_mean{ch}", float(mean))\n'
           '\t\tsetattr(self, f"_det_noise_p95{ch}", float(p95))\n'
           '\t\tsetattr(self, f"_det_noise_samples{ch}", int(count))')
    source = replace(source, old, new)
    source = after_line(source, 'self._det_noise_avg1 = 0.0',
                        'self._det_noise_mean0 = self._det_noise_mean1 = 0.0\n'
                        'self._det_noise_p950 = self._det_noise_p951 = 0.0\n'
                        'self._det_noise_samples0 = self._det_noise_samples1 = 0', count=1)
    source = after_line(source, "'_det_noise_ref0', '_det_noise_ref1', '_det_noise_avg0', '_det_noise_avg1',",
                        "'_det_noise_mean0', '_det_noise_mean1', '_det_noise_p950', '_det_noise_p951',\n"
                        "'_det_noise_samples0', '_det_noise_samples1',")
    source = after_line(source, '"noise_avg": float(noise_avg),',
                        '"noise_estimator": "max_mean_p95",\n'
                        '"noise_mean": float(getattr(self, f"_det_noise_mean{ch}", 0.0)),\n'
                        '"noise_p95": float(getattr(self, f"_det_noise_p95{ch}", 0.0)),\n'
                        '"noise_samples": int(getattr(self, f"_det_noise_samples{ch}", 0)),')
    source = after_line(source, "'noise_ref': float(noise_far), 'noise_avg': float(getattr(self,",
                        "'noise_estimator': 'max_mean_p95',\n"
                        "'noise_mean': float(getattr(self, f'_det_noise_mean{ch}', 0.0)),\n"
                        "'noise_p95': float(getattr(self, f'_det_noise_p95{ch}', 0.0)),\n"
                        "'noise_samples': int(getattr(self, f'_det_noise_samples{ch}', 0)),")
    return source


def patch_core(source):
    for ch in (0, 1):
        source = after_line(source, f'det_noise_avg{ch} = float(getattr(scope,',
                            f'det_noise_mean{ch} = float(getattr(scope, "_det_noise_mean{ch}", 0.0) or 0.0)\n'
                            f'det_noise_p95{ch} = float(getattr(scope, "_det_noise_p95{ch}", 0.0) or 0.0)\n'
                            f'det_noise_samples{ch} = int(getattr(scope, "_det_noise_samples{ch}", 0) or 0)')
        source = after_line(source, f'"noise_avg{ch}": det_noise_avg{ch},',
                            f'"noise_mean{ch}": det_noise_mean{ch},\n'
                            f'"noise_p95{ch}": det_noise_p95{ch},\n'
                            f'"noise_samples{ch}": det_noise_samples{ch},')
        source = after_line(source, f'"noise_avg": det_noise_avg{ch},',
                            '"noise_estimator": "max_mean_p95",\n'
                            f'"noise_mean": det_noise_mean{ch},\n'
                            f'"noise_p95": det_noise_p95{ch},\n'
                            f'"noise_samples": det_noise_samples{ch},')
    source = after_line(source, '"noise_avg0": det_noise_avg0,', '"noise_estimator": "max_mean_p95",')
    source = after_line(source, '"noise_window_s": float(det_settings.get(',
                        '"noise_estimator": "max_mean_p95",\n"noise_percentile": 95.0,')
    source = replace(source, 'averaged reference-noise level', 'reference-noise estimate max(mean, P95)', count=2)
    source = replace(source, ', averaged noise ', ', estimated noise ', count=1)
    source = replace(source, 'Manual mode: averaged noise ', 'Manual mode: estimated noise ', count=1)
    source = replace(source, ' The averaged reference noise was accumulated over ',
                     ' The reference-noise history window was ', count=1)
    source = replace(source,
                     'noise_avg:Number(ch.noise_avg||(lower?det.noise_avg1:det.noise_avg0))||0,',
                     'noise_avg:Number(ch.noise_avg||(lower?det.noise_avg1:det.noise_avg0))||0,'
                     "noise_estimator:String(det.noise_estimator||'mean'),"
                     'noise_mean:Number(ch.noise_mean)||0,noise_p95:Number(ch.noise_p95)||0,noise_samples:Number(ch.noise_samples)||0,')
    source = replace(source,
                     'noise_avg0:Number(det.noise_avg0)||0,noise_avg1:Number(det.noise_avg1)||0,',
                     "noise_estimator:String(det.noise_estimator||'mean'),"
                     'noise_avg0:Number(det.noise_avg0)||0,noise_avg1:Number(det.noise_avg1)||0,'
                     'noise_mean0:Number(det.noise_mean0)||0,noise_mean1:Number(det.noise_mean1)||0,'
                     'noise_p950:Number(det.noise_p950)||0,noise_p951:Number(det.noise_p951)||0,')
    anchor = "  if(snap.auto_threshold){\n"
    addition = ("  if(snap.noise_estimator==='max_mean_p95')parts.push({text:'NOISE P95',"
                "tip:'Reference-noise estimate = max(mean, 95th percentile) in the selected time window. Mean '+"
                "detectorLevelNumber(detectorSnapshotDisplay(snap.noise_mean,snap))+'; P95 '+"
                "detectorLevelNumber(detectorSnapshotDisplay(snap.noise_p95,snap))+'; samples '+String(snap.noise_samples||0)+'.'});\n")
    source = replace(source, anchor, addition + anchor)
    return source


def patch_portal(source):
    source = replace(source, 'Noise averaging window <b', 'Noise history window <b')
    source = replace(source,
        'Seconds of reference-side noise accumulated for the adaptive trigger threshold.',
        'History duration for the noise estimate: the larger of mean and 95th percentile.', count=2)
    source = replace(source,
        ' The number of buffers follows the current receive rate.',
        ' The number of buffers follows the current receive rate. The current-packet signal/noise check is also retained.', count=1)
    anchor = '                  <tr>\n                    <td class="tag-desc">\n                      <span class="tag-param">Noise history window'
    block = ('                  <tr>\n'
             '                    <td class="tag-desc"><span class="tag-param">Noise estimator</span></td>\n'
             '                    <td class="tag-channel" colspan="2">max(mean, P95) &mdash; 95th percentile</td>\n'
             '                  </tr>\n')
    return replace(source, anchor, block + anchor)


def prepare(source_dir, target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, patcher in [('engine', patch_engine), ('core', patch_core), ('portal', patch_portal)]:
        text = patcher((source_dir / (name + '.py')).read_text(encoding='utf8'))
        ast.parse(text)
        (target_dir / (name + '.py')).write_text(text, encoding='utf8', newline='\n')


if __name__ == '__main__':
    import sys
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))

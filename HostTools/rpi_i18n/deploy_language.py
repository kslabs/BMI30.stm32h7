"""Use the existing identity/settings/rollback guards for M02 localization only."""
import json,sys
from pathlib import Path
HERE=Path(__file__).parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'HostTools/rpi_detection'))
import deploy_noise_readout_help as previous
WORK=ROOT/'.tmp/language-20261016'
old_script=previous.script
def script(name):
    source=old_script(name).replace('.tmp/noise-readout-help-20261016','.tmp/language-20261016')
    source=source.replace('Integer readouts and two-second p/n/t/SN help','Offline portal and oscilloscope localization: 11 languages')
    source=source.replace('Integer scope readouts; explain p/n/t/SN after two-second hover while live values refresh','English default; persistent shared language selector before RPI/STM32, hover for two seconds')
    source=source.replace('Integer p/n/t/SN readouts and two-second help; retain precise native arithmetic','Offline UI catalogs, localized labels/status/tooltips/canvas; preserve detector and settings')
    source=source.replace('validated-M02-noise-readout-help-update','validated-M02-language-update')
    if name=='verify':
        source=source.replace("assert 'В расчётах сохраняется полная точность' in core_html", "assert 'Zero or invalid reference noise cannot confirm a tag.' in core_html\nassert 'bmi30-i18n' in core_html and 'bmi30-language-menu' in core_html")
        source=source.replace("area_field = re.search", "assert 'bmi30-i18n' in html and 'bmi30-language-menu' in html\narea_field = re.search")
        source=source.replace("assert html.index('name=\"noise_area_percent\"')", "html_markup = re.sub(r'<script\\b[^>]*>[\\s\\S]*?</script>', '', html, flags=re.I)\nassert html_markup.index('name=\"noise_area_percent\"')")
        source=source.replace("< html.index('Noise estimator') < html.index('name=\"noise_window_s\"')", "< html_markup.index('Noise estimator') < html_markup.index('name=\"noise_window_s\"')")
    if name=='registry':
        old="rows = [dict(zip(fields, values))]+[row for row in rows if row[fields[1]] != r['version']]"
        new="new_values = [values] + p.get('earlier_journal_rows', [])\nassert all(len(value)==len(fields) for value in new_values)\nnew_ids = {value[1] for value in new_values}\nrows = [dict(zip(fields, value)) for value in new_values] + [row for row in rows if row[fields[1]] not in new_ids]"
        assert source.count(old)==1
        source=source.replace(old,new)
    return source
previous.previous.base.WORK=WORK
previous.previous.base.script=script
call=previous.call
if __name__=='__main__':
    name=sys.argv[1];payload=json.loads(Path(sys.argv[2]).read_text(encoding='utf8')) if len(sys.argv)>2 else None
    result=call(name,payload,timeout=240 if name=='build' else 150)
    print(json.dumps({k:v for k,v in result.items() if k not in ('inline_scripts','diagnostics','sources','agents','packager','switcher','config','status')},indent=2))

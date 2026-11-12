"""Apply reviewed detector terminology and audit all bundled catalogs."""
import json,re
from pathlib import Path
HERE=Path(__file__).parent
locales=['uk','es','de','cs','ja','fr','pt','zh-Hans','it','ru']
keys=json.loads((HERE/'source_strings.json').read_text(encoding='utf8'))
noise_help=json.loads((HERE/'noise_help.json').read_text(encoding='utf8'))
overrides={c:{} for c in locales}
for line in (HERE/'reviewed.tsv').read_text(encoding='utf8').splitlines():
    columns=line.split('\t');assert len(columns)==11,(columns[0],len(columns))
    for code,value in zip(locales,columns[1:]):overrides[code][columns[0]]=value
for code in locales:
    path=HERE/f'translations.{code}.json';values=json.loads(path.read_text(encoding='utf8'))
    if code=='ru':values.update(dict(line.split('\t',1) for line in (HERE/'ru.tsv').read_text(encoding='utf8').splitlines() if '\t' in line))
    values.update(overrides[code])
    assert len(noise_help[code])==len(noise_help['en'])
    values.update(zip(noise_help['en'],noise_help[code]))
    # Keep protocol identifiers, product names, numbers and mathematical notation intact.
    for k in ['RPI','STM32','USB','RS485','BMI30','SN','p/n/t/SN','P95','TX1','TX2','RX','DC','ADC','SSID','SHA-256','FPS','HFP/HSP','A2DP','max(mean, P95)','med3','med5','avg7','avg9','avg11']:
        if k in values:values[k]=k
    template='n (noise) — noise estimate: max(mean, P95) over {seconds} s. P95 is the 95th percentile of measurement history.'
    assert '{seconds}' in values[template],(code,values[template])
    assert all(k in values and values[k] for k in keys)
    assert not any('<script' in v.lower() for v in values.values())
    path.write_text(json.dumps(values,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8')
print(json.dumps({'catalogs':len(locales),'source_strings':len(keys),'reviewed_terms':len(overrides['ru'])}))

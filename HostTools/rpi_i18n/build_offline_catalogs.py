"""Prepare UI dictionaries locally using official Argos model data.

Only resulting JSON catalogs ship to the Raspberry; model/runtime dependencies do not.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,os
from pathlib import Path
import re,time
from urllib.request import urlopen
import zipfile
HERE=Path(__file__).parent
WORK=HERE.parents[1]/'.tmp/language-20261016'
MODELS=WORK/'models';MODELS.mkdir(exist_ok=True)
LOCALES=['uk','es','de','cs','ja','fr','pt','zh','it','ru']
def download(code):
    index=json.loads((WORK/'argos-index.json').read_text())
    entry=next(x for x in index if x['from_code']=='en' and x['to_code']==code)
    # Official Argos data archive (the index's former argos-net host is unavailable).
    url='https://data.argosopentech.com/argospm/v1/'+entry['links'][0].rsplit('/',1)[-1]
    path=MODELS/(code+'.argosmodel')
    if not path.exists():
        print('Downloading '+code,flush=True)
        with urlopen(url,timeout=45) as response, path.with_suffix('.part').open('wb') as target:
            while block:=response.read(1024*1024):target.write(block)
        path.with_suffix('.part').replace(path)
    target=MODELS/code
    if not target.exists():
        target.mkdir()
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                dest=(target/member.filename).resolve()
                assert target.resolve() in dest.parents or dest==target.resolve()
            archive.extractall(target)
    print('Ready '+code,flush=True)
    return {'language':code,'url':url,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'metadata':entry}
def main():
    with ThreadPoolExecutor(max_workers=2) as pool:provenance=list(pool.map(download,LOCALES))
    (HERE/'model_provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    import ctranslate2
    from argostranslate.tokenizer import SentencePieceTokenizer, BPETokenizer
    keys=json.loads((HERE/'source_strings.json').read_text(encoding='utf8'))
    manual=dict(line.split('\t',1) for line in (HERE/'ru.tsv').read_text(encoding='utf8').splitlines() if '\t' in line)
    for code in LOCALES:
        locale='zh-Hans' if code=='zh' else code
        path=HERE/f'translations.{locale}.json'
        values=json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
        if code=='ru':values.update(manual)
        model=next((MODELS/code).rglob('model.bin')).parent
        package=model.parent
        tokenizer=SentencePieceTokenizer(package/'sentencepiece.model') if (package/'sentencepiece.model').exists() else BPETokenizer(package/'bpe.model','en',code)
        translator=ctranslate2.Translator(str(model),device='cpu',compute_type='int8',inter_threads=1,intra_threads=4)
        pending=[k for k in keys if k not in values]
        for offset in range(0,len(pending),24):
            group=pending[offset:offset+24]
            # Preserve named placeholders without asking the translation model to interpret them.
            encoded=[tokenizer.encode(k.replace('{seconds}','12345')) for k in group]
            results=translator.translate_batch(encoded,beam_size=2,max_batch_size=24,max_decoding_length=600)
            for key,result in zip(group,results):
                value=tokenizer.decode(result.hypotheses[0]).replace('12345','{seconds}')
                values[key]=value or key
            path.write_text(json.dumps(values,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8')
            print(json.dumps({'locale':locale,'done':len(values),'total':len(keys)}),flush=True)
        path.write_text(json.dumps(values,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8')
        del translator
if __name__=='__main__':main()

"""Keep both captured antennas in AutoFreeze without changing detector gates."""
import ast
import json
from pathlib import Path
import re


def once(source, old, new):
    assert source.count(old) == 1, (old[:100], source.count(old))
    return source.replace(old, new)


REFERENCE_TIP = ('Reference antenna captured with this detector request. '
                 'The source frames may differ from the firing antenna; no detection is implied.')
FREEZE_TIP = ('AutoFreeze: show captured product waveforms for both antennas from confirmed detections; '
              'older results are darker and the final result is brightest. Click to return live.')
TRANSLATIONS = {
    'uk': ['Опорну антену збережено разом із цим запитом детектора. Вихідні кадри можуть відрізнятися від антени, що спрацювала; це не означає виявлення.', 'AutoFreeze: показує збережені добутки обох антен при підтвердженому виявленні; старі результати темніші, останній найяскравіший. Натисніть для повернення наживо.'],
    'es': ['Antena de referencia capturada con esta solicitud del detector. Sus tramas pueden diferir de las de la antena activada; no implica una detección.', 'AutoFreeze: muestra los productos capturados de ambas antenas en detecciones confirmadas; los anteriores son más oscuros y el último más brillante. Pulse para volver al modo en directo.'],
    'de': ['Referenzantenne aus derselben Detektoranfrage. Ihre Quelldaten können von denen der auslösenden Antenne abweichen; dies bedeutet keine Erkennung.', 'AutoFreeze: zeigt die gespeicherten Produktsignale beider Antennen bei bestätigten Erkennungen; ältere Ergebnisse sind dunkler, das letzte am hellsten. Klicken Sie für die Liveanzeige.'],
    'cs': ['Referenční anténa zachycená se stejným požadavkem detektoru. Zdrojové snímky se mohou lišit od antény, která spustila detekci; nejde o potvrzení detekce.', 'AutoFreeze: zobrazuje uložené součiny obou antén při potvrzené detekci; starší výsledky jsou tmavší a poslední nejjasnější. Kliknutím se vrátíte k živému zobrazení.'],
    'ja': ['この検出要求とともに保存された参照アンテナです。入力フレームは検出したアンテナと異なる場合があり、検出を意味しません。', 'AutoFreeze：検出確定時に保存した両アンテナの積の波形を表示します。古い結果は暗く、最後の結果が最も明るく表示されます。クリックするとライブ表示に戻ります。'],
    'fr': ["Antenne de référence enregistrée avec cette requête du détecteur. Les trames sources peuvent différer de celles de l’antenne déclenchée ; cela ne signifie pas une détection.", 'AutoFreeze : affiche les produits enregistrés des deux antennes lors des détections confirmées ; les anciens sont plus sombres et le dernier plus lumineux. Cliquez pour revenir au direct.'],
    'pt': ['Antena de referência capturada com este pedido do detetor. Os quadros de origem podem diferir dos da antena que disparou; isto não implica uma deteção.', 'AutoFreeze: mostra os produtos capturados de ambas as antenas nas deteções confirmadas; os anteriores são mais escuros e o último mais brilhante. Clique para voltar à visualização em direto.'],
    'zh-Hans': ['与本次检测请求一同保存的参考天线。其源帧可能与触发检测的天线不同；这不表示检测成功。', 'AutoFreeze：显示确认检测时保存的两根天线的乘积波形；较早的结果较暗，最后的结果最亮。点击返回实时显示。'],
    'it': ["Antenna di riferimento acquisita con questa richiesta del rilevatore. I fotogrammi sorgente possono differire da quelli dell’antenna attivata; ciò non implica un rilevamento.", 'AutoFreeze: mostra i prodotti acquisiti di entrambe le antenne nei rilevamenti confermati; i precedenti sono più scuri e l’ultimo più luminoso. Fare clic per tornare alla visualizzazione dal vivo.'],
    'ru': ['Опорная антенна сохранена вместе с этим запросом детектора. Исходные кадры могут отличаться от сработавшей антенны; это не означает срабатывание.', 'AutoFreeze: показывает сохранённые произведения обеих антенн при подтверждённом срабатывании; старые результаты темнее, последний — самый яркий. Нажмите для возврата к живому графику.'],
}


def patch_engine(source):
    old = """\t\tif review_frame['synchronized']:
\t\t\tif isinstance(candidate_frame_block_in, (tuple, list)) and len(candidate_frame_block_in) == 4:
\t\t\t\tfor name, raw in zip(('data0_even','data0_odd','data1_even','data1_odd'), candidate_frame_block_in):
\t\t\t\t\treview_frame[name] = np.array(raw, dtype=np.int32, copy=True)
\t\t\tif isinstance(candidate_product_block_in, (tuple, list)) and len(candidate_product_block_in) == 2:
\t\t\t\tfor peer in (0, 1):
\t\t\t\t\treview_frame[f'product{peer}'] = np.array(candidate_product_block_in[peer], dtype=np.float64, copy=True)
"""
    new = """\t\t# Preserve the other antenna from this immutable detector request even
\t\t# when independent arrivals have no exact common-mode pair. This is
\t\t# display context only: synchronized and all detector gates stay intact.
\t\tpeer = 1-ch
\t\tif (isinstance(candidate_frame_block_in, (tuple, list)) and len(candidate_frame_block_in) == 4
\t\t\t\tand isinstance(candidate_product_block_in, (tuple, list)) and len(candidate_product_block_in) == 2
\t\t\t\tand candidate_product_block_in[peer] is not None
\t\t\t\tand all(candidate_frame_block_in[peer*2+p] is not None for p in (0, 1))):
\t\t\tfor p, phase in enumerate(('even', 'odd')):
\t\t\t\treview_frame[f'data{peer}_{phase}'] = np.array(candidate_frame_block_in[peer*2+p], dtype=np.int32, copy=True)
\t\t\treview_frame[f'product{peer}'] = np.array(candidate_product_block_in[peer], dtype=np.float64, copy=True)
"""
    result = once(source, old, new)
    ast.parse(result)
    return result


def patch_core(source, version):
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, 'id', '') == 'SERVICE_HTML' for t in n.targets))
    page = ast.literal_eval(node.value)
    page = once(page,
        "if(snap&&snap.reference_only)return[{text:'SYNC '+snap.context_frames+'/'+snap.context_required,tip:'Measured reference waveforms from the same source frames. No asynchronous detector decision is attached.'}];",
        "if(snap&&snap.reference_only)return[{text:(snap.synchronized?'SYNC ':'REF ')+snap.context_frames+'/'+snap.context_required,tip:snap.synchronized?'Measured reference waveforms from the same source frames. No asynchronous detector decision is attached.':" + json.dumps(REFERENCE_TIP) + "}];")
    old_tip = 'AutoFreeze: show synchronized product waveforms for both antennas from confirmed detections; older results are darker and the final result is brightest. Click to return live.'
    # Retain all existing catalogs and add the two new tooltip entries.
    match = re.search(r'const catalogs = (\{[^\n]+\});', page)
    catalogs = json.loads(match.group(1))
    for code, translations in TRANSLATIONS.items():
        catalogs[code][REFERENCE_TIP], catalogs[code][FREEZE_TIP] = translations
    page = page[:match.start(1)] + json.dumps(catalogs, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/') + page[match.end(1):]
    page = once(page, "'" + old_tip + "'", "'" + FREEZE_TIP + "'")
    lines = source.splitlines(keepends=True)
    result = ''.join(lines[:node.lineno-1]) + 'SERVICE_HTML = ' + repr(page) + '\n' + ''.join(lines[node.end_lineno:])
    result, count = re.subn(r'(DEFAULT_ENGINE_FILE\s*=\s*HOST_DIR / )[\'"][^\'\"]+[\'\"]',
                           lambda m: m.group(1) + repr('BMI30.200.py.' + version), result)
    assert count == 1
    def backend(t):
        return [ast.dump(n) for n in t.body if not (isinstance(n, ast.Assign) and
                any(getattr(x, 'id', '') in ('SERVICE_HTML', 'DEFAULT_ENGINE_FILE') for x in n.targets))]
    assert backend(tree) == backend(ast.parse(result))
    return result, page


if __name__ == '__main__':
    import sys
    folder, version = Path(sys.argv[1]), sys.argv[2]
    candidate = folder / 'candidate'
    candidate.mkdir(exist_ok=True)
    core, page = patch_core((folder/'core.py').read_text(encoding='utf8'), version)
    (candidate/'core.py').write_text(core, encoding='utf8', newline='\n')
    (candidate/'engine.py').write_text(patch_engine((folder/'engine.py').read_text(encoding='utf8')), encoding='utf8', newline='\n')
    (candidate/'portal.py').write_bytes((folder/'portal.py').read_bytes())
    (candidate/'scope.html').write_text(page, encoding='utf8', newline='\n')

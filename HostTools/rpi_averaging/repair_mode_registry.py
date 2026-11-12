"""Record the averaging mode-transition repair using the guarded media writer."""
import ast
import json
from repair_mode_switch import base, HERE, W

tree=ast.parse((HERE/'expand12_registry.py').read_text(encoding='utf8'))
script=next(ast.literal_eval(node.value) for node in tree.body
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='REMOTE' for t in node.targets))
start=script.index("values=['1'")
end=script.index('assert len(fields)==len(values)',start)
script=script[:start]+"values=p['values'];values[-1]=datetime.datetime.now().astimezone().isoformat()\n"+script[end:]
script=script.replace('bmi30-average12-registry','bmi30-average-mode-fix-registry')
script=script.replace('bmi30-averaging12','bmi30-averaging-mode-fix')
release=json.loads((W/'release.json').read_text())
values=['1',release['version'],'Установлена S07/M02: USB + eMMC',
        'Исправлен сброс усреднения после режима 3',
        'AVG_ROI проверяет непрерывный stream_seq с шагом 1. Убрана зависимость от step_a/step_b, которые могут сохранять шаг DMA из raw/latest режима. Диапазон 1–12 и настройки сохранены.',
        release['paths']['core'],release['paths']['engine'],
        'host/BMI30.GUI.001.py.2026-09-30-1650','host/bmi30_averaging.py',
        'Переход raw/latest (режим 3) -> AVG_ROI/детекция (режим 6)',
        '11 тестов локально и на обеих платах. Новый регрессионный тест падал до исправления и проходит после. На M02 реальный переход 3->6: окно 12/12/12/12, +284 результата за 6 с, оба графика меняются, ошибок потока нет. SHA256/версия совпадают на всех 4 корнях.',
        'Обе платы работают с USB. eMMC проверена офлайн без отдельной загрузки. Настройки сохранены. Физическая проверка прохода метки не выполнялась.',
        'Обычная эксплуатация и наблюдение после переключений режима.','']
for board in ['M02','S07']:
    result=base.call(board,script,'registry',{'before':json.loads((W/(board+'-before.json')).read_text()),
                     'release':release,'values':values},timeout=30)
    print(json.dumps({'board':board,**result}),flush=True)
sync_script="""
import json,subprocess
r=subprocess.run(['python3','utilities/sync_bmi30_version_registry.py','--replace'],cwd='/home/techaid/Documents',capture_output=True,text=True,timeout=40)
print(json.dumps({'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr}))
"""
result=base.call('M02',sync_script,'registry-cloud',timeout=50)
print(json.dumps(result),flush=True)

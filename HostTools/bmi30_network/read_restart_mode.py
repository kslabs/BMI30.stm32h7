from deploy import remote
print(remote('BMI30-7AA5CED27',"import json,pathlib; p=pathlib.Path('/home/techaid/Documents/host/bmi30_sel.json'); print(json.dumps(json.loads(p.read_text())))").decode())

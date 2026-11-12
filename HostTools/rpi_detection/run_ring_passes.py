"""Run temporary T33 passage logging on S10 using a verified button calibration.

Sends helper sources through SSH stdin; installs no files or services on S10.
Does not start Player Recorder or change detector settings.
"""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--distance-cm", type=float, required=True)
    parser.add_argument("--seconds", type=float, default=300)
    parser.add_argument("--channel", choices=("upper", "lower"), default="upper")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    calibration = json.loads(args.calibration.read_text(encoding="utf-8-sig"))
    if calibration.get("status") != "verified":
        parser.error("Button calibration must be verified against the user's counted sequence")
    if calibration.get("hostname") != "BMI30-D5EB8F26C" or calibration.get("mac") != "FF:24:01:26:A0:4A":
        parser.error("This runner is limited to the calibrated T33 on S10")
    mapping = calibration.get("button_gestures", {})
    actions = [mapping.get(button) for button in ("center", "up", "down")]
    if any(x not in ("tap", "x+", "x-", "y+", "y-") for x in actions) or len(set(actions)) != 3:
        parser.error("Center/up/down must have three distinct verified gestures")
    if not 1 <= args.seconds <= 3600 or not 0 < args.distance_cm < 1000:
        parser.error("Use seconds 1..3600 and a positive distance below 1000 cm")
    argv = ["remote_pass_logger", "--hostname", calibration["hostname"],
            "--mac", calibration["mac"], "--input-mode", "touch",
            "--touch-start", actions[0], "--touch-hit", actions[1], "--touch-miss", actions[2],
            "--tag", args.tag, "--distance-cm", str(args.distance_cm),
            "--seconds", str(args.seconds), "--channel", args.channel]
    if args.inspect:
        argv.append("--inspect")
    directory = Path(__file__).parent
    decoder = (directory / "ring_gestures.py").read_text(encoding="utf-8")
    logger = (directory / "remote_pass_logger.py").read_text(encoding="utf-8")
    remote = f'''
import pathlib,socket,sys,types
if socket.gethostname()!={calibration['hostname']!r}:raise RuntimeError('Unexpected host')
decoder=types.ModuleType('ring_gestures')
sys.modules['ring_gestures']=decoder
exec(compile({decoder!r},'ring_gestures.py','exec'),decoder.__dict__)
logger=types.ModuleType('remote_pass_logger')
exec(compile({logger!r},'remote_pass_logger.py','exec'),logger.__dict__)
devices=[]
for p in pathlib.Path('/sys/class/input').glob('event*'):
    try:
        if (p/'device/uniq').read_text().strip().lower()!={calibration['mac'].lower()!r}:continue
        identity=logger.device_identity('/dev/input/'+p.name,{calibration['mac']!r})
        capabilities=int((p/'device/capabilities/abs').read_text().replace(' ',''),16)
        if all(capabilities&(1<<axis) for axis in (53,54,57)):devices.append(identity['device'])
    except FileNotFoundError:continue
if len(devices)!=1:raise RuntimeError('Expected exactly one connected T33 touch interface')
sys.argv={argv!r}+['--device',devices[0]]
logger.main()
'''
    compile(remote, "remote_bundle", "exec")
    command = [r"C:\Windows\System32\OpenSSH\ssh.exe", "-F", str(Path.home() / ".ssh/bmi30_config"),
               "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
               calibration["hostname"], "nice -n 10 python3 -"]
    with args.output.open("xb") as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            process.stdin.write(remote.encode())
            process.stdin.close()
            for line in process.stdout:
                log.write(line)
                log.flush()
                record = json.loads(line)
                if record["kind"] not in ("sample", "input", "key"):
                    print(line.decode().strip(), flush=True)
            if process.wait() != 0:
                raise RuntimeError("Pass logger failed; inspect its output before using results")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()

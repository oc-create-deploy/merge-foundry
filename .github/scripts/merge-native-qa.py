"""Drive normal native touch input; QA snapshots are read-only observations."""
import json, os, pathlib, subprocess, time
from datetime import datetime, timezone
launch_utc = datetime.now(timezone.utc)
out = pathlib.Path('screenshots'); out.mkdir(exist_ok=True)
udid = os.environ['UDID']; bundle = os.environ['APP_BUNDLE_ID']
container = pathlib.Path(subprocess.check_output(['xcrun','simctl','get_app_container',udid,bundle,'data'],text=True).strip())
seq = 0
import importlib.util
spec=importlib.util.spec_from_file_location('touchbridge',pathlib.Path(__file__).with_name('native-touch-bridge.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
bridge=module.TouchBridge(out,udid,bundle)

def state():
    global container
    deadline = time.time()+300
    while time.time()<deadline:
        for p in container.rglob('qa-state.json'):
            try:
                s=json.loads(p.read_text())
                assert not s['errors'],s['errors']
                if s["frame"] >= 30 and datetime.fromisoformat(s["timestampUtc"].replace("Z","+00:00")) >= launch_utc: return s
            except (json.JSONDecodeError,FileNotFoundError): pass
        # Simulator can relocate the data container during a slow first launch.
        try:
            container = pathlib.Path(subprocess.check_output(['xcrun','simctl','get_app_container',udid,bundle,'data'],text=True).strip())
        except subprocess.CalledProcessError: pass
        time.sleep(1)
    (out/'container-probe.txt').write_text(str(container)+'\n'+'\n'.join(str(p) for p in container.rglob('*') if p.is_file()))
    probe_errors=list(container.rglob('qa-recorder-error.txt'))
    if probe_errors:
        detail=probe_errors[0].read_text()
        (out/'qa-recorder-error.txt').write_text(detail)
        raise AssertionError('Native diagnostic recorder failed: '+detail)
    raise AssertionError('Read-only native snapshot missing')

def capture(label):
    (out/(label+'.json')).write_text(json.dumps(state(),indent=2))
    subprocess.run(['xcrun','simctl','io',udid,'screenshot',str(out/(label+'.png'))],check=True)

def point(item):
    assert 0<item['x']<1 and 0<item['y']<1,item
    return f"{round(item['x']*100)}%, {round(item['y']*100)}%"

def swipe_point(item):
    return f"{item['x']*100:.4f}%, {item['y']*100:.4f}%"

def action(body):
    global seq
    seq+=1
    p=out/f'input-{seq:03}.yaml'
    p.write_text(f'appId: {bundle}\n---\n'+body+'\n')
    if bridge.proc is not None and body.startswith("- tapOn:"):
        bridge.tap_direct(body.split('"')[1])
    else: bridge.action(body)
    time.sleep(.1)

def tap(text):
    choices=[i for i in state()['items'] if i['kind']=='button' and i['interactable'] and text.casefold() in (i.get('text','').strip().casefold(),i['name'].strip().casefold())]
    assert choices,('Button missing',text)
    action('- tapOn:\n    point: "'+point(choices[0])+'"')

def val(key):return next(v['value'] for v in state()['values'] if v['key']==key)

def wait_for(predicate,seconds=12):
    end=time.time()+seconds
    while time.time()<end:
        if predicate():return
        time.sleep(.3)
    assert predicate(),'Observed merge transition timed out'
def slots():return [i for i in state()['items'] if i['kind']=='slot']
def full():return [i for i in slots() if i['text']=='Full']
def saved():return json.loads(val('YG2.saves'))
def drag(a,b):
    action('- swipe:\n    start: "'+swipe_point(a)+'"\n    end: "'+swipe_point(b)+'"\n    duration: 1500')
    time.sleep(1)
def board():return sorted((i['id'],i['text'],i['group']) for i in slots())
state();capture('01-first-launch')
# Start the touch driver with a neutral corner tap, not a model mutation.
action('- tapOn:\n    point: "98%, 98%"')
wait_for(lambda:len(full())>=2)
a,b=full()[:2];assert a['group']==b['group'],'Fresh board needs matching tutorial pair'
old=a['group'];drag(a,b)
wait_for(lambda:len(full())==1 and full()[0]['group']==old+1)
capture('02-real-merge')
# Move upgraded item into its vacated source slot via normal native drag.
source=full()[0];target=next(i for i in slots() if i['text']=='Empty')
drag(source,target)
wait_for(lambda:any(i['id']==target['id'] and i['group']==old+1 for i in full()))
capture('03-real-move')
# A release outside the board must restore the carried item rather than lose it.
before=board();drag(full()[0],{'x':.98,'y':.98})
wait_for(lambda:board()==before)
capture('04-invalid-drop-restored')
# These probes require the updated readonly telemetry export (0e8187b+).
buttons=[i for i in state()['items'] if i['kind']=='button' and i['interactable'] and 'AddItem' in (i.get('actions') or '').split(',')]
assert buttons,'Purchase UI callback not observed'
coins_before=saved()['coins'];count_before=len(full())
action('- tapOn:\n    point: "'+point(buttons[0])+'"')
wait_for(lambda:len(full())==count_before+1 and saved()['coins']<coins_before)
capture('05-purchased')
# Different tiers must not merge; both pieces and currency must survive.
a,b=full()[:2];assert a['group']!=b['group']
before=board();coins_before=saved()['coins'];drag(a,b)
wait_for(lambda:board()==before and saved()['coins']==coins_before)
capture('06-incompatible-restored')
sale=next(i for i in state()['items'] if i['kind']=='saleArea')
count_before=len(full());coins_before=saved()['coins']
drag(max(full(),key=lambda i:i['group']),sale)
wait_for(lambda:len(full())==count_before-1 and saved()['coins']>coins_before)
capture('07-sold')
coins=saved()['coins'];expected=board()
subprocess.run(['xcrun','simctl','terminate',udid,bundle],check=True)
for p in container.rglob('qa-state.json'):p.unlink()
launch_utc = datetime.now(timezone.utc)
subprocess.run(['xcrun','simctl','launch',udid,bundle,'--playabilityQA'],check=True)
wait_for(lambda:board()==expected and saved()['coins']==coins,300)
capture('08-coldlaunch')
(out/'result.json').write_text(json.dumps({'realMatchingMerge':True,'realEmptySlotMove':True,'invalidDropRestored':True,'boardCoinsColdlaunch':True,'fullProductAcceptance':False,'actualPurchase':True,'actualSale':True,'incompatibleDropRestored':True,'remainingChecks':['pauseMidDrag','visualAcceptance']}))

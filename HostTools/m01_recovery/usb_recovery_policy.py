"""Pure, fail-closed planning rules. This module never writes or formats media."""
import hashlib,json,re

SOURCE_SERIAL='b36ca3105ab38e6a'
TARGET_SERIAL='a043b0627448d72b'
def serial(value):
    value=str(value).strip().lower()
    if not re.fullmatch(r'[0-9a-f]{16}',value):raise ValueError('Invalid physical CM5 serial')
    return value
def hostname(value):return 'BMI30-'+serial(value)[-9:].upper()
def flatten(nodes,parent=None):
    result=[]
    for node in nodes:
        result.append((node,parent))
        result.extend(flatten(node.get('children',[]),node))
    return result
def containing_disk(nodes,path):
    flat=flatten(nodes);by_path={n['path']:(n,p) for n,p in flat}
    if path not in by_path:raise ValueError('Active partition not identified in inventory')
    node,parent=by_path[path]
    while parent is not None:node,parent=by_path[parent['path']]
    return node['path']
def disk_binding(inv,node):
    if not node.get('serial') and not node.get('diskseq'):
        raise ValueError('USB lacks a stable identity in this boot')
    data={k:node.get(k) for k in ('path','maj:min','serial','model','size','diskseq')}
    data['boot_id']=inv['boot_id']
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def source_binding(inv):
    if serial(inv['physical_serial'])!=SOURCE_SERIAL:raise ValueError('Source must be physical M01')
    if inv['root_source']!='/dev/mmcblk0p2' or inv['boot_source']!='/dev/mmcblk0p1':
        raise ValueError('Expected M01 eMMC root and boot must be verified first')
    return {k:inv[k] for k in ('physical_serial','boot_id','root_source','boot_source')}
def create_plan(inv,target_path,required_bytes,now_mono):
    source=source_binding(inv);nodes=inv['blockdevices']
    protected={containing_disk(nodes,inv['root_source']),containing_disk(nodes,inv['boot_source'])}
    disks={n['path']:n for n,p in flatten(nodes) if n.get('type')=='disk'}
    if target_path not in disks:raise ValueError('Target is not an observed whole disk')
    target=disks[target_path]
    if target_path in protected:raise ValueError('Active root/boot media cannot be a target')
    if target.get('tran')!='usb':raise ValueError('Only an external USB disk is allowed')
    if int(target.get('size') or 0)<int(required_bytes):raise ValueError('USB capacity is insufficient')
    for node,_ in flatten([target]):
        if node.get('ro'):raise ValueError('USB or a partition is read-only')
        if any(node.get('mountpoints') or []):raise ValueError('USB has mounted partitions')
    return {'format':1,'source':source,'target_path':target_path,'target_binding':disk_binding(inv,target),
            'target_size':int(target['size']),'required_bytes':int(required_bytes),'created_mono':float(now_mono),
            'destination_serial':TARGET_SERIAL,'destination_hostname':hostname(TARGET_SERIAL),
            'destructive_action_authorized':False,'boot_tested':False}
def revalidate(plan,inv,now_mono):
    if not 0<=float(now_mono)-plan['created_mono']<=300:raise ValueError('Plan expired')
    current=create_plan(inv,plan['target_path'],plan['required_bytes'],now_mono)
    if current['source']!=plan['source'] or current['target_binding']!=plan['target_binding']:
        raise ValueError('Source boot or USB identity changed; select media again')
    return current
def can_import_device_identity(actual_serial,bound_serial):
    return serial(actual_serial)==TARGET_SERIAL and serial(bound_serial)==TARGET_SERIAL

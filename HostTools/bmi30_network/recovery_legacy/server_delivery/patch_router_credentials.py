"""Apply the router-password selection fix without changing recovery hooks."""
import ast


def patch(source, authoritative):
    if '    def router_secret(' in source: return source
    changes=(
        ('                secret = self.profile_cache.get(choice["ssid"]) or self.secrets["wifi:" + choice["ssid"]]',
         '                secret = self.router_secret(choice["ssid"])'),
        ('        if now - self.last_profiles > 60 or not self.profile_cache:',
         "        if (now - self.last_profiles > 60 or not self.profile_cache\n                or getattr(self, 'profile_station_uuid', None) != local.get('station_uuid')):"),
        ('            self.last_profiles = now\n',"            self.last_profiles = now\n            self.profile_station_uuid = local.get('station_uuid')\n"),
        ('            if local["path"] == [local["id"]] and local["ssid"] in self.profile_cache and not access_policy.forgotten(local["ssid"]):',
         '            if (local[\'uplink\'] == \'wifi\' and local["path"] == [local["id"]]\n                    and local["ssid"] in self.profile_cache and not access_policy.forgotten(local["ssid"])):'),
        ('            profiles.update(self.profile_cache)\n',
         '            profiles.update(self.profile_cache)\n            profiles = {ssid: self.router_secret(ssid) for ssid in profiles}\n'))
    for before,after in changes:
        if source.count(before)!=1: raise ValueError('Unknown router credential selection site')
        source=source.replace(before,after,1)
    cls=next(n for n in ast.parse(authoritative).body if isinstance(n,ast.ClassDef) and n.name=='Agent')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='router_secret')
    lines=authoritative.splitlines(keepends=True)
    body=''.join(lines[method.lineno-1:method.end_lineno])+'\n\n'
    anchor='    def execute(self, choice):\n'
    assert source.count(anchor)==1
    source=source.replace(anchor,body+anchor,1)
    ast.parse(source)
    return source

"""Bounded owner EP0-read requests; never fake refresh completion or scan."""
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import Mock, patch

import network


def core(stamp=100.):
    def entry(node):
        return {'node_id':node,'host_updated_at':stamp,'complete':True,'short_valid':True,
                'short_id':'8CC938B79' if node==7 else '5F295F47B','node_id_matches_status':True,'node_conflict':False}
    return {'ok':True,'stale':False,'busy':False,'connection':{'connected':True},
            'sync':{'local_node_id':2,'device_id':2,'sync_seen_mask':132},
            'rs485_ident':{'node_count':2,'nodes':{'2':entry(2),'7':entry(7)}}}


class OwnerReadTests(unittest.TestCase):
    def run_read(self,value,opener=None):
        adapter=network.Network({'id':'BMI30-LOCAL'});opener=opener or Mock()
        opener.open.return_value=io.StringIO('{"ok":true,"refresh_started":true,"old_cache":true}')
        with patch('network.time.time',return_value=1000.),patch('network.time.monotonic',return_value=100.):
            result=adapter.refresh_stale_rid(value,opener)
        return adapter,opener,result

    def test_stale_read_uses_only_known_ids_and_explicit_scan_false_without_identity_writes(self):
        original=core();before=deepcopy(original);_,opener,result=self.run_read(original)
        self.assertTrue(result['attempted']);self.assertTrue(result['http_completed'])
        request=opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data),{'action':'rs485_ident','params':{'request_scan':False,'device_ids':[2,7]}})
        self.assertEqual(opener.open.call_args.kwargs['timeout'],1.5)
        self.assertEqual(opener.open.call_count,1);self.assertEqual(original,before)
        self.assertNotIn('fresh',result)  # immediate cached response is ignored

    def test_already_fresh_or_unavailable_or_busy_owner_is_never_called(self):
        variants=[core(stamp=990.),dict(core(),stale=True),dict(core(),ok=False),dict(core(),busy=True),dict(core(),connection={'connected':False}),dict(core(),rs485_ident=[])]
        for value in variants:
            with self.subTest(value=value):
                _,opener,result=self.run_read(value);self.assertFalse(result['attempted']);opener.open.assert_not_called()

    def test_unknown_seen_member_or_partial_map_or_more_than_four_members_does_not_infer_ids(self):
        unknown=core();unknown['sync']['sync_seen_mask']|=1<<8
        partial=core();partial['rs485_ident']['nodes']['7']['complete']=False
        wrong_count=core();wrong_count['rs485_ident']['node_count']=3
        too_many=core();too_many['rs485_ident']['nodes'].update({str(n):dict(too_many['rs485_ident']['nodes']['2'],node_id=n) for n in (3,4,5)})
        for value in (unknown,partial,wrong_count,too_many):
            _,opener,result=self.run_read(value);self.assertFalse(result['attempted']);opener.open.assert_not_called()

    def test_one_attempt_per_thirty_seconds_including_timeout_no_retry_loop(self):
        adapter=network.Network({'id':'BMI30-LOCAL'});opener=Mock();opener.open.side_effect=TimeoutError('busy owner')
        with patch('network.time.time',return_value=1000.),patch('network.time.monotonic',side_effect=[100.,110.,130.]):
            first=adapter.refresh_stale_rid(core(),opener);second=adapter.refresh_stale_rid(core(),opener);third=adapter.refresh_stale_rid(core(),opener)
        self.assertTrue(first['attempted']);self.assertFalse(first['http_completed']);self.assertFalse(second['attempted']);self.assertTrue(third['attempted'])
        self.assertEqual(opener.open.call_count,2)

    def test_thirty_second_aging_triggers_before_ttl_and_recent_flag_cannot_hide_old_host_time(self):
        for stamp in (970.,100.):
            value=core(stamp)
            for entry in value['rs485_ident']['nodes'].values():entry['recent']=True
            _,opener,result=self.run_read(value)
            self.assertTrue(result['attempted']);self.assertEqual(opener.open.call_count,1)
            self.assertTrue(all(e['host_updated_at']==stamp for e in value['rs485_ident']['nodes'].values()))


if __name__=='__main__':unittest.main()

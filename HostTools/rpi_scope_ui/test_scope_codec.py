import json
import struct
import unittest
import zlib

from scope_codec import packets


def make_frame():
    names=['data0_even','data0_odd','prod0','data1_even','data1_odd','prod1']
    # Includes jumps, wraparound, and uncorrelated values: samples stay exact.
    values={name:[(i*i*7717+j*33191)&65535 for i in range(200)] for j,name in enumerate(names)}
    h={'ok':True,'available':True,'base_buf_len':200,'count':200,'step':1,'last_frame_t':100.123,
       'arrays':names,'prod_norm0':{'scale':1.23456789},'prod_norm1':{'scale':2.3456789},'review_seq':0}
    meta=json.dumps(h,separators=(',',':')).encode()
    raw=b'BMF1'+struct.pack('<I',len(meta))+meta+(b'\0' if len(meta)%2 else b'')
    return raw+b''.join(struct.pack('<200H',*values[n]) for n in names),values


def decode(packet):
    raw=zlib.decompress(packet[4:]);size=struct.unpack('<I',raw[4:8])[0];h=json.loads(raw[8:8+size])
    offset=8+size+size%2;planes=raw[offset:];n=len(planes)//2
    data=bytes(value for pair in zip(planes[:n],planes[n:]) for value in pair)
    out={}
    for j,name in enumerate(h['arrays']):
        a=list(struct.unpack('<'+str(h['count'])+'H',data[j*h['count']*2:(j+1)*h['count']*2]))
        for _ in range(2):
            for i in range(1,len(a)):a[i]=(a[i]+a[i-1])&65535
        out[name]=a
    return h,out


class CodecTests(unittest.TestCase):
    def test_all_six_arrays_and_normalization_are_exact(self):
        raw,values=make_frame();result=packets(raw);self.assertEqual(len(result),2)
        restored={}
        for channel,packet in enumerate(result):
            self.assertEqual(packet[:4],b'BMD1');h,arrays=decode(packet)
            self.assertEqual(h['_scopeChannel'],channel);self.assertEqual(h['last_frame_t'],100.123)
            self.assertIn('prod_norm'+str(channel),h);self.assertNotIn('prod_norm'+str(1-channel),h)
            restored.update(arrays)
        self.assertEqual(restored,values)

    def test_missing_channel_does_not_create_false_samples(self):
        raw,values=make_frame();size=struct.unpack('<I',raw[4:8])[0];h=json.loads(raw[8:8+size]);h['arrays']=h['arrays'][:3]
        meta=json.dumps(h,separators=(',',':')).encode();raw=b'BMF1'+struct.pack('<I',len(meta))+meta+(b'\0' if len(meta)%2 else b'')+b''.join(struct.pack('<200H',*values[n]) for n in h['arrays'])
        result=packets(raw);self.assertEqual(len(result),1);self.assertEqual(decode(result[0])[0]['_scopeChannel'],0)

    def test_truncated_array_rejected(self):
        raw,_=make_frame()
        with self.assertRaises(ValueError):packets(raw[:-1])

    def test_width_annotations_belong_to_the_same_frame_and_channel(self):
        raw,values=make_frame();size=struct.unpack('<I',raw[4:8])[0];h=json.loads(raw[8:8+size])
        h['prod_peak0']={'sample':39,'amplitude':3.125,'raw_amplitude':51200,
            'base_width':{'left':30,'right':50,'width':20,'limit':25,'enabled':True,'valid':True,'ok':True,
                          'left_kind':'minimum','right_kind':'zero','reason':'measured'}}
        h['prod_peak1']={'sample':107,'amplitude':8.75,'raw_amplitude':143360,
            'base_width':{'left':90,'right':125,'width':35,'limit':25,'enabled':True,'valid':True,'ok':False,
                          'left_kind':'zero','right_kind':'minimum','reason':'too_wide'}}
        meta=json.dumps(h,separators=(',',':')).encode()
        raw=b'BMF1'+struct.pack('<I',len(meta))+meta+(b'\0' if len(meta)%2 else b'')+b''.join(struct.pack('<200H',*values[n]) for n in h['arrays'])
        for channel,packet in enumerate(packets(raw)):
            decoded,_=decode(packet);key=f'prod_peak{channel}'
            self.assertEqual(decoded[key],h[key]);self.assertNotIn(f'prod_peak{1-channel}',decoded)
            self.assertEqual(decoded['last_frame_t'],h['last_frame_t'])

    def test_unknown_boundaries_remain_unknown(self):
        raw,values=make_frame();size=struct.unpack('<I',raw[4:8])[0];h=json.loads(raw[8:8+size])
        h['prod_peak0']={'sample':0,'base_width':{'left':None,'right':3,'width':None,
            'limit':25,'enabled':True,'valid':False,'ok':False,'reason':'open_edge'}}
        h['prod_peak1']=None
        meta=json.dumps(h,separators=(',',':')).encode()
        raw=b'BMF1'+struct.pack('<I',len(meta))+meta+(b'\0' if len(meta)%2 else b'')+b''.join(struct.pack('<200H',*values[n]) for n in h['arrays'])
        result=packets(raw)
        self.assertEqual(decode(result[0])[0]['prod_peak0'],h['prod_peak0'])
        self.assertIsNone(decode(result[1])[0]['prod_peak1'])


if __name__=='__main__':unittest.main()

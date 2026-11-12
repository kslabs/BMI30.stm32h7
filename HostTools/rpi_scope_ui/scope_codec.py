"""Lossless per-channel display packets; BMF1 snapshots remain unchanged."""
import json
import struct
import zlib


def packets(raw):
    size = struct.unpack('<I', raw[4:8])[0]
    header = json.loads(raw[8:8 + size])
    count = int(header.get('count') or 0)
    if count <= 0 or not header.get('arrays'):
        return [b'BMC1' + zlib.compress(raw, 1)]
    offset = 8 + size + size % 2
    arrays = {}
    for name in header['arrays']:
        end = offset + count * 2
        if end > len(raw):
            raise ValueError('Truncated display array')
        arrays[name] = raw[offset:end]
        offset = end
    result = []
    for channel in (0, 1):
        names = [name for name in (f'data{channel}_even', f'data{channel}_odd', f'prod{channel}') if name in arrays]
        if not names:
            continue
        small = {key: header[key] for key in ('ok', 'available', 'base_buf_len', 'step', 'count', 'last_frame_t')}
        for key in (f'prod_norm{channel}', f'prod_peak{channel}'):
            if key in header:
                small[key] = header[key]
        small['arrays'], small['_scopeChannel'] = names, channel
        meta = json.dumps(small, separators=(',', ':')).encode()
        payload = bytearray()
        for name in names:
            values = struct.unpack('<' + str(count) + 'H', arrays[name])
            first = [values[0]] + [(values[i] - values[i-1]) & 65535 for i in range(1, count)]
            second = [first[0]] + [(first[i] - first[i-1]) & 65535 for i in range(1, count)]
            payload.extend(struct.pack('<' + str(count) + 'H', *second))
        # Byte planes improve deflate's compression without changing any sample.
        data = b'BMF1' + struct.pack('<I', len(meta)) + meta + (b'\0' if len(meta) % 2 else b'')
        data += payload[0::2] + payload[1::2]
        # Keep restored annotation metadata compact as well as sample planes.
        result.append(b'BMD1' + zlib.compress(data, 3))
    return result

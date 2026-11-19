"""纯 Python AES（128/192/256 位，CBC 模式）——顺丰国际开放平台报文加解密用。
不依赖 pycryptodome/cryptography，Windows 内嵌 Python 免装 C 扩展。报文只有几百字节，性能足够。"""

def _build_tables():
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)          # p *= 3
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09                                                   # q /= 3
        x = q ^ ((q << 1) | (q >> 7)) & 0xFF ^ ((q << 2) | (q >> 6)) & 0xFF ^ ((q << 3) | (q >> 5)) & 0xFF ^ ((q << 4) | (q >> 4)) & 0xFF
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv


SBOX, INV_SBOX = _build_tables()


def _xt(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xt(a)
        b >>= 1
    return r


MUL9 = [_mul(i, 9) for i in range(256)]
MUL11 = [_mul(i, 11) for i in range(256)]
MUL13 = [_mul(i, 13) for i in range(256)]
MUL14 = [_mul(i, 14) for i in range(256)]
MUL2 = [_mul(i, 2) for i in range(256)]
MUL3 = [_mul(i, 3) for i in range(256)]


def _expand_key(key: bytes):
    nk = len(key) // 4
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must be 16/24/32 bytes")
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= rcon
            rcon = _xt(rcon)
        elif nk > 6 and i % nk == 4:
            t = [SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    rks = []
    for r in range(nr + 1):
        rk = []
        for c in range(4):
            rk.extend(w[4 * r + c])
        rks.append(rk)
    return rks, nr


def _encrypt_block(block, rks, nr):
    s = [block[i] ^ rks[0][i] for i in range(16)]
    for r in range(1, nr + 1):
        s = [SBOX[b] for b in s]
        # ShiftRows（列主序存储）
        s = [s[0], s[5], s[10], s[15], s[4], s[9], s[14], s[3], s[8], s[13], s[2], s[7], s[12], s[1], s[6], s[11]]
        if r != nr:
            n = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                n += [MUL2[a0] ^ MUL3[a1] ^ a2 ^ a3, a0 ^ MUL2[a1] ^ MUL3[a2] ^ a3,
                      a0 ^ a1 ^ MUL2[a2] ^ MUL3[a3], MUL3[a0] ^ a1 ^ a2 ^ MUL2[a3]]
            s = n
        s = [s[i] ^ rks[r][i] for i in range(16)]
    return bytes(s)


def _decrypt_block(block, rks, nr):
    s = [block[i] ^ rks[nr][i] for i in range(16)]
    for r in range(nr - 1, -1, -1):
        # InvShiftRows
        s = [s[0], s[13], s[10], s[7], s[4], s[1], s[14], s[11], s[8], s[5], s[2], s[15], s[12], s[9], s[6], s[3]]
        s = [INV_SBOX[b] for b in s]
        s = [s[i] ^ rks[r][i] for i in range(16)]
        if r != 0:
            n = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                n += [MUL14[a0] ^ MUL11[a1] ^ MUL13[a2] ^ MUL9[a3], MUL9[a0] ^ MUL14[a1] ^ MUL11[a2] ^ MUL13[a3],
                      MUL13[a0] ^ MUL9[a1] ^ MUL14[a2] ^ MUL11[a3], MUL11[a0] ^ MUL13[a1] ^ MUL9[a2] ^ MUL14[a3]]
            s = n
    return bytes(s)


def cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """data 长度必须是 16 的倍数（填充由调用方负责）。"""
    if len(data) % 16:
        raise ValueError("data length must be a multiple of 16")
    rks, nr = _expand_key(key)
    out, prev = [], iv
    for i in range(0, len(data), 16):
        blk = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        prev = _encrypt_block(blk, rks, nr)
        out.append(prev)
    return b"".join(out)


def cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    if len(data) % 16:
        raise ValueError("ciphertext length must be a multiple of 16")
    rks, nr = _expand_key(key)
    out, prev = [], iv
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        out.append(bytes(a ^ b for a, b in zip(_decrypt_block(blk, rks, nr), prev)))
        prev = blk
    return b"".join(out)

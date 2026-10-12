import struct,sys,os
G="C:/Program Files (x86)/Steam/steamapps/common/Left 4 Dead 2/"
def extract(dirvpk, want, out):
    f=open(G+dirvpk,'rb').read()
    sig,ver,tree=struct.unpack_from('<III',f,0); o=12 if ver==1 else 28; hdr=o
    def s():
        nonlocal o
        e=f.index(b'\0',o); r=f[o:e].decode('latin-1'); o=e+1; return r
    while True:
        ext=s()
        if not ext: break
        while True:
            d=s()
            if not d: break
            while True:
                n=s()
                if not n: break
                crc,pre,arc,off,ln,term=struct.unpack_from('<IHHIIH',f,o); o+=18
                data=f[o:o+pre]; o+=pre
                full=f"{d}/{n}.{ext}"
                if full in want:
                    if ln:
                        a=open(G+dirvpk.replace('_dir',f'_{arc:03d}'),'rb'); a.seek(off); data+=a.read(ln)
                    p=os.path.join(out,full.replace('/','__')); open(p,'wb').write(data); print('extracted',p,len(data))
extract(sys.argv[1], set(sys.argv[2:]), 'game')

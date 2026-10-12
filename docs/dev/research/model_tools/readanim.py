import struct,sys,math
b=open(sys.argv[1],'rb').read()
I=lambda o:struct.unpack_from('<i',b,o)[0]
na,ai=I(180),I(184)
for a in range(na):
    o=ai+a*100
    name=b[o+I(o+4):].split(b'\0')[0].decode()
    fps=struct.unpack_from('<f',b,o+8)[0]; flags=I(o+12); nf=I(o+16)
    ao=o+I(o+56)
    print('anim',name,'fps',fps,'flags',hex(flags),'frames',nf,'sectionframes',I(o+84))
    while True:
        bone,fl,nxt=struct.unpack_from('<BBh',b,ao)
        d=ao+4; s=''
        if fl&0x20:
            v=struct.unpack_from('<Q',b,d)[0]
            x=((v&0x1FFFFF)-1048576)/1048576.5;y=(((v>>21)&0x1FFFFF)-1048576)/1048576.5;z=(((v>>42)&0x1FFFFF)-1048576)/1048576.5
            w=math.sqrt(max(0,1-x*x-y*y-z*z)); w=-w if v>>63 else w
            s+=f' rot64 xyzw=({x:.4f},{y:.4f},{z:.4f},{w:.4f})'; d+=8
        if fl&0x02:
            x,y,zz=struct.unpack_from('<HHH',b,d)
            X=(x-32768)/32768;Y=(y-32768)/32768;Z=((zz&0x7fff)-16384)/16384
            w=math.sqrt(max(0,1-X*X-Y*Y-Z*Z)); w=-w if zz>>15 else w
            s+=f' rot48 ({X:.4f},{Y:.4f},{Z:.4f},{w:.4f})'; d+=6
        if fl&0x08: s+=' animrot'; d+=6
        if fl&0x01:
            import numpy
            p=numpy.frombuffer(b[d:d+6],dtype=numpy.float16); s+=f' rawpos {p}'
        if fl&0x04: s+=' animpos'
        print('  bone',bone,'flags',hex(fl),s)
        if nxt==0: break
        ao+=nxt

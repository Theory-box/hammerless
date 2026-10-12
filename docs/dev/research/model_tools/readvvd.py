import struct,sys,math
b=open(sys.argv[1],'rb').read()
n=struct.unpack_from('<i',b,16)[0]; vs=struct.unpack_from('<i',b,56)[0]
seen=set()
for i in range(n):
    o=vs+i*48
    w=struct.unpack_from('<3f',b,o); bones=b[o+12:o+15]; nb=b[o+15]
    p=struct.unpack_from('<3f',b,o+16)
    k=(tuple(round(x,2) for x in p))
    print(nb,list(bones[:nb]),[round(x,3) for x in w[:nb]],k)

function check(v,msg){if(!v)throw msg;}
local r=[Vector(0,0,0),Vector(10,0,0),Vector(20,0,0)];
check(!HLRoute.Point(null,0).found,"missing");check(!HLRoute.Point([],0).found,"empty");
foreach(i in [-1,1.5,3,"0"])check(!HLRoute.Point(r,i).found,"bad index");
check(HLRoute.Point(r,1).position.x==10,"ordered point");
local copied=HLRoute.Point(r,1);copied.position.x=200;check(r[1].x==10,"copy output");
check(HLRoute.Nearest(r,Vector(5,0,0)).index==0,"first tie");
check(HLRoute.Nearest(r,Vector(18,0,0)).index==2,"nearest");
local far=HLRoute.Nearest([Vector(2e16,0,0),Vector(1e16,0,0)],Vector(0,0,0));check(far.index==1&&far.distance>1e15,"large finite distances");
check(!HLRoute.Nearest(r,null).found,"bad origin");
local a=HLRoute.Advance(r,2,1,"ONCE");check(a.completed&&a.index==2,"once end");
a=HLRoute.Advance(r,0,-1,"ONCE");check(a.completed&&a.index==0,"once reverse end");
a=HLRoute.Advance(r,1,-1,"ONCE");check(!a.completed&&a.index==0&&a.direction== -1,"reverse");
a=HLRoute.Advance(r,2,1,"LOOP");check(a.index==0&&!a.completed,"loop wrap");
a=HLRoute.Advance(r,0,-1,"LOOP");check(a.index==2&&a.direction== -1,"reverse loop");
a=HLRoute.Advance(r,2,1,"PINGPONG");check(a.index==1&&a.direction== -1,"bounce no duplicate endpoint");
a=HLRoute.Advance(r,0,-1,"PINGPONG");check(a.index==1&&a.direction==1,"bounce forward");
check(!HLRoute.Advance(r,0,0,"ONCE").found,"zero direction");
foreach(mode in ["LOOP","PINGPONG"]){a=HLRoute.Advance([Vector(0,0,0)],0,1,mode);check(a.index==0&&!a.completed,"single looping point");}
check(HLRoute.Advance([Vector(0,0,0)],0,1,"ONCE").completed,"single complete");

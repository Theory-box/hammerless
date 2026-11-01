::clock <- 0.0;
::Time <- function(){return clock;};
::printl <- function(s){};
class Vec {
 x=0;y=0;z=0;
 constructor(a,b,c){x=a;y=b;z=c;}
 function _sub(v){return Vec(x-v.x,y-v.y,z-v.z);}
 function LengthSqr(){return x*x+y*y+z*z;}
 function Length(){return sqrt(LengthSqr());}
}
::Vector <- function(x,y,z){return Vec(x,y,z);};
::IsPlayerABot <- function(b){return true;};
::moves <- 0; ::resets <- 0;
::CommandABot <- function(args){if(args.cmd==1)moves++;if(args.cmd==3)resets++;return true;};
class Bot {p=null;constructor(){p=Vector(-20,0,0);}function IsValid(){return true;}function GetHealth(){return 100;}function GetClassname(){return "infected";}function GetOrigin(){return p;}function GetCenter(){return p;}function GetAngles(){return Vector(0,0,0);}}
function check(v,msg){if(!v)throw msg;}


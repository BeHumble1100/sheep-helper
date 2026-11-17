// Compact static-board search. Every exported path must be replayed in Python.
// See native/README.md for state encoding and search phases.
using System;
using System.IO;
using System.Collections.Generic;
using System.Diagnostics;
using System.Web.Script.Serialization;
class FastSolver {
 struct Bits : IEquatable<Bits> {
  public ulong a,b,c,d;
  public bool Has(int i){ulong v=i<64?a:i<128?b:i<192?c:d;return (v&(1UL<<(i&63)))!=0;}
  public void Set(int i){ulong v=1UL<<(i&63);if(i<64)a|=v;else if(i<128)b|=v;else if(i<192)c|=v;else d|=v;}
  public void Clear(int i){ulong v=~(1UL<<(i&63));if(i<64)a&=v;else if(i<128)b&=v;else if(i<192)c&=v;else d&=v;}
  public bool Intersects(Bits x){return ((a&x.a)|(b&x.b)|(c&x.c)|(d&x.d))!=0;}
  public bool Empty{get{return (a|b|c|d)==0;}}
  public bool Equals(Bits x){return a==x.a&&b==x.b&&c==x.c&&d==x.d;}
  public override int GetHashCode(){unchecked{ulong h=a*0x9E3779B185EBCA87UL+b*0xC2B2AE3D27D4EB4FUL+c*0x165667B19E3779F9UL+d;return (int)(h^(h>>32));}}
 }
 struct State { public Bits rem,avail;public ulong tray;public uint p0,p1,p2;public byte flags;public int picked; }
 struct Key : IEquatable<Key> {public Bits rem;public ulong tray;public uint park;
  public bool Equals(Key x){return rem.Equals(x.rem)&&tray==x.tray&&park==x.park;}
  public override bool Equals(object x){return x is Key&&Equals((Key)x);}
  public override int GetHashCode(){unchecked{return rem.GetHashCode()*397+((int)tray^(int)(tray>>32))*31+(int)park;}}
 }
 static HashSet<Bits> countSeen;static int[] leftCounts;static int countRound;static int n;static double lookWeight;static double[] colorBias=new double[16];static Bits[] ancestor;static bool[] built;static int[] kinds,layers;static Bits[] blockers;static int[][] deps;
 static int[][] actions=new int[300][], trays=new int[300][],counts=new int[300][];static double[][] scores=new double[300][];
 static int[] path=new int[300],bestPath=new int[0],localPath=new int[0];
 static int bestPicked,localPicked,branches;static long nodes,stopNodes;static double deadline,end,jitter,layerWeight;static uint random=1;
 static Stopwatch clock=Stopwatch.StartNew();static HashSet<Key> seen;static List<int[]> archive=new List<int[]>();static JavaScriptSerializer json=new JavaScriptSerializer();static string output;
 static double Rand(){random^=random<<13;random^=random>>17;random^=random<<5;return random/(double)uint.MaxValue;}
 static int Len(ulong x){int n=0;while(x!=0){n++;x>>=9;}return n;}
 static uint Push(uint p,int card){return p==0?(uint)(card+1):p|((uint)(card+1)<<9);}
 static int Peek(uint p){return (int)((p>>9)!=0?p>>9:p)-1;}
 static uint Pop(uint p){return (p>>9)!=0?p&511U:0;}
 static uint Park(State s,int i){return i==0?s.p0:i==1?s.p1:s.p2;}
 static void SetPark(ref State s,int i,uint v){if(i==0)s.p0=v;else if(i==1)s.p1=v;else s.p2=v;}
 static bool Apply(State s,int act,out State t){
  t=s;int[] buffer=new int[7];int len=0;ulong v=s.tray;while(v!=0){buffer[len++]=(int)(v&511)-1;v>>=9;}
  if(act==-1){if((s.flags&1)!=0||len==0)return false;for(int i=0;i<Math.Min(3,len);i++)SetPark(ref t,i,Push(Park(t,i),buffer[i]));t.tray=s.tray>>Math.Min(3,len)*9;t.flags|=1;return true;}
  int card;
  if(act>=0){card=act;if(!s.avail.Has(card))return false;t.rem.Clear(card);t.avail.Clear(card);t.picked++;
   foreach(int dep in deps[card])if(t.rem.Has(dep)&&!blockers[dep].Intersects(t.rem))t.avail.Set(dep);
  }else{int slot=-act-2;uint p=Park(s,slot);if(p==0)return false;card=Peek(p);SetPark(ref t,slot,Pop(p));}
  int count=0,insert=len;
  for(int i=0;i<len;i++)if(kinds[buffer[i]]==kinds[card]){count++;insert=i+1;}
  if(count==2){int w=0;for(int i=0;i<len;i++)if(kinds[buffer[i]]!=kinds[card])buffer[w++]=buffer[i];len=w;}
  else{for(int i=len;i>insert;i--)buffer[i]=buffer[i-1];buffer[insert]=card;len++;}
  if(len==7){if((s.flags&2)!=0)return false;for(int i=0;i<3;i++)SetPark(ref t,i,Push(Park(t,i),buffer[i]));for(int i=3;i<len;i++)buffer[i-3]=buffer[i];len-=3;t.flags|=2;}
  t.tray=0;for(int i=0;i<len;i++)t.tray|=(ulong)(buffer[i]+1)<<(i*9);return true;
 }
 static Key MakeKey(State s){Key key=new Key();key.rem=s.rem;int[] typeCounts=new int[16];int length=0;ulong v=s.tray;ulong types=0;
  while(v!=0){int k=kinds[(int)(v&511)-1];typeCounts[k]++;types|=(ulong)(k+1)<<(length++*5);v>>=9;}
  if(s.flags==3){types=0;int z=0;for(int k=0;k<16;k++)for(int j=0;j<typeCounts[k];j++)types|=(ulong)(k+1)<<(z++*5);}
  key.tray=types|((ulong)s.flags<<32);
  uint[] ps=new uint[3];for(int i=0;i<3;i++){uint p=Park(s,i);uint a=p&511,b=p>>9;ps[i]=(a==0?0:(uint)kinds[a-1]+1)|((b==0?0:(uint)kinds[b-1]+1)<<5);}
  if(s.flags==3)Array.Sort(ps);key.park=ps[0]|(ps[1]<<10)|(ps[2]<<20);return key;
 }
 static void Record(State s,int depth){if(s.picked>localPicked){localPicked=s.picked;localPath=new int[depth];Array.Copy(path,localPath,depth);}
  if(s.picked>bestPicked){bestPicked=s.picked;bestPath=new int[depth];Array.Copy(path,bestPath,depth);Console.WriteLine("best="+bestPicked+" states="+nodes+" seconds="+clock.Elapsed.TotalSeconds.ToString("F2"));}
 }
 static Bits Anc(int i){if(built[i])return ancestor[i];Bits m=blockers[i];for(int j=0;j<n;j++)if(blockers[i].Has(j)){Bits v=Anc(j);m.a|=v.a;m.b|=v.b;m.c|=v.c;m.d|=v.d;}m.Set(i);ancestor[i]=m;built[i]=true;return m;}
 static int Count(ulong x){x=x-((x>>1)&0x5555555555555555UL);x=(x&0x3333333333333333UL)+((x>>2)&0x3333333333333333UL);return (int)((((x+(x>>4))&0x0F0F0F0F0F0F0F0FUL)*0x0101010101010101UL)>>56);}
 static int Cost(Bits a,Bits b){return Count(a.a&b.a)+Count(a.b&b.b)+Count(a.c&b.c)+Count(a.d&b.d);}
 static bool Visit(State s,int depth){
  nodes++;if((nodes&255)==0&&(nodes>=stopNodes||clock.Elapsed.TotalSeconds>=deadline))return false;
  Record(s,depth);if(s.rem.Empty&&s.tray==0&&s.p0==0&&s.p1==0&&s.p2==0){bestPath=new int[depth];Array.Copy(path,bestPath,depth);return true;}
  if(!seen.Add(MakeKey(s)))return false;
  int[] cs=counts[depth];Array.Clear(cs,0,16);ulong tv=s.tray;int occupied=0;while(tv!=0){cs[kinds[(int)(tv&511)-1]]++;occupied++;tv>>=9;}
  double[] bonus=new double[n];
  if(lookWeight>0)for(int k=0;k<16;k++)if(cs[k]>0){int best=-1,second=-1,cost=999,other=999;
   for(int i=0;i<n;i++)if(kinds[i]==k&&s.rem.Has(i)){int c=Cost(ancestor[i],s.rem);if(c<cost){second=best;other=cost;best=i;cost=c;}else if(c<other){second=i;other=c;}}
   if(best>=0)for(int j=0;j<n;j++)if(s.avail.Has(j)&&ancestor[best].Has(j))bonus[j]+=lookWeight*(cs[k]==2?3:1)/cost;
   if(cs[k]==1&&second>=0)for(int j=0;j<n;j++)if(s.avail.Has(j)&&ancestor[second].Has(j))bonus[j]+=lookWeight/other;
  }
  int num=0;int[] acts=actions[depth];double[] sc=scores[depth];
  for(int i=0;i<n;i++)if(s.avail.Has(i))acts[num++]=i;
  for(int i=0;i<3;i++)if(Park(s,i)!=0)acts[num++]=-i-2;
  if((s.flags&1)==0&&occupied>0)acts[num++]=-1;
  for(int q=0;q<num;q++){
   int act=acts[q];if(act==-1){sc[q]=occupied>=6?110:28+Rand()*jitter;continue;}
   int card=act>=0?act:Peek(Park(s,-act-2));int held=cs[kinds[card]];
   double score=(held==2?180:held==1?72:0)+(occupied>=5?(held>0?42:-65):0)+(act<0&&held>0?8:0)+Rand()*jitter;
   if(act>=0){score+=layerWeight*layers[card]+bonus[card]+colorBias[kinds[card]];Bits rem=s.rem;rem.Clear(card);foreach(int dep in deps[card])if(!blockers[dep].Intersects(rem)){int c=cs[kinds[dep]];score+=c==2?16:c==1?5:1;}}
   sc[q]=score;
  }
  for(int i=1;i<num;i++){double val=sc[i];int act=acts[i],j=i-1;while(j>=0&&sc[j]<val){sc[j+1]=sc[j];acts[j+1]=acts[j];j--;}sc[j+1]=val;acts[j+1]=act;}
  for(int i=0;i<num;i++){
   int act=acts[i];if(i>=branches){if(act==-1)continue;int card=act>=0?act:Peek(Park(s,-act-2));if(cs[kinds[card]]!=2)continue;}
   State child;if(!Apply(s,act,out child))continue;path[depth]=act;if(Visit(child,depth+1))return true;
   if(nodes>=stopNodes||clock.Elapsed.TotalSeconds>=deadline)return false;
  }
  return false;
 }
 static int[] Ints(object x){var a=new List<int>();foreach(object v in (System.Collections.IEnumerable)x)a.Add(Convert.ToInt32(v));return a.ToArray();}

 static bool VisitCounts(State s,int depth){
  nodes++;if((nodes&255)==0&&(nodes>=stopNodes||clock.Elapsed.TotalSeconds>=deadline))return false;
  Record(s,depth);if(s.rem.Empty&&s.tray==0){bestPath=new int[depth];Array.Copy(path,bestPath,depth);return true;}
  if(!countSeen.Add(s.rem))return false;
  int[] cs=counts[depth];Array.Clear(cs,0,16);ulong tv=s.tray;int occupied=0;while(tv!=0){cs[kinds[(int)(tv&511)-1]]++;occupied++;tv>>=9;}
  int num=0;int[] acts=actions[depth];double[] sc=scores[depth];
  for(int card=0;card<n;card++)if(s.avail.Has(card)){
   int k=kinds[card],held=cs[k];if(occupied>=6&&held!=2)continue;
   double val=(held==2?100:held==1?50:0)+(leftCounts[k]>=3?30:leftCounts[k]==2?20:0)+2*layers[card]+(occupied>=5&&held>0?40:0);
   if(countRound>0)val+=Rand()*0.1;
   acts[num]=card;sc[num++]=val;
  }
  for(int i=1;i<num;i++){double val=sc[i];int act=acts[i],j=i-1;while(j>=0&&sc[j]<val){sc[j+1]=sc[j];acts[j+1]=acts[j];j--;}sc[j+1]=val;acts[j+1]=act;}
  for(int i=0;i<Math.Min(6,num);i++){int card=acts[i];State child;if(!Apply(s,card,out child))continue;path[depth]=card;leftCounts[kinds[card]]--;
   bool solved=VisitCounts(child,depth+1);leftCounts[kinds[card]]++;if(solved)return true;
   if(nodes>=stopNodes||clock.Elapsed.TotalSeconds>=deadline)return false;
  }
  return false;
 }
 static void Main(string[] args){try{Run(args);}catch(Exception e){Console.WriteLine(e.GetType().FullName);Console.WriteLine(e.Message);Environment.Exit(1);}}
 static void Run(string[] args){Console.WriteLine("STARTING");
  var data=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));output=args[1];double seconds=Double.Parse(args[2],System.Globalization.CultureInfo.InvariantCulture);end=clock.Elapsed.TotalSeconds+seconds;
  kinds=Ints(data["types"]);n=kinds.Length;if(n>256)throw new Exception("prototype supports at most 256 tiles");layers=Ints(data["layers"]);var bs=(System.Collections.IList)data["blockers"];blockers=new Bits[n];deps=new int[n][];var dl=new List<int>[n];for(int i=0;i<n;i++)dl[i]=new List<int>();
  State initial=new State();for(int i=0;i<n;i++){initial.rem.Set(i);foreach(int j in Ints(bs[i])){blockers[i].Set(j);dl[j].Add(i);}if(blockers[i].Empty)initial.avail.Set(i);}for(int i=0;i<n;i++)deps[i]=dl[i].ToArray();ancestor=new Bits[n];built=new bool[n];for(int i=0;i<n;i++)Anc(i);
  for(int i=0;i<300;i++){actions[i]=new int[n+4];scores[i]=new double[n+4];trays[i]=new int[7];counts[i]=new int[16];}

  if(data.ContainsKey("traces")){
   var results=new List<object>();
   foreach(object raw in (System.Collections.IEnumerable)data["traces"]){State current=initial;bool legal=true;
    foreach(int act in Ints(raw)){State next;if(!Apply(current,act,out next)){legal=false;break;}current=next;}
    var remaining=new List<int>();var available=new List<int>();for(int i=0;i<n;i++){if(current.rem.Has(i))remaining.Add(i);if(current.avail.Has(i))available.Add(i);}
    var trayList=new List<int>();ulong tv=current.tray;while(tv!=0){trayList.Add((int)(tv&511)-1);tv>>=9;}
    var parked=new List<int[]>();for(int i=0;i<3;i++){uint p=Park(current,i);var slot=new List<int>();if((p&511)!=0)slot.Add((int)(p&511)-1);if((p>>9)!=0)slot.Add((int)(p>>9)-1);parked.Add(slot.ToArray());}
    results.Add(new { legal=legal,remaining=remaining,available=available,tray=trayList,parked=parked,move_out=(current.flags&1)!=0,revive=(current.flags&2)!=0 });
   }
   File.WriteAllText(output,json.Serialize(results));return;
  }

  if(data.ContainsKey("prefixes"))foreach(object a in (System.Collections.IEnumerable)data["prefixes"])archive.Add(Ints(a));
  double[] jitters={12,30,60,120},weights={-2,0,1,2,4};int[] widths={6,12,32,256},backs={6,12,24,48,80,120};
  bool solved=false;int restart=0;
  for(countRound=0;countRound<10000&&clock.Elapsed.TotalSeconds<end*0.9;countRound++){
   random=(uint)(countRound*17749+73);leftCounts=new int[16];foreach(int k in kinds)leftCounts[k]++;
   countSeen=new HashSet<Bits>();localPicked=-1;localPath=new int[0];stopNodes=nodes+1470000;deadline=Math.Min(end,clock.Elapsed.TotalSeconds+2.0);
   if(VisitCounts(initial,0)){solved=true;break;}
   int[] prefix=localPath;archive.Add(prefix);State state=initial;int depth=0;foreach(int act in prefix){State next;if(!Apply(state,act,out next))throw new Exception("bad count prefix");state=next;path[depth++]=act;}
   seen=new HashSet<Key>();localPicked=-1;localPath=new int[0];stopNodes=nodes+2030000;deadline=Math.Min(end,clock.Elapsed.TotalSeconds+2.0);
   branches=8;jitter=12;layerWeight=2;lookWeight=0;Array.Clear(colorBias,0,16);
   if(Visit(state,depth)){solved=true;break;}
   if(localPath.Length>0)archive.Add(localPath);
  }

  while(!solved&&clock.Elapsed.TotalSeconds<end){
   restart++;random=(uint)(restart*17749+73);seen=new HashSet<Key>();localPicked=-1;localPath=new int[0];
   State start=initial;int depth=0;
   if(archive.Count>0&&Rand()<0.8){int[] src=(bestPath.Length>0&&Rand()<0.5)?bestPath:archive[(int)(Rand()*archive.Count)%archive.Count];int cut=Math.Max(0,src.Length-backs[(int)(Rand()*backs.Length)%backs.Length]);
    for(int i=0;i<cut;i++){State next;if(!Apply(start,src[i],out next))throw new Exception("invalid seed prefix");start=next;path[depth++]=src[i];}
   }
   lookWeight=new double[]{0,40,200,800}[restart%4];for(int k=0;k<16;k++)colorBias[k]=(Rand()-.5)*80;jitter=jitters[(restart/5)%jitters.Length];layerWeight=weights[restart%weights.Length];branches=widths[(restart/3)%widths.Length];
   stopNodes=nodes+150000;deadline=Math.Min(end,clock.Elapsed.TotalSeconds+0.5);
   if(Visit(start,depth)){solved=true;break;}
   if(localPicked>=bestPicked-24&&localPath.Length>0){archive.Add(localPath);if(archive.Count>48)archive.RemoveAt((int)(Rand()*(archive.Count-1)));}
   if(restart%40==0)Console.WriteLine("restarts="+restart+" best="+bestPicked+" states="+nodes);
  }
  File.WriteAllText(output,json.Serialize(new { solved=solved,actions=bestPath,states=nodes,seconds=clock.Elapsed.TotalSeconds,restarts=restart,best=bestPicked }));
  Console.WriteLine("DONE solved="+solved+" best="+bestPicked+" states="+nodes+" seconds="+clock.Elapsed.TotalSeconds);
 }
}

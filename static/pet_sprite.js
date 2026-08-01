/* pet_sprite.js —— 像素小人的"画法"，养成页和牧场页共用。
   放到 static/ 目录。以后加新版本小人，只在这个文件的 VERSIONS 里加一张 body 即可。 */
 
const BODY = [
"...........KK...KK..KK..........",".........KKKKKKKKKKKKKK.........",".......KKKKKKKKKKKKKKKKKK.......","......KKKKKKKKKKKKKKKKKKKK......",
".....KKKKKKKKKKKKKKKKKKKKKK.....",".....KKKKKKKKKKKKkkkKKKKKKK.....",".....KKKKKKKKKKKKKKKKKKKKKK.....",".....KKKKKKKKKKKKKKKKKKKKKK.....",
".....KKKKKKKSSSSSSSSSSSSKKK.....",".....KKKKKSSSSSSSSSSSSSSKKK.....",".....KKKKSNNNNSSSSNNNNSSKKK.....",".....KKFFFFFFFFFFFFFFFFFFKK.....",
".....KKFWWWWWWFSSFWWWWWWFKK.....",".....KKFWPPPPWFSSFWPPPPWFKK.....",".....KKFWWEEEWFSSFWWEEEWFKK.....",".....KKFWWWWWWFSSFWWWWWWFKK.....",
".....KKFWWWWWWFSSFWWWWWWFKK.....",".....KKFFFFFFFFFFFFFFFFFFKK.....",".....KKSSSSSSSSsssSSSSSSSKK.....",".....KKSSSSSSSwwwwwwSSSSSKK.....",
".....KKSSSSSSSSMMMMSSSSSSKK.....",".....KKSBSSSSSSSSSSSSSSBSKK.....",".........SSSSSSSSSSSSSS.........","...........SSSSSSSSSS...........",
".............ssssss.............","..........TTTttttttTTT..........","........TTTTTTTTTTTTTTTT........","........TTTTTTTTTTTTTTTT........",
"........TtTTTTTTTTTTTTtT........","........SSTTTTTTTTTTTTSS........","........SSTTTTTTTTTTTTSS........",".........STTTTTTTTTTTTS.........",
"...........DDDDDDDDDD...........","...........HHHH..HHHH..........."];
const COCOON = [
"................................","................................","................................","................................",
"...........KKKKKKKKKK...........","..........KKKKKKKKKKKK..........","..........KKKKKKKKKKKK..........","..........KKSSSSSSSSKK..........",
"..........KKSPPSSPPSKK..........","..........KKSSSSSSSSKK..........","..........KKSSMMMMSSKK..........","..........KKKSSSSSSKKK..........",
"...........SSSSSSSS.............","..........TTTTTTTTTTTT..........",".........TTTTTTTTTTTTTT.........",".........TTTTTTTTTTTTTT.........",
".........TTTTTTTTTTTTTT.........",".........TTTTTTTTTTTTTT.........",".........TTTTTTTTTTTTTT.........",".........TTTTTTTTTTTTTT.........",
".........TTTTTTTTTTTTTT.........",".........TTTTTTTTTTTTTT.........",".........TtTTTTTTTTTTtT.........",".........tttttttttttttt.........",
"................................","................................","................................","................................",
"................................","................................","................................","................................",
"................................","................................"];
const CLOUD = [
"................................","................................","................................","................................",
"................................","................................","................................","..............cccc..............",
"...........ccCCCCCCcc...........",".........ccCCCCCCCCCCcc.........","......cccCCCCCCCCCCCCCCccc......",".....cCCCCCCCCCCCCCCCCCCCCc.....",
"....cCCCCCCCCCCCCCCCCCCCCCCc....","...cCCCCCCCCCCCCCCCCCCCCCCCCc...","..cCCCCCCCCCCCCCCCCCCCCCCCCCCc..","..cCCCCCCCCCCCCCCCCCCCCCCCCCCc..",
"...cCCCCCCCCCCCCCCCCCCCCCCCCc...","....cCCCCCCCCCCCCCCCCCCCCCCc....",".....ccccCCCCCCCCCCCCCCcccc.....","........ccccccccccccccc........",
"................................","................................","................................","................................",
"................................","................................","................................","................................",
"................................","................................","................................","................................",
"................................","................................"];
 
// —— 幼年 / 老年 由成年图自动生成，加新版本只要给一张 body ——
const KID_BODY = [
"..........TTTTTTTTTTTT..........",
".........TTTTTTTTTTTTTT.........",
".........STTTTTTTTTTTTS.........",
".........STTTTTTTTTTTTS.........",
"..........TTTTTTTTTTTT..........",
"...........DDDDDDDDDD...........",
"...........DDDDDDDDDD...........",
"...........HHHH..HHHH...........",
"................................"
];
function makeKid(body){ return body.slice(0,25).concat(KID_BODY); }
function makeOld(body){
  const g = body.map(function(r){ return r.replace(/[KkN]/g, 'G'); });
  if(g.length > 23){
    g[22] = ".........GGGGGGGGGGGGGG.........";
    g[23] = "...........GGGGGGGGGG...........";
  }
  return g;
}
 
// v2：无框大眼、可爱清秀（学生）
const BODY2 = [
"...........KKK..KKKKK...........",".........KKKKKKKKKKKKKK.........",".......KKKKKKKKKKKKKKKKKK.......","......KKKKKKKKKKKKKKKKKKKK......",
".....KKKKKKKKKkkkKKKKKKKKKK.....",".....KKKKKKKKKKKKKkkkKKKKKK.....",".....KKKKKKKKKKKKKKKKKKKKKK.....",".....KKKKKKKKKKKKKKKKKKKKKK.....",
".....KKKKKKSKSSSKSSSKSSSKKK.....",".....KKKKSSSSSSSSSSSSSSSSKK.....",".....KKKSSNNNSSSSSSSNNNSSKK.....",".....KKKSSSSSSSSSSSSSSSSKKK.....",
".....KKKSSWWWWSSSSWWWWSSKKK.....",".....KKKSSWPPWSSSSWPPWSSKKK.....",".....KKKSSWPEWSSSSWPEWSSKKK.....",".....KKKSSWWWWSSSSWWWWSSKKK.....",
".....KKKSSSSSSSSSSSSSSSSKKK.....",".....KKKSSSSSSSSSSSSSSSSKKK.....",".....KKKSSSSSSSssSSSSSSSKKK.....",".....KKKSBBSSSSSSSSSSBBSKKK.....",
".....KKKSSSSSSMwwMSSSSSSKKK.....",".....KKKSSSSSSSMMSSSSSSSKKK.....","........SSSSSSSSSSSSSSSS........","..........SSSSSSSSSSSS..........",
".............ssssss.............","..........TTWWWMMWWWTT..........","........TTWWWWMMMMWWWWTT........","........TTTWWMMMMMMWWTTT........",
"........TTTTWWWMMWWWTTTT........","........WWTWTTTMMTTTTTWW........","........SSTTTTttttTTTTSS........",".........TttTTTTTTTTttT.........",
"...........DDDDD.DDDDD..........","...........HHHH...HHHH.........."];
 
// v3：宽圆脸、上梳短发、透明镜框、咧嘴大笑（西装）
const BODY3 = [
"........KKKKKKKKKKKKKKKK........","......KKKKKKKKKKKKKKKKKKKK......",".....KKKKKKKKKKKKKKKKKKKKKK.....","....KKKKKKKKKKKKKKKKKKKKKKKK....",
"....KKKKKKKKKKKKkkkKKKKKKKKK....","....KKKKKKKKKKKKKKKKKKKKKKKK....","....KKKSSSSSSSSSSSSSSSSSSKKK....","....KKSSSSSSSSSSSSSSSSSSSSKK....",
"....KSSSSSSSSSSSSSSSSSSSSSSK....","....KSSSSSSSSSSSSSSSSSSSSSSK....","....KSSSNNNNSSSSSSSSNNNNSSSK....","....KSLLLLLLLLLLLLLLLLLLLLSK....",
"....KSLWWWWWWWLSSLWWWWWWWLSK....","....KSLWWPPPWWLSSLWWPPPWWLSK....","....KSLWWEEEWWLSSLWWEEEWWLSK....","....KSLWWWWWWWLSSLWWWWWWWLSK....",
"....KSLLLLLLLLLLLLLLLLLLLLSK....","....KSSSSSSSSSSSSSSSSSSSSSSK....","....KSSSSSSSSSssSSSSSSSSSSSK....","....KSSSSSSMwwwwwwwwMSSSSSSK....",
"....KSSSSMMMMMMMMMMMMMMSSSSK....","....KBBSSSSSSSSSSSSSSSSSSBBK....","......SSSSSSSSSSSSSSSSSSSS......",".........SSSSSSSSSSSSSS.........",
".............ssssss.............","..........TTTWWMMWWTTT..........","........TTtttWWMMWWtttTT........","........TTTtttWMMWtttTTT........",
"........TTTTtttMMtttTTTT........","........WWTTTttMMttTTTWW........","........SSTTTTTttTTTTTSS........",".........TtttTTTTTTtttT.........",
"...........DDDDD.DDDDD..........","...........HHHH...HHHH.........."];
 
// 版本口子：每个版本给一张 body 即可，幼年/老年/睡袋自动生成。加版本就在这里加一行。
const VERSIONS = {
  "v1": { body: BODY },
  "v2": { body: BODY2 },
  "v3": { body: BODY3 },
};
 
const HATS = {
  "光头":[], "蝴蝶结":[[20,3,3,4,"#FF7A59"],[24,3,3,4,"#FF7A59"],[23,4,1,2,"#E45B3C"]],
  "小花":[[5,5,2,2,"#FF9FB2"],[3,7,2,2,"#FF9FB2"],[7,7,2,2,"#FF9FB2"],[5,9,2,2,"#FF9FB2"],[5,7,2,2,"#F5D97A"]],
  "贝雷帽":[[7,1,18,4,"#7E5DBA"],[5,4,22,3,"#6D4FA3"],[22,0,3,1,"#7E5DBA"]],
  "耳机":[[8,0,16,2,"#6D4FA3"],[5,2,2,9,"#6D4FA3"],[25,2,2,9,"#6D4FA3"],[3,11,3,6,"#F5A623"],[26,11,3,6,"#F5A623"]],
  "毛线帽":[[7,1,18,4,"#7FB3E8"],[5,5,22,3,"#A9CFF5"],[14,0,4,1,"#FFFFFF"]],
  "小丑鼻":[[15,15,3,3,"#FF3B3B"]],
  "墨镜":[[7,12,7,4,"#2A2E36"],[18,12,7,4,"#2A2E36"],[14,13,4,1,"#2A2E36"]],
  "小皇冠":[[8,3,16,3,"#F5A623"],[8,0,3,3,"#F5A623"],[14,0,3,3,"#F5A623"],[21,0,3,3,"#F5A623"],[14,1,3,1,"#FF7A59"]],
  "天使环":[[9,-3,14,1,"#FFF0B0"],[9,-2,14,1,"#F5D97A"]],
  "猫耳":[[7,-1,3,4,"#3B322D"],[8,-3,2,2,"#3B322D"],[8,0,1,2,"#F2A69B"],[22,-1,3,4,"#3B322D"],[23,-3,2,2,"#3B322D"],[23,0,1,2,"#F2A69B"]],
  "鸭舌帽":[[6,2,20,4,"#5F7BA6"],[2,5,11,2,"#4A6288"]],
  "头巾":[[5,7,22,3,"#FF7A59"],[5,8,22,1,"#E45B3C"]],
  "呆毛":[[15,-4,2,5,"#3B322D"],[16,-5,2,2,"#3B322D"]],
  "花环":[[6,4,3,2,"#FF9FB2"],[10,3,3,2,"#F5D97A"],[14,3,3,2,"#FF9FB2"],[18,3,3,2,"#A188C9"],[22,4,3,2,"#F5D97A"]],
  "恶魔角":[[7,-1,2,3,"#C0322F"],[6,-3,2,2,"#C0322F"],[23,-1,2,3,"#C0322F"],[24,-3,2,2,"#C0322F"]],
  "眼罩":[[18,12,7,4,"#2A2E36"],[7,11,18,1,"#2A2E36"]]
};
const OUTFITS=[{main:"#5A3038",shade:"#43232A"},{main:"#8C949E",shade:"#6F7780"},{main:"#5F7BA6",shade:"#4A6288"},{main:"#72B4A0",shade:"#579485"},{main:"#E08E75",shade:"#C0725B"},{main:"#A188C9",shade:"#836CA8"},{main:"#DFBB4E",shade:"#BF9C34"},{main:"#3A3D45",shade:"#2A2C32"}];
const HAIRS=[{k:"#241E1C",hi:"#3B322D"},{k:"#4A3227",hi:"#66473A"},{k:"#8C6A4A",hi:"#A98663"},{k:"#3B4453",hi:"#556072"},{k:"#B0708A",hi:"#C98CA4"}];
const SKINS=[{s:"#F6D5B8",d:"#E2B492"},{s:"#F0C49F",d:"#D9A379"},{s:"#C6976A",d:"#A87B54"}];
 
const PS=5,GW=32,GH=34,OX=(200-GW*PS)/2,OY=200-GH*PS-8;
function pal(o){const out=OUTFITS[o.outfit||0],hair=HAIRS[o.hair||0],skin=SKINS[o.skin||0];
  return {K:hair.k,k:hair.hi,N:hair.k,G:"#C7C7C7",L:"#8A9BB0",S:skin.s,s:skin.d,F:"#333A44",W:"#E4EDF6",E:"#4A362A",P:"#241C16",w:"#FFF6EE",M:"#8E4B44",B:"#F2A69B",T:out.main,t:out.shade,D:"#3C4450",H:"#2B3038"};}
function grid2rect(grid,p){let out="";for(let y=0;y<grid.length;y++){const row=grid[y];let x=0;while(x<row.length){const ch=row[x];if(ch==="."||!p[ch]){x++;continue;}let w=1;while(x+w<row.length&&row[x+w]===ch)w++;out+=`<rect x="${OX+x*PS}" y="${OY+y*PS}" width="${w*PS}" height="${PS}" fill="${p[ch]}"/>`;x+=w;}}return out;}
function list2rect(list){return list.map(([x,y,w,h,c])=>`<rect x="${OX+x*PS}" y="${OY+y*PS}" width="${w*PS}" height="${h*PS}" fill="${c}"/>`).join("");}
function petSVG(s){
  if(s.stage==="cloud"){
    return `<svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges">
      <g class="sprite">${grid2rect(CLOUD,{C:"#D5DEE8",c:"#EEF3F8"})}</g></svg>`;
  }
  const p=pal(s); const V=VERSIONS[s.version]||VERSIONS["v1"];
  let grid, scale=1;
  if(s.stage==="cocoon"){ grid=V.cocoon||COCOON; }
  else if(s.stage==="kid"){ grid=V.kid||makeKid(V.body); scale=0.6; }
  else if(s.stage==="old"){ grid=V.old||makeOld(V.body); }
  else { grid=V.body; }
  const wrap=`translate(100,192) scale(${scale}) translate(-100,-192)`;
  const hat = s.stage==="cocoon" ? "" : list2rect(HATS[s.hat]||[]);
  return `<svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges">
    <g transform="${wrap}"><g class="sprite">${grid2rect(grid,p)}${hat}</g></g></svg>`;
}
 
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Blob} from 'node:buffer';
import {DecompressionStream} from 'node:stream/web';

class LocalResponse {
  constructor(stream){this.stream=stream;}
  async arrayBuffer(){let reader=this.stream.getReader(),parts=[],size=0;while(true){let {done,value}=await reader.read();if(done)break;parts.push(value);size+=value.byteLength;}
    let out=new Uint8Array(size),offset=0;for(let part of parts){out.set(part,offset);offset+=part.byteLength;}return out.buffer;}
}

export default async function testCodec(scheduler,page,fixture) {
  const source=await readFile(page,'utf8'),code=await readFile(scheduler,'utf8'),data=JSON.parse(await readFile(fixture,'utf8'));
  let x=source.slice(source.indexOf('function makeFrameX('),source.indexOf('\nfunction frameConfirmationProducts('));
  let parse=source.slice(source.indexOf('function parseFrameBinary('),source.indexOf('\nfunction fetchFrame('));
  let decode=code.slice(code.indexOf('async function decodeScopeRtc('),code.indexOf('\nfunction scopeLiveEligible('));
  let peakMarker=source.slice(source.indexOf('function peakSampleMarker('),source.indexOf('\nfunction drawPeakSampleGuide('));
  let widthGuide=source.slice(source.indexOf('function drawBaseWidthGate('),source.indexOf('\nfunction drawDetectionGate('));
  let ctx=vm.createContext({ArrayBuffer,Uint8Array,Uint16Array,DataView,TextDecoder,Blob,Response:LocalResponse,DecompressionStream,
    frameTextDecoder:new TextDecoder()});
  vm.runInContext(x+'\n'+parse+'\n'+decode+'\n'+peakMarker+'\n'+widthGuide,ctx);
  function buffer(base64){let b=Buffer.from(base64,'base64'),out=new ArrayBuffer(b.length);new Uint8Array(out).set(b);return out;}
  ctx.original=buffer(data.raw);let original=vm.runInContext('parseFrameBinary(original)',ctx),restored={},widthMarkers=0;
  for(let packet of data.packets){ctx.packet=buffer(packet);let f=await vm.runInContext('decodeScopeRtc(packet,false)',ctx);
    for(let name of f.arrays){assert.deepEqual(Array.from(f[name]),Array.from(original[name]),name);restored[name]=true;}
    let norm='prod_norm'+f._scopeChannel;assert.equal(JSON.stringify(f[norm]),JSON.stringify(original[norm]));
    let peak='prod_peak'+f._scopeChannel;
    if(Object.hasOwn(original,peak))assert.equal(JSON.stringify(f[peak]),JSON.stringify(original[peak]),'same-frame width metadata must survive transport');
    let width=f[peak]?.base_width;
    if(width){
      let lines=[],labels=[],current=[],plot={save(){},restore(){},setLineDash(){},beginPath(){current=[];},
        moveTo(x,y){current.push([x,y]);},lineTo(x,y){current.push([x,y]);},stroke(){lines.push({color:this.strokeStyle,points:current});},
        measureText(s){return {width:s.length*6};},fillRect(){},fillText(text,x,y){labels.push({text,x,y,color:this.fillStyle});}};
      ctx.plot=plot;ctx.widthInfo=width;ctx.samples=f.x;ctx.count=f.count;
      vm.runInContext('drawBaseWidthGate(plot,widthInfo,samples,count,1000,500,1)',ctx);
      let color=width.enabled?(width.ok?'#b697ff':'#ffa657'):'#a8aeba';
      assert.equal(labels[0].text,'BASE '+(width.valid?width.width:'?')+(width.enabled?' / '+width.limit:' OFF'));
      assert.equal(labels[0].color,color);
      for(let bound of [width.left,width.right])if(bound!==null&&bound!==undefined){
        let expected=(bound-f.x[0])/(f.x.at(-1)-f.x[0])*1000;
        assert(lines.some(line=>line.color===color&&Math.abs(line.points[0][0]-expected)<1e-9&&line.points[0][1]===0));
      }
      widthMarkers++;
    }
  }
  assert.deepEqual(Object.keys(restored).sort(),Array.from(original.arrays).sort());
  return {passed:true,exactArrays:Object.keys(restored).length,widthMarkers,packetBytes:data.packets.map(x=>Buffer.from(x,'base64').length)};
}

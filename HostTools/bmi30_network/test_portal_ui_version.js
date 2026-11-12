const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const callbacks={},hash='a'.repeat(64);let reloads=0;
const context=vm.createContext({Set,document:{activeElement:null,querySelector:()=>({content:hash}),addEventListener:(n,fn)=>{callbacks[n]=fn;}},
  window:{location:{reload(){reloads++;}}},relayBusy:false,nonAddressableLedBusy:false,_groupBusy:{}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'portal_ui_version.js'),'utf8'),context);
assert.equal(context.portalUIVersionCheck(hash),false);assert.equal(reloads,0);
assert.equal(context.portalUIVersionCheck('bad'),false);
context.document.activeElement={matches:()=>true};assert.equal(context.portalUIVersionCheck('b'.repeat(64)),false);
context.document.activeElement=null;
const form={},el={form,matches:()=>true,closest:()=>null,hasAttribute:()=>false};
callbacks.input({target:el,isTrusted:true});assert.equal(context.portalUIVersionCheck('b'.repeat(64)),false,'unsaved form edits block a reload after leaving the field');
callbacks.submit({target:form});context.relayBusy=true;assert.equal(context.portalUIVersionCheck('b'.repeat(64)),false);
context.relayBusy=false;context._groupBusy.peer=true;assert.equal(context.portalUIVersionCheck('b'.repeat(64)),false);
context._groupBusy={};assert.equal(context.portalUIVersionCheck('b'.repeat(64)),true);
assert.equal(reloads,1);context.portalUIVersionCheck('b'.repeat(64));assert.equal(reloads,1,'reload happens once');
console.log('Portal UI: installed code change, unchanged/invalid hashes, focused field, unsaved edits, pending commands and single reload passed');

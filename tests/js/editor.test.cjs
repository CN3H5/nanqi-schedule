const {test}=require('node:test');
const assert=require('node:assert/strict');
const model=require('../../assets/web/editor-model.js');
test('unchanged overnight and full day edits preserve duration',()=>{
 for(const [time,duration,end] of [['23:00',120,'01:00'],['09:00',1440,'09:00'],['09:00',60,'10:00']])
  assert.equal(model.duration(time,end,{time,duration},end),duration);
});
test('changed overnight and full day times validated, never clamped',()=>{
 assert.equal(model.duration('23:00','01:00',null,''),120);
 assert.equal(model.duration('09:00','09:00',null,''),1440);
 assert.throws(()=>model.duration('09:00','09:05',null,''));
});
test('deleted NLP tokens restore defaults but preserve manual values',()=>{
 const defaults={date:'2030-01-01',time:'09:00',endTime:'',reminder:10,priority:false,tags:'',kind:'schedule',category:'work',repeat:'1',repeatUntil:''};
 const parsed={date:null,time:null,endTime:null,reminder:null,priority:null,tags:[],kind:null,category:null,repeat:null};
 assert.deepEqual(model.autoFields(parsed,defaults,{}),defaults);
 assert.ok(!('repeat' in model.autoFields(parsed,defaults,{repeat:true})));
});

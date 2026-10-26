(function(root,factory){
 if(typeof module==='object'&&module.exports)module.exports=factory();
 else root.NanqiEditor=factory();
})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const minutes=s=>Number(s.slice(0,2))*60+Number(s.slice(3));
 function duration(start,end,existing,originalEnd){
  if(existing&&start===existing.time&&end===originalEnd)return existing.duration;
  if(!end)return 60;
  let value=minutes(end)-minutes(start);
  if(value<=0)value+=1440;
  if(!Number.isInteger(value)||value<15||value>1440)throw new Error('时长须为 15–1440 分钟');
  return value;
 }
 function autoFields(parsed,defaults,manual){
  const values={date:parsed.date||defaults.date,time:parsed.time||defaults.time,
   endTime:parsed.endTime||defaults.endTime,reminder:parsed.reminder??defaults.reminder,
   category:parsed.category||defaults.category,priority:parsed.priority?parsed.priority==='high':defaults.priority,
   tags:parsed.tags.length?parsed.tags.join(' '):defaults.tags,kind:parsed.kind||defaults.kind,
   repeat:parsed.repeat?parsed.repeat.frequency:defaults.repeat,
   repeatUntil:parsed.repeat&&parsed.repeat.until||defaults.repeatUntil};
  return Object.fromEntries(Object.entries(values).filter(([k])=>!manual[k]));
 }
 return {duration,autoFields};
});

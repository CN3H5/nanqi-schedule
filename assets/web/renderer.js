const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],api=window.nanqi;
const paths={widget:'M4 4h16v16H4zM4 9h16M9 9v11M12 13h5M12 16h4',plus:'M12 5v14M5 12h14',calendar:'M4 5h16v16H4zM7 3v4M17 3v4M4 10h16M8 14h2M14 14h2M8 17h2',sun:'M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',bell:'M18 8a6 6 0 0 0-12 0c0 7-2 7-2 9h16c0-2-2-2-2-9M10 21h4',check:'m5 12 4 4L19 6',settings:'m9 3-1 3-3 1 1 3-2 2 2 2-1 3 3 1 1 3h6l1-3 3-1-1-3 2-2-2-2 1-3-3-1-1-3zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',search:'M16 10a6 6 0 1 1-12 0 6 6 0 0 1 12 0m-2 4 6 6',left:'m14 6-6 6 6 6',right:'m9 6 6 6-6 6',shield:'m12 3 8 3v6c0 4-8 9-8 9s-8-5-8-9V6zM8 12l3 3 5-6',clock:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7v5l3 2',leaf:'M20 4C7 2 2 8 6 16s16 3 14-12ZM5 21l10-12',arrow:'M7 17 17 7M7 7h10v10'};
const icon=name=>'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="'+(paths[name]||paths.calendar)+'"/></svg>';
$$('[data-icon]').forEach(e=>e.innerHTML=icon(e.dataset.icon));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const key=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
const parse=s=>new Date(s+'T12:00:00');const add=(d,n)=>{d=new Date(d);d.setDate(d.getDate()+n);return d;};
const monday=d=>add(d,-(d.getDay()+6)%7);const today=()=>key(new Date());
// 分类是数据（state.categories），不再是写死的常量；state 未加载时用默认三分类兜底。
const FALLBACK_CATS=[{id:'work',name:'工作 / 项目',color:'#6b9b87',bg:'#e9f3ed',ink:'#355e4b'},{id:'study',name:'学习 / 成长',color:'#a195b9',bg:'#f1edf7',ink:'#6b5784'},{id:'life',name:'生活 / 日常',color:'#c6a574',bg:'#fbf1df',ink:'#84602f'}];
const catList=()=>state&&state.categories&&state.categories.length?state.categories:FALLBACK_CATS;
const catOf=id=>catList().find(c=>c.id===id)||catList()[0];
const catShort=c=>String(c.name||'').split('/')[0].trim();
let state,view='calendar',mode='four',anchor=monday(new Date()),selected=today(),mini=new Date(),filter=null,query='',toastTimer,editing=null,busy=false;
let dragId=null,dragCandidate=false,deferredRender=false;
function flushDeferredRender(){if(deferredRender&&!dragId&&!dragCandidate){deferredRender=false;render();}}
let showCompleted=(localStorage.getItem('nanqi.showCompleted') ?? localStorage.getItem('weeklight.showCompleted'))!=='false',listStatus='all';
const matchesStatus=t=>listStatus==='all'||(listStatus==='done'?t.done:!t.done);
// 课表是「时间表」，不是待办：不算待办数、不算待办用时，也不参与过期判断。
// 考试(exam)要算进去——它是一件要去完成的事，只是提醒方式与上课相同。
const isCourseBlock=t=>t.category==='course';
const isCourse=t=>t.category==='course'||t.category==='exam';
const isOverdue=t=>!isCourseBlock(t)&&!t.done&&t.date<today();
const sorted=ts=>ts.slice().sort((a,b)=>(a.date+a.time).localeCompare(b.date+b.time));
// ── 渲染期缓存（性能优化，输出与原实现完全一致）──────────────────────────────
// 原来 filtered() 会被调用 30 多次/帧：renderFour/renderWeek 逐日取任务（28 或 7 次）、
// renderPanel 1 次、render() 与 renderList 各 1 次，而每次都要对**整份任务表**过滤 + 排序。
// 任务一多（几百项）这一步就成了切换视图时的主要开销。这里在同一帧内只算一次，
// 并把结果按日期建索引；render() 开始时重置，结束时清空，跨帧不会拿到旧数据。
let frameCache=null;
function beginFrame(){frameCache={list:null,byDay:null,pendingDays:null};}
function endFrame(){frameCache=null;}
function filtered(ts=state.tasks){
 // 只缓存「整份任务表」这一种调用；传入自定义数组时仍按原样即时计算，避免语义变化。
 if(frameCache&&ts===state.tasks){if(!frameCache.list)frameCache.list=sorted(ts.filter(t=>(!filter||t.category===filter)&&(!query||(t.title+' '+t.notes+' '+(t.tags||[]).join(' ')).toLowerCase().includes(query.toLowerCase()))));return frameCache.list;}
 return sorted(ts.filter(t=>(!filter||t.category===filter)&&(!query||(t.title+' '+t.notes+' '+(t.tags||[]).join(' ')).toLowerCase().includes(query.toLowerCase()))));
}
function dayIndex(){
 if(!frameCache)return null;
 if(!frameCache.byDay){const map=new Map();for(const t of filtered()){const bucket=map.get(t.date);if(bucket)bucket.push(t);else map.set(t.date,[t]);}frameCache.byDay=map;}
 return frameCache.byDay;
}
function pendingDaySet(){
 if(!frameCache)return null;
 if(!frameCache.pendingDays){const set=new Set();for(const t of state.tasks)if(!t.done)set.add(t.date);frameCache.pendingDays=set;}
 return frameCache.pendingDays;
}
function inRange(t,start=anchor,days=mode==='week'?7:28){return t.date>=key(start)&&t.date<key(add(start,days));}
function dayTasks(d){const index=dayIndex();if(index)return index.get(d)||[];return filtered().filter(t=>t.date===d);}
function calendarDayTasks(d){return dayTasks(d).filter(t=>showCompleted||!t.done);}
function style(t){const c=catOf(t.category);return '--event-bg:'+c.bg+';--event-ink:'+c.ink+';--event-border:'+c.color;}
function fmt(d){return d.getMonth()+1+'月'+d.getDate()+'日';}
function toast(s){$('#toast').textContent=s;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),3500);}
async function run(fn,message){try{const result=await fn();if(message&&result!==null)toast(message);return result;}catch(e){toast(e.message.replace(/^Error invoking remote method '[^']+': Error: /,''));return null;}}
function openDialog(el){if(!el.open)el.showModal();}
// ── 按帧合并重渲染（流畅度优化）────────────────────────────────────────────
// 一次操作往往会触发两次重渲染：Python 侧 change() 会广播一次状态（onState），
// 而调用本身的返回值（await api.saveTask(...) = state）后面还会再 render() 一次。
// 连续操作（连续勾选、连续拖拽、批量保存）更会把好几帧的重绘堆在一起。
// 这里把 render() 变成「排到下一帧」，同一帧里多次调用只真正执行一次：
//   * 省掉重复的整表过滤/排序与 DOM 重建；
//   * 重绘与屏幕刷新对齐，不会在一帧中间做一半又被打断，观感更顺。
//
// 【必须配一个定时器兜底】窗口被隐藏（`--hidden` 启动、或最小化到托盘）时，
// Chromium 不会触发 requestAnimationFrame。只靠 rAF 会让「排好的那次渲染」永远
// 不执行，页面停在旧内容上——界面自检正是这么发现它的（它故意不显示窗口）。
// 所以同时挂一个 120ms 的定时器，谁先到用谁。
let renderPending=0,renderFallback=0;
function render(){
 if(renderPending||renderFallback)return;
 const fire=()=>{
  if(!renderPending&&!renderFallback)return;
  if(renderPending)cancelAnimationFrame(renderPending);
  clearTimeout(renderFallback);
  renderPending=0;renderFallback=0;
  renderNow();
 };
 renderPending=requestAnimationFrame(fire);
 renderFallback=setTimeout(fire,120);
}
function renderNow(){
 if(!state)return;
 // Keep native drag sources attached while cross-window state updates arrive.
 if(dragId||dragCandidate){deferredRender=true;return;}
 deferredRender=false;
 beginFrame();try{   // 本帧只算一次「过滤+排序」，见 filtered()/dayIndex() 的说明
 $$('.nav-item[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
 $('#today-count').textContent=state.tasks.filter(t=>!isCourseBlock(t)&&t.date<=today()&&!t.done).length;
 $('#today-count').title='今天及之前尚未完成的计划（不含课表）';
 // 原来这里是 state.history.filter(h => ... state.tasks.some(...))：待处理提醒最多 300 条、
 // 任务最多 10000 项，最坏情况是 300 万次比较，每次广播都会做一遍。改成先建 id 集合，
 // 变成 O(任务数 + 提醒数)，结果完全一致。
 const openIds=new Set();for(const t of state.tasks)if(!t.done)openIds.add(t.id);
 const pending=state.history.filter(h=>!h.dismissed&&openIds.has(h.taskId));
 $('#reminder-count').textContent=pending.length;$('#bell-dot').style.display=pending.length?'block':'none';
 $('#categories').innerHTML=catList().map(c=>'<button class="category-button '+(filter===c.id?'active':'')+'" data-category="'+c.id+'"><i class="category-dot" style="--category:'+c.color+'"></i>'+esc(c.name)+'<span>'+state.tasks.filter(t=>t.category===c.id&&!t.done).length+'</span></button>').join('');
 const summaryTasks=state.tasks.filter(t=>inRange(t,monday(new Date()),28)),done=summaryTasks.filter(t=>t.done).length;
 // 待办用时只算「真要去做的」：课表是固定占用，不算进用时（用户明确要求）；
// 考试、复习、二课、其它待办都算。
  const hours=summaryTasks.filter(t=>!t.done&&!isCourseBlock(t)).reduce((a,t)=>a+t.duration,0)/60;
 $('#summary').innerHTML=[['calendar','未来四周',summaryTasks.length,'项计划',''],['check','已完成',done,'项计划',summaryTasks.length?Math.round(done/summaryTasks.length*100)+'% 完成率':''],['clock','待办预计用时',Number(hours.toFixed(1)),'小时','']].map(a=>'<div class="stat-card"><div class="stat-icon">'+icon(a[0])+'</div><div class="stat-copy"><span>'+a[1]+'</span><div class="stat-number">'+a[2]+'<small>'+a[3]+'</small></div></div><div class="stat-caption">'+a[4]+'</div></div>').join('');
 $('#page-title').textContent=({calendar:'计划日历',today:'今天',inbox:'提醒中心',done:'已完成'}[view]);
 $('#page-subtitle').textContent=({calendar:'安排未来几周，也掌握今天要做的事。',today:'今天的安排与此前未完成的计划，一起查看。',inbox:'处理到时提醒，或选择稍后再次提醒。',done:'查看已经完成的计划，回顾每天的进展。'}[view]);
 $('#filter-label').textContent=(filter?catOf(filter).name:'全部计划')+(query?' · 搜索结果':'');
 $('.segmented').hidden=view!=='calendar';
 $('#previous').disabled=view!=='calendar';$('#next').disabled=view!=='calendar';
 $$('.segmented button').forEach(b=>b.classList.toggle('active',b.dataset.mode===mode));
 $('#date-range').textContent=view==='calendar'? (anchor.getFullYear()+'年 '+fmt(anchor)+' — '+fmt(add(anchor,mode==='week'?6:27))):({today:fmt(new Date())+' · 今天',inbox:'待处理提醒',done:'已完成计划'}[view]);
 const isList=view==='today'||(view==='calendar'&&mode==='list');
 $('#list-filter').hidden=!isList;
 $('#date-jump-label').hidden=view!=='calendar';
 $('#jump-date').value=selected;
 $('#calendar-completed-label').hidden=view!=='calendar'||mode==='list';
 $('#show-completed').checked=showCompleted;
 $('.view-options').hidden=view==='inbox';
 $$('#list-filter button').forEach(b=>{b.classList.toggle('active',b.dataset.status===listStatus);b.setAttribute('aria-pressed',String(b.dataset.status===listStatus));});
 let visible=filtered().filter(t=>view==='today'?(t.date===today()||isOverdue(t)):view==='done'?t.done:inRange(t));
 if(isList)visible=visible.filter(matchesStatus);
 else if(view==='calendar'&&!showCompleted)visible=visible.filter(t=>!t.done);
 $('#view-count').textContent=visible.filter(t=>!t.done).length+' 待办 · '+visible.filter(t=>t.done).length+' 已办';
 $('#calendar-tip').textContent=view==='calendar'?(mode==='list'?'日期旁 ＋ 添加计划 · 点击计划编辑':'点击日期查看 · 拖动计划改期 · N 新建'):'勾选完成 · 点击计划编辑';
 const scroll=$('.week-scroll')?.scrollTop;
 if(view==='calendar'&&mode==='four')renderFour();
 else if(view==='calendar'&&mode==='week')renderWeek();
 else renderList(pending);
 if($('.week-scroll'))$('.week-scroll').scrollTop=scroll??8*72;
 renderMini();renderPanel();
 wrapForAnimation();   // 统一套一层入场容器，让每次切换都有一次轻量过渡
 }finally{endFrame();}     // 清掉本帧缓存，下一帧重新计算（避免跨帧拿到旧数据）
}
// 给当前视图的内容套一层新容器：元素是新建的，CSS 入场动画会自动播放一次，
// 不需要「移除类 → 强制回流 → 再加类」那套写法（那样会让每次渲染多一次同步布局）。
// 这里只是把已有子节点搬进新父节点，不重新解析 HTML，代价可以忽略。
function wrapForAnimation(){
 const cal=$('#calendar');
 if(!cal||!cal.firstChild||cal.firstElementChild?.classList.contains('view-enter'))return;
 const box=document.createElement('div');
 box.className='view-enter';
 while(cal.firstChild)box.appendChild(cal.firstChild);
 cal.appendChild(box);
}
function event(t,extra='',className=''){
 return '<button class="event '+className+(t.done?' done':'')+'" draggable="true" data-task="'+esc(t.id)+'" style="'+style(t)+extra+'" title="'+esc(t.title+' · '+t.time)+'"><span class="event-time">'+esc(t.time)+(t.priority==='high'?'<span class="event-important">◆</span>':'')+'</span><div class="event-title">'+(t.done?'✓ ':'')+esc(t.title)+'</div></button>';
}
function renderFour(){
 $('#calendar').innerHTML='<div class="weekday-row">'+['周一','周二','周三','周四','周五','周六','周日'].map(d=>'<span>'+d+'</span>').join('')+'</div><div class="month-grid">'+Array.from({length:28},(_,i)=>{
 const d=add(anchor,i),k=key(d),ts=calendarDayTasks(k);
 return '<div class="day-cell '+(k===selected?'selected':'')+'" data-date="'+k+'"><div class="day-number"><b class="'+(k===today()?'today':'')+'">'+d.getDate()+'</b><span>'+(d.getDate()===1?d.getMonth()+1+'月':'')+'</span><button class="day-add" data-new-date="'+k+'" aria-label="'+k+' 新建计划">+</button></div>'+ts.slice(0,3).map(t=>event(t)).join('')+(ts.length>3?'<button class="more-button" data-select="'+k+'">还有 '+(ts.length-3)+' 项</button>':'')+(!ts.length&&k===today()?'<div class="empty-hint">暂无计划<br>点击 ＋ 添加</div>':'')+'</div>';
 }).join('')+'</div>';
}
function renderWeek(){
 const days=Array.from({length:7},(_,i)=>add(anchor,i));
 $('#calendar').innerHTML='<div class="week-header"><span></span>'+days.map((d,i)=>'<button data-select="'+key(d)+'" class="'+(key(d)===today()?'today':'')+'">周'+['一','二','三','四','五','六','日'][i]+'<b>'+d.getDate()+'</b></button>').join('')+'</div><div class="week-scroll"><div class="week-body"><div class="time-labels">'+Array.from({length:24},(_,h)=>'<div>'+String(h).padStart(2,'0')+':00</div>').join('')+'</div>'+days.map(d=>{
 const ts=calendarDayTasks(key(d));const placements=[];let cluster=[],end=-1;
 const flush=()=>{const width=Math.max(1,...cluster.map(e=>e.lane+1));cluster.forEach(e=>placements.push({...e,width}));cluster=[];};
 ts.forEach(t=>{const start=+t.time.slice(0,2)*60 + +t.time.slice(3),finish=start+Math.max(t.duration,25);if(start>=end){flush();end=-1;}let lane=0;while(cluster.some(e=>e.lane===lane&&e.end>start))lane++;cluster.push({t,start,end:finish,lane});end=Math.max(end,finish);});flush();
 return '<div class="week-column">'+Array.from({length:24},(_,h)=>'<div class="hour-cell" data-date="'+key(d)+'" data-time="'+String(h).padStart(2,'0')+':00"></div>').join('')+placements.map(e=>event(e.t,';top:'+e.start*1.2+'px;height:'+Math.min(Math.max(28,e.t.duration*1.2-3),1728-e.start*1.2)+'px;left:calc('+e.lane/e.width*100+'% + 2px);width:calc('+100/e.width+'% - 4px)','week-event')).join('')+(key(d)===today()?'<div class="now-line" style="top:'+((new Date().getHours()*60+new Date().getMinutes())*1.2)+'px"></div>':'')+'</div>';
 }).join('')+'</div></div>';
}
function row(t){const c=catOf(t.category);const tags=(t.tags||[]).map(g=>'<span class="tag tag-plain">#'+esc(g)+'</span>').join('');return '<div class="task-row '+(t.done?'done':'')+(isOverdue(t)?' overdue':'')+'"><button class="task-check '+(t.done?'checked':'')+'" data-toggle="'+esc(t.id)+'" aria-label="'+(t.done?'取消完成 ':'完成 ')+esc(t.title)+'">'+(t.done?'✓':'')+'</button><div class="task-info" role="button" tabindex="0" data-task="'+esc(t.id)+'"><strong>'+esc(t.title)+(t.priority==='high'?' <span class="event-important">◆</span>':'')+(t.kind==='review'?' <span class="kind-badge">复习</span>':t.kind==='plan'?' <span class="kind-badge">计划</span>':'')+'</strong><p>'+(isOverdue(t)?'<span class="overdue-date">'+fmt(parse(t.date))+' · </span>':'')+t.time+' · '+t.duration+' 分钟'+(t.reminder>=0?' · 提醒':'')+'</p><span class="tag" style="background:'+c.bg+';color:'+c.ink+'">'+esc(catShort(c))+'</span>'+tags+'</div></div>';}
function empty(title,desc){return '<div class="empty-state"><div class="empty-icon">'+icon('leaf')+'</div><h3>'+title+'</h3><p>'+desc+'</p><button class="secondary" data-action="new">'+icon('plus')+' 安排一件事</button></div>';}
function renderList(pending){
 if(view==='inbox'){
  const hs=pending.filter(h=>filtered().some(t=>t.id===h.taskId)).reverse();
  $('#calendar').innerHTML='<div class="list-view">'+(hs.length?hs.map(h=>{const t=state.tasks.find(t=>t.id===h.taskId);return '<div class="reminder-row"><h3>'+esc(t.title)+'</h3><p>'+t.date+' '+t.time+' · '+esc(catOf(t.category).name)+'</p><div class="actions"><button class="small-button" data-reminder="'+t.id+'" data-action="done">✓ 完成</button><button class="small-button" data-reminder="'+t.id+'" data-action="snooze">10 分钟后提醒</button><button class="text-button" data-reminder="'+t.id+'" data-action="dismiss">知道了</button></div></div>';}).join(''):empty('暂时没有待处理提醒','计划到达提醒时间后，会出现在这里。'))+'</div>';return;
 }
 let ts=filtered().filter(t=>view==='today'?(t.date===today()||isOverdue(t)):view==='done'?t.done:inRange(t));
 if(view!=='done')ts=ts.filter(matchesStatus);
 const groups=[...new Set(ts.map(t=>t.date))];
 let content='';
 if(view==='today'){
  const overdue=ts.filter(isOverdue),current=ts.filter(t=>t.date===today());
  if(overdue.length)content+='<section class="today-group overdue-group"><h3 class="list-group-title">逾期未完成 <span>'+overdue.length+'</span></h3><p class="group-description">可以完成这些计划，或点击修改日期。</p>'+overdue.map(row).join('')+'</section>';
  if(current.length)content+='<section class="today-group"><h3 class="list-group-title">今天 <span>'+current.length+'</span></h3>'+current.map(row).join('')+'</section>';
 }else content=groups.map(d=>'<section class="list-day-group" data-list-date="'+d+'"><h3 class="list-group-title">'+fmt(parse(d))+' · 周'+['日','一','二','三','四','五','六'][parse(d).getDay()]+'<span>'+ts.filter(t=>t.date===d).length+'</span>'+(view==='calendar'?'<button type="button" class="list-day-add" data-new-date="'+d+'" aria-label="'+d+' 添加计划">'+icon('plus')+'添加计划</button>':'')+'</h3>'+ts.filter(t=>t.date===d).map(row).join('')+'</section>').join('');
 const emptyTitle=view==='done'||listStatus==='done'?'还没有已完成的计划':listStatus==='todo'?'当前没有待办计划':'这段时间还没有计划';
 $('#calendar').innerHTML='<div class="list-view">'+(content||empty(query?'没有找到匹配的计划':emptyTitle,query?'试试其他关键词，或清除搜索。':listStatus!=='all'?'可以切换清单状态，或添加新的计划。':'点击「新建计划」，安排下一步。'))+'</div>';
}
function renderMini(){
 $('#mini-label').textContent=mini.getFullYear()+'年 '+(mini.getMonth()+1)+'月';
 const first=monday(new Date(mini.getFullYear(),mini.getMonth(),1));
 // 原来的写法对 42 个格子各扫一遍全部任务（state.tasks.some），任务多时是 42×N 次比较。
 // 改成一次遍历建「有未完成计划的日期」集合，结果一致但只在任务数上线性。
 const pendingDays=pendingDaySet()||(()=>{const set=new Set();for(const t of state.tasks)if(!t.done)set.add(t.date);return set;})();
 $('#mini-days').innerHTML=Array.from({length:42},(_,i)=>{const d=add(first,i),k=key(d);return '<button data-mini="'+k+'" class="'+(d.getMonth()!==mini.getMonth()?'outside ':'')+(k===selected?'selected ':'')+(pendingDays.has(k)?'dot':'')+'" aria-label="'+k+'">'+d.getDate()+'</button>';}).join('');
}
function renderPanel(){
 const d=parse(selected),ts=dayTasks(selected),week=state.tasks.filter(t=>inRange(t,monday(d),7)),done=week.filter(t=>t.done).length,pct=week.length?Math.round(done/week.length*100):0;
 $('#day-panel').innerHTML='<div class="day-panel-inner"><div class="panel-heading">'+dayNav()+'<div><div><div class="panel-eyebrow">'+(selected===today()?'今天':'所选日期')+'</div><h2 class="panel-title">'+fmt(d)+'</h2><div class="panel-subtitle">周'+['日','一','二','三','四','五','六'][d.getDay()]+' · '+ts.length+' 项计划</div></div>'+icon('sun')+'</div><div class="panel-list">'+(ts.length?ts.map(row).join(''):'<div class="empty-panel"><div class="empty-icon">'+icon('leaf')+'</div><p>这一天还没有计划</p><small>点击下方按钮添加</small></div>')+'</div><button class="panel-add" data-new-date="'+selected+'">＋ 为这天添加计划</button></div><div class="week-focus"><h3>'+icon('leaf')+' 这一周的进展</h3><p><strong>'+pct+'<span style="font-size:11px">%</span></strong><span class="week-total">'+done+' / '+week.length+' 已完成</span></p><div class="progress-track"><i style="width:'+pct+'%"></i></div></div>';
}
function setView(v){view=v;if(v==='today')selected=today();render();}
// 日期前后翻页：原来只能在迷你月历上点日期，或者用「上一段/下一段」跳整周。
// 用户在「今天」视图里想看看昨天、明天，没有就近的按钮。这里补上。
function shiftDay(delta){const next=add(parse(selected),delta);selected=key(next);mini=parse(selected);render();}
function dayNav(){return '<div class="day-nav"><button class="icon-button" data-shift="-1" aria-label="前一天" title="前一天">‹</button><button class="text-button" data-shift="0" title="回到今天">今天</button><button class="icon-button" data-shift="1" aria-label="后一天" title="后一天">›</button></div>';}
// ── 编辑器：自由文本 + 自然语言识别 + ⋯ 弹出面板 ────────────────────────────
// 输入的第一行被 quickparse.js 拆成 日期/时间/重复/标签/分类 等字段；
// 弹出面板的控件在「用户没手动改过」时跟随识别结果，手动改过就以用户为准。
const timeToMin=t=>+t.slice(0,2)*60+ +t.slice(3);
const minToTime=m=>String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0');
const KIND_NAMES={schedule:'日程',plan:'计划',review:'复习计划'};
let manual={},attachmentsDraft=[],repeatUntilAuto=true,popoverSyncing=false,editorDefaults={},originalEnd="";
const EDITOR_DEFAULT_HINT='直接在第一行写下时间与安排，程序会自动识别；备注支持 Markdown，下方实时预览。';
function effectiveParse(){const f=$('#task-form');return window.NanqiQuick.parse(f.elements.quick.value,{categories:catList()});}
function renderChips(parsed){
 const d=parsed.detected,chips=[];
 const label={'daily':'每天','weekdays':'工作日','weekly':'每周'}[parsed.repeat&&parsed.repeat.frequency];
 if(d.date)chips.push('日期 '+parsed.date);
 if(d.time)chips.push('时间 '+parsed.time+(d.endTime?' – '+parsed.endTime:''));
 if(d.repeat&&parsed.repeat)chips.push('重复 '+label+(parsed.repeat.until?' 至 '+parsed.repeat.until:''));
 if(d.reminder)chips.push('提醒 '+(parsed.reminder===-1?'不提醒':parsed.reminder===0?'开始时':'提前 '+parsed.reminder+' 分钟'));
 if(d.priority)chips.push('重要');
 if(d.kind)chips.push(KIND_NAMES[parsed.kind]);
 if(d.category)chips.push('分类 '+catShort(catOf(parsed.category)));
 if(d.tags)chips.push(parsed.tags.map(t=>'#'+t).join(' '));
 $('#parsed-chips').innerHTML=chips.length?'<span class="chips-label">已识别</span>'+chips.map(c=>'<span class="chip">'+esc(c)+'</span>').join(''):'';
}
function renderPreview(){
 const f=$('#task-form'),text=f.elements.quick.value,box=$('#quick-preview');
 if(!text.trim()){box.hidden=true;box.innerHTML='';return;}
 box.hidden=false;box.innerHTML=window.NanqiMarkdown.render(text);
}
function renderAttachments(){
 $('#attachment-list').innerHTML=attachmentsDraft.length?attachmentsDraft.map((a,i)=>'<span class="attachment-chip"><button type="button" class="attachment-name" data-open-attachment="'+i+'" title="打开附件">'+esc(a.name)+'</button><button type="button" class="attachment-remove" data-remove-attachment="'+i+'" aria-label="移除附件">×</button></span>').join(''):'<span class="attachment-empty">暂无附件</span>';
}
function syncPopover(parsed){
 const f=$('#task-form');popoverSyncing=true;
 try{
  const fields=window.NanqiEditor.autoFields(parsed,editorDefaults,manual);
  for(const [name,value] of Object.entries(fields)){
   if(name==='repeat'&&editing)continue;
   if(name==='priority')f.elements.priority.checked=value;
   else f.elements[name].value=value;
  }
  if(!manual.repeatUntil)repeatUntilAuto=!(parsed.repeat&&parsed.repeat.until);
 }finally{popoverSyncing=false;}
 syncEditorVisibility();syncEditorHint();
}
function syncEditorVisibility(){
 const f=$('#task-form'),kind=f.elements.kind.value,repeat=f.elements.repeat.value,ranged=!editing&&repeat!=='1';
 $('#exam-date-field').hidden=kind!=='review'||!!editing;
 $('#repeat-field').hidden=!!editing;
 $('#repeat-until-field').hidden=!ranged;f.elements.repeatUntil.disabled=!ranged;
 if(ranged&&repeatUntilAuto&&!manual.repeatUntil){
  const start=f.elements.date.value||today();
  f.elements.repeatUntil.value=key(add(parse(start),repeat==='weekly'?83:29));
 }
 f.elements.repeatUntil.min=f.elements.date.value;
}
function syncEditorHint(){
 const f=$('#task-form');
 if(editing){$('#form-hint').textContent='修改仅影响当前这项计划。';return;}
 const repeat=f.elements.repeat.value,start=f.elements.date.value,until=f.elements.repeatUntil.value;
 if(repeat==='1'){$('#form-hint').textContent=EDITOR_DEFAULT_HINT;return;}
 const days=start&&until?(Date.parse(until+'T00:00:00Z')-Date.parse(start+'T00:00:00Z'))/86400000+1:0;
 let count=days;
 if(repeat==='weekdays'&&days>0){count=Math.floor(days/7)*5;const firstDay=parse(start).getDay();for(let i=0;i<days%7;i++)if((firstDay+i)%7!==0&&(firstDay+i)%7!==6)count++;}
 if(repeat==='weekly'&&days>0)count=Math.floor((days-1)/7)+1;
 const label={daily:'每日',weekdays:'工作日',weekly:'每周'}[repeat];
 $('#form-hint').textContent=days<=0?'请选择不早于开始日期的结束日期。':count===0?'所选日期范围内没有匹配的日子，请调整日期。':count>10000?'最多生成 10000 项，请缩短日期范围。':'将创建 '+count+' 项'+label+'计划，每项可分别修改、完成或删除。';
}
$('#quick-input').addEventListener('input',()=>{const parsed=effectiveParse();renderChips(parsed);renderPreview();syncPopover(parsed);});
$('#more-options').onclick=()=>{const p=$('#extras-popover');p.hidden=!p.hidden;};
// 用户在面板里手动改过的字段，之后不再被自动识别覆盖。
function markManual(e){if(popoverSyncing)return;const n=e.target.name;if(!n)return;manual[n]=true;if(n==='repeatUntil')repeatUntilAuto=false;syncEditorVisibility();syncEditorHint();}
$('#extras-popover').addEventListener('input',markManual);
$('#extras-popover').addEventListener('change',markManual);
$('#extras-popover').addEventListener('click',async e=>{
 const open=e.target.closest('[data-open-attachment]');
 if(open){const a=attachmentsDraft[+open.dataset.openAttachment];if(a)await run(()=>api.openAttachment(a.path));return;}
 const remove=e.target.closest('[data-remove-attachment]');
 if(remove){attachmentsDraft.splice(+remove.dataset.removeAttachment,1);renderAttachments();return;}
 if(e.target.closest('#add-attachments')){
  const picked=await run(()=>api.pickAttachments());
  if(picked&&picked.length){for(const a of picked)if(!attachmentsDraft.some(x=>x.path===a.path))attachmentsDraft.push(a);renderAttachments();}
 }
});
function fillCategoryOptions(selectedId){
 const f=$('#task-form'),select=f.elements.category;
 select.innerHTML=catList().map(c=>'<option value="'+esc(c.id)+'">'+esc(c.name)+'</option>').join('');
 select.value=catList().some(c=>c.id===selectedId)?selectedId:catList()[0].id;
}
function openEditor(t=null,date=selected,time='09:00'){
 editing=t;manual={};repeatUntilAuto=true;popoverSyncing=false;
 attachmentsDraft=(t&&t.attachments||[]).map(a=>({name:a.name,path:a.path}));
 const f=$('#task-form');f.reset();
 f.elements.id.value=t?.id||'';
 f.elements.quick.value=t?t.title+(t.notes?'\n'+t.notes:''):'';
 fillCategoryOptions(t?.category||filter||catList()[0].id);
 f.elements.kind.value=t?.kind&&KIND_NAMES[t.kind]?t.kind:'schedule';
 f.elements.date.value=t?.date||date;f.elements.time.value=t?.time||time;
 f.elements.endTime.value=t?minToTime((timeToMin(t.time)+t.duration)%1440):'';
 f.elements.reminder.value=String(t?t.reminder:10);
 f.elements.priority.checked=t?.priority==='high';
 f.elements.tags.value=(t?.tags||[]).join(' ');
 f.elements.repeat.value='1';f.elements.repeatUntil.value='';f.elements.examDate.value='';
 originalEnd=f.elements.endTime.value;editorDefaults={};
 for(const name of ['date','time','endTime','reminder','category','tags','kind','repeat','repeatUntil'])editorDefaults[name]=f.elements[name].value;
 editorDefaults.priority=f.elements.priority.checked;
 if(t)for(const n of ['date','time','endTime','reminder','category','priority','tags','kind'])manual[n]=true;
 $('#editor-title').textContent=t?'编辑计划':'新建计划';$('#delete-task').hidden=!t;$('#form-error').textContent='';
 $('#extras-popover').hidden=true;renderAttachments();
 const parsed=effectiveParse();renderChips(parsed);renderPreview();syncEditorVisibility();syncEditorHint();
 openDialog($('#editor'));setTimeout(()=>f.elements.quick.focus(),50);
}
document.addEventListener('click',async e=>{
 const b=e.target.closest('button,[data-task],.day-cell,.hour-cell');if(!b)return;
 if(b.dataset.reminder){await run(()=>b.dataset.action==='done'?window.NanqiTaskActions.act(b.dataset.reminder,'complete'):api.reminderAction(b.dataset.reminder,b.dataset.action),'已更新提醒');return;}
 if(b.dataset.task){const t=state.tasks.find(t=>t.id===b.dataset.task);if(t)openEditor(t);return;}
 if(b.dataset.toggle){const t=state.tasks.find(t=>t.id===b.dataset.toggle);if(t)await run(()=>window.NanqiTaskActions.act(t.id,t.done?'reopen':'complete'));return;}
 if(b.dataset.newDate){selected=b.dataset.newDate;openEditor(null,selected);return;}
 if(b.dataset.view){setView(b.dataset.view);return;}
 if(b.dataset.mode){mode=b.dataset.mode;render();return;}
 if(b.dataset.status){listStatus=b.dataset.status;render();return;}
 if(b.dataset.category){filter=filter===b.dataset.category?null:b.dataset.category;render();return;}
 if(b.dataset.mini){selected=b.dataset.mini;anchor=monday(parse(selected));mini=parse(selected);view='calendar';render();return;}
 if(b.dataset.shift!==undefined){const n=Number(b.dataset.shift);if(n===0){selected=today();mini=new Date();}else shiftDay(n);return;}
 if(b.dataset.select){selected=b.dataset.select;mini=parse(selected);render();return;}
 if(b.dataset.action==='new'){openEditor();return;}if(b.dataset.action==='inbox'){setView('inbox');return;}
 if(b.classList.contains('close-dialog')){b.closest('dialog').close();return;}
 if(b.dataset.date){selected=b.dataset.date;if(b.dataset.time)openEditor(null,selected,b.dataset.time);else{mini=parse(selected);render();}return;}
});
document.addEventListener('dblclick',e=>{const cell=e.target.closest('.day-cell');if(cell&&!e.target.closest('button'))openEditor(null,cell.dataset.date);});
document.addEventListener('keydown',e=>{
 if((e.key==='Enter'||e.key===' ')&&e.target.matches('[role=button][data-task]')){e.preventDefault();e.target.click();}
 if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#search-row').hidden=false;$('#search').focus();}
 if(!document.querySelector('dialog[open]')&&!['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)&&!e.ctrlKey&&!e.metaKey&&e.key.toLowerCase()==='n'){e.preventDefault();openEditor();}
});
function moveRange(days){anchor=add(anchor,days);selected=key(anchor);mini=parse(selected);render();}
$('#previous').onclick=()=>moveRange(mode==='week'?-7:-28);
$('#next').onclick=()=>moveRange(mode==='week'?7:28);
$('#go-today').onclick=()=>{anchor=monday(new Date());selected=today();mini=new Date();if(view!=='calendar')view='today';render();};
$('#mini-prev').onclick=()=>{mini=new Date(mini.getFullYear(),mini.getMonth()-1,1);renderMini();};
$('#mini-next').onclick=()=>{mini=new Date(mini.getFullYear(),mini.getMonth()+1,1);renderMini();};
$('#reset-filter').onclick=()=>{filter=null;render();};
// 「一键添加」：先选这次要加什么（课表 / 二课 / 考试），再打开对应网页。
// 网页里右下角的「一键添加」是通用的，会自己识别当前页面类型后导入。
const IMPORT_HINTS={schedule:'已打开教务系统课表页，登录后点右下角「一键添加」',ercourse:'已打开二课页面，点右下角「一键添加」',exam:'已打开教务系统，切到「考试安排」后点右下角「一键添加」'};
$('#import-anything').onclick=()=>{$('#import-error').textContent='';openDialog($('#import-dialog'));};
for(const button of $$('#import-dialog [data-import]'))button.onclick=async()=>{
 $('#import-dialog').close();
 const kind=button.dataset.import;
 const r=await run(()=>api.importFrom(kind));
 if(r!==null)toast(IMPORT_HINTS[kind]||'已打开页面，点右下角「一键添加」');
};
// ── 分类管理对话框 ─────────────────────────────────────────────────────────
async function catRun(fn){try{return await fn();}catch(e){$('#category-error').textContent=e.message.replace(/^Error invoking remote method '[^']+': Error: /,'');return null;}}
function renderCategoryList(){
 $('#category-list').innerHTML=catList().map(c=>'<div class="category-row" data-cat-row="'+esc(c.id)+'"><input type="color" value="'+esc(c.color)+'" aria-label="分类颜色" data-cat-color="'+esc(c.id)+'"><input class="category-name" maxlength="20" value="'+esc(c.name)+'" aria-label="分类名称" data-cat-name="'+esc(c.id)+'"><span class="category-count">'+state.tasks.filter(t=>t.category===c.id).length+' 项</span><button type="button" class="text-button category-delete" data-cat-delete="'+esc(c.id)+'" '+(catList().length<=1?'disabled':'')+'>删除</button></div>').join('');
}
$('#manage-categories').onclick=()=>{renderCategoryList();$('#category-error').textContent='';$('#new-category-name').value='';openDialog($('#categories-dialog'));};
$('#categories-dialog').addEventListener('change',async e=>{
 const id=e.target.dataset.catColor?e.target.dataset.catColor:e.target.dataset.catName;
 if(!id)return;
 const row=$('#categories-dialog [data-cat-row="'+CSS.escape(id)+'"]');
 const name=row.querySelector('[data-cat-name]').value.trim();
 const color=row.querySelector('[data-cat-color]').value;
 const r=await catRun(()=>api.saveCategory({id,name,color}));
 if(r){state=r;render();renderCategoryList();$('#category-error').textContent='';}
 else{const fresh=await catRun(()=>api.get());if(fresh){state=fresh;renderCategoryList();}}
});
$('#categories-dialog').addEventListener('click',async e=>{
 const del=e.target.closest('[data-cat-delete]');
 if(!del)return;
 const r=await catRun(()=>api.deleteCategory(del.dataset.catDelete));
 if(r){state=r;if(filter===del.dataset.catDelete)filter=null;render();renderCategoryList();$('#category-error').textContent='';}
});
$('#add-category').onclick=async()=>{
 const name=$('#new-category-name').value.trim();
 if(!name){$('#category-error').textContent='请填写分类名称';return;}
 const r=await catRun(()=>api.saveCategory({name}));
 if(r){state=r;render();renderCategoryList();$('#new-category-name').value='';$('#category-error').textContent='';}
};
$('#categories-dialog').addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.id==='new-category-name'){e.preventDefault();$('#add-category').click();}});
$('#open-widget').onclick=()=>run(()=>api.openWidget());
$('#open-mail').onclick=()=>run(()=>api.openMail());
$('#show-completed').onchange=e=>{showCompleted=e.target.checked;localStorage.setItem('nanqi.showCompleted',String(showCompleted));render();};
$('#jump-date').onchange=e=>{if(!/^\d{4}-\d{2}-\d{2}$/.test(e.target.value))return;selected=e.target.value;mini=parse(selected);anchor=monday(mini);render();};
$('#search-button').onclick=()=>{$('#search-row').hidden=!$('#search-row').hidden;if(!$('#search-row').hidden)$('#search').focus();};
$('#search').oninput=e=>{query=e.target.value.trim();render();};
$('#clear-search').onclick=()=>{query='';$('#search').value='';render();};
$('#task-form').onsubmit=async e=>{
 e.preventDefault();if(busy)return;busy=true;const submit=e.target.querySelector('[type=submit]');submit.disabled=true;
 try{
  const f=e.target,parsed=effectiveParse(),title=(parsed.title||'').trim();
  if(!title)throw new Error('先在最上面的输入框里写点什么吧');
  const tagsText=f.elements.tags.value.trim();
  const tags=(tagsText?tagsText.split(/[\s,，、]+/):parsed.tags).filter(Boolean).slice(0,10);
  const duration=window.NanqiEditor.duration(f.elements.time.value,f.elements.endTime.value,editing,originalEnd);
  const data={id:f.elements.id.value,title,notes:parsed.notes,date:f.elements.date.value,time:f.elements.time.value,duration,
   category:f.elements.category.value,reminder:Number(f.elements.reminder.value),priority:f.elements.priority.checked?'high':'normal',
   kind:f.elements.kind.value,tags,attachments:attachmentsDraft,done:editing?.done||false};
  const repeatValue=f.elements.repeat.value;
  const repeat=editing?1:repeatValue==='1'?1:{frequency:repeatValue,until:f.elements.repeatUntil.value};
  if(!editing&&repeatValue!=='1'&&(!f.elements.repeatUntil.value||f.elements.repeatUntil.value<data.date))throw new Error('请选择不早于开始日期的重复结束日期');
  state=await api.saveTask(data,repeat,!editing&&data.kind==='review'?f.elements.examDate.value:null);
  $('#editor').close();selected=data.date;
  if(!editing&&repeatValue==='weekdays'){while([0,6].includes(parse(selected).getDay()))selected=key(add(parse(selected),1));}
  mini=parse(selected);if(!inRange({date:selected}))anchor=monday(mini);if(view==='inbox'||view==='done')view='calendar';render();
  toast(editing?'计划已更新':'计划已保存');
 }catch(error){$('#form-error').textContent=error.message;}
 finally{busy=false;submit.disabled=false;}
};
$('#delete-task').onclick=async()=>{if(!editing)return;const id=editing.id;const result=await run(()=>window.NanqiTaskActions.act(id,'delete'),'计划已删除');if(result){state=result;$('#editor').close();render();}};
document.addEventListener('pointerdown',e=>{dragCandidate=!!e.target.closest('.event');});
for(const name of ['pointerup','pointercancel'])document.addEventListener(name,()=>{dragCandidate=false;setTimeout(flushDeferredRender,0);});
function dropCell(e){
 const cell=e.target.closest('.day-cell,.hour-cell');if(cell)return cell;
 const column=e.target.closest('.week-column');if(!column)return null;
 const hour=Math.max(0,Math.min(23,Math.floor((e.clientY-column.getBoundingClientRect().top)/72)));
 return column.querySelectorAll('.hour-cell')[hour];
}
function clearDrag(){dragId=null;dragCandidate=false;$$('.dragover').forEach(e=>e.classList.remove('dragover'));}
document.addEventListener('dragstart',e=>{const t=e.target.closest('.event');if(t){dragCandidate=false;dragId=t.dataset.task;e.dataTransfer.setData('text/plain',dragId);e.dataTransfer.effectAllowed='move';}});
document.addEventListener('dragover',e=>{const cell=dropCell(e);if(cell&&dragId){e.preventDefault();e.dataTransfer.dropEffect='move';$$('.dragover').filter(el=>el!==cell).forEach(el=>el.classList.remove('dragover'));cell.classList.add('dragover');}});
document.addEventListener('dragleave',e=>{const cell=e.target.closest('.day-cell,.hour-cell');if(cell&&!cell.contains(e.relatedTarget))cell.classList.remove('dragover');});
document.addEventListener('dragend',()=>{clearDrag();flushDeferredRender();});
document.addEventListener('drop',async e=>{const cell=dropCell(e),id=dragId||e.dataTransfer.getData('text/plain');if(cell&&state.tasks.some(t=>t.id===id)){e.preventDefault();clearDrag();await run(()=>api.moveTask(id,cell.dataset.date,cell.dataset.time),'计划已改期');flushDeferredRender();}});
let settingsBusy=false,settingsError='';
const settingsErrorText=e=>e.message.replace(/^Error invoking remote method '[^']+': Error: /,'');
$('#settings-button').onclick=async()=>{
 settingsBusy=true;settingsError='';syncSettings();openDialog($('#settings-dialog'));
 try{state=await api.get();}catch(e){settingsError=settingsErrorText(e);}finally{settingsBusy=false;syncSettings();}
};
function syncSettings(){
 if(!state){$('#settings-error').textContent=settingsError||'数据尚未加载，请稍后重试';return;}
 $('#setting-sound').checked=state.settings.sound;$('#setting-popup').checked=state.settings.popup;$('#setting-startup').checked=state.autostart;
 $('#setting-sound').disabled=settingsBusy;$('#setting-popup').disabled=settingsBusy;$('#setting-startup').disabled=settingsBusy||!state.packaged;
 $('#startup-hint').textContent=settingsBusy?'正在确认设置…':state.packaged?'登录 Windows 后在后台运行':'请使用 Windows 安装版设置开机启动';
 const w=state.settings.weather||{enabled:false,latitude:31.84,longitude:117.26,city:'合肥'};
 $('#setting-weather').checked=!!w.enabled;$('#setting-weather').disabled=settingsBusy;
 $('#weather-lat').value=w.latitude;$('#weather-lon').value=w.longitude;
 $('#weather-lat').disabled=settingsBusy;$('#weather-lon').disabled=settingsBusy;
 $('#weather-hint').textContent=w.enabled?(w.city||'自定义坐标')+'（'+w.latitude+'N, '+w.longitude+'E）：当天有课时查一次天气':'默认合肥（31.84N, 117.26E），开启后才会联网查询';
 $('#shift-days').disabled=settingsBusy;$('#shift-courses').disabled=settingsBusy;
 $('#settings-error').textContent=settingsError||state.autostartError||'';$('#data-path').textContent=state.dataDir;
}
async function saveSetting(patch){
 settingsBusy=true;settingsError='';$('#settings-error').textContent='';
 try{state=await api.settings(patch);render();}
 catch(e){settingsError=settingsErrorText(e);try{state=await api.get();}catch{}}
 finally{settingsBusy=false;syncSettings();}
}
for(const [id,k] of [['sound','sound'],['popup','popup'],['startup','autostart']])$('#setting-'+id).onchange=async e=>{
 if(settingsBusy)return;
 const enabled=e.target.checked;
 await saveSetting({[k]:enabled});
};
// 天气：开关与坐标分开保存，但都走同一个白名单处理器（见 nanqi/ipc/settings.py）。
$('#setting-weather').onchange=e=>{if(!settingsBusy)saveSetting({weather:{enabled:e.target.checked}});};
for(const id of ['weather-lat','weather-lon'])$('#'+id).onchange=()=>{
 if(settingsBusy)return;
 const lat=Number($('#weather-lat').value),lon=Number($('#weather-lon').value);
 if(!isFinite(lat)||!isFinite(lon)){$('#settings-error').textContent='坐标必须是数字';return;}
 saveSetting({weather:{latitude:lat,longitude:lon}});
};
// 课程整体平移：把课程日程挪 N 天，用于对齐真实教学周（二课不参与）。
$('#shift-courses').onclick=async()=>{
 if(settingsBusy)return;
 const days=Number($('#shift-days').value||0);
 if(!isFinite(days)||days===0){$('#settings-error').textContent='请填写要平移的天数（可以是负数）';return;}
 settingsBusy=true;$('#settings-error').textContent='';
 try{
  const next=await api.shiftCourses(Math.trunc(days));
  state=next;render();
  toast(next.shiftedCourses?('已整体平移 '+next.shiftedCourses+' 项课程日程'):'没有可平移的课程日程');
  $('#shift-days').value=0;
 }catch(e){$('#settings-error').textContent=settingsErrorText(e);try{state=await api.get();}catch{}}
 finally{settingsBusy=false;syncSettings();}
};
$('#test-reminder').onclick=()=>run(()=>api.testReminder(),'测试提醒已发送');
$('#export-data').onclick=()=>run(()=>api.exportData(),'备份已导出');
$('#import-data').onclick=()=>run(()=>api.importData(),'备份已合并');
$('#open-data').onclick=()=>run(()=>api.openData());
$('#quit').onclick=()=>run(()=>api.quit());
$('#load-samples').onclick=async()=>{
 if(busy)return;busy=true;$('#load-samples').disabled=true;
 try{
 const base=monday(new Date());const examples=[['梳理本周优先事项',0,'09:00','work',60],['留给深度工作的时间',1,'10:00','work',120],['读完一章，记下想法',2,'19:30','study',45],['散步，给自己充充电',3,'18:00','life',30],['整理项目进展',4,'14:00','work',60],['一周回顾与下周计划',6,'17:00','life',30],['把一个新想法做成小实验',8,'09:30','study',90],['推进关键里程碑',10,'14:00','work',120],['和朋友一起吃晚餐',12,'18:30','life',90],['整理阅读笔记',16,'20:00','study',60],['项目阶段复盘',18,'15:00','work',60],['为下个月留出方向',25,'10:00','life',60]];
 for(const [title,i,time,category,duration]of examples){const name='[示例] '+title;const cid=catList().some(c=>c.id===category)?category:catList()[0].id;if(!state.tasks.some(t=>t.title===name))await api.saveTask({title:name,date:key(add(base,i)),time,category:cid,duration,reminder:-1,notes:'这是示例计划，可以修改或删除。',priority:'normal'},1);}
 toast('已添加示例计划，提醒均为关闭');$('#settings-dialog').close();view='calendar';anchor=base;selected=today();mini=new Date();render();
 }catch(e){toast(e.message);}finally{busy=false;$('#load-samples').disabled=false;}
};
api.onState(s=>{state=s;render();});
api.onNew(date=>openEditor(null,typeof date==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(date)?date:selected));api.onError(toast);
api.onEditTask?.(async id=>{try{state=await api.get();const task=state.tasks.find(t=>t.id===id);if(!task){toast('这项计划已被删除');return;}selected=task.date;anchor=monday(parse(selected));mini=parse(selected);view='calendar';render();openEditor(task);}catch(e){toast('无法打开计划：'+e.message);}});
api.get().then(s=>{state=s;render();if(s.startupError)toast(s.startupError);else if(s.migrationNote)toast(s.migrationNote);}).catch(e=>toast('无法读取数据：'+e.message));
setInterval(()=>{if(!document.querySelector('dialog[open]'))render();},60000);

window.addEventListener("unhandledrejection",e=>{toast(e.reason?.message||"操作失败，请重试");});
window.addEventListener("error",e=>{console.error(e.message);toast("界面更新失败，请重试");});

'use strict';
const $=id=>document.getElementById(id), TARGET='https://alambda-forge.onrender.com/';
let sid=null, epoch=null, controller=null, authenticated=false, connected=false;
let busy=false, syncing=false, poll=null, frameURL=null, frameSequence=0, lastFocus=null;
let threadId=null, chatState='idle', chatPoll=null, chatSeen=0, chatRun=null, agentReady=false;
let selectedTab='Chat', pinned=true, lastActivity=Date.now();
let voiceController=null, voiceCapability=null, voiceUi={active:false,phase:'idle'}, captionRole='', captionText='';
let chatOwner=null, chatPending=null, chatGeneration=0, chatInfo=null;
const chatWaiters=new Set();
function wake(){lastActivity=Date.now();$('panel').classList.remove('dim')}
function remember(value){try{value?sessionStorage.setItem('reign-session',value):sessionStorage.removeItem('reign-session')}catch{}}
function recalled(){try{return sessionStorage.getItem('reign-session')}catch{return null}}
function say(message,mode){$('status').textContent=message;if(mode&&!voiceUi.active){$('state').textContent=mode;voiceState=mode==='Working'?'working':'idle';activity=mode==='Working'?.55:.1;document.querySelector('.orb').dataset.mode=mode==='Your control'?'human':voiceState}wake()}
function entry(title,message,error=false){const row=document.createElement('div');row.className='entry'+(error?' error':'');const label=document.createElement('b');label.textContent=title;const time=document.createElement('time');time.textContent=new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});row.append(time,label,document.createTextNode(message));$('feed').append(row);while($('feed').children.length>60)$('feed').firstChild.remove();$('feed').scrollTop=$('feed').scrollHeight;wake()}
function csrf(){return document.cookie.split('; ').find(x=>x.startsWith('ax_csrf='))?.slice(8)||''}
async function request(path,method='GET',body,options={}){
  const headers={},requestAbort=new AbortController();let timedOut=false;
  if(method!=='GET'){headers['Content-Type']='application/json';headers['X-CSRF-Token']=csrf()}
  const cancel=()=>requestAbort.abort();
  if(options.signal?.aborted)cancel();else options.signal?.addEventListener('abort',cancel,{once:true});
  const timeout=setTimeout(()=>{timedOut=true;requestAbort.abort()},path.startsWith('/api/chat')?15000:path.startsWith('/api/voice')?40000:90000);
  try{
    const response=await fetch(path,{method,headers,credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal:requestAbort.signal,keepalive:!!options.keepalive});
    if(!response.ok){
      const error=new Error(response.status===401?'Your sign-in expired. Sign in again.':response.status===409?'Control or session availability changed. Refresh and try again.':response.status===404?'This session has expired. Open a new session.':response.status===403?'This action couldn’t be verified. Reload and sign in again.':'The workspace couldn’t complete that request. Try again.');
      if(path.startsWith('/api/voice')){try{const value=await response.json();if(typeof value.detail==='string')error.message=value.detail.slice(0,350)}catch{}}
      error.status=response.status;
      // Every authenticated surface recovers to the same visible sign-in form.
      if(error.status===401&&authenticated){authenticated=false;lost(error)}
      throw error;
    }
    return response;
  }catch(error){
    if(timedOut)throw Object.assign(new Error('The workspace request timed out. Its outcome could not be confirmed.'),{status:504});
    throw error;
  }finally{clearTimeout(timeout);options.signal?.removeEventListener('abort',cancel)}
}
async function api(path,method='GET',body,options){return (await request(path,method,body,options)).json()}
function refreshControls(){const human=connected&&controller==='human'&&!busy;const agent=connected&&controller==='agent'&&!busy;$('open').disabled=!!sid||busy||!authenticated;for(const id of ['take','takeExpanded'])$(id).disabled=!agent;for(const id of ['return','returnExpanded','tabKey','enterKey','scroll','scrollUp','send','text'])$(id).disabled=!human;$('close').disabled=!sid||busy;$('expandRemote').disabled=!sid||!connected;$('reconnect').hidden=!sid||connected;$('reconnect').disabled=busy;$('logout').disabled=busy;$('sendCommand').disabled=busy||chatOccupied();$('resumeAgent').hidden=chatState!=='paused';$('resumeAgent').disabled=controller!=='agent';$('cancelAgent').hidden=!threadId&&!chatPending;$('session').textContent=sid||'None';$('controller').textContent=controller||'—';$('epoch').textContent=epoch??'—';$('controlLabel').textContent=!sid?'No active session':!connected?'Connection interrupted':human||controller==='human'?'You have control':'REIGN has control';$('remoteStatus').textContent=$('controlLabel').textContent;$('connection').textContent=!authenticated?'SIGN IN':connected?'CONNECTED':'WORKSPACE';$('liveLabel').textContent=connected?'● LIVE BROWSER':sid?'CONNECTION INTERRUPTED':'COMPUTER · NOT CONNECTED';refreshVoiceControls()}
function clearFrame(){for(const id of ['screen','fullScreen'])$(id).removeAttribute('src');$('screen').hidden=true;$('emptyScreen').hidden=false;if(frameURL)URL.revokeObjectURL(frameURL);frameURL=null;frameSequence=0;$('frameStatus').textContent='Waiting for a session'}
function pausePolling(){if(poll)clearTimeout(poll);poll=null}
function stop(){pausePolling();sid=null;epoch=null;controller=null;connected=false;remember(null);clearFrame();closeExpanded();refreshControls()}
function lost(error){connected=false;controller=null;epoch=null;pausePolling();clearFrame();$('frameStatus').textContent='No live frame';refreshControls();say(error.message,'Disconnected');entry('CONNECTION',error.message,true);if(error.status===404){stop();say('Session expired. Open Git High to start again.','Ready')}if(error.status===401){voiceController?.stop({reason:'Your sign-in expired. Sign in before calling again.',failed:true});stop();authenticated=false;$('console').hidden=true;$('loginPanel').hidden=false;$('loginMessage').textContent=error.message;refreshControls()}}
async function sync(){if(!sid||syncing)return;syncing=true;const activeId=sid;try{const state=await api('/api/sessions/'+encodeURIComponent(activeId));if(sid!==activeId)return;controller=state.mode;epoch=state.epoch;const response=await request('/api/sessions/'+encodeURIComponent(activeId)+'/frame');if(response.headers.get('X-Session-ID')!==activeId||!response.headers.get('Content-Type')?.startsWith('image/jpeg'))throw Error('The live screen could not be verified. Reconnect to continue.');const sequence=Number(response.headers.get('X-Frame-Sequence'));if(!Number.isFinite(sequence)||sequence<=frameSequence)throw Error('The screen stopped updating. Reconnect to continue.');const blob=await response.blob();if(sid!==activeId)return;const nextURL=URL.createObjectURL(blob),previous=frameURL;frameURL=nextURL;frameSequence=sequence;for(const id of ['screen','fullScreen'])$(id).src=nextURL;if(previous)URL.revokeObjectURL(previous);$('screen').hidden=false;$('emptyScreen').hidden=true;$('frameStatus').textContent='Live · '+new Date().toLocaleTimeString();connected=true;refreshControls();say(controller==='human'?'Click, type, or scroll in the expanded screen.':'Watch REIGN’s browser, or take control whenever you need.',controller==='human'?'Your control':'Connected')}catch(error){if(sid===activeId)lost(error)}finally{syncing=false}}
function schedule(){pausePolling();if(sid&&connected)poll=setTimeout(async()=>{await sync();schedule()},1500)}
async function perform(action){if(busy)return;busy=true;refreshControls();try{await action()}catch(error){entry('WORKSPACE',error.message,true);say(error.message,'Needs attention');if(error.status===401||error.status===404)lost(error);else if(error.status===409&&sid)await sync()}finally{busy=false;refreshControls();schedule()}}
async function openSession(){if(sid)return;say('Opening Git High in your managed browser…','Working');entry('COMPUTER','Opening Git High…');const session=await api('/api/sessions','POST',{url:TARGET});sid=session.id;remember(sid);frameSequence=0;await sync();if(connected)entry('COMPUTER','Git High is open. Your live screen is connected.')}
async function control(mode){if(!connected||!sid)throw Error('Reconnect to the browser before changing control.');const state=await api('/api/sessions/'+encodeURIComponent(sid)+'/control','POST',{mode,epoch});controller=state.mode;epoch=state.epoch;entry('CONTROL',mode==='human'?'You took control. REIGN’s browser actions are paused.':'Control returned to REIGN.');await sync()}
async function closeSession(){if(!sid)return;await api('/api/sessions/'+encodeURIComponent(sid),'DELETE');stop();entry('COMPUTER','Session closed and browser resources released.');say('Open a browser whenever you’re ready.','Ready')}
async function input(data){if(!connected||controller!=='human')throw Error('Take control before sending input.');await api('/api/sessions/'+encodeURIComponent(sid)+'/input','POST',{epoch,...data});await sync()}
async function checkStatus(){const state=await api('/api/status');const ready=state.status==='ready';entry('WORKSPACE',ready?'Core, workflow service, and browser worker are ready.':'Workspace dependencies are unavailable.');if(!sid)say(ready?'Open Git High to begin.':'Your workspace is reconnecting.',ready?'Ready':'Reconnecting');if(selectedTab==='Agents')showDetails(state);return state}
function showDetails(state){$('details').replaceChildren();const lines=selectedTab==='Agents'?(state?Object.entries(state.components).map(([name,value])=>name+': '+value):['Checking connected services…']):selectedTab==='Tools'?['Managed Chromium browser · available','External computer / full desktop · not connected','LangGraph agent · '+(agentReady?'connected':'not configured'),'Voice · '+(voiceUi.active?'call active':voiceCapability?.configured?'ready to connect':'not configured')]:selectedTab==='Work'?['Git High · connected workspace destination','Open a browser to view and control your work.']:[];for(const text of lines){const p=document.createElement('p');p.textContent=text;$('details').append(p)}$('details').hidden=!lines.length}
function selectTab(tab){selectedTab=tab;document.querySelectorAll('[data-tab]').forEach(button=>{button.classList.toggle('on',button.dataset.tab===tab);button.setAttribute('aria-pressed',String(button.dataset.tab===tab))});$('panelName').textContent=tab==='Chat'?'LIVING CANVAS':tab==='Work'?'WORKSPACE':tab.toUpperCase();showDetails();if(tab==='Agents')perform(checkStatus);wake()}
function openExpanded(){if(!sid||!connected)return;lastFocus=document.activeElement;$('remoteOverlay').hidden=false;document.body.style.overflow='hidden';$('closeRemote').focus()}
function closeExpanded(){if($('remoteOverlay').hidden)return;$('remoteOverlay').hidden=true;document.body.style.overflow='';lastFocus?.focus();lastFocus=null}
async function signedIn(){authenticated=true;$('loginPanel').hidden=true;$('console').hidden=false;$('loginMessage').textContent='';refreshControls();entry('WORKSPACE','Signed in. Your commands now reach the live workspace.');sid=recalled();if(sid){await sync();schedule()}await restoreChat();await loadVoiceCapability();try{await checkStatus()}catch(error){say('Signed in. Workspace dependencies are still reconnecting.','Reconnecting');entry('WORKSPACE',error.message,true)}}
$('loginForm').onsubmit=async e=>{e.preventDefault();const button=e.currentTarget.querySelector('button');button.disabled=true;$('loginMessage').textContent='Signing in…';try{await api('/login','POST',{password:$('password').value});$('password').value='';await signedIn()}catch(error){$('loginMessage').textContent=error.status===401?'That password wasn’t accepted. Try again.':error.message}finally{button.disabled=false}};
$('open').onclick=()=>perform(openSession);$('take').onclick=$('takeExpanded').onclick=()=>perform(()=>control('human'));$('return').onclick=$('returnExpanded').onclick=()=>perform(()=>control('agent'));$('close').onclick=()=>perform(closeSession);$('reconnect').onclick=()=>perform(async()=>{frameSequence=0;await sync()});$('expandRemote').onclick=openExpanded;$('closeRemote').onclick=closeExpanded;
$('logout').onclick=()=>perform(async()=>{await voiceController.stop({reason:'Signed out. Microphone off.'});await clearChat();if(sid)await closeSession();await api('/logout','POST');stop();authenticated=false;$('console').hidden=true;$('loginPanel').hidden=false;$('password').focus();$('feed').replaceChildren();refreshControls()});
$('inputForm').onsubmit=e=>{e.preventDefault();perform(async()=>{await input({kind:'text',text:$('text').value});$('text').value=''})};$('scroll').onclick=()=>perform(()=>input({kind:'scroll',dy:450}));$('scrollUp').onclick=()=>perform(()=>input({kind:'scroll',dy:-450}));$('tabKey').onclick=()=>perform(()=>input({kind:'key',key:'Tab'}));$('enterKey').onclick=()=>perform(()=>input({kind:'key',key:'Enter'}));
for(const id of ['screen','fullScreen'])$(id).onclick=e=>{if(controller!=='human'||busy||!connected)return;const image=e.currentTarget,rect=image.getBoundingClientRect(),scale=Math.min(rect.width/1280,rect.height/800),left=rect.left+(rect.width-1280*scale)/2,top=rect.top+(rect.height-800*scale)/2,x=(e.clientX-left)/scale,y=(e.clientY-top)/scale;if(x<0||x>=1280||y<0||y>=800)return;perform(()=>input({kind:'click',x,y}))};
$('form').onsubmit=e=>{e.preventDefault();const text=$('message').value.trim();if(!text||busy||chatOccupied())return;$('message').value='';entry('YOU',text);const command=text.toLowerCase().replace(/[.!?]+$/,'');const actions={'open git high':openSession,'open':openSession,'take control':()=>control('human'),'return control':()=>control('agent'),'return to reign':()=>control('agent'),'close session':closeSession,'close':closeSession,'status':checkStatus};if(actions[command])perform(actions[command]);else sendChat(text)};
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>selectTab(button.dataset.tab));
$('pin').onclick=()=>{pinned=!pinned;$('pin').textContent=pinned?'Unpin':'Pin visible';$('pin').setAttribute('aria-pressed',String(pinned));wake()};pinned=false;
$('motion').onclick=e=>{run=!run;e.currentTarget.innerHTML=run?'<i aria-hidden="true">◉</i>Pause fluid':'<i aria-hidden="true">▶</i>Resume fluid';if(run)animate()};if(!run)$('motion').innerHTML='<i aria-hidden="true">▶</i>Resume fluid';
document.addEventListener('pointerdown',wake,{passive:true});setInterval(()=>{if(!pinned&&!sid&&Date.now()-lastActivity>5200)$('panel').classList.add('dim')},750);
document.addEventListener('keydown',e=>{if($('remoteOverlay').hidden)return;if(e.key==='Escape'){closeExpanded();return}if(e.key==='Tab'){const nodes=[...$('remoteOverlay').querySelectorAll('button:not(:disabled),input:not(:disabled)')].filter(x=>!x.hidden);const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}});
document.addEventListener('visibilitychange',()=>{if(document.hidden){pausePolling();if(voiceUi.active)voiceController.stop({reason:'Call ended because REIGN moved to the background.'})}else if(sid)sync().then(schedule)});
window.addEventListener('pagehide',()=>{if(voiceUi.active)voiceController.stop({reason:'Call ended because the page closed.'})});


// Voice and typed requests share one graph lease. No new turn can overtake an
// active or paused run, including the interval before POST /api/chat returns.
function chatOccupied(){return !!chatOwner||!!chatPending||['running','paused','disconnected','cancelling'].includes(chatState)}
function notifyChatWaiters(){for(const notify of [...chatWaiters])notify()}
function abortError(){return Object.assign(new Error('The voice request was stopped.'),{name:'AbortError'})}
function waitForChat(signal,onStatus){
  if(signal?.aborted)return Promise.reject(abortError());
  if(!chatOccupied())return Promise.resolve();
  onStatus?.('queued');
  return new Promise((resolve,reject)=>{
    let timer;
    const finish=(error)=>{clearTimeout(timer);chatWaiters.delete(check);signal?.removeEventListener('abort',cancel);error?reject(error):resolve()};
    const check=()=>{if(!chatOccupied())finish()};
    const cancel=()=>finish(abortError());
    chatWaiters.add(check);signal?.addEventListener('abort',cancel,{once:true});
    timer=setTimeout(()=>finish(Error('The current task is still running or paused. Finish it or use Stop / new chat.')),300000);
    check();
  });
}
function rememberChat(){try{threadId?sessionStorage.setItem('reign-thread',threadId):sessionStorage.removeItem('reign-thread')}catch{}}
function settleChatJob(job,result,error){
  if(!job)return;
  job.signal?.removeEventListener('abort',job.abortListener);
  if(error)job.reject(error);else job.resolve(result);
  if(chatPending===job){chatPending=null;chatOwner=null;refreshControls();notifyChatWaiters()}
}
async function restoreChat(){
  try{
    const capability=await api('/api/agent');agentReady=capability.configured;
    $('agentLabel').textContent=agentReady?'REIGN is ready · conversation memory lasts for this service session':'AI connection is not configured';
    threadId=sessionStorage.getItem('reign-thread');if(threadId)await pollChat();
  }catch(error){
    if(error.status===404){threadId=null;chatState='idle';rememberChat()}
    else entry('AGENT','Agent connection is unavailable. Browser controls remain available.',true)
  }
}
async function startChatRun(text,{owner='typed',signal,onStatus,connectionCheck=false}={}){
  while(chatOccupied())await waitForChat(signal,onStatus);
  if(signal?.aborted)throw abortError();
  if(!authenticated)throw Error('Sign in before sending a voice request.');
  if(!agentReady)throw Error('The AI connection is unavailable. Browser controls remain available.');
  if(threadId&&['failed','cancelled'].includes(chatState))throw Error('The previous task did not complete. Use Stop / new chat before continuing; completed actions remain.');
  const generation=chatGeneration;
  let resolve,reject;const outcome=new Promise((yes,no)=>{resolve=yes;reject=no});
  const job={owner,signal,onStatus,generation,resolve,reject,cancelled:false,aborted:false,threadId:null,submissionError:null};
  job.abortListener=()=>{job.aborted=true;reject(abortError())};
  signal?.addEventListener('abort',job.abortListener,{once:true});
  chatOwner=owner;chatPending=job;chatState='running';refreshControls();onStatus?.('running');
  if(owner!=='typed')entry(connectionCheck?'VOICE CHECK':'YOU · VOICE',text);
  job.submission=(async()=>{
    // Rotate only an explicitly completed, exhausted thread. A generic conflict
    // must never trigger an automatic replay of a possibly completed action.
    if(threadId&&chatInfo?.status==='completed'&&chatInfo.turns>=chatInfo.max_turns){
      await api('/api/chat/'+encodeURIComponent(threadId),'DELETE');
      threadId=null;chatInfo=null;chatRun=null;chatSeen=0;rememberChat();
      entry('CONVERSATION','Started a new conversation after the previous one reached its limit. Earlier conversation memory is no longer active.');
    }
    if(job.aborted||job.cancelled)return;
    const result=await api('/api/chat','POST',{message:text,thread_id:threadId,session_id:sid});
    job.threadId=result.thread_id;
    if(job.aborted||job.cancelled||generation!==chatGeneration)return;
    threadId=result.thread_id;rememberChat();chatRun=null;
    await pollChat();
  })();
  job.submission.catch(error=>{
    job.submissionError=error;
    if(job.cancelled||job.aborted)return;
    if(chatPending!==job)return;
    if(!job.threadId&&(!error.status||error.status>=500)){
      // A lost POST response cannot establish whether Core accepted the task.
      // Keep the lease instead of starting or replaying another action.
      chatState='disconnected';job.reject(error);refreshControls();
      entry('AGENT','The request outcome is unknown. Reload to check the workspace before sending another request.',true);return;
    }
    chatState='failed';entry('AGENT',error.status===409?'The conversation cannot accept that request. Use Stop / new chat; completed actions remain.':error.message,true);
    settleChatJob(job,null,error);
  });
  return outcome;
}
async function sendChat(text){try{await startChatRun(text)}catch(error){if(error.name!=='AbortError')entry('AGENT',error.message,true)}}
async function pollChat(){
  clearTimeout(chatPoll);chatPoll=null;if(!threadId)return;
  const activeThread=threadId,generation=chatGeneration;
  try{
    const result=await api('/api/chat/'+encodeURIComponent(activeThread));
    if(threadId!==activeThread||generation!==chatGeneration)return;
    chatInfo=result;
    if(chatRun!==result.run_id){chatRun=result.run_id;chatSeen=0}
    chatState=result.status;
    for(const event of result.events){
      if(event.sequence<=chatSeen)continue;chatSeen=event.sequence;
      if(event.kind==='tool_started')entry('REIGN','Using '+event.tool.replaceAll('_',' ')+'…');
      if(event.kind==='tool_completed')entry('TOOL',event.tool.replaceAll('_',' ')+(event.ok?' completed.':' could not complete.'),!event.ok);
      if(event.kind==='paused')entry('REIGN','Paused while you control the browser. Return control, then press Resume.');
      if(event.kind==='completed')entry('REIGN',result.answer||'The run finished without a text response.');
      if(event.kind==='failed')entry('AGENT',result.error,true);
    }
    chatPending?.onStatus?.(chatState);
    if(result.session_id&&!sid){sid=result.session_id;remember(sid);frameSequence=0;await sync();schedule()}
    $('agentLabel').textContent=chatState==='running'?'REIGN is working…':chatState==='paused'?'Waiting for you to return control':chatState==='completed'?'Ready · '+result.usage.input_tokens+' input / '+result.usage.output_tokens+' output tokens':chatState==='failed'?'Run failed · start a new chat':'Agent ready';
    refreshControls();
    if(['completed','failed','cancelled'].includes(chatState)){
      settleChatJob(chatPending,result);notifyChatWaiters();
    }else if(chatState==='running')chatPoll=setTimeout(pollChat,750);
  }catch(error){
    if(threadId!==activeThread||generation!==chatGeneration)return;
    chatState='disconnected';entry('AGENT',error.message,true);
    if(error.status===404){threadId=null;chatState='idle';chatInfo=null;rememberChat();settleChatJob(chatPending,null,error);notifyChatWaiters()}
    else if(error.status===401){settleChatJob(chatPending,null,error);voiceController?.stop({reason:'Your sign-in expired. Sign in to continue.',failed:true})}
    refreshControls();$('agentLabel').textContent='Agent connection interrupted';
    if(error.status!==404&&error.status!==401)chatPoll=setTimeout(pollChat,2000);
  }
}
async function clearChat(expectedOwner){
  const job=chatPending;
  if(expectedOwner&&job?.owner!==expectedOwner)return;
  if(job)job.cancelled=true;
  chatGeneration++;clearTimeout(chatPoll);chatPoll=null;
  const previousThread=threadId;
  chatState='cancelling';refreshControls();
  try{
    // Keep the submission alive until its real thread ID is known. Aborting a
    // POST alone would leave accepted server work running without a cancel ID.
    if(job?.submission)await job.submission;
    if(job?.submissionError&&!job.threadId)throw Error('The request outcome is unknown. Task cancellation could not be confirmed.');
    const target=job?.threadId||previousThread;
    if(target){try{await api('/api/chat/'+encodeURIComponent(target),'DELETE',undefined,{keepalive:true})}catch(error){if(error.status!==404)throw error}}
    threadId=null;chatRun=null;chatSeen=0;chatInfo=null;chatState='idle';rememberChat();
    settleChatJob(job,null,abortError());chatOwner=null;refreshControls();notifyChatWaiters();
    $('agentLabel').textContent='New conversation · browser session stays open';
  }catch(error){
    if(job){job.cancelled=false;job.generation=chatGeneration;if(job.threadId){threadId=job.threadId;rememberChat()}}
    chatState='disconnected';refreshControls();
    if(threadId)chatPoll=setTimeout(pollChat,1000);
    throw error;
  }
}
async function runVoiceTurn(message,options){return startChatRun(message,{...options,owner:options.voiceCallId})}
async function cancelVoiceTurn(owner){if(chatPending?.owner===owner)await clearChat(owner)}
$('cancelAgent').onclick=async()=>{try{if(voiceUi.active)await voiceController.stop({reason:'Call ended and the unfinished voice task was stopped.'});await clearChat()}catch(error){entry('AGENT',error.message,true)}};
$('resumeAgent').onclick=async()=>{if(!threadId)return;try{await api('/api/chat/'+encodeURIComponent(threadId)+'/resume','POST');chatRun=null;await pollChat()}catch(error){entry('AGENT','Return browser control before resuming. '+error.message,true)}};

function refreshVoiceControls(){
  const active=!!voiceUi.active,ending=voiceUi.phase==='ending';
  $('voiceStart').hidden=active||ending;$('voiceStart').disabled=!authenticated||!voiceCapability?.configured||ending;
  $('voiceEnd').hidden=!active&&!ending;$('voiceEnd').disabled=ending;
  $('mute').hidden=!active;$('mute').disabled=!active||['checking','microphone'].includes(voiceUi.phase);
  $('mute').setAttribute('aria-pressed',String(!!voiceUi.muted));$('mute').querySelector('span').textContent=voiceUi.muted?'Unmute':'Mute';
  $('voiceSpeaker').hidden=!active||!voiceUi.speakerBlocked;
}
function renderVoice(value){
  voiceUi=value;$('voiceStatus').textContent=value.status;$('voiceStatus').dataset.error=String(value.phase==='failed');
  refreshVoiceControls();
  const labels={checking:'Connecting',microphone:'Allow microphone',connecting:'Connecting',listening:value.muted?'Microphone muted':'Listening',thinking:value.graphStatus==='paused'?'Waiting for you':'Thinking',speaking:'Speaking',reconnecting:'Reconnecting',ending:'Ending call',failed:'Voice needs attention',ended:'Ready',idle:'Ready'};
  if(value.active||['ending','failed','ended'].includes(value.phase)){
    $('state').textContent=labels[value.phase]||'Ready';
    voiceState=value.active?(['listening','thinking','speaking'].includes(value.phase)?value.phase:'working'):'idle';
    document.querySelector('.orb').dataset.mode=voiceState;activity=value.phase==='speaking'?.65:value.phase==='thinking'?.4:.1;
  }
  $('voiceDiagnostics').hidden=!value.active;
  $('voiceDiagnostics').textContent=value.audioPlaying?'Playing REIGN’s response':value.audioEnergy||value.audioReceived?'Audio connected':'Waiting for REIGN’s response';
  if(value.phase==='ended'&&connected)say(controller==='human'?'Click, type, or scroll in the expanded screen.':'Watch REIGN’s browser, or take control whenever you need.',controller==='human'?'Your control':'Connected');
  if(selectedTab==='Tools')showDetails();wake();
}
async function loadVoiceCapability(){
  try{
    voiceCapability=await api('/api/voice');
    if(!voiceUi.active)$('voiceStatus').textContent=voiceCapability.configured?'Start voice to speak with REIGN. A spoken greeting checks the connection.':'Voice is not configured for this workspace. Text and browser controls are available.';
  }catch(error){voiceCapability=null;$('voiceStatus').textContent='Voice availability could not be checked. Reload to retry; text and browser controls remain available.'}
  refreshVoiceControls();
}
voiceController=REIGNVoice.createVoiceController({
  api,runTurn:runVoiceTurn,cancelTurn:cancelVoiceTurn,audio:$('voiceAudio'),onState:renderVoice,
  onCaption(value){
    if(value.role!==captionRole||value.final){captionRole=value.role;captionText=value.text}else captionText+=value.text;
    captionText=captionText.slice(-16000);$('voiceCaption').hidden=!captionText;
    $('voiceCaption').textContent=(value.role==='user'?'You: ':'REIGN: ')+captionText;
  },
  onLevel(level){document.querySelector('.orb').style.setProperty('--voice-level',String(level));if(voiceUi.active)activity=Math.max(voiceUi.phase==='thinking'?.25:.06,level)}
});
$('voiceStart').onclick=()=>{captionRole='';captionText='';$('voiceCaption').hidden=true;voiceController.start()};
$('voiceEnd').onclick=()=>voiceController.stop();$('mute').onclick=()=>voiceController.mute();$('voiceSpeaker').onclick=()=>voiceController.enableSpeaker();
api('/api/me').then(signedIn).catch(()=>{authenticated=false;refreshControls()});

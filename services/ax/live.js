'use strict';
const $=id=>document.getElementById(id), TARGET='https://alambda-forge.onrender.com/';
let sid=null, epoch=null, controller=null, authenticated=false, connected=false;
let busy=false, syncing=null, restoringWorkspace=false, poll=null, frameURL=null, frameSequence=0, lastFocus=null;
let threadId=null, chatState='idle', chatPoll=null, chatSeen=0, chatRun=null, agentReady=false;
let selectedTab='Chat', pinned=true, lastActivity=Date.now();
let voiceController=null, voiceCapability=null, voiceUi={active:false,phase:'idle'}, captionRole='', captionText='';
let chatOwner=null, chatPending=null, chatGeneration=0, chatInfo=null;
let accountsVisible=false, accountsLoading=false, accountsMutation=false, accountsGeneration=0, accountsLoadId=0;
let accountsReadAbort=null, accountsFocus=null, accountRemovalId=null, savedAccounts=[];
let accountsVault={ready:false,durable:false,code:'vault_not_ready'};
const ACCOUNT_MESSAGES={
  vault_not_ready:'Secure saved accounts are not ready for this workspace.',
  vault_unavailable:'Secure saved accounts are unavailable. Refresh and try again.',
  invalid_account:'The saved account request is not valid. Check the form and try again.',
  origin_not_allowed:'That website is not allowed for saved logins.',
  account_not_found:'That saved login no longer exists. Refresh Accounts.',
  account_exists:'A saved login with that label already exists. Choose a different label.',
  account_use_conflict:'The saved login cannot be used in the current browser or conversation state.',
  grant_invalid:'This saved login request expired or could not be verified. Start a new request.'
};
const chatWaiters=new Set();
const expiredSessions=new Set();
const validSessionId=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
function wake(){lastActivity=Date.now();$('panel').classList.remove('dim')}
function remember(value){try{value?sessionStorage.setItem('reign-session',value):sessionStorage.removeItem('reign-session')}catch{}}
function recalled(){try{return sessionStorage.getItem('reign-session')}catch{return null}}
function say(message,mode){$('status').textContent=message;if(mode&&!voiceUi.active){$('state').textContent=mode;voiceState=mode==='Working'?'working':'idle';activity=mode==='Working'?.55:.1;document.querySelector('.orb').dataset.mode=mode==='Your control'?'human':voiceState}wake()}
function entry(title,message,error=false){const row=document.createElement('div');row.className='entry'+(error?' error':'');const label=document.createElement('b');label.textContent=title;const time=document.createElement('time');time.textContent=new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});row.append(time,label,document.createTextNode(message));$('feed').append(row);while($('feed').children.length>60)$('feed').firstChild.remove();$('feed').scrollTop=$('feed').scrollHeight;wake()}
function csrf(){return document.cookie.split('; ').find(x=>x.startsWith('ax_csrf='))?.slice(8)||''}

function renderRunTrace(result){
  const output=$('traceOutput');
  if(!result){output.textContent='No current conversation trace.';return}
  // This view is current-run, sanitized metadata only. It must never render
  // model messages, browser form values, raw errors, request bodies or secrets.
  const safe=value=>typeof value==='string'&&/^[a-z][a-z0-9_.-]{0,63}$/.test(value)?value:'—';
  const count=value=>Number.isSafeInteger(value)&&value>=0&&value<=10000000?String(value):'—';
  const storage=result.memory==='ephemeral'?'Ephemeral · lost after Core restart':
    result.memory==='injected'?'Injected checkpoint saver · durability unverified':'Not reported';
  const traceId=typeof result.trace_id==='string'&&/^[0-9a-f-]{36}$/.test(result.trace_id)?result.trace_id:'Unavailable';
  const spans=Array.isArray(result.trace_spans)?result.trace_spans.slice(-40):[];
  const lines=['Storage: '+storage,'Trace ID: '+traceId,
    'Trace events: '+spans.length+' (last 40)',''];
  for(const span of spans){
    if(!span||typeof span!=='object')continue;
    lines.push(safe(span.service)+' / '+safe(span.operation)+
      (safe(span.tool_name)!=='—'?' · '+safe(span.tool_name):'')+
      ' · '+safe(span.outcome)+' · '+count(span.duration_ms)+' ms'+
      ' · tokens '+count(span.input_tokens)+'/'+count(span.output_tokens)+
      (safe(span.error_code)!=='—'?' · '+safe(span.error_code):''));
  }
  output.textContent=lines.join('\n');
}

async function request(path,method='GET',body,options={}){
  const headers={},requestAbort=new AbortController();let timedOut=false;
  if(method!=='GET'){headers['Content-Type']='application/json';headers['X-CSRF-Token']=csrf()}
  const cancel=()=>requestAbort.abort();
  if(options.signal?.aborted)cancel();else options.signal?.addEventListener('abort',cancel,{once:true});
  const timeout=setTimeout(()=>{timedOut=true;requestAbort.abort()},path.startsWith('/api/chat')||path.startsWith('/api/accounts')?15000:path.startsWith('/api/voice')?40000:90000);
  try{
    const response=await fetch(path,{method,headers,credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal:requestAbort.signal,keepalive:!!options.keepalive});
    if(!response.ok){
      const error=new Error(response.status===401?'Your sign-in expired. Sign in again.':response.status===409?'Control or session availability changed. Refresh and try again.':response.status===404?'This session has expired. Open a new session.':response.status===403?'This action couldn’t be verified. Reload and sign in again.':'The workspace couldn’t complete that request. Try again.');
      if(path.startsWith('/api/voice')||path.startsWith('/api/accounts')||response.status===409){try{
        const value=await response.json();
        if(path.startsWith('/api/voice')&&typeof value.detail==='string')error.message=value.detail.slice(0,350);
        if(path.startsWith('/api/accounts')&&typeof value.code==='string'&&Object.hasOwn(ACCOUNT_MESSAGES,value.code)){
          error.code=value.code;error.message=ACCOUNT_MESSAGES[value.code];
        }
        if(response.status===409&&value.code==='browser_occupied'){
          error.code='browser_occupied';
          error.message='A browser session is already open. Reconnect to it or close it before opening another.';
        }
      }catch{}}
      error.status=response.status;
      // Every authenticated surface recovers to the same visible sign-in form.
      if(error.status===401&&authenticated){authenticated=false;lost(error)}
      throw error;
    }
    // fetch resolves at headers; retain the deadline until the entire body is
    // available so a stalled JSON response cannot strand task cancellation.
    const responseBody=await response.arrayBuffer();
    return new Response([204,205].includes(response.status)?null:responseBody,{status:response.status,statusText:response.statusText,headers:response.headers});
  }catch(error){
    if(timedOut)throw Object.assign(new Error('The workspace request timed out. Its outcome could not be confirmed.'),{status:504});
    throw error;
  }finally{clearTimeout(timeout);options.signal?.removeEventListener('abort',cancel)}
}
async function api(path,method='GET',body,options){return (await request(path,method,body,options)).json()}
function accountErrorMessage(error,mutation=false){
  if(typeof error?.code==='string'&&Object.hasOwn(ACCOUNT_MESSAGES,error.code))return ACCOUNT_MESSAGES[error.code];
  if(error?.status===401)return 'Your sign-in expired. Sign in again.';
  if(error?.status===403)return 'This action couldn’t be verified. Reload and sign in again.';
  return mutation?'The request outcome could not be confirmed. Refresh Accounts before trying again.':ACCOUNT_MESSAGES.vault_unavailable;
}
function showAccountError(message){$('accountsError').textContent=message||'';$('accountsError').hidden=!message}
function accountStoreReady(){return accountsVault.ready===true&&accountsVault.durable===true}
function refreshAccountsControls(){
  const blocked=!authenticated||restoringWorkspace||accountsLoading||accountsMutation;
  const secureInput=accountStoreReady()&&!blocked&&(!voiceUi.active||voiceUi.muted===true);
  $('accountsOpen').disabled=!authenticated||restoringWorkspace;
  for(const id of ['accountLabel','accountOrigin','accountUsername','accountPassword','accountSave'])$(id).disabled=!secureInput;
  $('accountsRefresh').disabled=blocked;
  const useBlocked=!secureInput||busy||!sid||!connected||!agentReady||chatOccupied()||!!accountRemovalId;
  $('accountsList').querySelectorAll('[data-account-use]').forEach(button=>{button.disabled=useBlocked});
  $('accountsList').querySelectorAll('[data-account-remove]').forEach(button=>{button.disabled=!secureInput||!!accountRemovalId});
  $('accountRemoveCancel').disabled=accountsMutation;
  $('accountRemoveConfirm').disabled=!secureInput||!accountRemovalId;
  $('accountsVoiceNotice').hidden=!accountsVisible||!voiceUi.active;
  $('accountsVoiceNotice').textContent=voiceUi.muted?'Your microphone is muted for secure account entry. You can unmute after closing Accounts.':'Muting your microphone before secure account entry…';
}
function clearAccountRemoval(){accountRemovalId=null;$('accountRemoval').hidden=true;$('accountRemoveMessage').textContent='';refreshAccountsControls()}
function renderSavedAccounts(){
  $('accountsList').replaceChildren();
  if(!savedAccounts.length){
    const empty=document.createElement('p');empty.textContent=accountStoreReady()?'No saved logins yet.':'Saved logins will appear when secure storage is available.';$('accountsList').append(empty);
  }
  for(const account of savedAccounts){
    const row=document.createElement('article');row.className='account-row';
    const label=document.createElement('strong');label.textContent=account.label;
    const origin=document.createElement('p');origin.textContent=account.origin;
    const username=document.createElement('p');username.textContent='Username: '+account.username;
    const actions=document.createElement('div');actions.className='account-row-actions';
    const use=document.createElement('button');use.type='button';use.textContent='Use saved login';use.dataset.accountUse=account.id;use.setAttribute('aria-label','Use saved login '+account.label);use.onclick=()=>useSavedAccount(account.id);
    const remove=document.createElement('button');remove.type='button';remove.textContent='Remove';remove.dataset.accountRemove=account.id;remove.setAttribute('aria-label','Remove saved login '+account.label);remove.onclick=()=>{
      if(accountsLoading||accountsMutation||!accountStoreReady())return;
      accountRemovalId=account.id;$('accountRemoveMessage').textContent='Remove “'+account.label+'” for '+account.origin+' from REIGN? This removes the saved login; the website account stays open.';$('accountRemoval').hidden=false;showAccountError('');refreshAccountsControls();$('accountRemoveCancel').focus();
    };
    actions.append(use,remove);row.append(label,origin,username,actions);$('accountsList').append(row);
  }
  refreshAccountsControls();
}
async function loadAccounts({notice=''}={}){
  if(!accountsVisible||!authenticated||accountsMutation)return;
  accountsReadAbort?.abort();const abort=new AbortController();accountsReadAbort=abort;
  const generation=accountsGeneration,load=++accountsLoadId;
  accountsLoading=true;clearAccountRemoval();showAccountError('');$('accountsStatus').textContent='Checking secure storage…';refreshAccountsControls();
  try{
    const result=await api('/api/accounts','GET',undefined,{signal:abort.signal});
    if(!accountsVisible||!authenticated||generation!==accountsGeneration||load!==accountsLoadId)return;
    if(!result?.vault||!Array.isArray(result.accounts)||result.accounts.length>100)throw Error('Invalid account metadata');
    accountsVault={ready:result.vault.ready===true,durable:result.vault.durable===true,code:typeof result.vault.code==='string'&&Object.hasOwn(ACCOUNT_MESSAGES,result.vault.code)?result.vault.code:'vault_not_ready'};
    savedAccounts=result.accounts.map(account=>{
      if(!validSessionId(account?.id)||typeof account.label!=='string'||typeof account.origin!=='string'||typeof account.username!=='string')throw Error('Invalid account metadata');
      // Keep only the metadata needed by this modal. Never retain arbitrary
      // server fields or credential material in browser application state.
      return {id:account.id,label:account.label,origin:account.origin,username:account.username};
    });
    $('accountsStatus').textContent=accountStoreReady()?(notice||'Secure saved accounts are ready. Choose a login to use, or add one below.'):(ACCOUNT_MESSAGES[accountsVault.code]||ACCOUNT_MESSAGES.vault_not_ready);
    renderSavedAccounts();
  }catch(error){
    if(error.name==='AbortError'||!accountsVisible||generation!==accountsGeneration||load!==accountsLoadId)return;
    accountsVault={ready:false,durable:false,code:'vault_unavailable'};savedAccounts=[];renderSavedAccounts();
    $('accountsStatus').textContent=ACCOUNT_MESSAGES.vault_unavailable;showAccountError(accountErrorMessage(error));
  }finally{
    if(generation===accountsGeneration&&load===accountsLoadId){accountsLoading=false;if(accountsReadAbort===abort)accountsReadAbort=null;refreshAccountsControls()}
  }
}
function presetAccountRequest({label,origin}={}){
  if(!$('accountLabel').value&&typeof label==='string'&&label.length<=80)$('accountLabel').value=label.trim();
  if(!$('accountOrigin').value&&typeof origin==='string'&&origin.length<=512){
    try{const parsed=new URL(origin);if(parsed.protocol==='https:'&&!parsed.username&&!parsed.password&&!parsed.search&&!parsed.hash&&['','/'].includes(parsed.pathname))$('accountOrigin').value=parsed.origin}catch{}
  }
}
function openAccounts(preset={}){
  if(!authenticated||restoringWorkspace)return;
  if(accountsVisible){presetAccountRequest(preset);return}
  closeExpanded();accountsFocus=document.activeElement;accountsVisible=true;accountsGeneration++;
  $('accountForm').reset();showAccountError('');clearAccountRemoval();presetAccountRequest(preset);
  $('accountsDialog').showModal();
  if(voiceUi.active&&!voiceUi.muted)voiceController?.mute();
  refreshVoiceControls();refreshAccountsControls();
  if(accountsMutation)$('accountsStatus').textContent='Finishing the saved account request…';else loadAccounts();
  $('accountsClose').focus();wake();
}
function closeAccounts(){
  // Closing never cancels or repeats a save/use already accepted by Core.
  // The password is erased even when a network request is still settling.
  accountsVisible=false;accountsGeneration++;accountsReadAbort?.abort();accountsReadAbort=null;accountsLoading=false;
  $('accountPassword').value='';$('accountForm').reset();savedAccounts=[];accountsVault={ready:false,durable:false,code:'vault_not_ready'};
  accountRemovalId=null;$('accountRemoval').hidden=true;$('accountRemoveMessage').textContent='';$('accountsList').replaceChildren();showAccountError('');
  if($('accountsDialog').open)$('accountsDialog').close();
  refreshVoiceControls();refreshAccountsControls();
  if(accountsFocus?.isConnected)accountsFocus.focus();accountsFocus=null;
}
async function saveAccount(event){
  event.preventDefault();
  if(!accountsVisible||accountsLoading||accountsMutation||!accountStoreReady()||!authenticated||(voiceUi.active&&!voiceUi.muted))return;
  if(!$('accountForm').reportValidity())return;
  // Password exists only in this form and this one request payload. It never
  // enters conversation, activity, captions, or browser storage.
  const body={label:$('accountLabel').value,origin:$('accountOrigin').value,username:$('accountUsername').value,password:$('accountPassword').value};
  $('accountPassword').value='';const generation=accountsGeneration;accountsMutation=true;showAccountError('');refreshAccountsControls();
  let saved=false;
  try{
    await api('/api/accounts','POST',body);saved=true;
    if(accountsVisible&&generation===accountsGeneration){$('accountForm').reset();$('accountsStatus').textContent='Saved login added.'}
  }catch(error){if(accountsVisible&&generation===accountsGeneration)showAccountError(accountErrorMessage(error,true))}
  finally{
    body.password='';$('accountPassword').value='';accountsMutation=false;refreshAccountsControls();
    if(accountsVisible&&authenticated&&(saved||generation!==accountsGeneration))await loadAccounts({notice:saved?'Saved login added.':undefined});
  }
}
async function removeSelectedAccount(){
  const id=accountRemovalId;
  if(!id||!savedAccounts.some(account=>account.id===id)||accountsLoading||accountsMutation||!accountStoreReady()||!authenticated)return;
  const generation=accountsGeneration;accountsMutation=true;showAccountError('');refreshAccountsControls();let removed=false;
  try{await api('/api/accounts/'+encodeURIComponent(id),'DELETE');removed=true}
  catch(error){if(accountsVisible&&generation===accountsGeneration)showAccountError(accountErrorMessage(error,true))}
  finally{
    accountsMutation=false;if(generation===accountsGeneration)clearAccountRemoval();refreshAccountsControls();
    if(accountsVisible&&authenticated&&(removed||generation!==accountsGeneration))await loadAccounts({notice:removed?'Saved login removed.':undefined});
  }
}
async function useSavedAccount(id){
  if(!savedAccounts.some(account=>account.id===id)||!accountStoreReady()||accountsLoading||accountsMutation||busy||restoringWorkspace||!authenticated||!sid||!connected||!agentReady||chatOccupied()||accountRemovalId)return;
  closeAccounts();
  try{await startChatRun('',{owner:'account-'+id,accountId:id})}
  catch(error){if(error.name!=='AbortError')entry('ACCOUNTS',accountErrorMessage(error,true),true)}
}
function refreshControls(){const blocked=busy||restoringWorkspace;const human=connected&&controller==='human'&&!blocked;const agent=connected&&controller==='agent'&&!blocked;$('open').disabled=!!sid||blocked||!authenticated;for(const id of ['take','takeExpanded'])$(id).disabled=!agent;for(const id of ['return','returnExpanded','tabKey','enterKey','scroll','scrollUp','send','text'])$(id).disabled=!human;$('close').disabled=!sid||blocked;$('expandRemote').disabled=!sid||!connected||restoringWorkspace;$('reconnect').hidden=!sid||connected;$('reconnect').disabled=blocked;$('logout').disabled=blocked;$('sendCommand').disabled=blocked||chatOccupied();$('resumeAgent').hidden=chatState!=='paused';$('resumeAgent').disabled=controller!=='agent'||restoringWorkspace;$('cancelAgent').hidden=!threadId&&!chatPending;$('cancelAgent').disabled=restoringWorkspace;$('session').textContent=sid||'None';$('controller').textContent=controller||'—';$('epoch').textContent=epoch??'—';$('controlLabel').textContent=!sid?'No active session':!connected?'Connection interrupted':human||controller==='human'?'You have control':'REIGN has control';$('remoteStatus').textContent=$('controlLabel').textContent;$('connection').textContent=!authenticated?'SIGN IN':connected?'CONNECTED':'WORKSPACE';$('liveLabel').textContent=connected?'● LIVE BROWSER':sid?'CONNECTION INTERRUPTED':'COMPUTER · NOT CONNECTED';refreshVoiceControls();refreshAccountsControls()}
function clearFrame(){for(const id of ['screen','fullScreen'])$(id).removeAttribute('src');$('screen').hidden=true;$('emptyScreen').hidden=false;if(frameURL)URL.revokeObjectURL(frameURL);frameURL=null;frameSequence=0;$('frameStatus').textContent='Waiting for a session'}
function pausePolling(){if(poll)clearTimeout(poll);poll=null}
function stop(){pausePolling();sid=null;epoch=null;controller=null;connected=false;remember(null);clearFrame();closeExpanded();refreshControls()}
function lost(error){connected=false;controller=null;epoch=null;pausePolling();clearFrame();$('frameStatus').textContent='No live frame';refreshControls();say(error.message,'Disconnected');entry('CONNECTION',error.message,true);if(error.status===404){if(sid)expiredSessions.add(sid);stop();say('Session expired. Open Git High to start again.','Ready')}if(error.status===401){closeAccounts();voiceController?.stop({reason:'Your sign-in expired. Sign in before calling again.',failed:true});stop();authenticated=false;$('console').hidden=true;$('loginPanel').hidden=false;$('loginMessage').textContent=error.message;refreshControls()}}
async function sync(){
  const activeId=sid;if(!activeId)return;
  // Recovery may replace a tab's stale session while its last frame is still
  // loading. Wait for that read, then fetch the selected session's own frame.
  if(syncing){await syncing;if(sid===activeId)return sync();return}
  const operation=(async()=>{
    try{
      const state=await api('/api/sessions/'+encodeURIComponent(activeId));if(sid!==activeId)return;
      controller=state.mode;epoch=state.epoch;
      const response=await request('/api/sessions/'+encodeURIComponent(activeId)+'/frame');
      if(response.headers.get('X-Session-ID')!==activeId||!response.headers.get('Content-Type')?.startsWith('image/jpeg'))throw Error('The live screen could not be verified. Reconnect to continue.');
      const sequence=Number(response.headers.get('X-Frame-Sequence'));
      if(!Number.isFinite(sequence)||sequence<=frameSequence)throw Error('The screen stopped updating. Reconnect to continue.');
      const blob=await response.blob();if(sid!==activeId)return;
      const nextURL=URL.createObjectURL(blob),previous=frameURL;frameURL=nextURL;frameSequence=sequence;
      for(const id of ['screen','fullScreen'])$(id).src=nextURL;if(previous)URL.revokeObjectURL(previous);
      $('screen').hidden=false;$('emptyScreen').hidden=true;$('frameStatus').textContent='Live · '+new Date().toLocaleTimeString();
      connected=true;refreshControls();
      say(controller==='human'?'Click, type, or scroll in the expanded screen.':'Watch REIGN’s browser, or take control whenever you need.',controller==='human'?'Your control':'Connected');
    }catch(error){if(sid===activeId)lost(error)}
  })();
  syncing=operation;try{await operation}finally{if(syncing===operation)syncing=null}
}
function schedule(){pausePolling();if(sid&&connected)poll=setTimeout(async()=>{await sync();schedule()},1500)}
async function perform(action){if(busy||restoringWorkspace)return;busy=true;refreshControls();try{await action()}catch(error){entry('WORKSPACE',error.message,true);say(error.message,'Needs attention');if(error.status===401||error.status===404)lost(error);else if(error.status===409&&sid)await sync()}finally{busy=false;refreshControls();schedule()}}
async function recoverBrowserSession({useStored=true}={}){
  const stored=useStored?recalled():null;
  let session=null;
  if(stored&&validSessionId(stored)){
    try{session=await api('/api/sessions/'+encodeURIComponent(stored))}
    catch(error){
      if(error.status!==404)throw error;
      expiredSessions.add(stored);remember(null);
    }
  }else if(stored)remember(null);
  if(!authenticated)return null;
  if(!session){
    const result=await api('/api/sessions/active');
    if(!authenticated)return null;
    session=result.session;
  }
  if(!session){stop();return null}
  if(!validSessionId(session.id)||!['agent','human'].includes(session.mode)||!Number.isInteger(session.epoch)||session.epoch<0){
    throw Error('The existing browser session could not be verified. Reload to reconnect.');
  }
  // Discovery attaches to the existing page. Never open, navigate, close, or
  // transfer ownership as part of restoring a tab's local session reference.
  pausePolling();clearFrame();sid=session.id;remember(sid);
  controller=session.mode;epoch=session.epoch;connected=false;refreshControls();
  await sync();
  if(connected&&sid===session.id){
    if(stored!==sid)entry('COMPUTER','Reconnected to the existing browser. Its page and control owner were preserved.');
    schedule();return session;
  }
  // A session can expire between lookup and its first frame. Discover once
  // more, without retrying any browser action.
  if(authenticated&&useStored&&expiredSessions.has(session.id))return recoverBrowserSession({useStored:false});
  return null;
}
async function openSession(){
  if(sid)return;
  say('Opening Git High in your managed browser…','Working');entry('COMPUTER','Opening Git High…');
  let session;
  try{session=await api('/api/sessions','POST',{url:TARGET})}
  catch(error){
    if(error.code!=='browser_occupied')throw error;
    entry('COMPUTER',error.message);
    if(!await recoverBrowserSession({useStored:false}))throw error;
    return;
  }
  sid=session.id;remember(sid);frameSequence=0;await sync();
  if(connected)entry('COMPUTER','Git High is open. Your live screen is connected.');
}
async function control(mode){if(!connected||!sid)throw Error('Reconnect to the browser before changing control.');const state=await api('/api/sessions/'+encodeURIComponent(sid)+'/control','POST',{mode,epoch});controller=state.mode;epoch=state.epoch;entry('CONTROL',mode==='human'?'You took control. REIGN’s browser actions are paused.':'Control returned to REIGN.');await sync()}
async function closeSession(){if(!sid)return;await api('/api/sessions/'+encodeURIComponent(sid),'DELETE');stop();entry('COMPUTER','Session closed and browser resources released.');say('Open a browser whenever you’re ready.','Ready')}
async function input(data){if(!connected||controller!=='human')throw Error('Take control before sending input.');await api('/api/sessions/'+encodeURIComponent(sid)+'/input','POST',{epoch,...data});await sync()}
async function checkStatus(){const state=await api('/api/status');const ready=state.status==='ready';entry('WORKSPACE',ready?'Core, workflow service, and browser worker are ready.':'Workspace dependencies are unavailable.');if(!sid)say(ready?'Open Git High to begin.':'Your workspace is reconnecting.',ready?'Ready':'Reconnecting');return state}
function showDetails(){
  $('details').replaceChildren();const work=selectedTab==='Work';
  if(work){for(const text of ['Git High · configured browser destination','Open a browser to view and control your work.']){const p=document.createElement('p');p.textContent=text;$('details').append(p)}}
  $('details').hidden=!work;$('workAccounts').hidden=!work;refreshAccountsControls();
}
function selectTab(tab){if(!['Chat','Work'].includes(tab))return;selectedTab=tab;document.querySelectorAll('[data-tab]').forEach(button=>{button.classList.toggle('on',button.dataset.tab===tab);button.setAttribute('aria-pressed',String(button.dataset.tab===tab))});$('panelName').textContent=tab==='Chat'?'LIVING CANVAS':'WORKSPACE';showDetails();wake()}
function openExpanded(){if(!sid||!connected)return;lastFocus=document.activeElement;$('remoteOverlay').hidden=false;document.body.style.overflow='hidden';$('closeRemote').focus()}
function closeExpanded(){if($('remoteOverlay').hidden)return;$('remoteOverlay').hidden=true;document.body.style.overflow='';lastFocus?.focus();lastFocus=null}
async function signedIn(){
  authenticated=true;restoringWorkspace=true;$('loginPanel').hidden=true;$('console').hidden=false;$('loginMessage').textContent='';
  refreshControls();entry('WORKSPACE','Signed in. Reconnecting to your existing workspace…');
  try{
    await recoverBrowserSession();if(!authenticated)return;
    await restoreChat();if(!authenticated)return;
    await loadVoiceCapability();if(!authenticated)return;
    try{await checkStatus()}
    catch(error){if(authenticated){say('Signed in. Workspace dependencies are still reconnecting.','Reconnecting');entry('WORKSPACE',error.message,true)}}
  }catch(error){
    if(authenticated){say('The existing workspace could not be recovered. Reload to try again.','Needs attention');entry('WORKSPACE',error.message,true)}
  }finally{restoringWorkspace=false;refreshControls()}
}
$('accountsOpen').onclick=()=>openAccounts();$('accountsClose').onclick=closeAccounts;
$('accountsRefresh').onclick=()=>loadAccounts();$('accountForm').onsubmit=saveAccount;
$('accountRemoveCancel').onclick=clearAccountRemoval;$('accountRemoveConfirm').onclick=removeSelectedAccount;
$('accountsDialog').addEventListener('cancel',event=>{event.preventDefault();closeAccounts()});
$('accountsDialog').addEventListener('close',()=>{if(accountsVisible&&!$('accountsDialog').open)closeAccounts()});
$('loginForm').onsubmit=async e=>{e.preventDefault();const button=e.currentTarget.querySelector('button');button.disabled=true;$('loginMessage').textContent='Signing in…';try{await api('/login','POST',{password:$('password').value});$('password').value='';await signedIn()}catch(error){$('loginMessage').textContent=error.status===401?'That password wasn’t accepted. Try again.':error.message}finally{button.disabled=false}};
$('open').onclick=()=>perform(openSession);$('take').onclick=$('takeExpanded').onclick=()=>perform(()=>control('human'));$('return').onclick=$('returnExpanded').onclick=()=>perform(()=>control('agent'));$('close').onclick=()=>perform(closeSession);$('reconnect').onclick=()=>perform(async()=>{frameSequence=0;await sync()});$('expandRemote').onclick=openExpanded;$('closeRemote').onclick=closeExpanded;
$('logout').onclick=()=>{closeAccounts();perform(async()=>{await voiceController.stop({reason:'Signed out. Microphone off.'});await clearChat();if(sid)await closeSession();await api('/logout','POST');stop();authenticated=false;$('console').hidden=true;$('loginPanel').hidden=false;$('password').focus();$('feed').replaceChildren();refreshControls()})};
$('inputForm').onsubmit=e=>{e.preventDefault();perform(async()=>{await input({kind:'text',text:$('text').value});$('text').value=''})};$('scroll').onclick=()=>perform(()=>input({kind:'scroll',dy:450}));$('scrollUp').onclick=()=>perform(()=>input({kind:'scroll',dy:-450}));$('tabKey').onclick=()=>perform(()=>input({kind:'key',key:'Tab'}));$('enterKey').onclick=()=>perform(()=>input({kind:'key',key:'Enter'}));
for(const id of ['screen','fullScreen'])$(id).onclick=e=>{if(controller!=='human'||busy||!connected)return;const image=e.currentTarget,rect=image.getBoundingClientRect(),scale=Math.min(rect.width/1280,rect.height/800),left=rect.left+(rect.width-1280*scale)/2,top=rect.top+(rect.height-800*scale)/2,x=(e.clientX-left)/scale,y=(e.clientY-top)/scale;if(x<0||x>=1280||y<0||y>=800)return;perform(()=>input({kind:'click',x,y}))};
$('form').onsubmit=e=>{e.preventDefault();const text=$('message').value.trim();if(!text||busy||restoringWorkspace||chatOccupied())return;$('message').value='';entry('YOU',text);const command=text.toLowerCase().replace(/[.!?]+$/,'');const actions={'open git high':openSession,'open':openSession,'take control':()=>control('human'),'return control':()=>control('agent'),'return to reign':()=>control('agent'),'close session':closeSession,'close':closeSession,'status':checkStatus};if(actions[command])perform(actions[command]);else sendChat(text)};
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>selectTab(button.dataset.tab));
$('pin').onclick=()=>{pinned=!pinned;$('pin').textContent=pinned?'Unpin':'Pin visible';$('pin').setAttribute('aria-pressed',String(pinned));wake()};pinned=false;
$('motion').onclick=e=>{run=!run;e.currentTarget.innerHTML=run?'<i aria-hidden="true">◉</i>Pause fluid':'<i aria-hidden="true">▶</i>Resume fluid';if(run)animate()};if(!run)$('motion').innerHTML='<i aria-hidden="true">▶</i>Resume fluid';
document.addEventListener('pointerdown',wake,{passive:true});setInterval(()=>{if(!pinned&&!sid&&Date.now()-lastActivity>5200)$('panel').classList.add('dim')},750);
document.addEventListener('keydown',e=>{if($('remoteOverlay').hidden)return;if(e.key==='Escape'){closeExpanded();return}if(e.key==='Tab'){const nodes=[...$('remoteOverlay').querySelectorAll('button:not(:disabled),input:not(:disabled)')].filter(x=>!x.hidden);const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}});
document.addEventListener('visibilitychange',()=>{if(document.hidden){closeAccounts();pausePolling();if(voiceUi.active)voiceController.stop({reason:'Call ended because REIGN moved to the background.'})}else if(sid)sync().then(schedule)});
window.addEventListener('pagehide',()=>{closeAccounts();if(voiceUi.active)voiceController.stop({reason:'Call ended because the page closed.'})});


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
function detachExpiredConversation(){
  if(!threadId||!expiredSessions.has(chatInfo?.session_id)||['running','paused'].includes(chatState)||chatPending)return false;
  // Only release this tab's stale reference. Discovery must never delete a
  // shared conversation or cancel a run that another tab may still be using.
  chatGeneration++;clearTimeout(chatPoll);chatPoll=null;
  threadId=null;chatRun=null;chatSeen=0;chatInfo=null;chatState='idle';chatOwner=null;rememberChat();
  entry('CONVERSATION','The previous conversation used an expired browser session. This tab will start a new conversation; previous server runs are preserved.');
  $('agentLabel').textContent='New conversation · existing browser preserved';
  refreshControls();notifyChatWaiters();return true;
}
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
    let stored=null;try{stored=sessionStorage.getItem('reign-thread')}catch{}
    if(stored&&!validSessionId(stored)){threadId=null;rememberChat();return}
    threadId=stored;if(threadId)await pollChat({restore:true});
  }catch(error){
    if(error.status===404){threadId=null;chatState='idle';rememberChat()}
    else entry('AGENT','Agent connection is unavailable. Browser controls remain available.',true)
  }
}
async function startChatRun(text,{owner='typed',signal,onStatus,connectionCheck=false,accountId=null}={}){
  if(restoringWorkspace)throw Error('Wait for the existing workspace to reconnect before sending a request.');
  while(chatOccupied())await waitForChat(signal,onStatus);
  if(signal?.aborted)throw abortError();
  if(!authenticated)throw Error('Sign in before sending a voice request.');
  if(!agentReady)throw Error('The AI connection is unavailable. Browser controls remain available.');
  if(accountId&&(!validSessionId(accountId)||!sid||!connected))throw Error('Open or reconnect to the managed browser before using a saved login.');
  if(threadId&&['failed','cancelled'].includes(chatState))throw Error('The previous task did not complete. Use Stop / new chat before continuing; completed actions remain.');
  const generation=chatGeneration;
  let resolve,reject;const outcome=new Promise((yes,no)=>{resolve=yes;reject=no});
  const job={owner,signal,onStatus,generation,resolve,reject,cancelled:false,aborted:false,threadId:null,submissionError:null};
  job.abortListener=()=>{job.aborted=true;reject(abortError())};
  signal?.addEventListener('abort',job.abortListener,{once:true});
  chatOwner=owner;chatPending=job;chatState='running';refreshControls();onStatus?.('running');
  if(accountId)entry('ACCOUNTS','Requested use of a saved login in the managed browser.');
  else if(owner!=='typed')entry(connectionCheck?'VOICE CHECK':'YOU · VOICE',text);
  job.submission=(async()=>{
    // Rotate only an explicitly completed, exhausted thread. A generic conflict
    // must never trigger an automatic replay of a possibly completed action.
    if(threadId&&chatInfo?.status==='completed'&&chatInfo.turns>=chatInfo.max_turns){
      await api('/api/chat/'+encodeURIComponent(threadId),'DELETE');
      threadId=null;chatInfo=null;chatRun=null;chatSeen=0;rememberChat();
      entry('CONVERSATION','Started a new conversation after the previous one reached its limit. Earlier conversation memory is no longer active.');
    }
    if(job.aborted||job.cancelled)return;
    const result=accountId?
      await api('/api/accounts/'+encodeURIComponent(accountId)+'/use','POST',{thread_id:threadId,session_id:sid}):
      await api('/api/chat','POST',{message:text,thread_id:threadId,session_id:sid});
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
async function pollChat({restore=false}={}){
  clearTimeout(chatPoll);chatPoll=null;if(!threadId)return;
  const activeThread=threadId,generation=chatGeneration;
  try{
    const result=await api('/api/chat/'+encodeURIComponent(activeThread));
    if(threadId!==activeThread||generation!==chatGeneration)return;
    if(restore&&result.session_id&&result.session_id!==sid&&!expiredSessions.has(result.session_id)){
      try{await api('/api/sessions/'+encodeURIComponent(result.session_id))}
      catch(error){if(error.status!==404)throw error;expiredSessions.add(result.session_id)}
      if(threadId!==activeThread||generation!==chatGeneration)return;
    }
    chatInfo=result;
    renderRunTrace(result);
    if(chatRun!==result.run_id){chatRun=result.run_id;chatSeen=0}
    chatState=result.status;
    if(detachExpiredConversation())return;
    if(restore&&expiredSessions.has(result.session_id)&&['running','paused'].includes(chatState)){
      entry('CONVERSATION','The restored task refers to an expired browser session. Its run remains tracked; use Stop / new chat only if you want to request cancellation.',true);
    }
    for(const event of result.events){
      if(event.sequence<=chatSeen)continue;chatSeen=event.sequence;
      if(event.kind==='tool_started')entry('REIGN','Using '+event.tool.replaceAll('_',' ')+'…');
      if(event.kind==='tool_completed')entry('TOOL',event.tool.replaceAll('_',' ')+(event.ok?' completed.':' could not complete.'),!event.ok);
      if(event.kind==='paused')entry('REIGN','Paused while you control the browser. Return control, then press Resume.');
      if(event.kind==='account_entry_requested'&&!restore)openAccounts({label:event.label,origin:event.origin});
      if(event.kind==='completed')entry('REIGN',result.answer||'The run finished without a text response.');
      if(event.kind==='failed')entry('AGENT',result.error,true);
    }
    chatPending?.onStatus?.(chatState);
    if(result.session_id&&!sid&&!expiredSessions.has(result.session_id)){sid=result.session_id;remember(sid);frameSequence=0;await sync();schedule()}
    $('agentLabel').textContent=chatState==='running'?'REIGN is working…':chatState==='paused'?'Waiting for you to return control':chatState==='completed'?'Ready · '+result.usage.input_tokens+' input / '+result.usage.output_tokens+' output tokens':chatState==='failed'?'Run failed · start a new chat':'Agent ready';
    refreshControls();
    if(['completed','failed','cancelled'].includes(chatState)){
      settleChatJob(chatPending,result);detachExpiredConversation();notifyChatWaiters();
    }else if(chatState==='running')chatPoll=setTimeout(pollChat,750);
  }catch(error){
    if(threadId!==activeThread||generation!==chatGeneration)return;
    if(error.status===404){
      // A confirmed expired conversation does not disconnect the recovered
      // browser. Drop only this tab's chat reference; never cancel server work.
      const interrupted=!!chatPending;
      error.message=interrupted?'The conversation expired. Check the browser before starting a new task; an action already sent may have completed.':'The previous conversation expired. Your next message will start a new conversation.';
      threadId=null;chatRun=null;chatSeen=0;chatInfo=null;chatState='idle';rememberChat();renderRunTrace(null);
      settleChatJob(chatPending,null,error);chatOwner=null;notifyChatWaiters();
      entry('CONVERSATION',error.message,interrupted);refreshControls();
      $('agentLabel').textContent='New conversation · previous chat expired';
      return;
    }
    chatState='disconnected';entry('AGENT',error.message,true);
    if(error.status===401){settleChatJob(chatPending,null,error);voiceController?.stop({reason:'Your sign-in expired. Sign in to continue.',failed:true})}
    refreshControls();$('agentLabel').textContent='Agent connection interrupted';
    if(error.status!==401)chatPoll=setTimeout(pollChat,2000);
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
    $('agentLabel').textContent='New conversation · check the browser; an action already sent may have completed.';
  }catch(error){
    if(job){job.cancelled=false;job.generation=chatGeneration;if(job.threadId){threadId=job.threadId;rememberChat()}}
    chatState='disconnected';refreshControls();
    if(threadId)chatPoll=setTimeout(pollChat,1000);
    throw error;
  }
}
async function runVoiceTurn(message,options){return startChatRun(message,{...options,owner:options.voiceCallId})}
async function cancelVoiceTurn(owner){if(chatPending?.owner===owner)await clearChat(owner)}
$('cancelAgent').onclick=async()=>{try{if(voiceUi.active)await voiceController.stop({reason:'Call ended. Microphone off.'});await clearChat()}catch(error){entry('AGENT',error.message,true)}};
$('resumeAgent').onclick=async()=>{if(!threadId)return;try{await api('/api/chat/'+encodeURIComponent(threadId)+'/resume','POST');chatRun=null;await pollChat()}catch(error){entry('AGENT','Return browser control before resuming. '+error.message,true)}};

function refreshVoiceControls(){
  const active=!!voiceUi.active,ending=voiceUi.phase==='ending';
  $('voiceStart').hidden=active||ending;$('voiceStart').disabled=!authenticated||restoringWorkspace||accountsVisible||!voiceCapability?.configured||ending;
  $('voiceEnd').hidden=!active&&!ending;$('voiceEnd').disabled=ending;
  $('mute').hidden=!active;$('mute').disabled=!active||accountsVisible||['checking','microphone'].includes(voiceUi.phase);
  $('mute').setAttribute('aria-pressed',String(!!voiceUi.muted));$('mute').querySelector('span').textContent=voiceUi.muted?'Unmute':'Mute';
  $('voiceSpeaker').hidden=!active||!voiceUi.speakerBlocked;
}
function renderVoice(value){
  voiceUi=value;
  if(accountsVisible&&value.active&&!value.muted&&voiceController?.mute())return;
  $('voiceStatus').textContent=value.status;$('voiceStatus').dataset.error=String(value.phase==='failed');
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
  refreshAccountsControls();wake();
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

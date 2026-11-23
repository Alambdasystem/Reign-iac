'use strict';
const $=id=>document.getElementById(id);
const code=new URLSearchParams(location.search).get('code');
if(code&&/^[A-Z2-9]{4}-[A-Z2-9]{4}$/.test(code))$('code').value=code;
history.replaceState(null,'','/connect/openstl');
const csrf=()=>document.cookie.split('; ').find(v=>v.startsWith('ax_csrf='))?.split('=')[1]||'';
async function request(path,body){const r=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',headers:body?{'Content-Type':'application/json','X-CSRF-Token':csrf()}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});const result=await r.json();if(!r.ok)throw Object.assign(Error(typeof result.detail==='string'?result.detail:'Connection failed'),{status:r.status});return result}
async function check(){try{await request('/api/me');$('login').hidden=true;$('consent').hidden=false;$('status').textContent='Check that this code matches your game before approving.'}catch{$('login').hidden=false;$('consent').hidden=true;$('status').textContent='Sign in here on REIGN. Your password is never sent to Open STL.'}}
$('login').onsubmit=async e=>{e.preventDefault();try{await request('/login',{password:$('password').value});$('password').value='';await check()}catch{$('password').value='';$('status').textContent='Sign-in failed. Check your REIGN password.'}};
$('approve').onclick=async()=>{const button=$('approve');button.disabled=true;try{await request('/api/openstl/approve',{user_code:$('code').value.trim().toUpperCase()});$('status').textContent='Connected. Return to Open STL; your workspace will open there.'}catch(e){$('status').textContent=e.message;if(e.status===401)await check()}finally{button.disabled=false}};
$('disconnect').onclick=async()=>{try{const r=await request('/api/openstl/disconnect-all',{});$('status').textContent=r.cleanup_pending?'Disconnected. REIGN is still stopping the task; cleanup will retry.':'Disconnected and stopped. Start a new connection inside Open STL.'}catch(e){$('status').textContent=e.message}};
check();

// Render cron entry point. This process only receives aggregate counts.
const url=process.env.RENT_ALERT_URL;
const key=process.env.RENT_ALERT_JOB_KEY;
if(!url?.startsWith("https://")||!key)throw Error("Configure RENT_ALERT_URL and RENT_ALERT_JOB_KEY.");
for(let batch=0;batch<100;batch++){
  const response=await fetch(url,{method:"POST",headers:{Authorization:"Bearer "+key},redirect:"error",signal:AbortSignal.timeout(120000)});
  if(!response.ok)throw Error("Monthstead returned HTTP "+response.status);
  const result=await response.json();
  if(typeof result.more!=="boolean")throw Error("Monthstead returned an unexpected response.");
  console.log(JSON.stringify(result));
  if(result.failed||result.unknown||result.unconfigured)process.exitCode=1;
  if(!result.more)break;
  if(batch===99)throw Error("Backlog remains for the next scheduled run.");
}

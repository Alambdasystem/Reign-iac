package systems.alambda.reignprobe;
import android.app.*;
import android.content.*;
import android.os.*;
import android.view.*;
import android.webkit.*;
import java.util.concurrent.*;

public class Probe extends Instrumentation {
 Activity activity;
 WebView web;
 @Override public void onCreate(Bundle b) { super.onCreate(b); start(); }
 private void report(String msg) { Bundle b=new Bundle();b.putString("progress",msg);sendStatus(1,b); }
 private WebView find(View v) {
  if(v instanceof WebView)return (WebView)v;
  if(v instanceof ViewGroup)for(int i=0;i<((ViewGroup)v).getChildCount();i++){WebView w=find(((ViewGroup)v).getChildAt(i));if(w!=null)return w;}
  return null;
 }
 private String js(String expression) throws Exception {
  CompletableFuture<String> val=new CompletableFuture<>();
  runOnMainSync(()->web.evaluateJavascript(expression,val::complete));
  return val.get(90,TimeUnit.SECONDS);
 }
 private void waitJs(String expression,int seconds) throws Exception {
  long deadline=SystemClock.elapsedRealtime()+seconds*1000L;
  while(SystemClock.elapsedRealtime()<deadline) {
   if("true".equals(js(expression)))return;
   Thread.sleep(1300);
  }
  throw new Exception("Timed out: "+expression+" at "+js("document.body?.innerText?.slice(0,1000)"));
 }
 @Override public void onStart() {
  Bundle result=new Bundle();
  try {
   String app="systems.alambda.reignnode";
   Intent i=new Intent().setClassName(app,app+".NodeActivity").setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
   activity=startActivitySync(i);
   runOnMainSync(()->web=find(activity.getWindow().getDecorView()));
   if(web==null)throw new Exception("No local WebView");
   waitJs("!!document.getElementById('load')",70);
   report("Loaded Android UI: "+js("document.title+' '+navigator.userAgent"));
   js("document.getElementById('load').click()");
   waitJs("document.getElementById('model').textContent.startsWith('Ready')",300);
   report("Android: local model ready");
   js("document.getElementById('prompt').value='What color is a ripe banana? Answer in one word.';document.getElementById('ask').click()");
   waitJs("!document.getElementById('reuse').hidden",360);
   String reply=js("document.getElementById('answer').textContent");
   report("Offline answer: "+reply);
   if(!reply.toLowerCase().contains("yellow"))throw new Exception("Unexpected answer");
   result.putString("outcome","Android local inference passed.");
   finish(Activity.RESULT_OK,result);
  } catch(Throwable t) {
   result.putString("error",t.toString());
   finish(Activity.RESULT_CANCELED,result);
  }
 }
}

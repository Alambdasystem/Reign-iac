
// Semi-Lagrangian incompressible 2D fluid simulation: velocity advection,
// pressure projection, dye advection, vorticity-like stirring and dissipation.
// The dye is transported by the velocity field, rather than moving circles.
const cv=document.getElementById('fluid'),ctx=cv.getContext('2d',{alpha:false});
const display=document.createElement('canvas'),dc=display.getContext('2d');
// GPU compositing: WebGL shader samples the simulated dye texture and applies
// nonlinear filmic color, edge lighting, and subtle refractive warping.
const gpuCanvas=document.createElement('canvas');
const gl=gpuCanvas.getContext('webgl',{alpha:false,premultipliedAlpha:false,preserveDrawingBuffer:false});
let gpuProgram=null,gpuTex=null,gpuBuffer=null,gpuTime=null,gpuState=null;
function initGPU(){if(!gl)return false;
 const compile=(type,source)=>{const sh=gl.createShader(type);gl.shaderSource(sh,source);gl.compileShader(sh);if(!gl.getShaderParameter(sh,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(sh));return sh};
 try{
 gl.getExtension('OES_standard_derivatives');
 const vs=compile(gl.VERTEX_SHADER,`attribute vec2 a;varying vec2 uv;void main(){uv=(a+1.)*.5;gl_Position=vec4(a,0.,1.);}`);
 const fs=compile(gl.FRAGMENT_SHADER,`#extension GL_OES_standard_derivatives : enable\nprecision mediump float;varying vec2 uv;uniform sampler2D dye;uniform float time;uniform float activity;
 void main(){vec2 q=uv;float n=sin(q.y*18.+time*.13)*sin(q.x*11.-time*.09);q+=vec2(n*.0015,-n*.001);vec3 c=texture2D(dye,q).rgb;
 float density=max(0.,c.r-c.g*.5);float ed=abs(dFdx(c.r))+abs(dFdy(c.r));
 vec3 base=vec3(.012,.001,.006);vec3 deep=vec3(.22,.009,.045);vec3 ruby=vec3(.72,.025,.14);vec3 bright=vec3(1.,.19,.27);
 float a=smoothstep(.02,.25,density);float b=smoothstep(.19,.8,density);float rim=clamp(ed*1.2,0.,1.)*b;
 vec3 color=mix(base,deep,a);color=mix(color,ruby,b*.85);color=mix(color,bright,clamp(rim*.36+activity*.07*b,0.,.3));
 color*=.92+.08*sin(time*.25+q.y*9.);gl_FragColor=vec4(color,1.);}`);
 gpuProgram=gl.createProgram();gl.attachShader(gpuProgram,vs);gl.attachShader(gpuProgram,fs);gl.linkProgram(gpuProgram);if(!gl.getProgramParameter(gpuProgram,gl.LINK_STATUS))throw Error('shader link failed');
 gl.useProgram(gpuProgram);gpuBuffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,gpuBuffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
 const loc=gl.getAttribLocation(gpuProgram,'a');gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);
 gpuTex=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,gpuTex);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
 gpuTime=gl.getUniformLocation(gpuProgram,'time');gpuState=gl.getUniformLocation(gpuProgram,'activity');gpuCanvas.width=NX;gpuCanvas.height=NY;gl.viewport(0,0,NX,NY);return true;
 }catch(e){console.warn('GPU compositing unavailable, using 2D fallback:',e);return false}
}
let useGPU=false;

const mobile=Math.min(innerWidth,innerHeight)<650, NX=mobile?112:170, NY=Math.round(NX*innerHeight/innerWidth),N=NX*NY;
display.width=NX;display.height=NY;let img=dc.createImageData(NX,NY);
let vx=new Float32Array(N),vy=new Float32Array(N),tx=new Float32Array(N),ty=new Float32Array(N),r=new Float32Array(N),g=new Float32Array(N),b=new Float32Array(N),tr=new Float32Array(N),tg=new Float32Array(N),tb=new Float32Array(N),pressure=new Float32Array(N),div=new Float32Array(N),p2=new Float32Array(N);
const ix=(x,y)=>Math.max(0,Math.min(NY-1,y|0))*NX+Math.max(0,Math.min(NX-1,x|0));
function sample(a,x,y){x=Math.max(0,Math.min(NX-1.001,x));y=Math.max(0,Math.min(NY-1.001,y));let X=x|0,Y=y|0,fx=x-X,fy=y-Y,i=Y*NX+X;return (a[i]*(1-fx)+a[i+1]*fx)*(1-fy)+(a[i+NX]*(1-fx)+a[i+NX+1]*fx)*fy}
function advect(dst,src,dt,decay){for(let y=1;y<NY-1;y++)for(let x=1;x<NX-1;x++){let i=y*NX+x;dst[i]=sample(src,x-vx[i]*dt,y-vy[i]*dt)*decay}}
function inject(x,y,dx,dy,strength=1){const rad=Math.max(5,NX*.10),minX=Math.max(1,(x-rad)|0),maxX=Math.min(NX-2,(x+rad)|0),minY=Math.max(1,(y-rad)|0),maxY=Math.min(NY-2,(y+rad)|0);for(let j=minY;j<=maxY;j++)for(let k=minX;k<=maxX;k++){let xx=(k-x)/rad,yy=(j-y)/rad,dist=xx*xx+yy*yy;if(dist>1)continue;let fall=Math.pow(1-dist,2),i=j*NX+k;vx[i]+=(dx-yy*1.9)*fall*strength;vy[i]+=(dy+xx*1.9)*fall*strength;let wisps=.65+.35*Math.sin(k*.21+j*.43+clock*.015);r[i]=Math.min(2.2,r[i]+fall*strength*.42*wisps);g[i]=Math.min(2,g[i]+fall*strength*.05);b[i]=Math.min(2,b[i]+fall*strength*.11)}}
let activity=0,voiceState="idle";let clock=0,run=!matchMedia('(prefers-reduced-motion: reduce)').matches,raf=0;
function step(){clock++;
// Autonomous asymmetric disturbances: they create vortices, not visible circular shapes.
if(clock%9===0){
 const force=voiceState==='speaking'?1.6:voiceState==='thinking'?1.25:voiceState==='working'?1.1:.65;
 let cx=NX*.5,cy=NY*.38,t=clock*.011;
 // State-dependent asymmetrical currents around the voice orb.
 inject(cx+Math.sin(t*1.3)*NX*.11,cy+Math.cos(t*.9)*NY*.08,
 voiceState==='speaking'?Math.sin(t)*3:Math.cos(t)*-.8,
 voiceState==='speaking'?Math.cos(t)*3:Math.sin(t)*-.8,force*.22);
 }
if(clock%11===0){let t=clock*.008,x=NX*(.5+.31*Math.sin(t*.81+Math.sin(t*.23))),y=NY*(.5+.32*Math.cos(t*.63+Math.sin(t*.31)));inject(x,y,Math.sin(t*1.2)*1.2,Math.cos(t*.8)*1.3,.75)}
if(clock%37===0){let t=clock*.017;inject(NX*(.5+.38*Math.sin(t*1.31)),NY*(.5+.4*Math.cos(t*.7)),Math.cos(t)*2,Math.sin(t)*2,.85)}
// Transport velocity and apply damping.
advect(tx,vx,.75,.994);advect(ty,vy,.75,.994);[vx,tx]=[tx,vx];[vy,ty]=[ty,vy];
// Compute divergence.
for(let y=1;y<NY-1;y++)for(let x=1;x<NX-1;x++){let i=y*NX+x;div[i]=-.5*(vx[i+1]-vx[i-1]+vy[i+NX]-vy[i-NX]);pressure[i]=0}
// Jacobi pressure solve.
for(let it=0;it<17;it++){for(let y=1;y<NY-1;y++)for(let x=1;x<NX-1;x++){let i=y*NX+x;p2[i]=(div[i]+pressure[i-1]+pressure[i+1]+pressure[i-NX]+pressure[i+NX])*.25}[pressure,p2]=[p2,pressure]}
for(let y=1;y<NY-1;y++)for(let x=1;x<NX-1;x++){let i=y*NX+x;vx[i]-=.5*(pressure[i+1]-pressure[i-1]);vy[i]-=.5*(pressure[i+NX]-pressure[i-NX])}
// Advect dyes through the same velocity field.
advect(tr,r,.75,.998);advect(tg,g,.75,.998);advect(tb,b,.75,.998);[r,tr]=[tr,r];[g,tg]=[tg,g];[b,tb]=[tb,b];
}
function paint(){const data=img.data;for(let i=0;i<N;i++){let density=Math.max(0,r[i]);let w=Math.min(1,Math.pow(density,1.15));let folds=Math.max(0,Math.min(1,(r[i]-g[i]*.35)*.8));let light=Math.pow(w,1.6);data[i*4]=Math.min(255,6+210*folds+45*light);data[i*4+1]=Math.min(255,1+13*w+45*light+35*g[i]);data[i*4+2]=Math.min(255,3+16*w+33*light+70*b[i]);data[i*4+3]=255}dc.putImageData(img,0,0);ctx.imageSmoothingEnabled=true;
 if(useGPU){
  gl.bindTexture(gl.TEXTURE_2D,gpuTex);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,display);
  gl.uniform1f(gpuTime,clock*.016);gl.uniform1f(gpuState,activity);
  gl.drawArrays(gl.TRIANGLES,0,6);ctx.drawImage(gpuCanvas,0,0,cv.width,cv.height);
 }else ctx.drawImage(display,0,0,cv.width,cv.height)}
function size(){let d=Math.min(devicePixelRatio||1,1.5);cv.width=innerWidth*d;cv.height=innerHeight*d}addEventListener('resize',size);size();
function animate(){if(!run)return;step();paint();raf=requestAnimationFrame(animate)}
// Seed irregularly over time, allowing actual advection to form folds.
for(let i=0;i<65;i++){let t=i*.27;inject(NX*(.5+.34*Math.sin(t*.61+Math.sin(t*.29))),NY*(.5+.3*Math.cos(t*.83)),Math.cos(t)*1.6,Math.sin(t*.7)*1.4,1.1);step()}
useGPU=initGPU();paint();if(run)animate();
let pointer=null;cv.style.pointerEvents='auto';
addEventListener('pointerdown',e=>{if(e.target.closest('button,input,form,.screen-card,.remote-overlay'))return;pointer={x:e.clientX,y:e.clientY};wake()});
addEventListener('pointermove',e=>{if(!pointer||!run)return;let x=e.clientX/innerWidth*NX,y=e.clientY/innerHeight*NY,dx=(e.clientX-pointer.x)/innerWidth*NX,dy=(e.clientY-pointer.y)/innerHeight*NY;inject(x,y,dx*1.6,dy*1.6,2.3);pointer={x:e.clientX,y:e.clientY};wake()});
addEventListener('pointerup',()=>pointer=null);


"use strict";
/* Twenty more openings, written to the motion-design rules from whaleyxbt/claude-motion:
   one focal point per frame, one accent colour, entrances ease out, exits ease in, scenes overlap,
   every readable state holds for at least a second, nothing is dead still (grain, drift, breathing),
   and every transition is motivated by an object on screen. Each draws one frame from the time t. */

const SANS='Bahnschrift,"Segoe UI",sans-serif', SERIF='Georgia,"Palatino Linotype",serif', MONO='Consolas,"Courier New",monospace';
const INK="#14110f", PAPER="#f1ebdf", EMBER="#ff5a1f", AMBER="#ffb347";
const sm=t=>t*t*(3-2*t);
const easeOutB=t=>1-Math.pow(1-clamp(t),4);           // bezier(0.16,1,0.3,1)-like
const easeInB=t=>Math.pow(clamp(t),2.4);
const frac=x=>x-Math.floor(x);
function tx(c,s,x,y,px,col,o={}){ c.font=`${o.italic?"italic ":""}${o.weight||400} ${px}px ${o.font||SANS}`; c.fillStyle=col; c.textAlign=o.align||"center"; c.textBaseline=o.base||"middle"; c.fillText(s,x,y); }
function tracked(c,s,cx,y,px,col,track,o={}){ c.font=`${o.weight||400} ${px}px ${o.font||SANS}`; c.fillStyle=col; c.textBaseline="middle"; c.textAlign="left";
  const total=spacedWidth(c,s,track); let x=cx-total/2; [...s].forEach(ch=>{ c.fillText(ch,x,y); x+=c.measureText(ch).width+track; }); return total; }
function wordCanvas(key,w,h,draw){ return textCanvas(key,w,h,draw); }
function sizedCache(key,w,h,make){ const k="s"+key+w+"x"+h; if(!cache[k]) cache[k]=make(); return cache[k]; }

/* 21. Aperture: seven blades open on a lit page */
const aperture={ id:"aperture", title:"Aperture", by:"The iris opens on the word", dur:12, still:8.4,
  blurb:"Seven blades open from a pinhole of light. What they reveal is a lit page, and the name sitting still in it.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#07080a"; c.fillRect(0,0,w,h);
    const k=eio(ramp(t,.7,5.6)), r=lerp(7*u,1150*u,k), rot=-.5-t*.16, N=7, Rv=r*1.06;
    const vs=[...Array(N)].map((_,i)=>[cx+Math.cos(rot+i*2*Math.PI/N)*Rv, cy+Math.sin(rot+i*2*Math.PI/N)*Rv]);
    c.save(); c.beginPath(); vs.forEach(([x,y],i)=>i?c.lineTo(x,y):c.moveTo(x,y)); c.closePath(); c.clip();
    const g=c.createRadialGradient(cx,cy-60*u,50*u,cx,cy,900*u); g.addColorStop(0,"#fbf4e4"); g.addColorStop(1,"#d9cdb4"); c.fillStyle=g; c.fillRect(0,0,w,h);
    const s=lerp(1.1,1,eoutQ(ramp(t,.7,7)));
    c.translate(cx,cy); c.scale(s,s); tx(c,"Carmen",0,-10*u,230*u,INK,{font:SERIF,italic:true});
    const a=easeOutB(ramp(t,6.6,8)); c.globalAlpha=a; tracked(c,"F O C U S",0,118*u+(1-a)*14*u,30*u,EMBER,6*u,{weight:600}); c.globalAlpha=1; c.restore();
    c.strokeStyle=`rgba(255,244,222,${.55*(1-ramp(t,5,6.2))})`; c.lineWidth=2*u;
    vs.forEach(([x,y],i)=>{ const [nx,ny]=vs[(i+1)%N]; const dx=nx-x, dy=ny-y; c.beginPath(); c.moveTo(x-dx*.9,y-dy*.9); c.lineTo(nx+dx*.9,ny+dy*.9); c.stroke(); });
    const halo=c.createRadialGradient(cx,cy,r*.7,cx,cy,r*1.5); halo.addColorStop(0,"rgba(255,230,190,.16)"); halo.addColorStop(1,"rgba(255,230,190,0)"); c.fillStyle=halo; c.fillRect(0,0,w,h);
    grain(c,t,w,h,.07); vig(c,w,h,.45); } };

/* 22. Metronome: each tick sets one letter */
const metronome={ id:"metronome", title:"Tempo", by:"One tick, one letter", dur:12, still:9,
  blurb:"A metronome on warm paper. Every swing of the weight sets a letter of the name, then it slows and stops upright.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    const bg=c.createLinearGradient(0,0,0,h); bg.addColorStop(0,"#eee7d8"); bg.addColorStop(1,"#e0d6c1"); c.fillStyle=bg; c.fillRect(0,0,w,h);
    const T0=1.3, P=.7, word="CARMEN";
    [...word].forEach((ch,i)=>{ const ti=T0+P*i, a=easeOutB(ramp(t,ti,ti+.5)); if(a<=0) return;
      c.globalAlpha=a; tx(c,ch,cx+(i-2.5)*118*u,cy-205*u+(1-a)*-26*u,128*u,INK,{weight:700}); c.globalAlpha=1; });
    const amp=.52*easeOutB(ramp(t,0,1.3))*(1-eio(ramp(t,5.8,7.8)));
    const th=amp*Math.cos(Math.PI*(t-T0)/P);
    const baseY=cy+215*u, pivotY=cy+170*u;
    c.fillStyle=INK; c.beginPath(); c.moveTo(cx-120*u,baseY); c.lineTo(cx+120*u,baseY); c.lineTo(cx+38*u,cy-45*u); c.lineTo(cx-38*u,cy-45*u); c.closePath(); c.fill();
    c.fillStyle="#2a241f"; c.beginPath(); c.moveTo(cx-70*u,baseY); c.lineTo(cx+70*u,baseY); c.lineTo(cx+24*u,cy-45*u); c.lineTo(cx-24*u,cy-45*u); c.closePath(); c.fill();
    c.save(); c.translate(cx,pivotY); c.rotate(th);
    c.strokeStyle="#cfc4ac"; c.lineWidth=5*u; c.lineCap="round"; c.beginPath(); c.moveTo(0,40*u); c.lineTo(0,-285*u); c.stroke();
    c.fillStyle=EMBER; c.fillRect(-20*u,-185*u,40*u,56*u); c.fillStyle="rgba(255,255,255,.28)"; c.fillRect(-20*u,-185*u,10*u,56*u);
    c.restore(); c.fillStyle="#cfc4ac"; c.beginPath(); c.arc(cx,pivotY,10*u,0,7); c.fill();
    const tick=Math.abs(Math.cos(Math.PI*(t-T0)/P)); if(t>T0&&t<T0+P*6&&tick>.97){ c.fillStyle="rgba(255,90,31,.10)"; c.fillRect(0,0,w,h); }
    const sub=easeOutB(ramp(t,8.2,9.6)); c.globalAlpha=sub; tracked(c,"F O C U S",cx,cy-120*u+(1-sub)*10*u,26*u,EMBER,8*u,{weight:600}); c.globalAlpha=1;
    grain(c,t,w,h,.05,160); vig(c,w,h,.25); } };

/* 23. Gooey: drops pull together, then flood the frame */
const gooey={ id:"gooey", title:"Drops", by:"Five drops become one", dur:12, still:9,
  blurb:"Five drops of orange circle each other, merge into one, then flood the screen and leave the name cut out of it.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    const sw=Math.round(w/2), sh=Math.round(h/2);
    const off=sizedCache("goo",w,h,()=>{ const o=document.createElement("canvas"); o.width=sw; o.height=sh; return o; });
    const o=off.getContext("2d"); o.globalCompositeOperation="source-over"; o.fillStyle="#000"; o.fillRect(0,0,sw,sh); o.globalCompositeOperation="lighter";
    const spread=lerp(330,0,eio(ramp(t,.8,4.2))), flood=easeInB(ramp(t,4.6,6.2)), base=lerp(52,100,eio(ramp(t,.8,4.2)));
    for(let i=0;i<5;i++){ const a=t*(.9+.13*i)+i*1.26, rr=spread*(.7+.3*Math.sin(t*.8+i)), x=sw/2+Math.cos(a)*rr*u/2, y=sh/2+Math.sin(a*1.1)*rr*u/2*.8;
      const R=(base+i*4)*u/2 + flood*700*u; const g=o.createRadialGradient(x,y,0,x,y,R*1.6); g.addColorStop(0,"rgba(255,255,255,1)"); g.addColorStop(.45,"rgba(255,255,255,.85)"); g.addColorStop(1,"rgba(255,255,255,0)");
      o.fillStyle=g; o.beginPath(); o.arc(x,y,R*1.6,0,7); o.fill(); }
    c.fillStyle="#101010"; c.fillRect(0,0,w,h);
    c.save(); c.filter="contrast(40) blur(0.6px)"; c.drawImage(off,0,0,w,h); c.restore();
    c.globalCompositeOperation="multiply"; c.fillStyle=EMBER; c.fillRect(0,0,w,h); c.globalCompositeOperation="source-over";
    const a=easeOutB(ramp(t,6,7.4)); c.globalAlpha=a; tx(c,"Carmen",cx,cy-6*u+(1-a)*30*u,230*u,"#14110f",{font:SERIF,italic:true}); c.globalAlpha=1;
    const b=easeOutB(ramp(t,7.8,9)); c.globalAlpha=b; tracked(c,"F O C U S",cx,cy+130*u,28*u,"#fff1e6",9*u,{weight:600}); c.globalAlpha=1;
    grain(c,t,w,h,.06,160); } };

/* 24. Departure board */
const flipboard={ id:"flipboard", title:"Departures", by:"A split-flap board settles", dur:12, still:9,
  blurb:"Every cell clatters through the alphabet before it lands. A board for one departure: the name, then the word focus.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#0f1012"; c.fillRect(0,0,w,h);
    const AL="ABCDEFGHIJKLMNOPQRSTUVWXYZ", R=rng(5);
    const row=(word,cw,ch,gap,y,px,t0)=>{ const total=word.length*cw+(word.length-1)*gap, x0=cx-total/2;
      [...word].forEach((target,i)=>{ const settle=t0+i*.34+R()*.4, start=t0-.6+i*.12, x=x0+i*(cw+gap), a=easeOutB(ramp(t,start-.4,start+.3));
        c.globalAlpha=a; c.fillStyle="#1e1f23"; c.beginPath(); c.roundRect(x,y-ch/2,cw,ch,8*u); c.fill();
        c.fillStyle="rgba(255,255,255,.035)"; c.beginPath(); c.roundRect(x,y-ch/2,cw,ch/2,[8*u,8*u,0,0]); c.fill();
        let glyph=target, sy=1;
        if(t<settle){ const q=t*13+i*5; glyph=AL[Math.floor(q)%26]; sy=Math.max(.12,Math.abs(Math.cos(Math.PI*frac(q)))); }
        else { sy=1-.5*Math.max(0,Math.sin(Math.PI*ramp(t,settle,settle+.18))); }
        c.save(); c.translate(x+cw/2,y); c.scale(1,sy); tx(c,glyph,0,0,px,"#f1ebdf",{weight:700}); c.restore();
        c.fillStyle="#0a0a0b"; c.fillRect(x,y-1.2*u,cw,2.4*u); c.globalAlpha=1; }); };
    row("CARMEN",100*u,146*u,10*u,cy-46*u,112*u,1.6);
    row("FOCUS",58*u,82*u,7*u,cy+98*u,60*u,4.4);
    const bar=easeOutB(ramp(t,7.6,8.8)); c.fillStyle=EMBER; c.fillRect(cx-170*u,cy+168*u,340*u*bar,4*u);
    grain(c,t,w,h,.05,140); vig(c,w,h,.4); } };

/* 25. Halftone */
function halftoneSamples(w,h,s){ return sizedCache("ht"+s,w,h,()=>{
  const u=U(w,h), mask=textCanvas("htm",w,h,x=>{ x.fillStyle="#fff"; x.fillRect(0,0,w,h); x.fillStyle="#000"; x.textAlign="center"; x.textBaseline="middle"; x.font=`700 ${250*u}px ${SANS}`; x.fillText("CARMEN",w/2,h/2-10*u); });
  const blur=px=>{ const o=document.createElement("canvas"); o.width=w; o.height=h; const x=o.getContext("2d"); x.filter=`blur(${px}px)`; x.drawImage(mask,0,0); return x.getImageData(0,0,w,h).data; };
  const heavy=blur(26*u), light=blur(1.2*u), cols=Math.ceil(w/s)+1, rows=Math.ceil(h/s)+1, out=[];
  for(let j=0;j<rows;j++) for(let i=0;i<cols;i++){ const x=Math.min(w-1,Math.round(i*s)), y=Math.min(h-1,Math.round(j*s)), k=(y*w+x)*4; out.push([i*s+(j%2?s/2:0),j*s,1-heavy[k]/255,1-light[k]/255]); }
  return out; }); }
const halftone={ id:"halftone", title:"Halftone", by:"Dots of ink resolve into a name", dur:12, still:9,
  blurb:"A print screen. Dots swell in from the left as a smudge, then the screen tightens until the smudge is a sharp word.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2, s=11*u, dots=halftoneSamples(w,h,s);
    c.fillStyle=PAPER; c.fillRect(0,0,w,h);
    const f=sm(ramp(t,3.2,6.8)); c.fillStyle=INK;
    for(const [x,y,hv,lv] of dots){ const appear=easeOutB(ramp(t-.3-(x/w)*1.5,0,.9)); const v=lerp(hv,lv,f); const r=Math.max(.9*u,s*.66*Math.sqrt(clamp(v)))*appear; if(r<.3) continue; c.beginPath(); c.arc(x,y,r,0,7); c.fill(); }
    const a=easeOutB(ramp(t,7.6,9)); c.globalAlpha=a; tracked(c,"F O C U S",cx,cy+170*u,28*u,EMBER,10*u,{weight:700}); c.globalAlpha=1;
    grain(c,t,w,h,.05,120); } };

/* 26. Orbits line up */
const orbit={ id:"orbit", title:"Alignment", by:"Seven orbits line up", dur:12, still:9,
  blurb:"Seven bodies, each on its own orbit, drift apart and then, slowly, all fall into one line. Then the system lifts to make room for the name.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2;
    const bg=c.createRadialGradient(cx,h/2,0,cx,h/2,w*.7); bg.addColorStop(0,"#121a33"); bg.addColorStop(1,"#070a14"); c.fillStyle=bg; c.fillRect(0,0,w,h);
    const R=rng(77); for(let i=0;i<90;i++){ c.fillStyle=`rgba(255,255,255,${.1+.25*R()*(.6+.4*Math.sin(t*1.3+i))})`; c.fillRect(R()*w,R()*h,1.4*u,1.4*u); }
    const f=eio(ramp(t,1.2,5.4)), lift=eio(ramp(t,5.8,7.6)), sc=lerp(1,.5,lift), oy=lerp(h/2,h/2-150*u,lift);
    c.save(); c.translate(cx,oy); c.scale(sc,sc);
    const rad=[], pts=[];
    for(let k=0;k<7;k++){ const r=(54+k*40)*u; rad.push(r); c.strokeStyle="rgba(180,200,255,.16)"; c.lineWidth=1.3*u; c.beginPath(); c.arc(0,0,r,0,7); c.stroke();
      const ang=(k*1.9+1.1+(.5+.22*k)*t)*(1-f); pts.push([Math.cos(ang)*r,Math.sin(ang)*r]); }
    const aligned=ramp(t,5,5.6); if(aligned>0){ c.strokeStyle=`rgba(255,176,110,${.9*aligned*(1-lift*.5)})`; c.lineWidth=2.2*u; c.beginPath(); c.moveTo(0,0); c.lineTo(rad[6],0); c.stroke(); }
    pts.forEach(([x,y],k)=>{ const col=k===6?EMBER:"#f1ebdf", r=(k===6?9:6.5)*u; const g=c.createRadialGradient(x,y,0,x,y,r*4); g.addColorStop(0,col==="#f1ebdf"?"rgba(241,235,223,.35)":"rgba(255,90,31,.5)"); g.addColorStop(1,"rgba(0,0,0,0)"); c.fillStyle=g; c.beginPath(); c.arc(x,y,r*4,0,7); c.fill(); c.fillStyle=col; c.beginPath(); c.arc(x,y,r,0,7); c.fill(); });
    c.fillStyle="#f1ebdf"; c.beginPath(); c.arc(0,0,11*u,0,7); c.fill(); c.restore();
    const ring=ramp(t,5.2,6.6); if(ring>0&&ring<1){ c.strokeStyle=`rgba(255,200,150,${.5*(1-ring)})`; c.lineWidth=3*u; c.beginPath(); c.arc(cx,h/2,easeOutB(ring)*700*u,0,7); c.stroke(); }
    const a=easeOutB(ramp(t,7,8.6)); c.globalAlpha=a; tx(c,"Carmen",cx,h/2+95*u+(1-a)*20*u,210*u,"#f6efe0",{font:SERIF,italic:true}); tracked(c,"F O C U S",cx,h/2+235*u,26*u,EMBER,10*u,{weight:600}); c.globalAlpha=1;
    grain(c,t,w,h,.05,120); } };

/* 27. Searchlight */
const searchlight={ id:"searchlight", title:"Searchlight", by:"A beam goes looking", dur:12, still:9,
  blurb:"Total dark. A single beam wanders the stage, catching pieces of a word, until it stops and widens on the whole name.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#050506"; c.fillRect(0,0,w,h);
    const word=textCanvas("slw",w,h,x=>{ x.fillStyle="#e9e2d2"; x.textAlign="center"; x.textBaseline="middle"; x.font=`700 ${200*u}px ${SANS}`; x.fillText("CARMEN",w/2,h/2); });
    c.globalAlpha=.07; c.drawImage(word,0,0); c.globalAlpha=1;
    const g=eio(ramp(t,5.2,7.4)), R=lerp(170,1300,g)*u, wander=1-g;
    const bx=cx+Math.sin(t*.8)*340*u*wander, by=cy+Math.sin(t*1.7+1)*60*u*wander;
    const layer=sizedCache("slb",w,h,()=>{ const o=document.createElement("canvas"); o.width=w; o.height=h; return o; }), l=layer.getContext("2d");
    l.globalCompositeOperation="source-over"; l.clearRect(0,0,w,h); l.drawImage(word,0,0); l.globalCompositeOperation="destination-in";
    const rg=l.createRadialGradient(bx,by,R*.25,bx,by,R); rg.addColorStop(0,"rgba(0,0,0,1)"); rg.addColorStop(1,"rgba(0,0,0,0)"); l.fillStyle=rg; l.fillRect(0,0,w,h);
    c.drawImage(layer,0,0);
    c.save(); c.globalCompositeOperation="lighter"; const cone=c.createLinearGradient(cx*.0+bx,0,bx,by); cone.addColorStop(0,"rgba(255,236,200,.0)"); cone.addColorStop(1,"rgba(255,236,200,.13)");
    c.fillStyle=cone; c.beginPath(); c.moveTo(bx-30*u,-10); c.lineTo(bx+30*u,-10); c.lineTo(bx+R*.8,by+R*.5); c.lineTo(bx-R*.8,by+R*.5); c.closePath(); c.fill();
    const pool=c.createRadialGradient(bx,by,0,bx,by,R); pool.addColorStop(0,"rgba(255,226,170,.10)"); pool.addColorStop(1,"rgba(255,226,170,0)"); c.fillStyle=pool; c.fillRect(0,0,w,h); c.restore();
    const D=rng(3); c.fillStyle="rgba(255,240,210,.5)"; for(let i=0;i<46;i++){ const px=bx+(D()-.5)*R*1.3, py=D()*by*1.4+((t*12*(.4+D()))%(h*.3)); c.fillRect(px,py%h,1.5*u,1.5*u); }
    const a=easeOutB(ramp(t,7.8,9.2)); c.globalAlpha=a; tracked(c,"F O C U S",cx,cy+150*u,26*u,AMBER,12*u,{weight:600}); c.globalAlpha=1;
    grain(c,t,w,h,.08,200); vig(c,w,h,.6); } };

/* 28. Curtain */
const curtain={ id:"curtain", title:"Curtain", by:"The velvet parts", dur:13, still:9.5,
  blurb:"Heavy velvet folds gather to the sides on a slow ease. A single light is already on the name behind them.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    const st=c.createLinearGradient(0,0,0,h); st.addColorStop(0,"#1a1410"); st.addColorStop(1,"#0a0807"); c.fillStyle=st; c.fillRect(0,0,w,h);
    const lit=easeOutB(ramp(t,.5,2)); const sp=c.createRadialGradient(cx,cy-20*u,0,cx,cy-20*u,520*u); sp.addColorStop(0,`rgba(255,214,140,${.38*lit})`); sp.addColorStop(1,"rgba(255,214,140,0)"); c.fillStyle=sp; c.fillRect(0,0,w,h);
    tx(c,"Carmen",cx,cy-14*u,230*u,"#f3d28f",{font:SERIF,italic:true});
    const a=easeOutB(ramp(t,7.6,9)); c.globalAlpha=a; tracked(c,"F O C U S",cx,cy+130*u,28*u,"#f3d28f",12*u,{weight:600}); c.globalAlpha=1;
    const o=eio(ramp(t,2.2,6.4)), half=w/2, pw=half*(1-o*.9), folds=11, sway=Math.sin(t*1.1)*3*u*(1-o);
    for(const side of [-1,1]){ for(let x=0;x<pw;x+=3){ const p=x/pw, ph=p*folds*Math.PI*2+(1-o)*0.0, shade=.5+.5*Math.sin(ph), dark=lerp(.0,.55,Math.min(1,o*1.2)*(1-p*.0));
        const col=mix("#2a0505","#b41a1f",clamp(shade*(.85-dark*.3)+ (side<0?0:0)));
        c.fillStyle=col; const X=side<0?x+sway:w-x-3-sway; c.fillRect(X,0,3.2,h); } }
    const sh=c.createLinearGradient(0,0,0,h*.2); sh.addColorStop(0,"rgba(0,0,0,.55)"); sh.addColorStop(1,"rgba(0,0,0,0)"); c.fillStyle=sh; c.fillRect(0,0,w,h*.2);
    c.fillStyle="#6e0f14"; c.fillRect(0,0,w,34*u); c.fillStyle="#d8aa55"; c.fillRect(0,34*u,w,4*u);
    grain(c,t,w,h,.06,160); vig(c,w,h,.6); } };

/* 29. Marquee */
const marquee={ id:"marquee", title:"Marquee", by:"A sign chases itself awake", dur:13, still:9.5,
  blurb:"A ring of bulbs chases around a sign, all flash on together, then the letters warm up one by one.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#0b0908"; c.fillRect(0,0,w,h);
    const W=800*u, H=330*u, x0=cx-W/2, y0=cy-H/2, step=36*u, per=2*(W+H), n=Math.round(per/step), bulbs=[];
    for(let i=0;i<n;i++){ let d=i*per/n, x,y; if(d<W){x=x0+d;y=y0;} else if(d<W+H){x=x0+W;y=y0+d-W;} else if(d<2*W+H){x=x0+W-(d-W-H);y=y0+H;} else {x=x0;y=y0+H-(d-2*W-H);} bulbs.push([x,y]); }
    c.strokeStyle="#2a211a"; c.lineWidth=14*u; c.strokeRect(x0,y0,W,H); c.strokeStyle="#15100c"; c.lineWidth=8*u; c.strokeRect(x0,y0,W,H);
    const flash=ramp(t,4,4.3)*(1-ramp(t,4.9,5.6)), steady=ramp(t,4.2,5.2);
    bulbs.forEach(([x,y],i)=>{ let on=0; if(t<4) on=((i-Math.floor(t*11))%4+4)%4===0?1:.12; else on=lerp(1,.88+.12*Math.sin(t*5+i*1.7),steady*.6); on=Math.max(on,flash);
      if(t<.5) on*=ramp(t,0,.5)+.15;
      const g=c.createRadialGradient(x,y,0,x,y,26*u); g.addColorStop(0,`rgba(255,196,90,${.55*on})`); g.addColorStop(1,"rgba(255,196,90,0)"); c.fillStyle=g; c.beginPath(); c.arc(x,y,26*u,0,7); c.fill();
      c.fillStyle=on>.5?"#ffe2a8":"#3b2f22"; c.beginPath(); c.arc(x,y,6.5*u,0,7); c.fill(); });
    c.font=`700 italic ${180*u}px ${SERIF}`; c.textAlign="left"; c.textBaseline="middle"; const word="Carmen", total=c.measureText(word).width; let x=cx-total/2;
    [...word].forEach((ch,i)=>{ const on=easeOutB(ramp(t,5.4+i*.28,6+i*.28)); c.shadowColor="rgba(255,176,60,.9)"; c.shadowBlur=36*u*on; c.fillStyle=mix("#3a2d1e","#ffe9b8",on); c.fillText(ch,x,cy-18*u); x+=c.measureText(ch).width; });
    c.shadowBlur=0; const a=easeOutB(ramp(t,8,9.4)); c.globalAlpha=a; tracked(c,"F O C U S",cx,cy+112*u,28*u,AMBER,16*u,{weight:600}); c.globalAlpha=1;
    grain(c,t,w,h,.06,160); vig(c,w,h,.55); } };

/* 30. Assemble */
const assemble={ id:"assemble", title:"Assemble", by:"Shards fly home", dur:12, still:8.8,
  blurb:"A word cut into tiles. Each tile arrives from far outside the frame, turning, and locks into place with no seams.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#101010"; c.fillRect(0,0,w,h);
    const pack=sizedCache("asm",w,h,()=>{ const mask=textCanvas("asw",w,h,x=>{ x.fillStyle="#f1ebdf"; x.textAlign="center"; x.textBaseline="middle"; x.font=`700 ${240*u}px ${SANS}`; x.fillText("CARMEN",w/2,h/2-6*u); });
      const bx=cx-470*u, by=cy-150*u, bw=940*u, bh=300*u, cols=36, rows=12, tw=bw/cols, th=bh/rows, d=mask.getContext("2d").getImageData(0,0,w,h).data, R=rng(41), tiles=[];
      for(let j=0;j<rows;j++) for(let i=0;i<cols;i++){ const sx=bx+i*tw, sy=by+j*th; let ink=false; for(let yy=0;yy<th&&!ink;yy+=2) for(let xx=0;xx<tw;xx+=2){ if(d[((Math.floor(sy+yy))*w+Math.floor(sx+xx))*4+3]>20){ ink=true; break; } }
        if(!ink) continue; const a=R()*Math.PI*2, dist=(900+R()*700)*u; tiles.push({sx,sy,tw,th,ox:Math.cos(a)*dist,oy:Math.sin(a)*dist,rot:(R()-.5)*8,delay:.5+(i/cols)*1.3+R()*1.5}); }
      return {mask,tiles}; });
    for(const tl of pack.tiles){ const p=easeOutB(ramp(t,tl.delay,tl.delay+1.9)), q=1-p; if(p<=0) continue;
      c.save(); c.globalAlpha=Math.min(1,p*3); c.translate(tl.sx+tl.tw/2+tl.ox*q,tl.sy+tl.th/2+tl.oy*q); c.rotate(tl.rot*q); c.drawImage(pack.mask,tl.sx-.4,tl.sy-.4,tl.tw+.8,tl.th+.8,-tl.tw/2-.4,-tl.th/2-.4,tl.tw+.8,tl.th+.8); c.restore(); }
    const a=easeOutB(ramp(t,6.4,7.8)); c.fillStyle=EMBER; c.fillRect(cx-60*u,cy+170*u,120*u*a,5*u);
    c.globalAlpha=easeOutB(ramp(t,7.2,8.6)); tracked(c,"F O C U S",cx,cy+214*u,26*u,"#f1ebdf",12*u,{weight:600}); c.globalAlpha=1;
    grain(c,t,w,h,.05,140); vig(c,w,h,.4); } };

/* 31. Contours */
const contour={ id:"contour", title:"Contours", by:"A map comes into focus", dur:13, still:10,
  blurb:"Topographic lines wobble like terrain, then relax into perfect circles around a single point. The name sits at the centre.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#0a1312"; c.fillRect(0,0,w,h);
    const A=1-eio(ramp(t,1.2,7)), N=30;
    for(let k=0;k<N;k++){ const base=(14+k*17)*u, amp=A*(10+k*3.6)*u, col=mix("#58e0c2","#12342f",k/N), ph=k*.77;
      c.strokeStyle=col; c.lineWidth=(k%5===0?2.2:1.2)*u; c.globalAlpha=.35+.65*(1-k/N)*0+.65*(k%5===0?1:.7); c.beginPath();
      for(let i=0;i<=120;i++){ const th=i/120*Math.PI*2, n=Math.sin(3*th+ph+t*.25)*.5+Math.sin(5*th-ph*1.3)*.3+Math.sin(2*th+ph*.5+t*.18)*.2; const r=base+amp*n; const x=cx+Math.cos(th)*r, y=cy+Math.sin(th)*r*.92; i?c.lineTo(x,y):c.moveTo(x,y); }
      c.closePath(); c.stroke(); }
    c.globalAlpha=1;
    const hole=c.createRadialGradient(cx,cy,60*u,cx,cy,300*u); hole.addColorStop(0,`rgba(10,19,18,${.96*ramp(t,5.8,7.4)})`); hole.addColorStop(1,"rgba(10,19,18,0)"); c.fillStyle=hole; c.fillRect(0,0,w,h);
    const a=easeOutB(ramp(t,6.4,8)); c.globalAlpha=a; tx(c,"Carmen",cx,cy-6*u+(1-a)*14*u,150*u,"#e7fbf4",{font:SERIF,italic:true});
    tracked(c,"F O C U S",cx,cy+78*u,22*u,"#58e0c2",10*u,{weight:600}); c.globalAlpha=1;
    grain(c,t,w,h,.05,120); vig(c,w,h,.45); } };

/* 32. Timecode */
const timecode={ id:"timecode", title:"Timecode", by:"Twenty-five minutes, run at speed", dur:12, still:9,
  blurb:"A camera timecode races to 00:25:00:00, holds a beat, and gives way to the name in the same place.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#0d0d0f"; c.fillRect(0,0,w,h);
    const bw=20*u, m=70*u; c.strokeStyle="rgba(255,255,255,.55)"; c.lineWidth=2*u;
    for(const [sx,sy] of [[1,1],[-1,1],[1,-1],[-1,-1]]){ const x=sx>0?m:w-m, y=sy>0?m:h-m; c.beginPath(); c.moveTo(x,y+sy*bw*1.6); c.lineTo(x,y); c.lineTo(x+sx*bw*1.6,y); c.stroke(); }
    const blink=Math.floor(t*2)%2===0; if(blink){ c.fillStyle="#ff3b30"; c.beginPath(); c.arc(m+10*u,m+44*u,7*u,0,7); c.fill(); } tx(c,"REC",m+30*u,m+44*u,19*u,"#f1ebdf",{font:MONO,align:"left"});
    const frames=Math.round(45000*Math.pow(ramp(t,.5,4.8),2.4)), ff=frames%30, ss=Math.floor(frames/30)%60, mm=Math.floor(frames/1800)%60, hh=Math.floor(frames/108000), p2=n=>String(n).padStart(2,"0");
    const s=`${p2(hh)}:${p2(mm)}:${p2(ss)}:${p2(ff)}`, px=150*u, adv=px*.6, x0=cx-s.length*adv/2;
    const out=easeInB(ramp(t,6.2,7.4)), a=1-out;
    c.save(); c.globalAlpha=a; c.translate(cx,cy); c.scale(1+out*.1,1+out*.1); c.translate(-cx,-cy);
    [...s].forEach((ch,i)=>{ tx(c,ch,x0+i*adv+adv/2,cy-4*u,px,t>4.8&&t<6.2?"#ffb347":"#f1ebdf",{font:MONO,weight:700}); }); c.restore();
    const b=easeOutB(ramp(t,6.6,8)); c.globalAlpha=b; c.save(); c.beginPath(); c.rect(0,cy-150*u,w,300*u*b+1); c.clip();
    tx(c,"CARMEN",cx,cy+(1-b)*24*u,190*u,"#f1ebdf",{weight:700}); c.restore();
    c.globalAlpha=easeOutB(ramp(t,8.2,9.4)); tracked(c,"F O C U S",cx,cy+130*u,26*u,EMBER,14*u,{weight:600}); c.globalAlpha=1;
    c.fillStyle="rgba(0,0,0,.2)"; for(let y=0;y<h;y+=3) c.fillRect(0,y,w,1);
    grain(c,t,w,h,.06,160); vig(c,w,h,.5); } };

/* 33. Search */
const search={ id:"search", title:"Search", by:"A question, and an answer", dur:14, still:11.5,
  blurb:"Someone types the thing they actually want to know, takes the suggestion, and deletes it all for the one word that answers it.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    const bg=c.createLinearGradient(0,0,0,h); bg.addColorStop(0,"#f7f4ee"); bg.addColorStop(1,"#ebe5d8"); c.fillStyle=bg; c.fillRect(0,0,w,h);
    const q="how do I stop getting distracted", sugg=["at work","for 25 minutes at a time","by blocking sites","with a timer that holds me to it"];
    const pillW=780*u, pillH=84*u, px=cx-pillW/2, py=cy-150*u, fade=1-ramp(t,8.6,9.8);
    let n; if(t<5.4) n=Math.floor(q.length*easeOutB(ramp(t,.8,3.4))*1+0); else if(t<6.3) n=Math.floor(q.length*(1-ramp(t,5.4,6.3))); else n=0;
    if(t<5.4) n=Math.floor(q.length*ramp(t,.8,3.4)); const shown=t<6.5?q.slice(0,Math.max(0,n)):"Carmen".slice(0,Math.floor(6*ramp(t,6.6,7.6)));
    c.globalAlpha=fade; c.fillStyle="#fff"; c.shadowColor="rgba(60,40,20,.18)"; c.shadowBlur=26*u; c.shadowOffsetY=8*u; c.beginPath(); c.roundRect(px,py,pillW,pillH,pillH/2); c.fill(); c.shadowBlur=0; c.shadowOffsetY=0;
    c.strokeStyle="#8a8174"; c.lineWidth=3*u; c.beginPath(); c.arc(px+46*u,py+pillH/2-3*u,10*u,0,7); c.stroke(); c.beginPath(); c.moveTo(px+54*u,py+pillH/2+5*u); c.lineTo(px+62*u,py+pillH/2+13*u); c.stroke();
    if(t>3.5){ const sa=ramp(t,3.5,4.1)*(1-ramp(t,5.3,5.8)); c.globalAlpha=fade*sa; c.fillStyle="#fff"; c.shadowColor="rgba(60,40,20,.12)"; c.shadowBlur=20*u; c.beginPath(); c.roundRect(px,py+pillH+14*u,pillW,310*u,26*u); c.fill(); c.shadowBlur=0;
      sugg.forEach((sv,i)=>{ const a=easeOutB(ramp(t,3.6+i*.22,4.3+i*.22)), y=py+pillH+14*u+54*u+i*62*u; c.globalAlpha=fade*sa*a; if(i===2&&t>4.7){ c.fillStyle="#f2ede3"; c.fillRect(px+16*u,y-28*u,pillW-32*u,56*u); }
        tx(c,q,px+84*u,y,29*u,"#8a8174",{align:"left"}); const w0=c.measureText(q+" ").width; tx(c,sv,px+84*u+w0,y,29*u,"#14110f",{align:"left",weight:700}); }); }
    c.globalAlpha=fade;
    if(t<8.6){ const f=34*u; c.font=`400 ${f}px ${SANS}`; const tw=c.measureText(shown).width, caret=Math.floor(t*2.4)%2===0||t<1;
      tx(c,shown,px+84*u,py+pillH/2,f,"#14110f",{align:"left"}); if(caret){ c.fillStyle=EMBER; c.fillRect(px+86*u+tw,py+pillH/2-20*u,3*u,40*u); } }
    c.globalAlpha=1;
    if(t>=8.6){ const e=eio(ramp(t,8.6,10.4)), f=lerp(34,200,e)*u; c.font=`400 ${f}px ${SANS}`; const tw=c.measureText("Carmen").width, left=lerp(px+84*u,cx-tw/2,e), y=lerp(py+pillH/2,cy-10*u,e);
      tx(c,"Carmen",left,y,f,"#14110f",{align:"left",weight:lerp(400,700,e)}); }
    const a=easeOutB(ramp(t,10.6,11.8)); c.globalAlpha=a; tracked(c,"F O C U S",cx,cy+120*u,28*u,EMBER,14*u,{weight:700}); c.globalAlpha=1;
    grain(c,t,w,h,.04,100); } };

/* 34. Venetian blinds */
const blinds={ id:"blinds", title:"Slats", by:"Twenty-eight slats turn over", dur:12, still:9,
  blurb:"A wall of dark slats turns one after another to show the name painted across their faces.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    const face=textCanvas("blf",w,h,x=>{ x.fillStyle=PAPER; x.fillRect(0,0,w,h); x.fillStyle=INK; x.textAlign="center"; x.textBaseline="middle"; x.font=`700 ${250*u}px ${SANS}`; x.fillText("CARMEN",w/2,h/2-24*u);
      x.fillStyle=EMBER; x.font=`600 ${28*u}px ${SANS}`; x.letterSpacing=`${14*u}px`; x.textAlign="center"; x.fillText("F O C U S",w/2+7*u,h/2+150*u); });
    c.fillStyle="#1a1816"; c.fillRect(0,0,w,h);
    const N=28, sw=w/N;
    for(let i=0;i<N;i++){ const p=eio(ramp(t,.8+i*.085,2.6+i*.085)), ang=Math.PI*(1-p), cs=Math.cos(ang), wd=Math.abs(cs)*sw, x=i*sw+sw/2;
      if(cs>0){ c.drawImage(face,i*sw,0,sw,h,x-wd/2,0,wd+.6,h); c.fillStyle=`rgba(0,0,0,${(1-cs)*.55})`; c.fillRect(x-wd/2,0,wd+.6,h); }
      else { c.fillStyle=mix("#2a2623","#14110f",Math.abs(cs)); c.fillRect(x-wd/2,0,wd+.6,h); c.fillStyle="rgba(255,255,255,.05)"; c.fillRect(x-wd/2,0,Math.max(1,wd*.15),h); } }
    grain(c,t,w,h,.05,120); vig(c,w,h,.35); } };

/* 35. Resolve */
const resolve={ id:"resolve", title:"Resolve", by:"Pixels refine to a picture", dur:12, still:9,
  blurb:"The whole frame starts as a handful of enormous pixels, then doubles its resolution again and again until it is sharp.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    const scene=textCanvas("rsv",w,h,x=>{ const g=x.createLinearGradient(0,0,0,h); g.addColorStop(0,"#2a2a6e"); g.addColorStop(.55,"#c0457b"); g.addColorStop(1,"#ffae5c"); x.fillStyle=g; x.fillRect(0,0,w,h);
      x.fillStyle="#ffe8b0"; x.beginPath(); x.arc(cx,cy+170*u,118*u,0,7); x.fill(); x.fillStyle="#1b1230"; x.fillRect(0,cy+170*u,w,h);
      x.fillStyle="#fff4e0"; x.textAlign="center"; x.textBaseline="middle"; x.font=`italic 400 ${230*u}px ${SERIF}`; x.fillText("Carmen",cx,cy-64*u);
      x.font=`600 ${28*u}px ${SANS}`; x.letterSpacing=`${14*u}px`; x.fillStyle="#ffcf94"; x.fillText("F O C U S",cx+7*u,cy+260*u); });
    const sizes=[110,55,28,14,7,4,2,1], idx=Math.min(sizes.length-1,Math.floor(Math.max(0,t-.8)/.78)), s=Math.max(1,sizes[idx]*u*(t<.8?1.4:1));
    if(s<=1.2){ c.drawImage(scene,0,0); }
    else { const sw=Math.max(2,Math.ceil(w/s)), sh=Math.max(2,Math.ceil(h/s)); const tmp=sizedCache("rs"+sw,w,h,()=>{ const o=document.createElement("canvas"); o.width=sw; o.height=sh; return o; }), tc=tmp.getContext("2d");
      tc.imageSmoothingEnabled=true; tc.clearRect(0,0,sw,sh); tc.drawImage(scene,0,0,sw,sh); c.imageSmoothingEnabled=false; c.drawImage(tmp,0,0,sw,sh,0,0,sw*s,sh*s); c.imageSmoothingEnabled=true; }
    if(t<7.2){ c.fillStyle="rgba(255,255,255,.75)"; c.font=`500 ${15*u}px ${MONO}`; c.textAlign="left"; c.textBaseline="alphabetic"; c.fillText(`RESOLUTION ${Math.max(1,Math.round(w/Math.max(1,s)))} PX`,36*u,h-34*u); }
    grain(c,t,w,h,.04,100); } };

/* 36. Scan */
const scan={ id:"scan", title:"Scan", by:"A beam draws it, then fills it", dur:13, still:9.6,
  blurb:"A thin band of light descends and leaves an outline behind it. On the way back up it fills the letters in.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#06090b"; c.fillRect(0,0,w,h);
    c.strokeStyle="rgba(125,249,209,.07)"; c.lineWidth=1; const gs=40*u; for(let x=cx%gs;x<w;x+=gs){ c.beginPath(); c.moveTo(x,0); c.lineTo(x,h); c.stroke(); } for(let y=cy%gs;y<h;y+=gs){ c.beginPath(); c.moveTo(0,y); c.lineTo(w,y); c.stroke(); }
    const top=cy-170*u, bot=cy+170*u, down=eio(ramp(t,1,4.2)), up=eio(ramp(t,5,8)), by1=lerp(top,bot,down), by2=lerp(bot,top,up), beam=t<4.6?by1:by2;
    const font=`700 ${240*u}px ${SANS}`;
    c.save(); c.beginPath(); c.rect(0,0,w,by1); c.clip(); c.font=font; c.textAlign="center"; c.textBaseline="middle"; c.strokeStyle="#7df9d1"; c.lineWidth=2.2*u; c.shadowColor="rgba(125,249,209,.8)"; c.shadowBlur=12*u; c.strokeText("CARMEN",cx,cy-10*u); c.restore();
    if(t>5){ c.save(); c.beginPath(); c.rect(0,by2,w,h); c.clip(); c.font=font; c.textAlign="center"; c.textBaseline="middle"; c.fillStyle="#eafff8"; c.shadowColor="rgba(125,249,209,.5)"; c.shadowBlur=18*u; c.fillText("CARMEN",cx,cy-10*u); c.restore(); }
    const show=(t>1&&t<4.4)||(t>5&&t<8.2); if(show){ const g=c.createLinearGradient(0,beam-46*u,0,beam+46*u); g.addColorStop(0,"rgba(125,249,209,0)"); g.addColorStop(.5,"rgba(125,249,209,.35)"); g.addColorStop(1,"rgba(125,249,209,0)"); c.fillStyle=g; c.fillRect(0,beam-46*u,w,92*u);
      c.fillStyle="#d9fff3"; c.fillRect(cx-520*u,beam-1.2*u,1040*u,2.4*u); c.fillStyle="#7df9d1"; c.fillRect(cx-540*u,beam-9*u,18*u,18*u); c.fillRect(cx+522*u,beam-9*u,18*u,18*u); }
    const a=easeOutB(ramp(t,8.4,9.8)); c.globalAlpha=a; tracked(c,"F O C U S",cx,cy+200*u,24*u,"#7df9d1",14*u,{weight:600}); c.globalAlpha=1;
    c.fillStyle="rgba(0,0,0,.18)"; for(let y=0;y<h;y+=3) c.fillRect(0,y,w,1);
    grain(c,t,w,h,.05,100); vig(c,w,h,.55); } };

/* 37. Dot matrix */
const dotmatrix={ id:"dotmatrix", title:"LED sign", by:"A message scrolls in and stops", dur:12, still:9,
  blurb:"A wall of unlit bulbs. The name scrolls across it from the right, slows, and stays lit while the rest of the board breathes.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#0b0a08"; c.fillRect(0,0,w,h);
    const cols=88, sp=w/cols, rows=Math.ceil(h/sp), rad=sp*.34, oy=(h-rows*sp)/2;
    const mask=textCanvas("dmw",w,h,x=>{ x.fillStyle="#fff"; x.textAlign="center"; x.textBaseline="middle"; x.font=`700 ${245*u}px ${SANS}`; x.fillText("CARMEN",w/2,h/2-30*u);
      x.font=`700 ${62*u}px ${SANS}`; x.fillText("FOCUS",w/2,h/2+150*u); });
    const data=sizedCache("dmd",w,h,()=>mask.getContext("2d").getImageData(0,0,w,h).data);
    const shift=(1-easeOutB(ramp(t,.6,5.2)))*w*.95;
    for(let j=0;j<rows;j++) for(let i=0;i<cols;i++){ const x=i*sp+sp/2, y=oy+j*sp+sp/2, sx=Math.round(x-shift);
      let on=0; if(sx>=0&&sx<w&&y>=0&&y<h) on=data[((Math.floor(y))*w+sx)*4+3]/255;
      const wave=.5+.5*Math.sin(x*.012-t*2.2+y*.01), idle=.1+.05*wave;
      if(on>.5){ const g=c.createRadialGradient(x,y,0,x,y,rad*2.6); g.addColorStop(0,"rgba(255,176,0,.5)"); g.addColorStop(1,"rgba(255,176,0,0)"); c.fillStyle=g; c.beginPath(); c.arc(x,y,rad*2.6,0,7); c.fill(); c.fillStyle="#ffc233"; }
      else c.fillStyle=`rgba(255,176,0,${idle*.55})`;
      c.beginPath(); c.arc(x,y,rad,0,7); c.fill(); }
    const sweep=ramp(t,6,8.4); if(sweep>0&&sweep<1){ const x=sweep*(w+300*u)-150*u, g=c.createLinearGradient(x-120*u,0,x+120*u,0); g.addColorStop(0,"rgba(255,230,160,0)"); g.addColorStop(.5,"rgba(255,230,160,.22)"); g.addColorStop(1,"rgba(255,230,160,0)"); c.fillStyle=g; c.fillRect(0,0,w,h); }
    vig(c,w,h,.5); } };

/* 38. Big C */
const bigc={ id:"bigc", title:"Big C", by:"The first letter shrinks to the page", dur:11, still:8,
  blurb:"One enormous letter fills the frame, then retreats into its place in the word while the rest of the name slides out from behind it.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2, px=230*u;
    c.fillStyle=EMBER; c.fillRect(0,0,w,h);
    c.font=`700 ${px}px ${SANS}`; const wc=c.measureText("C").width, wr=c.measureText("armen").width, tot=wc+wr, left=cx-tot/2;
    const e=eio(ramp(t,1,3.6)), S=lerp(7.5,1,e), fx=lerp(cx,left+wc/2,e), fy=lerp(cy+90*u,cy-8*u,e);
    c.save(); c.translate(fx,fy); c.scale(S,S); c.fillStyle=INK; c.textAlign="center"; c.textBaseline="middle"; c.fillText("C",0,0); c.restore();
    c.font=`700 ${px}px ${SANS}`; c.textAlign="left"; c.textBaseline="middle"; let lx=left+wc;
    [..."armen"].forEach((ch,i)=>{ const q=easeOutB(ramp(t,3.3+i*.16,4.1+i*.16)); if(q<=0){ lx+=c.measureText(ch).width; return; } c.save(); c.beginPath(); c.rect(lx-2,cy-px*.7,c.measureText(ch).width+4,px*1.4); c.clip(); c.fillStyle=INK; c.fillText(ch,lx,cy-8*u+(1-q)*px*.9); c.restore(); lx+=c.measureText(ch).width; });
    const a=easeOutB(ramp(t,5.4,6.8)); c.globalAlpha=a; tracked(c,"F O C U S",cx,cy+150*u,30*u,"#fff1e6",16*u,{weight:700}); c.globalAlpha=1;
    grain(c,t,w,h,.07,200); } };

/* 39. Moon phases */
const moon={ id:"moon", title:"Waxing", by:"A moon fills out, light by light", dur:13, still:10,
  blurb:"A new moon in a still sky waxes to full in one slow move. Its light is what writes the name.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2-70*u, r=128*u;
    const sky=c.createLinearGradient(0,0,0,h); sky.addColorStop(0,"#060a1a"); sky.addColorStop(1,"#101a36"); c.fillStyle=sky; c.fillRect(0,0,w,h);
    const R=rng(8); for(let i=0;i<110;i++){ const a=.15+.5*R()*(.55+.45*Math.sin(t*(.6+R())+i)); c.fillStyle=`rgba(255,255,255,${a})`; c.fillRect(R()*w,R()*h*.85,(R()>.9?2:1.2)*u,(R()>.9?2:1.2)*u); }
    const p=eio(ramp(t,1.5,6.6)), halo=.15+.85*p;
    const hg=c.createRadialGradient(cx,cy,r*.8,cx,cy,r*(2.2+2*p)); hg.addColorStop(0,`rgba(214,224,255,${.28*halo})`); hg.addColorStop(1,"rgba(214,224,255,0)"); c.fillStyle=hg; c.fillRect(0,0,w,h);
    c.fillStyle="#1a2240"; c.beginPath(); c.arc(cx,cy,r,0,7); c.fill();
    c.save(); c.beginPath(); c.arc(cx,cy,r,-Math.PI/2,Math.PI/2); const k=Math.cos(Math.PI*p);
    for(let i=0;i<=48;i++){ const ph=Math.PI/2-i/48*Math.PI; c.lineTo(cx+r*k*Math.cos(ph),cy+r*Math.sin(ph)); } c.closePath(); c.fillStyle="#f6f0de"; c.fill(); c.clip();
    c.fillStyle="rgba(120,110,90,.16)"; [[-40,-30,26],[30,20,34],[-10,58,18],[48,-52,16],[-62,34,14]].forEach(([dx,dy,rr])=>{ c.beginPath(); c.arc(cx+dx*u,cy+dy*u,rr*u,0,7); c.fill(); }); c.restore();
    const a=easeOutB(ramp(t,6.6,8.4)); c.globalAlpha=a; c.shadowColor="rgba(214,224,255,.6)"; c.shadowBlur=24*u; tracked(c,"CARMEN",cx,cy+r+170*u+(1-a)*14*u,84*u,"#eef0ff",30*u,{weight:300}); c.shadowBlur=0;
    tracked(c,"F O C U S",cx,cy+r+250*u,22*u,"#9fb2ff",14*u,{weight:600}); c.globalAlpha=1;
    grain(c,t,w,h,.05,120); vig(c,w,h,.5); } };

/* 40. Gears */
function gearPath(c,x,y,r,n,rot){ const tooth=r*.15, ro=r+tooth/2, ri=r-tooth/2, P=2*Math.PI/n; c.beginPath();
  for(let i=0;i<n;i++){ const a=rot+i*P; [[ri,-.42],[ro,-.2],[ro,.2],[ri,.42]].forEach(([rr,k],j)=>{ const X=x+Math.cos(a+k*P)*rr, Y=y+Math.sin(a+k*P)*rr; (i===0&&j===0)?c.moveTo(X,Y):c.lineTo(X,Y); }); } c.closePath(); }
const gears={ id:"gears", title:"Movement", by:"A watch movement winds down", dur:13, still:10,
  blurb:"Brass gears mesh and spin up, slow with a last heavy click, and a band of light travels across the name they were driving.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    const bg=c.createRadialGradient(cx,cy,0,cx,cy,w*.7); bg.addColorStop(0,"#1d1a16"); bg.addColorStop(1,"#0b0a09"); c.fillStyle=bg; c.fillRect(0,0,w,h);
    const spin=(1-Math.pow(1-ramp(t,0,5.6),3))*28 + t*.12, shake=Math.max(0,1-Math.abs(t-5.7)*7)*3*u*Math.sin(t*90);
    const mesh=(ax,ay,ra,na,rotA,phi,rb,nb)=>{ const bx=ax+Math.cos(phi)*(ra+rb), by=ay+Math.sin(phi)*(ra+rb), Aph=(phi-rotA)*na/(2*Math.PI), rotB=phi+Math.PI-(.5-Aph)*2*Math.PI/nb; return [bx,by,rotB]; };
    const set=[]; const A=[cx-390*u,cy+120*u+shake,150*u,22]; const rA=spin; set.push([A[0],A[1],A[2],A[3],rA]);
    const [bx,by,rB]=mesh(A[0],A[1],A[2],A[3],rA,-.6,96*u,14); set.push([bx,by,96*u,14,rB]);
    const [dx,dy,rD]=mesh(bx,by,96*u,14,rB,-.2,70*u,10); set.push([dx,dy,70*u,10,rD]);
    const C=[cx+400*u,cy-110*u+shake,170*u,26]; const rC=-spin*22/26; set.push([C[0],C[1],C[2],C[3],rC]);
    const [ex,ey,rE]=mesh(C[0],C[1],C[2],C[3],rC,2.3,84*u,12); set.push([ex,ey,84*u,12,rE]);
    set.forEach(([x,y,r,n,rot],i)=>{ gearPath(c,x,y,r,n,rot); const g=c.createLinearGradient(x-r,y-r,x+r,y+r); g.addColorStop(0,"#e8c776"); g.addColorStop(.5,"#a9812f"); g.addColorStop(1,"#5c4416"); c.fillStyle=g; c.globalAlpha=lerp(.9,.28,ramp(t,5.9,7.2)); c.fill(); c.globalAlpha=1;
      c.strokeStyle="rgba(0,0,0,.4)"; c.lineWidth=1.6*u; c.stroke(); c.fillStyle="#17130d"; c.beginPath(); c.arc(x,y,r*.62,0,7); c.fill();
      c.strokeStyle="#c9a24a"; c.lineWidth=3*u; for(let s=0;s<4;s++){ const aa=rot+s*Math.PI/2; c.beginPath(); c.moveTo(x+Math.cos(aa)*r*.16,y+Math.sin(aa)*r*.16); c.lineTo(x+Math.cos(aa)*r*.6,y+Math.sin(aa)*r*.6); c.stroke(); }
      c.fillStyle="#e8c776"; c.beginPath(); c.arc(x,y,r*.12,0,7); c.fill(); });
    const a=easeOutB(ramp(t,5.9,7.2)); c.save(); c.globalAlpha=a; c.shadowColor="rgba(0,0,0,.8)"; c.shadowBlur=30*u;
    const tg=c.createLinearGradient(cx-300*u,0,cx+300*u,0); tg.addColorStop(0,"#d9b45e"); tg.addColorStop(.5,"#f9e7b3"); tg.addColorStop(1,"#c9a24a"); tx(c,"Carmen",cx,cy-12*u,230*u,tg,{font:SERIF,italic:true}); c.restore();
    const sh=ramp(t,7,9.4); if(sh>0&&sh<1){ const x=cx-520*u+sh*1040*u, g=c.createLinearGradient(x-90*u,0,x+90*u,0); g.addColorStop(0,"rgba(255,255,255,0)"); g.addColorStop(.5,"rgba(255,248,224,.5)"); g.addColorStop(1,"rgba(255,255,255,0)");
      c.save(); c.globalCompositeOperation="lighter"; c.fillStyle=g; c.fillRect(x-90*u,cy-140*u,180*u,240*u); c.restore(); }
    c.globalAlpha=easeOutB(ramp(t,8,9.4)); tracked(c,"F O C U S",cx,cy+135*u,26*u,"#c9a24a",16*u,{weight:600}); c.globalAlpha=1;
    grain(c,t,w,h,.06,140); vig(c,w,h,.55); } };

[aperture,metronome,gooey,flipboard,halftone,orbit,searchlight,curtain,marquee,assemble,contour,timecode,search,blinds,resolve,scan,dotmatrix,bigc,moon,gears].forEach(s=>SEQS.push(s));

"use strict";
/* Sixteen more openings. Each one starts from an idea taken from focus itself (a lens, a
   shadow, sand, a pendulum, a countdown), commits to one palette, and draws one frame as a
   pure function of time so it can be scrubbed and replayed. */

const grain=(c,t,w,h,a=.1,n=240)=>{ const R=rng(Math.floor(t*24)*131+7); for(let i=0;i<n;i++){ c.fillStyle=`rgba(255,255,255,${R()*a})`; c.fillRect(R()*w,R()*h,1.3,1.3); } };
const vig=(c,w,h,a=.6)=>{ const g=c.createRadialGradient(w/2,h/2,Math.min(w,h)*.35,w/2,h/2,Math.max(w,h)*.78); g.addColorStop(0,"rgba(0,0,0,0)"); g.addColorStop(1,`rgba(0,0,0,${a})`); c.fillStyle=g; c.fillRect(0,0,w,h); };
const U=(w,h)=>Math.min(w,h*1.6)/1000;
const mix=(a,b,t)=>{ const p=x=>[1,3,5].map(i=>parseInt(x.slice(i,i+2),16)); const A=p(a),B=p(b); return "#"+A.map((v,i)=>Math.round(lerp(v,B[i],t)).toString(16).padStart(2,"0")).join(""); };
const cache={};
function textCanvas(key,w,h,draw){ const k=key+w+"x"+h; if(!cache[k]){ const o=document.createElement("canvas"); o.width=w; o.height=h; draw(o.getContext("2d")); cache[k]=o; } return cache[k]; }
function textPoints(key,w,h,step,draw){ const k="p"+key+w+"x"+h+step; if(!cache[k]){ const o=textCanvas(key,w,h,draw); const d=o.getContext("2d").getImageData(0,0,w,h).data; const pts=[]; for(let y=0;y<h;y+=step) for(let x=0;x<w;x+=step) if(d[(y*w+x)*4+3]>140) pts.push([x,y]); cache[k]=pts; } return cache[k]; }
function lineP(c,x1,y1,x2,y2,p){ if(p<=0) return; c.beginPath(); c.moveTo(x1,y1); c.lineTo(lerp(x1,x2,p),lerp(y1,y2,p)); c.stroke(); }

/* 5. Pull focus: the title starts as soft light and a camera finds it */
const pull={ id:"pull", title:"Pull focus", by:"A lens finds the word", dur:12, still:8.6,
  blurb:"Warm out-of-focus lights, a blurred word, and an autofocus frame that locks on as the lens sharpens.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#0b0806"; c.fillRect(0,0,w,h);
    const R=rng(21), cols=["#ffb54a","#ff7a3d","#f4d58a","#2fb8b0","#ff9a5a"], fadeIn=eout(ramp(t,0,1.6));
    for(let i=0;i<46;i++){ const x=R()*w,y=R()*h,r=(26+R()*84)*u,col=cols[i%5],ph=R()*6,a=(.10+.26*R())*(.78+.22*Math.sin(t*.7+ph))*fadeIn;
      c.globalAlpha=a; c.fillStyle=col; c.beginPath(); c.arc(x+Math.sin(t*.2+ph)*20*u,y+Math.cos(t*.17+ph)*14*u,r,0,7); c.fill();
      c.globalAlpha=a*.9; c.strokeStyle=col; c.lineWidth=2*u; c.stroke(); }
    c.globalAlpha=1;
    const f=eio(ramp(t,2.0,6.4)), zoom=lerp(1.07,1,f);
    c.save(); c.translate(cx,cy); c.scale(zoom,zoom); c.filter=`blur(${(1-f)*46*u}px)`;
    c.fillStyle="#fff1dc"; c.textAlign="center"; c.textBaseline="middle"; c.font=`italic 400 ${230*u}px Georgia,"Palatino Linotype",serif`;
    c.globalAlpha=eout(ramp(t,1,3)); c.fillText("Carmen",0,-10*u); c.restore(); c.filter="none"; c.globalAlpha=1;
    // autofocus frame
    const k=ramp(t,5.2,6.6), lock=eoutQ(k), s=lerp(1.5,1,lock), hw=330*u*s, hh=130*u*s, L=34*u;
    if(k>0&&t<9.4){ const a=eout(ramp(t,5.2,5.8))*(1-ramp(t,8.6,9.4)); c.strokeStyle=`rgba(255,244,220,${a})`; c.lineWidth=3*u;
      for(const [sx,sy] of [[-1,-1],[1,-1],[1,1],[-1,1]]){ c.beginPath(); c.moveTo(cx+sx*hw,cy-10*u+sy*(hh-L)); c.lineTo(cx+sx*hw,cy-10*u+sy*hh); c.lineTo(cx+sx*(hw-L),cy-10*u+sy*hh); c.stroke(); } }
    const flash=ramp(t,6.4,6.6)*(1-ramp(t,6.6,7.6)); if(flash>0){ const g=c.createRadialGradient(cx,cy,0,cx,cy,520*u); g.addColorStop(0,`rgba(255,235,200,${.35*flash})`); g.addColorStop(1,"rgba(255,235,200,0)"); c.fillStyle=g; c.fillRect(0,0,w,h); }
    c.globalAlpha=eout(ramp(t,7,8.4)); c.fillStyle="#ffcf94"; c.font=`400 ${30*u}px Bahnschrift,"Segoe UI",sans-serif`; c.textAlign="center"; spaced(c,"F O C U S",cx,cy+190*u,14*u); c.globalAlpha=1;
    vig(c,w,h,.7); grain(c,t,w,h,.07); } };

/* 6. Ensō: one brush stroke */
function brushCircle(c,cx,cy,Rr,prog,u){ const R=rng(9), a0=-2.35, N=64;
  for(let k=0;k<N;k++){ const off=(k/(N-1)-.5)*2, lim=Math.PI*2*.94*(.84+.16*R())*(1-.14*Math.abs(off)), reach=Math.min(prog*Math.PI*2*.96,lim), n=Math.max(2,Math.floor(reach/.035)), shade=.55+.4*R();
    for(let i=0;i<n;i++){ const th=a0+i*.035, g=i*.035/lim, wd=50*u*(1-.62*g), rr=Rr+off*wd+(R()-.5)*1.4*u;
      c.strokeStyle=`rgba(20,17,15,${shade*(1-.9*Math.pow(g,2.6))})`; c.lineWidth=3.4*u; c.beginPath(); c.arc(cx,cy,rr,th,th+.05); c.stroke(); } } }
const enso={ id:"enso", title:"Ensō", by:"One stroke, no second chance", dur:12, still:10.4,
  blurb:"Ink on washi paper. A brush draws a single circle, runs dry, and the name settles inside with a red seal.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#ece5d4"; c.fillRect(0,0,w,h); const R=rng(4); for(let i=0;i<900;i++){ c.fillStyle=`rgba(120,100,70,${R()*.07})`; c.fillRect(R()*w,R()*h,R()*2.4,R()*1.2); }
    for(let i=0;i<40;i++){ const x=R()*w,y=R()*h,a=R()*6.28; c.strokeStyle="rgba(150,130,95,.10)"; c.lineWidth=1; c.beginPath(); c.moveTo(x,y); c.lineTo(x+Math.cos(a)*40*u,y+Math.sin(a)*40*u); c.stroke(); }
    brushCircle(c,cx,cy-8*u,210*u,eio(ramp(t,.8,4.8)),u);
    const a=eout(ramp(t,5.4,7.2)); c.globalAlpha=a; c.fillStyle="#14110f"; c.textAlign="center"; c.textBaseline="middle"; c.font=`400 ${62*u}px Georgia,"Yu Mincho",serif`; spaced(c,"CARMEN",cx,cy-14*u,18*u);
    c.globalAlpha=eout(ramp(t,6.4,8)); c.fillStyle="#5b5348"; c.font=`400 ${22*u}px Georgia,serif`; spaced(c,"f o c u s",cx,cy+36*u,8*u); c.globalAlpha=1;
    const sp=ramp(t,8.4,9.0); if(sp>0){ const sc=lerp(1.35,1,eoutQ(sp)); c.save(); c.translate(cx+250*u,cy+190*u); c.rotate(-.05); c.scale(sc,sc); c.globalAlpha=eout(sp);
      c.fillStyle="#bf3a2b"; c.fillRect(-42*u,-42*u,84*u,84*u); c.fillStyle="#ece5d4"; c.font=`700 ${56*u}px "Yu Mincho","MS Mincho","SimSun",serif`; c.textAlign="center"; c.textBaseline="middle"; c.fillText("集中",0,2*u); c.restore(); }
    const g=c.createRadialGradient(cx,cy,Math.min(w,h)*.4,cx,cy,Math.max(w,h)*.8); g.addColorStop(0,"rgba(80,60,30,0)"); g.addColorStop(1,"rgba(80,60,30,.22)"); c.fillStyle=g; c.fillRect(0,0,w,h); } };

/* 7. Sundial: one light, one shadow, letters carved in stone */
const sundial={ id:"sundial", title:"Sundial", by:"A shadow tells the hour", dur:13, still:10.5,
  blurb:"A single light moves across a stone dial. As the shadow turns, the carved name lifts out of the stone.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2-34*u;
    const bg=c.createLinearGradient(0,0,0,h); bg.addColorStop(0,"#e6d8bb"); bg.addColorStop(1,"#c9b791"); c.fillStyle=bg; c.fillRect(0,0,w,h);
    const R=rng(3); for(let i=0;i<1400;i++){ c.fillStyle=`rgba(90,70,40,${R()*.06})`; c.fillRect(R()*w,R()*h,1.5,1.5); }
    const sweep=eio(ramp(t,.8,10)), th=lerp(-2.55,-.6,sweep), r0=200*u; // shadow angle
    c.fillStyle="#d6c6a2"; c.strokeStyle="#9c8760"; c.lineWidth=4*u; c.beginPath(); c.arc(cx,cy,r0,0,7); c.fill(); c.stroke();
    c.lineWidth=1.5*u; c.beginPath(); c.arc(cx,cy,r0-26*u,0,7); c.stroke();
    const num=["XII","I","II","III","IIII","V","VI","VII","VIII","IX","X","XI"]; c.fillStyle="#5c4a2e"; c.font=`600 ${23*u}px Georgia,serif`; c.textAlign="center"; c.textBaseline="middle";
    for(let i=0;i<12;i++){ const a=i/12*Math.PI*2-Math.PI/2, on=ramp(t,.2+i*.07,.8+i*.07); c.globalAlpha=on; c.fillText(num[i],cx+Math.cos(a)*(r0-13*u),cy+Math.sin(a)*(r0-13*u)); } c.globalAlpha=1;
    c.save(); c.beginPath(); c.arc(cx,cy,r0-2*u,0,7); c.clip(); c.fillStyle="rgba(45,30,12,.38)"; c.filter=`blur(${3*u}px)`;
    c.beginPath(); c.moveTo(cx,cy); c.lineTo(cx+Math.cos(th-.05)*r0*1.4,cy+Math.sin(th-.05)*r0*1.4); c.lineTo(cx+Math.cos(th+.05)*r0*1.4,cy+Math.sin(th+.05)*r0*1.4); c.closePath(); c.fill(); c.restore(); c.filter="none";
    c.fillStyle="#3b2c18"; c.beginPath(); c.arc(cx,cy,9*u,0,7); c.fill();
    // carved name lit from the same light
    const lx=-Math.cos(th), ly=-Math.sin(th), reveal=eio(ramp(t,3,9)), off=4.2*u*reveal;
    c.font=`700 ${96*u}px Georgia,serif`; c.textBaseline="middle"; const y=cy+r0+88*u, track=16*u, base="#c3b189";
    c.globalAlpha=1; c.fillStyle=`rgba(255,250,235,${.9*reveal})`; spaced(c,"CARMEN",cx+lx*off,y+ly*off,track);
    c.fillStyle=`rgba(60,42,18,${.55*reveal})`; spaced(c,"CARMEN",cx-lx*off,y-ly*off,track);
    c.fillStyle=base; spaced(c,"CARMEN",cx,y,track);
    c.globalAlpha=eout(ramp(t,10,11.6)); c.fillStyle="#6b5a3c"; c.font=`500 ${22*u}px Georgia,serif`; spaced(c,"F O C U S",cx,y+66*u,12*u); c.globalAlpha=1;
    const tipX=cx+Math.cos(th+Math.PI)*0, g=ramp(t,10.2,11)*(1-ramp(t,11.2,12.4)); if(g>0){ const gx=cx+lx*(r0*.78), gy=cy+ly*(r0*.78); const rg=c.createRadialGradient(gx,gy,0,gx,gy,90*u); rg.addColorStop(0,`rgba(255,245,215,${.8*g})`); rg.addColorStop(1,"rgba(255,245,215,0)"); c.fillStyle=rg; c.fillRect(gx-90*u,gy-90*u,180*u,180*u); }
    const sh=c.createRadialGradient(cx,cy,Math.min(w,h)*.4,cx,cy,Math.max(w,h)*.8); sh.addColorStop(0,"rgba(60,40,10,0)"); sh.addColorStop(1,"rgba(60,40,10,.28)"); c.fillStyle=sh; c.fillRect(0,0,w,h); } };

/* 8. Sand: grains fall into the word */
const sand={ id:"sand", title:"Sand", by:"Time becomes the word", dur:13, still:9.6,
  blurb:"A single stream of golden sand pours from above and piles up into letters, bottom first.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2, W=Math.round(w), H=Math.round(h);
    const bg=c.createLinearGradient(0,0,0,h); bg.addColorStop(0,"#0c0a08"); bg.addColorStop(1,"#1b1510"); c.fillStyle=bg; c.fillRect(0,0,w,h);
    const step=Math.max(4,Math.round(5.6*u)), pts=textPoints("sand",W,H,step,o=>{ o.fillStyle="#000"; o.font=`900 ${230*u}px "Arial Black",Impact,sans-serif`; o.textAlign="center"; o.textBaseline="middle"; o.fillText("CARMEN",W/2,H/2-30*u); });
    let ymin=1e9,ymax=-1e9; for(const p of pts){ ymin=Math.min(ymin,p[1]); ymax=Math.max(ymax,p[1]); }
    const cols=["#e3b766","#c9953f","#f2d79b","#b9822f"]; const sz=Math.max(2.2,step*.62);
    let falling=0;
    pts.forEach((p,i)=>{ const R=rng(i*7919+3), key=1-(p[1]-ymin)/(ymax-ymin||1), launch=lerp(.6,8.4,Math.min(1,key*.82+R()*.18)), T=1.0+R()*.6, q=(t-launch)/T;
      if(q<=0) return; const k=clamp(q), sx=cx+(R()-.5)*16*u, x=lerp(sx,p[0],eout(k)), y=lerp(-30*u,p[1],k*k);
      if(q<1) falling++; const shimmer=q>=1?(.85+.15*Math.sin(t*3+i)):1; c.globalAlpha=shimmer; c.fillStyle=cols[i&3]; c.fillRect(x,y,sz,sz); });
    c.globalAlpha=1;
    if(falling>0){ c.strokeStyle="rgba(235,200,130,.25)"; c.lineWidth=2*u; c.beginPath(); c.moveTo(cx,0); c.lineTo(cx,cy-240*u); c.stroke(); }
    c.globalAlpha=eout(ramp(t,9.6,11.2)); c.fillStyle="#d9b574"; c.font=`400 ${28*u}px Bahnschrift,"Segoe UI",sans-serif`; c.textAlign="center"; spaced(c,"F O C U S",cx,cy+150*u,16*u); c.globalAlpha=1;
    vig(c,w,h,.6); } };

/* 9. Typewriter: a short letter to distraction */
const typewriter={ id:"typewriter", title:"Dear Distraction", by:"A letter, typed", dur:13, still:10.6,
  blurb:"A letter is typed out one key at a time, the first line is struck through, and a red stamp ends it.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#2e2620"; c.fillRect(0,0,w,h); const R=rng(2); for(let i=0;i<70;i++){ c.strokeStyle=`rgba(255,235,200,${.02+R()*.03})`; c.lineWidth=1+R()*2; const y=R()*h; c.beginPath(); c.moveTo(0,y); c.bezierCurveTo(w*.3,y+(R()-.5)*30,w*.7,y+(R()-.5)*30,w,y+(R()-.5)*20); c.stroke(); }
    c.save(); c.translate(cx,cy); c.rotate(-.018); c.shadowColor="rgba(0,0,0,.5)"; c.shadowBlur=40*u; c.shadowOffsetY=14*u; c.fillStyle="#f6f0e0"; const pw=700*u,ph=560*u; c.fillRect(-pw/2,-ph/2,pw,ph); c.shadowColor="transparent";
    const lines=[["Dear Distraction,",.7,2.1,true],["we are no longer",3.3,4.4],["on speaking terms.",4.6,5.9],["— Carmen",6.5,7.4,false,true]];
    c.font=`700 ${35*u}px "Courier New",Courier,monospace`; c.textBaseline="alphabetic"; const left=-pw/2+64*u; let y0=-ph/2+110*u;
    lines.forEach(([txt,a,b,strike,right],li)=>{ const n=Math.floor(clamp((t-a)/(b-a))*txt.length), y=y0+li*70*u, rr=rng(li*31+5);
      let x=right?pw/2-64*u-c.measureText(txt).width:left; for(let i=0;i<n;i++){ const ch=txt[i], cw=c.measureText(ch).width; c.fillStyle=`rgba(28,24,22,${.78+rr()*.2})`; c.fillText(ch,x+(rr()-.5)*1.2*u,y+(rr()-.5)*2*u); x+=cw; }
      if(n<txt.length&&n>0&&t>a&&Math.floor(t*6)%2===0){ c.fillStyle="rgba(28,24,22,.5)"; c.fillRect(x,y-30*u,18*u,34*u); }
      if(strike){ const s=ramp(t,2.4,2.9); if(s>0){ c.strokeStyle="#b3261e"; c.lineWidth=4*u; c.beginPath(); c.moveTo(left-8*u,y-10*u); c.lineTo(left-8*u+(c.measureText(txt).width+16*u)*eio(s),y-10*u+ (s>.5?-2*u:0)); c.stroke(); } } });
    const sp=ramp(t,8.4,9.0); if(sp>0){ const sc=lerp(1.7,1,eoutQ(sp)); c.save(); c.translate(60*u,150*u); c.rotate(-.1); c.scale(sc,sc); c.globalAlpha=eout(sp)*.92; c.strokeStyle="#b3261e"; c.fillStyle="#b3261e"; c.lineWidth=6*u;
      c.strokeRect(-250*u,-62*u,500*u,124*u); c.lineWidth=2.5*u; c.strokeRect(-240*u,-52*u,480*u,104*u); c.font=`900 ${72*u}px "Arial Black",Impact,sans-serif`; c.textAlign="center"; c.textBaseline="middle"; c.fillText("CARMEN FOCUS",0,4*u); c.restore(); }
    c.restore(); vig(c,w,h,.55); } };

/* 10. Blueprint: drawn line by line */
const blueprint={ id:"blueprint", title:"Blueprint", by:"Drafted, dimensioned, signed", dur:13, still:10.4,
  blurb:"A cyanotype sheet. A timer ring is drafted, measured and labelled, then the title block fills in.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2-70*u, cy=h/2-10*u;
    c.fillStyle="#0f3c73"; c.fillRect(0,0,w,h);
    c.lineWidth=1; for(let x=0;x<w;x+=20*u){ c.strokeStyle=(Math.round(x/(20*u))%5===0)?"rgba(190,225,255,.16)":"rgba(190,225,255,.06)"; c.beginPath(); c.moveTo(x,0); c.lineTo(x,h); c.stroke(); }
    for(let y=0;y<h;y+=20*u){ c.strokeStyle=(Math.round(y/(20*u))%5===0)?"rgba(190,225,255,.16)":"rgba(190,225,255,.06)"; c.beginPath(); c.moveTo(0,y); c.lineTo(w,y); c.stroke(); }
    c.strokeStyle="#e4f3ff"; c.fillStyle="#e4f3ff"; c.lineWidth=2.2*u; c.lineCap="round";
    lineP(c,cx-240*u,cy,cx+240*u,cy,eio(ramp(t,.8,2))); lineP(c,cx,cy-240*u,cx,cy+240*u,eio(ramp(t,1,2.2)));
    const rp=eio(ramp(t,1.2,4)); c.beginPath(); c.arc(cx,cy,170*u,-Math.PI/2,-Math.PI/2+Math.PI*2*rp); c.stroke();
    c.save(); c.setLineDash([10*u,8*u]); c.lineWidth=1.5*u; c.beginPath(); c.arc(cx,cy,148*u,-Math.PI/2,-Math.PI/2+Math.PI*2*eio(ramp(t,2.2,4.4))); c.stroke(); c.restore();
    for(let i=0;i<60;i++){ const on=ramp(t,3+i*.02,3.3+i*.02); if(!on) continue; const a=i/60*Math.PI*2-Math.PI/2, r1=170*u, r2=r1-(i%5===0?18:8)*u*on; c.lineWidth=(i%5?1.2:2.2)*u; c.beginPath(); c.moveTo(cx+Math.cos(a)*r1,cy+Math.sin(a)*r1); c.lineTo(cx+Math.cos(a)*r2,cy+Math.sin(a)*r2); c.stroke(); }
    c.lineWidth=2.6*u; lineP(c,cx,cy,cx+Math.cos(-1.05)*130*u,cy+Math.sin(-1.05)*130*u,eio(ramp(t,4.4,5.2)));
    c.font=`400 ${19*u}px Bahnschrift,"Segoe UI",sans-serif`; c.textAlign="center"; c.textBaseline="middle"; c.lineWidth=1.5*u;
    const dp=eio(ramp(t,5,6.2)), yb=cy+218*u; lineP(c,cx-170*u,yb,cx+170*u,yb,dp); for(const s of [-1,1]){ lineP(c,cx+s*170*u,cy+176*u,cx+s*170*u,yb+14*u,dp); }
    if(dp>.9){ c.fillStyle="#0f3c73"; c.fillRect(cx-44*u,yb-12*u,88*u,24*u); c.fillStyle="#e4f3ff"; c.fillText("Ø 340",cx,yb); }
    const lp=eio(ramp(t,5.8,7)); lineP(c,cx+120*u,cy-120*u,cx+215*u,cy-205*u,lp); lineP(c,cx+215*u,cy-205*u,cx+330*u,cy-205*u,lp);
    c.globalAlpha=ramp(t,6.6,7.4); c.textAlign="left"; c.fillText("A   25 MIN RING",cx+222*u,cy-222*u); c.globalAlpha=1;
    // title block
    const bx=cx+215*u, by=cy+60*u, bw=290*u, bh=170*u, bp=eio(ramp(t,7,8.4));
    if(bp>0){ c.lineWidth=2.4*u; c.beginPath(); c.moveTo(bx,by); c.lineTo(bx+bw*bp,by); c.stroke(); lineP(c,bx+bw,by,bx+bw,by+bh,ramp(bp,.3,1)); lineP(c,bx+bw,by+bh,bx,by+bh,ramp(bp,.5,1)); lineP(c,bx,by+bh,bx,by,ramp(bp,.7,1)); lineP(c,bx,by+70*u,bx+bw,by+70*u,ramp(bp,.8,1)); lineP(c,bx+bw*.5,by+70*u,bx+bw*.5,by+bh,ramp(bp,.8,1)); }
    c.globalAlpha=eout(ramp(t,8.2,9.4)); c.textAlign="left"; c.font=`700 ${34*u}px Bahnschrift,"Segoe UI",sans-serif`; c.fillText("CARMEN FOCUS",bx+16*u,by+36*u);
    c.font=`400 ${15*u}px Bahnschrift,"Segoe UI",sans-serif`; c.fillText("DRAWN BY   YOU",bx+16*u,by+100*u); c.fillText("SCALE   1:1",bx+bw*.5+14*u,by+100*u); c.fillText("SHEET   1 / 1",bx+16*u,by+142*u); c.fillText("REV   A",bx+bw*.5+14*u,by+142*u); c.globalAlpha=1;
    vig(c,w,h,.32); } };

/* 11. Swiss grid */
const swiss={ id:"swiss", title:"Swiss", by:"Grid, circle, one typeface", dur:12, still:9.2,
  blurb:"International Typographic Style: a visible grid, flush-left bold type, and one red circle that sweeps like a timer.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2, L=cx-440*u;
    c.fillStyle="#f1eee6"; c.fillRect(0,0,w,h);
    c.strokeStyle="rgba(10,10,10,.14)"; c.lineWidth=1; for(let i=0;i<=6;i++){ const x=L+i*146*u, p=eio(ramp(t,.2+i*.1,1.2+i*.1)); lineP(c,x,0,x,h*p,1); }
    const rp=eoutQ(ramp(t,1.2,2.6)), R0=150*u, ccx=L+4.5*146*u, ccy=cy-150*u;
    c.fillStyle="#e2231a"; c.beginPath(); c.arc(ccx,ccy,R0*rp,0,7); c.fill();
    const sw=eio(ramp(t,2.6,6)); c.fillStyle="#f1eee6"; c.strokeStyle="#f1eee6"; c.lineWidth=7*u; c.lineCap="butt"; c.beginPath(); c.moveTo(ccx,ccy); c.lineTo(ccx+Math.cos(-Math.PI/2+sw*Math.PI*2*.42)*R0*.86,ccy+Math.sin(-Math.PI/2+sw*Math.PI*2*.42)*R0*.86); c.stroke();
    c.beginPath(); c.arc(ccx,ccy,10*u,0,7); c.fill();
    c.font=`700 ${236*u}px Arial,Helvetica,sans-serif`; c.fillStyle="#0a0a0a"; c.textBaseline="alphabetic"; c.textAlign="left"; c.letterSpacing=`${-9*u}px`;
    const lines=[["Carmen",cy+60*u,2.2],["Focus",cy+250*u,2.7]];
    lines.forEach(([txt,y,s])=>{ const k=eoutQ(ramp(t,s,s+1.4)); c.save(); c.beginPath(); c.rect(L-10*u,y-200*u,w,215*u); c.clip(); c.fillText(txt,L-6*u,y+(1-k)*230*u); c.restore(); });
    c.letterSpacing="0px"; c.font=`400 ${23*u}px Arial,Helvetica,sans-serif`; c.fillStyle="#0a0a0a";
    [["25 min",0],["one task",1],["2026",2]].forEach(([s,i])=>{ c.globalAlpha=eout(ramp(t,4.8+i*.3,5.6+i*.3)); c.fillText(s,L+(i*2)*146*u+4*u,cy-215*u); }); c.globalAlpha=1;
    c.fillStyle="#0a0a0a"; c.fillRect(L,cy+290*u,(6*146*u)*eio(ramp(t,5.6,7.6)),6*u); } };

/* 12. Neon */
const neon={ id:"neon", title:"Neon", by:"A sign switches on", dur:12, still:9,
  blurb:"A glass-tube sign stutters to life against a brick wall. The wall lights up around it.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#0c1517"; c.fillRect(0,0,w,h); const R=rng(8), bh=34*u, bw=96*u;
    for(let y=0,row=0;y<h;y+=bh,row++) for(let x=-(row%2)*bw/2;x<w;x+=bw){ c.fillStyle=`rgb(${22+R()*10},${40+R()*12},${44+R()*12})`; c.fillRect(x+2,y+2,bw-4,bh-4); }
    const sched=[[1.0,1.08],[1.22,1.3],[1.55,1.9],[2.0,2.06],[2.4,99]]; let on=0; for(const s of sched) if(t>=s[0]&&t<s[1]) on=1; if(t>6.3&&t<6.36) on=0; if(t>9.4&&t<9.45) on=.4;
    const wall=c.createRadialGradient(cx,cy,0,cx,cy,Math.max(w,h)*.7); wall.addColorStop(0,`rgba(255,140,60,${.34*on})`); wall.addColorStop(.5,`rgba(255,100,40,${.10*on})`); wall.addColorStop(1,"rgba(0,0,0,0)"); c.fillStyle=wall; c.fillRect(0,0,w,h);
    c.textAlign="center"; c.textBaseline="middle"; c.lineJoin="round"; c.font=`700 ${190*u}px "Segoe Script","Brush Script MT",cursive`;
    c.strokeStyle="#3a2d29"; c.lineWidth=8*u; c.strokeText("Carmen",cx,cy-30*u);
    if(on>0){ c.save(); c.globalAlpha=on; c.shadowColor="#ff7a2f"; c.shadowBlur=60*u; c.strokeStyle="#ff8a3d"; c.lineWidth=12*u; c.strokeText("Carmen",cx,cy-30*u); c.shadowBlur=18*u; c.strokeStyle="#ffb273"; c.lineWidth=7*u; c.strokeText("Carmen",cx,cy-30*u); c.shadowBlur=0; c.strokeStyle="#fff3e4"; c.lineWidth=3.2*u; c.strokeText("Carmen",cx,cy-30*u); c.restore(); }
    const f2=t>3.6&&(t>4.2||Math.floor(t*14)%2===0)?1:0; c.font=`300 ${52*u}px Bahnschrift,"Segoe UI",sans-serif`; c.letterSpacing=`${26*u}px`;
    c.strokeStyle="#2a3438"; c.lineWidth=5*u; c.strokeText("FOCUS",cx+13*u,cy+150*u);
    if(f2){ c.save(); c.shadowColor="#7fd3ff"; c.shadowBlur=34*u; c.strokeStyle="#8fd8ff"; c.lineWidth=5*u; c.strokeText("FOCUS",cx+13*u,cy+150*u); c.shadowBlur=0; c.strokeStyle="#f2fbff"; c.lineWidth=2*u; c.strokeText("FOCUS",cx+13*u,cy+150*u); c.restore(); }
    c.letterSpacing="0px"; vig(c,w,h,.62); grain(c,t,w,h,.05); } };

/* 13. Ripples */
const ripples={ id:"ripples", title:"Ripples", by:"Calm after the drops", dur:13, still:11,
  blurb:"Drops fall on dark water. The surface settles and the name is read as a reflection that slowly steadies.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2, W=Math.round(w), H=Math.round(h);
    const bg=c.createLinearGradient(0,0,0,h); bg.addColorStop(0,"#091216"); bg.addColorStop(1,"#14262b"); c.fillStyle=bg; c.fillRect(0,0,w,h);
    const mg=c.createRadialGradient(cx+240*u,cy-190*u,0,cx+240*u,cy-190*u,260*u); mg.addColorStop(0,"rgba(220,240,235,.20)"); mg.addColorStop(1,"rgba(220,240,235,0)"); c.fillStyle=mg; c.fillRect(0,0,w,h);
    const drops=[[.8,.32,.45],[2.2,.62,.55],[3.4,.45,.62],[4.6,.26,.5],[5.5,.7,.6],[6.3,.5,.5]]; let energy=0;
    for(const [t0,fx,fy] of drops){ const a=t-t0; if(a<0) continue; const x=w*fx, y=h*fy;
      for(let k=0;k<4;k++){ const r=(a*150-k*34)*u; if(r<=0) continue; const al=Math.max(0,1-r/(420*u))*Math.exp(-a*.32)*.9; c.strokeStyle=`rgba(214,236,230,${al*.7})`; c.lineWidth=(1.6-k*.2)*u; c.beginPath(); c.ellipse(x,y,r,r*.4,0,0,7); c.stroke(); }
      energy+=Math.exp(-a*.55)*ramp(a,0,.3); }
    const amp=Math.min(1,energy*.75+(t<2?0:0))*14*u*(1-ramp(t,9,11.5)), show=eout(ramp(t,3.2,6.5));
    const off=textCanvas("ripple",W,H,o=>{ o.fillStyle="#e9f5f0"; o.font=`300 ${176*u}px Bahnschrift,"Segoe UI Light",sans-serif`; o.textAlign="center"; o.textBaseline="middle"; o.letterSpacing=`${30*u}px`; o.fillText("CARMEN",W/2,H/2-10*u); });
    c.globalAlpha=show*.92; for(let y=0;y<h;y+=3){ const dx=Math.sin(y*.045+t*2.6)*amp+Math.sin(y*.11-t*1.7)*amp*.4; c.drawImage(off,0,y,w,3,dx,y,w,3); } c.globalAlpha=1;
    c.globalAlpha=eout(ramp(t,10,11.6)); c.fillStyle="#9fc5bc"; c.font=`400 ${26*u}px Bahnschrift,"Segoe UI",sans-serif`; c.textAlign="center"; spaced(c,"F O C U S",cx,cy+130*u,16*u); c.globalAlpha=1;
    vig(c,w,h,.55); grain(c,t,w,h,.04,120); } };

/* 14. Film leader */
const leader={ id:"leader", title:"Leader", by:"Five, four, three, two", dur:13, still:3.4,
  blurb:"The countdown at the head of a film reel: sweep hand, crosshair, jitter and dust, then a black frame and the card.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2, R=rng(Math.floor(t*24)*17+3);
    const card=t>=6.6;
    if(!card){ const jx=(R()-.5)*5*u, jy=(R()-.5)*4*u; c.save(); c.translate(jx,jy);
      c.fillStyle="#c9c4ae"; c.fillRect(-20,-20,w+40,h+40);
      const n=Math.min(5,5-Math.floor(clamp(t-1,0,4.99))), within=clamp(t-1)%1, black=t>=6;
      if(black){ c.fillStyle="#000"; c.fillRect(-20,-20,w+40,h+40); if(t<6.12){ c.fillStyle="rgba(255,255,255,.9)"; c.fillRect(0,0,w,h); } }
      else { c.strokeStyle="#1c1b18"; c.lineWidth=3*u; c.beginPath(); c.moveTo(0,cy); c.lineTo(w,cy); c.moveTo(cx,0); c.lineTo(cx,h); c.stroke();
        for(const r of [300,262]){ c.beginPath(); c.arc(cx,cy,r*u,0,7); c.stroke(); }
        const a0=-Math.PI/2; c.fillStyle="rgba(20,20,16,.20)"; c.beginPath(); c.moveTo(cx,cy); c.arc(cx,cy,262*u,a0,a0+((t<1)?0:within*Math.PI*2)); c.closePath(); c.fill();
        c.strokeStyle="#1c1b18"; c.lineWidth=4*u; c.beginPath(); c.moveTo(cx,cy); c.lineTo(cx+Math.cos(a0+within*Math.PI*2)*262*u,cy+Math.sin(a0+within*Math.PI*2)*262*u); c.stroke();
        if(t>=1){ c.fillStyle="#1c1b18"; c.font=`700 ${340*u}px Arial,Helvetica,sans-serif`; c.textAlign="center"; c.textBaseline="middle"; c.fillText(String(n),cx,cy+8*u); }
        else { c.fillStyle="#1c1b18"; c.font=`700 ${34*u}px Arial,sans-serif`; c.textAlign="center"; spaced(c,"PICTURE START",cx,cy+8*u,10*u); }
        if(t>=1&&t<1.5){ } }
      c.restore();
      c.strokeStyle="rgba(20,20,16,.5)"; c.lineWidth=1; for(let i=0;i<2;i++) if(R()<.5){ const x=R()*w; c.beginPath(); c.moveTo(x,0); c.lineTo(x+(R()-.5)*8,h); c.stroke(); }
      for(let i=0;i<300;i++){ c.fillStyle=`rgba(${R()<.5?"0,0,0":"255,255,255"},${R()*.3})`; c.fillRect(R()*w,R()*h,1+R()*2,1+R()*2); }
      vig(c,w,h,.6); }
    else { c.fillStyle="#000"; c.fillRect(0,0,w,h); const a=eout(ramp(t,6.9,8.2))*(1-ramp(t,12.2,13)); const fl=.9+.1*R();
      c.globalAlpha=a*fl; c.fillStyle="#efe6cf"; c.textAlign="center"; c.textBaseline="middle"; c.font=`400 ${118*u}px "Palatino Linotype",Palatino,Georgia,serif`; spaced(c,"CARMEN FOCUS",cx,cy-6*u,6*u);
      c.fillStyle="#b9ae93"; c.font=`400 ${26*u}px "Palatino Linotype",Georgia,serif`; c.globalAlpha=a*fl*ramp(t,8,9.4); spaced(c,"A   W O R K   S E S S I O N",cx,cy+86*u,6*u); c.globalAlpha=1;
      c.fillStyle="rgba(239,230,207,.7)"; c.fillRect(cx-150*u,cy+38*u,300*u*eio(ramp(t,7.6,9)),1.5*u);
      for(let i=0;i<160;i++){ c.fillStyle=`rgba(255,255,255,${R()*.12})`; c.fillRect(R()*w,R()*h,1.3,1.3); } vig(c,w,h,.7); } } };

/* 15. Constellation */
const constellation={ id:"constellation", title:"Constellation", by:"Stars find their places", dur:13, still:10,
  blurb:"Stars drift in from across the sky, land on the points of the letters, and are joined into a constellation.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2, W=Math.round(w), H=Math.round(h);
    const bg=c.createLinearGradient(0,0,0,h); bg.addColorStop(0,"#050816"); bg.addColorStop(1,"#101a38"); c.fillStyle=bg; c.fillRect(0,0,w,h);
    const R=rng(14); for(let i=0;i<170;i++){ const x=R()*w,y=R()*h,s=.5+R()*1.4,ph=R()*6; c.fillStyle=`rgba(255,244,214,${.2+.3*Math.sin(t*1.1+ph)*Math.sin(t*1.1+ph)})`; c.beginPath(); c.arc(x,y,s*u,0,7); c.fill(); }
    c.strokeStyle="rgba(233,196,106,.12)"; c.lineWidth=1.2*u; c.beginPath(); c.ellipse(cx,cy+360*u,760*u,300*u,0,Math.PI*1.1,Math.PI*1.9); c.stroke();
    const step=Math.max(8,Math.round(15*u)), pts=textPoints("constel",W,H,step,o=>{ o.fillStyle="#000"; o.font=`800 ${210*u}px Bahnschrift,"Arial Black",sans-serif`; o.textAlign="center"; o.textBaseline="middle"; o.fillText("CARMEN",W/2,H/2-20*u); });
    const pos=pts.map((p,i)=>{ const Rr=rng(i*131+9), arrive=lerp(.8,5,Rr()), k=eio(ramp(t,arrive-1.8,arrive)); const sx=Rr()*w, sy=Rr()*h; return [lerp(sx,p[0],k),lerp(sy,p[1],k),k,Rr()]; });
    const la=ramp(t,5.2,7.4); if(la>0){ c.strokeStyle=`rgba(233,196,106,${.55*la})`; c.lineWidth=1.3*u; for(let i=0;i<pos.length;i++) for(let j=i+1;j<pos.length;j++){ const dx=pts[i][0]-pts[j][0], dy=pts[i][1]-pts[j][1]; if(dx*dx+dy*dy<(step*1.5)*(step*1.5)){ c.beginPath(); c.moveTo(pos[i][0],pos[i][1]); c.lineTo(pos[j][0],pos[j][1]); c.stroke(); } } }
    pos.forEach(([x,y,k,r])=>{ const tw=.75+.25*Math.sin(t*2+r*9); c.fillStyle=`rgba(255,244,214,${(.3+.7*k)*tw})`; c.beginPath(); c.arc(x,y,(1.5+r*1.8)*u,0,7); c.fill(); if(k>.99&&r>.8){ const g=c.createRadialGradient(x,y,0,x,y,14*u); g.addColorStop(0,"rgba(255,230,160,.5)"); g.addColorStop(1,"rgba(255,230,160,0)"); c.fillStyle=g; c.fillRect(x-14*u,y-14*u,28*u,28*u); } });
    c.globalAlpha=eout(ramp(t,8.4,10)); c.fillStyle="#e9c46a"; c.font=`400 ${26*u}px Bahnschrift,"Segoe UI",sans-serif`; c.textAlign="center"; spaced(c,"F O C U S",cx,cy+150*u,16*u); c.globalAlpha=1; } };

/* 16. Eclipse */
const eclipse={ id:"eclipse", title:"Totality", by:"The moon takes the sun", dur:14, still:10.4,
  blurb:"The moon slides across the sun until only the corona is left, one bright bead at the edge, then the name.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2-110*u, S=104*u;
    c.fillStyle="#040406"; c.fillRect(0,0,w,h);
    const R=rng(6); for(let i=0;i<80;i++){ c.fillStyle=`rgba(255,255,255,${.1+R()*.25})`; c.fillRect(R()*w,R()*h,1.3,1.3); }
    const mx=lerp(330*u,-14*u,eio(ramp(t,1.6,8))), tot=ramp(t,5.6,7.2)*(1-ramp(t,9,10.4));
    const glow=c.createRadialGradient(cx,cy,S*.9,cx,cy,S*(2.6+tot*2)); glow.addColorStop(0,`rgba(255,236,190,${.55+.2*tot})`); glow.addColorStop(1,"rgba(255,236,190,0)"); c.fillStyle=glow; c.fillRect(0,0,w,h);
    c.fillStyle="#fff8e8"; c.beginPath(); c.arc(cx,cy,S,0,7); c.fill();
    if(tot>0){ const r2=rng(33); for(let i=0;i<150;i++){ const a=r2()*Math.PI*2, len=(50+r2()*170)*u*tot, wd=(1.5+r2()*5)*u, x0=cx+Math.cos(a)*S, y0=cy+Math.sin(a)*S; const g=c.createLinearGradient(x0,y0,x0+Math.cos(a)*len,y0+Math.sin(a)*len); g.addColorStop(0,"rgba(255,244,214,.6)"); g.addColorStop(1,"rgba(255,244,214,0)"); c.strokeStyle=g; c.lineWidth=wd; c.beginPath(); c.moveTo(x0,y0); c.lineTo(x0+Math.cos(a)*len,y0+Math.sin(a)*len); c.stroke(); } }
    c.fillStyle="#040406"; c.beginPath(); c.arc(cx+mx,cy,S*1.03,0,7); c.fill(); c.strokeStyle=`rgba(255,235,200,${.25*tot})`; c.lineWidth=2*u; c.stroke();
    const d=ramp(t,8.2,8.9)*(1-ramp(t,8.9,10.2)); if(d>0){ const bx=cx+Math.cos(-.62)*S*1.02, by=cy+Math.sin(-.62)*S*1.02; const g=c.createRadialGradient(bx,by,0,bx,by,120*u); g.addColorStop(0,`rgba(255,255,255,${d})`); g.addColorStop(.2,`rgba(255,240,200,${.6*d})`); g.addColorStop(1,"rgba(255,240,200,0)"); c.fillStyle=g; c.fillRect(bx-120*u,by-120*u,240*u,240*u);
      c.strokeStyle=`rgba(255,255,255,${.6*d})`; c.lineWidth=2*u; c.beginPath(); c.moveTo(bx-170*u*d,by); c.lineTo(bx+170*u*d,by); c.stroke(); }
    c.globalAlpha=eout(ramp(t,9.4,11.4)); c.fillStyle="#f6f1e6"; c.font=`200 ${150*u}px Bahnschrift,"Segoe UI Light",sans-serif`; c.textAlign="center"; c.textBaseline="middle"; spaced(c,"CARMEN",cx,cy+300*u,34*u);
    c.globalAlpha=eout(ramp(t,10.6,12.4)); c.fillStyle="#d7b866"; c.font=`400 ${24*u}px Bahnschrift,"Segoe UI",sans-serif`; spaced(c,"F O C U S",cx,cy+372*u,16*u); c.globalAlpha=1;
    vig(c,w,h,.35); } };

/* 17. Pendulum: each swing sets a letter */
const pendulum={ id:"pendulum", title:"Pendulum", by:"One swing, one letter", dur:13, still:9.6,
  blurb:"A brass pendulum swings. At the end of every swing a letter of the name is set, until it comes to rest.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, px=cx, py=-30*u, Lp=470*u;
    c.fillStyle="#ece4d2"; c.fillRect(0,0,w,h); const R=rng(5); for(let i=0;i<800;i++){ c.fillStyle=`rgba(110,90,60,${R()*.05})`; c.fillRect(R()*w,R()*h,1.4,1.4); }
    const amp=.62*ramp(t,0,.6)*(1-eio(ramp(t,7.4,9.6))), th=amp*Math.sin(Math.PI*(t-.0));
    const bx=px+Math.sin(th)*Lp, by=py+Math.cos(th)*Lp;
    for(let i=1;i<=6;i++){ const tt=t-i*.045, a2=.62*ramp(tt,0,.6)*(1-eio(ramp(tt,7.4,9.6)))*Math.sin(Math.PI*tt); c.fillStyle=`rgba(184,138,46,${.07*(7-i)/6})`; c.beginPath(); c.arc(px+Math.sin(a2)*Lp,py+Math.cos(a2)*Lp,36*u,0,7); c.fill(); }
    c.strokeStyle="#1d1a16"; c.lineWidth=3.4*u; c.beginPath(); c.moveTo(px,py); c.lineTo(bx,by); c.stroke();
    const g=c.createRadialGradient(bx-10*u,by-10*u,2,bx,by,40*u); g.addColorStop(0,"#f3dc9a"); g.addColorStop(.6,"#b88a2e"); g.addColorStop(1,"#6e4f14"); c.fillStyle=g; c.beginPath(); c.arc(bx,by,36*u,0,7); c.fill();
    const word="CARMEN", ly=cy0(h)+215*u; c.font=`700 ${108*u}px Georgia,serif`; c.textAlign="left"; c.textBaseline="middle"; const track=20*u, tw=spacedWidth(c,word,track); let x=cx-tw/2;
    [...word].forEach((ch,i)=>{ const set=.5+i*1.0, a=eout(ramp(t,set,set+.35)); if(a>0){ c.globalAlpha=a; c.fillStyle="#1d1a16"; c.fillText(ch,x,ly+(1-a)*-24*u); const f=ramp(t,set,set+.5)*(1-ramp(t,set+.3,set+.8)); if(f>0){ c.strokeStyle=`rgba(184,138,46,${f})`; c.lineWidth=2*u; c.beginPath(); c.arc(x+c.measureText(ch).width/2,ly,(40+60*(1-f))*u,0,7); c.stroke(); } } x+=c.measureText(ch).width+track; }); c.globalAlpha=1;
    c.globalAlpha=eout(ramp(t,9.4,11)); c.fillStyle="#7a6a4a"; c.font=`500 ${24*u}px Georgia,serif`; c.textAlign="center"; spaced(c,"F O C U S",cx,ly+86*u,14*u); c.globalAlpha=1;
    const gg=c.createRadialGradient(cx,h/2,Math.min(w,h)*.4,cx,h/2,Math.max(w,h)*.8); gg.addColorStop(0,"rgba(90,70,40,0)"); gg.addColorStop(1,"rgba(90,70,40,.24)"); c.fillStyle=gg; c.fillRect(0,0,w,h); } };
function cy0(h){ return h/2; }

/* 18. Stop-motion cut paper */
const stopmotion={ id:"stopmotion", title:"Cut paper, stop motion", by:"Eight frames a second", dur:12, still:9.6,
  blurb:"Coloured paper tiles drop in at eight frames a second, each one landing a little crooked, like hand animation.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2, ts=Math.floor(t*8)/8;
    c.fillStyle="#f3ead7"; c.fillRect(0,0,w,h); const R=rng(12); for(let i=0;i<500;i++){ c.fillStyle=`rgba(120,100,70,${R()*.06})`; c.fillRect(R()*w,R()*h,1.6,1.6); }
    const pal=["#1f6f6b","#f26b4b","#f2b632","#1c2a44","#f26b4b","#1f6f6b"], tw=128*u, gap=14*u, total=6*tw+5*gap, x0=cx-total/2+tw/2;
    const stepE=p=>Math.floor(clamp(p)*5)/5;
    const shapes=[[-420,-190,"c",.4,"#f26b4b",70],[400,-170,"t",1.1,"#f2b632",80],[-380,200,"h",1.9,"#1f6f6b",76],[410,205,"c",2.6,"#1c2a44",60]];
    shapes.forEach(([sx,sy,k,s,col,r])=>{ const p=stepE((ts-s)/.9); if(p<=0) return; c.save(); c.translate(cx+sx*u,cy+sy*u+(1-p)*-260*u); c.rotate(((Math.floor(ts*8)*37)%9-4)*.012); c.fillStyle="rgba(0,0,0,.18)"; c.beginPath(); c.arc(6*u,8*u,r*u,0,7); c.fill(); c.fillStyle=col;
      if(k==="c"){ c.beginPath(); c.arc(0,0,r*u,0,7); c.fill(); } else if(k==="t"){ c.beginPath(); c.moveTo(0,-r*u); c.lineTo(r*.95*u,r*.7*u); c.lineTo(-r*.95*u,r*.7*u); c.closePath(); c.fill(); } else { c.beginPath(); c.arc(0,0,r*u,Math.PI,0); c.fill(); } c.restore(); });
    [..."CARMEN"].forEach((ch,i)=>{ const s=.9+i*.5, p=stepE((ts-s)/.8); if(p<=0) return; const jr=((Math.floor(ts*8)*(i+3)*29)%11-5)*.011*(p<1?1.6:.5), jy=((Math.floor(ts*8)*(i+5)*17)%7-3)*1.1*u;
      c.save(); c.translate(x0+i*(tw+gap),cy-20*u+(1-p)*-300*u+jy); c.rotate(jr); c.fillStyle="rgba(0,0,0,.2)"; c.beginPath(); c.roundRect(-tw/2+7*u,-tw*.55+9*u,tw,tw*1.1,14*u); c.fill(); c.fillStyle=pal[i]; c.beginPath(); c.roundRect(-tw/2,-tw*.55,tw,tw*1.1,14*u); c.fill();
      c.fillStyle="#f3ead7"; c.font=`900 ${104*u}px "Arial Black",Impact,sans-serif`; c.textAlign="center"; c.textBaseline="middle"; c.fillText(ch,0,6*u); c.restore(); });
    const sp=stepE((ts-5.2)/1.0); if(sp>0){ c.save(); c.translate(cx,cy+170*u); c.rotate(-.012); c.fillStyle="rgba(0,0,0,.2)"; c.fillRect(-300*u+7*u,-34*u+9*u,600*u*sp,68*u); c.fillStyle="#1c2a44"; c.fillRect(-300*u,-34*u,600*u*sp,68*u);
      c.save(); c.beginPath(); c.rect(-300*u,-40*u,600*u*sp,80*u); c.clip(); c.fillStyle="#f3ead7"; c.font=`900 ${40*u}px "Arial Black",Impact,sans-serif`; c.textAlign="center"; c.textBaseline="middle"; spaced(c,"F O C U S",0,3*u,18*u); c.restore(); c.restore(); } } };

/* 19. Sunrise: flat shapes, one colour at a time */
const sunrise={ id:"sunrise", title:"First sun", by:"Flat shapes, slow dawn", dur:14, still:9.4,
  blurb:"A flat-colour dawn: the sky warms, a striped sun climbs behind three ridges and the title rests on the sky.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, hz=h/2+110*u, p=eio(ramp(t,.4,9.4));
    const topN=["#0f1438","#3a2a6e","#c4503f"], botN=["#1c2557","#e4572e","#f9c46b"];
    const pick=(A,k)=>k<.5?mix(A[0],A[1],k*2):mix(A[1],A[2],(k-.5)*2);
    const sky=c.createLinearGradient(0,0,0,hz); sky.addColorStop(0,pick(topN,p)); sky.addColorStop(1,pick(botN,p)); c.fillStyle=sky; c.fillRect(0,0,w,hz);
    const R=rng(17); for(let i=0;i<90;i++){ c.fillStyle=`rgba(255,244,214,${(1-p*1.6>0?(1-p*1.6):0)*(.2+.5*R())})`; c.fillRect(R()*w,R()*hz*.8,1.6,1.6); }
    const sr=100*u, sy=lerp(hz+120*u,hz-150*u,p), sg=c.createRadialGradient(cx,sy,sr*.8,cx,sy,sr*3.2); sg.addColorStop(0,`rgba(253,233,168,${.35*p})`); sg.addColorStop(1,"rgba(253,233,168,0)"); c.fillStyle=sg; c.fillRect(0,0,w,hz);
    c.save(); c.beginPath(); c.rect(0,0,w,hz); c.clip(); c.fillStyle="#fde9a8"; c.beginPath(); c.arc(cx,sy,sr,0,7); c.fill(); c.fillStyle=pick(botN,p); for(let i=0;i<6;i++){ const yy=sy+8*u+i*i*3.2*u+i*8*u; c.fillRect(cx-sr,yy,sr*2,(1.5+i*1.5)*u); } c.restore();
    const ridge=(base,amp,seed,col,par)=>{ c.fillStyle=col; c.beginPath(); c.moveTo(0,hz); const Rr=rng(seed); const ys=[]; for(let i=0;i<=12;i++) ys.push(Rr()); for(let i=0;i<=12;i++){ const x=i/12*w, y=hz-base*u-ys[i]*amp*u+Math.sin(t*.1)*par; c.lineTo(x,y); } c.lineTo(w,hz); c.closePath(); c.fill(); };
    ridge(40,110,5,mix("#2b2358",pick(botN,p),.25),0); ridge(18,85,9,"#1e1a47",0); ridge(0,60,13,"#120f2e",0);
    const sea=c.createLinearGradient(0,hz,0,h); sea.addColorStop(0,"#171233"); sea.addColorStop(1,"#0a0820"); c.fillStyle=sea; c.fillRect(0,hz,w,h-hz);
    for(let i=0;i<9;i++){ const k=i/9, ww=(1-k)*sr*1.6*p+6*u, yy=hz+14*u+i*i*3.2*u+i*7*u; c.fillStyle=`rgba(253,233,168,${.7*p*(1-k)})`; c.fillRect(cx-ww/2+Math.sin(t*2+i)*6*u,yy,ww,3*u); }
    c.globalAlpha=eout(ramp(t,4.6,7.4)); c.fillStyle="#fff1d6"; c.font=`300 ${150*u}px Bahnschrift,"Segoe UI Light",sans-serif`; c.textAlign="center"; c.textBaseline="middle"; spaced(c,"CARMEN",cx,hz-260*u,30*u);
    c.globalAlpha=eout(ramp(t,7.2,9.4)); c.font=`400 ${24*u}px Bahnschrift,"Segoe UI",sans-serif`; c.fillStyle="#ffe3b0"; spaced(c,"F O C U S",cx,hz-186*u,16*u); c.globalAlpha=1; } };

/* 20. Terminal */
const terminal={ id:"terminal", title:"Boot", by:"Amber phosphor", dur:13, still:6.6,
  blurb:"An old green-screen boot log checks your attention, blocks fourteen distractions, and prints the name in glowing type.",
  draw(c,t,w,h){ const u=U(w,h), cx=w/2, cy=h/2;
    c.fillStyle="#0b0905"; c.fillRect(0,0,w,h);
    const AMB="#ffb000", lines=[["CARMEN FOCUS OS 1.0",.4,1.4],["> checking attention ......... 1 of 1",1.7,3.1],["> blocking distractions ..... 14 found, 14 gone",3.3,5.0],["> setting timer .............. 25:00",5.2,6.3],["> ready.",6.6,7.1]];
    const show=t<8, fl=.94+.06*Math.sin(t*50)*Math.sin(t*3.1);
    c.shadowColor="rgba(255,176,0,.85)"; c.shadowBlur=14*u; c.fillStyle=AMB; c.textBaseline="alphabetic";
    if(show){ c.font=`500 ${27*u}px Consolas,"Courier New",monospace`; c.textAlign="left"; const x0=cx-420*u;
      lines.forEach(([txt,a,b],i)=>{ const n=Math.floor(clamp((t-a)/(b-a))*txt.length); c.globalAlpha=fl; c.fillText(txt.slice(0,n),x0,cy-150*u+i*50*u); if(n<txt.length&&n>0||(i===4&&n===txt.length&&Math.floor(t*2)%2===0)){ c.fillRect(x0+c.measureText(txt.slice(0,n)).width+4*u,cy-150*u+i*50*u-22*u,16*u,26*u); } }); c.globalAlpha=1; }
    const k=ramp(t,8.3,9.6); if(k>0){ c.save(); c.beginPath(); c.rect(0,0,w,h*k); c.clip(); c.shadowBlur=30*u; c.globalAlpha=fl; c.textAlign="center"; c.font=`700 ${190*u}px Consolas,"Courier New",monospace`; c.fillText("CARMEN",cx,cy+30*u);
      c.shadowBlur=14*u; c.font=`500 ${32*u}px Consolas,"Courier New",monospace`; c.fillText("F O C U S   O S   1 . 0",cx,cy+110*u); if(Math.floor(t*2)%2===0&&t>10) c.fillRect(cx+250*u,cy+84*u,20*u,30*u); c.restore(); }
    c.shadowBlur=0; c.globalAlpha=1; c.fillStyle="rgba(0,0,0,.24)"; for(let y=0;y<h;y+=3) c.fillRect(0,y,w,1.2);
    const bandY=((t*90*u)%(h+160*u))-80*u; const bg=c.createLinearGradient(0,bandY-60*u,0,bandY+60*u); bg.addColorStop(0,"rgba(255,176,0,0)"); bg.addColorStop(.5,"rgba(255,176,0,.05)"); bg.addColorStop(1,"rgba(255,176,0,0)"); c.fillStyle=bg; c.fillRect(0,bandY-60*u,w,120*u);
    vig(c,w,h,.85); } };

[pull,enso,sundial,sand,typewriter,blueprint,swiss,neon,ripples,leader,constellation,eclipse,pendulum,stopmotion,sunrise,terminal].forEach(s=>SEQS.push(s));

// Logic checks for static/js/shell.js (drawer, scroll lock, student nav, bell, All Clean) in jsdom.
// Layout/animation cannot be checked here. Run: npm i jsdom && node tests/js/shell_logic.test.js
const {JSDOM}=require('jsdom'); const fs=require('fs'); const assert=require('assert');
const src=fs.readFileSync(require('path').join(__dirname,'..','..','static','js','shell.js'),'utf8');
let pass=0; const ok=(c,m)=>{assert(c,m); pass++; console.log('ok -',m)};
function mk(html, opts={}){
  const dom=new JSDOM(`<!doctype html><html><head><meta name="csrf-token" content="TOK"></head><body class="${opts.cls||''}">${html}</body></html>`,{runScripts:'outside-only',pretendToBeVisual:true,url:'https://x.test/'});
  const w=dom.window; const calls=[];
  w.matchMedia=q=>({matches:opts.phone!==false,addEventListener(){},removeEventListener(){}});
  w.scrollTo=()=>{}; w.fetch=opts.fetch||((u,o)=>{calls.push([u,o&&o.method,o&&o.body]);return Promise.resolve({ok:true,json:()=>Promise.resolve({ok:true,notifications:[],unread_count:0})})});
  w.AMC={toast(t,m){calls.push(['toast',t,m])},confirm:()=>Promise.resolve(opts.confirm!==false)};
  w.addEventListener('error',e=>console.log('PAGE ERR',e.message)); w.eval(src); return {w,d:w.document,calls};
}
const T=()=>new Promise(r=>setTimeout(r,20));
(async()=>{
 // drawer
 let {w,d}=mk(`<header class="topbar"><button id="menuBtn"></button></header><div class="scrim" id="scrim"></div><aside id="sidebar"><a href="/x">L</a></aside><main class="main"></main><nav class="bottom-nav"><button id="moreBtn"></button></nav>`,{cls:'teacher-shell'});
 const root=d.documentElement, body=d.body, side=d.getElementById('sidebar');
 d.getElementById('menuBtn').click();
 ok(body.classList.contains('drawer-open')&&root.classList.contains('amc-scroll-lock'),'top-left menu opens drawer and locks scroll');
 ok(d.querySelector('.main').hasAttribute('inert'),'background is inert while open');
 d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
 ok(!body.classList.contains('drawer-open')&&!root.classList.contains('amc-scroll-lock')&&!d.querySelector('.main').hasAttribute('inert'),'Escape closes, unlocks, restores');
 d.getElementById('moreBtn').click(); ok(body.classList.contains('drawer-open'),'bottom More opens the SAME drawer');
 d.getElementById('menuBtn').dispatchEvent(new w.Event('click')); 
 d.getElementById('scrim').click(); ok(!body.classList.contains('drawer-open'),'overlay click closes');
 d.getElementById('moreBtn').click(); side.querySelector('a').click(); ok(!body.classList.contains('drawer-open'),'link click closes');
 ok(d.getElementById('moreBtn').getAttribute('aria-expanded')==='false','aria-expanded reset');
 // student nav
 ({w,d}=mk(`<nav class="bottom-nav"><a data-nav="home" class="active"></a><a data-nav="tests"></a></nav>`,{cls:'student-shell'}));
 let y=0, H=3000; Object.defineProperty(w,'pageYOffset',{get:()=>y}); Object.defineProperty(w,'innerHeight',{value:800}); Object.defineProperty(w.document.documentElement,'scrollHeight',{get:()=>H});
 w.requestAnimationFrame=f=>{f();return 1};
 const sc=v=>{y=v;w.dispatchEvent(new w.Event('scroll'))};
 sc(30); ok(!d.body.classList.contains('nav-hidden'),'visible near top');
 sc(200); ok(d.body.classList.contains('nav-hidden'),'scroll down hides');
 sc(204); ok(d.body.classList.contains('nav-hidden'),'tiny move ignored (no flicker)');
 sc(150); ok(!d.body.classList.contains('nav-hidden'),'scroll up shows');
 sc(400); sc(2195); ok(d.body.classList.contains('nav-hidden'),'hidden again');
 sc(2199); sc(2200); ok(d.body.classList.contains('nav-hidden'),'bottom bounce does not flip state');
 sc(20); ok(!d.body.classList.contains('nav-hidden'),'top always shows');
 d.querySelector('[data-nav=tests]').ownerDocument.defaultView.location.hash='#tests'; w.dispatchEvent(new w.Event('hashchange'));
 ok(d.querySelector('[data-nav=tests]').classList.contains('active')&&!d.querySelector('[data-nav=home]').classList.contains('active'),'#tests highlights Tests');
 // teacher nav never hides
 ({w,d}=mk(`<nav class="bottom-nav"></nav>`,{cls:'teacher-shell'})); Object.defineProperty(w,'pageYOffset',{get:()=>500}); w.dispatchEvent(new w.Event('scroll'));
 ok(!d.body.classList.contains('nav-hidden'),'teacher nav unaffected');
 // bell
 const items=[{id:1,title:'<b>A</b>',message:'m',type:'test',link:'/t',created_at:'2026-11-14T10:00:00',is_read:false},{id:2,title:'B',message:'m',type:'info',link:'',created_at:'2026-11-13T10:00:00',is_read:true}];
 const html=`<button id="notifBtn"><span class="notif-badge" id="notifBadge">1</span></button><div id="notifBackdrop" hidden></div><section id="notifPanel" hidden><button id="clearNotifBtn" disabled></button><div id="notifList"></div></section><script id="amcNotifData" type="application/json">{}</script>`;
 let calls; const fetchMock=(c)=>(u,o)=>{c.push([u,o&&o.method]);return Promise.resolve({ok:true,json:()=>Promise.resolve(u==='/api/notifications'?{ok:true,notifications:items,unread_count:1}:{ok:true})})};
 calls=[]; ({w,d}=mk(html,{cls:'student-shell',fetch:fetchMock(calls)}));
 d.getElementById('notifBtn').click(); await T(); await T();
 ok(!d.getElementById('notifPanel').hasAttribute('hidden'),'bell opens panel');
 ok(d.querySelectorAll('.notif-item').length===2&&d.querySelectorAll('.notif-item.is-new').length===1,'history shown, unread highlighted');
 ok(!d.querySelector('.notif-item-title').innerHTML.includes('<b>'),'titles are escaped (textContent)');
 ok(!d.getElementById('notifBadge'),'badge cleared immediately');
 ok(calls.filter(c=>c[0]==='/api/notifications/mark-read').length===1,'exactly one mark-read request');
 ok(d.documentElement.classList.contains('amc-scroll-lock'),'phone panel locks background scroll');
 ok(!d.getElementById('clearNotifBtn').disabled,'All Clean enabled when list has items');
 d.getElementById('clearNotifBtn').click(); await T(); await T();
 ok(calls.some(c=>c[0]==='/api/notifications/clear'&&c[1]==='POST'),'All Clean posts after confirm');
 ok(d.querySelectorAll('.notif-item').length===0&&/no notifications/i.test(d.getElementById('notifList').textContent),'list emptied');
 d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true})); ok(d.getElementById('notifPanel').hasAttribute('hidden')&&!d.documentElement.classList.contains('amc-scroll-lock'),'Escape closes panel, unlocks');
 // cancelled confirm -> no request
 calls=[]; ({w,d}=mk(html,{cls:'student-shell',confirm:false,fetch:fetchMock(calls)}));
 d.getElementById('notifBtn').click(); await T(); await T(); d.getElementById('clearNotifBtn').click(); await T(); await T();
 ok(!calls.some(c=>c[0]==='/api/notifications/clear'),'cancelled All Clean sends nothing');
 // impersonation: read only, no mark-read
 calls=[]; ({w,d}=mk(html.replace('{}','{"impersonating":true}'),{cls:'student-shell',fetch:fetchMock(calls)}));
 d.getElementById('notifBtn').click(); await T(); await T();
 ok(!calls.some(c=>c[0]==='/api/notifications/mark-read'),'view-as-student never marks read');
 console.log('\nALL',pass,'JS CHECKS PASSED');
})().catch(e=>{console.error('FAIL',e.message);process.exit(1)});

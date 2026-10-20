/* Nexus docs-enhance.js — progressive enhancements (loaded before search.js). No deps. */
(function(){ 'use strict'; var doc=document;
/* heading anchors (fallback if server-side missed) */
(function(){ var hs=doc.querySelectorAll('.prose h2[id], .prose h3[id], article h2[id], article h3[id]'); hs.forEach(function(h){ if(h.querySelector('.anchor-enh')) return; var a=doc.createElement('a'); a.className='anchor-enh'; a.href='#'+h.id; a.textContent='#'; a.setAttribute('aria-label','Link to '+h.id); h.appendChild(a); });})();
/* reading time (fills [data-reading-time]) */
(function(){ var badge=doc.querySelector('[data-reading-time]'); var art=doc.querySelector('.prose, article'); if(badge&&art){ var w=(art.innerText||'').trim().split(/\s+/).filter(Boolean).length; badge.textContent=Math.max(1,Math.round(w/210))+' min read'; }})();
/* code line numbers for pres with many lines (skipped on phones: the gutter
   costs ~42px of a ~330px measure and the numbers are decorative) */
(function(){ if((window.innerWidth||960)<640) return; doc.querySelectorAll('pre').forEach(function(pre){ var code=pre.querySelector('code'); var src=code?code.textContent:pre.textContent; var n=(src||'').split('\n').length; if(n<4||pre.classList.contains('no-ln')) return; pre.classList.add('code-ln-enhanced'); if(pre.querySelector('.ln-gutter')) return; var gut=doc.createElement('div'); gut.className='ln-gutter'; gut.setAttribute('aria-hidden','true'); var s=''; for(var i=1;i<=n;i++) s+=i+'\n'; gut.textContent=s; pre.appendChild(gut); });})();

/* mark horizontally scrollable code so CSS can show a fade edge */
(function(){ var mark=function(){ doc.querySelectorAll('.codeblock').forEach(function(b){ var pre=b.querySelector('pre'); if(!pre) return; b.classList.toggle('has-overflow', pre.scrollWidth>pre.clientWidth+4); }); }; mark(); window.addEventListener('load', mark); window.addEventListener('resize', mark, {passive:true});})();
/* table enhancements: wrap + sortable affordance */
(function(){ doc.querySelectorAll('table').forEach(function(t){ if(t.closest('.table-wrap')||t.closest('.compare')) return; var w=doc.createElement('div'); w.className='table-wrap table-premium'; t.parentNode.insertBefore(w,t); w.appendChild(t); });})();
/* back-to-top (enhanced layer) */
(function(){ var btn=doc.getElementById('to-top'); if(!btn) return; var on=function(){ btn.classList.toggle('is-visible', (window.scrollY||doc.documentElement.scrollTop)>520); }; window.addEventListener('scroll', on, {passive:true}); on();})();
/* TOC progress (sets --p on .toc-progress if present) */
(function(){ var bar=doc.querySelector('.toc-progress'); if(!bar) return; var on=function(){ var h=doc.documentElement; var max=h.scrollHeight-h.clientHeight; var p=max>0?(h.scrollTop/max):0; bar.style.setProperty('--p', p); bar.classList.toggle('is-on', p>0); }; window.addEventListener('scroll', on, {passive:true}); on();})();
/* lightbox enhance: zoom on prose images not already wired */
(function(){ var lb=doc.querySelector('.lightbox'); if(!lb) return; var img=lb.querySelector('img'); doc.querySelectorAll('.prose-enhanced img, .figure-enhanced img').forEach(function(im){ if(im.closest('a')) return; im.style.cursor='zoom-in'; im.addEventListener('click', function(){ if(img){ img.src=im.currentSrc||im.src; img.alt=im.alt||''; lb.classList.add('open'); doc.body.style.overflow='hidden'; }}); });})();

/* ── in-page TOC: scroll-spy + mobile collapse ─────────────────────────── */
(function(){
  var links=[].slice.call(doc.querySelectorAll('.toc .toc-list a, .toc-mobile .toc-list a'));
  if(!links.length) return;
  var heads=links.map(function(a){ var id=(a.getAttribute('href')||'').replace('#',''); return id?doc.getElementById(id):null; }).filter(Boolean);
  if(!heads.length) return;
  var byId={}; links.forEach(function(a){ byId[(a.getAttribute('href')||'').replace('#','')]=a; });
  var rail=doc.querySelector('.toc');
  var setActive=function(id){
    links.forEach(function(a){ a.classList.remove('is-active'); a.removeAttribute('aria-current'); });
    var a=byId[id]; if(!a) return;
    a.classList.add('is-active'); a.setAttribute('aria-current','location');
    if(rail && a.offsetTop!=null){
      var top=a.offsetTop-rail.clientHeight/2;
      if(Math.abs(top-rail.scrollTop)>40) rail.scrollTo({top:top,behavior:'smooth'});
    }
  };
  var current=null;
  var update=function(){
    var y=(window.scrollY||doc.documentElement.scrollTop)+120, hit=null;
    for(var i=0;i<heads.length;i++){ if(heads[i].getBoundingClientRect().top+ (window.scrollY||0) <= y) hit=heads[i]; }
    if(hit && hit.id!==current){ current=hit.id; setActive(hit.id); }
  };
  var raf=0;
  window.addEventListener('scroll', function(){ if(raf) return; raf=requestAnimationFrame(function(){ raf=0; update(); }); }, {passive:true});
  update();
  if(!links.some(function(a){ return a.classList.contains('is-active'); })){ setActive(heads[0].id); }
  /* on narrow screens the TOC is a <details>: close it after picking a section */
  var mob=doc.querySelector('details.toc-mobile');
  if(mob) mob.addEventListener('click', function(e){ var t=e.target.closest('a'); if(t) mob.removeAttribute('open'); });
})();
})();

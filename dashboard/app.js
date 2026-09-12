/* GM dashboard front end (v0.25.0).

   The layout changed; the views did not. Every loader below still asks the same
   endpoint for the same data and still renders the same columns. What is new is
   above them: a page template that gives each view a header, a density control
   and - where a view has three or more sections - tabs instead of a stack.

   Cultivation had eleven tables one under another, Crafting eleven more,
   Exploration nine, Samsara eight. Reaching the last one meant scrolling past
   the other ten, every time, with nothing on the page saying what was down
   there. `sectionize` below is the whole fix, and it works by MOVING the nodes
   a loader already rendered rather than re-serialising them - so every click
   handler a loader attached survives, and no loader had to be rewritten to get
   tabs. */

const app=document.getElementById('app'); const drawer=document.getElementById('drawer'); const drawerBody=document.getElementById('drawerBody');
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const n=v=>Number(v??0).toLocaleString();
// Discord IDs are snowflakes past Number.MAX_SAFE_INTEGER: never put one through
// Number() or n(). The API sends them as exact decimal strings - show and compare
// them as-is. id() renders one, sameId() compares two regardless of string/number.
const id=v=>(v===null||v===undefined||v==='')?'—':esc(String(v));
const sameId=(a,b)=>String(a??'')===String(b??'');
const jsonText=(v,empty='—')=>{if(v===null||v===undefined||v==='')return empty;try{const x=typeof v==='string'?JSON.parse(v):v;if(Array.isArray(x))return x.length?x.join(', '):empty;if(x&&typeof x==='object')return Object.entries(x).map(([k,val])=>`${k}: ${val}`).join(', ')||empty;return String(x)}catch{return String(v)}};
const pill=(v,cls='')=>`<span class="pill ${cls}">${esc(v??'—')}</span>`;
async function api(path){const r=await fetch(path,{cache:'no-store'}); if(!r.ok) throw new Error(`${r.status} ${await r.text()}`); return r.json()}
async function narrationPost(action,payload={}){const r=await fetch('/api/narration/action',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Xianxia-Admin':'1'},body:JSON.stringify({action,payload})});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(d.message||d.error||`Narration action failed (${r.status})`);return d}
async function discordPost(action,payload={}){const r=await fetch('/api/discord/action',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Xianxia-Admin':'1'},body:JSON.stringify({action,payload})});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(d.message||d.error||`Discord setup action failed (${r.status})`);return d}
async function adminPost(action,payload={}){const r=await fetch('/api/admin/action',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Xianxia-Admin':'1'},body:JSON.stringify({action,payload})});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(d.message||d.error||`Admin action failed (${r.status})`);return d}
function resultBox(v,ok=true){return `<div class="result ${ok?'goodbox':'badbox'}"><b>${ok?'Success':'Failed'}</b><pre>${esc(typeof v==='string'?v:JSON.stringify(v,null,2))}</pre></div>`}
function optionRows(rows,value='user_id',label=r=>`${r.name} · ${r.discord_name}`){return rows.map(r=>`<option value="${esc(r[value])}">${esc(label(r))}</option>`).join('')}
function fmtGM(m){m=Number(m||0); const y=Math.floor(m/(60*24*30*12))+1, remY=m%(60*24*30*12), mo=Math.floor(remY/(60*24*30))+1, remM=remY%(60*24*30), d=Math.floor(remM/(60*24))+1; return `Y${y} M${mo} D${d}`}

/* One table for the whole dashboard. It carries its own row count, because
   "No records." and "the first 300 of 4,000" used to look identical, and its
   header sticks - which it never actually did before, the old wrapper having no
   height for `position:sticky` to stick within. */
function table(headers,rows,opts={}){
  rows=rows||[];
  if(!rows.length)return emptyState(opts.empty||'Nothing here yet.',opts.why);
  const total=opts.total??rows.length;
  const caption=rows.length<total?`showing ${n(rows.length)} of ${n(total)}`:`${n(rows.length)} row${rows.length===1?'':'s'}`;
  return `<div class="tablewrap"><table><thead><tr>${headers.map(h=>`<th>${esc(h[0])}</th>`).join('')}</tr></thead>`
    +`<tbody>${rows.map(r=>`<tr${opts.rowAttrs?' '+opts.rowAttrs(r):''}>${headers.map(h=>`<td>${typeof h[1]==='function'?h[1](r):esc(r[h[1]])}</td>`).join('')}</tr>`).join('')}</tbody>`
    +`</table></div><div class="tablefoot">${caption}</div>`;
}
function emptyState(text,why){return `<div class="empty">${esc(text)}${why?`<span class="why">${esc(why)}</span>`:''}</div>`}
function timeline(rows){if(!rows.length)return emptyState('No history recorded.');return `<div class="timeline">${rows.map(r=>`<div class="timeline-item"><div>${pill(r.event_type,'purple')} ${pill(`sig ${r.significance}`,'warn')} ${pill(r.visibility)}</div><strong>${esc(r.title)}</strong><div>${esc(r.summary)}</div><div class="timeline-meta">${fmtGM(r.game_minute)} · ${esc(r.location||'Unknown location')} ${r.faction?`· ${esc(r.faction)}`:''}${r.actor_name?` · ${esc(r.actor_name)}`:''}</div></div>`).join('')}</div>`}
function filters(html){return `<div class="filters">${html}<button class="btn" id="applyFilters">Apply</button></div>`}
function panel(title,body,opts={}){return `<div class="panel ${opts.cls||''}"><div class="ptitle"><h3>${esc(title)}</h3>${opts.count?`<span class="count">${esc(opts.count)}</span>`:''}${opts.acts?`<div class="pacts">${opts.acts}</div>`:''}</div>${body}</div>`}
function metric(label,value,delta,cls){return `<div class="m"><small>${esc(label)}</small><b class="${cls||''}">${value}</b>${delta?`<span class="delta">${delta}</span>`:''}</div>`}

/* Every view: what it is called, the sentence under the title, where it sits in
   the sidebar, and whether its sections become tabs. `stack` is for the two
   pages that are meant to be read whole. */
const VIEWS={
  overview:{t:'Overview',g:'World',b:'What the world is doing right now, and whether the machinery underneath it is keeping up.',layout:'stack'},
  timeline:{t:'Timeline',g:'World',b:'Every mechanically recorded event, permission-filtered exactly as the narrator sees it.'},
  npcs:{t:'NPCs',g:'World',b:'Who is alive, where they are, and what they are trying to do. A card opens the full definition.'},
  events:{t:'World Events',g:'World',b:'Active events, the regions they are happening to, and the eras they belong to.'},
  sects:{t:'Sect Politics',g:'World',b:'Influence, cohesion and the treaties between the nine. Internal factions have their own agendas.'},
  families:{t:'Families',g:'World',b:'Birth families, player-founded lines, and the autonomous marriages and descendants the simulation produces.'},
  conflicts:{t:'Conflicts',g:'World',b:'Wars, battles, feuds, bounties and boss encounters — everything currently being fought over.'},
  players:{t:'Player Activity',g:'Players',b:'Who is playing, where they are, and what they were last doing. A row opens the character sheet.'},
  cultivation:{t:'Cultivation',g:'Players',b:'Every progression system a character carries — eleven tables, one at a time.'},
  crafting:{t:'Crafting & Assets',g:'Players',b:'Professions, alchemy, bound companions, and everything a character owns or has built.'},
  conditions:{t:'Conditions',g:'Players',b:'Active conditions by category and severity. A GM can clear one from the player sheet.'},
  party:{t:'Parties & Formations',g:'Players',b:'Standing parties and the formations they have practised.'},
  pvp:{t:'PvP',g:'Players',b:'Challenges and duels. A match re-checks its own preconditions on every action.'},
  dynasties:{t:'Samsara Dynasties',g:'Players',b:'Reincarnation, soul legacy, and the claims and conflicts descendants raise over them.'},
  quests:{t:'Quests',g:'Content',b:'Every definition players can be given — forged, hand-written, or a commission an NPC hands out.'},
  commissions:{t:'Commissions',g:'Content',b:'The commission pipeline: what is on offer, who is carrying one, and how their givers feel about them.'},
  exploration:{t:'Exploration',g:'Content',b:'Events, secret realms, discoveries, beasts and caravans — everything that happens away from a settlement.'},
  economy:{t:'Economy',g:'Content',b:'Markets, auctions, the black market, and the crimes recorded against them.'},
  rag:{t:'RAG Memory',g:'Systems',b:'What the narrator can retrieve, and at what visibility. Nothing here creates game truth.'},
  decisions:{t:'Autonomous Decisions',g:'Systems',b:'What the simulation decided on its own, and which of those decisions reached permanent history.'},
  threads:{t:'Discord Threads',g:'Systems',b:'Household, expedition and private scene threads bound to guild channels.'},
  ai_routing:{t:'AI Routing',g:'Systems',b:'Which narration route serves a scene, what each one last answered, and what the daily liveness check retired. Read-only — narration is descriptive, never authoritative.'},
  discord:{t:'Discord Setup',g:'Admin',b:'Provision and repair the server layout. Only discord.py touches guilds — no game mechanics happen here.'},
  narration:{t:'Narration Routes',g:'Admin',b:'Which free models narrate, in what order, and what the daily probe found out about each one. Narration is descriptive only — nothing here can change canonical state.'},
  admin:{t:'Admin Console',g:'Admin',b:'Actions that change the world. Every one is applied by the engine and written to admin_audit_log with your name on it.'},
};

let CURRENT='overview';
const SECTION={};

function density(){try{return localStorage.getItem('xr.density')||'comfortable'}catch{return 'comfortable'}}
function setDensity(v){document.documentElement.dataset.density=v;try{localStorage.setItem('xr.density',v)}catch{}
  document.querySelectorAll('[data-density-btn]').forEach(b=>b.classList.toggle('primary',b.dataset.densityBtn===v))}

/* Move what a loader rendered into a header + sections, without re-serialising
   any of it. Node moves keep every listener the loader just attached, which is
   why not one of the twenty-three loaders had to change to gain tabs. */
function sectionize(view){
  const cfg=VIEWS[view]||{t:view,b:''};
  const nodes=Array.from(app.childNodes);
  const lead=[]; let secs=[]; let cur=null;
  for(const node of nodes){
    if(node.nodeType===1&&node.tagName==='H2'){cur={label:node.textContent.trim(),nodes:[]};secs.push(cur);continue}
    (cur?cur.nodes:lead).push(node);
  }
  // Several views open with a heading over nothing but the metric strip -
  // "Cultivation & Aptitudes" above five cards, say. That is the page summary,
  // not one of its sections, and left alone it became an empty first tab.
  secs=secs.filter(sec=>{
    const els=sec.nodes.filter(node=>node.nodeType===1);
    if(!els.length||!els.every(el=>el.classList&&el.classList.contains('cards')))return true;
    els.forEach(el=>lead.push(el));
    return false;
  });
  const useTabs=cfg.layout!=='stack'&&secs.length>=3;
  app.textContent='';

  const head=document.createElement('div');
  head.className='phead';
  head.innerHTML=`<div><h2>${esc(cfg.t)}</h2>${cfg.b?`<p>${esc(cfg.b)}</p>`:''}</div>`
    +`<div class="pheadacts"><button class="btn sm" data-density-btn="comfortable">Comfortable</button>`
    +`<button class="btn sm" data-density-btn="compact">Compact</button></div>`;
  app.appendChild(head);
  // A view with page-level actions renders them into #pageActions; they are
  // moved into the shared header rather than each view drawing its own.
  const acts=lead.find(node=>node.nodeType===1&&node.id==='pageActions');
  if(acts){
    const slot=head.querySelector('.pheadacts');
    Array.from(acts.children).forEach(child=>slot.insertBefore(child,slot.firstChild));
    lead.splice(lead.indexOf(acts),1);
  }
  const current=density();
  head.querySelectorAll('[data-density-btn]').forEach(b=>{
    b.classList.toggle('primary',b.dataset.densityBtn===current);
    b.onclick=()=>setDensity(b.dataset.densityBtn);
  });

  const leadWrap=document.createElement('div');
  lead.forEach(node=>leadWrap.appendChild(node));
  app.appendChild(leadWrap);

  let tabsEl=null;
  if(useTabs){
    tabsEl=document.createElement('div');
    tabsEl.className='tabs';
    app.appendChild(tabsEl);
  }
  const active=Math.min(SECTION[view]??0,Math.max(0,secs.length-1));
  secs.forEach((sec,i)=>{
    const wrap=document.createElement('div');
    wrap.className='sec';
    wrap.dataset.sec=String(i);
    if(!useTabs){const h=document.createElement('h2');h.textContent=sec.label;wrap.appendChild(h)}
    sec.nodes.forEach(node=>wrap.appendChild(node));
    if(useTabs&&i!==active)wrap.hidden=true;
    app.appendChild(wrap);
    if(tabsEl){
      const b=document.createElement('button');
      b.textContent=sec.label;
      b.className=i===active?'active':'';
      b.onclick=()=>selectSection(view,i);
      tabsEl.appendChild(b);
    }
  });
  if(useTabs&&secs.length)location.hash=`${view}/${active}`; else location.hash=view;
}

function selectSection(view,index){
  SECTION[view]=index;
  app.querySelectorAll('.sec').forEach(el=>{el.hidden=Number(el.dataset.sec)!==index});
  app.querySelectorAll('.tabs button').forEach((b,i)=>b.classList.toggle('active',i===index));
  location.hash=`${view}/${index}`;
}

async function switchView(v){
  if(!VIEWS[v])v='overview';
  CURRENT=v;
  const cfg=VIEWS[v];
  document.querySelectorAll('#nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===v));
  document.getElementById('crumb').textContent=`${cfg.g} / ${cfg.t}`;
  app.innerHTML='<div class="skel"><i></i><i></i><i></i></div>';
  try{await loaders[v]();sectionize(v)}
  catch(e){app.innerHTML=`<div class="panel failed"><div class="ptitle"><h3 class="bad">${esc(cfg.t)} could not load</h3>`
    +`<div class="pacts"><button class="btn sm" id="retryView">Retry</button></div></div>`
    +`<div class="panelerr">The rest of the dashboard is fine.<code>${esc(e.message)}</code></div></div>`;
   const r=document.getElementById('retryView');if(r)r.onclick=()=>switchView(v)}
}

/* Re-running the current view after an action, keeping the section you were on.
   Deliberately not switchView: that paints a loading skeleton, and this runs on
   the fifteen-second Overview poll where a flash every fifteen seconds would be
   worse than the staleness it fixes. */
async function refresh(){
  const view=CURRENT;
  await loaders[view]();
  if(CURRENT===view)sectionize(view);
}

function bindShell(){
  document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>{document.body.classList.remove('railopen');switchView(b.dataset.view)});
  document.getElementById('closeDrawer').onclick=()=>drawer.classList.add('hidden');
  drawer.onclick=e=>{if(e.target===drawer)drawer.classList.add('hidden')};
  document.getElementById('railToggle').onclick=()=>document.body.classList.toggle('railopen');
  const filter=document.getElementById('navFilter');
  filter.oninput=()=>{
    const q=filter.value.trim().toLowerCase();
    document.querySelectorAll('#nav button').forEach(b=>{
      b.classList.toggle('hiddenmatch',!!q&&!b.textContent.toLowerCase().includes(q));
    });
    document.querySelectorAll('.navgroup').forEach(g=>{g.style.display=q?'none':''});
  };
  filter.onkeydown=e=>{
    if(e.key==='Escape'){filter.value='';filter.oninput();filter.blur()}
    if(e.key==='Enter'){const first=document.querySelector('#nav button:not(.hiddenmatch)');if(first){filter.value='';filter.oninput();switchView(first.dataset.view)}}
  };
  document.addEventListener('keydown',e=>{
    if(e.key==='/'&&document.activeElement!==filter&&!/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName||'')){e.preventDefault();filter.focus()}
    if(e.key==='Escape'&&!drawer.classList.contains('hidden'))drawer.classList.add('hidden');
  });
  window.onhashchange=()=>{
    const [v,s]=String(location.hash||'').replace('#','').split('/');
    if(v&&VIEWS[v]&&v!==CURRENT){if(s!==undefined)SECTION[v]=Number(s)||0;switchView(v)}
  };
}

/* Overview is the one view rewritten rather than re-laid-out, because it gained
   the only genuinely new thing in this release: a short list of conditions that
   already existed on other pages and that a GM had no reason to go looking for.
   Everything in it is a row from somewhere else, with the page it lives on. */
const SEV={bad:'bad',warn:'warn',info:'mut'};
function attentionPanel(items){
  if(!items||!items.length)
    return panel('Wants your attention',emptyState('Nothing is waiting.','No drafts to review, no system behind its interval, no overdue commissions.'),{count:'0'});
  const rows=items.map(a=>`<tr>
    <td><span class="${SEV[a.severity]||''}">●</span> ${esc(a.text)}</td>
    <td class="mut nowrap">${esc((VIEWS[a.view]||{}).t||a.view)}</td>
    <td class="right"><button class="btn sm" data-attend="${esc(a.view)}" data-kind="${esc(a.kind)}" data-system="${esc(a.system||'')}">${esc(a.action||'Open')}</button></td>
  </tr>`).join('');
  return panel('Wants your attention',`<div class="tablewrap"><table><tbody>${rows}</tbody></table></div>`,{count:`${items.length}`});
}

async function loadOverview(){
  const [d,cap]=await Promise.all([api('/api/overview'),api('/api/capabilities')]);
  const clock=d.clock||{};
  document.getElementById('worldClock').textContent=clock.display||'—';
  document.getElementById('schema').textContent=`Schema ${d.schema_version} · API v${cap.api_version} · ×${clock.scale} time`;
  const c=d.counts||{};const impl=cap.implementation||{};const att=d.attention||[];
  const behind=att.filter(a=>a.kind==='simulation_lag').length;
  document.getElementById('engineHealth').innerHTML=behind
    ?`<span class="status-dot bad"></span>${behind} system${behind===1?'':'s'} behind`
    :'<span class="status-dot"></span>simulation current';
  const badge=document.getElementById('questBadge');
  const drafts=att.find(a=>a.kind==='quest_drafts');
  if(badge){if(drafts){badge.textContent=String(drafts.count);badge.hidden=false}else{badge.hidden=true}}

  const coverage=Object.entries(cap.systems||{}).map(([name,s])=>
    `<div class="row"><span>${esc(name)}</span><b class="${s.available?'good':'warn'}">${s.available?'ready':'partial'}</b></div>`
    +(s.missing_tables?.length?`<div class="mut" style="font-size:11px">missing: ${esc(s.missing_tables.join(', '))}</div>`:'')).join('');

  app.innerHTML=`
  <div class="metrics">
    ${metric('Players',n(c.players),`${n(c.players_alive)} alive`)}
    ${metric('Living NPCs',n(c.npcs_alive),`${n(c.npc_marriages)} married · ${n(c.npc_descendants)} descendants`)}
    ${metric('Injured NPCs',n(c.npcs_injured),'',Number(c.npcs_injured)?'warn':'')}
    ${metric('Active events',n(c.active_world_events))}
    ${metric('Active battles',n(c.active_battles),`${n(c.active_wars)} war${Number(c.active_wars)===1?'':'s'}`)}
    ${metric('World history',n(c.world_history),`${n(c.rag_memories)} RAG memories`)}
    ${metric('Sects',n(c.sects),`${n(c.birth_families)} birth families`)}
    ${metric('Implementation gate',impl.schema_review_current?'PASS':'REVIEW','',impl.schema_review_current?'good':'warn')}
  </div>
  ${attentionPanel(att)}
  <h2>Simulation &amp; coverage</h2>
  <div class="grid2">
    <div>${panel('Simulation health',table([['System','system'],['Interval',r=>`${n(r.interval_game_minutes)}m`],
        ['Lag',r=>`<span class="${Number(r.lag_game_minutes)>Number(r.interval_game_minutes||0)?'bad':'good'}">${n(r.lag_game_minutes)}m</span>`],
        ['Runs','runs']],d.simulations||[],{empty:'No simulation state recorded.'}),{count:`${(d.simulations||[]).length} systems`})}</div>
    <div><div>${panel('Coverage & contract',`<div style="padding:12px 14px">
        <div class="row"><span>Implementation check</span><b class="${impl.schema_review_current?'good':'warn'}">${impl.schema_review_current?'pass':'review'}</b></div>
        <div class="row"><span>Schema / reviewed</span><b>${esc(cap.schema_version)} / ${esc(impl.reviewed_schema_version??'—')}</b></div>
        <div class="row"><span>Dashboard API</span><b>v${esc(cap.api_version)}</b></div>
        ${coverage}</div>`)}</div>
    ${panel('Current era',d.active_era?`<div style="padding:12px 14px">
        <h3 class="gold" style="font-family:Georgia,serif">${esc(d.active_era.name)}</h3>
        <p class="mut" style="margin:7px 0 0;line-height:1.6">${esc(d.active_era.description||'')}</p>
        <div class="row" style="margin-top:12px"><span>Started</span><b>${fmtGM(d.active_era.started_game_minute)}</b></div></div>`
      :emptyState('No active era.','Eras are opened by the world simulation or by a GM.'))}</div></div>
  </div>
  <h2>Recent canonical history</h2>
  ${timeline(d.recent_history||[])}`;

  document.querySelectorAll('[data-attend]').forEach(b=>b.onclick=async()=>{
    if(b.dataset.kind==='simulation_lag'&&b.dataset.system){
      if(!confirm(`Force a ${b.dataset.system} tick now?`))return;
      b.disabled=true;
      try{await adminPost('simulation.force',{system:b.dataset.system,steps:1,reason:'GM dashboard overview'});await refresh()}
      catch(e){alert(e.message);b.disabled=false}
      return;
    }
    switchView(b.dataset.attend);
  });
}

async function loadTimeline(){const d=await api('/api/timeline?limit=150');app.innerHTML=`<h2>Permanent World Timeline</h2>${filters(`<input id="q" placeholder="Search history"><select id="etype"><option value="">All event types</option>${d.event_types.map(x=>`<option>${esc(x)}</option>`).join('')}</select><select id="vis"><option value="">All visibility</option><option>public</option><option>participant</option><option>faction</option><option>hidden</option></select>`)}<div id="timelineRows">${timeline(d.rows)}</div>`;document.getElementById('applyFilters').onclick=async()=>{const p=new URLSearchParams({limit:'200',q:q.value,event_type:etype.value,visibility:vis.value});const x=await api('/api/timeline?'+p);document.getElementById('timelineRows').innerHTML=timeline(x.rows)}}
async function loadNPCs(){const d=await api('/api/npcs?limit=300&status=alive');app.innerHTML=`<h2>Active NPCs</h2>${filters(`<input id="q" placeholder="Name, job, goal"><select id="loc"><option value="">All locations</option>${d.locations.map(x=>`<option>${esc(x)}</option>`).join('')}</select><select id="fac"><option value="">All factions</option>${d.factions.map(x=>`<option>${esc(x)}</option>`).join('')}</select><select id="status"><option>alive</option><option>dead</option><option>all</option></select>`)}<div id="npcRows">${npcCards(d.rows)}</div>`;bindNpc();document.getElementById('applyFilters').onclick=async()=>{const p=new URLSearchParams({limit:'300',q:q.value,location:loc.value,faction:fac.value,status:status.value});const x=await api('/api/npcs?'+p);document.getElementById('npcRows').innerHTML=npcCards(x.rows);bindNpc()}}
function npcCards(rows){return `<div class="npc-grid">${rows.map(r=>`<div class="card npc-card" data-npc="${esc(r.npc_name)}"><h3><span class="status-dot ${r.status==='dead'?'dead':''}"></span>${esc(r.npc_name)}</h3><div class="tagline">${pill(`Realm ${r.realm_index}/${r.phase}`,'blue')}${pill(r.sect_rank||r.faction,'warn')}${r.injury_severity?pill(`Injury ${r.injury_severity}`,'bad'):''}</div><div class="row"><span>Location</span><b>${esc(r.current_location)}</b></div><div class="row"><span>Activity</span><b>${esc(r.activity)}</b></div><div class="row"><span>Mood</span><b>${esc(r.mood||'—')}</b></div><div class="row"><span>Goal</span><b>${esc(r.current_goal||'—')}</b></div><div class="row"><span>Family</span><b>${esc(r.relationship_status||'single')}${r.spouse_name?` · ${esc(r.spouse_name)}`:''}</b></div></div>`).join('')}</div>`}
function bindNpc(){document.querySelectorAll('.npc-card').forEach(el=>el.onclick=()=>showNpc(el.dataset.npc))}
async function showNpc(name){const d=await api('/api/npc?name='+encodeURIComponent(name));const r=d.npc||{};drawerBody.innerHTML=`<h2>${esc(r.npc_name)}</h2><div class="tagline">${pill(r.status)} ${pill(`Realm ${r.realm_index}/${r.phase}`,'blue')} ${pill(r.sect_rank,'warn')} ${r.injury_severity?pill(`Injury ${r.injury_severity}`,'bad'):''}</div><div class="section"><h3>Current life state</h3>${[['Location',r.current_location],['Faction',r.faction],['Profession',r.profession],['Activity',r.activity],['Mood',r.mood],['Goal',r.current_goal],['Goal progress',`${r.goal_progress||0}%`],['Health',r.health],['Injury',r.injury||'None'],['Spouse',r.spouse_name||'None'],['Children',r.children_count||0]].map(x=>`<div class="row"><span>${x[0]}</span><b>${esc(x[1])}</b></div>`).join('')}</div><div class="section"><h3>Social relations</h3>${table([['NPC A','npc_a'],['NPC B','npc_b'],['Type','relation_type'],['Affinity','affinity'],['Trust','trust'],['Grudge','grudge']],d.social)}</div><div class="section"><h3>Master / disciple</h3>${table([['Master','master_name'],['Disciple','disciple_name'],['Status','status'],['Reason','reason']],d.disciples)}</div><div class="section"><h3>Descendants</h3>${table([['Child','child_name'],['Parent A','parent_a'],['Parent B','parent_b'],['Root','spiritual_root'],['Status','status']],d.descendants)}</div><div class="section"><h3>Canonical history</h3>${timeline(d.history)}</div><details><summary>GM-only canonical NPC definition</summary><div class="code">${esc(JSON.stringify(d.gm_definition,null,2))}</div></details>`;drawer.classList.remove('hidden')}
async function showPlayer(uid){const d=await api('/api/player?user_id='+encodeURIComponent(uid));const r=d.player||{};if(!r.user_id){drawerBody.innerHTML='<div class="empty">Player not found.</div>';drawer.classList.remove('hidden');return}const scene=d.scene||{};drawerBody.innerHTML=`<h2>${esc(r.name)}</h2><div class="tagline">${pill(r.life_status,r.life_status==='alive'?'good':'bad')} ${pill(`Realm ${r.realm_index}/${r.phase}`,'blue')} ${pill(r.location)}</div><div class="section"><h3>Character sheet</h3>${[['Discord',r.discord_name],['Origin',r.origin],['Path',r.path],['Spiritual Root',r.spiritual_root],['Gender',r.gender],['Karma',r.karma_score],['Cultivation Realm',`${r.realm_index}/${r.phase}`],['Body Realm',`${r.body_realm_index}/${r.body_phase}`],['Vitality',`${r.vitality}/${r.vitality_max}`],['Qi',`${r.qi}/${r.qi_max}`],['Spirit Stones',n(r.spirit_stones)],['Sect',r.sect_name?`${r.sect_name} · ${r.rank_name}`:'Independent'],['Updated',r.updated_at?new Date(Number(r.updated_at)*1000).toLocaleString():'—']].map(x=>`<div class="row"><span>${x[0]}</span><b>${esc(x[1])}</b></div>`).join('')}</div><div class="section"><h3>Inventory</h3>${table([['Item','item_id'],['Qty','quantity']],d.inventory||[])}</div><div class="section"><h3>Cooldowns</h3>${table([['Action','action'],['Available at',r=>new Date(Number(r.available_at)*1000).toLocaleString()]],d.cooldowns||[])}</div><div class="section"><h3>Conditions</h3>${table([['Condition','name'],['Category','category'],['Severity','severity'],['',c=>`<button class="btn" data-clear-condition="${c.condition_id}">Clear</button>`]],d.conditions||[])}</div><div class="section"><h3>Scene state</h3>${scene.scene_type?[['Type',scene.scene_type],['Key',scene.scene_key],['Label',scene.scene_label],['Physical Location',scene.physical_location]].map(x=>`<div class="row"><span>${x[0]}</span><b>${esc(x[1])}</b></div>`).join(''):'<div class="empty">No scene state recorded.</div>'}</div>`;drawer.classList.remove('hidden');document.querySelectorAll('[data-clear-condition]').forEach(el=>el.onclick=async()=>{if(!confirm('Clear this condition?'))return;try{await adminPost('player.clear_condition',{user_id:uid,condition_id:Number(el.dataset.clearCondition),reason:'GM cleared condition'});await showPlayer(uid)}catch(e){alert(e.message)}})}
async function loadFamilies(){const d=await api('/api/families');const byFam=(arr,id)=>arr.filter(x=>Number(x.family_id)===Number(id));app.innerHTML=`<h2>Birth Families & Clan Politics</h2><div class="family-grid">${d.birth_families.map(f=>`<div class="card family-card"><h3>${esc(f.family_name)}</h3><div class="tagline">${pill(f.archetype)} ${pill(`Tier ${f.tier}`,'warn')} ${pill(f.line_status,f.line_status==='active'?'good':'bad')}</div><div class="row"><span>Head</span><b>${esc(f.head_title)} ${esc(f.head_name)}</b></div><div class="row"><span>Location</span><b>${esc(f.location)}</b></div><div class="row"><span>Wealth / Influence</span><b>${n(f.wealth)} / ${n(f.influence)}</b></div><div class="row"><span>Bloodline</span><b>${esc(f.bloodline_name)} (${n(f.bloodline_purity)}%)</b></div><div class="row"><span>Branches / Retainers</span><b>${n(f.branch_count)} / ${n(f.retainer_count)}</b></div><details><summary>Family tree</summary><div class="section"><b>Player line</b>${byFam(d.birth_family_players,f.family_id).map(x=>`<div>• ${esc(x.name)} · Realm ${x.realm_index}/${x.phase}</div>`).join('')||'<div class="muted">No player character.</div>'}</div><div class="section"><b>NPC relatives</b>${byFam(d.birth_family_npcs,f.family_id).map(x=>`<div>• ${esc(x.relation)} — ${esc(x.name)} (${esc(x.status)})</div>`).join('')||'<div class="muted">None.</div>'}</div><div class="section"><b>Branches</b>${byFam(d.clan_branches,f.family_id).map(x=>`<div>• ${esc(x.branch_name)} — ${esc(x.leader_name)} · strength ${x.martial_strength}</div>`).join('')||'<div class="muted">None.</div>'}</div><div class="section"><b>Discord household thread</b>${byFam(d.household_threads,f.family_id).map(x=>`<div>• Guild ${id(x.guild_id)} → thread ${id(x.thread_id)} (parent ${id(x.parent_channel_id)})</div>`).join('')||'<div class="muted">No thread bound yet.</div>'}</div><div class="section"><b>Currently present</b>${byFam(d.current_occupants,f.family_id).map(x=>`<div>• ${esc(x.name)} (${esc(x.life_status)})</div>`).join('')||'<div class="muted">No one currently inside the household scene.</div>'}</div></details></div>`).join('')}</div><h2>Player-Founded Families</h2>${table([['Family','name'],['Founder','founder_name'],['ID','family_id']],d.player_families)}<h2>Autonomous NPC Marriages & Descendants</h2>${table([['NPC','npc_name'],['Spouse','spouse_name'],['Children','children_count'],['Rank','sect_rank']],d.npc_marriages)}${table([['Child','child_name'],['Parent A','parent_a'],['Parent B','parent_b'],['Root','spiritual_root'],['Realm',r=>`${r.realm_index}/${r.phase}`],['Status','status']],d.npc_descendants)}`}
async function loadSects(){const d=await api('/api/sects');app.innerHTML=`<h2>Sect Politics</h2><div class="sect-grid">${d.sects.map(s=>`<div class="card sect-card"><h3>${esc(s.sect_name)}</h3><div class="tagline">${pill(s.alignment)} ${pill(s.specialty,'blue')}</div><div class="row"><span>Influence</span><b>${s.influence}</b></div><div class="bars"><span style="width:${Math.max(0,Math.min(100,s.influence))}%"></span></div><div class="row"><span>Cohesion / Resources</span><b>${s.cohesion} / ${s.resources}</b></div><div class="row"><span>Recruitment / Doctrine</span><b>${s.recruitment_pressure} / ${s.doctrine_pressure}</b></div><div class="row"><span>Policy</span><b>${esc(s.leader_policy)}</b></div><div class="row"><span>Players / NPCs</span><b>${s.player_members} / ${s.npc_members}</b></div><div class="row"><span>Prestige / Treasury</span><b>${n(s.prestige)} / ${n(s.treasury_stones)}</b></div></div>`).join('')}</div><h2>Internal Factions</h2>${table([['Sect','sect_name'],['Faction','faction_name'],['Agenda','agenda'],['Power','power'],['Loyalty','loyalty']],d.factions)}<h2>Inter-Sect Relations</h2>${table([['Sect A','sect_a'],['Sect B','sect_b'],['Score','relation_score'],['Type','relation_type'],['Treaty','treaty_status']],d.relations)}<h2>Recent Political Developments</h2>${table([['Sect','sect_name'],['Event','event_text'],['Severity','severity'],['Time',r=>fmtGM(r.game_minute)]],d.events)}`}
async function loadConflicts(){const d=await api('/api/conflicts');app.innerHTML=`<h2>Territorial Wars</h2>${table([['ID','war_id'],['Territory','territory_name'],['Attacker','attacker_key'],['Defender','defender_key'],['Status','status'],['Score',r=>`${r.attacker_score} : ${r.defender_score}`],['Siege','siege_progress'],['Resolution','resolution']],d.wars)}<h2>Battles</h2>${table([['Player','player_name'],['Opponent','npc_name'],['Location','location'],['Source','source'],['Status','status'],['HP',r=>`${r.player_hp}/${r.player_hp_max} vs ${r.npc_hp}/${r.npc_hp_max}`],['Outcome','final_outcome']],d.battles)}<h2>NPC Feuds</h2>${table([['NPC A','npc_a'],['NPC B','npc_b'],['Type','relation_type'],['Grudge','grudge'],['Affinity','affinity'],['Trust','trust']],d.npc_feuds)}<h2>Player Grudges & Bounties</h2>${table([['Player','player_name'],['Holder',r=>`${r.holder_type}:${r.holder_key}`],['Intensity','intensity'],['Reason','reason']],d.player_grudges)}${table([['Player','player_name'],['Amount','amount'],['Reason','reason'],['Hunter','hunter_name'],['Pursuit','pursuit_status'],['Pressure','pressure']],d.bounties)}<h2>Boss Encounters</h2>${table([['Boss','boss_name'],['Location','location'],['Status','status'],['HP',r=>`${r.boss_hp}/${r.boss_hp_max}`],['Round','round_index'],['Phase','phase_index']],d.bosses)}`}
async function loadEvents(){const d=await api('/api/events');const rot=d.secret_realm_rotation||{};app.innerHTML=`<h2>Secret Realm Rotation</h2><div class="cards">${[['Realms on rotation',rot.realms],['Last opened',rot.last_realm_name?`${rot.last_realm_name} · ${rot.last_location}`:'none yet'],['Next to open',rot.next_realm_name?`${rot.next_realm_name} · ${rot.next_location}`:'—'],['Next opening',rot.last_game_minute?fmtGM(rot.next_game_minute):'on the next tick']].map(x=>`<div class="card"><small>${x[0]}</small><div class="metric" style="font-size:1rem">${esc(String(x[1]??'—'))}</div></div>`).join('')}</div><h2>World Events</h2>${table([['Title','title'],['Type','event_type'],['Location','location'],['Active',r=>pill(r.active?'ACTIVE':'closed',r.active?'good':'muted')],['Ends',r=>new Date(Number(r.ends_at)*1000).toLocaleString()]],d.world_events)}<h2>Regional Civilization State</h2>${table([['Location','location'],['World','world_name'],['Population',r=>n(r.population)],['Prosperity','prosperity'],['Security','security'],['Resources','spirit_resources'],['Food','food_supply'],['Migration','migration_pressure'],['Unrest','unrest']],d.regions)}<h2>Recent Regional Incidents</h2>${table([['Location','location'],['Event','event_text'],['Severity','severity'],['Time',r=>fmtGM(r.game_minute)]],d.civilization_events)}<h2>World Eras</h2>${table([['Era','name'],['Description','description'],['Active',r=>r.active?'Yes':'No'],['Started',r=>fmtGM(r.started_game_minute)]],d.eras)}`}
async function loadPlayers(){const d=await api('/api/players');app.innerHTML=`<h2>Players</h2>${table([['Name','name'],['Discord','discord_name'],['Life','life_status'],['Realm',r=>`${r.realm_index}/${r.phase}`],['Body',r=>`${r.body_realm_index}/${r.body_phase}`],['Location','location'],['Sect',r=>r.sect_name?`${r.sect_name} · ${r.rank_name}`:'Independent'],['Vitality',r=>`${r.vitality}/${r.vitality_max}`],['Qi',r=>`${r.qi}/${r.qi_max}`],['Karma','karma_score'],['Memories','memory_count']],d.players)}<h2>Recent Player World Actions</h2>${table([['Player','player_name'],['Action','action_type'],['Target',r=>`${r.target_type}:${r.target_key}`],['Location','location'],['Severity','severity'],['Time',r=>fmtGM(r.game_minute)]],d.recent_actions)}<h2>Scene Activity</h2>${table([['Player','player_name'],['Messages','player_messages'],['Last Scene','last_scene_id'],['Last Real Time',r=>r.last_scene_at?new Date(Number(r.last_scene_at)*1000).toLocaleString():'—']],d.scene_activity)}`}
async function loadCultivation(){const d=await api('/api/cultivation');const s=d.summary||{};app.innerHTML=`<h2>Cultivation & Aptitudes</h2><div class="cards">${[['Cultivators',s.roots],['Mutated Roots',s.mutated_roots],['Bloodlines',s.bloodlines],['Active Seclusion',s.active_seclusion],['Uncleared Tribulations',s.uncleared_tribulations],['Ruptured Qi Bodies',s.ruptured_qi_bodies]].map(x=>`<div class="card"><small>${x[0]}</small><div class="metric">${n(x[1])}</div></div>`).join('')}</div><h2>Spiritual Roots</h2>${table([['Player','name'],['Life','life_status'],['Realm',r=>`${r.realm_index}/${r.phase}`],['Path','path'],['Grade',r=>pill(r.root_grade||'Legacy','purple')],['Purity',r=>r.purity===undefined?'—':`${r.purity}%`],['Elements',r=>esc(jsonText(r.elements_json,r.legacy_root||'—'))],['Mutation',r=>esc(r.mutation||'—')],['Stability',r=>r.stability===undefined?'—':`${r.stability}%`],['Refinement',r=>r.refinement_progress===undefined?'—':`${r.refinement_progress}%`],['Compatibility',r=>r.compatibility===undefined?'—':`${r.compatibility}%`]],d.roots)}<h2>Bloodlines</h2>${table([['Player','player_name'],['Bloodline','name'],['Affinity','affinity'],['Purity',r=>`${r.purity}%`],['State','state'],['Evolution','evolution_stage'],['Progress',r=>`${r.progress}%`],['Mutation','mutation']],d.bloodlines)}<h2>Physiques</h2>${table([['Player','player_name'],['Physique','name'],['State','state'],['Evolution','evolution_stage'],['Progress',r=>`${r.progress}%`],['Stability',r=>`${r.stability}%`],['Instability','instability']],d.physiques)}<h2>Tribulations</h2>${table([['Player','player_name'],['Gate','gate_realm_index'],['Preparation','preparation'],['Attempts','attempts'],['Cleared',r=>pill(r.cleared?'CLEARED':'OPEN',r.cleared?'good':'warn')],['Last Result','last_result'],['Updated',r=>fmtGM(r.updated_game_minute)]],d.tribulations)}<h2>Recent Tribulation Attempts</h2>${table([['Player','player_name'],['Gate','gate_realm_index'],['Preparation','preparation_used'],['Result',r=>pill(r.success?'SUCCESS':'FAIL',r.success?'good':'bad')],['Waves',r=>esc(jsonText(r.waves_json))],['Time',r=>fmtGM(r.created_game_minute)]],d.tribulation_attempts)}<h2>Dao Progress</h2>${table([['Player','player_name'],['Dao','dao_id'],['Progress','progress']],d.dao)}<h2>Law Comprehension</h2>${table([['Player','player_name'],['Law','law_id'],['Comprehension','comprehension'],['Insights','insights']],d.laws)}<h2>Realm Perfection</h2>${table([['Player','player_name'],['Realm','realm_index'],['Active',r=>r.active?'Yes':'No'],['Completed',r=>r.completed?'Yes':'No'],['Progress',r=>`${r.progress}%`],['Training','training_progress'],['Quests','completed_quests']],d.realm_perfection)}<h2>Body Realm Perfection</h2>${table([['Player','player_name'],['Realm','realm_index'],['Active',r=>r.active?'Yes':'No'],['Completed',r=>r.completed?'Yes':'No'],['Progress',r=>`${r.progress}%`],['Training','training_progress'],['Quests','completed_quests']],d.body_realm_perfection)}<h2>Seclusion</h2>${table([['Player','player_name'],['Mode','mode'],['Status','status'],['Start','start_location'],['Environment','environment_mult'],['Gain','accumulated_gain'],['From',r=>fmtGM(r.started_game_minute)],['Until',r=>fmtGM(r.ends_game_minute)]],d.seclusion)}<h2>Qi Bodies</h2>${table([['Player','player_name'],['Realm',r=>`${r.realm_index}/${r.phase}`],['Qi',r=>pill(r.qi_type||'spirit',(r.qi_type||'spirit')==='death'?'warn':'good')],['Dantian',r=>`${n(r.qi)} / ${n(r.qi_max)}`],['Purity',r=>`${r.purity}%`],['Meridians',r=>`${r.meridians_open}/108`],['Ruptured',r=>r.meridians_damaged?pill(r.meridians_damaged,'bad'):'—'],['Vessel',r=>pill(r.dantian_state||'intact',(r.dantian_state||'intact')==='intact'?'good':'warn')],['Corruption',r=>Number(r.corruption||0)?pill(`${r.corruption}/100`,Number(r.corruption)>=60?'bad':'warn'):'—'],['Ghost Form',r=>Number(r.ghost_form||0)||(r.qi_type==='death')?esc(String(r.ghost_form??0)):'—'],['Settled',r=>fmtGM(r.settled_game_minute)]],d.qi_bodies)}`}

async function loadCrafting(){const d=await api('/api/crafting');const s=d.summary||{};app.innerHTML=`<h2>Crafting, Companions & Property</h2><div class="cards">${[['Profession Tracks',s.profession_tracks],['Alchemy Users',s.alchemy_users],['Active Beasts',s.active_beasts],['Awakened Artifacts',s.awakened_artifacts],['Properties',s.properties],['Deployed Arrays',s.deployed_arrays]].map(x=>`<div class="card"><small>${x[0]}</small><div class="metric">${n(x[1])}</div></div>`).join('')}</div><h2>Profession Progress</h2>${table([['Player','player_name'],['Profession','profession'],['Level','level'],['XP','xp'],['Successes','successes'],['Failures','failures'],['Quality','quality_points']],d.professions)}<h2>Alchemy State</h2>${table([['Player','player_name'],['Toxicity',r=>pill(r.pill_toxicity,r.pill_toxicity>=80?'bad':r.pill_toxicity>=50?'warn':'good')],['Refinements','total_refinements'],['Successes','successful_refinements'],['Flawless','flawless_refinements'],['Best Margin','best_margin'],['Last Quality','last_quality']],d.alchemy)}<h2>Recent Alchemy Batches</h2>${table([['Player','player_name'],['Recipe','recipe_name'],['Quality','quality'],['Margin','margin'],['Success',r=>r.success?'Yes':'No'],['Output',r=>esc(jsonText(r.output_json))],['Location','location'],['Time',r=>fmtGM(r.game_minute)]],d.alchemy_batches)}<h2>Spirit Beasts</h2>${table([['Player','player_name'],['Beast','name'],['Species','species'],['Rank','rank'],['Element','element'],['Bloodline','bloodline'],['Evolution','evolution_stage'],['Loyalty','loyalty'],['Contract','contract_type'],['Active',r=>r.active?'Yes':'No']],d.spirit_beasts)}<h2>Artifact Bonds</h2>${table([['Player','player_name'],['Item','item_id'],['Bond','bond_level'],['Resonance','resonance'],['Awakened',r=>r.awakened?'Yes':'No'],['Spirit','spirit_name'],['Temperament','temperament']],d.artifact_bonds)}<h2>Cave Abodes & Properties</h2>${table([['Owner','owner_name'],['Name','name'],['Type','property_type'],['Base','base_location'],['Grade','grade'],['Cultivation','cultivation_level'],['Alchemy','alchemy_level'],['Forge','forge_level'],['Formation','formation_level'],['Defense','defense_level'],['Storage','storage_level'],['Garden','herb_garden_level'],['Beast Pen','beast_pen_level']],d.cave_abodes)}<h2>Sect Abodes</h2>${table([['Owner','owner_name'],['Sect','sect_name'],['Name','name'],['Base','base_location'],['Location Key','location_key']],d.sect_abodes)}<h2>Personal Worlds</h2>${table([['Owner','owner_name'],['Name','name'],['Stability','stability'],['Access','access_mode'],['Laws',r=>esc(jsonText(r.laws_json))]],d.personal_worlds)}<h2>Deployed Formations / Arrays</h2>${table([['Name','name'],['Location','location'],['Owner','owner_name'],['Sect','sect_name'],['Item','item_id'],['Effect',r=>esc(jsonText(r.effect_json))],['Starts',r=>fmtGM(r.starts_game_minute)],['Ends',r=>fmtGM(r.ends_game_minute)]],d.deployed_arrays)}<h2>Equipment Instances</h2>${table([['Player','player_name'],['Item','item_id'],['Slot','slot'],['Quality','quality'],['Durability',r=>`${r.durability}/${r.max_durability}`],['Equipped',r=>r.equipped?'Yes':'No']],d.equipment)}`}

async function loadExploration(){const d=await api('/api/exploration');const s=d.summary||{};app.innerHTML=`<h2>Exploration & Travel Systems</h2><div class="cards">${[['Active Events',s.active_events],['Secret Realms',s.active_secret_realms],['Recent Discoveries',s.discoveries_shown],['Beast Encounters',s.active_beast_encounters],['Traveling Caravans',s.traveling_caravans]].map(x=>`<div class="card"><small>${x[0]}</small><div class="metric">${n(x[1])}</div></div>`).join('')}</div><h2>Exploration Events</h2>${table([['Title','title'],['Category','category'],['Kind','kind'],['Visibility','visibility'],['Location','location'],['Severity','severity'],['State','state'],['Stage','stage'],['Participants','participants'],['Time',r=>fmtGM(r.created_game_minute)]],d.events)}<h2>Event Participants</h2>${table([['Player','player_name'],['Event','title'],['Location','location'],['Stage','stage'],['Status','status'],['Joined',r=>fmtGM(r.joined_game_minute)]],d.participants)}<h2>Secret Realm Runs</h2>${table([['Player','player_name'],['Realm','realm_id'],['Event','event_key'],['Room','room_index'],['Danger','danger'],['Active',r=>r.active?'Yes':'No'],['Location','location'],['Expires',r=>new Date(Number(r.expires_at)*1000).toLocaleString()]],d.secret_realms)}<h2>Recent Location Discoveries</h2>${table([['Player','player_name'],['Location','location'],['Kind','discovery_kind'],['Time',r=>fmtGM(r.discovered_game_minute)]],d.discoveries)}<h2>Wild Beast Encounters</h2>${table([['Player','player_name'],['Species','species'],['Rank','rank'],['Element','element'],['Bloodline','bloodline'],['TN','taming_tn'],['Location','location'],['Status','status'],['Time',r=>fmtGM(r.created_game_minute)]],d.wild_beast_encounters)}<h2>Caravans</h2>${table([['ID','caravan_id'],['Owner',r=>`${r.owner_type}:${r.owner_key}`],['Route',r=>`${r.origin} → ${r.destination}`],['Status','status'],['Risk','risk'],['Escort','escort_strength'],['Concealment','concealment'],['Smuggling','smuggling'],['Toll','toll_paid'],['Outcome','outcome'],['Payout','payout_final'],['Depart',r=>fmtGM(r.depart_game_minute)],['Arrive',r=>fmtGM(r.arrive_game_minute)]],d.caravans)}<h2>Discord Expedition Threads</h2>${table([['Player','player_name'],['Guild','guild_id'],['Thread','thread_id'],['Parent','parent_channel_id'],['Last Location','last_location']],d.expedition_threads)}<h2>Forged Quests</h2><div class="empty">Forged quests moved to the <b>Quests</b> page, where they can be edited, previewed and approved beside every other definition.</div>`}

async function loadEconomy(){const d=await api('/api/economy');const s=d.summary||{};app.innerHTML=`<h2>Economy, Auctions & Crime</h2><div class="cards">${[['Market Rows',s.markets],['Active Auctions',s.active_auctions],['Black Markets',s.active_black_markets],['Open Crimes',s.open_crimes]].map(x=>`<div class="card"><small>${x[0]}</small><div class="metric">${n(x[1])}</div></div>`).join('')}</div><h2>Dynamic Markets</h2>${table([['Location','location'],['World','world_name'],['Item','item_id'],['Currency','currency_id'],['Base','base_price'],['Supply','supply'],['Demand','demand'],['Index',r=>Number(r.price_index||0).toFixed(2)],['Time',r=>fmtGM(r.last_game_minute)]],d.markets)}<h2>Recent Economy Events</h2>${table([['Location','location'],['Item','item_id'],['Event','event_text'],['Time',r=>fmtGM(r.game_minute)]],d.events)}<h2>Auctions</h2>${table([['ID','auction_id'],['House','house_id'],['Seller','seller_name'],['Item','item_id'],['Qty','quantity'],['Start','starting_bid'],['Current','current_bid'],['Bidder','bidder_name'],['Bids','bid_count'],['Anonymous',r=>r.anonymous?'Yes':'No'],['Active',r=>r.active?'Yes':'No'],['Ends',r=>new Date(Number(r.ends_at)*1000).toLocaleString()]],d.auctions)}<h2>Black Market Posts</h2>${table([['World','world_name'],['Location','location'],['Heat','heat'],['Active',r=>r.active?'Yes':'No'],['Opens',r=>fmtGM(r.opens_game_minute)],['Closes',r=>fmtGM(r.closes_game_minute)]],d.black_market_posts)}<h2>Black Market Stock</h2>${table([['World','world_name'],['Item','item_id'],['Currency','currency_id'],['Price','unit_price'],['Qty','quantity'],['Legal Status','legal_status']],d.black_market_stock)}<h2>Crime Records</h2>${table([['Player','player_name'],['Jurisdiction','jurisdiction'],['Crime','crime_type'],['Severity','severity'],['Evidence','evidence'],['Status','status'],['Description','description'],['Time',r=>fmtGM(r.created_game_minute)]],d.crimes)}<h2>Trades Between Cultivators</h2><div id="tradeOut"></div>${table([['ID','offer_id'],['From','from_name'],['To','to_name'],['Inn','location'],['Gives',r=>tradeSide(r.give_json,r.give_stones)],['Wants',r=>tradeSide(r.want_json,r.want_stones)],['Status',r=>pill(String(r.status||'').toUpperCase(),r.status==='open'?'good':(r.status==='accepted'?'warn':'muted'))],['Expires',r=>fmtGM(r.expires_game_minute)],['',r=>r.status==='open'?`<button class="danger" data-void="${esc(r.offer_id)}">Void</button>`:'']],d.trades)}`;document.querySelectorAll('[data-void]').forEach(b=>b.onclick=async()=>{if(!confirm(`Void trade offer #${b.dataset.void}? Nothing has moved; the offer simply closes and the audit log records it.`))return;const out=document.getElementById('tradeOut');try{const r=await adminPost('trade.void',{offer_id:Number(b.dataset.void),reason:'GM dashboard: voided from the Economy page'});out.innerHTML=resultBox(r.result??r);setTimeout(()=>loadEconomy().catch(()=>{}),900)}catch(e){out.innerHTML=resultBox(e.message,false)}})}
function tradeSide(json,stones){let items={};try{items=JSON.parse(json||'{}')||{}}catch{}const parts=Object.entries(items).map(([k,v])=>`${k} ×${v}`);if(Number(stones||0))parts.push(`${stones} stones`);return parts.length?parts.join(', '):'nothing'}

async function loadParty(){const d=await api('/api/party');app.innerHTML=`<h2>Parties & Formations</h2><h3>Parties</h3>${table([['Party','name'],['Leader','leader_name'],['Status','status']],d.parties)}<h3>Members</h3>${table([['Party','party_id'],['Player','name'],['Role','role']],d.party_members)}<h3>Formations</h3>${table([['Party','party_id'],['Formation','name'],['Stance','stance'],['Cohesion','cohesion'],['Active',r=>r.active?'Yes':'No']],d.party_formations)}<h3>Formation Positions</h3>${table([['Formation','formation_id'],['Player','name'],['Position','position']],d.formation_positions)}`}


async function loadPvp(){const d=await api('/api/pvp');app.innerHTML=`<h2>PvP</h2><h3>Challenges</h3>${table([['Challenger','challenger_name'],['Target','target_name'],['Stakes','stakes'],['Status','status']],d.pvp_challenges)}<h3>Matches</h3>${table([['Player 1','player1_name'],['Player 2','player2_name'],['HP',r=>`${r.player1_hp} : ${r.player2_hp}`],['Turn','turn_user_id'],['Status','status'],['Winner','winner_name']],d.pvp_matches)}`}


async function loadConditions(){const d=await api('/api/conditions');app.innerHTML=`<h2>Active Conditions</h2>${table([['Player','player_name'],['Condition','name'],['Category','category'],['Severity','severity'],['Source',r=>`${r.source_type}:${r.source_id}`],['Status','life_status']],d.character_conditions)}`}
async function loadThreads(){const d=await api('/api/threads');const s=d.summary||{};const kinds=Object.entries(s.by_kind||{});app.innerHTML=`<h2>Discord Threads</h2><div class="cards"><div class="card"><small>Total Threads</small><div class="metric">${n(s.total_threads)}</div></div>${kinds.map(([k,c])=>`<div class="card"><small>${esc(k)}</small><div class="metric">${n(c)}</div></div>`).join('')}</div>${filters(`<select id="kind"><option value="">All types</option>${kinds.map(([k])=>`<option>${esc(k)}</option>`).join('')}</select>`)}<div id="threadRows">${threadTable(d.threads)}</div>`;document.getElementById('applyFilters').onclick=()=>{const want=kind.value;document.getElementById('threadRows').innerHTML=threadTable(want?d.threads.filter(r=>r.kind===want):d.threads)}}
function threadTable(rows){return table([['Type','kind'],['Owner / Context','owner_name'],['Detail','detail'],['Status',r=>r.status?pill(r.status,r.status==='active'?'good':''):'—'],['Thread ID','thread_id'],['Parent Channel','parent_channel_id'],['Last Activity',r=>r.updated_at?new Date(Number(r.updated_at)*1000).toLocaleString():'—'],['Open',r=>r.jump_url?`<a href="${esc(r.jump_url)}" target="_blank" rel="noopener">Open ↗</a>`:'<span class="muted">No guild bound</span>']],rows)}


async function loadDynasties(){const d=await api('/api/dynasties');const s=d.summary||{};app.innerHTML=`<h2>Samsara & Dynasty Systems</h2><div class="cards">${[['Active Reincarnations',s.active_reincarnations],['Dynasty Records',s.dynasty_records],['Open Leads',s.open_leads],['Active Quests',s.active_quests],['Contested Claims',s.contested_claims],['Active Conflicts',s.active_conflicts]].map(x=>`<div class="card"><small>${x[0]}</small><div class="metric">${n(x[1])}</div></div>`).join('')}</div><h2>Reincarnation State</h2>${table([['Player','current_name'],['Previous','previous_name'],['Mode','rebirth_mode'],['Target World','target_world'],['Lives','samsara_lives_count'],['Memory','memory_retention'],['Talent','talent_retention'],['Insight','insight_retention'],['Legacy','legacy_points'],['Trait','special_trait'],['Fortune','karmic_fortune'],['Active',r=>r.active?'Yes':'No'],['Ready',r=>fmtGM(r.ready_game_minute)]],d.reincarnation)}<h2>Soul Legacy</h2>${table([['Player','player_name'],['Incarnations','incarnation_count'],['Legacy Points','legacy_points'],['Memory Seed','memory_seed'],['Talent Echo','talent_echo'],['Law Echo','law_echo'],['Insight Echo','insight_echo'],['Fortune','karmic_fortune'],['Trait','special_trait'],['Awakened Memory','awakened_memory']],d.soul_legacy)}<h2>Dynasty History</h2>${table([['Player','player_name'],['Life','incarnation_number'],['From','source_family_name'],['Source World','source_world'],['To','destination_family_name'],['Destination World','destination_world'],['Lineage','lineage_status'],['Blood',r=>r.blood_continuity?'Yes':'No'],['Event','event_kind'],['Investigation','investigation_level'],['Summary','summary']],d.history)}<h2>Ancestral Leads</h2>${table([['Player','player_name'],['Lead','name'],['Kind','lead_kind'],['Location','location'],['World','world_name'],['Status','status'],['Danger','danger'],['Evidence','evidence_weight'],['Retainer','retainer_name']],d.leads)}<h2>Investigation Quests</h2>${table([['Player','player_name'],['Quest','title'],['Kind','quest_kind'],['Lead','lead_name'],['Location','location'],['Status','status'],['Progress',r=>`${r.progress}/${r.target}`],['Evidence','reward_evidence'],['Hostile Cause',r=>r.hostile_cause?'Yes':'No'],['Culprit','culprit_name']],d.quests)}<h2>Dynasty Claims</h2>${table([['Player','player_name'],['Type','claim_type'],['Dynasty','dynasty_name'],['Target Family','target_family_name'],['World','target_world'],['Status','status'],['Legitimacy','legitimacy'],['Support','support'],['Opposition','opposition'],['Blood Based',r=>r.blood_based?'Yes':'No'],['Resolution','resolution']],d.claims)}<h2>Dynasty Conflicts</h2>${table([['Player','player_name'],['Type','conflict_type'],['Dynasty','dynasty_name'],['Claim','claim_type'],['Opponent','opponent_name'],['Family','opponent_family_name'],['Stakes','stakes'],['Status','status'],['Score',r=>`${r.player_progress} : ${r.opponent_progress}`],['Rounds','rounds'],['Last Tactic','last_tactic'],['Outcome','outcome']],d.conflicts)}`}

async function loadRag(){const d=await api('/api/rag?limit=200');app.innerHTML=`<h2>RAG Memory Inspector</h2><div class="cards"><div class="card"><small>Player memories</small><div class="metric">${n(d.memories.length)}</div></div><div class="card"><small>Safe canon documents</small><div class="metric">${n(d.canon_count)}</div></div><div class="card"><small>World-history events</small><div class="metric">${n(d.history_count)}</div></div></div>${filters(`<input id="q" placeholder="Search memory"><select id="uid"><option value="">All players</option>${d.players.map(x=>`<option value="${x.user_id}">${esc(x.name)}</option>`).join('')}</select><input id="npc" placeholder="NPC exact name">`)}<div id="ragRows">${ragTable(d.memories)}</div>`;document.getElementById('applyFilters').onclick=async()=>{const p=new URLSearchParams({limit:'250',q:q.value,user_id:uid.value,npc:npc.value});const x=await api('/api/rag?'+p);document.getElementById('ragRows').innerHTML=ragTable(x.memories)}}
function ragTable(rows){return table([['Player','player_name'],['Kind','memory_kind'],['Summary','summary'],['Salience','salience'],['Location','location'],['NPC','npc_name'],['Source','source'],['Time',r=>fmtGM(r.game_minute)],['Recalls','recalled_count']],rows)}
async function loadDecisions(){const d=await api('/api/decisions?limit=250');app.innerHTML=`<h2>Autonomous NPC Decisions</h2>${table([['NPC','npc_name'],['Status','status'],['Location','current_location'],['Faction','faction'],['Rank','sect_rank'],['Activity','activity'],['Mood','mood'],['Goal','current_goal'],['Progress',r=>`${r.goal_progress}%`],['Recent autonomous development','recent_event']],d.minds)}<h2>Autonomous Outcomes Written to History</h2>${timeline(d.history)}<h2>Simulation Clocks</h2>${table([['System','system'],['Last minute','last_game_minute'],['Interval','interval_game_minutes'],['Runs','runs']],d.simulation)}`}

/* AI routing. Everything here is read from the bot process's own counters via
   the control plane - the router's chains and audit verdicts are in memory, not
   in SQLite - so this page reports and never commands. */
async function loadAiRouting(){
 const d=await api('/api/ai_routing');
 if(!d.control_available){app.innerHTML=`<h2>AI Routing</h2><div class="card badbox"><b>The bot's router is unreachable.</b><div class="muted">${esc(d.message||'The dashboard cannot reach the Python bot.')}</div></div>`;return}
 const chains=d.chains||{}, tiers=d.tiers||{}, lim=d.limiter||{}, google=d.google_route||{}, audit=d.audit||{}, models=d.models||[];
 const used=Number(lim.used_today||0), cap=Number(lim.max_requests_per_day||0);
 const req=Object.values(tiers).reduce((a,t)=>a+Number(t.requests||0),0);
 const served=Object.values(tiers).reduce((a,t)=>a+Number(t.served||0),0);
 // The number the whole page exists to explain: how much play is running on
 // template prose because no route answered.
 const fellBack=req-served, pct=req?Math.round(fellBack/req*100):0;
 const ago=t=>{if(!t)return '—';const s=Math.max(0,Date.now()/1000-Number(t));if(s<90)return `${Math.round(s)}s ago`;if(s<5400)return `${Math.round(s/60)}m ago`;return `${Math.round(s/3600)}h ago`};
 const chainRow=(tier,list)=>`<div class="card"><small>${esc(tier)} chain</small><div>${(list||[]).map((m,i)=>{
   const row=models.find(x=>x.model===m)||{};
   const cls=row.probe_retired?'bad':row.never_succeeded?'warn':row.successes?'good':'';
   return `${i?' <span class="muted">→</span> ':''}${pill(m,cls)}`}).join('')||'<span class="muted">empty</span>'}</div></div>`;
 const auditLine=()=>{
  if(!audit.at)return `<div class="muted">The daily check has not run yet this process.</div>`;
  if(audit.skipped)return `<div class="warnbox">Last check stood down — ${esc(audit.skipped)}</div>`;
  if(audit.fail_open)return `<div class="badbox"><b>Every route failed at once.</b> Treated as a local fault (proxy, firewall, revoked key), so none were retired.</div>`;
  const ret=audit.retired||[];
  return ret.length
   ? `<div class="warnbox"><b>${n(ret.length)} of ${n((audit.checked||[]).length)} retired</b> until the next pass: ${ret.map(m=>pill(m,'bad')).join(' ')}<div class="muted">They answered 401/403/404, so narration no longer spends a slot on them.</div></div>`
   : `<div class="goodbox">All ${n((audit.checked||[]).length)} routes reachable, checked ${ago(audit.at)}.</div>`};
 // No hero: sectionize() already renders the view's title and blurb from the
 // registry above, and repeating them here just pushed the numbers down a screen.
 app.innerHTML=`<div class="tagline">${pill(d.enabled?'narration enabled':'narration disabled',d.enabled?'good':'bad')}${pill(d.require_free?'free routes only':'paid routes allowed',d.require_free?'good':'warn')}${d.tls_failures?pill(`${n(d.tls_failures)} TLS failures`,'bad'):''}</div>
 <div class="cards">
  <div class="card"><small>Narration requests</small><div class="metric">${n(req)}</div></div>
  <div class="card"><small>Served by AI</small><div class="metric">${n(served)}</div></div>
  <div class="card"><small>Procedural fallbacks</small><div class="metric ${fellBack?'bad':''}">${n(fellBack)}</div><small>${pct}% of requests</small></div>
  <div class="card"><small>Daily free budget</small><div class="metric">${n(used)}/${n(cap)}</div><small>refused ${n(lim.rejected||0)} · ${d.credits_topped_up?'ten dollars on the account':'under ten dollars of credit'}</small></div>
 </div>
 <h2>What the calls are for</h2>
 <p class="muted">Since v0.31.0 a live call is made for three reasons only: an NPC answering a player, an epic beat, or an explicit ask (the picker's <b>Narrate it</b>, the button under an exploration or hunt, an @mention). Everything else reads from the procedural pool by design.</p>
 <div class="cards">
  ${['dialogue','epic','narrate_it','forge','monitor'].map(k=>{const p=(d.purposes||{})[k]||{};return `<div class="card"><small>${esc({dialogue:'Dialogue',epic:'Epic beats',narrate_it:'Narrate it',forge:'Quest forge',monitor:'Chat digest'}[k])}</small><div class="metric">${n(p.served||0)}</div><small>of ${n(p.requests||0)} asked</small></div>`}).join('')}
  <div class="card"><small>Procedural by design</small><div class="metric">${n((d.narrator||{}).procedural_by_default||0)}</div><small>explore and hunt results served from the pool</small></div>
 </div>
 <h2>Per-player budget</h2>
 <p class="muted">One bucket per player, every door: typed lines, slash commands and hub buttons, and Narrate-it asks. A refusal is answered, never queued.</p>
 <div class="cards">
  ${Object.entries((d.user_budget||{}).doors||{}).map(([door,c])=>`<div class="card"><small>${esc({typed:'Typed play',slash:'Slash and hub',narrate_it:'Narrate it'}[door]||door)}</small><div class="metric ${c.refused?'warn':''}">${n(c.refused||0)} refused</div><small>${n(c.granted||0)} granted</small></div>`).join('')||'<div class="card"><small>No door has been used yet.</small></div>'}
  <div class="card"><small>Bucket</small><div class="metric">${n((d.user_budget||{}).burst||0)}</div><small>burst, refilling ${n((d.user_budget||{}).per_minute||0)} a minute · ${n((d.user_budget||{}).tracked_users||0)} players tracked</small></div>
 </div>
 <h2>Chains</h2><div class="cards">${Object.entries(chains).map(([t,l])=>chainRow(t,l)).join('')}</div>
 <h2>Daily route check</h2>${auditLine()}
 <h2>Google AI Studio</h2>${google.configured
   ? (google.available
     ? `<div class="goodbox">Route <b>on</b> (${esc(google.model||'')}) — your own key, its own quota, outside the OpenRouter daily budget. It is never retired by the daily check.</div>`
     : `<div class="badbox"><b>A key is set but the route is off.</b><div class="muted">${esc(google.last_error||'the google-genai SDK could not be loaded')}</div></div>`)
   : `<div class="muted">No key configured. Narration runs on OpenRouter only; setting GOOGLE_AI_STUDIO_API_KEY adds a route that does not spend the daily budget.</div>`}
 <h2>Routes</h2>${table([
   ['Route','model'],
   ['State',r=>r.probe_retired?pill('retired','bad'):r.cooling_down?pill('cooling','warn'):r.never_succeeded?pill('never succeeded','warn'):r.successes?pill('serving','good'):pill('untried','')],
   ['Attempts','attempts'],['OK','successes'],['Failed','failures'],
   ['Skipped',r=>n(Number(r.skipped_cooling||0)+Number(r.skipped_route_limit||0)+Number(r.skipped_probe_retired||0))],
   ['Scratchpad',r=>r.scratchpad_rejected?pill(r.scratchpad_rejected,'warn'):'0'],
   ['Empty','empty_responses'],
   ['Probe',r=>r.probe_ok===null||r.probe_ok===undefined?'<span class="muted">not probed</span>':(r.probe_ok?pill('ok','good'):pill('failed','bad'))],
   ['Served by','last_provider'],
   ['Own key',r=>r.byok===null||r.byok===undefined?'—':(r.byok?pill('yes','good'):pill('shared pool','warn'))],
   ['Last success',r=>ago(r.last_success_at)],
   ['Last error',r=>r.last_error?`<span class="muted" title="${esc(r.last_error)}">${esc(String(r.last_error).slice(0,80))}</span>`:'—'],
 ],models,{empty:'No route has been called yet.',why:'Counters start empty on every bot restart.'})}`;
}

async function loadDiscordSetup(){
 const d=await api('/api/discord');
 if(!d.control_available){app.innerHTML=`<h2>Discord Server Setup</h2><div class="card badbox"><b>Discord bot control is unavailable.</b><div class="muted">${esc(d.message||'The dashboard cannot reach the Python Discord bot.')}</div></div>`;return}
 const guild=d.guild||{}, bot=d.bot||{}, perms=d.permissions||[], base=d.base_channels||[], realms=d.realm_hubs||[], halls=d.auction_halls||[], channels=d.text_channels||[];
 const pgood=perms.filter(x=>x.ok).length;
 // Compare channel IDs as strings. They are Discord snowflakes, so Number() on
 // either side rounds off the low digits and can pick the wrong channel (or none).
 const channelOptions=current=>channels.map(c=>`<option value="${esc(c.id)}" ${sameId(current,c.id)?'selected':''}>#${esc(c.name)} · ${esc(c.category)}</option>`).join('');
 const byKey=Object.fromEntries(base.map(x=>[x.key,x]));
 const bind=(id,label,key)=>`<label>${label}<select id="${id}"><option value="">Keep current</option>${channelOptions(byKey[key]?.channel_id||byKey[key]?.configured_id)}</select></label>`;
 app.innerHTML=`<div class="admin-hero"><div><div class="eyebrow">DISCORD SERVER CONTROL</div><h2>Xianxia RP Discord Setup</h2><p>The dashboard talks to the connected Python bot. Discord.py creates/repairs channels and roles; Go remains responsible only for canonical game state.</p></div><div class="tagline">${pill(guild.name||'Guild','good')}${pill(`${pgood}/${perms.length} permissions`,pgood===perms.length?'good':'warn')}${pill(`${d.registered_commands||0} commands`,'blue')}</div></div>
 <div id="discordResult"></div>
 <div class="cards">
  <div class="card"><small>Connected Guild</small><div class="metric">${esc(guild.name||'—')}</div><div class="muted">${esc(guild.id||'')}</div></div>
  <div class="card"><small>Base Channels</small><div class="metric">${n(d.base_ready)}/${n(d.base_total)}</div></div>
  <div class="card"><small>Realm Hubs</small><div class="metric">${n(d.realm_ready)}/${n(d.realm_total)}</div></div>
  <div class="card"><small>Setup State</small><div class="metric ${d.setup_ready?'good':'warn'}">${d.setup_ready?'READY':'ATTENTION'}</div></div>
  <div class="card"><small>Bug Reports</small><div class="metric ${(d.bugs||{}).ready?'good':'warn'}">${(d.bugs||{}).ready?`${n((d.bugs||{}).open_reports)} open`:'NOT SET UP'}</div></div>
 </div>
 <div class="admin-grid">
  <section class="card control"><h3>🛠️ Automatic Setup</h3><p>Safe and idempotent. Existing configured/name-matching channels are reused and repaired.</p><div class="buttonrow"><button class="btn primary" id="fullSetup">Full Setup</button><button class="btn" id="repairSetup">Repair Server</button><button class="btn" id="refreshDiscord">Refresh Status</button></div><div class="buttonrow"><button class="btn" id="syncCommands">Sync Slash Commands</button><button class="btn" id="syncRealmRoles">Sync Realm Roles</button><button class="btn" id="rebuildInfo">Rebuild Info Guide</button><button class="btn" id="testAnnouncement">Test Announcement</button></div></section>
  <section class="card control"><h3>🔐 Permission Diagnostics</h3>${perms.map(x=>`<div class="row"><span>${esc(x.label)}</span><b class="${x.ok?'good':'bad'}">${x.ok?'READY':'MISSING'}</b></div><div class="muted">${esc(x.purpose)}</div>`).join('')}${(d.permission_warnings||[]).length?`<div class="result badbox"><b>Attention</b><div>${(d.permission_warnings||[]).map(x=>`• ${esc(x)}`).join('<br>')}</div></div>`:''}</section>
 </div>
 <div class="grid2">
  <section><h2>Base Xianxia RP Channels</h2>${table([['Channel','key'],['State',r=>pill(r.status,r.status==='ready'?'good':r.status==='stale'?'bad':'warn')],['Discord Channel',r=>r.name?`#${esc(r.name)} · ${r.channel_id}`:'—']],base)}</section>
  <section><h2>Realm Capitals</h2><p class="muted">Each capital channel is visible only to cultivators standing in that city: the bot puts the presence role on when a character arrives and takes it off when they leave. Run Repair after upgrading to apply the gate.</p>${table([['World','world'],['State',r=>pill(r.ready?'ready':'missing',r.ready?'good':'warn')],['Channel',r=>r.channel_name?`#${esc(r.channel_name)}`:'—'],['Presence role',r=>esc(r.role_name||'—')],['Visibility',r=>r.channel_name?pill(r.hidden?'in-city only':'VISIBLE TO ALL',r.hidden?'good':'bad'):'—']],realms)}</section>
  <section><h2>Auction Houses</h2><p class="muted">Every city has an auction house (v0.33.1). A capital's grand house has a live channel of its own; the local floors of a world - smaller, six lots at a time, none longer than six hours - share one channel per world. A lot is posted the moment it is listed, its card follows every bid, and it is struck when the simulation tick settles it. Visible to cultivators who can reach that world. Setup/Repair creates the channels.</p>${table([['House','name'],['Size',r=>pill(r.size,r.size==='grand'?'purple':'')],['World','world'],['Entrance','entrance'],['State',r=>pill(r.ready?'ready':'missing',r.ready?'good':'warn')],['Channel',r=>r.channel_name?`#${esc(r.channel_name)}`:'—'],['Open lots',r=>n(r.open_lots)]],halls)}</section>
 </div>
 <h2>Use Existing Channels</h2><section class="card control"><p>Choose existing text channels instead of the recommended generated layout. Blank selections preserve the current binding.</p><div class="grid2">${bind('bindAnnouncements','World events / announcements','world-events')}${bind('bindScenes','Event scenes','event-scenes')}${bind('bindHomes','Player homes','player-homes')}${bind('bindLogs','Bot logs','bot-logs')}${bind('bindBegin','Begin here','begin-here')}${bind('bindInfo','Xianxia info','xianxia-info')}${bind('bindExploration','Expeditions','expeditions')}${bind('bindPlaytest','Playtest board','playtest')}</div><label>Audit reason<input id="discordReason" value="GM dashboard Discord setup"></label><button class="btn primary" id="saveBindings">Save Channel Bindings</button></section>
 <h2>Channel Messages</h2><section class="card control"><p>One persistent, editable welcome message per channel below. GM-authored defaults are pre-filled and ready to post as-is; edit any box and save to post/update it, or clear a box and save to remove that channel's message. A cleared box stays cleared - Full Setup/Repair will not put the default back. Use "Restore default text" to undo that. Saving edits the existing message in place - it never duplicates.</p><div class="grid2">${(d.channel_messages||[]).map(row=>`<label>${esc(row.label)}${row.is_default?' <span class="muted">(default)</span>':''}${row.is_disabled?' <span class="bad">(cleared — stays empty through Repair)</span>':''}${row.channel_ready?'':' <span class="bad">(channel not bound)</span>'}<textarea data-cm-key="${esc(row.key)}" rows="4">${esc(row.content)}</textarea>${row.is_disabled?`<button class="btn" type="button" data-cm-restore="${esc(row.key)}" data-cm-default="${esc(row.default_content||'')}">Restore default text</button>`:''}</label>`).join('')}</div><button class="btn primary" id="saveChannelMessages">Save Channel Messages</button></section>
 <h2>Bug Reports</h2><section class="card control"><p>${(d.bugs||{}).ready?`Players report issues as forum posts in <b>#${esc((d.bugs||{}).channel_name||'bugs')}</b>. Every post there is a bug report - no separate form needed. Tags actually on the forum: ${((d.bugs||{}).available_tags||[]).map(esc).join(', ')||'none'}.${((d.bugs||{}).missing_tags||[]).length?` <span class="bad">Missing required tag(s): ${((d.bugs||{}).missing_tags||[]).map(esc).join(', ')} — run Repair to add them.</span>`:''}`:'The #bugs forum channel is not set up yet - run Full Setup or Repair to create it (needs the Manage Channels permission; some servers also need Community enabled).'}</p><label>Post guidelines${(d.bugs||{}).is_default_guidelines?' <span class="muted">(default)</span>':''} — shown to anyone starting a new #bugs post<textarea id="bugsGuidelines" rows="4">${esc((d.bugs||{}).guidelines||'')}</textarea></label><div class="buttonrow"><button class="btn primary" id="saveBugsGuidelines">Save Guidelines</button><button class="btn" id="refreshBugReports">Refresh Reports</button></div><h4>Recent Reports</h4>${table([['Report',r=>r.url?`<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>`:esc(r.title)],['Reporter','author_name'],['Tags',r=>(r.tags||[]).join(', ')||'—'],['Status',r=>pill(r.archived?'archived':'open',r.archived?'warn':'good')],['Messages','message_count'],['Opened',r=>r.created_at?new Date(r.created_at*1000).toLocaleString():'—']],(d.bugs||{}).reports||[])}</section>
 <section class="card control dangerzone"><h3>🧹 Fresh Start</h3><p>Deletes and recreates <b>#world-events</b>, <b>#bot-logs</b>, <b>#begin-here</b>, <b>#xianxia-info</b>, all 4 realm-capital hubs and <b>#bugs</b> - wiping every message in them instantly, regardless of age. Guides and channel messages are reposted fresh automatically. <b>#player-homes</b>, <b>#expeditions</b> and <b>#event-scenes</b> are deliberately left alone, since they anchor players' live private/property/event threads and deleting them would destroy that game state. Any custom permission overwrites you added by hand to a wiped channel are lost (the bot only reapplies its own defaults), and channels reappear at the bottom of their category. Permanent - cannot be undone.</p><button class="btn danger" id="freshStartDiscord">Clear Channel History</button></section>
 <section class="card control dangerzone"><h3>🗑️ Teardown</h3><p>Deletes <b>everything this bot owns on Discord</b>: every tracked thread (expedition journals, households, sect/cave abodes, event scenes, battles), all 7 base channels including <b>#player-homes</b>, <b>#expeditions</b> and <b>#event-scenes</b>, all realm-capital hubs, <b>#bugs</b>, and the two Xianxia categories if nothing else is left in them. Then the bot forgets every channel and message id it held. <b>Nothing is recreated</b> - run Full Setup afterwards to rebuild from scratch - and <b>the database is not reset</b>: characters, sects and history survive; players get fresh threads on next use. Only channels a binding names are touched; channels you listed in <code>RP_CHANNEL_IDS</code>, the Xianxia realm roles and anything else you created are left alone. Type <code>DELETE</code> to confirm. Permanent - cannot be undone.</p><label>Type DELETE to confirm<input id="teardownConfirm" placeholder="DELETE" autocomplete="off"></label><button class="btn danger" id="teardownDiscord">Delete All Xianxia Channels</button></section>
 <section class="card control dangerzone"><h3>💀 Reset World</h3><p>Deletes every Discord thread this bot is tracking (expedition journals, household threads, sect/cave abodes, event scenes, battle threads) and posts a world-reset announcement in the world-events channel. This is the Discord half of a full world reset - it does not touch the database; run <code>./reset_database.sh</code> on the server for that (it calls this same cleanup automatically when the bot is running). Permanent - deleted threads cannot be recovered.</p><button class="btn danger" id="resetWorldDiscord">Delete All Threads &amp; Announce Reset</button></section>`;
 const out=document.getElementById('discordResult');
 const run=async(action,payload={},confirmText='')=>{if(confirmText&&!confirm(confirmText))return;out.innerHTML='<div class="loading">Applying Discord setup action…</div>';try{const r=await discordPost(action,{...payload,reason:discordReason?.value||'GM dashboard Discord setup'});out.innerHTML=resultBox(r.result??r);setTimeout(()=>refresh().catch(()=>{}),900)}catch(e){out.innerHTML=resultBox(e.message,false)}};
 fullSetup.onclick=()=>run('setup',{},'Install/repair the recommended Xianxia RP Discord layout and sync slash commands?');
 repairSetup.onclick=()=>run('repair',{},'Repair Xianxia RP channels, permissions, realm roles and slash commands?');
 refreshDiscord.onclick=()=>loadDiscordSetup();
 syncCommands.onclick=()=>run('sync_commands');
 syncRealmRoles.onclick=()=>run('sync_roles');
 rebuildInfo.onclick=()=>run('rebuild_info');
 testAnnouncement.onclick=()=>run('test_announcement',{},'Send a visible Xianxia RP dashboard test message to the configured world-events channel?');
 saveBindings.onclick=()=>run('bind_channels',{announcements:bindAnnouncements.value,scenes:bindScenes.value,homes:bindHomes.value,logs:bindLogs.value,begin:bindBegin.value,info:bindInfo.value,exploration:bindExploration.value,playtest:bindPlaytest.value},'Save these Discord channel bindings?');
 // Refill a cleared box with the built-in text locally; nothing is applied until Save.
 document.querySelectorAll('[data-cm-restore]').forEach(btn=>{btn.onclick=()=>{const box=document.querySelector(`[data-cm-key="${btn.dataset.cmRestore}"]`);if(box){box.value=btn.dataset.cmDefault||'';box.focus()}}});
 saveChannelMessages.onclick=()=>{const messages={};document.querySelectorAll('[data-cm-key]').forEach(el=>{messages[el.dataset.cmKey]=el.value});run('set_channel_messages',{messages},'Post/update these channel messages now (blank boxes remove that channel\'s message and keep it removed)?')};
 saveBugsGuidelines.onclick=()=>run('set_bugs_guidelines',{guidelines:bugsGuidelines.value});
 refreshBugReports.onclick=()=>loadDiscordSetup();
 freshStartDiscord.onclick=()=>run('fresh_start',{confirm:'CLEAR'},'PERMANENTLY delete and recreate world-events, bot-logs, begin-here, xianxia-info, the realm hubs and bugs - wiping all messages in them? player-homes, expeditions and event-scenes are left untouched. This cannot be undone.');
 teardownDiscord.onclick=()=>{const typed=(teardownConfirm.value||'').trim().toUpperCase();if(typed!=='DELETE'){out.innerHTML=resultBox('Type DELETE in the box to confirm the teardown.',false);return}run('teardown',{confirm:typed},'PERMANENTLY delete every Xianxia channel, category and thread this bot owns on Discord? Nothing is recreated and the database is not reset. This cannot be undone.')};
 resetWorldDiscord.onclick=()=>run('reset_world',{confirm:'RESET'},'PERMANENTLY delete every Discord thread this bot is tracking and post a world-reset announcement? This cannot be undone.');
}

async function loadNarration(){
 const d=await api('/api/narration');
 if(!d.connected){app.innerHTML=`<h2>Narration Routes</h2><div class="card badbox"><b>The narrator is unreachable.</b><div class="muted">${esc(d.message||'The dashboard cannot reach the Python bot process, so it cannot read or change the live chain.')}</div></div>`;return}
 const slots=d.slots||{}, st=d.status||{}, cat=d.catalogue||{}, models=cat.models||[], rows=st.models||[], chains=st.chains||{};
 // A slug the GM already has selected may not be in the catalogue any more -
 // that is exactly the case this panel exists to make visible - so it is added
 // to its own picker rather than silently reset to the first option.
 const options=(current,allowEmpty)=>{const ids=models.map(m=>m.id);const extra=current&&!ids.includes(current)?[{id:current,name:current+' (not in the free catalogue)',context_length:0}]:[];
  return (allowEmpty?`<option value="" ${current?'':'selected'}>— none: go straight to the dynamic router —</option>`:'')+[...extra,...models].map(m=>`<option value="${esc(m.id)}" ${sameId(current,m.id)?'selected':''}>${esc(m.name||m.id)}${m.context_length?` · ${n(m.context_length)} ctx`:''}</option>`).join('')}
 const slot=(id,label,key,allowEmpty,hint)=>`<label>${label}<select id="${id}">${options(slots[key]||'',allowEmpty)}</select><small class="muted">${esc(hint)}</small></label>`;
 const verdict=r=>{if(r.probe_retired)return pill('retired','bad');if(r.probe_ok===true)return pill('reachable','good');if(r.probe_ok===false)return pill('failed','warn');return pill('not probed','blue')};
 const budget=st.limiter||{};
 app.innerHTML=`<div class="admin-hero"><div><div class="eyebrow">NARRATION ROUTING</div><h2>Narration Routes</h2><p>Pick which free models narrate and in what order. Choices are stored by the Go engine and written to <code>admin_audit_log</code>, then applied to the running bot — they survive a restart. Narration is descriptive only: nothing chosen here can write rewards, deaths, relationships or history.</p></div><div class="tagline">${pill(st.enabled?'router live':'no route configured',st.enabled?'good':'bad')}${pill(`${n(budget.used_today||0)}/${n(budget.max_requests_per_day||0)} today`,'blue')}${pill(`${models.length} free models`,models.length?'good':'warn')}</div></div>
 <div id="narrationResult"></div>
 ${cat.error?`<div class="card warnbox"><b>The catalogue could not be refreshed.</b><div class="muted">${esc(cat.error)} — the pickers below show what is already selected, so you can still change the order.</div></div>`:''}
 <div class="cards">
  <div class="card"><small>Routine chain</small><div class="muted">${(chains.routine||[]).map(esc).join(' → ')||'—'}</div></div>
  <div class="card"><small>Epic chain</small><div class="muted">${(chains.epic||[]).map(esc).join(' → ')||'—'}</div></div>
 </div>
 <h2>Choose Routes</h2>
 <section class="card control"><p>Only free endpoints are offered while <code>OPENROUTER_REQUIRE_FREE</code> is on. A newly chosen route is treated as un-probed, so a retirement left by whatever occupied the slot before does not carry over.</p>
 <div class="grid2">
  ${slot('slotRoutine','Routine — primary','routine_model',false,'Ordinary scenes. Tried first.')}
  ${slot('slotRoutineFallback','Routine — second hop','routine_fallback_model',true,'Optional. Tried when the primary fails.')}
  ${slot('slotEpic','Epic — primary','epic_model',false,'Breakthroughs, sect trials, major events.')}
  ${slot('slotEpicFallback','Epic — second hop','epic_fallback_model',true,'Optional. Tried when the epic primary fails.')}
  ${slot('slotDynamic','Last hop — dynamic router','dynamic_free_model',false,'Closes both chains. After this one fails, narration is procedural.')}
 </div>
 <label class="check"><input type="checkbox" id="creditsToppedUp" ${st.credits_topped_up?'checked':''}> Ten dollars of credit are on the OpenRouter account<small class="muted">Off: the free tier is 50 narrations a day. On: 1000. Currently ${n(budget.max_requests_per_day||0)} a day; stored and audited with the routes.</small></label>
 <label>Audit reason<input id="narrationReason" value="GM dashboard narration routing"></label>
 <div class="buttonrow"><button class="btn primary" id="saveNarration">Save Routes</button><button class="btn" id="refreshCatalogue">Refresh Catalogue</button><button class="btn" id="refreshNarration">Refresh Status</button></div></section>
 <h2>What The Daily Probe Found</h2>
 <p class="muted">The audit proves a route <b>answers</b>, nothing more — a model that replies with its own reasoning instead of prose passes this and is still rejected at narration time. A 400 is only acted on when the route answers without the reasoning-off parameter and fails with it.</p>
 ${table([['Route','model'],['Probe',verdict],['400 verdict',r=>r.probe_400_class?esc(r.probe_400_class):'—'],['Attempts','attempts'],['Served','successes'],['Failed','failures'],['Empty','empty_responses'],['Scratchpad','scratchpad_rejected'],['Cooling',r=>r.cooling_down?`${r.cooldown_remaining_seconds}s`:'—'],['Last error',r=>esc(r.last_error||r.probe_error||'—')]],rows)}`;
 const out=document.getElementById('narrationResult');
 saveNarration.onclick=async()=>{
  const slotsOut={routine_model:slotRoutine.value,routine_fallback_model:slotRoutineFallback.value,epic_model:slotEpic.value,epic_fallback_model:slotEpicFallback.value,dynamic_free_model:slotDynamic.value,credits_topped_up:creditsToppedUp.checked?'true':'false'};
  if(!confirm('Save these narration routes? They are audited and applied to the running bot immediately.'))return;
  out.innerHTML='<div class="loading">Saving narration routes…</div>';
  try{const r=await narrationPost('narration.set_chain',{slots:slotsOut,reason:narrationReason?.value||'GM dashboard narration routing'});
   out.innerHTML=resultBox(r.applied?r:{...r,note:'Stored and audited, but the running bot did not pick it up — it will apply on the next restart.'},!!r.applied);
   setTimeout(()=>loadNarration().catch(()=>{}),900)}
  catch(e){out.innerHTML=resultBox(e.message,false)}};
 refreshCatalogue.onclick=async()=>{out.innerHTML='<div class="loading">Asking OpenRouter what is free right now…</div>';try{await narrationPost('narration.refresh_catalogue');await loadNarration()}catch(e){out.innerHTML=resultBox(e.message,false)}};
 refreshNarration.onclick=()=>loadNarration();
}

async function loadAdmin(){
 const d=await api('/api/admin');
 if(!d.enabled){app.innerHTML=`<h2>Admin Console</h2><div class="card warnbox"><b>Admin writes are disabled.</b><div class="muted">Set DASHBOARD_ADMIN_WRITES=true and GAME_ENGINE_URL to enable authoritative controls.</div></div>`;return}
 const players=d.players||[], sims=d.simulations||[], automation=d.automation||{};
 const pOpts=optionRows(players), locations=(d.locations||[]).map(x=>`<option>${esc(x)}</option>`).join(''), currencies=(d.currencies||[]).map(x=>`<option>${esc(x)}</option>`).join('');
 const npcOpts=(d.npcs||[]).map(x=>`<option>${esc(x)}</option>`).join(''), eventOpts=(d.active_events||[]).map(x=>`<option value="${esc(x.event_key)}">${esc(x.title)} (${esc(x.event_key)})</option>`).join('');
 const sectDatalist=(d.sects||[]).map(x=>`<option value="${esc(x)}">`).join('');
 const gradeOpts=['Mortal','Common','Refined','Earth','Heaven','Immortal'].map(x=>`<option>${esc(x)}</option>`).join('');
 const gateOpts=[[7,'Mortal Ascension Tribulation (realm 7)'],[15,'Transcendence Tribulation (realm 15)'],[23,'Celestial Ascension Tribulation (realm 23)']].map(([v,l])=>`<option value="${v}">${esc(l)}</option>`).join('');
 const db=d.database||{}, pragmas=db.pragmas||{};
 app.innerHTML=`<div class="admin-hero"><div><div class="eyebrow">AUTHORITATIVE GO CONTROL PLANE</div><h2>GM Admin Console</h2><p>All mutations are executed by the Go engine and written to the canonical SQLite WAL database. Dangerous actions require confirmation and are audit logged.</p></div><div class="tagline">${pill(pragmas.journal_mode||'DB','good')}${pill(`FK ${pragmas.foreign_keys??'?'}`,'blue')}${pill(`${players.length} players`,'warn')}</div></div>
 <div id="adminResult"></div>
 <div class="admin-grid">
  <section class="card control"><h3>⏳ World Time</h3><p>Advance or rewind canonical game time. Current scale: ${esc((d.clock||{}).scale ?? '?')}x game-minutes per real minute.</p><label>Minutes<input id="timeMinutes" type="number" value="1440"></label><label>New time scale (optional, blank = unchanged)<input id="timeScale" type="number" min="0" placeholder="e.g. 4"></label><label>Reason<input id="timeReason" value="GM world adjustment"></label><button class="btn primary" id="advanceTime">Apply time change</button></section>
  <section class="card control"><h3>⚙️ Simulation</h3><p>Force a native Go batch or change its interval.</p><label>System<select id="simSystem">${sims.map(x=>`<option>${esc(x.system)}</option>`).join('')}</select></label><div class="fieldrow"><label>Steps<input id="simSteps" type="number" min="1" max="120" value="1"></label><label>Interval days<input id="simDays" type="number" min="1" max="365" value="1"></label></div><div class="buttonrow"><button class="btn" id="forceSim">Force batch</button><button class="btn" id="setInterval">Set interval</button></div></section>
  <section class="card control"><h3>🧭 Teleport Player</h3><label>Player<select id="telePlayer">${pOpts}</select></label><label>Canonical location<select id="teleLocation">${locations}</select></label><label>Reason<input id="teleReason" value="GM teleport"></label><button class="btn primary" id="teleportPlayer">Teleport</button></section>
  <section class="card control"><h3>💰 Grant Currency</h3><label>Player<select id="moneyPlayer">${pOpts}</select></label><label>Currency<select id="currency">${currencies}</select></label><label>Amount<input id="currencyAmount" type="number" min="1" value="100"></label><label>Reason<input id="currencyReason" value="GM reward"></label><button class="btn primary" id="grantCurrency">Grant</button></section>
  <section class="card control"><h3>☯ Karma Adjustment</h3><label>Player<select id="karmaPlayer">${pOpts}</select></label><label>Delta<input id="karmaDelta" type="number" value="10"></label><label>Reason<input id="karmaReason" value="GM correction"></label><button class="btn" id="adjustKarma">Adjust karma</button></section><section class="card control"><h3>🧧 Fate Adjustment</h3><label>Player<select id="fatePlayer">${pOpts}</select></label><label>Delta<input id="fateDelta" type="number" value="1" min="-9" max="9"></label><label>Reason<input id="fateReason" value="GM reward"></label><button class="btn" id="adjustFate">Adjust fate</button></section>
  <section class="card control dangerzone"><h3>❤️ Recovery</h3><p>Emergency character-state recovery.</p><label>Player<select id="recoverPlayer">${pOpts}</select></label><label>Reason<input id="recoverReason" value="GM recovery"></label><div class="buttonrow"><button class="btn danger" id="revivePlayer">Revive</button><button class="btn danger" id="clearBattle">Clear battle</button><button class="btn danger" id="forceEndScene">Force end scene</button><button class="btn danger" id="forceReincarnationReady">Force reincarnation ready</button></div><p>Force reincarnation ready clears the real-world-time wait on a soul in the afterlife (reincarnation_state) so they can reincarnate immediately, without affecting anything else about their Samsara cycle.</p><label>Cooldown to clear (blank = all)<input id="cooldownAction" placeholder="e.g. explore"></label><button class="btn danger" id="resetCooldowns">Reset cooldowns</button></section>
  <section class="card control"><h3>👤 Player Detail</h3><p>Open a player's full character sheet, inventory and cooldowns.</p><label>Player<select id="detailPlayer">${pOpts}</select></label><button class="btn" id="viewPlayerDetail">View / Edit</button></section>
  <section class="card control"><h3>📈 Realm / Phase Override</h3><p>Directly set a player's cultivation realm and phase (story correction), bypassing normal breakthrough gating.</p><label>Player<select id="realmPlayer">${pOpts}</select></label><div class="fieldrow"><label>Realm index (0-31)<input id="realmIndex" type="number" min="0" max="31" value="0"></label><label>Phase (1-9)<input id="realmPhase" type="number" min="1" max="9" value="1"></label></div><label>Reason<input id="realmReason" value="GM story correction"></label><button class="btn primary" id="setRealm">Set realm</button></section>
  <section class="card control"><h3>❤️‍🩹 Resource Caps</h3><p>Directly set Vitality/Qi maximums. Leave a field blank to leave it unchanged; lowering a cap clamps the character's current value down to match.</p><label>Player<select id="capsPlayer">${pOpts}</select></label><div class="fieldrow"><label>Vitality max<input id="capsVitality" type="number" min="1" placeholder="unchanged"></label><label>Qi max<input id="capsQi" type="number" min="1" placeholder="unchanged"></label></div><label>Reason<input id="capsReason" value="GM correction"></label><button class="btn primary" id="setResourceCaps">Set caps</button></section>
  <section class="card control"><h3>⛩️ Sect Membership</h3><p>Assign, re-rank, or remove a player's sect membership.</p><label>Player<select id="sectPlayer">${pOpts}</select></label><label>Sect name<input id="sectName" list="sectList" placeholder="e.g. Azure Cloud Sect"></label><datalist id="sectList">${sectDatalist}</datalist><div class="fieldrow"><label>Rank name<input id="sectRankName" value="Disciple"></label><label>Rank level (0-100)<input id="sectRankLevel" type="number" min="0" max="100" value="0"></label></div><label>Reason<input id="sectReason" value="GM story correction"></label><div class="buttonrow"><button class="btn primary" id="setSect">Set sect / rank</button><button class="btn danger" id="removeSect">Remove sect membership</button></div></section>
  <section class="card control"><h3>🧘 Realm Perfection Progress</h3><p>Directly set cultivation or body-realm perfection progress for one realm.</p><label>Player<select id="perfPlayer">${pOpts}</select></label><div class="fieldrow"><label>Track<select id="perfTrack"><option value="cultivation">Cultivation</option><option value="body">Body</option></select></label><label>Realm index (0-31)<input id="perfRealmIndex" type="number" min="0" max="31" value="0"></label><label>Progress (0-100)<input id="perfProgress" type="number" min="0" max="100" value="0"></label></div><label>Reason<input id="perfReason" value="GM story correction"></label><button class="btn primary" id="setRealmPerfection">Set perfection progress</button></section>
  <section class="card control"><h3>🌀 Spiritual Root</h3><p>Directly edit grade/purity/mutation. Elements, stability, refinement and compatibility are left untouched.</p><label>Player<select id="rootPlayer">${pOpts}</select></label><div class="fieldrow"><label>Grade<select id="rootGrade">${gradeOpts}</select></label><label>Purity (0-100)<input id="rootPurity" type="number" min="0" max="100" value="50"></label></div><label>Mutation (optional)<input id="rootMutation" placeholder="e.g. Chaos-Attuned"></label><label>Reason<input id="rootReason" value="GM story correction"></label><button class="btn primary" id="setSpiritualRoot">Set spiritual root</button></section>
  <section class="card control"><h3>🩸 Bloodline</h3><p>Edit purity/evolution/progress on a bloodline the character already has. Name/affinity/state are left unchanged.</p><label>Player<select id="bloodPlayer">${pOpts}</select></label><label>Bloodline ID<input id="bloodId" placeholder="e.g. azure_dragon_blood"></label><div class="fieldrow"><label>Purity (0-100)<input id="bloodPurity" type="number" min="0" max="100" value="0"></label><label>Evolution stage<input id="bloodEvolution" type="number" min="0" value="0"></label><label>Progress (0-100)<input id="bloodProgress" type="number" min="0" max="100" value="0"></label></div><label>Reason<input id="bloodReason" value="GM story correction"></label><button class="btn primary" id="setBloodline">Set bloodline</button></section>
  <section class="card control"><h3>🫀 Physique</h3><p>Edit evolution/progress/stability on the character's physique. Requires the physique row to already exist.</p><label>Player<select id="physPlayer">${pOpts}</select></label><div class="fieldrow"><label>Evolution stage<input id="physEvolution" type="number" min="0" value="0"></label><label>Progress (0-100)<input id="physProgress" type="number" min="0" max="100" value="0"></label><label>Stability (0-100)<input id="physStability" type="number" min="0" max="100" value="100"></label></div><label>Reason<input id="physReason" value="GM story correction"></label><button class="btn primary" id="setPhysique">Set physique</button></section>
  <section class="card control dangerzone"><h3>⚡ Tribulation Gate</h3><p>Clear unlocks the breakthrough past this gate. Reset wipes preparation/attempts/cleared for a clean re-attempt.</p><label>Player<select id="tribPlayer">${pOpts}</select></label><label>Gate<select id="tribGate">${gateOpts}</select></label><label>Reason<input id="tribReason" value="GM unstuck the player"></label><div class="buttonrow"><button class="btn danger" id="clearTribulation">Clear gate</button><button class="btn danger" id="resetTribulation">Reset gate</button></div></section>
  <section class="card control dangerzone"><h3>🧪 Crafting Fixes</h3><p>Targeted fixes for pill toxicity, spirit beasts, equipment, and cave abode access. Look up beast/equipment IDs on the Crafting page first.</p><label>Player<select id="craftPlayer">${pOpts}</select></label><label>Reason<input id="craftReason" value="GM crafting fix"></label><div class="fieldrow"><label>Pill toxicity (0-1000)<input id="toxicityValue" type="number" min="0" max="1000" value="0"></label><button class="btn danger" id="setPillToxicity">Set toxicity</button></div><div class="fieldrow"><label>Beast ID<input id="beastId" type="number" min="1"></label><label>Loyalty (0-100, blank = unchanged)<input id="beastLoyalty" type="number" min="0" max="100" placeholder="unchanged"></label><label>Evolution stage (blank = unchanged)<input id="beastEvolution" type="number" min="0" placeholder="unchanged"></label><button class="btn danger" id="setBeastStats">Set beast stats</button></div><div class="fieldrow"><label>Equipment ID<input id="equipmentId" type="number" min="1"></label><button class="btn danger" id="removeEquipment">Remove equipment</button></div><div class="fieldrow"><label>Guest (for abode access)<select id="abodeGuest">${pOpts}</select></label><label>Access role<input id="abodeAccessRole" value="guest"></label><div class="buttonrow"><button class="btn" id="grantAbodeAccess">Grant access</button><button class="btn danger" id="revokeAbodeAccess">Revoke access</button></div></div></section>
  <section class="card control dangerzone"><h3>🚫 Moderation</h3><p>Freeze blocks every authoritative action this player takes. Mute blocks only free-form roleplay (scene actions) - combat, cultivation, shopping and other checks stay open. Ban blocks everything and never expires. A mute or freeze given a duration lifts itself when it runs out (the engine's simulation tick clears it; the block ends on time either way); leave it at 0 to hold until lifted. The same verbs exist as <code>/admin player mute|freeze|ban</code> in Discord and write the same audit row. Scope: this only covers actions routed through the Go authoritative engine; it cannot intercept the bot's direct database writes or the simulation runner, so treat it as a moderation nudge, not anti-cheat.</p><label>Player<select id="modPlayer">${pOpts}</select></label><div class="fieldrow"><label class="toggle"><span>Muted</span><input type="checkbox" id="modMuted"></label><label class="toggle"><span>Frozen</span><input type="checkbox" id="modFrozen"></label><label class="toggle"><span>Banned</span><input type="checkbox" id="modBanned"></label></div><label>Expires after (hours, 0 = until lifted; applies to the mute and freeze set now)<input id="modHours" type="number" min="0" step="0.5" value="0"></label><p class="muted" id="modStanding"></p><label>Reason (shown to the player)<input id="modReason" placeholder="e.g. cooling off period"></label><button class="btn danger" id="setModeration">Apply moderation</button></section>
  <section class="card control"><h3>🎒 Adjust Inventory</h3><p>Grant (positive) or remove (negative) an inventory item by quantity.</p><label>Player<select id="itemPlayer">${pOpts}</select></label><div class="fieldrow"><label>Item ID<input id="itemId" placeholder="e.g. spirit_herb (or the item name)"></label><label>Quantity delta<input id="itemDelta" type="number" value="1"></label></div><label>Reason<input id="itemReason" value="GM item grant"></label><button class="btn primary" id="adjustItem">Adjust item</button></section>
  <section class="card control"><h3>🗺️ Relocate NPC</h3><p>Force-move an NPC's current location (does not affect players).</p><label>NPC<select id="npcSelect">${npcOpts}</select></label><label>New location<select id="npcLocation">${locations}</select></label><label>Reason<input id="npcReason" value="GM story move"></label><button class="btn" id="relocateNpc">Relocate</button></section>
  <section class="card control"><h3>🌪️ End World Event</h3><p>End an active world event early.</p><label>Event<select id="eventSelect">${eventOpts}</select></label><label>Reason<input id="eventReason" value="GM ended early"></label><button class="btn" id="endWorldEvent">End event</button></section>
  <section class="card control"><h3>🤖 Automation</h3><p>Enable or disable persisted autonomous systems.</p><div class="toggle-list">${Object.entries(automation).map(([k,v])=>`<label class="toggle"><span>${esc(k)}</span><input type="checkbox" data-auto="${esc(k)}" ${v?'checked':''}></label>`).join('')}</div></section>
  <section class="card control dangerzone"><h3>💥 Bulk / Server-Wide Actions</h3><p>Applies to every character in one transaction, one audit entry.</p><div class="fieldrow"><label>Currency<select id="bulkCurrency">${currencies}</select></label><label>Amount<input id="bulkAmount" type="number" min="1" value="100"></label></div><label>Reason<input id="bulkReason" value="GM server event reward"></label><div class="buttonrow"><button class="btn danger" id="bulkGrantCurrency">Grant to everyone</button><button class="btn danger" id="bulkResetCooldowns">Reset everyone's cooldowns</button></div></section>
  <section class="card control dangerzone"><h3>🗄️ Database Maintenance</h3><p>Operations run inside the Go database service.</p><div class="buttonrow"><button class="btn" id="createBackup">Create backup</button><button class="btn" id="optimizeDb">Optimize</button><button class="btn danger" id="vacuumDb">VACUUM</button></div><h4>Backups</h4><p class="muted">Restore overwrites the live database with the selected backup. A safety backup of the current state is always taken automatically first, so a restore is itself never the last word.</p>${table([['Name',r=>(r.encrypted?'🔒 ':'')+esc(r.name)],['Size',r=>n(r.size)],['Created',r=>new Date(Number(r.modified_at)*1000).toLocaleString()],['',r=>`<button class="btn danger" data-restore-backup="${esc(r.name)}">Restore</button>`]],d.backups||[])}<p class="muted">Retention (XIANXIA_BACKUP_KEEP_DAILY / KEEP_WEEKLY / MAX_MB) prunes after every backup; 🔒 marks a backup sealed with XIANXIA_BACKUP_KEY, which restore needs to open it.</p></section>
 </div>
 <h2>Recent Admin Audit</h2><button class="btn danger" id="undoLastAction">↩️ Undo most recent action</button><p class="muted">Only a specific set of simple edits can be auto-undone (karma, realm, teleport, currency grants, realm/bloodline/physique/tribulation progress, item grants, force-end-scene, NPC relocation, single condition clears, and moderation). Bulk grants, Fate, sect changes, resource-cap edits, and world-time changes cannot be auto-undone.</p>${table([['ID','audit_id'],['Action','action'],['Target','target'],['Reason','reason'],['When',r=>new Date(Number(r.created_at)*1000).toLocaleString()]],d.audit||[])}`;
 const out=document.getElementById('adminResult');
 const run=async(action,payload,confirmText='')=>{if(confirmText&&!confirm(confirmText))return;out.innerHTML='<div class="loading">Applying authoritative action…</div>';try{const r=await adminPost(action,payload);out.innerHTML=resultBox(r.result??r);setTimeout(()=>refresh().catch(()=>{}),900)}catch(e){out.innerHTML=resultBox(e.message,false)}};
 advanceTime.onclick=()=>{const payload={minutes:Number(timeMinutes.value),reason:timeReason.value};if(timeScale.value!=='')payload.scale=Number(timeScale.value);run('world.advance_time',payload,`Change canonical world time by ${timeMinutes.value} minutes${timeScale.value!==''?` and set scale to ${timeScale.value}x`:''}?`)};
 forceSim.onclick=()=>run('simulation.force',{system:simSystem.value,steps:Number(simSteps.value),reason:'GM dashboard forced simulation'},`Force ${simSystem.value} for ${simSteps.value} step(s)?`);
 setInterval.onclick=()=>run('simulation.interval',{system:simSystem.value,days:Number(simDays.value),reason:'GM dashboard interval change'});
 // user_id is a Discord snowflake (17-19 digits) - well past Number.MAX_SAFE_INTEGER
 // (2^53-1). Wrapping it in Number() silently rounds off the low digits (e.g.
 // 847706123456789012 -> 847706123456789000), so the backend looks up a DIFFERENT,
 // nonexistent user and every one of these actions fails with "character not found".
 // <option value="..."> is already the exact string from the DB - pass it through as-is.
 teleportPlayer.onclick=()=>run('player.teleport',{user_id:telePlayer.value,location:teleLocation.value,reason:teleReason.value},'Teleport this player and reset their scene location?');
 grantCurrency.onclick=()=>run('player.grant_currency',{user_id:moneyPlayer.value,currency_id:currency.value,amount:Number(currencyAmount.value),reason:currencyReason.value});
 adjustKarma.onclick=()=>run('player.karma',{user_id:karmaPlayer.value,delta:Number(karmaDelta.value),reason:karmaReason.value});
 adjustFate.onclick=()=>run('player.fate',{user_id:fatePlayer.value,delta:Number(fateDelta.value),reason:fateReason.value});
 revivePlayer.onclick=()=>run('player.revive',{user_id:recoverPlayer.value,reason:recoverReason.value},'Revive this character, refill Vitality/Qi, cancel Samsara, and clear active battles?');
 clearBattle.onclick=()=>run('player.clear_battle',{user_id:recoverPlayer.value,reason:recoverReason.value},'Force-abandon all active battles for this player, including group combat and PvP?');
 forceReincarnationReady.onclick=()=>run('player.force_reincarnation_ready',{user_id:recoverPlayer.value,reason:recoverReason.value},'Clear the real-world-time wait so this soul can reincarnate immediately? Only affects a soul currently waiting in the afterlife.');
 forceEndScene.onclick=()=>run('player.force_end_scene',{user_id:recoverPlayer.value,reason:recoverReason.value},'Reset this player to a world scene at their current location, without teleporting them?');
 resetCooldowns.onclick=()=>run('player.reset_cooldowns',{user_id:recoverPlayer.value,action:cooldownAction.value,reason:recoverReason.value},cooldownAction.value?`Clear the "${cooldownAction.value}" cooldown for this player?`:'Clear ALL cooldowns for this player?');
 viewPlayerDetail.onclick=()=>showPlayer(detailPlayer.value);
 setRealm.onclick=()=>run('player.set_realm',{user_id:realmPlayer.value,realm_index:Number(realmIndex.value),phase:Number(realmPhase.value),reason:realmReason.value},`Set this player's realm to ${realmIndex.value}/${realmPhase.value}, bypassing breakthrough gating?`);
 setResourceCaps.onclick=()=>{const payload={user_id:capsPlayer.value,reason:capsReason.value};if(capsVitality.value!=='')payload.vitality_max=Number(capsVitality.value);if(capsQi.value!=='')payload.qi_max=Number(capsQi.value);if(payload.vitality_max===undefined&&payload.qi_max===undefined){out.innerHTML=resultBox('Set at least one of vitality_max or qi_max.',false);return}run('player.set_resource_caps',payload)};
 setSect.onclick=()=>run('player.set_sect',{user_id:sectPlayer.value,sect_name:sectName.value,rank_name:sectRankName.value,rank_level:Number(sectRankLevel.value),reason:sectReason.value});
 removeSect.onclick=()=>run('player.set_sect',{user_id:sectPlayer.value,remove:true,reason:sectReason.value},'Remove this player\'s sect membership entirely?');
 setRealmPerfection.onclick=()=>run('player.set_realm_perfection',{user_id:perfPlayer.value,track:perfTrack.value,realm_index:Number(perfRealmIndex.value),progress:Number(perfProgress.value),reason:perfReason.value});
 setSpiritualRoot.onclick=()=>run('player.set_spiritual_root',{user_id:rootPlayer.value,grade:rootGrade.value,purity:Number(rootPurity.value),mutation:rootMutation.value,reason:rootReason.value});
 setBloodline.onclick=()=>run('player.set_bloodline',{user_id:bloodPlayer.value,bloodline_id:bloodId.value,purity:Number(bloodPurity.value),evolution_stage:Number(bloodEvolution.value),progress:Number(bloodProgress.value),reason:bloodReason.value});
 setPhysique.onclick=()=>run('player.set_physique',{user_id:physPlayer.value,evolution_stage:Number(physEvolution.value),progress:Number(physProgress.value),stability:Number(physStability.value),reason:physReason.value});
 clearTribulation.onclick=()=>run('player.set_tribulation',{user_id:tribPlayer.value,gate_realm_index:Number(tribGate.value),mode:'clear',reason:tribReason.value},'Clear this tribulation gate, unlocking the breakthrough past it?');
 resetTribulation.onclick=()=>run('player.set_tribulation',{user_id:tribPlayer.value,gate_realm_index:Number(tribGate.value),mode:'reset',reason:tribReason.value},'Reset this tribulation gate (wipe preparation/attempts/cleared) for a clean re-attempt?');
 adjustItem.onclick=()=>run('player.adjust_item',{user_id:itemPlayer.value,item_id:itemId.value,quantity:Number(itemDelta.value),reason:itemReason.value});
 setPillToxicity.onclick=()=>run('player.set_pill_toxicity',{user_id:craftPlayer.value,pill_toxicity:Number(toxicityValue.value),reason:craftReason.value},`Set pill toxicity to ${toxicityValue.value}?`);
 setBeastStats.onclick=()=>{const payload={user_id:craftPlayer.value,beast_id:Number(beastId.value),reason:craftReason.value};if(beastLoyalty.value!=='')payload.loyalty=Number(beastLoyalty.value);if(beastEvolution.value!=='')payload.evolution_stage=Number(beastEvolution.value);run('player.set_beast_stats',payload,`Update spirit beast ${beastId.value}?`)};
 removeEquipment.onclick=()=>run('player.remove_equipment',{user_id:craftPlayer.value,equipment_id:Number(equipmentId.value),reason:craftReason.value},`Force-remove equipment ${equipmentId.value}? This cannot be undone through normal gameplay.`);
 grantAbodeAccess.onclick=()=>run('player.set_abode_access',{owner_user_id:craftPlayer.value,guest_user_id:abodeGuest.value,access_role:abodeAccessRole.value,reason:craftReason.value},`Grant abode access to this guest?`);
 revokeAbodeAccess.onclick=()=>run('player.set_abode_access',{owner_user_id:craftPlayer.value,guest_user_id:abodeGuest.value,revoke:true,reason:craftReason.value},`Revoke this guest's abode access?`);
 // Pre-fill the Muted/Frozen/Reason controls from the selected player's current
 // moderation state (snapshot() now returns is_muted/is_frozen/moderation_reason
 // on every player row) so the GM sees what's already set instead of guessing.
 const modState={};players.forEach(p=>{modState[String(p.user_id)]={muted:!!Number(p.is_muted),frozen:!!Number(p.is_frozen),banned:!!Number(p.is_banned),mutedUntil:Number(p.muted_until||0),frozenUntil:Number(p.frozen_until||0),reason:p.moderation_reason||''}});
 const untilText=(flag,until)=>!flag?'':until>0?(until*1000<=Date.now()?' (lapsed, clears on the next tick)':` until ${new Date(until*1000).toLocaleString()}`):' until lifted';
 const fillModeration=()=>{const cur=modState[String(modPlayer.value)]||{muted:false,frozen:false,banned:false,mutedUntil:0,frozenUntil:0,reason:''};modMuted.checked=cur.muted;modFrozen.checked=cur.frozen;modBanned.checked=cur.banned;modHours.value='0';modReason.value=cur.reason;const standing=[cur.banned?'banned':'',cur.frozen?'frozen'+untilText(true,cur.frozenUntil):'',cur.muted?'muted'+untilText(true,cur.mutedUntil):''].filter(Boolean);modStanding.textContent=standing.length?'Standing: '+standing.join(' · '):'Standing: nothing in force'};
 modPlayer.onchange=fillModeration;fillModeration();
 setModeration.onclick=()=>{const seconds=Math.max(0,Math.round(Number(modHours.value||0)*3600));const payload={user_id:modPlayer.value,muted:modMuted.checked,frozen:modFrozen.checked,banned:modBanned.checked,moderation_reason:modReason.value,reason:modReason.value||'GM moderation'};if(seconds>0){payload.muted_seconds=seconds;payload.frozen_seconds=seconds}run('player.set_moderation',payload,`Set this player to ${modMuted.checked?'muted':'not muted'}, ${modFrozen.checked?'frozen':'not frozen'} and ${modBanned.checked?'banned':'not banned'}${seconds>0?` for ${modHours.value} hour(s)`:''}?`)};
 relocateNpc.onclick=()=>run('npc.relocate',{npc_name:npcSelect.value,location:npcLocation.value,reason:npcReason.value});
 endWorldEvent.onclick=()=>run('world_event.end',{event_key:eventSelect.value,reason:eventReason.value},'End this world event early?');
 bulkGrantCurrency.onclick=()=>run('bulk.grant_currency',{currency_id:bulkCurrency.value,amount:Number(bulkAmount.value),reason:bulkReason.value},`Grant ${bulkAmount.value} ${bulkCurrency.value} to EVERY character on the server?`);
 bulkResetCooldowns.onclick=()=>run('bulk.reset_cooldowns',{reason:bulkReason.value},'Clear ALL cooldowns for EVERY character on the server?');
 createBackup.onclick=()=>run('backup.create',{reason:'GM dashboard backup'});
 optimizeDb.onclick=()=>run('database.optimize',{reason:'GM dashboard optimize'});
 vacuumDb.onclick=()=>run('database.vacuum',{reason:'GM dashboard vacuum'},'VACUUM locks/rebuilds SQLite. Run it now?');
 undoLastAction.onclick=()=>run('audit.undo_last',{reason:'GM undo'},'Undo the most recent admin action? Only a specific set of simple edits can be auto-undone - bulk grants, Fate, sect changes, resource-cap edits, and world-time changes cannot be. Undoing an undo redoes the original action.');
 document.querySelectorAll('[data-restore-backup]').forEach(el=>el.onclick=()=>run('backup.restore',{name:el.dataset.restoreBackup,reason:'GM restored from backup'},`⚠️ This OVERWRITES the live database with "${el.dataset.restoreBackup}". Every change made since that backup will be gone. A safety backup of the CURRENT state is taken automatically first, so this itself can be undone - but continue only if you mean it.`));
 document.querySelectorAll('[data-auto]').forEach(el=>el.onchange=()=>run('automation.set',{system:el.dataset.auto,enabled:el.checked,reason:'GM dashboard automation toggle'}));
}

// Commissions (v0.22.0). The GM's three questions in order: what is waiting for
// approval, what are players carrying, and how are commissions ending. Approve
// / retire / discard go through the audited engine actions; the outcome numbers
// are shown read-only because they are engine constants, not settings.
async function loadCommissions(){const d=await api('/api/commissions');const s=d.summary||{};const rules=d.rules||{};const gm=Number(d.game_minute||0);
 const due=r=>{if(r.due_in_game_minutes===null||r.due_in_game_minutes===undefined)return '—';const m=Number(r.due_in_game_minutes);if(m<=0)return `<span class="bad">overdue</span>`;const days=Math.floor(m/1440),hrs=Math.floor((m%1440)/60);return days?`${days}d ${hrs}h`:`${hrs}h`};
 const terms=v=>{const list=(()=>{try{return typeof v==='string'?JSON.parse(v):(v||[])}catch{return []}})();if(!list.length)return '—';return list.map(t=>`${esc(t.label||'terms')}: ${esc(Object.entries(t.rewards||{}).map(([k,val])=>typeof val==='object'?`${k} ${Object.entries(val).map(([i,q])=>`${i}×${q}`).join(' ')}`:`${val} ${k.replace('_',' ')}`).join(' · ')||'—')}`).join('<br>')};
 const outcome=v=>pill(String(v||'—').toUpperCase(),v==='completed'?'good':v==='active'?'warn':v==='failed'?'bad':'');
 app.innerHTML=`<h2>Commissions</h2>
 <div class="cards">${[['Approved in pool',s.approved_pool],['Drafts awaiting you',s.drafts],['Held by players',s.held],['Overdue now',s.overdue],['Sect-gated',s.sect_gated],['Undisclosed terms',s.undisclosed]].map(x=>`<div class="card"><small>${x[0]}</small><div class="metric">${n(x[1])}</div></div>`).join('')}
 <div class="card"><small>Outcomes so far</small><div class="metric"><span class="good">${n(s.completed)}</span> · <span class="bad">${n(s.failed)}</span> · ${n(s.abandoned)}</div><small>completed · failed · abandoned</small></div></div>
 <div class="card"><h3>What an outcome costs</h3><div>Completed: trust ${rules.completed?.trust>=0?'+':''}${n(rules.completed?.trust)}, respect ${rules.completed?.respect>=0?'+':''}${n(rules.completed?.respect)} · no cooldown</div>
 <div>Failed / abandoned: trust ${n(rules.failed?.trust)}, respect ${n(rules.failed?.respect)}, grudge +${n(rules.failed?.grudge)} · ${n(rules.cooldown_world_days)} world-day cooldown</div>
 <small>${esc(rules.note||'')} These are engine constants (commission_actions.go), shown here rather than edited here.</small></div>
 <h2>Definitions — drafts first</h2><div id="commissionOut"></div>
 ${table([['Commission',r=>`<b>${esc(r.title)}</b><br><small>${esc(r.source_key||r.origin||'')}</small>`],['Giver','giver_npc'],['Band',r=>esc(r.realm_band||'any')],['Tier','tier'],
   ['Sect',r=>r.requires_sect?pill(esc(r.requires_sect),'warn'):'—'],
   ['Terms shown?',r=>r.reward_visibility==='hidden'?pill('UNDISCLOSED','warn'):'shown'],
   ['For',r=>r.owner_user_id?`<span class="pill warn">${esc(r.owner_name||r.owner_user_id)}</span>`:'pool'],
   ['Terms',r=>terms(r.variants_json)],['Deadline',r=>r.deadline_game_minutes?`${Math.round(Number(r.deadline_game_minutes)/1440)}d`:'none'],
   ['Taken','taken'],['Status',r=>pill(String(r.status).toUpperCase(),r.status==='approved'?'good':r.status==='draft'?'warn':'bad')],
   ['Review',r=>`<button data-review="${esc(r.quest_key)}" data-status="approved">Approve</button> <button data-review="${esc(r.quest_key)}" data-status="retired">Retire</button> <button data-review="${esc(r.quest_key)}" data-status="discarded">Discard</button>`]],d.definitions||[])}
 <h2>Held by players</h2>
 ${table([['Player','player_name'],['Commission',r=>esc(r.title||r.quest_key)],['Giver','giver_npc'],['Terms',r=>`variant ${n(r.variant_index)}`],
   ['Accepted',r=>fmtGM(r.accepted_game_minute)],['Due',due],['Status',r=>outcome(r.status)],
   ['GM',r=>String(r.status)==='active'?`<button data-retire="${esc(r.quest_key)}" data-user="${id(r.user_id)}">Retire (no cost)</button>`:'—']],d.held||[])}
 <h2>Standing with givers</h2>
 ${table([['Player','player_name'],['Giver','npc_name'],['Standing','standing'],['Trust','trust'],['Respect','respect'],['Grudge','grudge'],
   ['Completed','commissions_completed'],['Failed','commissions_failed'],['Abandoned','commissions_abandoned'],
   ['Last','last_commission_outcome'],['Cooldown',r=>Number(r.commission_cooldown_until_game_minute||0)>gm?`<span class="warn">${Math.ceil((Number(r.commission_cooldown_until_game_minute)-gm)/1440)}d</span>`:'—']],d.standing||[])}`;
 const out=document.getElementById('commissionOut');
 const act=async(action,payload,confirmText)=>{if(!confirm(confirmText))return;out.innerHTML='<div class="loading">Applying…</div>';try{const r=await adminPost(action,{...payload,reason:'GM dashboard commissions'});out.innerHTML=resultBox(r.result??r);setTimeout(()=>refresh().catch(()=>{}),900)}catch(e){out.innerHTML=resultBox(e.message,false)}};
 document.querySelectorAll('[data-review]').forEach(b=>b.onclick=()=>act('commission.review',{quest_key:b.dataset.review,status:b.dataset.status},`Set "${b.dataset.review}" to ${b.dataset.status}?`));
 document.querySelectorAll('[data-retire]').forEach(b=>b.onclick=()=>act('commission.retire',{user_id:b.dataset.user,quest_key:b.dataset.retire},'Retire this commission? The player pays no standing and receives no reward.'));
}
/* ---------------------------------------------------------------------------
   Quests (v0.24.0). One page for every definition a player can be given -
   forged, hand-written, or a commission an NPC hands out. Before this the same
   quest was reviewable on two screens depending on whether it had a giver, and
   editable on neither: a drafted quest that was ninety per cent right had to be
   discarded and re-rolled. The editor here is the missing verb, and the hold
   policy is the decision that comes with it.
   --------------------------------------------------------------------------- */
let QD=null, QDRAFT=null;
const qOrigin=v=>({static:'muted',forge:'blue',world_event:'blue',commission:'warn',invented:'purple',authored:'good'}[v]||'');
const qStatus=v=>pill(String(v||'—'),v==='approved'?'good':v==='draft'?'warn':'muted');
const qJson=(v,fallback)=>{if(v===null||v===undefined||v==='')return fallback;if(typeof v!=='string')return v;try{return JSON.parse(v)}catch{return fallback}};
const qRewardText=r=>{const e=Object.entries(r||{}).filter(([,v])=>v&&(typeof v!=='object'||Object.keys(v).length));if(!e.length)return '—';return e.map(([k,v])=>typeof v==='object'?Object.entries(v).map(([i,q])=>`${esc(i)}×${q}`).join(' '):`${n(v)} ${esc(k.replace(/_/g,' '))}`).join(' · ')};
const qObjectiveText=o=>{const t=String(o.type||'');const target=String(o.target||'');const c=Math.max(1,Number(o.count||1));const label=o.label||(target?`${t} ${target}`:t);return `${esc(label)}${c>1?` ×${c}`:''}`};
const qObjectiveList=v=>{const list=qJson(v,[])||[];if(!list.length)return '<span class="muted">no objectives</span>';return list.map(o=>qObjectiveText(o)).join('<br>')};

async function loadQuests(){
 QD=await api('/api/quests');const s=QD.summary||{},cov=QD.coverage||{},vocab=QD.vocabulary||{};
 const defs=QD.definitions||[];
 const drafts=defs.filter(r=>String(r.status)==='draft');
 const pool=defs.filter(r=>String(r.status)==='approved');
 const budget=vocab.budget||{};
 app.innerHTML=`
 <div id="pageActions" hidden><button class="btn" id="qForge">Forge a draft</button><button class="btn primary" id="qNew">Write one</button></div>
 <div class="cards">
  ${[['Awaiting your review',s.drafts,'warn'],['Live pool',s.approved,''],['Held right now',s.held_active,''],['Approved, never taken',s.never_taken,'muted']]
    .map(x=>`<div class="card"><small>${x[0]}</small><div class="metric ${x[2]}">${n(x[1])}</div></div>`).join('')}
 </div>
 <div id="questOut"></div>
 <h2>Needs your review</h2>
 ${drafts.length?table([
   ['Quest',r=>`<b>${esc(r.title)}</b><div class="muted">${esc(r.quest_key)}</div><div class="muted">${esc(String(r.description||'').slice(0,160))}</div>`],
   ['Origin',r=>`${pill(String(r.source||'').replace(/_/g,' '),qOrigin(r.source))}<div class="muted">${esc(r.giver_npc||r.source_key||'—')}</div><div class="muted">${esc(r.model||'')}</div>`],
   ['Objectives',r=>qObjectiveList(r.objectives_json)],
   ['Rewards',r=>qRewardText(qJson(r.rewards_json,{}))],
   ['Review',r=>`<div class="btnrow">
      <button class="btn" data-qedit="${esc(r.quest_key)}">Edit</button>
      <button class="btn" data-qpreview="${esc(r.quest_key)}">Preview</button>
      <button class="btn" data-qreview="${esc(r.quest_key)}" data-status="approved">Approve</button>
      <button class="btn danger" data-qreview="${esc(r.quest_key)}" data-status="discarded">Discard</button></div>`],
 ],drafts):'<div class="empty">Nothing waiting. Forge a draft or write one by hand.</div>'}
 <h2>Live pool</h2>
 ${filters(`<input id="qq" placeholder="Search title or key"><select id="qorigin"><option value="">Any origin</option>${['static','forge','world_event','commission','invented','authored'].map(x=>`<option value="${x}">${x.replace(/_/g,' ')}</option>`).join('')}</select><select id="qband"><option value="">Any band</option>${[...new Set(defs.map(r=>String(r.realm_band||'')).filter(Boolean))].map(x=>`<option>${esc(x)}</option>`).join('')}</select><select id="qstatus"><option value="approved">approved</option><option value="">all</option><option value="retired">retired</option><option value="discarded">discarded</option></select>`)}
 <div id="qPool">${questPool(pool)}</div>
 <h2>Where the pool is thin</h2>
 <div class="grid3">
  <div class="card"><h3>By realm band</h3>${(cov.realm_bands||[]).map(b=>`<div class="row"><span>${esc(b.band)}</span><b class="${b.count?'':'bad'}">${n(b.count)}</b></div>`).join('')||'<div class="muted">No approved quests yet.</div>'}</div>
  <div class="card"><h3>By objective type</h3>${(cov.objective_types||[]).map(o=>`<div class="row"><span>${esc(o.type)}</span><b class="${o.count?'':'bad'}">${n(o.count)}</b></div>`).join('')}
   <div class="muted" style="margin-top:10px">Every quest is a walk-and-talk when the bottom rows sit at zero.</div></div>
  <div class="card"><h3>Places nothing points at</h3>
   <div>${(cov.unreferenced_locations||[]).slice(0,12).map(x=>pill(x)).join(' ')||'<span class="muted">Every public location is served.</span>'}</div>
   ${Number(cov.unreferenced_location_count||0)>12?`<div class="muted" style="margin-top:8px">…and ${n(Number(cov.unreferenced_location_count)-12)} more.</div>`:''}
   ${(cov.unknown_targets||[]).length?`<div class="badbox" style="margin-top:12px"><b>Approved quests pointing at nothing</b>${cov.unknown_targets.map(t=>`<div>${esc(t.quest_key)} — ${esc(t.type)} “${esc(t.target)}” is not a known ${esc(t.expects)}</div>`).join('')}</div>`:''}
  </div>
 </div>
 <h2>Held right now</h2>
 ${table([['Player','player_name'],['Quest',r=>esc(r.title||r.quest_key)],['Accepted',r=>fmtGM(r.accepted_game_minute)],
   ['Terms',r=>{const t=qJson(r.terms_json,null);return t?'<span class="good">fixed at acceptance</span>':'<span class="muted">not yet pinned</span>'}],
   ['Status',r=>pill(r.status,String(r.status)==='active'?'warn':'muted')]],(QD.held||[]).filter(r=>String(r.status)==='active'))}
 <div class="muted" style="margin-top:10px">Budget for a quest reward: ${n(budget.max_xp)} insight · ${n(budget.max_stones)} stones · ${n(budget.max_items)} items. A GM may go over; the change is audited.</div>`;
 bindQuests();
}

function questPool(rows){
 return table([
  ['Quest',r=>`<b>${esc(r.title)}</b><div class="muted">${esc(r.quest_key)}</div>`],
  ['Origin',r=>pill(String(r.source||'').replace(/_/g,' '),qOrigin(r.source))],
  ['Giver',r=>esc(r.giver_npc||'—')],
  ['Band',r=>esc(r.realm_band||'any')],
  ['Held',r=>n(r.held_now)],
  ['Completed',r=>n(r.completed)],
  ['Status',r=>qStatus(r.status)],
  ['',r=>`<div class="btnrow"><button class="btn" data-qedit="${esc(r.quest_key)}">Edit</button>
     <button class="btn" data-qpreview="${esc(r.quest_key)}">Preview</button>
     ${String(r.status)==='approved'?`<button class="btn" data-qreview="${esc(r.quest_key)}" data-status="retired">Retire</button>`
       :`<button class="btn" data-qreview="${esc(r.quest_key)}" data-status="approved">Approve</button>`}</div>`],
 ],rows);
}

function bindQuests(){
 const out=document.getElementById('questOut');
 const apply=()=>{const q=(document.getElementById('qq').value||'').toLowerCase(),o=document.getElementById('qorigin').value,b=document.getElementById('qband').value,st=document.getElementById('qstatus').value;
   const rows=(QD.definitions||[]).filter(r=>(!st||String(r.status)===st)&&(!o||String(r.source)===o)&&(!b||String(r.realm_band||'')===b)&&(!q||`${r.title} ${r.quest_key}`.toLowerCase().includes(q)));
   document.getElementById('qPool').innerHTML=questPool(rows);bindQuestRowButtons()};
 const af=document.getElementById('applyFilters');if(af)af.onclick=apply;
 document.getElementById('qNew').onclick=()=>openQuestEditor(null);
 document.getElementById('qForge').onclick=()=>openForge();
 bindQuestRowButtons();
 window.__questAct=async(action,payload,confirmText)=>{if(confirmText&&!confirm(confirmText))return;out.innerHTML='<div class="loading">Applying…</div>';
   try{const r=await adminPost(action,{...payload,reason:payload.reason||'GM dashboard quests'});out.innerHTML=resultBox(r.result??r);drawer.classList.add('hidden');setTimeout(()=>refresh().catch(()=>{}),700)}
   catch(e){out.innerHTML=resultBox(e.message,false)}};
}

function bindQuestRowButtons(){
 document.querySelectorAll('[data-qedit]').forEach(b=>b.onclick=()=>openQuestEditor(b.dataset.qedit));
 document.querySelectorAll('[data-qpreview]').forEach(b=>b.onclick=()=>openQuestPreview(b.dataset.qpreview));
 document.querySelectorAll('[data-qreview]').forEach(b=>b.onclick=()=>window.__questAct('quest.review',{quest_key:b.dataset.qreview,status:b.dataset.status},
   `Set "${b.dataset.qreview}" to ${b.dataset.status}?`));
}

function questByKey(key){return (QD.definitions||[]).find(r=>String(r.quest_key)===String(key))}

function questDraftFrom(row){
 if(!row)return {quest_key:'',title:'',description:'',objectives:[],rewards:{},giver_npc:'',realm_band:'',tier:1,deadline_game_minutes:0,requires_sect:'',reward_visibility:'shown',boast:'',hold_policy:'keep',held_now:0,status:'new'};
 return {quest_key:row.quest_key,title:row.title||'',description:row.description||'',
   objectives:(qJson(row.objectives_json,[])||[]).map(o=>({...o})),rewards:qJson(row.rewards_json,{})||{},
   giver_npc:row.giver_npc||'',realm_band:row.realm_band||'',tier:Number(row.tier||1),
   deadline_game_minutes:Number(row.deadline_game_minutes||0),requires_sect:row.requires_sect||'',
   reward_visibility:row.reward_visibility||'shown',boast:row.boast||'',hold_policy:'keep',
   held_now:Number(row.held_now||0),status:row.status||'draft'};
}

function openQuestEditor(key){QDRAFT=questDraftFrom(key?questByKey(key):null);renderQuestEditor();drawer.classList.remove('hidden')}

function renderQuestEditor(){
 const v=QD.vocabulary||{},d=QDRAFT,budget=v.budget||{};
 const types=Object.entries(v.objective_types||{});
 const targetFor=t=>((v.objective_types||{})[t]||{}).target;
 const overXp=Number(d.rewards.insight_xp||0)>Number(budget.max_xp||0);
 const overStones=Number(d.rewards.spirit_stones||0)>Number(budget.max_stones||0);
 drawerBody.innerHTML=`
 <h2>${d.quest_key?'Edit quest':'New quest'}</h2>
 <div class="tagline">${d.quest_key?pill(d.quest_key):pill('key derived from the title')} ${qStatus(d.status)}
   ${d.held_now?pill(`${d.held_now} holding it`,'warn'):pill('nobody holds this yet','muted')}</div>
 <div class="section"><h3>The quest</h3>
  <label>Title<input id="qeTitle" value="${esc(d.title)}"></label>
  <label>Summary — one or two lines, shown on the quest card<textarea id="qeDesc" rows="3">${esc(d.description)}</textarea></label>
 </div>
 <div class="section"><h3>Who can be given it</h3>
  <div class="grid3">
   <label>Realm band<input id="qeBand" value="${esc(d.realm_band)}" placeholder="e.g. 0-3"></label>
   <label>Given by<input id="qeGiver" value="${esc(d.giver_npc)}" list="qeNpcs" placeholder="nobody — found in the world"></label>
   <label>Tier<input id="qeTier" type="number" min="1" max="9" value="${Number(d.tier||1)}"></label>
  </div>
  <div class="grid3">
   <label>Deadline (game minutes, 0 = none)<input id="qeDeadline" type="number" min="0" value="${Number(d.deadline_game_minutes||0)}"></label>
   <label>Requires sect<input id="qeSect" value="${esc(d.requires_sect)}" placeholder="none"></label>
   <label>Rewards<select id="qeVisibility"><option value="shown"${d.reward_visibility==='shown'?' selected':''}>stated up front</option><option value="hidden"${d.reward_visibility==='hidden'?' selected':''}>the giver will not say</option></select></label>
  </div>
  <div class="muted">A quest with a giver is a commission: it occupies the one-at-a-time slot and carries his deadline. One with no giver sits in the world pool.</div>
 </div>
 <div class="section"><h3>Objectives — ${d.objectives.length} of ${n(v.max_objectives)}</h3>
  <div id="qeObjectives">${d.objectives.map((o,i)=>`
   <div class="card" style="margin-bottom:8px" data-obj="${i}">
    <div class="grid3">
     <label>Type<select data-otype="${i}">${types.map(([t])=>`<option value="${esc(t)}"${String(o.type)===t?' selected':''}>${esc(t)}</option>`).join('')}</select></label>
     <label>Target${targetFor(o.type)?'':' (none for this type)'}<input data-otarget="${i}" value="${esc(o.target||'')}" ${targetFor(o.type)?`list="${targetFor(o.type)==='location'?'qeLocations':targetFor(o.type)==='npc'?'qeNpcs':'qeScene'}"`:'disabled'}></label>
     <label>Count<input data-ocount="${i}" type="number" min="1" max="${n(v.max_objective_count)}" value="${Math.max(1,Number(o.count||1))}"></label>
    </div>
    <button class="btn danger" data-odel="${i}">Remove</button>
   </div>`).join('')||'<div class="empty">No objectives yet. A quest needs at least one.</div>'}</div>
  <button class="btn" id="qeAdd"${d.objectives.length>=Number(v.max_objectives||4)?' disabled':''}>+ Add objective</button>
  <div class="muted" style="margin-top:8px">Only these types can be completed by the engine, so only these can be chosen.</div>
 </div>
 <div class="section"><h3>Rewards on completion</h3>
  <div class="grid3">
   <label>Insight XP<input id="qeXp" type="number" min="0" value="${Number(d.rewards.insight_xp||0)}" class="${overXp?'bad':''}"></label>
   <label>Spirit stones<input id="qeStones" type="number" min="0" value="${Number(d.rewards.spirit_stones||0)}" class="${overStones?'bad':''}"></label>
   <label>Items — id×qty, comma separated<input id="qeItems" value="${esc(Object.entries(d.rewards.items||{}).map(([i,q])=>`${i}×${q}`).join(', '))}" list="qeItemIds"></label>
  </div>
  <div class="${overXp||overStones?'bad':'muted'}">Budget: ${n(budget.max_xp)} insight · ${n(budget.max_stones)} stones · ${n(budget.max_items)} items.</div>
 </div>
 ${d.held_now?`<div class="section"><h3>${d.held_now} cultivator${d.held_now===1?'':'s'} already holding this</h3>
  <label><select id="qeHold">
   <option value="keep">Leave them on the terms they took — recommended</option>
   <option value="migrate">Move them onto the new terms — progress carries where the objective did not change</option>
   <option value="revoke">Take it back from all of them — they can accept the fixed version fresh</option>
  </select></label>
  <div class="muted">Their copy was frozen when they accepted it. Migrating drops progress on any objective that now asks for something else; the count comes back in the result.</div>
 </div>`:''}
 <div class="section" style="display:flex;gap:8px;flex-wrap:wrap">
  <button class="btn primary" id="qeSave">Save${d.held_now?' —':''}${d.held_now?' <span id="qeHoldLabel">keep the holders</span>':''}</button>
  <button class="btn" id="qePreview">Preview as a player</button>
  ${d.quest_key&&d.status!=='approved'?`<button class="btn" id="qeApprove">Approve</button>`:''}
  ${d.quest_key&&d.status==='approved'?`<button class="btn" id="qeRetire">Retire</button>`:''}
  ${d.quest_key?`<button class="btn danger" id="qeDiscard">Discard</button>`:''}
 </div>
 <div id="qeOut"></div>
 <datalist id="qeLocations">${(v.locations||[]).map(x=>`<option value="${esc(x)}">`).join('')}</datalist>
 <datalist id="qeNpcs">${(v.npcs||[]).map(x=>`<option value="${esc(x)}">`).join('')}</datalist>
 <datalist id="qeScene">${(v.scene_actions||[]).map(x=>`<option value="${esc(x)}">`).join('')}</datalist>
 <datalist id="qeItemIds">${(v.items||[]).map(x=>`<option value="${esc(x)}">`).join('')}</datalist>`;
 bindQuestEditor();
}

function readQuestEditor(){
 const d=QDRAFT;
 d.title=document.getElementById('qeTitle').value.trim();
 d.description=document.getElementById('qeDesc').value.trim();
 d.realm_band=document.getElementById('qeBand').value.trim();
 d.giver_npc=document.getElementById('qeGiver').value.trim();
 d.tier=Math.max(1,Number(document.getElementById('qeTier').value||1));
 d.deadline_game_minutes=Math.max(0,Number(document.getElementById('qeDeadline').value||0));
 d.requires_sect=document.getElementById('qeSect').value.trim();
 d.reward_visibility=document.getElementById('qeVisibility').value;
 const items={};document.getElementById('qeItems').value.split(',').map(x=>x.trim()).filter(Boolean).forEach(part=>{
   const m=part.split(/[×x*]/);const id=(m[0]||'').trim();const qty=Math.max(1,Number((m[1]||'1').trim())||1);if(id)items[id]=qty});
 d.rewards={};
 const xp=Number(document.getElementById('qeXp').value||0);if(xp>0)d.rewards.insight_xp=xp;
 const stones=Number(document.getElementById('qeStones').value||0);if(stones>0)d.rewards.spirit_stones=stones;
 if(Object.keys(items).length)d.rewards.items=items;
 const hold=document.getElementById('qeHold');d.hold_policy=hold?hold.value:'keep';
 return d;
}

function bindQuestEditor(){
 const d=QDRAFT,out=document.getElementById('qeOut');
 document.querySelectorAll('[data-otype]').forEach(el=>el.onchange=()=>{readQuestEditor();d.objectives[Number(el.dataset.otype)].type=el.value;d.objectives[Number(el.dataset.otype)].target='';renderQuestEditor()});
 document.querySelectorAll('[data-otarget]').forEach(el=>el.onchange=()=>{d.objectives[Number(el.dataset.otarget)].target=el.value.trim()});
 document.querySelectorAll('[data-ocount]').forEach(el=>el.onchange=()=>{d.objectives[Number(el.dataset.ocount)].count=Math.max(1,Number(el.value||1))});
 document.querySelectorAll('[data-odel]').forEach(el=>el.onclick=()=>{readQuestEditor();d.objectives.splice(Number(el.dataset.odel),1);renderQuestEditor()});
 const add=document.getElementById('qeAdd');if(add)add.onclick=()=>{readQuestEditor();d.objectives.push({type:'explore',target:'',count:1});renderQuestEditor()};
 const hold=document.getElementById('qeHold');const label=document.getElementById('qeHoldLabel');
 if(hold&&label)hold.onchange=()=>{label.textContent={keep:'keep the holders',migrate:'move the holders',revoke:'take it back'}[hold.value]};
 document.getElementById('qeSave').onclick=async()=>{
   const draft=readQuestEditor();out.innerHTML='<div class="loading">Saving…</div>';
   try{const r=await adminPost('quest.save',{...draft,reason:'GM dashboard quest editor'});
     out.innerHTML=resultBox(r.result??r);setTimeout(()=>refresh().catch(()=>{}),700)}
   catch(e){out.innerHTML=resultBox(e.message,false)}};
 document.getElementById('qePreview').onclick=()=>{readQuestEditor();openQuestPreview(null)};
 const approve=document.getElementById('qeApprove');
 if(approve)approve.onclick=()=>window.__questAct('quest.review',{quest_key:d.quest_key,status:'approved'},`Approve "${d.title}" and add it to the live pool?`);
 const retire=document.getElementById('qeRetire');
 if(retire)retire.onclick=()=>window.__questAct('quest.review',{quest_key:d.quest_key,status:'retired'},'Retire this quest? Nobody new may take it; everyone holding it keeps it and can still finish it.');
 const discard=document.getElementById('qeDiscard');
 if(discard)discard.onclick=()=>window.__questAct('quest.review',{quest_key:d.quest_key,status:'discarded'},'Discard this quest?');
}

function openQuestPreview(key){
 const row=key?questByKey(key):null;
 const d=row?questDraftFrom(row):QDRAFT;
 if(!d)return;
 const objectives=(d.objectives||[]).map(o=>`<div>▫️ ${qObjectiveText(o)}</div>`).join('')||'<div class="muted">No objectives.</div>';
 const hidden=d.reward_visibility==='hidden';
 drawerBody.innerHTML=`<h2>${esc(d.title||'Untitled quest')}</h2>
  <div class="tagline">${pill(d.quest_key||'unsaved')} ${qStatus(d.status)} ${d.held_now?pill(`${d.held_now} holding it`,'warn'):''}</div>
  <div class="section"><h3>What the player is shown</h3>
   <div class="card"><b class="gold">${esc(d.title||'Untitled quest')}</b>
    <p>${esc(d.description||'')}</p>
    <div><b>Asked of you</b>${objectives}</div>
    <div style="margin-top:8px"><b>Offered</b><div>${hidden?'<span class="muted">He will not say what it pays.</span>':qRewardText(d.rewards)}</div></div>
    ${d.giver_npc?`<div class="muted" style="margin-top:8px">Offered by ${esc(d.giver_npc)}${d.deadline_game_minutes?` · ${Math.round(d.deadline_game_minutes/1440)} day deadline`:''}</div>`:''}
   </div>
   ${hidden?'<div class="muted">Presentation only: the engine still locks exact terms at acceptance and pays exactly those.</div>':''}
  </div>
  <div class="section"><h3>What the engine stores</h3>
   <div class="code">${esc(JSON.stringify({quest_key:d.quest_key,realm_band:d.realm_band,giver_npc:d.giver_npc,tier:d.tier,objectives:d.objectives,rewards:d.rewards},null,2))}</div>
  </div>
  <div class="section" style="display:flex;gap:8px">
   <button class="btn" id="qpBack">Back to editing</button>
   ${d.quest_key&&d.status!=='approved'?'<button class="btn primary" id="qpApprove">Approve — add to the live pool</button>':''}
  </div>`;
 drawer.classList.remove('hidden');
 document.getElementById('qpBack').onclick=()=>{QDRAFT=d;renderQuestEditor()};
 const ap=document.getElementById('qpApprove');
 if(ap)ap.onclick=()=>window.__questAct('quest.review',{quest_key:d.quest_key,status:'approved'},`Approve "${d.title}"?`);
}

function openForge(){
 drawerBody.innerHTML=`<h2>Forge a draft</h2>
  <p class="muted">The Forge is handed the world's locations, NPCs and items, so it can only name things that exist. Every draft is checked before you see it, and lands in <b>Needs your review</b> — nothing reaches a player until you approve it.</p>
  <div class="section">
   <label>What should come out of it<textarea id="qfStory" rows="5" placeholder="Something a low-realm cultivator can do alone. No fighting. The marsh flood uncovered records the local administration would rather stayed buried."></textarea></label>
   <button class="btn primary" id="qfGo">Forge it</button>
   <div class="muted" style="margin-top:8px">A sentence or two at least. Three words is a title, not a brief.</div>
  </div>
  <div id="qfOut"></div>`;
 drawer.classList.remove('hidden');
 document.getElementById('qfGo').onclick=async()=>{
  const story=document.getElementById('qfStory').value.trim();const out=document.getElementById('qfOut');
  out.innerHTML='<div class="loading">Forging… this asks a free-tier model and can take a moment.</div>';
  try{const r=await discordPost('quest.forge',{story});
    out.innerHTML=resultBox(r.result??r,r.ok!==false);
    if(r.ok!==false)setTimeout(()=>{drawer.classList.add('hidden');refresh().catch(()=>{})},1200)}
  catch(e){out.innerHTML=resultBox(e.message,false)}};
}

const loaders={overview:loadOverview,timeline:loadTimeline,npcs:loadNPCs,families:loadFamilies,sects:loadSects,conflicts:loadConflicts,events:loadEvents,players:loadPlayers,cultivation:loadCultivation,crafting:loadCrafting,exploration:loadExploration,commissions:loadCommissions,quests:loadQuests,economy:loadEconomy,dynasties:loadDynasties,party:loadParty,pvp:loadPvp,conditions:loadConditions,threads:loadThreads,rag:loadRag,decisions:loadDecisions,discord:loadDiscordSetup,ai_routing:loadAiRouting,narration:loadNarration,admin:loadAdmin};

setDensity(density());
bindShell();
const boot=String(location.hash||'').replace('#','').split('/')[0]||'overview';
if(boot!=='overview'){
  api('/api/overview').then(d=>{document.getElementById('worldClock').textContent=(d.clock||{}).display||'—'}).catch(()=>{});
}
switchView(boot);
// Overview is the only view that refreshes itself; everything else is read on
// demand. Fifteen seconds is the interval it has always used.
setInterval(()=>{if(CURRENT==='overview')refresh().catch(()=>{})},15000);

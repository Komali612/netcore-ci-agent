"""The CI agent's browser console — served at GET / by ci_service.py.

Enter a repo URL, your GitHub token, and per-tool credentials, then click Run CI.
The button POSTs the REAL values to POST /ci (a direct, non-LLM channel), which
runs Discover -> Generate -> Validate -> set Actions secrets -> open a real PR.

This is the credentialed path, so values are sent as entered (no masking). Run it
over HTTPS in any shared/production setting.
"""

CI_CONSOLE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NetCore CI Agent</title>
<style>
  :root { color-scheme: light dark; --bd:#8883; --accent:#3b82f6; --muted:#8a94a6; --card:#8881; --warn:#d97706; }
  * { box-sizing: border-box; }
  body { font: 15px/1.55 system-ui, -apple-system, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.4rem; margin: 0 0 .2rem; }
  .sub { opacity: .6; margin-bottom: 1rem; font-size: .9rem; }
  label.fld { font-weight: 600; font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; opacity: .72; display:block; margin-bottom:.25rem; }
  input[type=text], input[type=password], select { width: 100%; padding: .6rem .7rem; font: 14px ui-monospace, monospace; border: 1px solid var(--bd); border-radius: 8px; background: var(--card); color: inherit; }
  .hint { font-size: .74rem; opacity: .55; margin-top: .2rem; }
  .tokrow { border: 1px solid var(--warn); background: #d9770614; border-radius: 10px; padding: .7rem .85rem; margin-top: .8rem; }
  details.sec { border: 1px solid var(--bd); border-radius: 12px; margin: .7rem 0 0; overflow: hidden; }
  details.sec > summary { list-style: none; cursor: pointer; padding: .7rem .95rem; display:flex; align-items:center; gap:.55rem; font-weight:700; font-size:.82rem; text-transform:uppercase; letter-spacing:.05em; opacity:.82; }
  details.sec > summary::-webkit-details-marker { display:none; }
  details.sec > summary .chev { transition: transform .18s ease; opacity:.6; font-weight:400; }
  details.sec[open] > summary .chev { transform: rotate(90deg); }
  details.sec[open] > summary { border-bottom: 1px solid var(--bd); }
  .body { padding: .5rem 1rem 1rem; }
  .catrow { padding: .7rem 0; border-bottom: 1px dashed var(--bd); }
  .catrow:last-child { border-bottom: 0; }
  .catfields { margin-top:.6rem; }
  .catfields .none { font-size:.82rem; opacity:.6; font-style:italic; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem 1rem; }
  @media (max-width: 640px){ .grid { grid-template-columns: 1fr; } }
  .opts { display:flex; gap:1rem 1.2rem; flex-wrap:wrap; align-items:center; margin:.9rem 0 .2rem; }
  label.chk { font-size:.87rem; opacity:.9; display:flex; align-items:center; gap:.4rem; cursor:pointer; }
  button { width:100%; margin-top:.8rem; padding:.8rem 1rem; font-size:.98rem; font-weight:700; cursor:pointer; border:0; border-radius:9px; background:var(--accent); color:#fff; }
  button:disabled { opacity:.5; cursor:wait; }
  pre { margin:.3rem 0 0; padding:.9rem; background:var(--card); border:1px solid var(--bd); border-radius:10px; white-space:pre-wrap; word-break:break-word; font:12px ui-monospace,monospace; max-height:360px; overflow:auto; }
  a { color: var(--accent); }
</style></head>
<body>
  <h1>.NET Core CI Agent</h1>
  <div class="sub">Give a repo + your GitHub token, pick tools, and open a real CI pull request.</div>

  <label class="fld" for="repo">Git repository URL</label>
  <input id="repo" type="text" placeholder="https://github.com/your-org/your-repo.git">

  <div class="tokrow">
    <label class="fld" for="ghtoken">GitHub token — repo + workflow scope</label>
    <input id="ghtoken" type="password" placeholder="ghp_… (used only to open the PR and set Actions secrets)">
    <div class="hint">Classic PAT (<code>repo</code> + <code>workflow</code>), or a fine-grained PAT with <code>Contents</code>, <code>Pull requests</code>, <code>Workflows</code> &amp; <code>Secrets</code> = read/write on the target repo. Sent only to this local agent, never stored.</div>
  </div>

  <details class="sec" open>
    <summary><span class="chev">&#9654;</span> CI tools &amp; credentials</summary>
    <div class="body" id="ciHost"></div>
  </details>

  <div class="opts">
    <label class="chk"><input id="optPr" type="checkbox" checked> open pull request</label>
    <label class="chk"><input id="optSecrets" type="checkbox" checked> set GitHub Actions secrets</label>
    <label class="chk"><input id="optDast" type="checkbox" checked> include DAST</label>
  </div>

  <button id="runCi">Run CI Agent &#9654;</button>
  <pre id="out">Fill the form and click Run CI Agent. The opened PR link will appear here.</pre>

  <script>
    const $ = id => document.getElementById(id);
    const F = (id,label,type,ph) => ({id,label,type:type||'text',ph:ph||''});
    const CI_CATS = [
      { key:'coverage', label:'Code coverage & quality', tools:{
        'SonarQube':[F('url','Host URL','text','https://sonarcloud.io'),F('token','Token','password')],
        'SonarCloud':[F('org','Organization','text'),F('token','Token','password')],
        'Codecov':[F('token','Upload token','password')] }},
      { key:'sast', label:'SAST (static analysis)', tools:{
        'Fortify SSC':[F('url','SSC URL','text'),F('token','Auth token','password')],
        'Checkmarx':[F('url','Base URL','text'),F('id','Client ID','text'),F('secret','Client secret','password')],
        'Semgrep':[F('token','App token','password')] }},
      { key:'sca', label:'SCA (dependency scan)', tools:{
        'Sonatype Nexus IQ':[F('url','IQ Server URL','text'),F('token','Auth token','password')],
        'Snyk':[F('org','Organization ID','text'),F('token','API token','password')] }},
      { key:'imgscan', label:'Container image scan', tools:{
        'Wiz':[F('id','Client ID','text'),F('secret','Client secret','password')],'Trivy':[],
        'Aqua':[F('url','Server URL','text'),F('token','Token','password')] }},
      { key:'artifact', label:'Artifact repository', tools:{
        'Nexus':[F('url','Repository URL','text'),F('user','Username','text'),F('pass','Password','password')],
        'JFrog Artifactory':[F('url','Base URL','text'),F('token','Access token','password')],
        'GitHub Packages':[F('owner','Owner / org','text'),F('pat','Personal access token','password')] }},
      { key:'registry', label:'Container registry', tools:{
        'GHCR (built-in token)':[],
        'Azure ACR':[F('server','Login server','text'),F('user','Username','text'),F('pass','Password','password')],
        'Docker Hub':[F('user','Username','text'),F('token','Access token','password')] }},
    ];
    function renderCategory(host, cat){
      const wrap=document.createElement('div'); wrap.className='catrow';
      const lab=document.createElement('label'); lab.className='fld'; lab.textContent=cat.label;
      const sel=document.createElement('select'); sel.id='sel__'+cat.key;
      sel.innerHTML='<option value="">— not used —</option>'+Object.keys(cat.tools).map(t=>'<option>'+t+'</option>').join('');
      const fields=document.createElement('div'); fields.id='f__'+cat.key; fields.className='catfields';
      sel.onchange=()=>{ fields.innerHTML=''; const t=sel.value; if(!t) return;
        const defs=cat.tools[t]; if(!defs.length){ fields.innerHTML='<div class="none">No credentials required — runs in-pipeline.</div>'; return; }
        const grid=document.createElement('div'); grid.className='grid';
        defs.forEach(fd=>{ const d=document.createElement('div');
          d.innerHTML='<label class="fld">'+fd.label+'</label><input id="fld__'+cat.key+'__'+fd.id+'" type="'+fd.type+'" placeholder="'+fd.ph+'">';
          grid.appendChild(d); });
        fields.appendChild(grid); };
      wrap.append(lab,sel,fields); host.appendChild(wrap);
    }
    CI_CATS.forEach(c=>renderCategory($('ciHost'),c));

    // Direct credentialed channel: send REAL values (no masking) to /ci.
    function collect(cats){ const out={};
      cats.forEach(cat=>{ const t=($('sel__'+cat.key)||{}).value; if(!t) return;
        const vals={tool:t};
        (cat.tools[t]||[]).forEach(fd=>{ const el=$('fld__'+cat.key+'__'+fd.id); const v=el?el.value.trim():''; if(v) vals[fd.label]=v; });
        out[cat.label]=vals; });
      return out; }

    async function run(){
      const repo=$('repo').value.trim(), token=$('ghtoken').value.trim();
      if(!repo){ $('out').textContent='Enter a Git repository URL first.'; return; }
      if(!token){ $('out').textContent='Enter a GitHub token (repo + workflow scope) first.'; return; }
      const spec={ repo_url:repo, github_token:token,
        options:{ open_pr:$('optPr').checked, set_secrets:$('optSecrets').checked, include_dast:$('optDast').checked },
        selected_tools:collect(CI_CATS) };
      $('out').textContent='Running CI agent — discovering, generating, validating, opening PR…';
      $('runCi').disabled=true;
      try{
        const r=await fetch('/ci',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(spec)});
        const t=await r.text(); let d; try{ d=JSON.parse(t); }catch{ $('out').textContent=t; return; }
        if(d.pr && d.pr.pr_url){
          $('out').innerHTML='✅ <b>PR opened:</b> <a href="'+d.pr.pr_url+'" target="_blank" rel="noopener">'+d.pr.pr_url+'</a>\\n\\n'+JSON.stringify(d,null,2);
        } else {
          $('out').textContent=(r.ok?'':'HTTP '+r.status+'\\n')+JSON.stringify(d,null,2);
        }
      }catch(e){ $('out').textContent='Error: '+e; }
      finally{ $('runCi').disabled=false; }
    }
    $('runCi').onclick=run;
  </script>
</body></html>
"""

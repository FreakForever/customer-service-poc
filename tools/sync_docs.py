#!/usr/bin/env python3
"""
Docs-as-code builder and traceability gate for the Matsui CS POC.

Sources of truth
  docs/requirements-registry.json   requirements (hand-edited, except trace.build_steps / trace.tests)
  docs/build-steps.json             build plan + progress (edited by the build loop)
  docs-src/{requirements,hld,lld}.md  prose for the three HTML documents
  docs-src/theme.css                shared Matsui theme

Generated
  docs/Requirements.html, docs/HighLevelDesign.html, docs/LLD.html
  trace.build_steps / trace.tests inside the registry

Usage
  python tools/sync_docs.py          # validate, update traces, regenerate HTML
  python tools/sync_docs.py --check  # CI: fail if invalid or anything would change
Requires: pip install markdown
"""
import hashlib
import html
import json
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DOCS, SRC = ROOT / "docs", ROOT / "docs-src"
REG_P, BS_P = DOCS / "requirements-registry.json", DOCS / "build-steps.json"
PAGES = [
    ("requirements.md", "Requirements.html", "Requirements", "REQ"),
    ("hld.md", "HighLevelDesign.html", "High-level design", "HLD"),
    ("lld.md", "LLD.html", "Low-level design & build", "LLD"),
]


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def dumps(o):
    return json.dumps(o, indent=2, ensure_ascii=False) + "\n"


def validate(reg, bs):
    errs = []
    reqs = reg["requirements"]
    ids = [r["id"] for r in reqs]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errs.append(f"duplicate requirement ids: {sorted(dup)}")
    idset = set(ids)
    steps = bs["steps"]
    sids = [s["id"] for s in steps]
    if len(sids) != len(set(sids)):
        errs.append("duplicate build step ids")
    sset = set(sids)
    covered = set()
    for s in steps:
        for r in s["implements"]:
            if r not in idset:
                errs.append(f"{s['id']} implements unknown requirement {r}")
            covered.add(r)
        for d in s["depends_on"]:
            if d not in sset:
                errs.append(f"{s['id']} depends on unknown step {d}")
        if s["status"] not in bs["meta"]["status_values"]:
            errs.append(f"{s['id']} bad status {s['status']}")
        if not (0 <= s["progress_pct"] <= 100):
            errs.append(f"{s['id']} progress_pct out of range")
    # cycle check
    graph = {s["id"]: s["depends_on"] for s in steps}
    seen, stack = set(), set()

    def dfs(n):
        if n in stack:
            errs.append(f"dependency cycle at {n}")
            return
        if n in seen:
            return
        stack.add(n)
        for m in graph.get(n, []):
            dfs(m)
        stack.discard(n)
        seen.add(n)

    for n in graph:
        dfs(n)
    for r in reqs:
        if r.get("status") == "deprecated":
            continue
        if r["type"] == "business":
            if not r.get("children"):
                errs.append(f"{r['id']} has no children")
            for c in r.get("children", []):
                if c not in idset:
                    errs.append(f"{r['id']} child {c} unknown")
        elif r["id"] not in covered:
            errs.append(f"{r['id']} is not implemented by any build step")
    return errs


def apply_traces(reg, bs):
    by_req = {}
    for s in bs["steps"]:
        for r in s["implements"]:
            by_req.setdefault(r, []).append(s["id"])
    for r in reg["requirements"]:
        if r["type"] == "business":
            r["trace"]["build_steps"] = sorted({b for c in r.get("children", []) for b in by_req.get(c, [])})
            r["trace"]["tests"] = []
        else:
            r["trace"]["build_steps"] = by_req.get(r["id"], [])
            r["trace"]["tests"] = [f"TC-{r['id']}"]
    return reg


def render_md(text):
    md = markdown.Markdown(extensions=["tables", "fenced_code", "attr_list", "md_in_html", "sane_lists"])
    out = md.convert(text).replace("\\|", "|")  # escaped pipes inside table cells
    out = re.sub(r'<pre><code class="language-mermaid">(.*?)</code></pre>', lambda m: f'<pre class="mermaid">{m.group(1)}</pre>', out, flags=re.S)
    return out


def toc(body):
    items = []
    for lvl, hid, txt in re.findall(r'<h([23])[^>]*id="([^"]+)"[^>]*>(.*?)</h\1>', body, flags=re.S):
        t = re.sub("<[^>]+>", "", txt)
        items.append(f'<a class="l{lvl}" href="#{hid}">{t}</a>')
    return "\n".join(items)


def safe_json(o):
    return json.dumps(o, ensure_ascii=False).replace("</", "<\\/")


REQ_JS = r"""
const REG = JSON.parse(document.getElementById('registry-data').textContent);
const BS = JSON.parse(document.getElementById('build-steps-data').textContent);
const stepById = Object.fromEntries(BS.steps.map(s => [s.id, s]));
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function prog(r){const ss=(r.trace.build_steps||[]).map(i=>stepById[i]).filter(Boolean); if(!ss.length) return null; return Math.round(ss.reduce((a,s)=>a+s.progress_pct,0)/ss.length);}
function renderReg(){
  const app=document.getElementById('registry-app'); if(!app) return;
  const reqs=REG.requirements, uniq=k=>[...new Set(reqs.map(r=>r[k]))];
  const opt=(id,label,vals)=>`<select id="${id}"><option value="">${label}: all</option>${vals.map(v=>`<option>${esc(v)}</option>`).join('')}</select>`;
  const n=t=>reqs.filter(r=>r.type===t).length;
  app.innerHTML=`<div class="kpis"><div class="kpi"><b>${reqs.length}</b><span>requirements</span></div><div class="kpi"><b>${n('business')}</b><span>business</span></div><div class="kpi"><b>${n('functional')}</b><span>functional</span></div><div class="kpi"><b>${n('non-functional')}</b><span>non-functional</span></div><div class="kpi"><b>${n('data')}</b><span>data</span></div><div class="kpi"><b>${reqs.filter(r=>r.priority==='Must').length}</b><span>Must-have</span></div></div>
  <div class="toolbar"><input id="q" placeholder="Search id, title, text…" size="32">${opt('ft','Type',uniq('type'))}${opt('fa','Area',uniq('area'))}${opt('fp','Priority',uniq('priority'))}${opt('fs','Status',uniq('status'))}<span class="small" id="cnt"></span></div>
  <table><thead><tr><th style="width:110px">ID</th><th>Requirement</th><th style="width:80px">Priority</th><th style="width:230px">Trace</th><th style="width:120px">Build progress</th></tr></thead><tbody id="rb"></tbody></table>`;
  const draw=()=>{const q=document.getElementById('q').value.toLowerCase(), f={type:ft.value,area:fa.value,priority:fp.value,status:fs.value};
    const rows=reqs.filter(r=>Object.entries(f).every(([k,v])=>!v||r[k]===v)&&(!q||JSON.stringify(r).toLowerCase().includes(q)));
    document.getElementById('cnt').textContent=rows.length+' shown';
    document.getElementById('rb').innerHTML=rows.map(r=>{const p=prog(r);
      const links=[...(r.trace.hld||[]).map(h=>`<a href="HighLevelDesign.html#${h}">${h}</a>`),...(r.trace.lld||[]).map(h=>`<a href="LLD.html#${h}">${h}</a>`)].join(' · ');
      const bsl=(r.trace.build_steps||[]).map(b=>`<a href="LLD.html#${b}">${b}</a>`).join(' ');
      const kids=r.children?`<div class="small">Children: ${r.children.map(c=>`<a href="#${c}">${c}</a>`).join(', ')}</div>`:'';
      return `<tr id="${r.id}"><td><b>${r.id}</b><div class="small">${esc(r.area)}</div><span class="pill s-${r.status}">${r.status}</span></td>
      <td><b>${esc(r.title)}</b><div>${esc(r.statement)}</div><details><summary class="small">Acceptance criteria · source: ${esc(r.source)} · verify by ${esc(r.verification)}</summary><ul>${r.acceptance.map(a=>`<li>${esc(a)}</li>`).join('')}</ul></details>${kids}</td>
      <td><span class="pill p-${r.priority.split(' ')[0]}">${esc(r.priority)}</span></td>
      <td class="small">${links||'—'}<br>Steps: ${bsl||'—'}<br>Tests: ${(r.trace.tests||[]).join(', ')||'—'}</td>
      <td>${p===null?'<span class="small">via children</span>':`<div class="bar"><i style="width:${p}%"></i></div><span class="small">${p}%</span>`}</td></tr>`}).join('');};
  ['q','ft','fa','fp','fs'].forEach(i=>document.getElementById(i).addEventListener('input',draw)); draw();
  if(location.hash){setTimeout(()=>document.querySelector(location.hash)?.scrollIntoView(),50)}
}
function renderMatrix(){
  const m=document.getElementById('trace-matrix'); if(!m) return;
  const reqs=REG.requirements.filter(r=>r.type!=='business');
  const head=BS.steps.map(s=>`<th title="${esc(s.title)}" style="writing-mode:vertical-rl;transform:rotate(180deg);font-size:11px;padding:6px 2px"><a href="LLD.html#${s.id}">${s.id}</a></th>`).join('');
  const body=reqs.map(r=>`<tr><td style="white-space:nowrap"><a href="#${r.id}">${r.id}</a></td>${BS.steps.map(s=>`<td style="text-align:center;padding:2px">${s.implements.includes(r.id)?'<span title="'+s.id+' implements '+r.id+'" style="color:var(--mt-blue)">●</span>':''}</td>`).join('')}</tr>`).join('');
  m.innerHTML=`<div style="overflow:auto"><table style="width:auto"><thead><tr><th>Req \\ Step</th>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
renderReg(); renderMatrix();
"""

BS_JS = r"""
const BS = JSON.parse(document.getElementById('build-steps-data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
(function(){
  const app=document.getElementById('build-steps-app'); if(!app) return;
  const S=BS.steps, avg=a=>a.length?Math.round(a.reduce((x,s)=>x+s.progress_pct,0)/a.length):0, done=S.filter(s=>s.status==='done').length;
  const phases=BS.phases.map(p=>{const ss=S.filter(s=>s.phase===p.id);return `<div class="kpi"><span>${p.id} · ${esc(p.name)}</span><b>${avg(ss)}%</b><div class="bar"><i style="width:${avg(ss)}%"></i></div><span>${ss.filter(s=>s.status==='done').length}/${ss.length} steps done</span></div>`}).join('');
  app.innerHTML=`<div class="callout"><b>Overall progress: ${avg(S)}%</b> · ${done}/${S.length} steps done · ${S.filter(s=>s.status==='in_progress').length} in progress · ${S.filter(s=>s.status==='blocked').length} blocked<div class="bar big" style="margin-top:8px"><i style="width:${avg(S)}%"></i></div><div class="small" style="margin-top:6px">Generated from docs/build-steps.json at ${esc(BS._generated||'')}. Update the JSON, then run <code>python tools/sync_docs.py</code>.</div></div>
  <div class="kpis">${phases}</div>
  <table><thead><tr><th style="width:80px">Step</th><th>What to build</th><th style="width:150px">Implements</th><th style="width:250px">Checklist & verify</th><th style="width:90px">Status</th><th style="width:120px">Progress</th></tr></thead><tbody>
  ${S.map(s=>`<tr id="${s.id}"><td><b>${s.id}</b><div class="small">${s.phase}</div></td>
   <td><b>${esc(s.title)}</b><div>${esc(s.goal)}</div><div class="small">Depends on: ${s.depends_on.map(d=>`<a href="#${d}">${d}</a>`).join(', ')||'—'} · LLD: ${s.lld_ref.map(l=>`<a href="#${l}">${l}</a>`).join(', ')}</div>${s.notes?`<div class="small"><b>Notes:</b> ${esc(s.notes)}</div>`:''}</td>
   <td class="small">${s.implements.map(r=>`<a href="Requirements.html#${r}">${r}</a>`).join('<br>')}</td>
   <td class="small"><details><summary>Checklist (${s.checklist.length})</summary><ul>${s.checklist.map(c=>`<li>${esc(c)}</li>`).join('')}</ul></details><details><summary>Verify</summary><ul>${s.verify.map(c=>`<li><code>${esc(c)}</code></li>`).join('')}</ul></details></td>
   <td><span class="pill s-${s.status}">${s.status.replace('_',' ')}</span>${s.completed?`<div class="small">${esc(s.completed)}</div>`:''}</td>
   <td><div class="bar"><i style="width:${s.progress_pct}%"></i></div><span class="small">${s.progress_pct}%</span></td></tr>`).join('')}
  </tbody></table>`;
  if(location.hash){setTimeout(()=>document.querySelector(location.hash)?.scrollIntoView(),50)}
})();
"""


def page(title, out_name, body, reg, bs, version, gen_time):
    css = (SRC / "theme.css").read_text(encoding="utf-8")
    nav = "".join(f'<a href="{o}" class="{"active" if o == out_name else ""}">{t}</a>' for _, o, t, _ in PAGES)
    data, js = "", ""
    if out_name == "Requirements.html":
        data = f'<script type="application/json" id="registry-data">{safe_json(reg)}</script>\n<script type="application/json" id="build-steps-data">{safe_json(bs)}</script>'
        js = REQ_JS
    elif out_name == "LLD.html":
        data = f'<script type="application/json" id="build-steps-data">{safe_json(bs)}</script>'
        js = BS_JS
    mermaid = ""
    if 'class="mermaid"' in body:
        # vendored copy (docs/assets/mermaid.min.js) so diagrams render offline; CDN fallback if missing
        mermaid = ('<script src="assets/mermaid.min.js"></script>'
                   '<script>(function(){function go(){mermaid.initialize({startOnLoad:false,theme:"base",themeVariables:{primaryColor:"#EAF2FB",'
                   'primaryBorderColor:"#0057A8",lineColor:"#5B6B7C",fontFamily:"Inter,Segoe UI,sans-serif"}});mermaid.run();}'
                   'if(window.mermaid){go();}else{var s=document.createElement("script");'
                   's.src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js";s.onload=go;document.head.appendChild(s);}})();</script>')
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} — Matsui CS POC</title>
<!-- GENERATED by tools/sync_docs.py from docs-src/ and docs/*.json. Do not edit by hand. -->
<style>{css}</style></head>
<body>
<header class="topbar"><div class="brand">MATSUI<span>Customer service POC · email + chat</span></div><span class="badge-demo">DEMO · synthetic data</span><nav>{nav}</nav></header>
<div class="layout"><aside class="toc">{toc(body)}</aside>
<main><div class="hero"><h1>{html.escape(title)}</h1><div class="meta">Project MCS · registry v{version} · generated {gen_time} · owner Aviral</div></div>
{body}
</main></div>
{data}
<script>{js}</script>
{mermaid}
</body></html>
"""


def main():
    check = "--check" in sys.argv
    reg, bs = load(REG_P), load(BS_P)
    errs = validate(reg, bs)
    reg = apply_traces(reg, bs)
    # deterministic build stamp = content hash of all sources (keeps --check stable across clones)
    h = hashlib.sha256(dumps(reg).encode() + dumps(bs).encode())
    for p in [SRC / "theme.css"] + [SRC / s for s, *_ in PAGES]:
        h.update(p.read_bytes())
    gen_time = f"{reg['registry']['date']} · build {h.hexdigest()[:8]}"
    bs_view = dict(bs, _generated=gen_time)
    outputs = {REG_P: dumps(reg)}
    anchors = {}
    for src, out, title, key in PAGES:
        body = render_md((SRC / src).read_text(encoding="utf-8"))
        anchors[key] = set(re.findall(r'id="([^"]+)"', body))
        outputs[DOCS / out] = page(title, out, body, reg, bs_view, reg["registry"]["version"], gen_time)
    for r in reg["requirements"]:
        for h in r["trace"].get("hld", []):
            if h not in anchors["HLD"]:
                errs.append(f"{r['id']} traces to missing HLD anchor {h}")
        for l in r["trace"].get("lld", []):
            if l not in anchors["LLD"]:
                errs.append(f"{r['id']} traces to missing LLD anchor {l}")
    for s in bs["steps"]:
        for l in s["lld_ref"]:
            if l not in anchors["LLD"]:
                errs.append(f"{s['id']} references missing LLD anchor {l}")
    if errs:
        print("TRACEABILITY ERRORS:\n  " + "\n  ".join(errs))
        sys.exit(1)
    changed = [p for p, c in outputs.items() if not p.exists() or p.read_text(encoding="utf-8") != c]
    if check:
        if changed:
            print("Docs out of sync, run: python tools/sync_docs.py\n  " + "\n  ".join(str(p.relative_to(ROOT)) for p in changed))
            sys.exit(1)
        print("OK: traceability valid and docs in sync")
        return
    for p in changed:
        p.write_text(outputs[p], encoding="utf-8")
    n_req = len(reg["requirements"])
    n_bs = len(bs["steps"])
    print(f"OK: {n_req} requirements, {n_bs} build steps, wrote {len(changed)} file(s)")


if __name__ == "__main__":
    main()

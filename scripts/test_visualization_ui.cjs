/* DOM contract tests in Node, not a rendered-browser or visual QA substitute. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(0, 'utf8');
const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].map(m => ({attributes:m[1],code:m[2]}));
const dataCode = scripts.find(s=>s.attributes.includes('id="atlas-data"')).code;
const iconCode = scripts.find(s=>s.attributes.includes('id="atlas-icons"')).code;
const executable = scripts.filter(s=>!s.attributes.includes('type='));
assert.equal(executable.length, 2);
const data = JSON.parse(dataCode);
class Element {
  constructor(tag='div') { this.tagName=tag;this.children=[];this.attrs={};this.dataset={};this.style={};this.value='';this.hidden=false;this.listeners={};this.className='';this.textContent='';this.open=true;this.classList={add:(n)=>this.className+=' '+n,remove:(n)=>this.className=this.className.split(' ').filter(c=>c!==n).join(' ')}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=[...items]; }
  setAttribute(name,value) { this.attrs[name]=value; }
  getAttribute(name) { return this.attrs[name]; }
  addEventListener(name,callback) { this.listeners[name]=callback; }
  dispatch(name) { if(this.listeners[name])this.listeners[name](); }
  focus() { this.focused=true; }
  setPointerCapture() {}
  getBoundingClientRect() { return {x:0,y:0,left:0,top:0,width:880,height:800}; }
}
function boot({blocked=false,saved=null,systemDark=false,narrow=false}={}) {
  const elements=new Map();
  for(const match of html.matchAll(/\bid="([^"]+)"/g))elements.set(match[1],new Element());
  elements.get('atlas-data').textContent=dataCode;elements.get('atlas-icons').textContent=iconCode;
  const themeButtons=['themeLight','themeDark','themeSystem'].map((id,i)=>{const e=elements.get(id);e.dataset.themeMode=['light','dark','system'][i];return e;});
  const images=[...html.matchAll(/data-icon="([^"]+)"/g)].map(m=>{const e=new Element('img');e.dataset.icon=m[1];return e;});
  const root=new Element(),zoomGroup=new Element();
  const medias=new Map();
  const storage=new Map(saved?[['personal-atlas.theme',saved]]:[]);
  const ctx=new Proxy({}, {get:(target,name)=>name in target?target[name]:()=>{},set:(target,name,value)=>(target[name]=value,true)});
  elements.get('graph').getContext=()=>ctx;
  const document={documentElement:root,getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),querySelectorAll:selector=>selector==='[data-theme-mode]'?themeButtons:selector==='img[data-icon]'?images:[],querySelector:selector=>selector==='.zoom-group'?zoomGroup:null,addEventListener:()=>{}};
  const sandbox={document,localStorage:{getItem:k=>{if(blocked)throw Error('blocked');return storage.get(k)||null;},setItem:(k,v)=>{if(blocked)throw Error('blocked');storage.set(k,v);}},matchMedia:q=>{if(!medias.has(q))medias.set(q,{matches:q.includes('color-scheme')?systemDark:narrow,addEventListener(name,fn){this.callback=fn;},addListener(fn){this.callback=fn;}});return medias.get(q);},devicePixelRatio:1,ResizeObserver:class{constructor(cb){this.cb=cb;}observe(){this.cb();}},getComputedStyle:()=>({getPropertyValue:n=>({'--focus':root.dataset.theme==='dark'?'#c3d8b0':'#48643f','--label':'#38413e','--canvas':'#fdfcf9'}[n]||'')}),console};
  const context=vm.createContext(sandbox);
  executable.forEach(s=>vm.runInContext(s.code,context));
  return {context,elements,root,storage,medias,images,run:code=>vm.runInContext(code,context)};
}
const ui=boot();
assert.equal(ui.root.dataset.theme,'light');assert.equal(ui.root.dataset.themeMode,'system');
assert.equal(ui.elements.get('results').children.length,data.nodes.length);
assert.ok(ui.images.every(i=>i.src.startsWith('data:image/svg+xml;base64,')));
const colorBefore=ui.run('color(nodes[0])');ui.elements.get('themeDark').onclick();
assert.equal(ui.root.dataset.theme,'dark');assert.equal(ui.storage.get('personal-atlas.theme'),'dark');assert.notEqual(ui.run('color(nodes[0])'),colorBefore);
assert.deepEqual([...ui.storage.keys()],['personal-atlas.theme']);
assert.equal(boot({saved:'dark'}).root.dataset.theme,'dark');
assert.equal(boot({saved:'invalid',systemDark:true}).root.dataset.themeMode,'system');
const blocked=boot({blocked:true});blocked.elements.get('themeDark').onclick();assert.equal(blocked.root.dataset.theme,'dark');assert.equal(blocked.storage.size,0);
ui.elements.get('themeSystem').onclick();const mq=ui.medias.get('(prefers-color-scheme: dark)');mq.matches=true;mq.callback();assert.equal(ui.root.dataset.theme,'dark');ui.elements.get('themeLight').onclick();mq.callback();assert.equal(ui.root.dataset.theme,'light');
ui.elements.get('search').value='no-match-ever';ui.elements.get('search').dispatch('input');assert.equal(ui.elements.get('results').children.length,1);assert.equal(ui.run('visible.length'),0);
ui.elements.get('reset').onclick();assert.equal(ui.run('visible.length'),data.nodes.length);assert.equal(ui.root.dataset.theme,'light');
ui.elements.get('domain').value=data.root.domains[0];ui.elements.get('domain').dispatch('change');assert.ok(ui.run('visible.length')<data.nodes.length);
ui.elements.get('reset').onclick();const id=data.nodes.find(n=>n.type!=='domain').id;ui.run('select('+JSON.stringify(id)+')');assert.equal(ui.run('selected'),id);assert.ok(ui.elements.get('detail').children.length>5);
ui.elements.get('focus').onclick();assert.equal(ui.elements.get('focus').getAttribute('aria-pressed'),'true');assert.ok(ui.run('visible.length')<=data.nodes.length);
ui.elements.get('timeView').onclick();assert.equal(ui.elements.get('timeline').hidden,false);assert.equal(ui.elements.get('graph').hidden,true);
ui.elements.get('mapView').onclick();const before=ui.run('zoom');ui.elements.get('zoomIn').onclick();assert.ok(ui.run('zoom')>before);ui.elements.get('zoomOut').onclick();assert.ok(Math.abs(ui.run('zoom')-before)<1e-10);
ui.elements.get('reset').onclick();assert.equal(ui.run('selected'),null);assert.equal(ui.run('view'),'map');assert.equal(ui.run('focus'),false);
const mobile=boot({narrow:true});assert.equal(mobile.elements.get('filtersPanel').open,false);assert.equal(mobile.elements.get('recordsPanel').open,false);
assert.deepEqual(JSON.parse(ui.run('JSON.stringify(data.nodes.map(n=>n.id))')),data.nodes.map(n=>n.id));
console.log('Theme, storage-disabled fallback, system updates, fresh UI data, search, filters, details, focus, timeline, zoom and reset contracts passed. Visual rendering not tested.');

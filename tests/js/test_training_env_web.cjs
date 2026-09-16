const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../../Web/first_setup.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
function load(){
  const elements = new Map();
  const el = id => {
    if(!elements.has(id)) elements.set(id, {value: id==='backendSel'?'auto':id==='sourceSel'?'file':'', options:[{},{}], checked:false, disabled:false, placeholder:'', textContent:'', innerHTML:'', style:{}, listeners:{}, addEventListener(event,fn){this.listeners[event]=fn;}, appendChild(){}});
    return elements.get(id);
  };
  const calls=[];
  const report={training_ready:false, checks:[{stage:'python',ok:false,code:'PYTHON_NOT_FOUND',remedy:'Install supported Python, then Re-check'}]};
  const context=vm.createContext({document:{getElementById:el,createElement:()=>({textContent:'',get outerHTML(){return this.textContent.replaceAll('<','&lt;');}})},fetch:async(url,options)=>{calls.push({url,options}); return {ok:true,json:async()=>url.includes('/status')?{success:true,slot:{}}:{success:true,report}};},setInterval:()=>1,clearInterval(){},setTimeout(){},Date,console});
  vm.runInContext(script,context);
  return {context,el,calls,report};
}
test('wizard JavaScript parses',()=>new vm.Script(script));
test('first setup allows explicit broker choice without claiming connectivity',async()=>{
  const {context,el}=load();
  await vm.runInContext('refreshStatus()',context);
  assert.equal(el('sourceSel').options[1].disabled,false);
  assert.match(el('brokerNote').textContent,/Windows.*MT5.*logged.in/i);
  assert.match(el('brokerNote').textContent,/3000.*100000/);
  assert.match(el('brokerNote').textContent,/not.*verified/i);
});

test('status exposes real allowed import roots and updates copy instructions without C:\\data',async()=>{
  const {context,el}=load();
  assert.doesNotMatch(el('trainFile').placeholder,/C:\\data/i);
  const fakeRoots=['/workspace/data/imports','/workspace/data/raw'];
  context.fetch=async(url)=>({
    ok:true,
    json:async()=>url.includes('/status')
      ? {success:true,slot:{},allowed_import_roots:fakeRoots}
      : {success:true,report:{}}
  });
  await vm.runInContext('refreshStatus()',context);
  assert.match(el('fileHelp').textContent,/Permitted import directories/i);
  assert.match(el('fileHelp').textContent,/\/workspace\/data\/imports/);
  assert.match(el('fileHelp').textContent,/copy/i);
  assert.match(el('trainFile').placeholder,/\/workspace\/data\/imports|data\/imports/);
});


test('checklist shows remedies and managed READY enables training',()=>{
  const {context,el}=load();
  vm.runInContext('renderEnv({training_ready:true,in_process_ready:false,checks:[]})',context);
  assert.equal(el('btnTrain').disabled,false);
  vm.runInContext('renderEnv({training_ready:false,checks:[{stage:"python",ok:false,remedy:"Install Python"}]})',context);
  assert.match(el('envNote').innerHTML,/Install Python/);
  assert.equal(el('btnTrain').disabled,true);
});
test('explicit consent sends preparation flag; discovery never posts',async()=>{
  const {context,el,calls}=load();
  await vm.runInContext('refreshStatus()',context);
  assert.ok(calls.every(c=>!c.options.method || c.options.method==='GET'));
  el('trainFile').value='data/imports/bars.csv';
  el('prepConsent').checked=true;
  await el('btnTrain').listeners.click();
  const start=calls.find(c=>c.url.endsWith('/train/start'));
  assert.equal(JSON.parse(start.options.body).prepare_environment,true);
});
test('active run cannot be re-enabled by periodic environment refresh',()=>{
  const {context,el}=load();
  vm.runInContext('trainingActive=true; renderEnv({training_ready:true,checks:[]})',context);
  assert.equal(el('btnTrain').disabled,true);
  assert.equal(el('btnInstallEnv').disabled,true);
});
test('all real progress stages and messages are rendered',()=>{
  const {context,el}=load();
  vm.runInContext('renderStages([{stage:"environment",status:"active",message:"Installing pinned dependencies"},{stage:"features",status:"done"},{stage:"validation",status:"running"}])',context);
  assert.match(el('trainStages').innerHTML,/Installing pinned dependencies/);
  assert.match(el('trainStages').innerHTML,/feature engineering \(row-shaped 50\/70D — no sequences used\)/);
  assert.match(el('trainStages').innerHTML,/per-fold out-of-sample \(OOS\) validation/);
});

test('final_fit metrics do not show undefined fold, and cancellation displays honest partial caveat',async()=>{
  const {context,el}=load();
  // 1. final_fit metrics should not have undefined fold
  vm.runInContext(`
    after = 0;
    allEvents = [];
  `, context);
  context.fetch=async(url)=>({
    ok:true,
    json:async()=>({
      success:true,
      active:true,
      events:[{
        stage:'train',
        status:'active',
        message:'Final fit epoch 2',
        metrics:{phase:'final_fit', epoch:2, epochs:4, loss:0.25}
      }]
    })
  });
  await vm.runInContext('pump()',context);
  assert.doesNotMatch(el('trainMetrics').textContent, /undefined/i);
  assert.match(el('trainMetrics').textContent, /final fit/i);
  assert.match(el('trainMetrics').textContent, /epoch 2\/4/i);
  assert.match(el('trainMetrics').textContent, /0\.2500/);

  // 2. cancellation outcome displays honest partial candidate caveat
  context.fetch=async(url)=>({
    ok:true,
    json:async()=>({
      success:true,
      active:false,
      result:{outcome:'CANCELLED'}
    })
  });
  await vm.runInContext('pump()',context);
  assert.doesNotMatch(el('trainMetrics').textContent, /no artifact was written/i);
  assert.match(el('trainMetrics').textContent, /cancelled/i);
  assert.match(el('trainMetrics').textContent, /serving/i);
  assert.match(el('trainMetrics').textContent, /partial.*candidate.*may remain/i);
});

test('switching back to file immediately shows the input and copy help',async()=>{
  const {context,el}=load();
  el('sourceSel').value='broker';
  await vm.runInContext('refreshStatus()',context);
  el('sourceSel').value='file';
  el('sourceSel').listeners.change();
  assert.equal(el('trainFile').style.display,'');
  assert.equal(el('fileHelp').style.display,'');
});

for(const failure of [false,true]) test('official request releases UI reservation after completion '+failure,async()=>{
  const {context,el}=load();
  await vm.runInContext('refreshStatus()',context);
  context.fetch=async()=>({ok:true,json:async()=>failure
    ? {success:false,error:{code:'OFFICIAL_BUNDLE_REJECTED'}}
    : {success:true,servable:true}});
  await el('btnOfficial').listeners.click();
  assert.equal(vm.runInContext('officialActive',context),false);
  assert.equal(el('btnOfficial').disabled,false);
});


test('official download is mutually exclusive with training and install in UI',async()=>{
  const {context,el}=load();
  // If trainingActive or installActive, official button is disabled
  vm.runInContext('trainingActive = true; updateControls();', context);
  assert.equal(el('btnOfficial').disabled, true);

  vm.runInContext('trainingActive = false; installActive = true; updateControls();', context);
  assert.equal(el('btnOfficial').disabled, true);

  vm.runInContext('installActive = false; updateControls();', context);
  assert.equal(el('btnOfficial').disabled, false);

  // When btnOfficial clicked, while request is in flight, training and install buttons are disabled
  let resolveOfficial;
  context.fetch = (url) => {
    if(url.includes('/official')){
      return new Promise((resolve) => {
        resolveOfficial = () => resolve({ok:true, json: async()=>({success:true, servable:true, bundle_id:'b1'})});
      });
    }
    return Promise.resolve({ok:true, json: async()=>({success:true, report:{}, slot:{}})});
  };

  const clickPromise = el('btnOfficial').listeners.click();
  // In flight:
  assert.equal(el('btnOfficial').disabled, true);
  assert.equal(el('btnTrain').disabled, true);
  assert.equal(el('btnInstallEnv').disabled, true);

  // Complete official download:
  resolveOfficial();
  await clickPromise;
  assert.equal(el('btnOfficial').disabled, false);
});



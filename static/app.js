'use strict';
const $ = id => document.getElementById(id);
const integer = new Intl.NumberFormat('de-DE',{maximumFractionDigits:0});
const decimal = new Intl.NumberFormat('de-DE',{maximumFractionDigits:2});
const time = value => new Intl.DateTimeFormat('de-DE',{dateStyle:'short',timeStyle:'short'}).format(new Date(value));
async function api(path, options={}) {
  const response=await fetch(path,{cache:'no-store',...options});
  let data={}; try {data=await response.json()} catch {}
  if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Der Dienst ist nicht erreichbar.');
  return data;
}
function formatDuration(seconds){const minutes=Math.floor(seconds/60);return `${integer.format(Math.floor(minutes/60))} h ${String(minutes%60).padStart(2,'0')} min`}
function svgRect(x,w,state){const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');rect.setAttribute('x',String(x));rect.setAttribute('y','0');rect.setAttribute('width',String(Math.max(0,w)));rect.setAttribute('height','40');rect.setAttribute('fill',state==='ON'?'var(--on)':state==='OFF'?'var(--off)':'var(--unknown)');return rect}
function renderTimeline(data){
 const svg=$('timeline'); svg.replaceChildren(); const start=Date.parse(data.start), end=Date.parse(data.end), span=end-start;
 for(const segment of data.segments){const x=(Date.parse(segment.start)-start)/span*1000;const w=(Date.parse(segment.end)-Date.parse(segment.start))/span*1000;svg.append(svgRect(x,w,segment.state))}
 svg.setAttribute('aria-label',`Verlauf vom ${time(data.start)} bis ${time(data.end)}; geschätzte Einschaltzeit ${formatDuration(data.on_seconds)}, Zeit ohne Daten ${formatDuration(data.unknown_seconds)}.`);
 const list=$('interval-list');list.replaceChildren();
 const visible=data.segments.filter(x=>x.state==='ON'||x.state==='unknown');
 if(!visible.length){const p=document.createElement('p');p.textContent='Keine Einschaltzeiten oder Datenlücken im gewählten Zeitraum erfasst.';list.append(p);return}
 for(const segment of visible.slice(-100).reverse()){
  const row=document.createElement('div');row.className='interval-row';
  const kind=document.createElement('strong');kind.textContent=segment.state==='ON'?'Vermutlich eingeschaltet':'Keine Daten';
  const when=document.createElement('span');when.textContent=`${time(segment.start)} bis ${time(segment.end)}`;
  const length=document.createElement('span');length.textContent=formatDuration((Date.parse(segment.end)-Date.parse(segment.start))/1000);
  row.append(kind,when,length);list.append(row);
 }
 if(visible.length>100){const p=document.createElement('p');p.textContent='Die 100 neuesten Abschnitte werden angezeigt.';list.append(p)}
}
async function refreshState(){
 try{
  const d=await api('/api/state');const label=d.state==='ON'?'Eingeschaltet':d.state==='OFF'?'Ausgeschaltet':'Unbekannt';
  $('device-state').textContent=label;$('indicator').className='indicator '+(d.state==='ON'?'on':d.state==='OFF'?'off':'');
  $('state-age').textContent=d.age_seconds===null?'Keine aktuelle Rückmeldung':`Letzte Rückmeldung vor ${integer.format(d.age_seconds)} s`;
  $('power').textContent=d.power_w===null?'–':`${integer.format(d.power_w)} W`;
  $('on').disabled=!d.can_turn_on;
  if(d.command_notice) $('command-status').textContent=d.command_notice;
 }catch{$('device-state').textContent='Unbekannt';$('state-age').textContent='Verbindung zur Seite unterbrochen';$('indicator').className='indicator';$('power').textContent='–';$('on').disabled=true}
}
async function refreshHistory(){
 $('history-status').textContent='Messdaten werden geladen.';
 try{
  const until=$('until').value;const d=await api('/api/history?days='+encodeURIComponent($('range').value)+(until?'&until='+encodeURIComponent(until):''));renderTimeline(d);
  $('history-start').textContent=`Als Heizstrahler erfasst seit ${time(d.history_start)} (Ortszeit). Davor liegen keine Heizstrahlerdaten vor.`;
  $('history-range').textContent=`${time(d.start)} bis ${time(d.end)}`;
  $('starts').textContent=integer.format(d.starts);$('duration').textContent=formatDuration(d.on_seconds);
  $('energy').textContent=d.kwh===null?'–':`${decimal.format(d.kwh)} kWh`;
  $('energy-detail').textContent=d.reset?'Zählerwechsel oder Reset erkannt':d.first_energy_at&&d.last_energy_at?`${time(d.first_energy_at)} bis ${time(d.last_energy_at)}`:'Mindestens zwei Messwerte nötig';
  $('cost').textContent=d.estimated_cost===null?'–':`${decimal.format(d.estimated_cost)} €`;
  $('cost-detail').textContent=d.tariff===null?'Tarif in Einstellungen setzen':`Schätzung mit ${decimal.format(d.tariff)} € / kWh`;
  const warnings=[];
  if(d.unknown_seconds>0)warnings.push(`Für ${formatDuration(d.unknown_seconds)} liegen keine verlässlichen Zustandsdaten vor; der Verlauf ist eine Schätzung.`);
  if(d.gap_count>0)warnings.push(`${d.gap_count} längere Messlücke(n): Ein verborgener Zählerreset kann die Verbrauchsschätzung beeinflussen.`);
  if(d.topic_count>1)warnings.push('Alte und neue Topics werden als derselbe Stromzähler zusammengeführt.');
  $('history-status').textContent=warnings.join(' ')||'Die Darstellung basiert auf den verfügbaren Schaltmeldungen.';
 }catch(e){$('history-status').textContent=e.message;$('history-start').textContent='Erfassungsbeginn derzeit nicht verfügbar.';for(const id of ['starts','duration','energy','cost'])$(id).textContent='–';$('timeline').replaceChildren();$('interval-list').replaceChildren();$('timeline').setAttribute('aria-label','Verlauf nicht verfügbar')}
}
async function send(action){
 $('on').disabled=true;$('command-status').textContent='Befehl wird gesendet …';
 try{const d=await api('/api/power',{method:'POST',headers:{'Content-Type':'application/json','X-Heater-Request':'1'},body:JSON.stringify({action})});$('command-status').textContent=d.message;setTimeout(refreshState,1200)}
 catch(e){$('command-status').textContent=e.message;await refreshState()}
}
async function refreshDatabase(){
 try{await api('/api/database-status');$('database-status').textContent='Zentrale Messdatenbank verbunden.'}
 catch{$('database-status').textContent='Messdatenbank derzeit nicht erreichbar.'}
}
async function refreshMode(){
 try{const d=await api('/api/light-mode');$('all-day').disabled=d.all_day===null;if(d.all_day!==null)$('all-day').checked=d.all_day;$('mode-status').textContent=d.all_day===null?'Schaltmodus nicht verfügbar.':d.all_day?'Ganztägig aktiv.':'Nur nachts (21–7 Uhr).'}
 catch{$('all-day').disabled=true;$('mode-status').textContent='Schaltmodus nicht verfügbar.'}
}
$('all-day').addEventListener('change',async()=>{
 const selected=$('all-day').checked;$('all-day').disabled=true;$('mode-status').textContent='Einstellung wird übertragen …';
 try{await api('/api/light-mode',{method:'PUT',headers:{'Content-Type':'application/json','X-Heater-Request':'1'},body:JSON.stringify({all_day:selected})});setTimeout(refreshMode,1200)}
 catch(e){$('mode-status').textContent=e.message;setTimeout(refreshMode,3000)}
});
$('until').max=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Berlin',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
$('until').addEventListener('change',refreshHistory);
$('on').addEventListener('click',()=>send('ON'));$('off').addEventListener('click',()=>send('OFF'));$('range').addEventListener('change',refreshHistory);
$('tariff-form').addEventListener('submit',async e=>{e.preventDefault();const value=Number($('tariff').value);try{await api('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-Heater-Request':'1'},body:JSON.stringify({euros_per_kwh:value})});$('settings-status').textContent='Strompreis gespeichert.';await refreshHistory()}catch(err){$('settings-status').textContent=err.message}});
api('/api/settings').then(d=>{if(d.euros_per_kwh!==null)$('tariff').value=d.euros_per_kwh}).catch(()=>$('settings-status').textContent='Einstellungen sind derzeit nicht verfügbar.');
refreshState();refreshHistory();refreshMode();refreshDatabase();setInterval(refreshDatabase,60000);setInterval(refreshMode,10000);setInterval(refreshState,5000);setInterval(refreshHistory,60000);

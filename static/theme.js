'use strict';
(() => {
 const key='heizstrahler-theme';
 const select=document.getElementById('theme');
 const valid=new Set(['system','light','dark']);
 function apply(value) {
  const chosen=valid.has(value)?value:'system';
  if(chosen==='system')delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme=chosen;
  select.value=chosen;
 }
 let stored='system';
 try{stored=localStorage.getItem(key)||'system'}catch{}
 apply(stored);
 select.addEventListener('change',()=>{
  const choice=valid.has(select.value)?select.value:'system';
  apply(choice);
  try{localStorage.setItem(key,choice)}catch{}
 });
})();

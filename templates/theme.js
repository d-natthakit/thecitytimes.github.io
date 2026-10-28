(function(){
  var root=document.documentElement, btn=document.getElementById('themeBtn');
  function setTheme(t){
    root.setAttribute('data-theme',t);
    btn.textContent = t==='bw' ? 'Sepia' : 'Black & White';
    try{localStorage.setItem('city-theme',t);}catch(e){}
  }
  var saved=null;
  try{saved=localStorage.getItem('city-theme');}catch(e){}
  setTheme(saved==='bw'?'bw':'sepia');
  btn.addEventListener('click',function(){setTheme(root.getAttribute('data-theme')==='bw'?'sepia':'bw');});
  window.addEventListener('beforeprint',function(){
    var d=document.querySelectorAll('details');
    for(var i=0;i<d.length;i++){d[i].open=true;}
  });
})();

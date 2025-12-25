// Simple garland script: creates bulbs along a curved line in the top-right corner
document.addEventListener('DOMContentLoaded', function(){
  try{
    const count = 14;
    const container = document.createElement('div');
    container.className = 'garland';

    const string = document.createElement('div');
    string.className = 'string';
    container.appendChild(string);

    const colors = ['#FF4D4F','#FFD23F','#4ADE80','#60A5FA','#A78BFA','#FB7185'];

    for(let i=0;i<count;i++){
      const bulb = document.createElement('div');
      bulb.className = 'garland-bulb';
      bulb.style.background = colors[i % colors.length];

      const t = i / (count - 1);
      const x = 12 + t * (296); // horizontal range inside the container
      const wave = Math.sin(t * Math.PI * 1.6) * 28; // curve amplitude
      const y = 40 + wave;

      bulb.style.left = Math.round(x) + 'px';
      bulb.style.top = Math.round(y) + 'px';
      bulb.style.animationDelay = (Math.random() * 2).toFixed(2) + 's';
      bulb.style.opacity = 0.9;

      container.appendChild(bulb);
    }

    document.body.appendChild(container);
  }catch(e){ console.error('Garland init error:', e); }
});

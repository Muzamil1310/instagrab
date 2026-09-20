
  const API = (window.INSTAGRAB_API || 'https://instagrab-nnl9.onrender.com').replace(/\/$/, '');
  const form = document.getElementById('downloadForm');
  const input = document.getElementById('url');
  const button = document.getElementById('downloadBtn');
  const clear = document.getElementById('clearBtn');
  const result = document.getElementById('result');
  const pasteBtn = document.getElementById('pasteBtn');

  clear.addEventListener('click', () => { input.value=''; input.focus(); result.className='result'; result.innerHTML=''; });
  pasteBtn.addEventListener('click', async () => { try { input.value = await navigator.clipboard.readText(); input.focus(); } catch { input.focus(); } });

  function esc(v){ return String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
  function showError(title, message){
    result.className='result show';
    result.innerHTML='<div class="error"><strong>'+esc(title)+'</strong><div class="error-message">'+esc(message)+'</div></div>';
  }

  function renderPreview(url, type){
    if(type === 'video' || type === 'reel') return '<video class="result-preview" controls playsinline src="'+esc(url)+'"></video>';
    return '<img class="result-preview" alt="Instagram media preview" src="'+esc(url)+'">';
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const url = input.value.trim();
    if (!/https?:\/\/(www\.)?(instagram\.com|instagr\.am)\//i.test(url)) { showError('Invalid Instagram link', 'Please paste a valid Instagram post, Reel, or supported public URL.'); return; }
    button.disabled=true;
    button.innerHTML='<span class="loading"><span class="spinner"></span>Searching…</span>';
    result.className='result show';
    const loadingMessages = [
      'Finding the available media…',
      'Waking up the little machine…',
      'Checking what is ready to grab…',
      'Looking for your media…',
      'Getting things ready…',
      'Almost there — fetching the media…',
      'One moment, grabbing the available media…'
    ];
    let loadingIndex = 0;
    result.innerHTML='<div class="result-card"><div class="loading"><span class="spinner"></span><span id="loadingMessage">'+loadingMessages[loadingIndex]+'</span></div></div>';
    const loadingMessage = document.getElementById('loadingMessage');
    const loadingTimer = setInterval(() => {
      loadingIndex = (loadingIndex + 1) % loadingMessages.length;
      if (loadingMessage) loadingMessage.textContent = loadingMessages[loadingIndex];
    }, 2800);
    try {
      const r = await fetch(API+'/api/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
      const data = await r.json();
      if (!r.ok) {
        const detail = data.detail;
        if (detail && typeof detail === 'object') {
          throw Object.assign(new Error(detail.message || 'Could not retrieve this URL.'), {
            friendlyTitle: detail.title || 'Could not retrieve this URL',
            friendlyMessage: detail.message || 'Please check the link and try again.'
          });
        }
        throw Object.assign(new Error(detail || 'Could not retrieve this URL.'), {
          friendlyTitle: 'Could not retrieve this URL',
          friendlyMessage: detail || 'Please check the link and try again.'
        });
      }

      const mediaUrl = API + data.download_url;
      const previewUrl = data.preview_url ? API + data.preview_url : mediaUrl;
      const label = data.type === 'carousel' ? 'Carousel ready' : (data.type === 'reel' ? 'Reel ready' : (data.type === 'photo' ? 'Photo ready' : 'Video ready'));
      const actionLabel = data.type === 'carousel' ? 'Download all' : 'Download';
      const meta = data.type === 'carousel' ? (data.count+' items • ZIP download') : (data.filename || 'media');
      const previewType = data.type === 'carousel' ? ((data.items && data.items[0] && data.items[0].type) || 'photo') : data.type;
      const extra = data.type === 'carousel' && data.items ? '<div class="result-count">'+data.count+' media items found. Download all as one ZIP.</div>' : '';

      result.className='result show';
      result.innerHTML = '<div class="result-card"><div class="result-top"><div><div class="result-title">✓ '+esc(label)+'</div><div class="result-meta">'+esc(data.title || 'Instagram media')+' • '+esc(meta)+'</div></div><div class="result-actions"><a class="download-link" href="'+esc(mediaUrl)+'" download>'+actionLabel+'</a></div></div>'+renderPreview(previewUrl, previewType)+extra+'</div>';
      result.scrollIntoView({behavior:'smooth',block:'nearest'});
    } catch(err) {
      showError(
        err.friendlyTitle || 'Could not retrieve this URL',
        err.friendlyMessage || 'Something went wrong. Please try again.'
      );
    }
    finally {
      clearInterval(loadingTimer);
      button.disabled=false;
      button.innerHTML='Search';
    }
  });

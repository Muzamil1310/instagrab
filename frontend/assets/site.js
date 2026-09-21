(function () {
  const root = document.documentElement;
  const menuBtn = document.getElementById('menuBtn');
  const navLinks = document.getElementById('navLinks');

  // ReelSloth currently stays dark by design. Light mode can be added later.
  const values = {
    '--bg':'#080a12',
    '--bg2':'#0d1020',
    '--text':'#f7f7fb',
    '--muted':'#9ba5bc',
    '--panel':'#101525'
  };
  Object.entries(values).forEach(([key, value]) => root.style.setProperty(key, value));
  document.body.style.background = '#080a12';

  // Mobile menu: a real backdrop makes outside taps/clicks reliable in both
  // Chrome DevTools device emulation and physical touch devices.
  let menuBackdrop = document.getElementById('menuBackdrop');
  if (!menuBackdrop) {
    menuBackdrop = document.createElement('button');
    menuBackdrop.type = 'button';
    menuBackdrop.id = 'menuBackdrop';
    menuBackdrop.className = 'menu-backdrop';
    menuBackdrop.setAttribute('aria-label', 'Close menu');
    menuBackdrop.setAttribute('tabindex', '-1');
    document.body.appendChild(menuBackdrop);
  }

  const setMenuState = (open) => {
    if (!navLinks || !menuBtn) return;
    navLinks.classList.toggle('open', open);
    menuBackdrop.classList.toggle('open', open);
    menuBtn.setAttribute('aria-expanded', String(open));
    menuBtn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  };

  menuBtn?.addEventListener('click', (event) => {
    event.stopPropagation();
    setMenuState(!navLinks?.classList.contains('open'));
  });

  const closeMenu = () => setMenuState(false);

  menuBackdrop.addEventListener('click', closeMenu);
  navLinks?.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', closeMenu);
  });

  // Capture pointer events as a fallback for touch/mouse interactions outside
  // the menu. composedPath() also works reliably with nested elements.
  document.addEventListener('pointerdown', (event) => {
    if (!navLinks?.classList.contains('open')) return;
    const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
    if (!path.includes(navLinks) && !path.includes(menuBtn)) closeMenu();
  }, true);

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMenu();
  });

  const observer = 'IntersectionObserver' in window
    ? new IntersectionObserver((entries) => entries.forEach((entry) => {
        if (entry.isIntersecting) entry.target.classList.add('in');
      }), { threshold: 0.12 })
    : null;
  document.querySelectorAll('.reveal').forEach((el) => observer ? observer.observe(el) : el.classList.add('in'));
})();
